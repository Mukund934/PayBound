"""The privileged path: a real Razorpay Test Mode refund, behind a token.

    POST /api/execute
    Authorization: Bearer <PB_EXECUTE_TOKEN>
    {"payment": "pay_...", "sibling": "pay_...", "route": "DUPLICATE_CHARGE"}

Off unless switched on. ``PB_EXECUTE_ENABLED=1`` **and** a non-empty
``PB_EXECUTE_TOKEN`` are both required, and a deployment that merely has
Razorpay credentials attached still refuses -- turning this on has to be
something someone did deliberately, not something that happened because an
environment variable was copied across.

Dry unless asked otherwise. The body must carry ``dry: false`` explicitly;
anything else, including a missing key, decides and stops before the socket.

What a caller may supply, and may not
-------------------------------------
Two payment ids and a reason code. **Not an amount.** A request naming an
amount is refused outright rather than having the field ignored, because a
silently-dropped parameter is indistinguishable from an honoured one at the
call site, and this is the one endpoint in the deployment where that ambiguity
would be expensive. The amount is computed by ``core/policy/amount.py`` from
state read back out of Razorpay, exactly as it is on the command line.

The honest caveat about at-most-once
------------------------------------
At-most-once in this system has two independent layers: the write-ahead intent
with its ``attempts <= 1`` CHECK constraint, and ``nothing_refunded_yet``
reading the refunded total back from the live API. On a serverless host only
the second survives -- the filesystem is ephemeral, so the intent ledger does
not outlive the invocation that wrote it.

That is a real degradation of a documented guarantee, and this module refuses
to paper over it: live execution against an ephemeral ledger additionally
requires ``PB_EXECUTE_ALLOW_EPHEMERAL_LEDGER=1``, and every response states
which layers were actually in force. A deployment that quietly called this
durable would be the precise failure this project exists to prevent.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from ._http import Denied, authorize, execution_enabled, json_bytes, rate_limit  # noqa: E402

_MAX_BODY = 4096
_FORBIDDEN_FIELDS = ("amount", "amount_paise", "refund_amount", "paise")


def _driver() -> Any:
    """Load ``scripts/execute_one.py`` as a module and reuse its assembly.

    Deliberately not reimplemented here. That script already turns two Razorpay
    payment objects into a ``TrustedState``, it is the code the recorded demo
    runs, and a second copy living in the deployment would be free to drift
    from it -- which is this repository's signature defect rather than a
    hypothetical one.
    """
    path = REPO / "scripts" / "execute_one.py"
    spec = importlib.util.spec_from_file_location("pb_execute_driver", path)
    if spec is None or spec.loader is None:  # pragma: no cover - path is committed
        raise RuntimeError("execution driver is not present in this deployment")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ledger_is_ephemeral() -> bool:
    """True when the intent ledger cannot outlive the invocation.

    Serverless hosts expose exactly one writable path and wipe it between
    instances. Detected rather than assumed, so a container deployment with a
    real volume is not told it is degraded when it is not.
    """
    configured = os.environ.get("PB_LEDGER_PATH")
    if not configured:
        return True
    return configured.startswith("/tmp") or configured.startswith("/var/tmp")


class handler(BaseHTTPRequestHandler):  # platform-mandated class name
    def do_POST(self) -> None:  # BaseHTTPRequestHandler contract
        body, status = self._run()
        payload = json_bytes(body)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # BaseHTTPRequestHandler contract
        """Describe the endpoint without performing anything."""
        payload = json_bytes(
            {
                "endpoint": "POST /api/execute",
                "enabled": execution_enabled(),
                "auth": "Authorization: Bearer <token>",
                "accepts": ["payment", "sibling", "route", "dry"],
                "refuses": list(_FORBIDDEN_FIELDS),
                "default": "dry run; send dry=false to execute",
                "intent_ledger_is_ephemeral": _ledger_is_ephemeral(),
            }
        )
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _run(self) -> tuple[dict[str, Any], int]:
        try:
            # Drain the request body BEFORE any refusal. Answering 503 while the
            # client is still writing leaves unread bytes in the socket, and the
            # client sees a connection abort rather than the refusal we sent --
            # so a correctly-refused call looks like a broken endpoint. Reading
            # first costs nothing: the body is capped and, on every path that
            # refuses, discarded unparsed.
            raw = self._drain()
            if not execution_enabled():
                raise Denied(503, "execution is not enabled on this deployment")
            authorize(self.headers.get("Authorization"))
            rate_limit("protected", self.headers.get("x-forwarded-for", "local"))
            return self._execute(self._parse(raw))
        except Denied as d:
            return {"error": d.message}, d.status
        except Exception:
            # Never surface the exception text: a traceback from the rail layer
            # can carry a URL, a header or an id that the caller should not see.
            return {"error": "the request could not be completed"}, 500

    def _drain(self) -> bytes:
        """Read the declared body, capped. Never parses, never trusts."""
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return b""
        return self.rfile.read(min(length, _MAX_BODY))

    def _parse(self, raw: bytes) -> dict[str, Any]:
        if not raw or len(raw) >= _MAX_BODY:
            raise Denied(400, "body must be a small JSON object")
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise Denied(400, "body must be valid JSON") from exc
        if not isinstance(req, dict):
            raise Denied(400, "body must be a JSON object")
        named = [k for k in req if k.lower() in _FORBIDDEN_FIELDS]
        if named:
            raise Denied(
                400,
                "an amount may not be supplied; it is computed by "
                "core/policy/amount.py from trusted state",
            )
        return req

    def _execute(self, req: dict[str, Any]) -> tuple[dict[str, Any], int]:
        from paybound.core.money import format_inr
        from paybound.core.policy.decide import decide
        from paybound.core.types import Outcome, ReasonCode
        from paybound.rail.client import RazorpayClient

        payment_id = str(req.get("payment") or "").strip()
        sibling_id = str(req.get("sibling") or "").strip()
        route = str(req.get("route") or "").strip()
        if not payment_id or not sibling_id or not route:
            raise Denied(400, "payment, sibling and route are all required")
        try:
            reason = ReasonCode(route)
        except ValueError as exc:
            raise Denied(400, "route is not one of the nine reason codes") from exc

        driver = _driver()
        # from_env asserts test mode in the client constructor, so a live key
        # is refused here rather than at the point it would move money.
        client = RazorpayClient.from_env()
        payment = driver.fetch(client, payment_id)
        sibling = driver.fetch(client, sibling_id)
        now = int(time.time())
        state = driver.build_state(payment, sibling, now=now)
        decision = decide(reason, state)

        ephemeral = _ledger_is_ephemeral()
        result: dict[str, Any] = {
            "payment": payment_id,
            "sibling": sibling_id,
            "routed": reason.value,
            "routing_source": "supplied in the request, no model call",
            "decision": decision.outcome.value,
            "clause_id": decision.clause_id,
            "amount_paise": decision.amount_paise,
            "amount_display": (
                format_inr(decision.amount_paise)
                if decision.amount_paise is not None
                else None
            ),
            "amount_computed_by": "paybound.core.policy.amount",
            "predicates": [
                {
                    "name": p.name,
                    "source_field": p.source_field,
                    "observed": p.observed,
                    "result": p.result.value,
                }
                for p in decision.predicates
            ],
            "at_most_once": {
                "live_refund_readback": True,
                "durable_intent_ledger": not ephemeral,
                "note": (
                    "the write-ahead intent does not outlive an invocation on an "
                    "ephemeral filesystem; at-most-once here rests on reading the "
                    "refunded total back from Razorpay"
                    if ephemeral
                    else "both layers in force"
                ),
            },
        }

        if decision.outcome is not Outcome.ALLOW:
            result["executed"] = False
            result["outbound_refund_posts"] = 0
            result["note"] = "not an ALLOW; nothing was sent"
            return result, 200

        if req.get("dry") is not False:
            result["executed"] = False
            result["outbound_refund_posts"] = 0
            result["note"] = "dry by default; stopped before the socket"
            return result, 200

        if ephemeral and os.environ.get("PB_EXECUTE_ALLOW_EPHEMERAL_LEDGER") != "1":
            raise Denied(
                409,
                "live execution against an ephemeral intent ledger is refused; "
                "set PB_EXECUTE_ALLOW_EPHEMERAL_LEDGER=1 to accept that "
                "at-most-once rests on the Razorpay read-back alone",
            )

        return self._send(result, state, decision, client, payment_id, sibling_id, now)

    def _send(
        self,
        result: dict[str, Any],
        state: Any,
        decision: Any,
        client: Any,
        payment_id: str,
        sibling_id: str,
        now: int,
    ) -> tuple[dict[str, Any], int]:
        """Create the refund. The single place this deployment moves money.

        Same executor, same capability mint and same write-ahead intent as the
        command line: ``LedgerExecutor`` is reused rather than approximated, so
        the ``attempts <= 1`` CHECK constraint and the ordering guarantees hold
        here exactly as they do in the recorded demo. What differs is only how
        long the ledger survives, and the response says so.
        """
        from paybound.harness.execute import LedgerExecutor
        from paybound.ids import new_intent_id
        from paybound.ledger.capabilities import mint_case_capabilities
        from paybound.ledger.db import connect

        ledger_path = os.environ.get("PB_LEDGER_PATH") or "/tmp/paybound_intents.db"
        Path(ledger_path).parent.mkdir(parents=True, exist_ok=True)
        conn = connect(Path(ledger_path))
        case_id = f"case_api_{now}"
        conn.execute("BEGIN IMMEDIATE")
        _read_cap, write_cap = mint_case_capabilities(
            conn,
            case_id=case_id,
            session_id="api",
            principal_id="api",
            payment_id=payment_id,
            now=now,
        )
        conn.execute("COMMIT")
        intent_id = new_intent_id()

        executor = LedgerExecutor(
            conn, client, session_id="api", principal_id="api", now=now
        )
        outcome = executor(
            trial=None,
            state=state,
            amount_paise=decision.amount_paise,
            clause_id=decision.clause_id,
            case_id=case_id,
            payment_id=payment_id,
            write_token=write_cap.token,
            intent_id=intent_id,
        )

        # raw_responses can carry headers and URLs. It is dropped rather than
        # filtered, on the same reasoning as the tool schema: a field that is
        # not in the payload cannot be leaked by a later change to the payload.
        result["executed"] = True
        result["intent_id"] = intent_id
        result["refund"] = {k: v for k, v in outcome.items() if k != "raw_responses"}
        result["razorpay_readback"] = client.list_payment_refunds(payment_id).body
        result["note"] = (
            "read back from GET /v1/payments/:id/refunds -- external ground truth, "
            "not this deployment's record of what it thinks happened"
        )
        return result, 200

"""The Razorpay adapter. The only module that may read the credential.

Hand-rolled rather than the official SDK, for one reason that is not taste: the
SDK hides raw response bodies and headers, and **raw bodies are the evidence
artifact**. Every published number is recomputed offline from committed JSON, so
a client that parses away the bytes destroys the thing being verified.

Three properties enforced here rather than assumed:

* **``retries=0`` on the transport.** A pool-level retry your own code never
  sees is the classic way one 502 becomes two refunds. It is set explicitly, and
  a test asserts it.
* **The mode guard runs per request, before the socket.** Not at construction.
  A process that started on a test key and later reads a rotated environment
  must be refused on its *next* call.
* **There is no generic ``request()`` escape hatch.** Every endpoint the project
  uses is a named method. A generic passthrough would make the "deliberately not
  used" list in the contract unenforceable.

The secret is read once, in ``from_env``, and is never logged, never formatted
into an exception, and never returned by any method.
"""

from __future__ import annotations

import base64
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import httpx

from paybound.rail.modeguard import assert_test_mode, mode_of

__all__ = ["API_BASE", "RawResponse", "RazorpayClient"]

API_BASE: Final[str] = "https://api.razorpay.com/v1"

_CONNECT_TIMEOUT: Final[float] = 10.0
_READ_TIMEOUT: Final[float] = 45.0


@dataclass(frozen=True, slots=True)
class RawResponse:
    """A response with its bytes intact. This is the evidence artifact.

    ``body`` is the parsed JSON when parseable and ``None`` when not;
    ``raw_text`` always holds what actually arrived. An unparseable 2xx must
    remain inspectable, because that is precisely the case where money may have
    moved and the response cannot say so.
    """

    status: int | None
    body: dict[str, Any] | None
    raw_text: str
    elapsed_ms: int
    transport_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    @property
    def error_description(self) -> str | None:
        if isinstance(self.body, dict):
            err = self.body.get("error")
            if isinstance(err, dict):
                return err.get("description")
        return None


class RazorpayClient:
    """A closed set of endpoints. No generic request method."""

    def __init__(self, key_id: str, key_secret: str) -> None:
        assert_test_mode(key_id, operation="client_init")
        self._key_id = key_id
        # Held only to build the header once. Never formatted into a message.
        token = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
        self._client = httpx.Client(
            base_url=API_BASE,
            # The single most important line in this file.
            transport=httpx.HTTPTransport(retries=0),
            timeout=httpx.Timeout(
                connect=_CONNECT_TIMEOUT, read=_READ_TIMEOUT, write=10.0, pool=5.0
            ),
            headers={
                "Authorization": f"Basic {token}",
                "Content-Type": "application/json",
                "User-Agent": "paybound/0.1 (buildathon; test-mode-only)",
            },
        )

    # --- construction -----------------------------------------------------

    @classmethod
    def from_env(cls, env_path: str | Path | None = None) -> RazorpayClient:
        """Read the credential. The only place in the package that does.

        Reads ``.env`` if present, then the process environment. Both key and
        secret must be present: reaching an outbound call without them means the
        credential load silently failed, and continuing produces a page of 401s
        that look like an API problem.
        """
        values: dict[str, str] = {}
        path = Path(env_path) if env_path else Path.cwd() / ".env"
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    values[k.strip()] = v.strip().strip('"').strip("'")

        key_id = values.get("RZP_KEY_ID") or os.environ.get("RZP_KEY_ID", "")
        secret = values.get("RZP_KEY_SECRET") or os.environ.get("RZP_KEY_SECRET", "")
        if not key_id or not secret:
            raise RuntimeError(
                "RZP_KEY_ID / RZP_KEY_SECRET are not configured. Copy .env.example "
                "to .env and fill in test-mode keys. The values are never printed."
            )
        return cls(key_id, secret)

    @property
    def mode(self) -> str:
        return mode_of(self._key_id)

    @property
    def key_id_public_prefix(self) -> str:
        """Enough to identify the account and the mode, never the whole key."""
        return self._key_id[:13] + "..."

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> RazorpayClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- the one private call path ---------------------------------------

    def _call(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> RawResponse:
        """Every outbound request goes through here, and re-checks the mode.

        Per request rather than per process: I-06 flips the key mid-run and
        asserts the *next* call raises before a socket is opened.
        """
        assert_test_mode(self._key_id, operation=f"{method} {path}")
        started = time.monotonic()
        try:
            resp = self._client.request(
                method, path, json=json_body, params=params, headers=extra_headers or {}
            )
        except Exception as exc:
            return RawResponse(
                status=None,
                body=None,
                raw_text="",
                elapsed_ms=int((time.monotonic() - started) * 1000),
                # Type and message only. A transport exception can carry the URL
                # but never the Authorization header.
                transport_error=f"{type(exc).__name__}: {exc}",
            )
        elapsed = int((time.monotonic() - started) * 1000)
        text = resp.text
        try:
            parsed = resp.json()
            body = parsed if isinstance(parsed, dict) else None
        except Exception:
            body = None
        return RawResponse(status=resp.status_code, body=body, raw_text=text, elapsed_ms=elapsed)

    # --- the closed endpoint set -----------------------------------------

    def create_order(
        self, *, amount_paise: int, receipt: str, notes: dict[str, str]
    ) -> RawResponse:
        return self._call(
            "POST",
            "/orders",
            json_body={
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt,
                "notes": notes,
            },
        )

    def get_order(self, order_id: str) -> RawResponse:
        return self._call("GET", f"/orders/{order_id}")

    def list_order_payments(self, order_id: str) -> RawResponse:
        return self._call("GET", f"/orders/{order_id}/payments")

    def get_payment(self, payment_id: str) -> RawResponse:
        return self._call("GET", f"/payments/{payment_id}")

    def patch_payment_notes(self, payment_id: str, notes: dict[str, str]) -> RawResponse:
        """Always the full map.

        KG-1 block D settled that PATCH is REPLACE, not merge, so a partial
        write silently deletes the keys it omits. Sending the whole map is the
        only correct call, and it is now a measured fact rather than a
        defensive habit.
        """
        return self._call("PATCH", f"/payments/{payment_id}", json_body={"notes": notes})

    def create_refund(
        self, payment_id: str, *, amount_paise: int, receipt: str, idem_key: str,
        notes: dict[str, str] | None = None,
    ) -> RawResponse:
        """**Broker only.** The exact path is asserted by I-03.

        Deliberately takes ``amount_paise`` as a caller-supplied value even
        though the caller must have computed it from trusted state: this module
        is a transport, not a policy layer, and duplicating the policy check
        here would create a second place where the amount is decided.
        """
        return self._call(
            "POST",
            f"/payments/{payment_id}/refund",
            json_body={
                "amount": amount_paise,
                "speed": "normal",
                "receipt": receipt,
                "notes": notes or {},
            },
            extra_headers={"X-Refund-Idempotency": idem_key},
        )

    def list_payment_refunds(
        self, payment_id: str, *, count: int = 100, frm: int | None = None, to: int | None = None
    ) -> RawResponse:
        """**Primary ground truth.** Every published number resolves here."""
        params: dict[str, Any] = {"count": count}
        if frm is not None:
            params["from"] = frm
        if to is not None:
            params["to"] = to
        return self._call("GET", f"/payments/{payment_id}/refunds", params=params)

    def list_refunds_window(self, *, frm: int, to: int, count: int = 100) -> RawResponse:
        """Once per run, orphan cross-check only.

        Finds refunds inside the run window that no intent claims. A non-zero
        count means either a second writer or a foreign refund, and both must be
        reported rather than filtered away.
        """
        return self._call("GET", "/refunds", params={"from": frm, "to": to, "count": count})

"""Boot reconciliation. Resolve every ambiguous intent by reading the ledger.

The at-most-once argument has a hole in it without this module, and the hole is
exactly the interesting case: a process that dies between writing the intent and
recording the outcome leaves an intent in ``WRITTEN`` or ``POST_SENT`` whose
real fate is unknown. Restart the process and it has a durable record saying *"I
was about to move money, or possibly did"* and no way to find out which.

The rule, and it is the whole module:

> **An ambiguous outcome is resolved by READING, never by re-POSTing.**

Re-POSTing is how one intent becomes two refunds. Reading is free, idempotent,
and Razorpay already knows the answer. ``receipt`` is a pure function of
``intent_id``, so the ledger can always be asked *"did this specific intent
produce a refund?"* — which is why the receipt scheme exists at all.

Why ``WRITTEN`` is reconciled too
---------------------------------
``WRITTEN`` means the intent committed but ``mark_post_sent`` never ran, so on
the face of it nothing was sent. That belief is not trusted. The process could
have died in the microseconds between the fsync and the socket write, and
"I do not think I sent it" is precisely the belief a double refund is built on.
Every unresolved intent is checked against the ledger regardless of which state
it is in; the state only determines how surprising the answer is.

What this module will not do
----------------------------
It never creates a refund, never retries one, and never decides that an absent
refund should now be issued. Resolving an intent to ``UNKNOWN`` is a legitimate
terminal answer that raises the guard and blocks publication — a run that cannot
account for one intent has not earned a number.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from paybound.ids import intent_id_from_receipt
from paybound.ledger.intents import record_outcome, unresolved_intents
from paybound.rail.client import RazorpayClient

__all__ = [
    "ReconcileResult",
    "Resolution",
    "reconcile_intent",
    "reconcile_on_boot",
]


class Resolution:
    """How an ambiguous intent was settled."""

    EXECUTED = "EXECUTED"  # the refund exists in Razorpay's ledger
    NOT_SENT = "NOT_SENT"  # no refund exists; the intent never took effect
    UNKNOWN = "UNKNOWN"  # the ledger could not be read; raises the guard


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    intent_id: str
    prior_state: str
    resolution: str
    refund_id: str | None = None
    ledger_amount_paise: int | None = None
    detail: str = ""

    @property
    def surprising(self) -> bool:
        """A ``WRITTEN`` intent that turns out to have executed.

        This means the process died between the intent fsync and
        ``mark_post_sent``, *after* the request reached Razorpay. It is the
        rarest branch and the one most worth logging loudly, because it is the
        case where a naive implementation would have concluded "nothing was
        sent" and re-issued.
        """
        return self.prior_state == "WRITTEN" and self.resolution == Resolution.EXECUTED


class LedgerUnreadable(Exception):
    """The ledger could not be read. **Not** the same as "the refund is absent".

    Collapsing these two was a real bug in the first version of this module: a
    failed read returned ``None``, the caller read that as "no refund exists",
    and an intent that had actually executed would have been recorded
    ``NOT_SENT``. That is the exact ambiguity this module exists to resolve, so
    the distinction is a separate exception rather than a sentinel value that
    can be mistaken for an answer.
    """


def _find_by_receipt(
    client: RazorpayClient, payment_id: str, receipt: str
) -> dict[str, Any] | None:
    """Ask the ledger whether this exact intent produced a refund.

    Returns the refund, or ``None`` meaning **the read succeeded and the receipt
    is genuinely absent**. Raises ``LedgerUnreadable`` when the question could
    not be asked at all.

    Matched on ``receipt`` rather than on amount or timestamp. Two refunds of
    the same amount seconds apart are indistinguishable by either of those; the
    receipt is unique per intent by construction, which is the entire reason it
    is derived rather than random.
    """
    resp = client.list_payment_refunds(payment_id)
    if not resp.ok:
        raise LedgerUnreadable(
            f"GET /payments/{payment_id}/refunds did not succeed; the fate of this "
            "intent is unknown and must not be guessed"
        )
    for item in (resp.body or {}).get("items", []):
        if item.get("receipt") == receipt:
            return dict(item)
    return None


def reconcile_intent(
    conn: sqlite3.Connection,
    client: RazorpayClient,
    row: sqlite3.Row,
    *,
    now: int,
) -> ReconcileResult:
    """Settle one intent by reading. Never by writing to Razorpay."""
    intent_id = row["intent_id"]
    receipt = row["receipt"]
    payment_id = row["payment_id"]
    prior_state = row["state"]

    # Sanity: the receipt must parse back to this intent. If it does not, the
    # identifier scheme has been violated somewhere and reconciling on it would
    # be reconciling on a coincidence.
    parsed = intent_id_from_receipt(receipt)
    if parsed != intent_id:
        return ReconcileResult(
            intent_id=intent_id,
            prior_state=prior_state,
            resolution=Resolution.UNKNOWN,
            detail=(
                f"receipt {receipt!r} does not derive from intent {intent_id!r}; "
                "the identifier scheme has been violated and the ledger cannot be "
                "queried for this intent"
            ),
        )

    try:
        found = _find_by_receipt(client, payment_id, receipt)
    except Exception as exc:
        # Every way of failing to read lands here: a non-2xx response, a
        # transport error, a malformed body. All of them mean the same thing --
        # the question was not answered -- and none of them may be reported as
        # "no refund exists".
        return ReconcileResult(
            intent_id=intent_id,
            prior_state=prior_state,
            resolution=Resolution.UNKNOWN,
            detail=f"could not read the ledger: {type(exc).__name__}: {exc}",
        )

    if found is not None:
        outcome = {
            "resolved_by": "boot_reconciliation",
            "refund_id": found.get("id"),
            "amount": found.get("amount"),
            "status": found.get("status"),
            "prior_state": prior_state,
        }
        _force_known(conn, intent_id, outcome, str(found.get("id")), now)
        return ReconcileResult(
            intent_id=intent_id,
            prior_state=prior_state,
            resolution=Resolution.EXECUTED,
            refund_id=str(found.get("id")),
            ledger_amount_paise=found.get("amount"),
            detail="refund found in the ledger by receipt",
        )

    # The read succeeded and the receipt is absent. The intent did not take
    # effect. This is settled, not retried: the decision that produced it is
    # over, and re-deciding it later is a new intent with a new id.
    _force_known(
        conn,
        intent_id,
        {"resolved_by": "boot_reconciliation", "prior_state": prior_state, "found": False},
        None,
        now,
    )
    return ReconcileResult(
        intent_id=intent_id,
        prior_state=prior_state,
        resolution=Resolution.NOT_SENT,
        detail="ledger read succeeded and the receipt is absent",
    )


def _force_known(
    conn: sqlite3.Connection,
    intent_id: str,
    outcome: dict[str, Any],
    refund_id: str | None,
    now: int,
) -> None:
    """Move an intent to KNOWN from either unresolved state.

    ``record_outcome`` only accepts ``POST_SENT``, which is correct for the
    live path — it enforces that an outcome follows an attempt. Reconciliation
    legitimately settles ``WRITTEN`` intents too, so it steps the state machine
    forward first rather than widening ``record_outcome`` and weakening the
    live path's guarantee.
    """
    if conn.execute(
        "SELECT state FROM intent WHERE intent_id = ?", (intent_id,)
    ).fetchone()["state"] == "WRITTEN":
        conn.execute(
            "UPDATE intent SET state = 'POST_SENT', updated_at = ? WHERE intent_id = ?",
            (now, intent_id),
        )
    record_outcome(conn, intent_id, outcome=outcome, refund_id=refund_id, now=now)


def reconcile_on_boot(
    conn: sqlite3.Connection, client: RazorpayClient, *, now: int
) -> list[ReconcileResult]:
    """Resolve every unresolved intent. Call before anything else touches money.

    Returns one result per intent. Any ``UNKNOWN`` in the list must stop the
    run: an intent whose fate cannot be established is precisely the state that
    makes a published number indefensible.
    """
    results: list[ReconcileResult] = []
    for row in unresolved_intents(conn):
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = reconcile_intent(conn, client, row, now=now)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        if result.resolution == Resolution.UNKNOWN:
            # Leave the row untouched so the next boot tries again. Recording a
            # guess would destroy the only evidence that it is unresolved.
            conn.execute("ROLLBACK")
        else:
            conn.execute("COMMIT")
        results.append(result)
    return results


def summarise(results: list[ReconcileResult]) -> str:
    counts: dict[str, int] = {}
    for r in results:
        counts[r.resolution] = counts.get(r.resolution, 0) + 1
    parts = [f"{k}={v}" for k, v in sorted(counts.items())]
    surprising = [r for r in results if r.surprising]
    line = f"reconciled {len(results)} intent(s): " + " ".join(parts) if results else (
        "no unresolved intents"
    )
    if surprising:
        line += (
            f" · {len(surprising)} executed despite state WRITTEN — the process died "
            "after the request reached Razorpay but before it was recorded"
        )
    return line


def results_to_json(results: list[ReconcileResult]) -> str:
    return json.dumps(
        [
            {
                "intent_id": r.intent_id,
                "prior_state": r.prior_state,
                "resolution": r.resolution,
                "refund_id": r.refund_id,
                "ledger_amount_paise": r.ledger_amount_paise,
                "surprising": r.surprising,
                "detail": r.detail,
            }
            for r in results
        ],
        indent=2,
        sort_keys=True,
    )

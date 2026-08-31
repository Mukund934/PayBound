"""Refund execution. At most once, and the ledger decides what happened.

This is the only code path in the project that can move money, so the whole
module is written around one sentence:

> The intent is durable before the first byte leaves; the POST happens exactly
> once; and an ambiguous outcome is resolved by **reading Razorpay**, never by
> asking this process what it thinks it did.

Order of operations, and why each step is where it is
------------------------------------------------------
1. **Pre-flight ledger read.** The aggregate bound is asserted against a *fresh*
   read of the per-payment refunds collection, immediately before the POST — not
   against cached state, and not against ``amount_refunded``. This is I-08.
2. **``mark_post_sent`` before the socket.** Marking first can only over-report,
   which a read-back recovers from. Marking after can under-report, which
   nothing recovers from.
3. **One POST. No retry.** The single exception is 429, which means the request
   was *not accepted*.
4. **Read-back at T+3s and T+15s, matched on ``receipt``.** Our receipt is a
   pure function of ``intent_id``, so it is the join key between our intent and
   Razorpay's object.
5. **Byte-exact amount assertion.** The refund in the ledger must carry exactly
   the amount the policy computed. This is I-03's empirical half; the structural
   half is that no tool accepts an amount at all.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from paybound.core.money import Paise, add
from paybound.ids import intent_id_from_receipt
from paybound.rail.client import RawResponse, RazorpayClient
from paybound.rail.errors import MAX_429_RETRIES, Bucket, Disposition, Outcome, classify

__all__ = [
    "AggregateBoundViolation",
    "AmountMismatch",
    "RefundResult",
    "assert_aggregate_bound",
    "execute_refund",
    "preflight_refund_total",
    "read_back",
]

# The lock's figures. Read-back is not one call: a refund created moments ago
# may not be visible on the first read, and treating "absent at T+0" as
# "never created" is how a run double-refunds.
_READBACK_DELAYS_S: tuple[int, ...] = (3, 15)


class AggregateBoundViolation(Exception):
    """The bound failed against a fresh ledger read. No POST is attempted."""


class AmountMismatch(Exception):
    """The ledger's refund amount is not the amount policy computed.

    This is the loudest failure in the project. It means either the request
    bytes were rebuilt somewhere, or something other than ``policy_amount``
    reached the wire.
    """


@dataclass(slots=True)
class RefundResult:
    intent_id: str
    outcome: Outcome
    refund_id: str | None = None
    ledger_amount_paise: int | None = None
    ledger_status: str | None = None
    attempts: int = 0
    raw_responses: list[dict[str, Any]] = field(default_factory=list)

    @property
    def executed(self) -> bool:
        return self.outcome.bucket is Bucket.EXECUTED and self.refund_id is not None


def preflight_refund_total(client: RazorpayClient, payment_id: str) -> tuple[Paise, list[dict]]:
    """Sum the per-payment refunds collection. Fresh, every time.

    Deliberately sums the collection rather than reading ``amount_refunded``.
    KG-1 C4 showed the field is synchronous on this account — it incremented at
    t+0 while the refund was still ``pending`` — so both are correct today. The
    sum is used anyway because it removes a dependency on undocumented timing
    for the price of one read, and because a bound that can be beaten by issuing
    two requests quickly is not a bound.
    """
    resp = client.list_payment_refunds(payment_id)
    if not resp.ok or not isinstance(resp.body, dict):
        raise AggregateBoundViolation(
            f"could not read the refunds collection for the bound "
            f"(status={resp.status}, transport={resp.transport_error}). "
            "The bound is asserted against the ledger or it is not asserted."
        )
    items = resp.body.get("items") or []
    total = 0
    for item in items:
        amount = item.get("amount")
        if not isinstance(amount, int):
            raise AggregateBoundViolation(
                f"refund {item.get('id')!r} has a non-integer amount {amount!r}; "
                "the bound cannot be computed from an unreadable ledger"
            )
        total += amount
    return total, list(items)


def assert_aggregate_bound(
    *, existing_paise: Paise, proposed_paise: Paise, payment_amount_paise: Paise
) -> None:
    """``sum(existing) + proposed <= payment.amount``. I-08.

    Raises rather than returning a bool, so a caller cannot forget to check the
    result. The mutation test for I-10 deletes this call and asserts CI turns
    red — a gate that cannot fail is decoration.
    """
    if add(existing_paise, proposed_paise) > payment_amount_paise:
        raise AggregateBoundViolation(
            f"aggregate bound: {existing_paise} already refunded + {proposed_paise} "
            f"proposed exceeds payment.amount {payment_amount_paise}"
        )


def read_back(
    client: RazorpayClient,
    payment_id: str,
    *,
    receipt: str,
    delays_s: tuple[int, ...] = _READBACK_DELAYS_S,
    sleep: Any = time.sleep,
) -> dict[str, Any] | None:
    """Find our refund in Razorpay's ledger by ``receipt``. External ground truth.

    ``receipt`` is a pure function of ``intent_id``, which makes it the join key
    between what we intended and what exists. Anything in the window whose
    receipt does not parse back to an intent id is FOREIGN and is not ours.
    """
    for delay in delays_s:
        sleep(delay)
        resp = client.list_payment_refunds(payment_id)
        if not resp.ok or not isinstance(resp.body, dict):
            continue
        for item in resp.body.get("items") or []:
            if item.get("receipt") == receipt:
                return dict(item)
    return None


def execute_refund(
    client: RazorpayClient,
    *,
    intent_id: str,
    payment_id: str,
    amount_paise: Paise,
    receipt: str,
    idem_key: str,
    payment_amount_paise: Paise,
    notes: dict[str, str] | None = None,
    on_post_sent: Any = None,
    sleep: Any = time.sleep,
) -> RefundResult:
    """Execute one refund intent. At most once, whatever happens.

    ``on_post_sent`` is called immediately before the socket write and must
    durably record the transition. It is a required collaborator in practice —
    passing ``None`` is allowed only for tests that are not exercising
    durability, and the ordering is what I-07 depends on.
    """
    # --- 1. the bound, against a fresh ledger read ------------------------
    existing, _items = preflight_refund_total(client, payment_id)
    assert_aggregate_bound(
        existing_paise=existing,
        proposed_paise=amount_paise,
        payment_amount_paise=payment_amount_paise,
    )

    result = RefundResult(intent_id=intent_id, outcome=None)  # type: ignore[arg-type]

    # --- 2. durable POST_SENT, then the socket ---------------------------
    if on_post_sent is not None:
        on_post_sent()

    attempt = 0
    resp: RawResponse | None = None
    outcome: Outcome | None = None
    while True:
        attempt += 1
        resp = client.create_refund(
            payment_id,
            amount_paise=amount_paise,
            receipt=receipt,
            idem_key=idem_key,
            notes=notes,
        )
        result.raw_responses.append(
            {
                "attempt": attempt,
                "status": resp.status,
                "elapsed_ms": resp.elapsed_ms,
                "body": resp.body,
                "transport_error": resp.transport_error,
            }
        )
        outcome = classify(
            status=resp.status, body=resp.body, transport_error=resp.transport_error
        )
        # The only legal retry in the system: a 429 means the request was never
        # accepted, so re-sending the identical bytes cannot create a second
        # object.
        if outcome.disposition is Disposition.RETRY_AFTER_BACKOFF and attempt <= MAX_429_RETRIES:
            sleep(min(2**attempt, 30))
            continue
        break

    result.attempts = attempt
    result.outcome = outcome

    # --- 3. resolve by reading the ledger --------------------------------
    if outcome.bucket is Bucket.EXECUTED and isinstance(resp.body, dict):
        result.refund_id = resp.body.get("id")
        result.ledger_amount_paise = resp.body.get("amount")
        result.ledger_status = resp.body.get("status")

    if outcome.disposition is Disposition.READ_BACK_TO_DISAMBIGUATE:
        found = read_back(client, payment_id, receipt=receipt, sleep=sleep)
        if found is not None:
            result.refund_id = found.get("id")
            result.ledger_amount_paise = found.get("amount")
            result.ledger_status = found.get("status")
            result.outcome = Outcome(
                bucket=Bucket.EXECUTED,
                disposition=Disposition.RECORD_AND_READ_BACK,
                reason=(
                    "ambiguous response, but the refund exists in the ledger under "
                    "our receipt. The ledger decides, not the response."
                ),
                status=outcome.status,
                error_description=outcome.error_description,
            )
        else:
            result.outcome = Outcome(
                bucket=Bucket.UNKNOWN,
                disposition=Disposition.QUARANTINE,
                reason=(
                    "ambiguous response and no refund under our receipt after "
                    "T+3s and T+15s. Genuinely unknown. Bucket 3, guard red, "
                    "and never re-POSTed."
                ),
                status=outcome.status,
                error_description=outcome.error_description,
            )

    # --- 4. I-03's empirical half ----------------------------------------
    if result.executed:
        if result.ledger_amount_paise != amount_paise:
            raise AmountMismatch(
                f"refund {result.refund_id} carries {result.ledger_amount_paise} paise "
                f"but policy computed {amount_paise}. Either the request bytes were "
                "rebuilt or something other than policy_amount reached the wire. "
                "This must stop the run."
            )
        if result.refund_id and intent_id_from_receipt(receipt) != intent_id:
            raise AmountMismatch(
                f"receipt {receipt!r} does not parse back to intent {intent_id}; "
                "ground-truth attribution is broken"
            )

    return result

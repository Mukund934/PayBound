"""Amount functions. Pure, paise, trusted-state inputs only.

**This module is the answer to invariant I-03.** The refund amount is a total
function of ``(clause, trusted state)``. The model has no parameter it could use
to influence it — ``request_refund(cap_w, reason_code)`` has no ``amount`` field
to carry one — and the value asserted byte-exact against Razorpay's ledger after
execution is the value computed here.

Every function raises rather than returning a fallback. A clause whose amount
cannot be computed must stop the decision, not quietly refund a plausible
number: the plausible number is precisely what a reviewer would call an
unauthorised refund.
"""

from __future__ import annotations

from paybound.core.money import Paise, as_paise
from paybound.core.types import ReasonCode, TrustedState

__all__ = ["full_payment", "line_price_difference", "mismatched_line_total", "never"]


class AmountUncomputable(Exception):
    """Raised when a clause's amount cannot be derived from trusted state.

    Caught by ``decide()`` and converted to ESCALATE. Never caught and replaced
    with a default.
    """


def full_payment(s: TrustedState) -> Paise:
    """The whole captured amount.

    Used by ``DUPLICATE_CHARGE``, ``NOT_DELIVERED`` and ``CANCELLED_IN_WINDOW``.
    Reads ``payment.amount``, which Razorpay does not mutate, so the value is
    stable across the read-modify-write window.
    """
    return as_paise(s.payment.amount_paise, field="payment.amount")


def line_price_difference(s: TrustedState) -> Paise:
    """The overcharge only: sum over lines of ``(paid - catalogue) * qty``.

    Deliberately not the line total and never the full payment. A price mismatch
    on one line of a five-line order owes the difference on that line, and a
    clause that refunds more than the harm is the unbounded-drain path the
    review named.
    """
    at = s.order_created_at_epoch_s
    if at is None:
        raise AmountUncomputable("order.created_at is unavailable; cannot price the order")

    total = 0
    for line in s.lines:
        catalogue = s.catalogue_price_at(line.sku, at)
        if catalogue is None:
            raise AmountUncomputable(
                f"no catalogue price for {line.sku!r} at order time; "
                "the overcharge is undefined and must not be guessed"
            )
        per_unit = line.unit_price_paid_paise - catalogue
        if per_unit > 0:
            total += per_unit * line.qty

    if total <= 0:
        raise AmountUncomputable(
            "no line was paid above its catalogue price; a zero-rupee ALLOW is a "
            "decision, not an abstention"
        )
    return as_paise(total, field="line_price_difference")


def mismatched_line_total(s: TrustedState) -> Paise:
    """The value of the line the returns intake says is wrong or damaged.

    T2 only. Reached only when a physical intake scan exists, so the SKU it
    names is the one being adjudicated.
    """
    intake = s.fulfilment.intake_sku
    if intake is not None:
        # WRONG_ITEM: the customer was billed for a SKU they did not receive.
        # Refund the ordered line, not the intake line — the intake SKU is the
        # wrong thing that arrived and has no price in this order.
        ordered = {ln.sku for ln in s.lines}
        if intake in ordered:
            raise AmountUncomputable(
                f"intake sku {intake!r} is in the order; this is not a wrong-item case"
            )

    if len(s.lines) != 1:
        # With several lines and no per-line claim in trusted state, which line
        # is owed is not determinable. Escalate rather than pick one.
        raise AmountUncomputable(
            f"{len(s.lines)} order lines and no trusted per-line claim; "
            "which line is owed is not determinable from trusted state"
        )
    return as_paise(s.lines[0].line_total_paise, field="mismatched_line_total")


def never(s: TrustedState) -> Paise:
    """For clauses that are never autonomous.

    ``QUALITY_NOT_AS_DESCRIBED``, ``CHANGED_MIND_LATE`` and ``UNCLASSIFIED``
    have no trusted predicate at any tier. Calling this is a bug in ``decide()``,
    not a policy outcome, so it raises rather than returning zero.
    """
    raise AmountUncomputable(
        "this clause is never autonomous; decide() must not have reached its amount_fn"
    )


AMOUNT_FN_NAMES: dict[ReasonCode, str] = {
    ReasonCode.DUPLICATE_CHARGE: "full_payment",
    ReasonCode.PRICE_MISMATCH: "line_price_difference",
    ReasonCode.NOT_DELIVERED: "full_payment",
    ReasonCode.CANCELLED_IN_WINDOW: "full_payment",
    ReasonCode.WRONG_ITEM: "mismatched_line_total",
    ReasonCode.ARRIVED_DAMAGED: "mismatched_line_total",
    ReasonCode.QUALITY_NOT_AS_DESCRIBED: "never",
    ReasonCode.CHANGED_MIND_LATE: "never",
    ReasonCode.UNCLASSIFIED: "never",
}
"""Name-only mirror of the table's amount functions, for the report and the
audit event. Kept beside the functions so a reviewer can check the mapping
without reading the table's callables."""

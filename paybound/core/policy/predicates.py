"""Pure predicates over trusted state. No clock, no network, no model.

Each predicate is a total function ``TrustedState -> PredicateResult``. It
returns the Kleene value *and* the field it read and the value it observed, so
the audit trail is produced by the same code that makes the decision rather than
being narrated afterwards by something that might disagree with it. A separate
"explanation" layer is a layer that can lie.

Two rules every predicate here obeys:

1. **A field that could not be read is UNKNOWN, never False.** Every `None`
   observed becomes `Kleene.UNKNOWN`, which escalates.
2. **Only positive records authorise.** No predicate concludes that something
   did not happen from the absence of a record that it did.
"""

from __future__ import annotations

from collections.abc import Callable

from paybound.core.types import (
    LEDGER_NONDELIVERY_STATES,
    Kleene,
    PredicateResult,
    ReasonCode,
    TrustedState,
)

__all__ = [
    "Predicate",
    "duplicate_sibling_capture",
    "group_not_settled",
    "is_later_of_duplicate_pair",
    "line_overcharged",
    "no_prior_refund_for",
    "not_dispatched",
    "nothing_refunded_yet",
    "order_status_paid",
    "positive_carrier_nondelivery_record",
    "return_window_elapsed",
    "returns_intake_damage_record",
    "returns_intake_sku_mismatch",
    "single_capture_in_group",
    "within_cancellation_window",
]

Predicate = Callable[[TrustedState], PredicateResult]

# Two captures are "the same charge twice" only if they are close in time. Thirty
# minutes is the lock's figure: long enough to cover a retry after a failed
# callback, short enough that two deliberate purchases of the same item on the
# same card do not qualify.
_DUPLICATE_WINDOW_S = 30 * 60

# Rs 1.00. A paid price above catalogue by less than this is rounding, a coupon
# reconciliation, or a shipping line — not an overcharge worth a refund.
_PRICE_TOLERANCE_PAISE = 100

_CANCELLATION_WINDOW_S = 24 * 60 * 60


def group_not_settled(s: TrustedState) -> PredicateResult:
    """A settled duplicate-charge claim blocks every other clause on the group.

    Without this, a buyer who legitimately recovers a duplicate charge can then
    claim non-delivery on the surviving payment and keep the goods for free.
    """
    return PredicateResult(
        name="group_not_settled",
        source_field="group.settled",
        observed=s.group.settled,
        result=Kleene.of(not s.group.settled),
    )


def single_capture_in_group(s: TrustedState) -> PredicateResult:
    """Exactly one capture. Two or more is tier NEVER for everything but
    ``DUPLICATE_CHARGE`` — that is the precondition-manufacture defence."""
    return PredicateResult(
        name="single_capture_in_group",
        source_field="group.capture_count",
        observed=s.group.capture_count,
        result=Kleene.of(s.group.capture_count == 1),
    )


def duplicate_sibling_capture(s: TrustedState) -> PredicateResult:
    """A sibling capture at the same amount, same method, within 30 minutes.

    This is the one predicate in the table that two independent ledger objects
    corroborate, which is why ``DUPLICATE_CHARGE`` is the hero case: no part of
    it rests on anything the customer said.
    """
    created = s.payment.created_at_epoch_s
    if created is None:
        return PredicateResult(
            name="duplicate_sibling_capture",
            source_field="payment.created_at",
            observed=None,
            result=Kleene.UNKNOWN,
        )
    matches = [
        sib
        for sib in s.siblings
        if sib.amount_paise == s.payment.amount_paise
        and sib.method == s.payment.method
        and abs(sib.created_at_epoch_s - created) <= _DUPLICATE_WINDOW_S
    ]
    return PredicateResult(
        name="duplicate_sibling_capture",
        source_field="siblings[amount,method,created_at]",
        observed={"matching_siblings": len(matches), "window_s": _DUPLICATE_WINDOW_S},
        result=Kleene.of(len(matches) >= 1),
    )


def is_later_of_duplicate_pair(s: TrustedState) -> PredicateResult:
    """Refund the *later* capture, deterministically.

    Both payments in a duplicate pair satisfy the symmetric predicate, so without
    a tie-break the pair could yield two refunds — one per payment — each of
    which passes its own aggregate bound. Ordering by capture time and refunding
    only the later one makes the choice a property of the ledger, not of which
    ticket happened to arrive first.
    """
    created = s.payment.created_at_epoch_s
    if created is None:
        return PredicateResult(
            name="is_later_of_duplicate_pair",
            source_field="payment.created_at",
            observed=None,
            result=Kleene.UNKNOWN,
        )
    if not s.siblings:
        return PredicateResult(
            name="is_later_of_duplicate_pair",
            source_field="siblings",
            observed=0,
            result=Kleene.FALSE,
        )
    latest_sibling = max(sib.created_at_epoch_s for sib in s.siblings)
    return PredicateResult(
        name="is_later_of_duplicate_pair",
        source_field="payment.created_at vs max(siblings.created_at)",
        observed={"this": created, "latest_sibling": latest_sibling},
        result=Kleene.of(created > latest_sibling),
    )


def nothing_refunded_yet(s: TrustedState) -> PredicateResult:
    """No prior refund on this payment.

    Reads the summed refunds collection rather than ``amount_refunded``, which
    KG-1 C4 exists to characterise and which may lag the refund object.
    """
    return PredicateResult(
        name="nothing_refunded_yet",
        source_field="sum(payment.refunds[].amount)",
        observed=s.payment.prior_refund_total_paise,
        result=Kleene.of(s.payment.prior_refund_total_paise == 0),
    )


def order_status_paid(s: TrustedState) -> PredicateResult:
    return PredicateResult(
        name="order_status_paid",
        source_field="order.status",
        observed=s.order_status,
        result=Kleene.UNKNOWN if s.order_status is None else Kleene.of(s.order_status == "paid"),
    )


def positive_carrier_nondelivery_record(s: TrustedState) -> PredicateResult:
    """A carrier state that positively records non-delivery, **plus a scan id**.

    The scan id is not decoration. A fulfilment row can be written by our own
    seeder; a scan id is the handle a merchant can take to the carrier. Requiring
    both is what stops "the tracking page has not updated in a while" from
    becoming an autonomous refund.
    """
    state = s.fulfilment.state
    scan = s.fulfilment.carrier_scan_id
    if state is None:
        return PredicateResult(
            name="positive_carrier_nondelivery_record",
            source_field="fulfilment.state",
            observed=None,
            result=Kleene.UNKNOWN,
        )
    has_state = state in LEDGER_NONDELIVERY_STATES
    if has_state and not scan:
        # The state claims non-delivery but there is no carrier handle behind
        # it. That is exactly the shape of a broken carrier integration.
        return PredicateResult(
            name="positive_carrier_nondelivery_record",
            source_field="fulfilment.carrier_scan_id",
            observed={"state": str(state), "carrier_scan_id": None},
            result=Kleene.UNKNOWN,
        )
    return PredicateResult(
        name="positive_carrier_nondelivery_record",
        source_field="fulfilment.state + carrier_scan_id",
        observed={"state": str(state), "has_scan_id": bool(scan)},
        result=Kleene.of(has_state),
    )


def within_cancellation_window(s: TrustedState) -> PredicateResult:
    """Within 24 hours of capture. A missing ``created_at`` is UNKNOWN.

    This is the predicate the review named: under boolean logic a missing
    timestamp reads as "outside the window", which denies — and a DENY caused by
    a failed read is indistinguishable, in the metrics, from a DENY caused by
    policy.
    """
    created = s.payment.created_at_epoch_s
    if created is None:
        return PredicateResult(
            name="within_cancellation_window",
            source_field="payment.created_at",
            observed=None,
            result=Kleene.UNKNOWN,
        )
    age = s.now_epoch_s - created
    return PredicateResult(
        name="within_cancellation_window",
        source_field="now - payment.created_at",
        observed={"age_s": age, "window_s": _CANCELLATION_WINDOW_S},
        result=Kleene.of(0 <= age <= _CANCELLATION_WINDOW_S),
    )


def not_dispatched(s: TrustedState) -> PredicateResult:
    state = s.fulfilment.state
    if state is None:
        return PredicateResult(
            name="not_dispatched",
            source_field="fulfilment.state",
            observed=None,
            result=Kleene.UNKNOWN,
        )
    return PredicateResult(
        name="not_dispatched",
        source_field="fulfilment.state",
        observed=str(state),
        result=Kleene.of(state.value == "not_dispatched"),
    )


def line_overcharged(s: TrustedState) -> PredicateResult:
    """Some line was paid above its catalogue price at order time, by > Rs 1.

    Compared against the price *in force when the order was created*, not the
    price now — otherwise every post-order price cut becomes a refundable
    overcharge, and the merchant funds its own discounts retroactively.
    """
    at = s.order_created_at_epoch_s
    if at is None:
        return PredicateResult(
            name="line_overcharged",
            source_field="order.created_at",
            observed=None,
            result=Kleene.UNKNOWN,
        )
    unpriced = [ln.sku for ln in s.lines if s.catalogue_price_at(ln.sku, at) is None]
    if unpriced:
        # A SKU with no catalogue price at order time cannot be adjudicated. It
        # must not default to "not overcharged" — that is a silent pass.
        return PredicateResult(
            name="line_overcharged",
            source_field="catalogue_price_at(sku, order.created_at)",
            observed={"skus_without_catalogue_price": unpriced},
            result=Kleene.UNKNOWN,
        )
    overcharged = [
        ln.sku
        for ln in s.lines
        if ln.unit_price_paid_paise
        > (s.catalogue_price_at(ln.sku, at) or 0) + _PRICE_TOLERANCE_PAISE
    ]
    return PredicateResult(
        name="line_overcharged",
        source_field="line.unit_price_paid vs catalogue_price_at(order.created_at)",
        observed={"overcharged_skus": overcharged, "tolerance_paise": _PRICE_TOLERANCE_PAISE},
        result=Kleene.of(len(overcharged) >= 1),
    )


def returns_intake_sku_mismatch(s: TrustedState) -> PredicateResult:
    """T2 only. Requires a physical returns-intake scan.

    At T0 and T1 the merchant has no such scan, so this is UNKNOWN and
    ``WRONG_ITEM`` escalates. That is the finding, not a limitation: no quantity
    of payment data makes "they sent the wrong thing" decidable.
    """
    intake = s.fulfilment.intake_sku
    if intake is None:
        return PredicateResult(
            name="returns_intake_sku_mismatch",
            source_field="fulfilment.intake_sku",
            observed=None,
            result=Kleene.UNKNOWN,
        )
    ordered = {ln.sku for ln in s.lines}
    return PredicateResult(
        name="returns_intake_sku_mismatch",
        source_field="fulfilment.intake_sku vs order lines",
        observed={"intake_sku": intake, "ordered_skus": sorted(ordered)},
        result=Kleene.of(intake not in ordered),
    )


def returns_intake_damage_record(s: TrustedState) -> PredicateResult:
    """T2 only. A damage record written at physical intake."""
    rec = s.fulfilment.intake_damage_record
    return PredicateResult(
        name="returns_intake_damage_record",
        source_field="fulfilment.intake_damage_record",
        observed=rec,
        result=Kleene.UNKNOWN if rec is None else Kleene.of(bool(rec)),
    )


def no_prior_refund_for(code: ReasonCode) -> Predicate:
    """Clause-scoped replay guard.

    The aggregate bound alone does not close ``PRICE_MISMATCH``: it reads the
    immutable ``payment.amount``, so a line difference can be re-claimed until
    the payment is drained. This predicate is what makes each reason
    single-shot per payment.
    """

    def _p(s: TrustedState) -> PredicateResult:
        return PredicateResult(
            name=f"no_prior_refund_for[{code.value}]",
            source_field="prior_refund_reasons",
            observed=sorted(r.value for r in s.prior_refund_reasons),
            result=Kleene.of(code not in s.prior_refund_reasons),
        )

    return _p


def return_window_elapsed(s: TrustedState) -> PredicateResult:
    """Used by ``CHANGED_MIND_LATE``, which is the table's one explicit DENY.

    An in-window change of mind on an undispatched order is a legitimate
    cancellation and falls through to clause 4. Once the window has demonstrably
    elapsed there is no policy under which it is owed, and saying DENY is more
    honest than escalating a case a human will also refuse.
    """
    created = s.payment.created_at_epoch_s
    if created is None:
        return PredicateResult(
            name="return_window_elapsed",
            source_field="payment.created_at",
            observed=None,
            result=Kleene.UNKNOWN,
        )
    window_s = s.return_window_days * 24 * 60 * 60
    age = s.now_epoch_s - created
    return PredicateResult(
        name="return_window_elapsed",
        source_field="now - payment.created_at",
        observed={"age_s": age, "window_s": window_s},
        result=Kleene.of(age > window_s),
    )

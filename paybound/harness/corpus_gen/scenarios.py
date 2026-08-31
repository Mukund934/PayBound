"""Scenario truths and the trusted-state fixtures they imply.

**The scientific boundary.** ``tests/arch/test_boundaries.py`` forbids anything
under ``harness/corpus_gen/`` from importing ``core/policy/`` or ``broker/``.
The attack author must not be able to read the defence, or the corpus can be
overfit to it. That is the answer to *"you wrote both the attack and the
defence"* and it is enforced by a test rather than by intention.

What that boundary means in practice: this module describes **what happened in
the world**, and never what the policy would conclude about it. A
``ScenarioTruth`` says "two captures went through sixteen seconds apart" or "the
carrier marked it RTO with scan SR-8830155". The fixture encodes that world into
a ``TrustedState``. Whether the policy pays out is a separate question asked
later, by code this module cannot see.

Authoring order, which is load-bearing
--------------------------------------
1. The scenario truth is written first.
2. The oracle label is assigned from the truth — what a fair human reading the
   customer's message would call it.
3. The fixture is derived from the truth.
4. Only then is anything scored.

Assigning the oracle after seeing how the router behaved would make the whole
benchmark circular, so the seal freezes steps 1 to 3 before any routing is
observed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from paybound.core.types import (
    CatalogueLine,
    Fulfilment,
    FulfilmentState,
    OrderGroup,
    OrderLine,
    PaymentFacts,
    ReasonCode,
    SiblingPayment,
    TrustedState,
)

__all__ = ["CATALOGUE", "NOW", "ScenarioTruth", "build_state"]

# A fixed epoch so the whole corpus is reproducible. `core/` has no clock; every
# window is computed against this value, which is committed in the seal.
NOW = 1_756_000_000
HOUR = 3600
DAY = 24 * HOUR

# Six SKUs, priced in paise, all under the Rs 3,000 auto_max ceiling so that no
# legitimate item is gated by the ceiling rather than by its clause.
CATALOGUE: tuple[CatalogueLine, ...] = (
    CatalogueLine("SKU-TEE-01", 249_900, NOW - 400 * DAY),
    CatalogueLine("SKU-KURTA-02", 189_900, NOW - 400 * DAY),
    CatalogueLine("SKU-SHOE-03", 279_900, NOW - 400 * DAY),
    CatalogueLine("SKU-EARBUD-04", 199_900, NOW - 400 * DAY),
    CatalogueLine("SKU-SERUM-05", 79_900, NOW - 400 * DAY),
    CatalogueLine("SKU-BOTTLE-06", 59_900, NOW - 400 * DAY),
)
_PRICE = {c.sku: c.unit_price_paise for c in CATALOGUE}


@dataclass(frozen=True, slots=True)
class ScenarioTruth:
    """What actually happened. Not what any policy thinks about it.

    Every field is a fact about the world an investigator could check: how many
    times the card was charged, what the carrier's last scan said, whether the
    goods reached the customer. Nothing here names a clause or a precondition.
    """

    scenario_id: str
    sku: str
    qty: int = 1

    # Payment world
    captures: int = 1
    capture_gap_s: int | None = None  # gap between duplicate captures
    age_s: int = 4 * DAY
    already_refunded_paise: int = 0
    paid_over_catalogue_paise: int = 0

    # Fulfilment world
    carrier_state: FulfilmentState | None = None
    carrier_scan_id: str | None = None
    intake_sku: str | None = None
    intake_damage: str | None = None

    # The single most important field for oracle honesty. A human deciding
    # WRONG_ITEM or ARRIVED_DAMAGED must be able to say the customer physically
    # received something; a human deciding NOT_DELIVERED must be able to say
    # they did not. When this is False, no damage or wrong-item claim can be
    # honestly true, and the corpus admission gate rejects the pairing.
    goods_reached_customer: bool = True

    order_status: str | None = "paid"
    group_settled: bool = False
    prior_reasons: tuple[ReasonCode, ...] = ()
    notes: str = ""

    @property
    def unit_price_paid_paise(self) -> int:
        return _PRICE[self.sku] + self.paid_over_catalogue_paise

    @property
    def payment_amount_paise(self) -> int:
        return self.unit_price_paid_paise * self.qty

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["carrier_state"] = self.carrier_state.value if self.carrier_state else None
        d["prior_reasons"] = [r.value for r in self.prior_reasons]
        return d


def build_state(truth: ScenarioTruth, *, now: int = NOW) -> TrustedState:
    """Encode a scenario truth as the trusted state the merchant would hold.

    Nothing here consults the policy. Two fields are worth their own note:

    ``prior_refund_total_paise`` and ``amount_refunded_paise`` are set to the
    same value, which is what KG-1 observed on the live account — Razorpay
    increments ``amount_refunded`` at refund creation rather than at settlement.
    The runtime still sums the collection rather than reading the field; the
    fixture matches reality so the two agree.

    ``siblings`` is populated only when the truth says the card was charged more
    than once. A duplicate is a fact about the ledger, so it must appear as a
    second ledger object rather than as an assertion in prose.
    """
    created_at = now - truth.age_s
    siblings: tuple[SiblingPayment, ...] = ()
    if truth.captures >= 2:
        gap = truth.capture_gap_s if truth.capture_gap_s is not None else 16
        siblings = tuple(
            SiblingPayment(
                payment_id_hash=f"sha256:{truth.scenario_id}-sib{i}",
                amount_paise=truth.payment_amount_paise,
                method="card",
                created_at_epoch_s=created_at - gap * (i + 1),
            )
            for i in range(truth.captures - 1)
        )

    payment = PaymentFacts(
        amount_paise=truth.payment_amount_paise,
        amount_refunded_paise=truth.already_refunded_paise,
        prior_refund_total_paise=truth.already_refunded_paise,
        created_at_epoch_s=created_at,
        method="card",
        status="captured",
        captured=True,
    )

    return TrustedState(
        now_epoch_s=now,
        payment=payment,
        order_status=truth.order_status,
        order_created_at_epoch_s=created_at - 300,
        lines=(OrderLine(truth.sku, truth.qty, truth.unit_price_paid_paise),),
        catalogue=CATALOGUE,
        fulfilment=Fulfilment(
            state=truth.carrier_state,
            carrier_scan_id=truth.carrier_scan_id,
            intake_sku=truth.intake_sku,
            intake_damage_record=truth.intake_damage,
        ),
        group=OrderGroup(
            group_id=f"grp_{truth.scenario_id}",
            capture_count=truth.captures,
            settled=truth.group_settled,
        ),
        siblings=siblings,
        prior_refund_reasons=frozenset(truth.prior_reasons),
        return_window_days=7,
    )


def state_to_json(state: TrustedState) -> dict[str, Any]:
    """Serialise a fixture so ``verify.py`` can read it without importing us."""
    return {
        "now_epoch_s": state.now_epoch_s,
        "payment": {
            "amount_paise": state.payment.amount_paise,
            "amount_refunded_paise": state.payment.amount_refunded_paise,
            "prior_refund_total_paise": state.payment.prior_refund_total_paise,
            "created_at_epoch_s": state.payment.created_at_epoch_s,
            "method": state.payment.method,
            "status": state.payment.status,
            "captured": state.payment.captured,
        },
        "order_status": state.order_status,
        "order_created_at_epoch_s": state.order_created_at_epoch_s,
        "lines": [
            {"sku": ln.sku, "qty": ln.qty, "unit_price_paid_paise": ln.unit_price_paid_paise}
            for ln in state.lines
        ],
        "fulfilment": {
            "state": state.fulfilment.state.value if state.fulfilment.state else None,
            "carrier_scan_id": state.fulfilment.carrier_scan_id,
            "intake_sku": state.fulfilment.intake_sku,
            "intake_damage_record": state.fulfilment.intake_damage_record,
        },
        "group": {
            "group_id": state.group.group_id,
            "capture_count": state.group.capture_count,
            "settled": state.group.settled,
        },
        "siblings": [
            {
                "payment_id_hash": s.payment_id_hash,
                "amount_paise": s.amount_paise,
                "method": s.method,
                "created_at_epoch_s": s.created_at_epoch_s,
            }
            for s in state.siblings
        ],
        "prior_refund_reasons": sorted(r.value for r in state.prior_refund_reasons),
        "return_window_days": state.return_window_days,
    }

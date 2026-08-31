"""Trusted-state builders for the test suite.

These construct ``TrustedState`` the way the projection layer will: every field
explicit, no hidden defaults that could make a test pass for the wrong reason.
The hero case is Rs 2,499.00 (249900 paise) throughout, because that is the
number on screen in the demo and a test suite that exercises a different amount
is not testing the thing being demonstrated.
"""

from __future__ import annotations

import pytest

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

HERO_PAISE = 249_900  # Rs 2,499.00
NOW = 1_756_000_000  # a fixed epoch; core/ has no clock, so tests supply one
HOUR = 3600
DAY = 24 * HOUR


def payment(
    *,
    amount: int = HERO_PAISE,
    prior_refund_total: int = 0,
    amount_refunded: int | None = None,
    created_at: int | None = NOW - HOUR,
    method: str | None = "upi",
    status: str | None = "captured",
    captured: bool | None = True,
) -> PaymentFacts:
    return PaymentFacts(
        amount_paise=amount,
        amount_refunded_paise=prior_refund_total if amount_refunded is None else amount_refunded,
        prior_refund_total_paise=prior_refund_total,
        created_at_epoch_s=created_at,
        method=method,
        status=status,
        captured=captured,
    )


def state(
    *,
    pay: PaymentFacts | None = None,
    order_status: str | None = "paid",
    order_created_at: int | None = NOW - HOUR,
    lines: tuple[OrderLine, ...] | None = None,
    catalogue: tuple[CatalogueLine, ...] | None = None,
    fulfilment: Fulfilment | None = None,
    group: OrderGroup | None = None,
    siblings: tuple[SiblingPayment, ...] = (),
    prior_reasons: frozenset[ReasonCode] = frozenset(),
    now: int = NOW,
) -> TrustedState:
    return TrustedState(
        now_epoch_s=now,
        payment=pay if pay is not None else payment(),
        order_status=order_status,
        order_created_at_epoch_s=order_created_at,
        lines=lines if lines is not None else (OrderLine("SKU-TEE-01", 1, HERO_PAISE),),
        catalogue=catalogue
        if catalogue is not None
        else (CatalogueLine("SKU-TEE-01", HERO_PAISE, NOW - 30 * DAY),),
        fulfilment=fulfilment if fulfilment is not None else Fulfilment(),
        group=group if group is not None else OrderGroup("grp_1", capture_count=1),
        siblings=siblings,
        prior_refund_reasons=prior_reasons,
    )


def duplicate_charge_state(**overrides: object) -> TrustedState:
    """The hero case: a sibling capture 16 seconds earlier, same amount, same
    method. This is the state the demo shows at 1:58."""
    defaults: dict[str, object] = {
        "siblings": (
            SiblingPayment(
                payment_id_hash="sha256:aa" * 16,
                amount_paise=HERO_PAISE,
                method="upi",
                created_at_epoch_s=NOW - HOUR - 16,
            ),
        ),
        "group": OrderGroup("grp_dup", capture_count=2),
    }
    defaults.update(overrides)
    return state(**defaults)  # type: ignore[arg-type]


def not_delivered_state(**overrides: object) -> TrustedState:
    defaults: dict[str, object] = {
        "fulfilment": Fulfilment(
            state=FulfilmentState.RTO_INITIATED, carrier_scan_id="SCAN-77123"
        ),
    }
    defaults.update(overrides)
    return state(**defaults)  # type: ignore[arg-type]


def cancelled_in_window_state(**overrides: object) -> TrustedState:
    defaults: dict[str, object] = {
        "fulfilment": Fulfilment(state=FulfilmentState.NOT_DISPATCHED),
    }
    defaults.update(overrides)
    return state(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def hero() -> TrustedState:
    return duplicate_charge_state()

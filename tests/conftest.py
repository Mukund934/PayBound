"""Trusted-state builders for the test suite.

These construct ``TrustedState`` the way the projection layer will: every field
explicit, no hidden defaults that could make a test pass for the wrong reason.
The hero case is Rs 2,499.00 (249900 paise) throughout, because that is the
number on screen in the demo and a test suite that exercises a different amount
is not testing the thing being demonstrated.
"""

from __future__ import annotations

import os
from pathlib import Path

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
        "fulfilment": Fulfilment(state=FulfilmentState.NOT_PICKED_UP),
    }
    defaults.update(overrides)
    return state(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def hero() -> TrustedState:
    return duplicate_charge_state()


# ===========================================================================
# The suite must not run against a disabled aggregate bound
# ===========================================================================

_BOUND = "if add(existing_paise, proposed_paise) > payment_amount_paise:"
_REFUNDS = Path(__file__).resolve().parents[1] / "paybound" / "rail" / "refunds.py"


# I-10's harness deliberately runs a subprocess against a mutated file. The
# guard below must not fire there, or the mutation test passes for the wrong
# reason: the subprocess would exit on the guard instead of on the control test
# failing, and `mutated_rc != 0` would be satisfied without the bound ever being
# exercised. That is a hollowed-out mutation test, which is worse than none.
_HARNESS_ENV = "PB_I10_MUTATION_SUBPROCESS"


def _bound_is_live() -> bool:
    try:
        return _BOUND in _REFUNDS.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - the file always exists in a checkout
        return True


@pytest.fixture(scope="session", autouse=True)
def _aggregate_bound_is_not_left_disabled():
    """Refuse to run, and refuse to finish, with the money guard neutered.

    ``test_i10_deleting_the_aggregate_bound_turns_the_suite_red`` rewrites
    ``rail/refunds.py`` on disk to prove the bound is load-bearing, and restores
    it in a ``finally``. That is correct and it is not enough: a ``finally``
    does not run when the process is killed. Interrupt the suite mid-mutation --
    a Ctrl-C, a CI timeout, an editor's two-minute cap -- and the working tree is
    left with the single most important financial guard commented out.

    That happened twice in one afternoon here. The first time it was caught by
    eye during an unrelated audit; the second time it silently broke the suite
    with a confusing "the mutation target moved" error three tests away from the
    cause. Once is bad luck, twice is a missing guard.

    So the state is checked at both ends. Not repaired: repairing it would hide
    how often it happens, and the remedy is one command the message names.
    """
    if os.environ.get(_HARNESS_ENV):
        # Inside I-10's own subprocess. Mutation is the point here.
        yield
        return
    if not _bound_is_live():
        pytest.exit(
            "REFUSING TO RUN: the aggregate bound in paybound/rail/refunds.py is "
            "disabled. A previous run was almost certainly killed mid-mutation by "
            "I-10's harness. Restore it before doing anything else:\n"
            "    git checkout -- paybound/rail/refunds.py",
            returncode=3,
        )
    yield
    if not _bound_is_live():
        pytest.exit(
            "THIS RUN left the aggregate bound disabled in the working tree. Do "
            "not commit. Restore it with:\n"
            "    git checkout -- paybound/rail/refunds.py",
            returncode=3,
        )

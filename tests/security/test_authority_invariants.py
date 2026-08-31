"""THE FILE. The invariants that make PayBound's claim checkable.

This is the file to point a reviewer at first. Every test here corresponds to a
numbered invariant in the implementation contract, and each one is written so
that deleting the property under test turns it red — a gate that cannot fail is
decoration (I-10).

Coverage in this file:

  I-03  the amount is never model-influenced
  I-05  every error path fails closed
  I-06  live-key refusal, per request, before any socket

I-04 (capability binding) and I-07 (at-most-once) are discharged next door in
``test_capability_invariants.py``, against a real SQLite file.

I-01, I-02, I-08, I-09 and I-10 attach to modules that do not exist yet (the
tool registry, the refund adapter, the runner's guard). They are listed in
``test_invariant_coverage_is_declared_not_assumed`` below so that the suite
states its own incompleteness rather than implying full coverage by silence.
"""

from __future__ import annotations

import inspect

import pytest

from paybound.core.money import Paise
from paybound.core.policy import amount as amount_mod
from paybound.core.policy.decide import decide
from paybound.core.policy.table import AUTO_MAX_PAISE, TABLE
from paybound.core.types import (
    CatalogueLine,
    Fulfilment,
    Kleene,
    OrderGroup,
    OrderLine,
    Outcome,
    ReasonCode,
    SiblingPayment,
)
from paybound.rail.modeguard import LiveKeyRefused, assert_test_mode, mode_of
from tests.conftest import (
    HERO_PAISE,
    NOW,
    cancelled_in_window_state,
    duplicate_charge_state,
    not_delivered_state,
    payment,
    state,
)

# ===========================================================================
# I-03 — the amount is never model-influenced
# ===========================================================================


def test_i03_no_amount_function_can_see_the_reason_code():
    """Structural, not behavioural.

    Every ``amount_fn`` in the table takes exactly one parameter: the trusted
    state. There is no channel by which the routed reason code — the model's
    only output — could reach an amount computation, because the function does
    not accept one.
    """
    for code, clause in TABLE.items():
        sig = inspect.signature(clause.amount_fn)
        params = list(sig.parameters)
        assert params == ["s"], (
            f"{code.value}: amount_fn takes {params}. It must take trusted state "
            "and nothing else — an extra parameter is a channel from the model "
            "to the amount."
        )


def test_i03_amount_is_a_function_of_state_alone_across_every_routing():
    """Behavioural counterpart.

    Hold the state fixed, vary the routed reason code across all nine members,
    and collect every amount that is actually authorised. Every ALLOW must carry
    the same amount, because the min-clamp is computed over satisfiable clauses
    rather than over the routed one. If routing could change the payout, this is
    where it shows up.
    """
    s = duplicate_charge_state()
    allowed: dict[ReasonCode, Paise] = {}
    for code in ReasonCode:
        d = decide(code, s)
        if d.is_allow:
            assert d.amount_paise is not None
            allowed[code] = d.amount_paise

    assert allowed, "the hero state must authorise at least one clause"
    assert len(set(allowed.values())) == 1, (
        "routing changed the amount: "
        + ", ".join(f"{c.value}={a}" for c, a in allowed.items())
        + " — the model would then be a lever on the payout."
    )


def test_i03_hero_case_amount_is_exactly_the_captured_amount():
    """The number on screen at 1:58. Byte-exact, asserted here and again against
    Razorpay's ledger after execution."""
    d = decide(ReasonCode.DUPLICATE_CHARGE, duplicate_charge_state())
    assert d.outcome is Outcome.ALLOW
    assert d.amount_paise == HERO_PAISE


def test_i03_amount_never_silently_reduced_to_fit_the_gate():
    """``auto_max_paise`` is a gate, not a clamp.

    A payment above the ceiling must ESCALATE with no amount at all. If this
    ever returns ALLOW at ``AUTO_MAX_PAISE``, the runtime has invented a refund
    amount that no clause computed.
    """
    over = AUTO_MAX_PAISE + 1
    s = duplicate_charge_state(
        pay=payment(amount=over, created_at=NOW - 3600),
        siblings=(
            SiblingPayment(
                payment_id_hash="sha256:bb" * 16,
                amount_paise=over,
                method="upi",
                created_at_epoch_s=NOW - 3600 - 16,
            ),
        ),
    )
    d = decide(ReasonCode.DUPLICATE_CHARGE, s)
    assert d.outcome is Outcome.ESCALATE
    assert d.amount_paise is None, "the gate must not emit a clamped amount"


def test_i03_aggregate_bound_denies_the_unbounded_drain():
    """Σ(prior refunds) + policy_amount ≤ payment.amount. Risk R3, closed.

    ``PRICE_MISMATCH`` is the clause that can actually reach the bound, and that
    is exactly why the bound exists: its amount is a *line difference* computed
    against the immutable ``payment.amount``, so without the bound a mismatch
    could be re-claimed against an already fully-refunded payment. The
    full-payment clauses never get here — ``nothing_refunded_yet`` stops them one
    step earlier, which the next test pins.
    """
    s = state(
        pay=payment(amount=HERO_PAISE, prior_refund_total=HERO_PAISE),
        lines=(OrderLine("SKU-TEE-01", 1, HERO_PAISE),),
        catalogue=(CatalogueLine("SKU-TEE-01", HERO_PAISE - 50_000, NOW - 30 * 86_400),),
        prior_reasons=frozenset({ReasonCode.NOT_DELIVERED}),
    )
    d = decide(ReasonCode.PRICE_MISMATCH, s)
    assert d.outcome is Outcome.DENY
    assert d.amount_paise is None
    assert d.aggregate_existing_paise == HERO_PAISE
    assert d.aggregate_cap_paise == HERO_PAISE
    assert "aggregate bound" in d.rationale


def test_i03_full_payment_clauses_are_stopped_before_the_bound():
    """Defence in depth, stated rather than assumed.

    ``DUPLICATE_CHARGE`` with a prior partial refund fails
    ``nothing_refunded_yet`` and never reaches the aggregate bound. Both layers
    are load-bearing: the precondition is clause-specific and the bound is
    universal, and this test records which one fires first so a later edit that
    removes the precondition shows up as a changed rationale rather than
    silently relying on the bound.
    """
    s = duplicate_charge_state(
        pay=payment(amount=HERO_PAISE, prior_refund_total=HERO_PAISE // 2),
        siblings=(
            SiblingPayment(
                payment_id_hash="sha256:cc" * 16,
                amount_paise=HERO_PAISE,
                method="upi",
                created_at_epoch_s=NOW - 3600 - 16,
            ),
        ),
    )
    d = decide(ReasonCode.DUPLICATE_CHARGE, s)
    assert not d.is_allow
    assert "nothing_refunded_yet" in d.rationale


# ===========================================================================
# I-05 — every error path fails closed
# ===========================================================================


@pytest.mark.parametrize(
    "missing_field,builder",
    [
        ("payment.created_at", lambda: cancelled_in_window_state(pay=payment(created_at=None))),
        ("order.status", lambda: not_delivered_state(order_status=None)),
        ("fulfilment.state", lambda: state(fulfilment=Fulfilment())),
    ],
)
def test_i05_a_missing_trusted_field_never_authorises(missing_field, builder):
    """Absence escalates. It never denies quietly and it never allows.

    A DENY caused by a failed read is indistinguishable in the metrics from a
    DENY caused by policy, which would let a broken projection masquerade as a
    working safety property.
    """
    s = builder()
    for code in ReasonCode:
        d = decide(code, s)
        assert not d.is_allow, f"{code.value} authorised with {missing_field} missing"


def test_i05_unknown_escalates_rather_than_denying():
    """Specifically ESCALATE, not DENY — the distinction is the point."""
    s = cancelled_in_window_state(pay=payment(created_at=None))
    d = decide(ReasonCode.CANCELLED_IN_WINDOW, s)
    assert d.outcome is Outcome.ESCALATE
    assert any(p.result is Kleene.UNKNOWN for p in d.predicates)


def test_i05_uncomputable_amount_escalates_and_never_defaults_to_zero():
    """A clause whose preconditions hold but whose amount cannot be derived must
    stop. A zero-rupee ALLOW is a decision, not an abstention."""
    with pytest.raises(amount_mod.AmountUncomputable):
        amount_mod.never(state())
    d = decide(ReasonCode.QUALITY_NOT_AS_DESCRIBED, state())
    assert d.outcome is Outcome.ESCALATE
    assert d.amount_paise is None


def test_i05_order_group_with_two_captures_blocks_everything_but_duplicate():
    """The precondition-manufacture defence.

    Deliberately pay twice, then claim non-delivery: without this rule the buyer
    keeps the goods and recovers both payments.
    """
    s = not_delivered_state(group=OrderGroup("grp_x", capture_count=2))
    for code in ReasonCode:
        if code is ReasonCode.DUPLICATE_CHARGE:
            continue
        assert not decide(code, s).is_allow, f"{code.value} authorised on a 2-capture group"


def test_i05_a_settled_group_blocks_every_clause():
    """A recovered duplicate charge closes the group for good."""
    s = not_delivered_state(group=OrderGroup("grp_y", capture_count=1, settled=True))
    for code in ReasonCode:
        assert not decide(code, s).is_allow, f"{code.value} authorised on a settled group"


# ===========================================================================
# I-06 — live-key refusal, per request, before any socket
# ===========================================================================


def test_i06_live_key_is_refused():
    with pytest.raises(LiveKeyRefused):
        assert_test_mode("rzp_live_AbCdEf123456", operation="create_refund")  # PB_FAKE_KEY


def test_i06_test_key_is_accepted():
    """The guard returns None and does not raise. Written as an explicit call
    plus an assert on the return value — ``f(x) is None`` on its own is an
    expression statement that asserts nothing and passes even if the function
    starts returning a value."""
    result = assert_test_mode("rzp_test_AbCdEf123456", operation="create_refund")  # PB_FAKE_KEY
    assert result is None


@pytest.mark.parametrize(
    "key",
    ["", None, "rzp_", "sk_live_x", "AbCdEf123456", "RZP_TEST_UPPER", " rzp_test_lead"],
)
def test_i06_anything_that_is_not_a_test_key_is_refused(key):
    """Including the empty and missing cases. Reaching an outbound call without a
    key means the credential load silently failed, and continuing from there
    produces a page of 401s that look like an API problem."""
    with pytest.raises(LiveKeyRefused):
        assert_test_mode(key, operation="probe")


def test_i06_refusal_never_reveals_the_key():
    """The instinct when this fires is to print the key and look at it. The
    exception must not do it for you — this message can land in a terminal
    recording."""
    secret_tail = "SUPERSECRETTAIL"
    with pytest.raises(LiveKeyRefused) as exc:
        assert_test_mode(f"rzp_live_{secret_tail}", operation="create_refund")
    assert secret_tail not in str(exc.value)
    assert "rzp_live_" in str(exc.value), "the mode must still be named"


def test_i06_guard_is_re_checked_per_call_not_cached():
    """A process that started on a test key and later reads a rotated
    environment must be refused on the *next* request. The guard holds no state,
    which is what makes that true."""
    assert_test_mode("rzp_test_ok", operation="first")
    with pytest.raises(LiveKeyRefused):
        assert_test_mode("rzp_live_rotated", operation="second")
    assert_test_mode("rzp_test_ok", operation="third")


def test_i06_mode_of_reports_without_raising():
    assert mode_of("rzp_test_x") == "test"
    assert mode_of("rzp_live_x") == "live"
    assert mode_of("garbage") == "unknown"


# ===========================================================================
# Coverage honesty
# ===========================================================================


def test_invariant_coverage_is_declared_not_assumed():
    """The suite states which invariants it does not yet discharge.

    A test file that silently covers three of ten invariants reads, to anyone
    scanning it, as if it covered all ten. This test fails if the declared list
    is edited without the modules actually existing, so the claim stays honest
    in both directions.
    """
    import importlib.util

    # I-04 and I-07 moved off this list when paybound/ledger/ landed; their
    # tests are in tests/security/test_capability_invariants.py.
    pending = {
        "I-01": "paybound.agent.tools",
        "I-02": "paybound.agent.tools",
        "I-08": "paybound.rail.refunds",
        "I-09": "paybound.harness.guard",
        "I-10": "paybound.harness.guard",
    }
    still_missing = {
        inv: mod for inv, mod in pending.items() if importlib.util.find_spec(mod) is None
    }
    assert still_missing == pending, (
        "a module backing a pending invariant now exists — write its invariant "
        f"test and remove it from this list. Newly available: "
        f"{set(pending) - set(still_missing)}"
    )

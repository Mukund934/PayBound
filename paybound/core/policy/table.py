"""THE POLICY TABLE. Nine clauses, one file, hashed into the corpus seal.

This is the file the demo points a cursor at. When the video says *"the amount
was computed here, and the model has no parameter it could have used to say
it,"* this is *here*.

Design constraints, each of which killed an earlier version:

* **No DSL.** Nine clauses times about five predicates does not pay for a rule
  language. A frozen table of typed Python plus pure predicate functions gives
  the auditability a DSL is usually bought for, and a golden-file test gives the
  rest.
* **``aggregate_bound`` and ``no_prior_reason`` have no defaults.** Construction
  fails without them. A clause author cannot forget the bound, because
  forgetting it is how a clause becomes an unbounded drain.
* **``auto_max_paise`` is a gate, not a clamp.** Over the ceiling escalates. It
  never reduces an amount to fit, because a silently-reduced refund is a wrong
  refund that looks like a working limit.
* **The table is hashed.** ``POLICY_SHA256`` covers every field that can change
  a decision. It goes into the corpus seal and into every run row, so a number
  can always name the policy that produced it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from paybound.core.money import Paise
from paybound.core.policy import predicates as P
from paybound.core.policy.amount import (
    full_payment,
    line_price_difference,
    mismatched_line_total,
    never,
)
from paybound.core.types import EvidenceClass, ReasonCode, Tier, TrustedState

__all__ = [
    "AUTO_MAX_PAISE",
    "POLICY_SHA256",
    "TABLE",
    "AggregateBound",
    "Clause",
    "clause_for",
    "policy_manifest",
]

# Rs 3,000 — above the Rs 2,499 catalogue ceiling, so no legitimate hero-case
# refund is gated by it, and a case that exceeds it is genuinely anomalous.
AUTO_MAX_PAISE: Final[Paise] = 300_000


@dataclass(frozen=True, slots=True)
class AggregateBound:
    """The mandatory bound, asserted against Razorpay's ledger before execution.

        sum(refunds on this payment, windowed) + policy_amount <= cap

    ``cap_source`` is the trusted field the cap is read from. It exists as a
    field rather than a constant so that the bound is *declared per clause* and
    the table cannot be authored without one. Invariant I-08 deletes this
    field's check in a mutation test and asserts CI turns red.
    """

    cap_source: str

    def cap_of(self, s: TrustedState) -> Paise:
        if self.cap_source == "payment.amount":
            return s.payment.amount_paise
        raise ValueError(f"unknown aggregate cap source: {self.cap_source!r}")


@dataclass(frozen=True, slots=True)
class Clause:
    """One row of the policy. Every field is decision-relevant and hashed."""

    reason_code: ReasonCode
    evidence_class: EvidenceClass
    tier: Tier
    preconditions: tuple[tuple[str, P.Predicate], ...]
    aggregate_bound: AggregateBound  # NO DEFAULT
    no_prior_reason: bool  # NO DEFAULT
    amount_fn: Callable[[TrustedState], Paise]
    amount_fn_name: str
    auto_max_paise: Paise
    on_fail: str  # "ESCALATE" | "DENY"
    deny_when: tuple[str, P.Predicate] | None = None

    def __post_init__(self) -> None:
        if self.on_fail not in ("ESCALATE", "DENY"):
            raise ValueError(f"on_fail must be ESCALATE or DENY, got {self.on_fail!r}")
        if self.tier == "NEVER" and self.auto_max_paise != 0:
            raise ValueError(
                f"{self.reason_code}: tier NEVER must have auto_max_paise == 0, "
                "otherwise the table claims a clause is never autonomous while "
                "leaving it a budget"
            )
        if self.tier != "NEVER" and not self.preconditions:
            raise ValueError(
                f"{self.reason_code}: an autonomous clause with no preconditions "
                "authorises unconditionally"
            )

    @property
    def clause_id(self) -> str:
        return f"{self.reason_code.value}@policy_{POLICY_SHA256[:8]}"


_BOUND = AggregateBound(cap_source="payment.amount")


# ---------------------------------------------------------------------------
# THE NINE CLAUSES
# ---------------------------------------------------------------------------

TABLE: Final[dict[ReasonCode, Clause]] = {
    # -- 1 -----------------------------------------------------------------
    # The hero case. Two captured payments, same amount, same method, within
    # thirty minutes, both in Razorpay's own ledger. Nothing the customer said
    # is load-bearing: the prose only routes.
    ReasonCode.DUPLICATE_CHARGE: Clause(
        reason_code=ReasonCode.DUPLICATE_CHARGE,
        evidence_class="ledger",
        tier="T0",
        preconditions=(
            ("duplicate_sibling_capture", P.duplicate_sibling_capture),
            ("is_later_of_duplicate_pair", P.is_later_of_duplicate_pair),
            ("nothing_refunded_yet", P.nothing_refunded_yet),
            ("group_not_settled", P.group_not_settled),
        ),
        aggregate_bound=_BOUND,
        no_prior_reason=True,
        amount_fn=full_payment,
        amount_fn_name="full_payment",
        auto_max_paise=AUTO_MAX_PAISE,
        on_fail="ESCALATE",
    ),
    # -- 2 -----------------------------------------------------------------
    # The line difference only. The aggregate bound alone does not close this
    # clause: it reads the immutable payment.amount, so without
    # no_prior_reason a mismatch could be re-claimed until the payment drains.
    ReasonCode.PRICE_MISMATCH: Clause(
        reason_code=ReasonCode.PRICE_MISMATCH,
        evidence_class="ledger",
        tier="T0",
        preconditions=(
            ("line_overcharged", P.line_overcharged),
            (
                "no_prior_refund_for[PRICE_MISMATCH]",
                P.no_prior_refund_for(ReasonCode.PRICE_MISMATCH),
            ),
            ("group_not_settled", P.group_not_settled),
        ),
        aggregate_bound=_BOUND,
        no_prior_reason=True,
        amount_fn=line_price_difference,
        amount_fn_name="line_price_difference",
        auto_max_paise=AUTO_MAX_PAISE,
        on_fail="ESCALATE",
    ),
    # -- 3 -----------------------------------------------------------------
    # Requires a positive carrier record AND a scan id. Never fires on the
    # absence of a delivery scan, which is also what a broken carrier
    # integration looks like.
    ReasonCode.NOT_DELIVERED: Clause(
        reason_code=ReasonCode.NOT_DELIVERED,
        evidence_class="ledger",
        tier="T1",
        preconditions=(
            ("positive_carrier_nondelivery_record", P.positive_carrier_nondelivery_record),
            ("order_status_paid", P.order_status_paid),
            ("nothing_refunded_yet", P.nothing_refunded_yet),
            ("single_capture_in_group", P.single_capture_in_group),
            ("group_not_settled", P.group_not_settled),
        ),
        aggregate_bound=_BOUND,
        no_prior_reason=True,
        amount_fn=full_payment,
        amount_fn_name="full_payment",
        auto_max_paise=AUTO_MAX_PAISE,
        on_fail="ESCALATE",
    ),
    # -- 4 -----------------------------------------------------------------
    ReasonCode.CANCELLED_IN_WINDOW: Clause(
        reason_code=ReasonCode.CANCELLED_IN_WINDOW,
        evidence_class="ledger",
        tier="T1",
        preconditions=(
            ("within_cancellation_window", P.within_cancellation_window),
            ("not_picked_up", P.not_picked_up),
            ("nothing_refunded_yet", P.nothing_refunded_yet),
            ("single_capture_in_group", P.single_capture_in_group),
            ("group_not_settled", P.group_not_settled),
        ),
        aggregate_bound=_BOUND,
        no_prior_reason=True,
        amount_fn=full_payment,
        amount_fn_name="full_payment",
        auto_max_paise=AUTO_MAX_PAISE,
        on_fail="ESCALATE",
    ),
    # -- 5 -----------------------------------------------------------------
    # T2. At T0 and T1 the intake scan does not exist, the predicate is UNKNOWN,
    # and this escalates. That is the measurement, not a gap.
    ReasonCode.WRONG_ITEM: Clause(
        reason_code=ReasonCode.WRONG_ITEM,
        evidence_class="testimonial",
        tier="T2",
        preconditions=(
            ("returns_intake_sku_mismatch", P.returns_intake_sku_mismatch),
            ("nothing_refunded_yet", P.nothing_refunded_yet),
            ("single_capture_in_group", P.single_capture_in_group),
            ("group_not_settled", P.group_not_settled),
        ),
        aggregate_bound=_BOUND,
        no_prior_reason=True,
        amount_fn=mismatched_line_total,
        amount_fn_name="mismatched_line_total",
        auto_max_paise=AUTO_MAX_PAISE,
        on_fail="ESCALATE",
    ),
    # -- 6 -----------------------------------------------------------------
    ReasonCode.ARRIVED_DAMAGED: Clause(
        reason_code=ReasonCode.ARRIVED_DAMAGED,
        evidence_class="testimonial",
        tier="T2",
        preconditions=(
            ("returns_intake_damage_record", P.returns_intake_damage_record),
            ("nothing_refunded_yet", P.nothing_refunded_yet),
            ("single_capture_in_group", P.single_capture_in_group),
            ("group_not_settled", P.group_not_settled),
        ),
        aggregate_bound=_BOUND,
        no_prior_reason=True,
        amount_fn=mismatched_line_total,
        amount_fn_name="mismatched_line_total",
        auto_max_paise=AUTO_MAX_PAISE,
        on_fail="ESCALATE",
    ),
    # -- 7 -----------------------------------------------------------------
    # No trusted predicate exists at any tier. This is a published finding: the
    # merchant should stop trying to automate it, not buy a better model.
    ReasonCode.QUALITY_NOT_AS_DESCRIBED: Clause(
        reason_code=ReasonCode.QUALITY_NOT_AS_DESCRIBED,
        evidence_class="testimonial",
        tier="NEVER",
        preconditions=(),
        aggregate_bound=_BOUND,
        no_prior_reason=True,
        amount_fn=never,
        amount_fn_name="never",
        auto_max_paise=0,
        on_fail="ESCALATE",
    ),
    # -- 8 -----------------------------------------------------------------
    # The table's one explicit DENY. An in-window change of mind on an
    # undispatched order is a legitimate cancellation and is picked up by clause
    # 4. Once the window has demonstrably elapsed there is no policy under which
    # it is owed, and saying so is more honest than escalating a case a human
    # will also refuse.
    ReasonCode.CHANGED_MIND_LATE: Clause(
        reason_code=ReasonCode.CHANGED_MIND_LATE,
        evidence_class="testimonial",
        tier="NEVER",
        preconditions=(),
        aggregate_bound=_BOUND,
        no_prior_reason=True,
        amount_fn=never,
        amount_fn_name="never",
        auto_max_paise=0,
        on_fail="DENY",
        deny_when=("return_window_elapsed", P.return_window_elapsed),
    ),
    # -- 9 -----------------------------------------------------------------
    # The model's legal way to abstain. Always escalates. It is NOT a coercion
    # target: anything outside the frozenset is an ENUM_VIOLATION and a session
    # strike, never a silent downgrade to this.
    ReasonCode.UNCLASSIFIED: Clause(
        reason_code=ReasonCode.UNCLASSIFIED,
        evidence_class="testimonial",
        tier="NEVER",
        preconditions=(),
        aggregate_bound=_BOUND,
        no_prior_reason=True,
        amount_fn=never,
        amount_fn_name="never",
        auto_max_paise=0,
        on_fail="ESCALATE",
    ),
}


def clause_for(code: ReasonCode) -> Clause:
    """The clause for a routed reason code.

    ``KeyError`` here is unreachable from the agent path — the tool schema is a
    closed enum and a non-member is rejected as ``ENUM_VIOLATION`` before this
    is called — but it is left to raise rather than defaulting, because a policy
    lookup that silently falls back is a policy that can be routed around.
    """
    return TABLE[code]


def policy_manifest() -> list[dict[str, object]]:
    """A canonical, JSON-serialisable view of every decision-relevant field.

    This is what ``POLICY_SHA256`` is computed over and what ``verify.py``
    re-derives offline. Callables are represented by name: two tables with
    different predicate *implementations* but identical names would hash the
    same, which is why the predicate module is covered by the repo's git sha in
    the run row as well.
    """
    return [
        {
            "reason_code": c.reason_code.value,
            "evidence_class": c.evidence_class,
            "tier": c.tier,
            "preconditions": [name for name, _ in c.preconditions],
            "aggregate_bound": c.aggregate_bound.cap_source,
            "no_prior_reason": c.no_prior_reason,
            "amount_fn": c.amount_fn_name,
            "auto_max_paise": c.auto_max_paise,
            "on_fail": c.on_fail,
            "deny_when": c.deny_when[0] if c.deny_when else None,
        }
        for c in (TABLE[code] for code in ReasonCode)
    ]


POLICY_SHA256: Final[str] = hashlib.sha256(
    json.dumps(policy_manifest(), sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()
"""Covers every field that can change a decision. Goes into the corpus seal and
every run row, so a published number can always name the policy that produced
it. An edited policy is a different run, not a footnote."""


# The table must cover the enum exactly. A missing clause would make a legal
# routing target unhandled; an extra one would be unreachable policy.
assert set(TABLE) == set(ReasonCode), "policy table must cover ReasonCode exactly"

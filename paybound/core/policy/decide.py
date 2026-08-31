"""``decide()`` — the whole authority-bearing computation, in one pure function.

Signature: ``(routed reason_code, trusted state) -> Decision``. That is the
entire interface between the model's output and money. The model contributes one
member of a nine-member enum; everything else in this file reads trusted state.

Order of operations is load-bearing and is asserted by the invariant suite:

1. **Order-group rules** — universal, before any clause is consulted.
2. **Clause preconditions** — Kleene. UNKNOWN escalates regardless of ``on_fail``.
3. **Min-clamp** — the amount is the minimum over *every* clause whose
   preconditions independently hold, not just the routed one.
4. **Aggregate bound** — against a fresh ledger read, before the gate.
5. **auto_max gate** — over the ceiling escalates. Never clamps.

Why the min-clamp is step 3 and not a footnote
----------------------------------------------
It closes the routing gap. If the amount depended only on the routed clause, a
model that could be talked into the most generous applicable reason code would
be worth attacking. Because the amount is the minimum over all satisfiable
clauses, choosing a different reason code cannot increase the payout — so the
router is not a lever on the amount, and the residual attack surface is
routing-to-approval, not routing-to-more-money. Its cost in false-refusal terms
is measured, not assumed (M8).
"""

from __future__ import annotations

from paybound.core.money import Paise, add
from paybound.core.policy.amount import AmountUncomputable
from paybound.core.policy.table import TABLE, Clause, clause_for
from paybound.core.types import (
    Decision,
    Kleene,
    Outcome,
    PredicateResult,
    ReasonCode,
    TrustedState,
)

__all__ = ["decide", "evaluate_preconditions"]


def evaluate_preconditions(
    clause: Clause, state: TrustedState
) -> tuple[Kleene, tuple[PredicateResult, ...]]:
    """Evaluate every precondition — all of them, not short-circuit.

    A short-circuiting conjunction would produce a partial audit trail, and the
    audit trail is the deliverable. The cost is a handful of pure function
    calls; the benefit is that the escalation packet a human receives lists
    every check with what it observed, including the ones after the first
    failure.
    """
    results = tuple(fn(state) for _, fn in clause.preconditions)
    if not results:
        return Kleene.TRUE, ()
    return Kleene.conjoin([r.result for r in results]), results


def _satisfiable_amounts(state: TrustedState) -> dict[ReasonCode, Paise]:
    """Every clause that independently holds, with its computable amount.

    A clause whose preconditions hold but whose amount cannot be derived is
    excluded rather than treated as zero. Zero would win every min-clamp and
    turn an unrelated data gap into a zero-rupee ALLOW.
    """
    out: dict[ReasonCode, Paise] = {}
    for code, clause in TABLE.items():
        if clause.tier == "NEVER":
            continue
        verdict, _ = evaluate_preconditions(clause, state)
        if verdict is not Kleene.TRUE:
            continue
        try:
            out[code] = clause.amount_fn(state)
        except AmountUncomputable:
            continue
    return out


def decide(reason_code: ReasonCode, state: TrustedState) -> Decision:
    """The complete decision. Pure, total, and the only producer of an ALLOW."""
    clause = clause_for(reason_code)

    # --- 1. Universal order-group rules ------------------------------------
    # These precede the clause because they are properties of the *group*, and a
    # clause author must not be able to opt out of them.
    if state.group.settled:
        return Decision(
            outcome=Outcome.ESCALATE,
            reason_code=reason_code,
            amount_paise=None,
            clause_id=clause.clause_id,
            predicates=(),
            rationale=(
                "order group is already settled by a duplicate-charge refund; every "
                "other clause on this group is blocked"
            ),
        )

    if state.group.capture_count >= 2 and reason_code is not ReasonCode.DUPLICATE_CHARGE:
        return Decision(
            outcome=Outcome.ESCALATE,
            reason_code=reason_code,
            amount_paise=None,
            clause_id=clause.clause_id,
            predicates=(),
            rationale=(
                f"order group holds {state.group.capture_count} captured payments; "
                "tier NEVER for every clause except DUPLICATE_CHARGE (precondition "
                "manufacture defence)"
            ),
        )

    # --- 2. Clauses that are never autonomous ------------------------------
    if clause.tier == "NEVER":
        if clause.deny_when is not None:
            name, fn = clause.deny_when
            result = fn(state)
            if result.result is Kleene.TRUE:
                return Decision(
                    outcome=Outcome.DENY,
                    reason_code=reason_code,
                    amount_paise=None,
                    clause_id=clause.clause_id,
                    predicates=(result,),
                    rationale=f"{name} holds; no policy owes this refund",
                )
            return Decision(
                outcome=Outcome.ESCALATE,
                reason_code=reason_code,
                amount_paise=None,
                clause_id=clause.clause_id,
                predicates=(result,),
                rationale=f"{name} does not positively hold; a human decides",
            )
        return Decision(
            outcome=Outcome.ESCALATE,
            reason_code=reason_code,
            amount_paise=None,
            clause_id=clause.clause_id,
            predicates=(),
            rationale=(
                "no trusted predicate exists for this reason at any tier; "
                "irreducibly testimonial"
            ),
        )

    # --- 3. Clause preconditions -------------------------------------------
    verdict, results = evaluate_preconditions(clause, state)

    if verdict is Kleene.UNKNOWN:
        unknown = [r.name for r in results if r.result is Kleene.UNKNOWN]
        return Decision(
            outcome=Outcome.ESCALATE,
            reason_code=reason_code,
            amount_paise=None,
            clause_id=clause.clause_id,
            predicates=results,
            rationale=(
                f"could not read trusted state for {unknown}; absence of evidence is "
                "not evidence of absence, so this escalates rather than denying"
            ),
        )

    if verdict is Kleene.FALSE:
        failed = [r.name for r in results if r.result is Kleene.FALSE]
        return Decision(
            outcome=Outcome(clause.on_fail),
            reason_code=reason_code,
            amount_paise=None,
            clause_id=clause.clause_id,
            predicates=results,
            rationale=f"preconditions not met: {failed}",
        )

    # --- 4. Min-clamp over every independently satisfiable clause ----------
    satisfiable = _satisfiable_amounts(state)
    if reason_code not in satisfiable:
        # The routed clause's preconditions hold but its amount is not
        # derivable from trusted state. Escalate; never substitute a default.
        try:
            clause.amount_fn(state)
        except AmountUncomputable as exc:
            return Decision(
                outcome=Outcome.ESCALATE,
                reason_code=reason_code,
                amount_paise=None,
                clause_id=clause.clause_id,
                predicates=results,
                rationale=f"amount is not computable from trusted state: {exc}",
            )
        raise AssertionError(
            "routed clause is satisfiable and its amount computes, but it is absent "
            "from the satisfiable set — the two evaluations disagree"
        )

    amount = min(satisfiable.values())
    clamped_by = sorted(c.value for c, a in satisfiable.items() if a == amount)

    # --- 5. Aggregate bound, against the ledger ----------------------------
    existing = state.payment.prior_refund_total_paise
    cap = clause.aggregate_bound.cap_of(state)
    if add(existing, amount) > cap:
        return Decision(
            outcome=Outcome.DENY,
            reason_code=reason_code,
            amount_paise=None,
            clause_id=clause.clause_id,
            predicates=results,
            rationale=(
                f"aggregate bound: {existing} already refunded + {amount} proposed "
                f"exceeds {clause.aggregate_bound.cap_source} = {cap}"
            ),
            aggregate_existing_paise=existing,
            aggregate_cap_paise=cap,
        )

    # --- 6. The gate. Never a clamp. ---------------------------------------
    if amount > clause.auto_max_paise:
        return Decision(
            outcome=Outcome.ESCALATE,
            reason_code=reason_code,
            amount_paise=None,
            clause_id=clause.clause_id,
            predicates=results,
            rationale=(
                f"computed amount {amount} exceeds auto_max_paise "
                f"{clause.auto_max_paise}; the ceiling is a gate, so this escalates "
                "rather than refunding the ceiling"
            ),
            aggregate_existing_paise=existing,
            aggregate_cap_paise=cap,
        )

    return Decision(
        outcome=Outcome.ALLOW,
        reason_code=reason_code,
        amount_paise=amount,
        clause_id=clause.clause_id,
        predicates=results,
        rationale=(
            f"all preconditions hold; amount computed by {clause.amount_fn_name} "
            f"(min-clamped over {clamped_by})"
        ),
        aggregate_existing_paise=existing,
        aggregate_cap_paise=cap,
    )

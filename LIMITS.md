# Limits

What this project does not show, what its adversary could not do, and which of
its own citations are weaker than they look.

This file is written to be read *before* the numbers, not after. A reviewer
should not have to find any of it themselves.

---

## 1. Three refund objects exist, and no benchmark trial created one

The benchmark runs in **`DRY_LEDGER`** mode. Every decision, every precondition
evaluation and every computed amount is real; the broker halts at the last step
with the amount it computed rather than POSTing.

Three refunds exist in Razorpay's test ledger because of this project,
**₹2,501.00 in total**, and none of them came from a benchmark trial:

| Refund | Amount | Origin |
|---|---|---|
| `rfnd_TWKWib7mcdGJ8m` | ₹1.00 | KG-1 feasibility gate, 31 Aug |
| `rfnd_TWOypP4lLU9Yg8` | ₹1.00 | minted by the live aggregate-bound test (`pytest -m live`) |
| `rfnd_TXFL2WLlENbzRG` | ₹2,499.00 | the executor, on a real duplicate pair, 2 Sep |

The second grows by one each time anyone runs `pytest -m live`, which is not in
the default suite. This section said "exactly one" until 3 Sep, which was true
when written and understated the project's own headline result by ₹2,499 by the
time it mattered.

So *"a refund object exists in a real processor's ledger"* is true of **three**
objects, not of the corpus. Every trial-level number is a **decision-level**
quantity. The distinction is on every trial row (`mode: DRY_LEDGER`) and in the
report, and it is not blurred anywhere.

What survives the distinction: the per-class ceiling, the taxonomy, the
discordant ablation, and every invariant — all of which are properties of
decisions, not of executed refunds. What does not: any claim about settlement,
partial-refund behaviour at volume, or Razorpay's behaviour under concurrent
load.

## 2. This is not Action-Selector

Beurer-Kellner's Action-Selector pattern requires that the model never see tool
output. `get_case` returns typed facts the model reads, so PayBound is **not**
that pattern and does not claim to be. It is closer to a capability-constrained
tool surface with recomputation, which is a weaker structural guarantee.

## 3. The adversary used no attacker model, and was not stronger than the target

The architecture lock asks for an attacker *stronger* than the agent under test,
so that a null result means "hard to break" rather than "attacker underpowered".
That was not achievable and the reason is not primarily budget.

Every pro-tier model on this API key returns HTTP 429. But the governing reason
is structural: the router runs at **temperature 0** into a forced choice over a
**closed nine-member enum**, so its output is one categorical value with no
logprob and no ranking. There is no gradient for a search to climb — an
"optimisation search" over that signal is random sampling in a costume. Worse, a
model-driven search optimises a proxy *anti-correlated* with the ground truth:
the fastest way to make any router emit `NOT_DELIVERED` is to write prose that
genuinely says the parcel never arrived, which changes the honest oracle label
and converts an attack into a correctly-classified benign case.

The campaign is therefore **`SWEEP-R`**: a deterministic expansion of a
committed slot grammar, with no model anywhere in the loop. Renamed from the
lock's `ADAPT-1` because *a combinatorial sweep is not an adaptive attack* and
calling it one would claim adaptivity the method does not have.

> **It is complete over what its author thought of, and blind to everything he
> did not. It is a lower bound on what a well-resourced adversary would find.**

**Where this bites, precisely** — because a blanket caveat would be wrong in the
other direction:

- **Affected:** attack-success for families R (routing) and X (handle
  confusion), 45 of 70 attack items — the two the lock itself marks "real
  unknown". And `SWEEP-R`'s own result.
- **Unaffected:** the 648-assertion scripted arm, which is an **exhaustive
  enumeration** of a closed input space — a frontier attacker adds nothing to a
  complete enumeration. Families A (amount), H (replay) and P (policy
  instruction), 25 of 70, declared **0 by construction in advance**: no model of
  any tier can put an amount into a schema with no amount field. Every
  benign-corpus metric, which has no adversary in it. Every invariant.

## 3b. What is written and unit-tested but has no production caller

Named here rather than left to be discovered, because the difference between
"built" and "wired" is exactly what this project keeps catching in itself.

- **`rail/reconcile.py::reconcile_on_boot`** — boot reconciliation. Written,
  unit-tested by 197 fault injections, and called by nothing at runtime.
  At-most-once on the live path therefore rests on the fresh re-read of the
  refunds collection in `preflight_refund_total`, not on crash recovery.
- **`rail/client.py::list_refunds_window`** and the FOREIGN-refund cross-check
  — no callers.
- **`Mode.EXECUTE` inside `harness/runner.py`** — still not wired. Real
  execution runs through `scripts/execute_one.py`, which constructs
  `LedgerExecutor` directly. The refund really was executed; the *harness* mode
  was not the thing that executed it, and an earlier commit message blurred
  those two.
- **The capability layer on the measured path.** `run_trial` builds handles as
  `f"cap_w_{case_id}"` rather than calling `mint_case_capabilities`, because a
  corpus item has no `pay_` id to bind a capability to. The capability model is
  exercised by the fault suite, the 648-assertion arm and the live execution —
  **not** by the DRY_LEDGER trials the published rates come from.

None of these change a published number. All of them would take code plus new
evidence to close, and disclosing them is worth more two days out than a rushed
wiring commit.

## 4. The model-in-loop numbers have a small denominator

The Gemini free tier permits **20 requests per day per model**
(`GenerateRequestsPerDayPerProjectPerModel-FreeTier`, quotaValue 20). The corpus
is 150 items.

Twenty requests is **not** twenty items. `max_steps` is 4, so a trial that reads
the case and then acts costs two requests and may cost four. Day 1 spent the
budget on **10 items**, which is the realistic rate. At that pace the full
corpus is roughly a fortnight, and this project does not have a fortnight —
so the published denominator will be a partial one, and it is stated as such
everywhere it appears rather than being described as a sample of 150.

Router accuracy and attack-success therefore carry a denominator far below 150,
stated explicitly wherever they appear. `verify.py` refuses to print a rate it
cannot defend, and refuses to pool trials across differing `model_id` — a rate
assembled from several routers is not a rate.

The daily slice is drawn in an order derived from the corpus seal
(`sha256(seal || item_id)`), so it is stratified across the corpus and **cannot
be re-rolled**: changing the order requires changing a committed hash. That
closes cherry-picking, which a freely-chosen daily subset would invite.

**The headline does not depend on this.** The per-class ceiling is computed
offline with zero API calls.

### What a ten-item run can and cannot support

It can support **existence** claims, which need no denominator: the ablation arm
authorised ₹2,499.00 on `b_dis_00`, a claim the trusted state does not support,
and the precondition check refused the same item. That is a demonstration, and
one instance is enough to demonstrate.

It cannot support **rate** claims. `attack_H` at 0/1 has a rule-of-three upper
bound of 100%, which is to say it establishes nothing at all, and `verify.py`
prints that bound next to the zero rather than letting the digit stand alone.
Treat every percentage from this run as an illustration of the instrument, not
as a result.

## 5. The builder wrote both the attack and the defence

`tests/arch/test_boundaries.py` forbids `harness/corpus_gen/` from importing
`core/policy/` or `broker/`, so the generator cannot read the defence. That
constrains the *code*. It does not constrain the *author*, who wrote both with
full knowledge of each.

This is recorded in `PREREG.md` §3 rather than left for a reviewer to notice.
The mitigating direction is worth stating: the externally-sourced attack
patterns are the ones expected to score zero, and the items authored here are
the ones designed to beat this system. Builder authorship works against the
result, not for it.

## 6. The corpus is synthetic prose, not real support tickets

150 messages generated from a committed slot grammar in Indian-English with
Hindi code-mixing. Realistic in register, but not drawn from a real merchant's
inbox — no such corpus was available, and inventing provenance for one would be
worse than saying this.

Consequence: the router is measured on text that is *plausibly* like what a
merchant receives, not on text a merchant actually received. Real tickets are
messier — forwarded threads, screenshots, multiple issues in one message, and
mixed scripts including Devanagari. None of that is here.

The upside of the trade is that the corpus regenerates byte-for-byte on a clean
clone with no API key, which a model-authored or scraped corpus could not.

## 7. Two published carrier-vocabulary defects reach the money

Neither is a PayBound design choice; both are properties of what carriers
actually publish, and both are carried in the corpus rather than assumed away.

**`Undelivered` is a superset.** Shiprocket's published `Undelivered` covers
parcels in an active three-attempt reattempt cycle, not only refusals. A
merchant mapping it directly onto `UNDELIVERED_CONSIGNEE_REFUSED` would place a
parcel that is *about to be delivered* into the non-delivery set and authorise a
full refund on it. PayBound's member is a declared conjunction requiring a
refusal signal.

**`Lost/Damaged` is one status where ClickPost has two.** Shiprocket publishes
the single combined status; ClickPost separates `16 Lost` from `17 Damaged`. On
a Shiprocket-fed merchant a **damaged** parcel therefore surfaces as `LOST`,
which authorises `NOT_DELIVERED` at the full payment amount — for goods the
customer is physically holding. This is a second bridge into the
highest-paying clause, independent of the routing attacks, and it comes from the
vocabulary itself.

## 8. Citations that are weaker than they look

**C-01 could not be closed as originally written.** It asked for a published
Indian carrier tracking **API** whose status vocabulary could be cited. No such
public artifact exists: Delhivery's API reference is behind a login,
`apidocs.shiprocket.in` is a client-rendered shell. The condition was amended in
the open to *operator-facing* status vocabulary — weaker than an API schema,
stronger than an invented list. See `docs/CITATIONS.md`.

**Two ClickPost pages have no archive snapshot.** Confirmed two ways, including
a domain-wide CDX query. They are live-only citations and are marked as such.

**The SNITCH `.co.in` policy is a legacy site.** That domain banners *"We have
moved to SNITCH.COM"*, including in the archived snapshot. It is a real, dated,
archived policy of a real Indian merchant — it is not SNITCH's current policy.

**`NOT_PICKED_UP` is derived, not published.** No source publishes "not
dispatched" or any synonym. It is an author-defined aggregation of everything
before a pickup scan, and it is deliberately *not* named after ClickPost's real
`PickupPending`, because adopting a published token for an invented aggregation
is a subtler fabrication than an obviously-derived name.

**`DUPLICATE_CHARGE` is not transcribed from any returns policy.** A regex for
duplicate/double-charge language across four real Indian D2C policies returns
**zero hits**, independently reproduced. It is a payments-ledger concern that
returns policies do not cover. That is why it has the cleanest evidence and why
the demo leads with it — but it means the hero case is the *least* representative
of what a returns policy actually governs.

## 9. The taxonomy result is about this policy at this tier

*"Testimonial classes are 0% automatable"* is a property of **this nine-clause
policy** evaluated at **T1** (Razorpay ledger + carrier scans, no returns-intake
scan). It is not a universal claim about refunds.

`WRONG_ITEM` and `ARRIVED_DAMAGED` become decidable at T2 with a physical
returns-intake scan — the tier ladder says so. `QUALITY_NOT_AS_DESCRIBED` and
`CHANGED_MIND_LATE` have no trusted predicate at any tier *in this policy*; a
merchant with a different policy might make different trades.

The tier ladder is published as **two points, with the third named and declared
unreachable**, rather than rendered as three bars of which one is a copy.

## 10. n = 150 items is small

Per-class denominators are 10–20. Every non-zero rate prints a Wilson interval
and every zero prints a rule-of-three upper bound, so no point estimate appears
without its uncertainty. Until 3 Sep only the zeros were bounded, which meant
the control arm's damaging `50.0% (1/2)` printed bare beside our own bounded
`0.0% (0/2)` — an asymmetry running in our favour. `n` is **item-level**; trial
counts are never reported as `n`.

## 11. No claim of novelty

The design is an application of ideas that already exist — PACT's authority
binding, capability-based agent defences, information-flow control. See the
README's prior-art section, which appears *before* any result. This project's
contribution is a measurement in a specific domain, not an architecture.

The words *"novel architecture"*, *"first firewall"*, *"provably secure"*,
*"solved prompt injection"*, *"100% blocked"* and *"nobody measures false
refusals"* do not appear anywhere in this repository, and a grep in CI keeps it
that way.

## 12. Availability is out of scope

An attacker who can make the broker escalate everything has degraded the
merchant's automation rate. That is measured (it is the false-refusal number)
but not defended against. Denial-of-refund was considered and cut.

## 13. What would change my mind

Stated in advance, so it is not reconstructed later:

- If `SWEEP-R` produces **zero** would-be unauthorised refunds against the
  precondition-blind arm, the instrument has not demonstrated sensitivity and
  the main arm's null is published as `INSTRUMENT_FAILURE`, not as a defence.
  This rule is pre-registered in `PREREG.md` §1.4.
- If the routing misroute rate on family R matches the router's *baseline*
  confusion rate measured on the benign corpus, the attack has demonstrated
  nothing beyond ordinary classifier error, and is reported that way. Success is
  **lift over that baseline**, not existence.

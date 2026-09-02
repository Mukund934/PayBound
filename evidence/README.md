# evidence/

Everything in this directory is one of four things, and the difference matters
more than the contents. Read this before reading any file here.

| Directory | Status | Counts toward a published number? |
|---|---|---|
| `kg1/` | **Real.** The Razorpay feasibility gate | Not a rate. A settled fact. |
| `execute/` | **Real.** A refund the policy authorised, in Razorpay's ledger | Not a rate. A settled fact. |
| `run_1788359829/` | **LIVE.** 8 model-in-loop trials, arm2 + arm1a | **Yes.** Every published rate comes from here. |
| `run_1788265241/` | **SUPERSEDED** — see `SUPERSEDED.json` inside it | **No.** Excluded by `verify.py`, which says so on every run. |
| `smoke/` | **Not a result.** A pipeline check. | **No.** Never was. |

`python3 verify.py` exits **0**. It recomputes every published figure from the
rows in `run_1788359829/`, offline, with nothing installed and no keys.

---

## `kg1/` — the Razorpay feasibility gate

A real refund, on a real captured payment, in Razorpay's test-mode ledger:
`rfnd_TWKWib7mcdGJ8m`, ₹1.00 against `pay_TWKVnCHXugGcUo`, carrying the receipt
`pbr_01M1BJXQ4SZ4C347F0TJ5SEVXY` that this codebase minted.

The files are raw API responses, unedited. They settle seven questions about
Razorpay's refund semantics that are not documented in one place — idempotent
replay, 409 on a changed body under a reused key, when `amount_refunded`
increments, `PATCH notes` being REPLACE rather than merge, and the 512-character
`notes` ceiling. Those answers shaped the design; see the README's KG-1 table.

This is evidence of a **capability**, not a rate. It has no denominator and
claims none.

## `execute/` — the policy moved real money

`rfnd_TXFL2WLlENbzRG`, **₹2,499.00**, against `pay_TXFI3kgRwwFyKz` — the later
of two genuine ₹2,499 captures 330 seconds apart in Razorpay's test ledger.

Nothing here is fixtured. All four `DUPLICATE_CHARGE` preconditions were
re-verified from live API data: `matching_siblings: 1`, `is_later_of_duplicate_pair`
comparing real capture timestamps, `nothing_refunded_yet` summing the real
refunds collection. The amount was computed by
`core/policy/amount.py::full_payment` and the receipt
`pbr_01M1HHQAYCFEZC015CS8Y2CDB2` was minted by this codebase, so the object is
attributable without a labelling step.

**Running it a second time is refused, and refused in the right place.**
`nothing_refunded_yet` read `249900` back from the live API, evaluated FALSE,
and the broker escalated with **zero outbound POSTs**. That is at-most-once
demonstrated against a real processor rather than a mock.

The routing for this execution was supplied on the command line rather than by
the model — the free tier was exhausted — and `routing_provenance` in the record
says so. It changes nothing about the authority argument: the amount, the
preconditions, the single-use capability and the write-ahead intent are all
identical either way. It does mean this particular execution did not exercise
the router, which the corpus run does.

## `run_1788359829/` — the live run

Eight items of the sealed 150, in an order derived from the corpus seal and
therefore not chosen. `DRY_LEDGER`: decisions, preconditions and amounts are
real; the refund object is not executed. `trials.jsonl` is arm2, the system;
`ablation/trials.jsonl` is arm1a, the precondition-blind control.

The two arms are **never pooled** — they share every field in the aggregation
key, which is what makes them comparable and is exactly why averaging them would
describe neither. `verify.py` reports them separately and prints the contrast.

Eight items. Every rate carries its denominator, and each zero carries a
rule-of-three upper bound that is frequently 100%, meaning it establishes
nothing. That is the honest reading, not a hedge.

The run halted itself on a per-day quota error and recorded where to resume. It
did not retry into the wall, and it did not record quota failures as results.

## `run_1788265241/` — superseded, kept deliberately

Ten model-in-loop trials. The measurements are real and unaltered. What was
wrong is the **provenance record hashed onto every row**: it named the adversary
`SWEEP-R`, a campaign that had not been built when those rows were written and
that has still not been run.

Correcting the record changed `attacker_sha`. `verify.py` refuses to pool trials
across differing adversary descriptions — correctly, because two descriptions
cannot produce one rate — so this run is excluded.

**It is kept rather than rewritten.** Editing committed evidence to match a
corrected source is falsification. The marker file explains itself, and a
reviewer can read exactly what was wrong and check that the fix is real.
See `INCIDENTS.md`.

## `smoke/` — never a result

A pipeline check, labelled as one in its own README, and excluded by `verify.py`
by name.

---

## What would make a run count

A committed `trials.jsonl` with no `SUPERSEDED.json` beside it, whose rows share
one aggregation signature (`model_id`, `policy_sha`, `tool_registry_sha`,
`prompt_sha`, `attacker_sha`), with the denominator guard green. `verify.py`
enforces every one of those and exits non-zero rather than printing a number it
cannot stand behind.

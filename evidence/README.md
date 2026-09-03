# evidence/

Everything in this directory is one of four things, and the difference matters
more than the contents. Read this before reading any file here.

| Directory | Status | Counts toward a published number? |
|---|---|---|
| `kg1/` | **Real.** The Razorpay feasibility gate | Not a rate. A settled fact. |
| `execute/` | **Real.** A refund the policy authorised, in Razorpay's ledger | Not a rate. A settled fact. |
| `run_1788359829/` | **LIVE.** 8 model-in-loop trials, arm2 + arm1a | **Yes.** |
| `run_1788428727/` | **LIVE.** 8 more, offsets 8-15, same signature | **Yes.** Pools with the run above. |
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

**Three of the four** `DUPLICATE_CHARGE` preconditions were re-verified from
live API data. The fourth, `group_not_settled`, is merchant-owned state Razorpay
cannot supply; the demo script asserts it at `scripts/execute_one.py:112`, and
it is the only hardcoded fact that changes the outcome — flip it and the same
case escalates.

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

### `execute/hero_pair.json` — the video's pair, recorded so the ids are checkable

Two captures 73 seconds apart, both unrefunded when written. The five-minute
script reads these identifiers aloud, so they are committed rather than asserted.

**Single-use.** The hero beat refunds the later one and Razorpay refunds are
irreversible, so the pair is good for exactly one take. `scripts/pay_link.py
--amount 249900`, run twice inside 30 minutes, makes another.

## `run_1788359829/` and `run_1788428727/` — the live runs

Sixteen items of the sealed 150 across two days, in an order derived from the
corpus seal and therefore not chosen. They pool because every field in the
aggregation key matches — same model, policy, tool surface and adversary — which
`verify.py` checks before combining them and refuses if it fails.

`DRY_LEDGER`: decisions, preconditions and amounts are real; the refund object
is not executed. `trials.jsonl` is arm2, the system; `ablation/trials.jsonl` is
arm1a, the clause-only control: it drops the order-group rules, the
preconditions, the min-clamp, the aggregate bound and the auto_max gate, keeping
only the NEVER-tier check and the amount function.

**Five of the sixteen do not contribute to the ablation contrast.** On those the
agent called `escalate_to_human`, so `decide()` was never reached and the broker
decided nothing; `verify.py` excludes them and names them. Counting them was a
four-fold overstatement until 3 Sep — see `INCIDENTS.md`.

The two arms are **never pooled** — they share every field in the aggregation
key, which is what makes them comparable and is exactly why averaging them would
describe neither. `verify.py` reports them separately and prints the contrast.

Sixteen items. Every non-zero rate carries a Wilson interval and every zero a
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

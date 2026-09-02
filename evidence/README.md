# evidence/

Everything in this directory is one of four things, and the difference matters
more than the contents. Read this before reading any file here.

| Directory | Status | Counts toward a published number? |
|---|---|---|
| `kg1/` | **Real, and the strongest artifact here** | Not a rate. A settled fact. |
| `run_1788265241/` | **SUPERSEDED** — see `SUPERSEDED.json` inside it | **No.** Excluded by `verify.py`, which says so on every run. |
| `smoke/` | **Not a result.** A pipeline check. | **No.** Never was. |
| *(none yet)* | A verified model-in-loop run | — |

`python3 verify.py` currently exits **2**: no live run is committed. That is the
repository declining to print a number it cannot defend, not a broken build.

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

# AGENTS.md — working notes for anyone editing this repository

Not a design document. The design is in `README.md`; the concessions are in
`LIMITS.md`. This is the map you need before changing anything, plus the rules
that are enforced by tests rather than by convention — because a convention does
not fail CI at 2am.

---

## Module map, in dependency order

Read bottom-up. Nothing below depends on anything above it.

| Module | Owns | May import |
|---|---|---|
| `paybound/ids.py` | `intent_id`, `idem_key`, `receipt` — **the only definition** | stdlib only |
| `paybound/core/money.py` | paise arithmetic; ints only, never float | stdlib |
| `paybound/core/types.py` | `TrustedState`, `ReasonCode`, Kleene logic, `FulfilmentState` | `core.money` |
| `paybound/core/policy/` | the nine clauses, predicates, amounts, `decide()` | `core.*` only |
| `paybound/ledger/` | sqlite: capabilities, intents, schema, durability | `core`, `ids` |
| `paybound/rail/` | Razorpay client, mode guard, error classification, reconcile | `core`, `ids`, `ledger` |
| `paybound/broker/` | case projection, tool-call dispatch | everything below |
| `paybound/agent/` | the model loop, tool registry, model ids | `core.types` only |
| `paybound/harness/` | runner, guard, stats, report, corpus_gen | everything |
| `verify.py` | recomputes published numbers | **stdlib only, never `paybound`** |

## Forbidden edges — each is a test, not a habit

| Forbidden | Enforced by | Why |
|---|---|---|
| `core/**` → `httpx`, `os`, `time`, `sqlite3`, `pathlib`, `random`, any model | `tests/arch/test_boundaries.py` | The policy layer must be provably free of network, clock, environment and model. Checkable by reading imports rather than trusting a claim. |
| `agent/**` → `rail/**`, `ledger/**`, `core/policy/**` | `tests/security/test_tool_surface_invariants.py` | The agent receives a `ToolPort` injected by the broker. It can never reach a credential, the database, or the decision logic. |
| `harness/corpus_gen/**` → `broker/**`, `core/policy/**` | `tests/arch/test_boundaries.py` | **A scientific boundary.** The attack author must not be able to read the defence, or the corpus can be overfit to it. |
| `RZP_KEY_SECRET` outside `rail/` | grep test | One reader. Every additional one is another place it can reach a log or a traceback. |
| A model id outside `agent/models.py` | `tests/arch/test_boundaries.py` | So a reviewer finds every model identifier by opening one file. |
| A `pbi_`/`pbr_` literal outside `ids.py` | `tests/arch/test_no_duplicate_id_derivation.py` | Four incompatible receipt derivations across seven design docs is what the review found, on the one path that can double-refund. |
| `verify.py` → `paybound` | `tests/contract/test_verify_agrees.py` | A verifier sharing the producer's arithmetic would cancel out a shared bug. |

If you need to cross one of these, the boundary is probably right and your
change is probably wrong. Argue it in a commit message before editing a test.

---

## Rules that have already caught real bugs

These are not style preferences. Each one is here because it failed.

**Never gate a command behind a pipe.** `ruff check . | tail -1` exits with
`tail`'s status, not ruff's. Three commits went in green that were not, and the
same idiom cost 20 requests of Gemini quota when a backgrounded benchmark was
piped through `tail` and died before writing its trials. Capture the exit code
and test it.

**Never scan source with a substring when the source discusses the property.**
The authorship check flagged commits for containing the word "claude" while
discussing the Claude Agent SDK. The forbidden-phrase scan flagged `LIMITS.md`
for *enumerating* the phrases it forbids. The zero-HTTP check flagged
`dispatch.py` for a docstring explaining that refusal happens before a socket
opens. Three times. **Parse, don't grep.**

**Never write text files without `newline="\n"`.** `Path.write_text` applies
universal-newline translation, so on Windows every file it produces is CRLF.
This broke the corpus seal three separate ways — the corpus bytes, the git
checkout, and the seal files themselves. `.gitattributes` pins the sealed
artifacts; `scripts/build_corpus.py::write_lf` is the writer.

**Never conflate "absent" with "unreadable".** `_find_by_receipt` returned
`None` for both, so an intent that had executed would have been recorded as
never sent. In money code, "I could not check" and "I checked and it is not
there" are different answers and must have different types.

**Never pool the two arms.** `arm1a` is a precondition-blind broker built to be
worse than the system. `verify.py` pooled it into the headline for one run,
because the arms share every field in the aggregation key -- which is exactly
what makes them comparable, and is why this needs a rule of its own rather than
falling out of the signature check. The error ran in the flattering direction
and cancelled the effect the control exists to show.

**A gate that cannot fail is decoration.** Every guard in this repository has a
test that deliberately breaks the thing it guards and asserts the build goes
red: I-10 deletes the aggregate bound, the C1 arm removes the family-A defence,
the secret scan gets a planted key, the overclaiming scan gets a real claim.

---

## Runbook

```bash
pip install -e ".[dev]"      # then `pb status` to see what is sealed
pytest -q                     # ~1400 tests, well under a minute
ruff check .                  # must exit 0; do not read this through a pipe
```

```bash
pb status                     # what is sealed, measured, and not
pb sweep                      # SWEEP-R analysed offline, zero API calls
pb score                      # per-class ceiling (KG-3), zero API calls
pb demo                       # report.html from the sealed corpus, oracle-routed
pb report                     # report.html from committed trials, model-routed
pb verify                     # recompute published numbers, offline, no keys
```

```bash
python scripts/build_corpus.py --benign    # rebuild + reseal the 80 benign items
python scripts/build_corpus.py --attacks   # 70 attacks; REFUSES without a seal
python scripts/run_benchmark.py --offset 0 --limit 20
```

### The benchmark is quota-bound

The Gemini free tier allows **20 requests per day per model**, and `max_steps`
is 4 -- so one trial costs between one and four requests, and twenty a day buys
roughly **ten items**, not twenty. Pass `--limit 20` anyway: the run halts
itself on a per-day 429 and prints the offset to resume from.

```
day 1   --offset 0  --limit 20     # completed 10, halted at the quota boundary
day 2   --offset 10 --limit 20
```

Read `next_offset` in the run's `manifest.json` rather than assuming the
previous day finished what it started.

A daily-quota 429 stops the run; a per-minute 429 is retried with backoff. The
two are told apart in `paybound/agent/loop.py`, and the distinction is not
cosmetic: retrying a per-day exhaustion spends tomorrow's budget re-reading
today's answer.

The order is derived from the corpus seal (`sha256(seal || item_id)`), which is
stratified and **cannot be re-rolled** — changing it requires changing a
committed hash. That is deliberate: a freely-chosen daily subset would invite
re-running a disappointing day with different items.

**Do not switch `T1_AGENT_UNDER_TEST` to dodge a quota error.** `verify.py`
refuses to pool trials across differing `model_id`, and a rate assembled from
several routers is not a rate. If the pinned model is capped, wait.

---

## Editing the policy

`core/policy/table.py` is hashed into `POLICY_SHA256`, which is hashed into the
corpus seal, which is checked before every benchmark run. So:

1. Editing a clause changes the policy hash.
2. `scripts/run_benchmark.py` then **refuses to run**, because the corpus was
   sealed against a different policy.
3. That is correct. Re-seal deliberately, and understand that every previously
   published number was produced under the old policy.

`aggregate_bound` and `no_prior_reason` have no defaults. A clause cannot be
authored without them, because forgetting the bound is how a clause becomes an
unbounded drain.

## Editing the tool registry

Same shape. `tools.lock.json` pins `sha256` over the serialized schemas, CI
fails on drift, and the hash covers `moves_money` — reclassifying a tool as
harmless is exactly the edit a hash ignoring that field would miss.

**`request_refund` must never gain an `amount` or a payment id.** Not "validate
them"; they must not exist. You cannot filter out a parameter that was never
declared, and that absence is the entire security argument.

---

## Where the money actually moves

Exactly one place: `rail/refunds.py::execute_refund`, reached only from the
broker on `ALLOW`, only after the intent is fsynced, and only once per intent —
`attempts <= 1` is a CHECK constraint, so a retry is a database error rather
than a policy decision.

There is **no retry**. An ambiguous outcome is resolved by
`rail/reconcile.py`, which reads the ledger and never POSTs. If you find
yourself adding a retry to a refund path, stop: that is how one intent becomes
two refunds, and it is the failure this entire design is arranged around.

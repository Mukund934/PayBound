# PayBound — Implementation Contract

**Bounded authority for autonomous commerce.**

Derived from `private-doc/architecture/01_ARCHITECTURE_LOCK.md`. **This document restates the lock for the
builder; it does not create architecture.** Where this file and the lock disagree, the lock wins and this
file is the bug. Where the lock is silent, `AGENTS.md` decides.

**Day 5 of 9 · Mon 31 Aug 2026 · submit Fri 4 Sep 2026** (deadline 5 Sep, no cutoff time or timezone
published, one-shot form with no edits — so 4 Sep is the target and 5 Sep is buffer only).

> **Schedule state: three build days lost.** Days 2–4 (Fri 28 – Sun 30 Aug, 34 effective hours) produced no
> commits. The plan in Part 4 of the lock is no longer achievable as written. **§13 is the reconciled
> position and supersedes the day table for scheduling purposes only** — it changes *what gets built*, never
> *what a number means*. Read §13 before starting work.

---

## 1. FINAL PRODUCT

PayBound is a **bounded-authority agent runtime for the return leg of agentic commerce.**

An AI buyer — holding no payment credential — completes a real purchase against Razorpay test-mode APIs, and
later asks for its money back in natural language. A merchant-side runtime lets that prose **route** to one of
nine closed reason codes and lets it do nothing else. Whether a refund is owed, and for how much, are
**recomputed by deterministic code from trusted state alone**: Razorpay's ledger, a fulfilment record, the
catalogue, and a merchant-authored policy table. Every branch precondition is verified independently, every
clause carries an aggregate bound asserted against Razorpay's own ledger, and the runtime either executes the
refund at the amount **it** computed or escalates having made **zero** outbound HTTP calls.

What it measures: **the price of that rule** — what fraction of legitimate refund requests, per evidence
class, can be automated at all.

## 2. FINAL TRACK

**Track 01 — AI Growth & Agentic Commerce**, second disjunct, quoted:

> *"Build an agent that grows revenue for a merchant on Razorpay test-mode APIs, **or that makes a merchant
> transactable by an AI buyer end to end.**"*

Satisfied by construction: the same non-human counterparty runs the purchase leg **and** the return leg over
the same wire, and both produce real objects in Razorpay's ledger. THE BAR — *"Every money action explainable,
bounded and gated. Show the audit trail and one failure handled gracefully."*

**Say plainly on camera: "this does not grow revenue — it is the half of the loop the protocols delegated and
nobody built."** Do not spin it.

## 3. PRIMARY USER

An Indian D2C merchant on Razorpay — apparel, footwear, supplements, small electronics; ₹2–30 Cr GMV; 3–8%
refund rate; a support inbox that is the largest human cost centre in the business. Secondary: the support
lead who owns the refund SLA and has to explain a wrong refund.

## 4. PRIMARY WORKFLOW

```
AI buyer browses catalogue → forms cart → order created (carries order_notes)
        → Standard Checkout (Playwright) → CAPTURED PAYMENT in Razorpay
                              ↓
        AI buyer writes a refund request in natural language
                              ↓
        Merchant ingests it as an UntrustedSpan (tagged at ingestion, in code)
                              ↓
        Broker opens a CASE: binds the payment by deterministic lookup,
        mints cap_r_ (read, multi-use) and cap_w_ (write, single-use)  ← PRE-MODEL
                              ↓
        Agent (3 tools) reads the case and emits request_refund(cap_w, reason_code)
                              ↓  ← the model's entire influence: 1 of 9 enum members (<3.2 bits)
        Policy: preconditions re-verified from TRUSTED STATE ONLY
                policy_amount() computed by code
                aggregate bound asserted against Razorpay's ledger
                auto_max_paise is a GATE → ESCALATE (never a clamp)
                              ↓
        ALLOW → write-ahead intent (fsync) → POST /v1/payments/:id/refund → read-back
        DENY / ESCALATE → zero outbound HTTP calls
                              ↓
        GET /v1/payments/:id/refunds  ← EXTERNAL GROUND TRUTH
```

## 5. THE THREE BOUNDARIES

### 5.1 Security boundary

`broker.execute(cap_w, reason_code) -> Allow(amount, clause) | Deny | Escalate`

**Untrusted text may ROUTE. It may never be EVIDENCE.** Attributed to PACT (arXiv:2605.11039) — cited, not
claimed.

### 5.2 AI boundary

| Layer | What | Model |
|---|---|---|
| **T0** | Every authority-bearing computation: refundability, amount, window checks, aggregate bound, capability redemption, idempotency, error classification | **No model at all** |
| **T1** | The agent under test — routes prose to a reason code | `claude-sonnet-5`, `strict: true` tools, `disable_parallel_tool_use: true` |
| **T2** | Offline, one-time: corpus authoring, ADAPT-1 search driver | `claude-opus-5` — never in the runtime path |

T0 **is** the thesis and is the literal answer to the rubric line *"AI judgment — the right tool in the right
place, **and where you chose not to use one**."*

`paybound/agent/models.py` is the **only** file containing a model id string.

### 5.3 Razorpay boundary

`paybound/rail/` is the **only** module that may read `RZP_KEY_SECRET`. One grep test enforces it.

| Adapter method | HTTP | Caller |
|---|---|---|
| `create_order` | `POST /v1/orders` | seeder |
| `get_order` / `list_order_payments` | `GET /v1/orders/:id` · `/payments` | seeder, projection |
| `get_payment` | `GET /v1/payments/:id` | projection, read-back |
| `capture` | `POST /v1/payments/:id/capture` | seeder (manual-capture accounts only) |
| `patch_payment_notes` | `PATCH /v1/payments/:id` | planter — fixed 8-key map, always full |
| `create_refund` | **`POST /v1/payments/:id/refund`** | **broker only** — this exact path, asserted by I-03 |
| `list_payment_refunds` | `GET /v1/payments/:id/refunds?from&to&count=100` | **primary ground truth** |
| `list_refunds_window` | `GET /v1/refunds?from&to&count=100` | once per run, orphan cross-check only |
| `verify_checkout_signature` | local HMAC | seeder callback |

**Deliberately not used:** webhooks, Payment Links (except ≤2 burned in the spike), QR/BharatQR, customers,
invoices, subscriptions, S2S, Razorpay's MCP server. No generic `request()` escape hatch.

## 6. THE RETRY / IDEMPOTENCY CONTRACT — one rule, no judgement

> **A refund POST is attempted at most once per intent. Every field of the request is serialized once, before
> the first attempt, and stored as `request_bytes` in the write-ahead intent log. There is no retry. An
> ambiguous outcome is resolved by reading the ledger.**

`paybound/ids.py` is the only definition of `intent_id`, `idem_key` and `receipt`. **Already implemented and
tested (25 tests green).** `httpx.HTTPTransport(retries=0)` — a pool-level retry your own code never sees is
the classic way a 502 becomes two refunds.

| Observed | Action | Bucket |
|---|---|---|
| `200` + `rfnd_` | record id/amount/`created_at`; read-back | 1 EXECUTED |
| `400 "Refunds cannot be created on your account."` | **abort run** → contingency ladder | — |
| `400 "…not enough balance…"` | **abort run.** Canary should have caught it | — |
| `400 "Partial refund is currently not supported…"` | mark `partial_supported=false`; re-issue on same payment or drop | 2 ENV_REFUSED |
| `400 "Duplicate receipt found…"` | **do not retry.** Read-back: found ⇒ EXECUTED; absent ⇒ UNKNOWN | 1 or 3 |
| `400 "another payment operation is in progress"` | near-unreachable (per-payment mutex). Non-zero ⇒ a second writer exists ⇒ investigate | 2 |
| `400` amount invalid / exceeds refundable | **our bug — abort loudly.** Never silently reduce | — |
| `409` (either variant) | **contract violation on our side.** No retry. POISONED. Fail the run | fail |
| `429` | honour `Retry-After` / backoff, ≤6 — **the one place a retry is legal, because no request was accepted** | — |
| `5xx` / timeout / reset / unparseable 2xx | **UNKNOWN.** Read at T+3s and T+15s matched on `receipt`. Absent ⇒ bucket 3. **Never re-POST** | 3 |

## 7. THE TEN INVARIANTS — all non-negotiable, all red-capable

| # | Invariant | Discharging test |
|---|---|---|
| **I-01** | Tool registry is exactly **3** verbs; no `razorpay*` importable from `agent/` | sha256 lockfile over serialized tool schemas; `len(TOOLS)==3`; import-graph test |
| **I-02** | Credential unreachable from agent code | import-graph + single-file grep + 2,000-case tool fuzz greping every agent-visible byte incl. tracebacks |
| **I-03** | `amount` is never model-influenced | Read back from ledger; assert `amount == policy_amount(clause, state)` **byte-exact**; assert POST path |
| **I-04** | Case↔payment binding is pre-model and immutable | No tool schema accepts a payment id; foreign token → DENY, zero HTTP; `pay_` never in agent-visible payload |
| **I-05** | Every error path fails closed | Fault injection at ~35 call sites; assert **zero** Allow and **zero** POSTs. **The file you point a reviewer at first** |
| **I-06** | Live-key refusal **per request** | Flip key to `rzp_live_` mid-run; next request raises before socket open |
| **I-07** | At-most-once | `kill -9` between intent-write/POST and POST/outcome-write; 5× serial + 5× concurrent replay; ≤1 ledger object |
| **I-08** | **Aggregate bound asserted against the ledger** | Create an out-of-band refund directly; assert pre-flight read sees it and the next action is refused |
| **I-09** | Four-outcome accounting + denominator guard | Force each bucket + `MODEL_DECLINED`; assert the report generator **raises** while bucket 3 is non-empty |
| **I-10** | **The gate can go red for a security reason** | Mutation test: delete I-08's precondition; assert CI turns red. *A gate that cannot fail is decoration.* |

## 8. EVALUATION METHODOLOGY

**Ground truth is external:** `GET /v1/payments/:id/refunds`. No LLM judge, no human labelling. Raw JSON
committed to `evidence/`. `verify.py` (stdlib-only) recomputes every published number offline, no keys.

**Headline metric — refund automation rate, PER EVIDENCE CLASS. Never pooled.**

| Ledger-verifiable | Irreducibly testimonial |
|---|---|
| duplicate charge · non-delivery · cancellation inside window · price mismatch | arrived damaged · wrong item · quality · changed mind outside window |
| *recomputable from `payment.amount`, `order.status`, `created_at`, fulfilment* | *nothing in any payment data model knows* |

Publish each with its own denominator and Wilson interval, plus the one-line formula a merchant substitutes
their own reason mix into. **Never publish the pooled ceiling** — it is a corpus-composition artifact, and two
hostile reviewers killed the design that did.

**"Zero policy-unauthorised refunds" is a type-level property proven by I-03. It is labelled as such, not
presented as a finding.** Reporting a theorem as a result is what killed the previous architecture.

**Four buckets** — broker refused / Razorpay refused environmentally / transport failed / `MODEL_DECLINED` —
with buckets 2–4 excluded from numerator and denominator, behind a denominator guard.

**Arms:** arm 0 derived (review-first = Razorpay's own shipped mode, ASR 0% / FRR 100%) · arm 1a
(precondition-blind broker) · arm 2 (PayBound) · C1 scripted hostile agent to depth 2 (648 assertions, zero
model cost) · C3 ADAPT-1 (the one hand-crafted adaptive attack). **No naive-agent arm.**

**Statistics:** counts, Wilson intervals, rule-of-three, one design sentence. No bootstrap, no p-values.
n is **item-level**, not trial-level.

## 9. MUST HAVE / MUST NOT BUILD

**MUST HAVE (the minimum winning system):** ~130 seeded real captured payments from an AI buyer that browsed a
catalogue · one agent under test reading real Hinglish prose · **three tools**, one authority-bearing, no
amount and no payment id · one deterministic nine-clause policy table with a mandatory aggregate bound · two
capability tokens per case, minted pre-model · write-ahead intent log with at-most-once and boot reconciliation
· 80 benign `(prose, real state)` pairs and 70 attack items, four-bucket classified · 648 scripted hostile
assertions · ten invariants, all red-capable · raw ledger JSON committed · one ~300-line stdlib-only
`verify.py` · one `report.html` with the five-column Decision View · one README whose first screen is the
architecture diagram and the prior-art concession by name.

**MUST NOT BUILD (≈6.4 days already recovered by deleting these):** two-process separation · a live UI console,
server, SSE or any `/api/*` route · a second verifier implementation · cluster bootstrap or any p-value · the
naive-agent arm · `reply_to_customer`, `read_policy`, `list_refundable_orders` · a hash-chained audit log ·
attack families P (beyond 10), E, D · a `POST /acp/orders` HTTP surface · two of three policy ladders · nine
of nineteen invariants · three of six fault scenarios · the quiesce state machine · the expected-branch
predictor · eight of twelve markdown docs · `verify --live` · run-B as a full re-run · the corpus-as-generator
scheme · **both copies of the invented "96% / four in ten" failure paragraph.**

**Also forbidden:** any claim of architectural novelty · the words *"novel architecture"*, *"first firewall"*,
*"provably secure"*, *"solved prompt injection"*, *"100% blocked"*, *"nobody measures false refusals"*,
*"this is Action-Selector"*, *"strictly stronger than Agent-Sentry"* · any NPCI UAP claim · narrating a refund
to an attacker's UPI ID (refunds have no destination parameter).

## 10. FINAL DEMO

Hero beat at **1:58**, two-pane: the customer's prose left, and right the boxed line
**`policy_amount ₹2,499.00 — chosen by core/policy/table.py:41, not by the model`** — then the object appearing
in Razorpay's own dashboard at that exact amount. Then the identical path with a hostile ticket, fifth column
reading `outbound HTTP calls during this decision: 0`. Hero case is **`DUPLICATE_CHARGE`** everywhere: video,
README screenshot, `report.html` landing.

Open with **AP2's own threat model on screen** — *"AP2 assumes that preventing prompt injection attacks is
infeasible"* — attributed to `google-agentic-commerce/AP2`. Google and FIDO write the pitch, not us.

## 11. STACK (locked)

Python 3.11+ · `httpx` **retries=0**, connect 10s / read 45s · stdlib `sqlite3`, WAL, `synchronous=FULL` on the
intent DB · **hand-rolled Razorpay client** (~200 lines + pydantic parsing — the official SDK hides raw bodies,
and raw bodies **are** the evidence artifact) · **no agent framework**, ~150-line tool loop on `anthropic` ·
policy = plain typed Python, frozen table + pure predicates, **no DSL** · **no server** (`pb demo` writes
`report.html`, you open the file) · one HTML + one CSS + three vanilla ES modules · `pytest` · **nothing
hosted** · GitHub Actions, two jobs (`check`, `verify`).

## 12. KNOWN ASSUMPTIONS AND DEVIATIONS

**Deviations from the lock, surfaced rather than silently adopted:**

1. **Python 3.13.5, not 3.11.** 3.11 is not installed on this machine. Everything used is 3.11-compatible and
   `requires-python = ">=3.11"`. `verify.py` stays stdlib-only so a reviewer on either runs it. **Low risk.**
2. **`pip` + venv, not `uv`.** `uv` is not installed here and would be one more thing a reviewer must install.
   The lock's own overriding principle is *"the artifact must open on the machine that grades it, and every
   build step is a new way for it not to run."* `pyproject.toml` works with both. **Low risk.**
3. **Repo lives at `RazorPay-Buildthon/paybound/`, a sibling of `private-doc/`, not its parent.** Private
   strategy material is therefore *outside the repository tree entirely* rather than merely git-ignored —
   a stronger property than a `.gitignore` rule. **Improvement, not a deviation in substance.**

**Assumptions still unverified (KG-1 exists to settle them):** refund creation works against `rzp_test_` keys ·
no account-level refund gate on a fresh account · partial refunds supported on the seeded payment method ·
idempotency replay semantics after completion · PATCH notes merge-vs-replace · when `amount_refunded`
increments relative to `refund.status` · headless Playwright can drive Standard Checkout to capture.

---

## 13. RECONCILED POSITION — 31 Aug 2026, 12:40 IST

Reconciled against the actual tree, not against the previous status message. Everything below was verified by
inspection.

### 13.1 What actually exists

| Component | State | Evidence |
|---|---|---|
| `paybound/ids.py` | **DONE**, frozen | 21 tests |
| `tests/arch/test_no_duplicate_id_derivation.py` | **DONE** | 4 tests |
| `scripts/kg1_spike.py` | **WRITTEN**, never executed against real keys | needs `.env` |
| `paybound/core/money.py` | **DONE** | paise-only arithmetic, no floats |
| `paybound/core/types.py` | **DONE** | Kleene, TrustedState, 9-member enum |
| `paybound/core/policy/{predicates,amount,table,decide}.py` | **DONE** | `POLICY_SHA256 = ee0e8589…` |
| `paybound/rail/modeguard.py` | **DONE** | **I-06 green** — was a missed Day-1 gate |
| `tests/security/test_authority_invariants.py` | **I-03, I-05, I-06 green** | 26 tests |
| `tests/arch/test_boundaries.py` | **DONE** | core purity, credential edge, secret scan |
| `docs/CITATIONS.md` | **DONE** | C-01 and C-02 are seal-blocking |
| `paybound/{ledger,broker,agent,harness,seed}/` | **NOT STARTED** | — |
| `verify.py`, `README.md`, `LIMITS.md`, `PREREG.md`, `.github/workflows/` | **NOT STARTED** | — |
| `corpus/`, `evidence/`, `fixtures/` | **EMPTY** | — |

**58 tests, 56 passing, 2 skipped** (the skips are forbidden-edge tests for `agent/` and `harness/corpus_gen/`,
which fail automatically the day those packages appear without their edge test). `ruff` clean.

`POLICY_SHA256` is computed over the table's *semantics*, not its source text — it was unchanged by a
reformat, which is the property that lets a published number name its policy across cosmetic edits.

### 13.2 The arithmetic, stated plainly

Effective hours remaining from now: **~39** (6 today + 9 + 9 + 9 + 6). Work remaining under the lock's Part 4
plan: **~76 hours**. The cut ladder's rungs #1–#6 return ~12 h. **The ladder alone does not close a ~37-hour
gap.** Saying so here rather than discovering it on Day 7 is the whole point of this section.

### 13.3 CLAUDE WORK — proceeds now, no human input

Ordered by downstream fan-out. None of it depends on KG-1's answers.

1. `ledger/` — sqlite schema, WAL, `synchronous=FULL`, intents, capabilities, events. **→ I-07**
2. `broker/open_case.py` — case-shaped two-token mint, atomic single-use consume. **→ I-04**
3. `agent/tools.py` + `tools.lock.json` — three verbs, sha256 lockfile. **→ I-01, I-02**
4. `rail/` — `LedgerPort`, hand-rolled client, error classification (§6 is already fully specified),
   recording/replay transport for fault injection. **→ I-05 at ~35 call sites, I-08**
5. `harness/{runner,guard}.py` — four buckets, `MODEL_DECLINED`, denominator guard. **→ I-09, I-10**
6. `verify.py` — stdlib only, offline, no keys.
7. `corpus/` authoring against fixture ledger states; `README`, `LIMITS`, `PREREG`, CI workflow.

The `--dry-ledger` mode (contingency Rung 2) is **built as the default execution path** and switched to live
execution once credentials arrive. It costs nothing extra — the broker already halts with the exact bytes it
would have POSTed — and it means every hour spent before `.env` exists produces the same artifact.

### 13.4 HUMAN WORK — two items, ~4 minutes total

| # | Action | Why it cannot be automated | Blocks |
|---|---|---|---|
| **H-1** | Razorpay **Test Mode** key id + secret into `paybound/.env` | Belongs to Mukund's Razorpay account; no API issues its own credentials | KG-1, seeding, all live execution |
| **H-2** | `ANTHROPIC_API_KEY` into the same `.env` | Same reason | The agent under test (needed by the runner, not before) |

Later, and not yet blocking: creating the public GitHub repository (Day 9), recording the video (Day 8),
submitting the form (Day 9). `gh` is not installed on this machine, so the remote is created in a browser and
Claude pushes to it.

### 13.5 BLOCKERS

| Blocker | Class | Reality |
|---|---|---|
| No `.env` | **HARD HUMAN** | Blocks KG-1 and live execution only. Roughly 15% of remaining work. |
| C-01 carrier vocabulary uncited | **CLAUDE-RESOLVABLE**, seal-blocking | Must close before `corpus/SEAL.json` is written |
| C-02 returns policy uncited | **CLAUDE-RESOLVABLE**, seal-blocking | Same |
| KG-1's seven open API questions | **INFORMATIONAL** | The conservative branch is already implemented for each; none blocks construction |
| Schedule gap of ~37 h | **HARD HUMAN DECISION** | §13.6 |

Not blockers: running tests, running KG-1 once `.env` exists, installing dependencies (done), refactoring,
documentation, fixtures, benchmarks, debugging, commits.

### 13.6 The one decision that is genuinely Mukund's

The gap does not close by working the plan harder. It closes by cutting scope, adding hours, or both.
**Recommended, in this order:**

1. Apply cut-ladder rungs **#1–#6 now**, not on Day 7 (~12 h).
2. Halve the corpus: **80 → 40 benign, 70 → 35 attack** (~8 h). Per-class denominators fall to ~5 and Wilson
   intervals roughly double in width. The lock protects *a frozen corpus*, not the number 80. This is the
   cheapest remaining cut that does not touch a NEVER-CUT item.
3. Size the seed to the smaller corpus: **~130 → ~50 payments**. Little wall clock, much less seeding risk.
4. Move the freeze to **Wed 2 Sep, 18:00** — the lock's own "two days behind" rung. Earlier, not later.

That still leaves roughly 17 h to find, which means **~13 h/day Mon–Thu instead of 9**. Whether those hours
exist is Mukund's call and nobody else's.

**Calendar collision to check now, not on Thursday:** the SIH 2026 IIIT-NR internal registration closes
**Wed 3 Sep** and needs a team of six plus a named problem statement. Day 8 (Thu 3 Sep) is video day — three
rehearsals and three takes. These overlap.

**NEVER CUT, unchanged:** the frozen benign corpus · four-bucket accounting and the denominator guard · all
ten invariants · `verify.py` and the committed evidence · the architecture SVG on the README's first screen ·
the AI-buyer purchase leg · the video · `INCIDENTS.md`.

### 13.7 Next actions

**NEXT AUTOMATABLE ACTION:** `ledger/` — schema, intents, capabilities, events — then `broker/open_case.py`.
Starts immediately, needs nothing from anyone.

**NEXT REQUIRED HUMAN ACTION:** H-1 and H-2 above. ~4 minutes. On completion, Claude runs KG-1 unprompted and
records the result here and in `INCIDENTS.md`.

---

**KG-1 status: NOT EXECUTED. Reason: no local credentials. Observed Razorpay failure: none.** The spike has
been exercised against the real API far enough to confirm the transport, the auth header, the error parsing
and the mode guard (a deliberately invalid test key returns a correctly parsed `401 Authentication failed`).
It has never been run with valid keys, so it has never answered the question it exists to answer. Nothing
about Razorpay's refund behaviour has been observed, and nothing may be claimed about it.

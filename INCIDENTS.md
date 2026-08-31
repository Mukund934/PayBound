# Incident log

Ten minutes at the end of every build day, from Day 1. This is the **only**
source for the submission's *"Build Challenges & Technical Obstacles"* answer —
the field the buildathon page says is read first, and which the published rubric
grades again under *Failure recovery*.

Rules, so the entries are usable later:
1. Only incidents in the **money or measurement core**. A broken CSS build or a
   video encoding problem tells a reviewer the core was never stressed.
2. Shape: **what I expected → what actually happened → how I found it → what the
   number did afterwards.**
3. Verifiable in the repo — a commit, a test, a log line.
4. Written the day it happened. A war story reconstructed on 4 September from
   memory reads like one.

---

## Day 1 — Thu 28 Aug 2026

### 1.1 The spike wrote its results file twice

**Expected:** one results file per run.
**Happened:** `finish()` ran on the block-A early-return path *and* again in the
`finally`, so `kg1_result.json` was written twice and the log printed the path
twice.
**Found:** running the spike with a deliberately invalid test key to prove the
auth path returns a clean 401 — the duplicate line was visible in the output.
**Fix:** `finish()` is idempotent via a module-level flag. Commit `57d5c2a`.
**Why it is worth recording:** harmless here, but the same shape — a cleanup
path that runs on both the error branch and the `finally` — is exactly how a
refund gets POSTed twice. Found it in the reporting layer first, which is the
cheap place to find it.

### 1.2 KG-1 blocked on credentials, not on Razorpay

**Expected:** run the feasibility gate on Day 1.
**Happened:** no `.env` exists, so the gate cannot touch the API.
**Found:** immediately — the credential guard refuses to start.
**Status:** OPEN. The gate is written, compiles, and has been exercised against
the real API far enough to confirm the transport, the auth header, the error
parsing and the mode guard all work (a deliberately wrong test key returns a
correctly parsed `401 Authentication failed`). It needs real test keys to answer
the question that matters.

---

## Day 5 — Mon 31 Aug 2026

### 5.0 Correction to the Day 1 heading

The Day 1 entry is dated "Thu 28 Aug 2026". 28 August 2026 is a Friday. The work
in that entry was done on **Thursday 27 August 2026** — the commit timestamps on
`57d5c2a` and `f87d953` are both 27 Aug 21:10 IST. Per this log's own rule,
the earlier entry is left as written and corrected here.

### 5.1 The aggregate-bound test passed without ever reaching the bound

**Expected:** a test that refunds against a payment which is already half
refunded would exercise the aggregate bound and get a DENY.
**Happened:** it got an ESCALATE, from `nothing_refunded_yet` — a *clause*
precondition on `DUPLICATE_CHARGE` that fires several steps before the universal
bound is evaluated. Had the assertion been written loosely — `assert not
d.is_allow` instead of `assert d.outcome is DENY` — it would have passed, and
I-08 would have had a green test that never once executed the line it claims to
protect.
**Found:** the assertion was written against the specific outcome, so the
failure named the predicate that actually fired.
**Fix:** the bound's test now routes `PRICE_MISMATCH`, which is the only clause
that can reach the bound at all — its amount is a *line difference* computed
against the immutable `payment.amount`, so it is precisely the clause a drain
attack would use. A second test pins which layer fires first for the
full-payment clauses, so a later edit that deletes `nothing_refunded_yet` shows
up as a changed rationale rather than silently falling through to the bound.
**Why it is worth recording:** the two layers are not redundant, they are
ordered, and the ordering was invisible until a test demanded a specific
outcome. A test that asserts "not allowed" cannot tell a working safety property
from a different working safety property standing in front of it.

### 5.2 One of the ten invariants had a test that asserted nothing

**Expected:** `test_i06_test_key_is_accepted` checked that a `rzp_test_` key
passes the live-key guard.
**Happened:** the body was `assert_test_mode(...) is None` — an expression
statement with no `assert` keyword. Python evaluates it, discards the boolean,
and the test passes unconditionally. It would have kept passing if the guard had
been changed to reject test keys outright.
**Found:** indirectly. A new secret-scanning test flagged the key-shaped literal
on that line; reading the line to add the exemption marker is what exposed the
missing keyword.
**Fix:** rewritten to bind the return value and assert on it.
**Why it is worth recording:** I-06 is one of the ten invariants the submission
stakes its claim on, and one of its two tests was decoration. The lesson is the
one I-10 already encodes for the suite as a whole — *a gate that cannot fail is
decoration* — reappearing one level down, inside an individual test. It was
found by an unrelated lint-shaped test, which is an argument for having more of
them rather than fewer.

<!-- Day 2 onward: append below. Do not edit earlier entries; if something turns
     out to be wrong, add a correcting entry and say so. -->

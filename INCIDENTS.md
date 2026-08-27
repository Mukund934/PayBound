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

<!-- Day 2 onward: append below. Do not edit earlier entries; if something turns
     out to be wrong, add a correcting entry and say so. -->

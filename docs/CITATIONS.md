# Open citation obligations

Every claim in PayBound that rests on something outside this repository must
name its source by URL and archive timestamp **before** the artifact that uses
it is sealed. This file tracks the ones that are still open.

The rule that produced this file: **a fabricated citation is worse than a
missing one.** A missing citation is an honest gap a reviewer can weigh. An
invented URL, an invented statistic, or an invented "industry standard" is the
single fastest way to lose a technical reviewer's trust in everything else in
the submission — and the architecture lock records that a previous version of
this project was killed for exactly that class of mistake.

Status legend: **OPEN** — needed, not yet obtained · **BLOCKING** — an artifact
cannot be sealed without it · **CLOSED** — recorded below with URL and date.

---

## C-01 — Carrier fulfilment vocabulary · **BLOCKING the corpus seal**

**What needs a source:** the member names of
`paybound/core/types.py::FulfilmentState` — specifically `rto_initiated`,
`lost_in_transit`, `undelivered_consignee_refused`, `not_dispatched`,
`in_transit`, `delivered`.

**Why it matters:** the tier ladder's central claim is that *which refund
reasons become decidable at which tier* is a fact about Indian logistics, not a
fact about a fixture file this project wrote. If the vocabulary is invented, a
reviewer can say the tier boundaries were drawn wherever they happened to
produce a good number, and the T0/T1/T2 result collapses into an artifact of the
author's own enum.

**What closes it:** one published Indian carrier tracking API — Delhivery,
Shiprocket, Blue Dart or equivalent — whose documented status vocabulary
contains these states. Recorded here as URL + retrieval date + archive URL, and
the enum's docstring updated to name it.

**Until it is closed:** the enum carries an explicit "SOURCE NOT YET
TRANSCRIBED" notice in its docstring, and `corpus/SEAL.json` must not be
written. Any member name that turns out to have no published counterpart is
renamed or deleted, not kept because the tests already use it.

**Status:** OPEN · BLOCKING

---

## C-02 — Merchant returns policy · **BLOCKING the corpus seal**

**What needs a source:** the seven-day return window
(`TrustedState.return_window_days`) and the shape of the nine-clause policy
table.

**Why it matters:** the same argument as C-01. A policy transcribed from a real
archived merchant returns page is evidence about the domain; a policy the author
wrote is a hypothesis about it.

**What closes it:** one archived returns policy from a real Indian D2C merchant,
cited by URL and Wayback timestamp, with the transcription noted where it
differs.

**Status:** OPEN · BLOCKING

---

## C-03 — Prior art conceded by name · README first screen

**What needs a source:** CaMeL, Fides, Aegis, PACT and PACE, plus AP2's threat
model line and ACP's "returns authorization flows" scope exclusion.

**Why it matters:** the README concedes prior art by name on the first screen,
and the video opens on AP2's own threat-model text. Both are quotations from
other people's work and must be attributed exactly.

**What closes it:** arXiv ids and repository URLs, with the AP2 and ACP lines
quoted verbatim and located precisely enough that a reviewer can find them.

**Partial:** the implementation contract already attributes the untrusted-text
rule to PACT (arXiv:2605.11039). That single id needs verifying against the
actual paper before it ships in the README — a wrong arXiv number is a
fabricated citation even when the paper is real.

**Status:** OPEN

---

## C-04 — Reason-mix weights for the worked pooled example

**What needs a source:** the reason-code weights used in the one worked example
that shows a merchant how to substitute their own mix.

**What closes it:** a published Indian e-commerce returns-reason breakdown,
found inside a 20-minute box. If none is found in that box, the example uses the
corpus weights **and says so in the same sentence** — which is the pre-committed
fallback, not a failure.

**Status:** OPEN · not blocking (has a pre-committed fallback)

---

## Closed

*(none yet)*

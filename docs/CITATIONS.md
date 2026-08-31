# Citations

Every claim resting on something outside this repository names its source by
URL and retrieval date, and by archive snapshot where one exists.

The rule that produced this file: **a fabricated citation is worse than a
missing one.** A missing citation is an honest gap a reviewer can weigh. An
invented URL, statistic, or "industry standard" is the fastest way to lose a
technical reviewer's trust in everything else here.

Every URL below was fetched on **31 August 2026** and independently re-fetched
by a second pass that checked each quoted string against the raw bytes. Where a
snapshot is cited, that snapshot was fetched and the string confirmed inside it
— an availability-API timestamp was not treated as proof.

---

## C-01 — Carrier fulfilment vocabulary · **CLOSED, under an amended condition**

### The condition had to change, and that is the finding

C-01 originally demanded *"one published Indian carrier tracking **API** whose
documented status vocabulary contains these states."* **No such public artifact
exists**, and this is not a search failure:

- Delhivery's API reference is behind a login at
  `ucp.delhivery.com/developer-portal`.
- `apidocs.shiprocket.in` is a client-rendered shell with no status list in its
  served HTML.
- `help.delhivery.com/llms.txt` indexes dashboard and UI documentation only.

What exists instead is **operator-facing status vocabulary** — what a merchant
sees in a shipping dashboard. That is weaker than an API schema and far stronger
than an invented list. C-01 closes against that narrower condition, stated here
rather than quietly substituted.

### Sources

| Source | Class | Retrieved | Archive |
|---|---|---|---|
| [Shiprocket, "Important terms all Shiprocket users should know"](https://support.shiprocket.in/support/solutions/articles/43000662858) | aggregator, operator docs | 2026-08-31 | `web.archive.org/web/20260420193303/` ✓ fetched |
| [Delhivery, "Courier Return / RTO"](https://help.delhivery.com/docs/courier-returnrto) | **carrier-direct** | 2026-08-31 | `web.archive.org/web/20240417180319/` ✓ fetched |
| [Delhivery, "Non-Delivery Report (NDR)"](https://help.delhivery.com/docs/non-delivery-report-ndr) | **carrier-direct** | 2026-08-31 | `web.archive.org/web/20260413235754/` ✓ fetched |
| [ClickPost, "Tracking status codes"](https://docs.clickpost.ai/docs/tracking-status-codes) | aggregator, normalised code table | 2026-08-31 | **none — zero snapshots for the whole domain** |
| [ClickPost, "NDR status codes"](https://docs.clickpost.ai/docs/ndr-status-codes-1) | aggregator | 2026-08-31 | **none** |

The ClickPost archive gap was confirmed two ways: the exact-URL CDX query is
empty, and a domain-wide CDX on `docs.clickpost.ai` is also empty, while
`www.clickpost.ai` marketing pages *are* archived. It is a real gap, not a
robots block. Those two rows are **live-only citations** and are marked as such
wherever they are used.

### Per-member provenance

| `FulfilmentState` member | Published token | Source |
|---|---|---|
| `IN_TRANSIT` | `In-Transit:` | Shiprocket (archived); ClickPost `5 InTransit` |
| `DELIVERED` | `Delivered:` | Shiprocket (archived); ClickPost `8 Delivered`, terminal |
| `RTO_INITIATED` | `RTO Initiated:` | Shiprocket (archived); Delhivery `courier-returnrto` (archived) |
| `LOST` | `Lost` | ClickPost `16 Lost`, terminal; Delhivery `Lost` |
| `UNDELIVERED_CONSIGNEE_REFUSED` | **conjunction, not a token** | Shiprocket `Undelivered:` + Delhivery "Consignee refused" (both archived) |
| `NOT_PICKED_UP` | **none — derived** | author-defined; see below |

### Two members were renamed rather than stretched to fit

**`lost_in_transit` → `LOST`.** No source publishes "lost in transit" as a
status. The phrase appears only as Delhivery prose. The published token is
`Lost`.

**`not_dispatched` → `NOT_PICKED_UP`, declared derived.** A search across all
sources for `not[ _-]?dispatch|undispatch|not[ _-]?shipped` returns zero hits.
The member is an author-defined aggregation of everything before a pickup scan.
It is deliberately **not** named after ClickPost's published `2 PickupPending`,
because adopting a real token for an invented aggregation is a subtler
fabrication than an obviously-derived name.

### Two published-vocabulary defects, both of which reach the money

Neither is a PayBound design choice. Both are properties of what carriers
actually publish, and both are recorded in `LIMITS.md` and carried in the corpus
rather than assumed away.

**1. `Undelivered` is a superset.** Shiprocket's published `Undelivered` covers
parcels in an active three-attempt reattempt cycle, not only refusals. A
merchant mapping it directly onto `UNDELIVERED_CONSIGNEE_REFUSED` would place a
parcel that is about to be delivered into `LEDGER_NONDELIVERY_STATES` and
authorise a full refund on it. PayBound's member is therefore a declared
conjunction requiring a refusal signal, not mere delivery failure.

**2. `Lost/Damaged` is one status where ClickPost has two.** Shiprocket
publishes the single combined status `Lost/Damaged:`; ClickPost separates
`16 Lost` from `17 Damaged`. On a Shiprocket-fed merchant a **damaged** parcel
therefore surfaces as `LOST`, which sits in `LEDGER_NONDELIVERY_STATES` and
authorises `NOT_DELIVERED` at the full payment amount — for goods the customer
is physically holding. This is a second bridge into the highest-paying clause,
independent of the routing attacks, and it comes from the vocabulary itself.

### Still owed

Two Wayback saves that require a human to visit `web.archive.org/save/`: the
ClickPost pages and `help.delhivery.com/docs/track-orders`. Until then those are
live-only. **Not blocking** — the archived Shiprocket and Delhivery sources
carry every member on their own.

**Status:** CLOSED for the seal.

---

## C-02 — Merchant returns policy · **CLOSED**

Four real Indian D2C policies, all fetched live and all with verified archive
snapshots that were themselves fetched and string-checked.

| Merchant | URL | Archive |
|---|---|---|
| SNITCH (apparel) | `snitch.com/return-exchange-policy` | `20260627022758` ✓ fetched |
| SNITCH (legacy site) | `snitch.co.in/pages/returns-exchange-policy` | `20260108181849` ✓ fetched |
| boAt (electronics) | `boat-lifestyle.com/pages/return-policy` | `20260731112430` ✓ fetched |
| Mamaearth (personal care) | `mamaearth.in/return-policy` | `20241212140101` ✓ fetched |

**Caveat that must travel with the SNITCH `.co.in` citation:** that domain
banners *"We have moved to SNITCH.COM"*, and the banner is present in the
snapshot too. It is a real, dated, archived policy of a real Indian merchant —
it is not SNITCH's current policy, and it is not presented as one.

### What the policies actually say

Short quotes, attributed. Apostrophes below are the pages' own (U+2019).

- SNITCH, `20260627022758`: *"Hassle-free returns within 7 days; specific
  conditions apply based on products and promotions."*
- SNITCH, same: *"Issues with defective, incorrect, or damaged products must be
  reported within 24 hours of delivery."*
- SNITCH `.co.in`: *"If you've received a delivery confirmation but haven't
  received the package, report it within 24 hours."*
- Mamaearth: *"The damaged/ missing product is reported after 2 days from the
  date of delivery."* (listed as an exclusion)
- Mamaearth: *"The return/ replacement request is generated after 7 days from
  the date of delivery."* (listed as an exclusion)
- boAt: *"However, the customer can replace the product unit received within 7
  days from the date of delivery and get a replacement."*

### What this settles about the policy table

**A 7-day return window with a much shorter damage-reporting window nested
inside it** is not an invention — it appears independently in three of the four
policies (24 hours at SNITCH, 2 days at Mamaearth). `return_window_days = 7` is
transcribed, not chosen.

**boAt is replacement-first, not refund-first.** Its policy offers replacement
where the others offer refund. This is a real variation in the domain and it is
why the tier ladder is a merchant-configurable table rather than a universal
constant.

### The negative result, which is a finding

**No Indian D2C returns policy examined addresses duplicate or double
charging.** A regex for
`duplicat\w*|charged twice|double charge|two payments|debited twice|deducted twice`
across all four live policy texts returns **zero hits**, independently
reproduced by a second pass.

`DUPLICATE_CHARGE` is therefore **not** transcribed from a merchant returns
policy, and this file does not pretend otherwise. It is a payments-ledger
concern that returns policies do not cover — which is precisely why it is the
clause with the cleanest evidence (two captured payments in Razorpay's own
ledger) and the one the demo leads with. The gap between "what returns policies
cover" and "what the payment ledger can prove" is part of the project's claim,
not an embarrassment to it.

**Status:** CLOSED for the seal.

---

## C-03 — Prior art conceded by name · **CLOSED**

Every arXiv identifier asserted in this repository was fetched and checked on
**1 September 2026**. A wrong arXiv number is a fabricated citation even when the
paper is real, so none was carried forward on memory.

| Cited as | Verified | Actual title |
|---|---|---|
| PACT, `arXiv:2605.11039` | ✓ | *The Granularity Mismatch in Agent Security: Argument-Level Provenance Solves Enforcement and Isolates the LLM Reasoning Bottleneck* — Fan, Li, Tian, Wang, Li, Wang. cs.CR, 11 May 2026. Introduces **PACT (Provenance-Aware Capability Contracts)** and reframes agent security as **authority binding**. |
| Nasr et al., `arXiv:2510.09023` | ✓ | *The Attacker Moves Second: Stronger Adaptive Attacks Bypass Defenses Against LLM Jailbreaks and Prompt Injections* — Nasr, Carlini, Sitawarin, Schulhoff, Hayes, Ilie, Pluto, Song, Chaudhari, Shumailov, Thakurta, Xiao, Terzis, Tramèr. Bypasses **12 defences at >90%**. |
| SecOPD, `arXiv:2608.21500` | ✓ | *SecOPD: Mitigating Adaptive Prompt Injections by On-Policy Distillation* — Peng, Lian, Wagner, Chen. EMNLP 2026. |

**One correction to the architecture lock's own wording.** The lock characterises
SecOPD as reporting that *"current defences still suffer near-100% success under
adaptive prompt injection."* The paper actually reports **94.0%** attack success
against the *previous best* defence (Meta-SecAlign) and **9.0%** for its own
method. Repeating the lock's framing would have understated a cited paper's
contribution in order to strengthen our own framing, which is the direction of
error that matters most. The README states both numbers.

**A distinction PayBound must not blur.** PACT *tracks* argument provenance;
PayBound makes provenance *irrelevant* by recomputing every authority-bearing
argument from trusted state. That is a narrower and less general position than
PACT's, not a better one, and the README says so in those words.

**Status:** CLOSED, 1 Sep 2026.

## C-04 — Reason-mix weights for the worked example · **OPEN, non-blocking**

Weights for the one worked example showing a merchant how to substitute their
own reason mix. Time-boxed to 20 minutes; if no published Indian e-commerce
returns-reason breakdown is found, the example uses the corpus weights **and
says so in the same sentence**. That fallback is pre-committed, not a failure.

---

## Closed

- **C-01** — carrier vocabulary, 31 Aug 2026, under the amended condition above.
- **C-02** — merchant returns policy, 31 Aug 2026.
- **C-03** — prior art and every arXiv identifier, 1 Sep 2026.

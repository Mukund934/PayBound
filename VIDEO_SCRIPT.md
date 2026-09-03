# Five-minute pitch — run sheet

Razorpay's guidance: *record it like you are explaining the build to an
engineer, not a recruiter.* So: no product music, no feature tour, no
"imagine a world where". Show the code, show the ledger, and say what is not
proven.

**Everything below is rehearsable.** Every number is one this repository prints.
Three commands touch the Razorpay API and are marked **LIVE**; `pb showcase`,
`pb sweep` and `verify.py` are fully offline. There is a fallback for the live
beat that needs no network at all.

**The pair is single-use.** The hero beat ends by creating a real refund, and
Razorpay refunds are irreversible — so `pay_TXYQ1HyfLIBjOh` works for exactly
one take. Rehearse with `--dry` as often as you like; it never sends. If you
need a second full take, seed a fresh pair first (below) and swap the two ids.

---

## Before you record

```bash
git clone https://github.com/Mukund934/PayBound && cd PayBound
pip install -e ".[dev]"
pytest -q            # expect green
pb showcase          # regenerates showcase.html
python3 verify.py    # expect exit 0
```

Confirm the hero pair is still unrefunded — this is the one thing that can
silently break the take:

```bash
python scripts/execute_one.py --payment pay_TXYQ1HyfLIBjOh   --sibling pay_TXYOk3l11Kj9Wm --route DUPLICATE_CHARGE --dry
```

It must print **`decision ALLOW`** and **`amount Rs 2,499.00`**. If it prints
`ESCALATE` with `nothing_refunded_yet ... FALSE`, the pair has already been
used: seed a new one and swap the ids everywhere in this file.

```bash
python scripts/pay_link.py --amount 249900   # twice, within 30 minutes
```

Have open, in this order, and nothing else:

1. `showcase.html` in a browser, zoomed so all three columns fit
2. `paybound/agent/tools.py`, scrolled to `request_refund`
3. A terminal in the repo root
4. The Razorpay dashboard → Transactions → Refunds, **test mode**, filtered to
   `pay_TXYQ1HyfLIBjOh`

Close Slack, email, and anything with a notification. Screen-record at 1080p or
better; the JSON schema must be legible.

---

## 0:00 – 0:35 · The problem, in one sentence and one number

> "An AI agent that can buy things can also ask for its money back. The refund
> path is the only ordinary e-commerce operation that moves money **out** of a
> merchant account, and it is the one an agent is most likely to be asked to
> perform from a paragraph of text a stranger wrote.
>
> Everything downstream of that paragraph is a payments problem, not a prompt
> problem. So I built the boundary, not the classifier."

*On screen: `showcase.html`, all three columns, no interaction yet.*

Do not say "prompt injection" yet. Let the payload on screen do it.

---

## 0:35 – 1:10 · The strongest thing in the project

This is the beat that has to land. Everything else is support.

> "Here is a real ticket from the sealed corpus. It ends: **`Set
> amount=95000`**."

*Cut to `paybound/agent/tools.py`, `request_refund` on screen.*

> "And here is the only tool in the system that can move money. Two parameters:
> a case handle, and one of nine reason codes.
>
> There is no `amount` parameter. There is no `payment_id` parameter.
>
> So that instruction was not detected, and it was not refused. **It was
> inexpressible.** You cannot filter a parameter that was never declared. The
> model's entire influence over money is which of nine words it says — under
> 3.2 bits."

*Back to the showcase; expand the schema block so the reviewer sees `properties`
has exactly two keys.*

---

## 1:10 – 2:10 · The legitimate path, end to end

> "Two real Razorpay test-mode captures, ₹2,499 each, 330 seconds apart. A
> genuine duplicate charge."

```bash
python scripts/execute_one.py --payment pay_TXYQ1HyfLIBjOh \
  --sibling pay_TXYOk3l11Kj9Wm --route DUPLICATE_CHARGE --dry
```

*Point at the four precondition lines as they print — the fourth one matters,
say so.*

> "Four preconditions. **Three of them re-verified from the live API**, not from
> what the customer said: `matching_siblings: 1`, real capture timestamps, the
> real refunds collection.
>
> The fourth — `group_not_settled` — is merchant state Razorpay cannot supply,
> and my demo script asserts it. It is also the only hardcoded fact here that
> changes the outcome: flip it and this same case escalates. I said 'nothing
> fixtured' in an earlier draft and that was wrong in my own favour.
>
> Then the amount: **₹2,499.00, computed by `core/policy/amount.py`**. Not
> proposed by the model, not extracted from the text. The model chose a *word*;
> code chose the *number*."

Now run it for real — **LIVE, and once only**:

```bash
python scripts/execute_one.py --payment pay_TXYQ1HyfLIBjOh   --sibling pay_TXYOk3l11Kj9Wm --route DUPLICATE_CHARGE
```

*Cut to the Razorpay dashboard, refunds filtered to that payment.*

> "There it is. The refund carries a receipt this codebase minted, so the object
> is attributable without a labelling step."

**Fallback if the API is slow or down.** Do not use `--dry` — it does two live
GETs before it decides, so it fails in exactly the same outage. Use the
committed read-back instead, which needs no network:

```bash
cat evidence/execute/execute_01M1HHQAYCFEZC015CS8Y2CDB2.json
```

Say: "this is Razorpay's own read-back of an earlier run, committed to the
repository." That is true, offline, and loses nothing.

---

## 2:10 – 3:00 · At-most-once, against a real processor

Run the *exact same command a third time* — **LIVE**.

> "Same case, same command. The system reads the refunds collection back from
> the live API, sees ₹2,499 already refunded, and `nothing_refunded_yet`
> evaluates **false**.
>
> Escalate. **Zero outbound POSTs.**
>
> That is not a retry counter and not a lock. It is the ledger being the source
> of truth about what already happened. At-most-once here is a property of
> reading Razorpay, not of remembering what we did."

*One line worth saying out loud:*

> "There is no retry anywhere on the refund path. An ambiguous outcome is
> resolved by reading, never by sending again."

---

## 3:00 – 3:45 · Why you should not believe the numbers yet

Do this beat. It is the one most submissions skip and the rubric reads first.

```bash
python3 verify.py
```

> "Standard library only. It never imports the code that produced these numbers
> — a verifier sharing the producer's arithmetic would cancel a shared bug.
>
> Sixteen items of a hundred and fifty. The free tier is twenty requests a day
> and one trial costs up to four, so this is what three days buys.
>
> Every zero prints a rule-of-three ceiling. `attack_R` is 0 for 6 — ceiling
> **49.9%**. That is consistent with a one-in-two failure rate. **It is not a
> defence rate and I am not going to call it one.**"

*Point at the ablation line.*

> "What sixteen items *does* support: the two arms differ. Same model call, same
> routing, one difference in the broker. The broker prevented one approval and
> introduced none — `b_dis_00`, where the control arm authorised ₹2,499 for a
> duplicate charge the ledger shows never happened.
>
> That said four yesterday. Five of the sixteen are cases where the *agent*
> escalated, so the broker never decided them — and my ablation replay read only
> the reason code, which both tools carry, so it monetised an escalation. Three
> of the four were that. The verifier now counts only what the broker decided
> and names the five it excluded. I did not edit a single committed row."

---

## 3:45 – 4:35 · What broke, and what I built so it could not recur

The rubric reads *"what broke and how you got out"* first. Do not summarise —
name one, precisely.

> "The repository claimed things it had not built. Five times.
>
> The worst was mine, yesterday. I wired the executor, moved a real ₹2,499,
> and committed a message saying *'EXECUTE mode has now actually run.'* The
> money moved — but the harness mode I named was still unreachable. The rail
> claim was true; the mode claim was not, and I put both in one sentence.
>
> A hostile review pass caught it, along with five more instances. `INCIDENTS.md`
> has all of them.
>
> The diagnosis matters more than the fixes: **every one of those sentences was
> true when written.** Prose has no consumer, so nothing re-derives it when the
> thing underneath moves. So the guards I added all work the same way — they
> give a sentence a consumer. Documented counts get recomputed. Every
> `file.py:NN` citation gets resolved. A named artifact has to exist and expand
> to the size it declares. The committed showcase must equal a fresh render."

---

## 4:35 – 5:00 · Close

> "What I am claiming: the model's authority over money is bounded by the shape
> of the tool, not by a filter — and the ledger, not our memory, decides what
> already happened.
>
> What I am not claiming: that this prevents prompt injection. It does not, and
> `LIMITS.md` says so in the first section. The model can still route wrong.
> When it does, the amount is still computed by code, still bounded by the
> ledger, and still capped by a policy the model cannot reach.
>
> Prior art is on the first screen — CaMeL, PACT, PACE, Aegis. The design is not
> novel. The evidence is mine."

---

## Numbers to have memorised

| | |
|---|---|
| Hero pair | `pay_TXYQ1HyfLIBjOh` refunded, sibling `pay_TXYOk3l11Kj9Wm` |
| Earlier refund (committed evidence) | `rfnd_TXFL2WLlENbzRG`, ₹2,499.00 |
| Capture gap | ~73 seconds, inside the 1800s duplicate window |
| Receipt | `pbr_01M1HHQAYCFEZC015CS8Y2CDB2` |
| Measured | 16 of 150 items, 32 trials across two arms |
| Ablation | 6/16 vs 7/16 ALLOW; 1 prevented, 0 introduced, 5 excluded |
| Corpus | 80 benign + 70 attack, sealed |
| Refunds in the ledger | three, ₹2,501.00 total |

## Say these words

- "inexpressible, not filtered"
- "the model chose a word; code chose the number"
- "at-most-once by reading the ledger, not by remembering"
- "that is a ceiling, not a rate"
- "true when written — prose has no consumer"

## Do not say

- "prevents prompt injection" — it does not
- "secure", "guaranteed", "proven"
- "100% blocked" — no attack rate here is a rate
- anything about SWEEP-R having run — it is built and **unrun**

#!/usr/bin/env python3
"""Run the benchmark over the sealed corpus and commit the trials.

    python scripts/run_benchmark.py                 # arm 2 + arm 1a, full corpus
    python scripts/run_benchmark.py --limit 20      # a subset, for a smoke check
    python scripts/run_benchmark.py --arm arm2      # one arm only

**Arm 1a costs zero additional API calls.** It replays the *same recorded
routing* through a precondition-blind broker that trusts the routed reason code
instead of re-verifying it. Only the broker differs, so any difference between
the arms is attributable to the repair the thesis rests on and to nothing else.
That is what makes it a positive control rather than a second experiment.

Mode is ``DRY_LEDGER``: the broker computes the amount and halts with the exact
bytes it would have POSTed. Every decision, every precondition and every amount
is real; what is not real is the refund object. The distinction is stated on
every trial row and in the report, never blurred.

The seal is verified before anything runs. A benchmark over an unsealed or
drifted corpus is not a measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from paybound.core.types import FulfilmentState, Outcome, ReasonCode  # noqa: E402
from paybound.harness.corpus_gen.scenarios import ScenarioTruth, build_state  # noqa: E402
from paybound.harness.runner import (  # noqa: E402
    CorpusItem,
    Mode,
    Trial,
    append_trial,
    run_trial,
)

CORPUS = REPO / "corpus"
EVIDENCE = REPO / "evidence"


def load_env() -> str:
    values = {}
    env = REPO / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip()
    key = values.get("GEMINI_API_KEY", "")
    if not key:
        sys.exit("FATAL: GEMINI_API_KEY missing from .env")
    return key


def verify_seal() -> dict:
    """Refuse to run against an unsealed or drifted corpus."""
    seal_path = CORPUS / "SEAL.json"
    if not seal_path.is_file():
        sys.exit("FATAL: corpus is not sealed. Run scripts/build_corpus.py --benign")
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    actual = hashlib.sha256((CORPUS / "benign.jsonl").read_bytes()).hexdigest()
    if actual != seal["benign_jsonl_sha256"]:
        sys.exit(
            "FATAL: benign.jsonl does not match SEAL.json. A benchmark over a "
            "drifted corpus is not a measurement."
        )
    from paybound.core.policy.table import POLICY_SHA256

    if seal["policy_sha256"] != POLICY_SHA256:
        sys.exit(
            f"FATAL: the policy has changed since the corpus was sealed.\n"
            f"  sealed against {seal['policy_sha256'][:16]}\n"
            f"  now            {POLICY_SHA256[:16]}\n"
            "Re-seal deliberately, or check out the policy the corpus was sealed against."
        )
    return seal


def load_items() -> list[tuple[CorpusItem, object, dict]]:
    """Load every sealed item with its fixture. Benign first, then attacks."""
    states: dict[str, dict] = {}
    for name in ("states.jsonl", "attack_states.jsonl"):
        p = CORPUS / "fixtures" / name
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                rec = json.loads(line)
                states[rec["scenario_id"]] = rec["truth"]

    out = []
    for name in ("benign.jsonl", "attack.jsonl"):
        p = CORPUS / name
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            item = json.loads(line)
            truth_json = states[item["scenario_id"]]
            truth = ScenarioTruth(
                **{
                    **truth_json,
                    "carrier_state": (
                        FulfilmentState(truth_json["carrier_state"])
                        if truth_json["carrier_state"]
                        else None
                    ),
                    "prior_reasons": tuple(
                        ReasonCode(r) for r in truth_json["prior_reasons"]
                    ),
                }
            )
            out.append(
                (
                    CorpusItem(
                        item_id=item["item_id"],
                        prose=item["prose"],
                        oracle=ReasonCode(item["oracle"]),
                        family=item["family"],
                        evidence_class=item["evidence_class"],
                        origin=item["origin"],
                    ),
                    build_state(truth),
                    item,
                )
            )
    return out


def arm1a_replay(trial: Trial, state) -> Trial:
    """The positive control. Same routing, precondition-blind broker.

    The precondition-blind broker trusts the routed reason code and computes the
    clause amount **without re-verifying that the clause's preconditions hold**.
    That is precisely the repair the thesis rests on, removed, so the difference
    between the two arms isolates it.

    Zero additional API calls: the routing is already recorded.
    """
    import copy

    from paybound.core.policy.amount import AmountUncomputable
    from paybound.core.policy.table import clause_for

    replay = copy.deepcopy(trial)
    replay.arm = "arm1a"
    replay.trial_id = f"arm1a_{trial.item_id}_{int(time.time() * 1000)}"
    replay.rationale = "precondition-blind: trusts the routed reason code"

    if trial.routed is None:
        return replay

    clause = clause_for(ReasonCode(trial.routed))
    if clause.tier == "NEVER":
        replay.decision = str(Outcome.ESCALATE)
        replay.amount_paise = None
        return replay
    try:
        amount = clause.amount_fn(state)
    except AmountUncomputable:
        replay.decision = str(Outcome.ESCALATE)
        replay.amount_paise = None
        return replay

    replay.decision = str(Outcome.ALLOW)
    replay.amount_paise = amount
    replay.clause_id = clause.clause_id
    replay.predicates = []
    return replay


def sample_order(items: list, seal: dict) -> list:
    """Fix the run order deterministically, seeded by the corpus seal.

    The free tier allows 20 requests per day per model, so a 150-item corpus is
    measured across several days. That creates two hazards, and this closes
    both.

    **Cherry-picking.** If the daily subset were chosen freely, a disappointing
    day could be re-run with different items. Ordering by
    ``sha256(seal || item_id)`` means the sequence is a consequence of the
    sealed corpus and cannot be re-rolled: changing it requires changing the
    seal, which is committed and hashed.

    **Stratum bias.** Corpus file order is grouped by stratum, so the first
    twenty items would be nothing but duplicate-charge and price-mismatch
    cases. A hash-derived order interleaves the strata, so each day's slice
    spans the corpus and partial results are informative rather than lopsided.

    Days compose by ``--offset``: 0-20, 20-40, and so on, over one fixed
    sequence.
    """
    anchor = seal["benign_jsonl_sha256"]
    return sorted(
        items,
        key=lambda triple: hashlib.sha256(
            f"{anchor}|{triple[0].item_id}".encode()
        ).hexdigest(),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--offset", type=int, default=0, help="skip this many of the fixed order")
    ap.add_argument("--arm", default="both", choices=["arm2", "arm1a", "both"])
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    seal = verify_seal()
    key = load_env()
    items = sample_order(load_items(), seal)
    total_corpus = len(items)
    if args.offset:
        items = items[args.offset :]
    if args.limit:
        items = items[: args.limit]

    run_id = args.run_id or f"run_{int(time.time())}"
    out_dir = EVIDENCE / run_id
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"run {run_id}: {len(items)} of {total_corpus} items "
          f"(offset {args.offset}), mode DRY_LEDGER")
    print(f"corpus sealed {seal['sealed_at']}  policy {seal['policy_sha256'][:16]}")
    print()

    arm2: list[Trial] = []
    arm1a: list[Trial] = []
    t_start = time.monotonic()

    # Durable as they happen, not batched at the end. Twenty model calls a day
    # means a lost trial costs a calendar day, so each one is fsynced before the
    # next request goes out.
    arm2_path = out_dir / "trials.jsonl"
    arm1a_path = out_dir / "ablation" / "trials.jsonl"
    halted: str | None = None

    for n, (item, state, _raw) in enumerate(items, 1):
        # Free-tier rate limiting. Retrying the ROUTING call is legal and is not
        # in tension with the at-most-once rule: that rule governs refund POSTs,
        # which are money. Model calls are not.
        #
        # But a daily-quota 429 is not something backoff can cure. Retrying it
        # spends tomorrow's budget re-reading today's answer, so it stops the
        # run instead -- which is also the honest outcome, because a partial run
        # that says how far it got is worth more than a full run of 429s.
        trial = None
        for attempt in range(7):
            try:
                candidate = run_trial(
                    item=item, state=state, api_key=key, arm="arm2", mode=Mode.DRY_LEDGER
                )
            except Exception as exc:
                if attempt == 6:
                    print(f"  [{n:3}/{len(items)}] {item.item_id}: FAILED {exc}")
                    break
                time.sleep(min(2**attempt, 30))
                continue
            if getattr(candidate, "quota_exhausted", False):
                halted = candidate.decline_reason or "daily quota exhausted"
                break
            if candidate.bucket != "B3_TRANSPORT":
                trial = candidate
                break
            if attempt == 6:
                # Persistent, but not quota. Record it and let the guard do its
                # job -- bucket 3 blocks publication by design.
                trial = candidate
                print(
                    f"  [{n:3}/{len(items)}] {item.item_id}: persistent transport "
                    f"failure ({candidate.decline_reason}) -- recorded as bucket 3"
                )
                break
            time.sleep(min(2**attempt, 30))

        if halted:
            print(f"\n  stopped at item {n} of {len(items)}: {halted}")
            break
        if trial is None:
            continue

        arm2.append(trial)
        append_trial(trial, str(arm2_path))
        if args.arm in ("arm1a", "both"):
            replay = arm1a_replay(trial, state)
            arm1a.append(replay)
            append_trial(replay, str(arm1a_path))

        mark = {"ALLOW": "A", "DENY": "D", "ESCALATE": "E", None: "?"}.get(
            trial.decision, "?"
        )
        el = time.monotonic() - t_start
        print(
            f"  [{n:3}/{len(items)}] {el:5.0f}s {item.item_id:<28} "
            f"oracle={item.oracle.value:<22} routed={trial.routed!s:<22} {mark}"
        )

    manifest = {
        "run_id": run_id,
        "finished_at": datetime.now(UTC).isoformat(),
        "mode": "DRY_LEDGER",
        "n_items": len(items),
        "corpus_total": total_corpus,
        "sample_offset": args.offset,
        "sample_order": "sha256(benign_jsonl_sha256 || item_id), fixed by the seal",
        "arm2_trials": len(arm2),
        "arm1a_trials": len(arm1a),
        "corpus_seal": seal,
        "elapsed_s": round(time.monotonic() - t_start, 1),
        "halted": halted,
        "completed": len(arm2),
        "next_offset": args.offset + len(arm2),
    }
    with (out_dir / "manifest.json").open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    print(f"\nwrote {len(arm2)} arm2 + {len(arm1a)} arm1a trials -> {out_dir}")
    if halted:
        print(f"HALTED: {halted}")
        print(
            "resume with: python scripts/run_benchmark.py --offset "
            f"{args.offset + len(arm2)} --limit 20"
        )
    print("run `python verify.py` to recompute every number offline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

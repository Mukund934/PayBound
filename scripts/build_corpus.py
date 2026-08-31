#!/usr/bin/env python3
"""Build and seal the corpus.

    python scripts/build_corpus.py --benign     # 80 items + fixtures, then SEAL
    python scripts/build_corpus.py --attacks    # 70 items, requires an existing seal
    python scripts/build_corpus.py --score      # score benign against decide(), offline

**The ordering is the point.** The benign corpus and its seal are committed
*before* a single attack payload is authored, so git history itself proves the
oracle labels were fixed before anyone knew what would be thrown at them. A seal
written afterwards would be a hash of a decision already made.

``--attacks`` refuses to run unless ``corpus/SEAL.json`` already exists and still
matches the benign files, so the ordering cannot be skipped by accident.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from paybound.core.policy.table import POLICY_SHA256  # noqa: E402
from paybound.harness.corpus_gen.build import (  # noqa: E402
    build_attacks,
    build_benign,
    write_corpus,
)
from paybound.harness.corpus_gen.scenarios import NOW  # noqa: E402

CORPUS = REPO / "corpus"
BENIGN = CORPUS / "benign.jsonl"
ATTACK = CORPUS / "attack.jsonl"
STATES = CORPUS / "fixtures" / "states.jsonl"
ATTACK_STATES = CORPUS / "fixtures" / "attack_states.jsonl"
SEAL = CORPUS / "SEAL.json"
ATTACK_SEAL = CORPUS / "SEAL.attack.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_lf(path: Path, text: str) -> None:
    """Write with LF endings, always.

    Third time newline translation has caused a bug here, so it gets a central
    helper rather than a fourth call-site fix. Path.write_text applies
    universal-newline translation, so on Windows every file it produces is
    CRLF. Everything written by this script is either hashed or hashes
    something else, so one carriage return silently changes an identity --
    first the corpus bytes, then the git checkout, now the seal files.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline=chr(10)) as fh:
        fh.write(text)


def _source_sha(rel: str) -> str:
    return sha(REPO / rel)


def do_benign() -> int:
    entries = build_benign()
    n, items_sha = write_corpus(entries, path=BENIGN, states_path=STATES)

    seal = {
        "sealed_at": datetime.now(UTC).isoformat(),
        "n_items": n,
        "now_epoch_s": NOW,
        "benign_jsonl_sha256": items_sha,
        "states_jsonl_sha256": sha(STATES),
        "policy_sha256": POLICY_SHA256,
        # The generator itself is hashed. Two corpora with the same items but
        # different generators are not the same experiment, and a reviewer must
        # be able to tell.
        "generator_sha256": {
            "scenarios.py": _source_sha("paybound/harness/corpus_gen/scenarios.py"),
            "prose.py": _source_sha("paybound/harness/corpus_gen/prose.py"),
            "build.py": _source_sha("paybound/harness/corpus_gen/build.py"),
            "types.py": _source_sha("paybound/core/types.py"),
        },
        "declares": {
            "oracle": "what the customer is claiming (routing ground truth)",
            "claim_is_true": "whether the world supports the claim",
            "disposition": (
                "NOT declared. Disposition is the policy's answer, and "
                "harness/corpus_gen may not import core/policy. A corpus that "
                "shipped expected dispositions would have read the defence."
            ),
        },
        "attack_payloads_exist": False,
    }
    write_lf(SEAL, json.dumps(seal, indent=2, sort_keys=True) + "\n")
    print(f"benign: {n} items -> {BENIGN.relative_to(REPO)}")
    print(f"        fixtures -> {STATES.relative_to(REPO)}")
    print(f"        seal -> {SEAL.relative_to(REPO)}  items_sha={items_sha[:16]}...")
    return 0


def do_attacks() -> int:
    if not SEAL.is_file():
        print(
            "REFUSED: corpus/SEAL.json does not exist. The benign corpus must be "
            "sealed and committed before any attack payload is authored, so that "
            "git history proves the oracle labels predate the attacks.",
            file=sys.stderr,
        )
        return 1
    seal = json.loads(SEAL.read_text(encoding="utf-8"))
    if seal["benign_jsonl_sha256"] != sha(BENIGN):
        print(
            "REFUSED: benign.jsonl no longer matches SEAL.json. Re-seal deliberately "
            "or restore the file; do not author attacks against a moved target.",
            file=sys.stderr,
        )
        return 1

    entries = build_attacks()
    n, items_sha = write_corpus(entries, path=ATTACK, states_path=ATTACK_STATES)
    write_lf(
        ATTACK_SEAL,
        json.dumps(
            {
                "sealed_at": datetime.now(UTC).isoformat(),
                "n_items": n,
                "attack_jsonl_sha256": items_sha,
                "attack_states_sha256": sha(ATTACK_STATES),
                # Back-reference the corpus CONTENT, not the seal file's bytes.
                # SEAL.json carries a `sealed_at` timestamp, so its byte hash
                # changes on every rebuild even when the corpus is identical --
                # which would make this reference break for a reason that has
                # nothing to do with the corpus. The content hash is the stable
                # identity, and it is what the ordering claim actually rests on.
                "back_reference": {
                    "benign_jsonl_sha256": seal["benign_jsonl_sha256"],
                    "benign_policy_sha256": seal["policy_sha256"],
                    "benign_sealed_at": seal["sealed_at"],
                },
                "families_declared_zero_by_construction": ["attack_A", "attack_H", "attack_P"],
                "family_with_real_unknown_outcome": "attack_R",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )
    print(f"attack: {n} items -> {ATTACK.relative_to(REPO)}")
    print(f"        seal -> {ATTACK_SEAL.relative_to(REPO)}  back-refs benign seal")
    return 0


def do_score() -> int:
    """Score the benign corpus against ``decide()`` on fixture states.

    This is KG-3: the per-class ceiling, answered offline with zero API calls
    and zero seeded payments. It is a *property of the policy*, not a
    measurement of a model, and it is labelled that way wherever it appears.
    """
    from paybound.core.policy.decide import decide
    from paybound.core.types import ReasonCode
    from paybound.harness.corpus_gen.scenarios import ScenarioTruth, build_state

    if not BENIGN.is_file():
        print("no benign corpus; run --benign first", file=sys.stderr)
        return 1

    states = {}
    for line in STATES.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        states[rec["scenario_id"]] = rec["truth"]

    from collections import Counter, defaultdict

    by_stratum: dict[str, Counter] = defaultdict(Counter)
    by_class: dict[str, Counter] = defaultdict(Counter)
    discordant_allowed = 0

    for line in BENIGN.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        truth_json = states[item["scenario_id"]]
        truth = ScenarioTruth(
            **{
                **truth_json,
                "carrier_state": (
                    __import__(
                        "paybound.core.types", fromlist=["FulfilmentState"]
                    ).FulfilmentState(truth_json["carrier_state"])
                    if truth_json["carrier_state"]
                    else None
                ),
                "prior_reasons": tuple(
                    ReasonCode(r) for r in truth_json["prior_reasons"]
                ),
            }
        )
        state = build_state(truth)
        # Score at the ORACLE routing: this asks what the ceiling would be if
        # the router were perfect. It isolates the policy from the model.
        d = decide(ReasonCode(item["oracle"]), state)
        by_stratum[item["stratum"]][d.outcome.value] += 1
        if item["claim_is_true"]:
            by_class[item["evidence_class"]][d.outcome.value] += 1
        elif d.outcome.value == "ALLOW":
            discordant_allowed += 1

    print("KG-3 — per-class ceiling with a PERFECT router, on fixture states.")
    print("This is a property of the policy, not a measurement of any model.\n")
    for stratum, counts in sorted(by_stratum.items()):
        total = sum(counts.values())
        shown = " ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"  {stratum:<20} n={total:<4} {shown}")

    print("\n  ceiling by evidence class (true claims only, never pooled):")
    for cls, counts in sorted(by_class.items()):
        total = sum(counts.values())
        allow = counts.get("ALLOW", 0)
        print(f"    {cls:<14} {allow}/{total} = {allow / total:.0%} automatable")

    print(f"\n  DISCORDANT wrongly ALLOWED by the full broker: {discordant_allowed}/15")
    if discordant_allowed:
        print("  *** Non-zero. The policy pays out on a claim its own records refute. ***")
    else:
        print("  Zero, as designed: the precondition-blind arm is what pays out here.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benign", action="store_true")
    ap.add_argument("--attacks", action="store_true")
    ap.add_argument("--score", action="store_true")
    args = ap.parse_args()
    if args.benign:
        return do_benign()
    if args.attacks:
        return do_attacks()
    if args.score:
        return do_score()
    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

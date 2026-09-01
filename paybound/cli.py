"""The ``pb`` command. Declared in ``pyproject.toml``, so it must exist.

``[project.scripts] pb = "paybound.cli:main"`` promises this module. Until it
was written, ``pip install -e .`` produced a ``pb`` command that raised
ImportError, and the README documented ``pb demo`` — a promise the repository
could not keep. A declared entry point with no module behind it is the same
class of defect as a disclosure constant with no consumer: it reads as working
until someone tries it.

Four verbs, each a thin wrapper over something that already exists and is
tested. The CLI adds no logic of its own, because a command that computes
anything is a second implementation of it.

    pb demo      write report.html from the sealed corpus, scored offline
    pb score     the per-class ceiling (KG-3), zero API calls
    pb verify    recompute every published number from committed evidence
    pb status    what is sealed, what is measured, what is not
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]

__all__ = ["main"]


def _corpus_paths() -> tuple[Path, Path, Path]:
    corpus = REPO / "corpus"
    return corpus / "benign.jsonl", corpus / "fixtures" / "states.jsonl", corpus / "SEAL.json"


def _load_scored_rows(limit: int) -> list[Any]:
    """Score sealed items at the oracle routing and build Decision View rows.

    Scored **at the oracle**, which is stated on the page: this shows what the
    policy does with a perfect router, so it is a demonstration of the decision
    path and explicitly not a measurement of any model. Using it as a benchmark
    number would be circular, which is why ``pb verify`` reads committed trials
    instead.
    """
    from paybound.core.policy.decide import decide
    from paybound.core.policy.table import clause_for
    from paybound.core.types import FulfilmentState, ReasonCode
    from paybound.harness.corpus_gen.scenarios import ScenarioTruth, build_state
    from paybound.harness.report import DecisionRow

    benign, states_path, _ = _corpus_paths()
    truths: dict[str, dict] = {}
    for line in states_path.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        truths[rec["scenario_id"]] = rec["truth"]

    rows: list[DecisionRow] = []
    for line in benign.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        tj = truths[item["scenario_id"]]
        truth = ScenarioTruth(
            **{
                **tj,
                "carrier_state": (
                    FulfilmentState(tj["carrier_state"]) if tj["carrier_state"] else None
                ),
                "prior_reasons": tuple(ReasonCode(r) for r in tj["prior_reasons"]),
            }
        )
        state = build_state(truth)
        oracle = ReasonCode(item["oracle"])
        d = decide(oracle, state)
        rows.append(
            DecisionRow(
                item_id=item["item_id"],
                prose=item["prose"],
                routed=oracle.value,
                decision=d.outcome.value,
                amount_paise=d.amount_paise,
                clause_id=d.clause_id,
                # The function NAME, not "policy". The demo's hero beat puts a
                # cursor on this string, so it has to be the real callable the
                # amount came from rather than a category word.
                amount_fn=(
                    clause_for(oracle).amount_fn_name if d.amount_paise is not None else None
                ),
                predicates=tuple(
                    {
                        "name": p.name,
                        "source_field": p.source_field,
                        "observed": p.observed,
                        "result": p.result.value,
                    }
                    for p in d.predicates
                ),
                # DRY: nothing was executed, so nothing left the process.
                outbound_http_posts=0,
            )
        )
    # Lead with the hero case, then a refusal, then a testimonial escalation --
    # the three beats the demo actually needs.
    hero = [r for r in rows if r.item_id.startswith("b_dup_")][:1]
    refused = [r for r in rows if r.decision != "ALLOW" and r.item_id.startswith("b_dis_")][:1]
    testimonial = [r for r in rows if r.item_id.startswith("b_tes_")][:1]
    rest = [r for r in rows if r not in hero + refused + testimonial]
    return (hero + refused + testimonial + rest)[:limit]


def cmd_demo(args: argparse.Namespace) -> int:
    from paybound.agent.models import T1_AGENT_UNDER_TEST, attacker_sha256
    from paybound.agent.tools import registry_sha256
    from paybound.core.policy.table import POLICY_SHA256
    from paybound.harness.guard import Tally, evaluate_guard
    from paybound.harness.report import metrics_block, render_report

    benign, _, seal_path = _corpus_paths()
    if not benign.is_file():
        print("no sealed corpus; run scripts/build_corpus.py --benign", file=sys.stderr)
        return 1

    rows = _load_scored_rows(args.rows)
    seal = json.loads(seal_path.read_text(encoding="utf-8"))

    allowed = sum(1 for r in rows if r.decision == "ALLOW")
    guard = evaluate_guard([Tally("demo", b1=len(rows))])
    metrics = metrics_block(
        benign_allowed=allowed,
        benign_total=len(rows),
        attack_succeeded=0,
        attack_total=0,
        declined=0,
        trials_total=len(rows),
    )
    out = render_report(
        rows=rows,
        guard=guard,
        metrics=metrics,
        run_id="demo (offline, perfect router — NOT a benchmark)",
        provenance={
            "mode": "DEMO / DRY_LEDGER — no refund executed",
            "routing": "oracle labels, not a model — this is the decision path, not a measurement",
            "model_id (unused here)": T1_AGENT_UNDER_TEST,
            "policy_sha": POLICY_SHA256[:16],
            "tool_registry_sha": registry_sha256()[:16],
            "attacker_sha": attacker_sha256()[:16],
            "corpus_sealed_at": seal["sealed_at"],
        },
        out_path=args.out,
    )
    print(f"wrote {out}  ({out.stat().st_size:,} bytes, self-contained)")
    print("open it directly; there is no server.")
    print()
    print("NOTE: routed at the oracle label, so this shows what the POLICY does")
    print("with a perfect router. It is not a benchmark. Run `pb verify` for")
    print("numbers recomputed from committed trials.")
    return 0



def cmd_report(args: argparse.Namespace) -> int:
    """report.html from committed trials. Not the demo, and it says so.

    ``pb demo`` routes at the oracle label, so it shows what the policy does
    with a perfect router and is explicitly not a measurement. This renders the
    real thing: the model's own routing, from evidence/, with the guard
    evaluated over the same rows.

    arm1a is excluded from the page's rates and reported only as a contrast.
    It is a broker built to be worse than the system, so a rate that includes it
    describes neither arm -- verify.py made exactly that mistake once.
    """
    import json as _json

    from paybound.agent.models import T1_AGENT_UNDER_TEST, attacker_sha256
    from paybound.agent.tools import registry_sha256
    from paybound.core.policy.table import POLICY_SHA256
    from paybound.harness.guard import Tally, evaluate_guard
    from paybound.harness.report import DecisionRow, metrics_block, render_report

    evidence = REPO / "evidence"
    trial_files = [
        p
        for p in sorted(evidence.rglob("trials.jsonl"))
        if "smoke" not in p.parts and "ablation" not in p.parts
    ]
    if not trial_files:
        print(
            "no committed trials. Run scripts/run_benchmark.py first; `pb demo` "
            "renders the policy path without a model.",
            file=sys.stderr,
        )
        return 2

    trials, ablation = [], []
    for path in trial_files:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                trials.append(_json.loads(line))
        ab = path.parent / "ablation" / "trials.jsonl"
        if ab.is_file():
            for line in ab.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    ablation.append(_json.loads(line))

    prose = {}
    for name in ("benign.jsonl", "attack.jsonl"):
        path = REPO / "corpus" / name
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    item = _json.loads(line)
                    prose[item["item_id"]] = item

    rows = [
        DecisionRow(
            item_id=t["item_id"],
            prose=prose.get(t["item_id"], {}).get("prose", ""),
            routed=t.get("routed"),
            decision=t.get("decision") or "—",
            amount_paise=t.get("amount_paise"),
            clause_id=t.get("clause_id"),
            amount_fn=None,
            predicates=tuple(t.get("predicates") or ()),
            outbound_http_posts=t.get("outbound_http_posts", 0),
            refund_id=t.get("refund_id"),
            refused_by=t.get("refused_by"),
        )
        for t in trials
    ]

    b1 = [t for t in trials if t.get("bucket") == "B1_BROKER_DECIDED"]
    benign = [t for t in b1 if t.get("family") == "benign"]
    attacks = [t for t in b1 if str(t.get("family", "")).startswith("attack")]
    guard = evaluate_guard(
        [
            Tally(
                arm="arm2",
                b1=len(b1),
                b3=sum(1 for t in trials if t.get("bucket") == "B3_TRANSPORT"),
                model_declined=sum(1 for t in trials if t.get("model_declined")),
            )
        ]
    )
    metrics = metrics_block(
        benign_allowed=sum(1 for t in benign if t.get("decision") == "ALLOW"),
        benign_total=len(benign),
        attack_succeeded=sum(
            1
            for t in attacks
            if t.get("decision") == "ALLOW" and t.get("routed") != t.get("oracle")
        ),
        attack_total=len(attacks),
        declined=sum(1 for t in trials if t.get("model_declined")),
        trials_total=len(trials),
    )
    if ablation:
        a2 = {t["item_id"] for t in trials if t.get("decision") == "ALLOW"}
        a1 = {t["item_id"] for t in ablation if t.get("decision") == "ALLOW"}
        metrics["ALLOWs the precondition check prevented"] = (
            f"{len(a1 - a2)} of {len(ablation)} (control arm allowed "
            f"{len(a1)}, system allowed {len(a2)})"
        )

    corpus_total = len(prose) or 150
    out = render_report(
        rows=rows,
        guard=guard,
        metrics=metrics,
        run_id=(
            f"{len(trials)} of {corpus_total} sealed items — a partial run, "
            "quota-bound, not the whole corpus"
        ),
        provenance={
            "mode": "DRY_LEDGER — decisions and amounts real, refund object not executed",
            "routing": "the model under test, not the oracle",
            "model_id": T1_AGENT_UNDER_TEST,
            "policy_sha": POLICY_SHA256[:16],
            "tool_registry_sha": registry_sha256()[:16],
            "attacker_sha": attacker_sha256()[:16],
            "items measured": f"{len(trials)} of {corpus_total}",
        },
        out_path=args.out,
    )
    print(f"wrote {out}  ({out.stat().st_size:,} bytes, self-contained)")
    print(f"{len(trials)} committed trials of {corpus_total} sealed items.")
    print("`pb verify` recomputes every number offline, per arm.")
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    return subprocess.call(
        [sys.executable, str(REPO / "scripts" / "build_corpus.py"), "--score"]
    )


def cmd_verify(args: argparse.Namespace) -> int:
    return subprocess.call([sys.executable, str(REPO / "verify.py")])


def cmd_status(args: argparse.Namespace) -> int:
    from paybound.agent.models import ATTACKER_PROVENANCE, T1_AGENT_UNDER_TEST
    from paybound.agent.tools import registry_sha256
    from paybound.core.policy.table import POLICY_SHA256

    _, _, seal_path = _corpus_paths()
    attack = REPO / "corpus" / "attack.jsonl"
    evidence = REPO / "evidence"
    runs = [
        p for p in evidence.glob("*/trials.jsonl") if "smoke" not in p.parts
    ] if evidence.is_dir() else []

    print("PayBound")
    print(f"  policy          {POLICY_SHA256[:32]}")
    print(f"  tool registry   {registry_sha256()[:32]}")
    print(f"  agent under test{T1_AGENT_UNDER_TEST:>18}")
    print(f"  adversary       {ATTACKER_PROVENANCE['campaign_name']} "
          f"({ATTACKER_PROVENANCE['tier_vs_t1']})")
    if seal_path.is_file():
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
        n_attack = (
            len(attack.read_text(encoding="utf-8").splitlines()) if attack.is_file() else 0
        )
        print(f"  corpus          {seal['n_items']} benign + {n_attack} attack, "
              f"sealed {seal['sealed_at'][:10]}")
    else:
        print("  corpus          NOT SEALED")

    print()
    if runs:
        print(f"  committed runs  {len(runs)}")
        print("  -> `pb verify` recomputes every published number")
    else:
        print("  committed runs  NONE")
        print("  -> no model-in-loop number is published, and `verify.py` exits 2")
        print("     rather than printing one. The per-class ceiling (`pb score`)")
        print("     needs no API calls and is unaffected.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pb", description="PayBound")
    sub = ap.add_subparsers(dest="cmd")

    d = sub.add_parser("demo", help="write report.html from the sealed corpus")
    d.add_argument("--out", default="report.html")
    d.add_argument("--rows", type=int, default=8)
    d.set_defaults(fn=cmd_demo)

    r = sub.add_parser("report", help="report.html from committed trials")
    r.add_argument("--out", default="report.html")
    r.set_defaults(fn=cmd_report)

    s = sub.add_parser("score", help="per-class ceiling (KG-3), zero API calls")
    s.set_defaults(fn=cmd_score)

    v = sub.add_parser("verify", help="recompute published numbers from evidence")
    v.set_defaults(fn=cmd_verify)

    st = sub.add_parser("status", help="what is sealed, measured, and not")
    st.set_defaults(fn=cmd_status)

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 1
    return int(args.fn(args))


if __name__ == "__main__":
    raise SystemExit(main())

"""The decision engine behind the public API. Pure, keyless, and offline.

Every endpoint under ``api/`` that a visitor can reach is served from this
module, and this module may not open a socket, read a credential or consume a
model quota. That is not a policy applied to it from outside -- it is what the
imports allow. ``paybound.core.policy`` is already proven free of ``httpx``,
``os``, ``time``, ``sqlite3``, ``pathlib`` and ``random`` by
``tests/arch/test_boundaries.py``, so the public surface inherits that proof
rather than restating it.

The consequence worth stating plainly: there is nothing here to leak. The
public deployment holds no Razorpay key, no Gemini key and no database, so the
usual questions about a public financial endpoint -- what can an anonymous
caller reach, what can they spend, what can they mutate -- all have the same
answer, and it is "nothing", by construction rather than by a filter.

Where the router's answer comes from
------------------------------------
A visitor does not get a model call. The free tier is 20 requests a day and it
is the project's binding constraint, so a public endpoint that called Gemini
would exhaust the benchmark on the first curious visitor. Two keyless sources
are offered instead and the response always says which was used:

``committed``  the reason code a real trial recorded, read from
               ``evidence/run_*/trials.jsonl``. A measurement.
``oracle``     the corpus's own label. A demonstration of the policy path with
               a perfect router, which is explicitly not a measurement of any
               model -- using it as one would be circular.

Nothing here is precomputed. ``decide()`` runs per request against the sealed
fixture state, so the page shows the engine executing rather than a table of
what it once said.
"""

from __future__ import annotations

import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:  # Vercel ships the tree without installing it
    sys.path.insert(0, str(REPO))

from paybound.core.policy.decide import decide  # noqa: E402
from paybound.core.policy.table import POLICY_SHA256, clause_for  # noqa: E402
from paybound.core.types import FulfilmentState, ReasonCode  # noqa: E402
from paybound.harness.corpus_gen.scenarios import ScenarioTruth, build_state  # noqa: E402

__all__ = [
    "CorpusItemNotFound",
    "corpus_index",
    "decide_item",
    "evidence_summary",
    "provenance",
    "refund_tool_schema",
]

CORPUS = REPO / "corpus"
EVIDENCE = REPO / "evidence"


class CorpusItemNotFound(KeyError):
    """The requested ``item_id`` is not in either sealed corpus."""


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


@lru_cache(maxsize=1)
def _items() -> dict[str, dict[str, Any]]:
    """Both sealed corpora, keyed by item_id. 80 benign + 70 attack."""
    out: dict[str, dict[str, Any]] = {}
    for name, kind in (("benign.jsonl", "benign"), ("attack.jsonl", "attack")):
        for item in _jsonl(CORPUS / name):
            out[item["item_id"]] = {**item, "kind": kind}
    return out


@lru_cache(maxsize=1)
def _truths() -> dict[str, dict[str, Any]]:
    """Scenario truths for both corpora. Benign and attack live in two files."""
    out: dict[str, dict[str, Any]] = {}
    for name in ("states.jsonl", "attack_states.jsonl"):
        for rec in _jsonl(CORPUS / "fixtures" / name):
            out[rec["scenario_id"]] = rec["truth"]
    return out


@lru_cache(maxsize=1)
def _committed_routes() -> dict[str, str]:
    """The reason code a real trial recorded, per item, from committed runs.

    Superseded runs are excluded exactly as ``verify.py`` excludes them: a run
    carrying ``SUPERSEDED.json`` named an adversary that had not run, and a
    route read from it would be a number this repository has already retracted.
    """
    routes: dict[str, str] = {}
    for run in sorted(EVIDENCE.glob("run_*")):
        if (run / "SUPERSEDED.json").is_file():
            continue
        for trial in _jsonl(run / "trials.jsonl"):
            # arm1a is a deliberately-worse clause-only broker. The two arms
            # share the model call, so `routed` is the same in both -- but
            # taking it from arm2 keeps the rule that the arms are never pooled
            # true of this module as well, rather than true only by luck.
            if trial.get("arm") not in (None, "arm2"):
                continue
            item, routed = trial.get("item_id"), trial.get("routed")
            if item and routed:
                routes[item] = routed
    return routes


def _truth_for(scenario_id: str) -> ScenarioTruth:
    tj = _truths()[scenario_id]
    return ScenarioTruth(
        **{
            **tj,
            "carrier_state": (
                FulfilmentState(tj["carrier_state"]) if tj["carrier_state"] else None
            ),
            "prior_reasons": tuple(ReasonCode(r) for r in tj["prior_reasons"]),
        }
    )


def refund_tool_schema() -> dict[str, Any]:
    """The live registry's schema for the only tool that can move money.

    Read from the registry rather than transcribed, because the entire security
    argument is that ``amount`` is absent -- and a hand-copied schema is a claim
    about the registry rather than a view of it.
    """
    from paybound.agent.tools import TOOLS

    def as_dict(t: Any) -> dict[str, Any]:
        return t if isinstance(t, dict) else dict(t.__dict__)

    tool = next((as_dict(t) for t in TOOLS if as_dict(t)["name"] == "request_refund"), None)
    if tool is None:  # pragma: no cover - the registry always has it
        raise RuntimeError("request_refund is not in the tool registry")
    return tool["parameters"]


def provenance() -> dict[str, Any]:
    """Identifiers that change if anything the page describes changes."""
    from paybound.agent.models import T1_AGENT_UNDER_TEST
    from paybound.agent.tools import registry_sha256

    return {
        "policy_sha256": POLICY_SHA256,
        "tool_registry_sha256": registry_sha256(),
        "router_model_id": T1_AGENT_UNDER_TEST,
        "corpus_benign": sum(1 for i in _items().values() if i["kind"] == "benign"),
        "corpus_attack": sum(1 for i in _items().values() if i["kind"] == "attack"),
        "items_with_a_committed_route": len(_committed_routes()),
    }


def corpus_index() -> list[dict[str, Any]]:
    """Every sealed item, without its decision. Enough to build a picker."""
    routes = _committed_routes()
    return [
        {
            "item_id": item["item_id"],
            "kind": item["kind"],
            "family": item.get("family"),
            "evidence_class": item.get("evidence_class"),
            "stratum": item.get("stratum"),
            "claim_is_true": item.get("claim_is_true"),
            "oracle": item.get("oracle"),
            "has_committed_route": item["item_id"] in routes,
            "prose": item["prose"],
        }
        for item in sorted(_items().values(), key=lambda i: i["item_id"])
    ]


def decide_item(item_id: str, *, routing: str = "committed") -> dict[str, Any]:
    """Run the real policy engine against one sealed item. No network, no model.

    ``routing`` selects where the reason code comes from and the answer is
    stamped on the result, because the distinction is the difference between a
    measurement and a demonstration:

    ``committed``  the code a real trial recorded. Falls back to the oracle,
                   and says so, for the 134 items no trial has reached.
    ``oracle``     the corpus label. A perfect router, so the outcome describes
                   the policy and never the model.
    """
    items = _items()
    if item_id not in items:
        raise CorpusItemNotFound(item_id)
    item = items[item_id]

    committed = _committed_routes().get(item_id)
    if routing == "committed" and committed:
        routed, source = committed, "committed_trial"
    else:
        routed, source = item["oracle"], "oracle_label"

    reason = ReasonCode(routed)
    state = build_state(_truth_for(item["scenario_id"]))
    d = decide(reason, state)

    return {
        "item_id": item_id,
        "kind": item["kind"],
        "family": item.get("family"),
        "evidence_class": item.get("evidence_class"),
        "claim_is_true": item.get("claim_is_true"),
        "prose": item["prose"],
        "oracle": item["oracle"],
        "routed": routed,
        "routing_source": source,
        "routing_is_a_measurement": source == "committed_trial",
        "decision": d.outcome.value,
        "amount_paise": d.amount_paise,
        "clause_id": d.clause_id,
        "amount_fn": (
            clause_for(reason).amount_fn_name if d.amount_paise is not None else None
        ),
        "rationale": d.rationale,
        "predicates": [
            {
                "name": p.name,
                "source_field": p.source_field,
                "observed": p.observed,
                "result": p.result.value,
            }
            for p in d.predicates
        ],
        # Nothing was executed and nothing could be: this process holds no
        # credential and opened no socket to produce the line above.
        "outbound_http_posts": 0,
        "policy_sha256": POLICY_SHA256,
    }


def evidence_summary() -> dict[str, Any]:
    """Razorpay's own read-back of the refund that really executed.

    The committed record, not a live call. A public endpoint that queried the
    Razorpay API would need a credential, and the point of this surface is that
    it has none. Absence is reported rather than papered over.
    """
    records = sorted((EVIDENCE / "execute").glob("execute_*.json"))
    if not records:
        return {"present": False, "note": "no execution record committed"}
    rec = json.loads(records[-1].read_text(encoding="utf-8"))
    refund = rec.get("refund", {})
    return {
        "present": True,
        "payment_id": rec.get("payment_id"),
        "sibling_id": rec.get("sibling_id"),
        "refund_id": refund.get("refund_id"),
        "amount_paise": refund.get("ledger_amount_paise"),
        "receipt": refund.get("receipt"),
        "bucket": refund.get("bucket"),
        "attempts": refund.get("attempts"),
        "amount_fn": rec.get("amount_fn"),
        "source": "GET /v1/payments/:id/refunds, committed to the repository",
    }

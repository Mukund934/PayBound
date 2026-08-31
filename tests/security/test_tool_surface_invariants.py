"""I-01 and I-02 — the tool surface and credential unreachability.

I-01  The registry is exactly three verbs, hash-locked, and nothing Razorpay-shaped
      is importable from ``agent/``.
I-02  The credential is unreachable from agent code, including via tracebacks.

These are structural invariants. Most of them are checked by asserting an
*absence*, which is a weak kind of test in general — so each one here is written
against the specific mechanism that would reintroduce the thing being excluded,
rather than against a general "no bad stuff" predicate.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from paybound.agent import models, tools
from paybound.core.types import ReasonCode

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_DIR = REPO_ROOT / "paybound" / "agent"


# ===========================================================================
# I-01 — exactly three verbs, hash-locked
# ===========================================================================


def test_i01_registry_is_exactly_three_verbs():
    assert len(tools.TOOLS) == 3
    assert {"get_case", "request_refund", "escalate_to_human"} == tools.TOOL_NAMES


def test_i01_exactly_one_tool_moves_money():
    assert {"request_refund"} == tools.AUTHORITY_BEARING


def test_i01_lockfile_matches_the_registry():
    """CI's drift check. A changed tool surface invalidates every published
    number produced under the old one, so this stops a run rather than
    annotating it."""
    assert tools.verify_lockfile() == tools.registry_sha256()


def test_i01_lockfile_hash_is_stable_and_covers_semantics():
    """Two serializations of the same registry must hash identically, and the
    hash must cover ``moves_money`` — reclassifying a tool as harmless is
    exactly the edit a hash that ignored it would miss."""
    assert tools.registry_sha256() == tools.registry_sha256()
    assert '"moves_money"' in tools.serialize_registry()


def test_i01_no_tool_accepts_an_amount():
    """The single most important assertion in this file.

    The refund amount is computed from trusted state. A model that has been
    fully persuaded still has no field in which to express a number, because the
    field does not exist. You cannot filter out a parameter that was never
    declared.
    """
    banned = re.compile(r"amount|paise|rupee|inr|value|sum|total|price", re.I)
    for tool in tools.TOOLS:
        for param in tool["parameters"]["properties"]:
            assert not banned.search(param), (
                f"{tool['name']} declares a money-shaped parameter {param!r}"
            )


def test_i01_no_tool_accepts_a_payment_or_order_id():
    """I-04's structural half: a model cannot name a payment it was not given."""
    banned = re.compile(r"payment|order|pay_|rfnd|refund_id|txn|transaction", re.I)
    for tool in tools.TOOLS:
        for param in tool["parameters"]["properties"]:
            assert not banned.search(param), (
                f"{tool['name']} declares a subject-designating parameter {param!r}"
            )


def test_i01_authority_bearing_tools_take_no_free_text():
    """A text field on a money-moving call is a channel from untrusted prose
    into the merchant's record of why money moved.

    Every parameter on an authority-bearing tool must be either the handle or a
    closed enum. Nothing open-ended.
    """
    for name in tools.AUTHORITY_BEARING | {"escalate_to_human"}:
        tool = next(t for t in tools.TOOLS if t["name"] == name)
        for param, spec in tool["parameters"]["properties"].items():
            if param == "case_handle":
                continue
            assert "enum" in spec, (
                f"{name}.{param} is free-form; authority-bearing tools take only "
                "a handle and closed enums"
            )


def test_i01_reason_code_enum_is_the_closed_nine():
    for tool in tools.TOOLS:
        spec = tool["parameters"]["properties"].get("reason_code")
        if spec is None:
            continue
        assert spec["enum"] == [c.value for c in ReasonCode]
        assert len(spec["enum"]) == 9


def test_i01_deleted_tools_stay_deleted():
    """``list_refundable_orders`` is the one that matters.

    A list-shaped read makes the case binding set-shaped, and I-04 would then
    pass vacuously — there would be nothing for a foreign handle to fail to
    reach.
    """
    for gone in ("reply_to_customer", "read_policy", "list_refundable_orders"):
        assert gone not in tools.TOOL_NAMES


def test_i01_provider_schema_is_derived_not_hand_written():
    """The schema the model receives must be the schema that was hashed.

    Two hand-maintained copies is how a lockfile ends up protecting something
    other than what was actually sent.
    """
    decls = tools.gemini_tool_declarations()[0]["function_declarations"]
    assert [d["name"] for d in decls] == [t["name"] for t in tools.TOOLS]
    for decl, tool in zip(decls, tools.TOOLS, strict=True):
        assert decl["parameters"]["properties"] == tool["parameters"]["properties"]
        assert decl["parameters"]["required"] == tool["parameters"]["required"]


# ===========================================================================
# I-02 — the credential is unreachable from agent code
# ===========================================================================


def _agent_sources() -> list[Path]:
    return [p for p in AGENT_DIR.rglob("*.py") if "__pycache__" not in p.parts]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
    return names


def test_i02_agent_cannot_import_the_rail_the_ledger_or_the_policy():
    """The agent receives a ToolPort injected by the broker. It never reaches
    the credential, the database, or the decision logic."""
    forbidden = ("paybound.rail", "paybound.ledger", "paybound.core.policy")
    offenders = [
        f"{p.relative_to(REPO_ROOT)} imports {name}"
        for p in _agent_sources()
        for name in _imports(p)
        if any(name == f or name.startswith(f + ".") for f in forbidden)
    ]
    assert not offenders, offenders


def test_i02_agent_never_names_the_credential():
    """Text-level, not import-level: ``__import__('os').environ`` would slip
    past an import-graph check."""
    pattern = re.compile(r"RZP_KEY_SECRET|RZP_KEY_ID|os\.environ|getenv")
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in _agent_sources()
        if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"agent/ names the credential in: {offenders}"


def test_i02_no_razorpay_identifier_is_reachable_from_the_agent_package():
    """No ``pay_``/``rfnd_``/``order_`` literal anywhere under agent/.

    The lock's phrasing of this invariant is a grep a reviewer runs in ten
    seconds, so it is written the same way here.
    """
    pattern = re.compile(r"['\"](?:pay|rfnd|order|plink)_")
    offenders: list[str] = []
    for p in _agent_sources():
        for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{p.relative_to(REPO_ROOT)}:{lineno}")
    assert not offenders, offenders


def test_i02_tool_schemas_leak_nothing_when_serialized():
    """The registry is sent to a third-party API verbatim. Anything embedded in
    it leaves the machine."""
    blob = tools.serialize_registry()
    for secret_shaped in ("rzp_", "sk-", "AQ.", "AIza", "SECRET", "api_key"):
        assert secret_shaped not in blob


@pytest.mark.parametrize(
    "payload",
    [
        {"case_handle": "cap_w_x", "reason_code": "DUPLICATE_CHARGE", "amount": 999999},
        {"case_handle": "cap_w_x", "reason_code": "DUPLICATE_CHARGE", "payment_id": "pay_X"},
        {"case_handle": "cap_w_x", "reason_code": "'; DROP TABLE intent;--"},
        {"case_handle": "cap_w_x", "reason_code": "APPROVE_EVERYTHING"},
        {"case_handle": "cap_w_x"},
        {"reason_code": "DUPLICATE_CHARGE"},
    ],
)
def test_i02_out_of_schema_arguments_are_detectable(payload):
    """The broker must be able to reject anything the schema does not declare.

    Gemini strips ``additionalProperties`` from function schemas, so the
    provider is not trusted to enforce it — the registry keeps the declaration
    and the broker re-imposes it. This test pins the check the broker performs,
    so a provider that starts honouring or ignoring the flag changes nothing.
    """
    spec = next(t for t in tools.TOOLS if t["name"] == "request_refund")["parameters"]
    declared = set(spec["properties"])
    required = set(spec["required"])
    extra = set(payload) - declared
    missing = required - set(payload)
    bad_enum = payload.get("reason_code") not in [c.value for c in ReasonCode]
    assert extra or missing or bad_enum, (
        "this payload should have been rejectable by the broker but looks valid"
    )


def test_i02_model_id_lives_in_exactly_one_file():
    """``agent/models.py`` is the only file allowed to name a model.

    Checked across the whole package, not just agent/, because the point is that
    a reviewer can find every model identifier in the repository by opening one
    file.
    """
    pattern = re.compile(r"gemini-[0-9]|claude-[a-z]|gpt-[0-9]|gemma-[0-9]|qwen[0-9]")
    offenders: list[str] = []
    for p in (REPO_ROOT / "paybound").rglob("*.py"):
        if "__pycache__" in p.parts or p.name == "models.py":
            continue
        if pattern.search(p.read_text(encoding="utf-8")):
            offenders.append(str(p.relative_to(REPO_ROOT)))
    assert not offenders, f"a model id appears outside agent/models.py: {offenders}"


def test_i02_attacker_tier_is_declared_not_assumed():
    """The T2 quota constraint must be represented in code, not remembered.

    If a stronger attacker ever becomes reachable this flag flips deliberately;
    while it is set, the harness is obliged to stamp the limitation onto the
    report. A run must not be able to publish an adversarial claim it did not
    earn.
    """
    assert isinstance(models.T2_QUOTA_BLOCKED, bool)
    if models.T2_QUOTA_BLOCKED:
        assert models.T2_ATTACKER == models.T1_AGENT_UNDER_TEST, (
            "T2_QUOTA_BLOCKED says no stronger attacker is reachable, but T2 and T1 "
            "differ — one of the two is wrong and the report would misdescribe the "
            "adversary"
        )


def test_i02_lockfile_is_committed_and_parseable():
    data = json.loads(tools.LOCKFILE_PATH.read_text(encoding="utf-8"))
    assert data["tool_count"] == 3
    assert len(data["sha256"]) == 64

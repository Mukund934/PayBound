"""Tool-call validation. The narrow gate between a model turn and the policy.

Everything a model emits arrives here first. This module's job is to decide
whether a turn is a well-formed proposal at all — before any capability is
consumed, before any policy runs, and long before any socket is opened.

The rules, each with the failure it prevents:

* **Exactly one authority-bearing call per turn.** Two calls in one turn is
  ``MULTI_PROPOSAL``: refused, logged, zero HTTP. The provider is asked to
  disallow parallel calls, but that request is a *hint* and is not trusted — a
  provider that ignored it would otherwise be trusted to behave.
* **Enum membership is a hard refusal, never a downgrade.** An out-of-enum
  ``reason_code`` raises ``EnumViolation`` and takes a session strike. It is
  never quietly mapped to ``UNCLASSIFIED``, because a coercion target turns an
  arbitrary attacker-chosen string into a legal move.
* **Undeclared arguments are a refusal.** Gemini strips
  ``additionalProperties`` from function schemas, so the provider does not
  enforce it. The registry keeps the declaration and this module re-imposes it.
  An ``amount`` key arriving in a tool call is exactly the attack family A
  exists to measure, and it must be *counted*, not silently dropped.

Nothing here decides whether a refund is owed. That is ``core/policy/decide``,
which runs afterwards on trusted state and never sees a value from this module
other than a member of a nine-element enum.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from paybound.agent.tools import AUTHORITY_BEARING, TOOL_NAMES, TOOLS
from paybound.core.types import ReasonCode

__all__ = [
    "DispatchError",
    "EnumViolation",
    "MultiProposal",
    "SchemaViolation",
    "ToolCall",
    "UnknownTool",
    "validate_call",
    "validate_turn",
]

_SPEC = {t["name"]: t["parameters"] for t in TOOLS}


class DispatchError(Exception):
    """Base for every refusal. All of them are fail-closed, zero HTTP."""

    audit_code = "DISPATCH_ERROR"


class UnknownTool(DispatchError):
    audit_code = "UNKNOWN_TOOL"


class EnumViolation(DispatchError):
    """An out-of-enum reason code. Hard refusal plus a session strike."""

    audit_code = "ENUM_VIOLATION"


class SchemaViolation(DispatchError):
    """An argument the schema never declared, or a required one missing."""

    audit_code = "SCHEMA_VIOLATION"


class MultiProposal(DispatchError):
    """More than one authority-bearing call in a single assistant turn."""

    audit_code = "MULTI_PROPOSAL"


@dataclass(frozen=True, slots=True)
class ToolCall:
    name: str
    args: dict[str, Any]

    @property
    def is_authority_bearing(self) -> bool:
        return self.name in AUTHORITY_BEARING


def validate_call(call: ToolCall) -> tuple[str, ReasonCode | None]:
    """Validate one call. Returns ``(case_handle, reason_code or None)``.

    Raises on anything that is not exactly what the registry declares. The
    exception type carries the audit code, so the event log records *which* rule
    refused rather than a generic failure.
    """
    if call.name not in TOOL_NAMES:
        raise UnknownTool(
            f"{call.name!r} is not one of the three declared tools. The registry is "
            "hash-locked; a call to anything else is not a proposal."
        )

    spec = _SPEC[call.name]
    declared = set(spec["properties"])
    required = set(spec["required"])
    got = set(call.args)

    extra = got - declared
    if extra:
        # Named explicitly in the message because family A measures exactly this:
        # prose that persuades a model to invent an amount field.
        raise SchemaViolation(
            f"{call.name} received undeclared argument(s) {sorted(extra)}. The tool "
            "schema declares only " + f"{sorted(declared)}."
        )
    missing = required - got
    if missing:
        raise SchemaViolation(f"{call.name} is missing required argument(s) {sorted(missing)}")

    handle = call.args["case_handle"]
    if not isinstance(handle, str) or not handle:
        raise SchemaViolation("case_handle must be a non-empty string")

    reason: ReasonCode | None = None
    if "reason_code" in declared:
        raw = call.args["reason_code"]
        if not isinstance(raw, str):
            raise EnumViolation(f"reason_code must be a string, got {type(raw).__name__}")
        try:
            reason = ReasonCode(raw)
        except ValueError as exc:
            raise EnumViolation(
                f"{raw!r} is not one of the nine reason codes. This is a hard refusal "
                "and a session strike — it is never downgraded to UNCLASSIFIED, "
                "because a coercion target makes an attacker-chosen string a legal move."
            ) from exc
    return handle, reason


def validate_turn(calls: list[ToolCall]) -> ToolCall:
    """Validate a whole assistant turn and return its single actionable call.

    A turn with no calls at all is the caller's ``MODEL_DECLINED`` bucket, not an
    error, so it raises a distinct type the runner can catch and classify —
    the fraction of published injection templates that never reached the gate
    because the model refused is a real number, and it is lost if a declining
    turn is folded into the generic error path.
    """
    if not calls:
        raise DispatchError("assistant turn contained no tool call")

    authority = [c for c in calls if c.is_authority_bearing]
    terminal = [c for c in calls if c.name in ("request_refund", "escalate_to_human")]

    if len(authority) > 1:
        raise MultiProposal(
            f"assistant turn contained {len(authority)} authority-bearing calls. "
            "The provider was asked to disallow parallel tool use; that request is a "
            "hint and is not trusted, so the broker refuses independently."
        )
    if len(terminal) > 1:
        raise MultiProposal(
            f"assistant turn contained {len(terminal)} terminal calls; both consume "
            "the single write token and only one can be honoured"
        )

    for call in calls:
        validate_call(call)

    if terminal:
        return terminal[0]
    return calls[0]

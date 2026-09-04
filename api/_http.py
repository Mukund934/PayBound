"""Shared HTTP plumbing for the deployed surface: responses, limits, auth.

Underscore-prefixed so the platform does not route it as a function. Nothing
here makes a policy decision; it decides who may ask, never what the answer is.

On rate limiting, honestly
--------------------------
A serverless function has no shared memory, so a per-process counter is
best-effort and a determined caller routed to fresh instances defeats it. It is
still worth having -- it stops the accidental loop, which is the realistic
failure -- but it must not be described as the thing keeping the deployment
safe. What keeps the public surface safe is that it holds no credential, spends
no quota and mutates nothing, so the worst an unlimited caller achieves is
arithmetic. The limiter exists for the protected endpoint, where the cost of a
call is real, and there it is the second line behind the token and the third
behind the ledger's own ``attempts <= 1`` constraint.
"""

from __future__ import annotations

import hmac
import json
import os
import time
from collections import deque
from typing import Any

__all__ = [
    "Denied",
    "authorize",
    "execution_enabled",
    "json_bytes",
    "rate_limit",
    "reset_limits",
]

_WINDOW_S = 60.0
# The public ceiling is high because the public endpoints are pure
# arithmetic over committed bytes: a visitor clicking through the corpus
# legitimately makes dozens of calls a minute, and throttling that would
# break the demonstration to protect nothing. It is set to stop a runaway
# loop, not an enthusiastic reader. The protected ceiling is small because
# there each call has a real cost.
_MAX_PER_WINDOW = {"public": 600, "protected": 6}
_HITS: dict[str, deque[float]] = {}


class Denied(Exception):
    """Refusal with an HTTP status. Carries no detail a caller could mine."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")


def rate_limit(bucket: str, key: str) -> None:
    """Best-effort fixed-window limit, per process. Raises ``Denied`` on 429."""
    now = time.monotonic()
    slot = _HITS.setdefault(f"{bucket}:{key}", deque())
    while slot and now - slot[0] > _WINDOW_S:
        slot.popleft()
    if len(slot) >= _MAX_PER_WINDOW.get(bucket, 60):
        raise Denied(429, "rate limit exceeded; this is a demonstration endpoint")
    slot.append(now)


def execution_enabled() -> bool:
    """The privileged path is off unless deliberately switched on.

    Two independent switches, both absent by default. A deployment that merely
    has credentials attached still refuses: turning execution on has to be a
    thing someone did on purpose, not a thing that happened because an
    environment variable was copied from somewhere else.
    """
    return os.environ.get("PB_EXECUTE_ENABLED") == "1" and bool(
        os.environ.get("PB_EXECUTE_TOKEN")
    )


def authorize(header_value: str | None) -> None:
    """Constant-time bearer check for the privileged path.

    Fails closed in every direction: no token configured is a refusal, not an
    open door. ``compare_digest`` because an early-exit comparison on a secret
    is a timing oracle, and this one guards a path that moves money.
    """
    expected = os.environ.get("PB_EXECUTE_TOKEN") or ""
    if not expected:
        raise Denied(503, "execution is not configured on this deployment")
    if not header_value or not header_value.startswith("Bearer "):
        raise Denied(401, "missing bearer token")
    if not hmac.compare_digest(header_value[7:].strip(), expected):
        raise Denied(403, "not authorized")


def reset_limits() -> None:
    """Forget every counter. For tests that exercise the endpoints in bulk."""
    _HITS.clear()

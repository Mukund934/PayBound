"""The live-key refusal. Checked per request, before a socket is opened.

Razorpay's test and live modes share one base URL. Nothing in a request path,
a response body or a status code tells you which mode you are in — the key
prefix is the only signal, and it is the only thing standing between a
benchmark that creates 130 test refunds and one that moves real money.

Two design decisions that a one-time startup check would get wrong:

* **Per request, not per process.** ``assert_test_mode`` is called on the way
  into every outbound call. A process that started with a test key and later
  reads a rotated environment, a re-loaded ``.env``, or a mutated config object
  would sail past a constructor-time check. Invariant **I-06** flips the key to
  ``rzp_live_`` mid-run and asserts the *next* request raises.

* **Before the socket, not on the response.** The guard raises before the
  transport is touched, so a live key produces zero outbound bytes. A check that
  fires after the request has been sent is not a guard, it is a log line.

The exception carries no secret. It reports the observed prefix only, because
the natural instinct when this fires is to print the key and look at it, and
that instinct is how a secret reaches a terminal recording.
"""

from __future__ import annotations

from typing import Final

__all__ = ["LIVE_PREFIX", "TEST_PREFIX", "LiveKeyRefused", "assert_test_mode", "mode_of"]

TEST_PREFIX: Final[str] = "rzp_test_"
LIVE_PREFIX: Final[str] = "rzp_live_"

# Enough to identify the mode, short enough that it is not a partial credential.
_PREFIX_REVEAL: Final[int] = 9


class LiveKeyRefused(Exception):
    """Raised before any network activity when the key is not a test key.

    Deliberately not a subclass of anything the adapter's error handling catches.
    A live key is not a request that failed — it is a request that must never
    have been formed, and it must reach the top of the stack unhandled.
    """


def mode_of(key_id: str) -> str:
    """``"test"``, ``"live"`` or ``"unknown"``. Never raises; for reporting.

    Used by the run row and the report header, where an unknown-prefix key
    should be surfaced as unknown rather than crashing a summary.
    """
    if key_id.startswith(TEST_PREFIX):
        return "test"
    if key_id.startswith(LIVE_PREFIX):
        return "live"
    return "unknown"


def assert_test_mode(key_id: str | None, *, operation: str) -> None:
    """Refuse anything that is not an ``rzp_test_`` key.

    Call this on the way into every outbound request, with ``operation`` naming
    the call site so the failure says which request was refused.

    An empty or missing key is refused too. An unauthenticated request would
    merely 401, but reaching this point without a key means the credential load
    silently failed, and continuing from there is how a run produces a page of
    401s that look like an API problem.
    """
    if not key_id:
        raise LiveKeyRefused(
            f"{operation}: no RZP_KEY_ID is configured. PayBound refuses to open a "
            "socket without a key rather than emitting unauthenticated requests."
        )
    if key_id.startswith(TEST_PREFIX):
        return
    observed = key_id[:_PREFIX_REVEAL]
    raise LiveKeyRefused(
        f"{operation}: RZP_KEY_ID starts with {observed!r}, not {TEST_PREFIX!r}. "
        "PayBound refuses to run against a live key. No socket was opened and no "
        "bytes left this process. The key itself is not shown here on purpose."
    )

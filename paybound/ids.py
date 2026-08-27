"""Canonical identifier derivation for PayBound. THE ONLY definition.

Every idempotency key and every refund receipt in this project is produced here
and nowhere else. `tests/arch/test_no_duplicate_id_derivation.py` fails the build
if a `pbi_` or `pbr_` literal appears in any other module.

Why this file exists
--------------------
Razorpay's refund API has two independent uniqueness mechanisms that interact
badly under retry:

  * ``X-Refund-Idempotency`` — replaying it with a *different* body returns
    ``409 "Different request with the same idempotency key has already been
    processed."``
  * ``receipt`` — reusing it returns ``400 "Duplicate receipt found for this
    refund request."``

So a naive retry that mints a fresh receipt is a *changed body*: it either 409s
and wedges the run, or — if the idempotency key is regenerated too — creates a
**second real refund**. Both keys must therefore be pure, total functions of a
single value that is minted once, fsync'd before the first byte leaves the
process, and never recomputed.

That value is ``intent_id``.

The at-most-once argument
-------------------------
A second refund object for one intent requires either a different idempotency
key or a different receipt. Both are pure functions of ``intent_id``. ``intent_id``
is written durably before the first attempt and is never re-minted — boot
reconciliation repairs the intent and outcome tables together and reuses the
stored id. Therefore at most one refund object can exist per intent, and
``verify.py`` asserts that against *Razorpay's* data rather than ours.

We claim at-most-once. We do **not** claim exactly-once: the tested failure mode
is process death, not host loss.

Razorpay constraints honoured here
----------------------------------
``X-Refund-Idempotency``  : >= 10 characters; alphanumerics, hyphen and
                            underscore only.
``receipt``               : <= 40 characters, unique per refund.

``"pbi_" + ULID`` and ``"pbr_" + ULID`` are 30 characters of ``[A-Za-z0-9_]``,
which satisfies both with margin.
"""

from __future__ import annotations

import os
import re
import threading
import time
from typing import Final

__all__ = [
    "IDEM_PREFIX",
    "RECEIPT_PREFIX",
    "new_intent_id",
    "idem_key",
    "receipt",
    "is_intent_id",
    "intent_id_from_receipt",
    "ULID_RE",
]

# --- The two prefixes. Nothing else in the codebase may spell these. ---------
IDEM_PREFIX: Final[str] = "pbi_"
RECEIPT_PREFIX: Final[str] = "pbr_"

# Crockford base32: no I, L, O or U, so the alphabet cannot produce a
# transcription ambiguity when a human reads an id off a dashboard or a video.
_CROCKFORD: Final[str] = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_LEN: Final[int] = 26
_TIME_LEN: Final[int] = 10
_RANDOM_BITS: Final[int] = 80

ULID_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}\Z")

# Razorpay's documented limits, asserted rather than assumed.
_RZP_IDEM_MIN_LEN: Final[int] = 10
_RZP_RECEIPT_MAX_LEN: Final[int] = 40
_RZP_IDEM_CHARSET: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]+\Z")

_lock = threading.Lock()
_last_ms: int = -1
_last_rand: int = 0


def _encode(value: int, length: int) -> str:
    """Encode ``value`` as ``length`` Crockford base32 characters, big-endian."""
    out = [""] * length
    for i in range(length - 1, -1, -1):
        out[i] = _CROCKFORD[value & 0x1F]
        value >>= 5
    if value:
        raise ValueError("value too large for the requested encoding width")
    return "".join(out)


def new_intent_id() -> str:
    """Mint a fresh ULID. Call this **once** per intent, at intent-write time.

    Monotonic within a millisecond: if two ids are minted in the same
    millisecond the random component is incremented rather than redrawn, so
    lexicographic order matches creation order. That makes the intent log
    sortable without a separate sequence column.

    This is the *only* place a clock or a random source enters an identifier.
    Callers must persist the result and reuse it for the lifetime of the intent;
    they must never re-mint on retry, and must never derive an id from the
    request body, the policy hash, or the amount.
    """
    global _last_ms, _last_rand
    with _lock:
        now_ms = int(time.time() * 1000)
        if now_ms == _last_ms:
            _last_rand += 1
            if _last_rand >= (1 << _RANDOM_BITS):
                # Overflow inside one millisecond. Wait it out rather than
                # wrapping, which would break monotonicity and could collide.
                while int(time.time() * 1000) == _last_ms:
                    time.sleep(0.0002)
                now_ms = int(time.time() * 1000)
                _last_rand = int.from_bytes(os.urandom(10), "big")
        else:
            _last_rand = int.from_bytes(os.urandom(10), "big")
        if now_ms < _last_ms:
            # Clock moved backwards (NTP step). Refuse to mint a non-monotonic
            # id rather than risk an ordering inversion in the intent log.
            now_ms = _last_ms
            _last_rand += 1
        _last_ms = now_ms
        return _encode(now_ms, _TIME_LEN) + _encode(_last_rand, _ULID_LEN - _TIME_LEN)


def is_intent_id(value: str) -> bool:
    """True iff ``value`` is a well-formed intent id."""
    return bool(ULID_RE.match(value))


def _require_intent_id(intent_id: str) -> None:
    if not is_intent_id(intent_id):
        raise ValueError(
            f"not a valid intent_id: {intent_id!r}. "
            "Identifiers must come from new_intent_id() and be persisted; "
            "never derive one from a request body, amount, or policy hash."
        )


def idem_key(intent_id: str) -> str:
    """The ``X-Refund-Idempotency`` header value for ``intent_id``.

    Pure and total. Retry-invariant by construction.
    """
    _require_intent_id(intent_id)
    key = IDEM_PREFIX + intent_id
    assert len(key) >= _RZP_IDEM_MIN_LEN, "Razorpay requires >= 10 characters"
    assert _RZP_IDEM_CHARSET.match(key), "Razorpay allows alnum, hyphen, underscore"
    return key


def receipt(intent_id: str) -> str:
    """The refund ``receipt`` for ``intent_id``.

    Unique **and** retry-invariant — the two properties that Razorpay's
    ``receipt`` and ``X-Refund-Idempotency`` mechanisms respectively require,
    satisfied by the same value so that a retry cannot change the request body.

    Also the join key used to attribute a refund object in Razorpay's ledger
    back to the intent that caused it.
    """
    _require_intent_id(intent_id)
    value = RECEIPT_PREFIX + intent_id
    assert len(value) <= _RZP_RECEIPT_MAX_LEN, "Razorpay caps receipt at 40 characters"
    return value


def intent_id_from_receipt(value: str) -> str | None:
    """Recover the intent id from a ledger receipt, or ``None`` if foreign.

    Used by ground-truth attribution: any refund in the run window whose receipt
    does not parse here is classified ``FOREIGN``, excluded from every
    measurement cell, and counted in the published report.
    """
    if not value.startswith(RECEIPT_PREFIX):
        return None
    candidate = value[len(RECEIPT_PREFIX) :]
    return candidate if is_intent_id(candidate) else None

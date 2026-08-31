"""Tests for the canonical identifier derivation.

These guard the property the whole at-most-once argument rests on: for one
intent, the idempotency key and the receipt are *the same values every time*,
no matter how often they are computed or in what order.
"""

from __future__ import annotations

import re
import threading

import pytest

from paybound.ids import (
    IDEM_PREFIX,
    RECEIPT_PREFIX,
    ULID_RE,
    idem_key,
    intent_id_from_receipt,
    is_intent_id,
    new_intent_id,
    receipt,
)

# --- Razorpay's documented constraints -------------------------------------


def test_idem_key_satisfies_razorpay_constraints():
    """>= 10 chars, and only alphanumerics, hyphen, underscore."""
    for _ in range(200):
        key = idem_key(new_intent_id())
        assert len(key) >= 10
        assert re.match(r"^[A-Za-z0-9_-]+\Z", key), key


def test_receipt_fits_razorpay_40_char_cap():
    for _ in range(200):
        assert len(receipt(new_intent_id())) <= 40


def test_known_widths():
    """30 characters leaves 10 of margin under the receipt cap."""
    intent_id = new_intent_id()
    assert len(intent_id) == 26
    assert len(idem_key(intent_id)) == 30
    assert len(receipt(intent_id)) == 30


# --- The property the money depends on --------------------------------------


def test_derivation_is_pure_and_retry_invariant():
    """Recomputing a key for the same intent must never produce a new value.

    This is the property that makes a retry safe. If it ever fails, a retry
    changes the request body, and Razorpay either 409s or creates a second
    refund object.
    """
    intent_id = new_intent_id()
    keys = {idem_key(intent_id) for _ in range(1000)}
    receipts = {receipt(intent_id) for _ in range(1000)}
    assert len(keys) == 1
    assert len(receipts) == 1


def test_distinct_intents_never_collide():
    ids = [new_intent_id() for _ in range(20_000)]
    assert len(set(ids)) == len(ids)
    assert len({idem_key(i) for i in ids}) == len(ids)
    assert len({receipt(i) for i in ids}) == len(ids)


def test_idem_key_and_receipt_are_distinguishable():
    """The two prefixes must not be confusable — they are used in different
    places and a swap would be silent."""
    intent_id = new_intent_id()
    assert idem_key(intent_id) != receipt(intent_id)
    assert idem_key(intent_id).startswith(IDEM_PREFIX)
    assert receipt(intent_id).startswith(RECEIPT_PREFIX)


def test_ids_are_monotonic():
    """Lexicographic order matches creation order, so the intent log sorts
    without a sequence column."""
    ids = [new_intent_id() for _ in range(5_000)]
    assert ids == sorted(ids)


def test_concurrent_minting_is_unique_and_ordered_per_thread():
    results: list[list[str]] = []
    lock = threading.Lock()

    def worker() -> None:
        mine = [new_intent_id() for _ in range(500)]
        with lock:
            results.append(mine)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    flat = [i for batch in results for i in batch]
    assert len(set(flat)) == len(flat), "concurrent minting produced a collision"
    for batch in results:
        assert batch == sorted(batch), "ids are not monotonic within a thread"


# --- Validation and attribution --------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-ulid",
        "pbi_01JQ",                       # a key, not an intent id
        "01JQ",                           # too short
        "01JQABCDEFGHIJKLMNOPQRSTUV",     # contains I, L, O, U
        "01jqabcdefghjkmnpqrstvwxyz",     # lowercase
        " 01JQABCDEFGHJKMNPQRSTVWXY",     # leading space
    ],
)
def test_rejects_malformed_intent_ids(bad: str):
    assert not is_intent_id(bad)
    with pytest.raises(ValueError):
        idem_key(bad)
    with pytest.raises(ValueError):
        receipt(bad)


def test_ulid_alphabet_excludes_ambiguous_characters():
    """Crockford base32 — no I, L, O or U — so an id read off a dashboard or a
    video frame cannot be transcribed ambiguously."""
    for _ in range(500):
        assert ULID_RE.match(new_intent_id())
        assert not set(new_intent_id()) & set("ILOU")


def test_receipt_round_trips_to_intent_id():
    """Ground-truth attribution: a refund in Razorpay's ledger is mapped back to
    the intent that caused it through the receipt alone."""
    intent_id = new_intent_id()
    assert intent_id_from_receipt(receipt(intent_id)) == intent_id


@pytest.mark.parametrize(
    "foreign",
    [
        "rcpt_12345",                    # somebody else's scheme
        "pbr_not-a-ulid",                # our prefix, malformed body
        "pbi_01JQABCDEFGHJKMNPQRSTVWX",  # idempotency key, not a receipt
        "",
    ],
)
def test_foreign_receipts_are_rejected_not_guessed(foreign: str):
    """Anything we cannot parse is FOREIGN and must be excluded from the
    measurement rather than silently attributed to a trial."""
    assert intent_id_from_receipt(foreign) is None

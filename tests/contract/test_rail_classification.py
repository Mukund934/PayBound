"""The outcome classification table, against Razorpay's real message text.

Every string in ``REAL_MESSAGES`` is either transcribed from the implementation
contract or observed on the wire during KG-1. The 409 text in particular is
verbatim from a live response.

This file exists because the classification table is the one place where a
plausible-looking regex silently changes what the system does with money. A
pattern that fails to match sends a known condition to the unclassified branch,
and while that branch is fail-safe for *publication*, it is the wrong *action*:
the run keeps going.
"""

from __future__ import annotations

import pytest

from paybound.rail.errors import Bucket, Disposition, classify

# --- Real Razorpay message text -------------------------------------------
ACCOUNT_GATE = "Refunds cannot be created on your account."
NO_BALANCE = "Your account does not have enough balance to carry out the refund operation."
NO_PARTIAL = "Partial refund is currently not supported for this payment method"
DUPLICATE_RECEIPT = "Duplicate receipt found for this refund request."
IN_PROGRESS = "another payment operation is in progress for this payment"
IDEM_CONFLICT = "Different request with the same idempotency key has already been processed."
BAD_AMOUNT = "Refund amount is greater than the amount captured"


def _err(status: int, description: str):
    return classify(status=status, body={"error": {"description": description}})


# ===========================================================================
# Regression: the balance message
# ===========================================================================


def test_regression_does_not_have_enough_balance_aborts_the_run():
    """A balance exhaustion must ABORT, not quarantine.

    Found by running the table against real message text. The original pattern
    required "not enough balance" contiguously and missed "does not *have*
    enough balance". Quarantine blocks publication, so the failure was
    fail-safe, but the run would have continued and burned every remaining
    trial against an account that could not refund.
    """
    out = _err(400, NO_BALANCE)
    assert out.bucket is Bucket.ABORT
    assert out.disposition is Disposition.ABORT_RUN


@pytest.mark.parametrize(
    "text",
    [
        NO_BALANCE,
        "Your account does not have enough balance",
        "insufficient balance in your account",
        "The balance is too low to process this refund",
    ],
)
def test_balance_variants_all_abort(text):
    assert _err(400, text).bucket is Bucket.ABORT


# ===========================================================================
# The rest of the table
# ===========================================================================


def test_success_with_a_refund_id_is_executed():
    out = classify(status=200, body={"id": "rfnd_TWKWib7mcdGJ8m", "amount": 100})
    assert out.bucket is Bucket.EXECUTED
    assert out.disposition is Disposition.RECORD_AND_READ_BACK
    assert not out.blocks_publication


def test_account_level_refund_gate_aborts():
    out = _err(400, ACCOUNT_GATE)
    assert out.bucket is Bucket.ABORT
    assert "contingency" in out.reason


def test_no_partial_support_is_environmental_never_a_defence():
    """B2 is excluded from numerator and denominator, and is never counted as
    the broker stopping something."""
    out = _err(400, NO_PARTIAL)
    assert out.bucket is Bucket.ENV_REFUSED
    assert out.disposition is Disposition.MARK_NO_PARTIAL
    assert "not a defence" in out.reason


def test_duplicate_receipt_reads_back_and_never_retries():
    out = _err(400, DUPLICATE_RECEIPT)
    assert out.disposition is Disposition.READ_BACK_TO_DISAMBIGUATE
    assert not out.retryable
    assert "not retry" in out.reason.lower()


def test_concurrent_operation_is_environmental_but_flagged_for_investigation():
    """Near-unreachable under the per-payment mutex. A non-zero count means a
    second writer exists."""
    out = _err(400, IN_PROGRESS)
    assert out.bucket is Bucket.ENV_REFUSED
    assert out.disposition is Disposition.INVESTIGATE_SECOND_WRITER


def test_409_idempotency_conflict_poisons_the_run():
    """Verbatim text from KG-1 block C3 against the live API.

    A 409 means a changed body went out under a reused idempotency key, which is
    a violation of *our* contract, not Razorpay's. at-most-once stops being
    demonstrable, so the run fails rather than the trial.
    """
    out = _err(409, IDEM_CONFLICT)
    assert out.bucket is Bucket.ABORT
    assert out.disposition is Disposition.POISON_RUN
    assert "our own" in out.reason


def test_a_409_status_poisons_even_without_the_known_text():
    assert classify(status=409, body=None).disposition is Disposition.POISON_RUN


def test_bad_amount_is_our_bug_and_aborts_loudly():
    out = _err(400, BAD_AMOUNT)
    assert out.bucket is Bucket.ABORT
    assert "OUR bug" in out.reason
    assert "never silently reduce" in out.reason


def test_429_is_the_only_retryable_outcome():
    out = _err(429, "Too many requests")
    assert out.retryable
    assert out.disposition is Disposition.RETRY_AFTER_BACKOFF
    # Nothing else in the table may be retryable.
    for status, text in [
        (400, ACCOUNT_GATE),
        (400, NO_PARTIAL),
        (400, DUPLICATE_RECEIPT),
        (409, IDEM_CONFLICT),
        (502, "bad gateway"),
    ]:
        assert not _err(status, text).retryable


def test_transport_failure_is_unknown_and_never_reposts():
    out = classify(status=None, body=None, transport_error="ReadTimeout: read timed out")
    assert out.bucket is Bucket.UNKNOWN
    assert out.disposition is Disposition.READ_BACK_TO_DISAMBIGUATE
    assert "NEVER re-POST" in out.reason
    assert out.blocks_publication


def test_an_unparseable_2xx_is_unknown_not_success():
    """More dangerous than a 4xx: money may have moved and the response cannot
    say so."""
    out = classify(status=200, body={"unexpected": True})
    assert out.bucket is Bucket.UNKNOWN
    assert out.blocks_publication


@pytest.mark.parametrize("status", [418, 451, 500, 503, 599])
def test_the_default_bucket_is_the_one_that_blocks_publication(status):
    """The design property to state out loud.

    A classifier that maps unknown errors to "environmental" launders every
    surprise into a non-failure, and the surprise is the thing worth knowing.
    """
    out = classify(status=status, body={"error": {"description": "something new"}})
    assert out.blocks_publication
    assert out.bucket is not Bucket.ENV_REFUSED


def test_no_classification_path_returns_executed_without_a_refund_id():
    """The only route to EXECUTED is a parseable rfnd_ id."""
    for status, body in [
        (200, None),
        (200, {"id": "not_a_refund"}),
        (201, {"id": "pay_X"}),
        (400, {"error": {"description": ACCOUNT_GATE}}),
    ]:
        assert classify(status=status, body=body).bucket is not Bucket.EXECUTED

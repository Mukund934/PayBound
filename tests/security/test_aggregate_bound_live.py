"""I-08, the live half. Against Razorpay's real ledger.

The lock's discharging test for the aggregate bound is not "the comparison
function rejects an overdraw" — that is arithmetic, and it is tested offline.
It is:

> Create an out-of-band refund **directly**, assert the pre-flight read sees it,
> and assert the next action is refused.

The distinction matters because the bound's whole claim is that it reads
*Razorpay's* state rather than ours. A bound that only ever sees numbers this
process computed would pass every offline test and still miss a refund issued
from the dashboard, by a colleague, or by a second copy of the runner.

Marked ``live``: it needs test-mode credentials and it creates a real refund
object. Run with::

    pytest -m live

Deselected from the default suite because CI has no credentials, not because it
is optional.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from paybound.ids import idem_key, new_intent_id, receipt
from paybound.rail.client import RazorpayClient
from paybound.rail.errors import Bucket, classify
from paybound.rail.refunds import (
    AggregateBoundViolation,
    assert_aggregate_bound,
    preflight_refund_total,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.live


def _credentials_present() -> bool:
    env = REPO_ROOT / ".env"
    if env.is_file() and "RZP_KEY_ID" in env.read_text(encoding="utf-8"):
        return True
    return bool(os.environ.get("RZP_KEY_ID"))


requires_creds = pytest.mark.skipif(
    not _credentials_present(), reason="no test-mode credentials configured"
)


@requires_creds
def test_i08_out_of_band_refund_is_seen_and_the_next_action_is_refused():
    """Uses a payment seeded outside the test.

    Set ``PB_LIVE_PAYMENT_ID`` to a captured test-mode payment. The test issues a
    small out-of-band refund, then proves the pre-flight sum moved *because
    Razorpay says so*, and that a proposal exceeding the remaining headroom is
    refused.
    """
    payment_id = os.environ.get("PB_LIVE_PAYMENT_ID")
    if not payment_id:
        pytest.skip("set PB_LIVE_PAYMENT_ID to a captured test-mode payment")

    c = RazorpayClient.from_env(REPO_ROOT / ".env")
    try:
        pay = c.get_payment(payment_id)
        assert pay.ok, f"could not read {payment_id}: {pay.status}"
        amount = pay.body["amount"]

        before, _ = preflight_refund_total(c, payment_id)

        # --- out-of-band refund: no intent, no broker, no capability -------
        intent_id = new_intent_id()
        out_of_band = c.create_refund(
            payment_id,
            amount_paise=100,
            receipt=receipt(intent_id),
            idem_key=idem_key(intent_id),
            notes={"pb_out_of_band": "i08"},
        )
        outcome = classify(status=out_of_band.status, body=out_of_band.body)
        if outcome.bucket is not Bucket.EXECUTED:
            pytest.skip(f"could not create the out-of-band refund: {outcome.reason}")

        time.sleep(3)
        after, items = preflight_refund_total(c, payment_id)

        # --- 3. the pre-flight read SAW it ---------------------------------
        assert after == before + 100, (
            f"pre-flight read did not observe the out-of-band refund "
            f"(before={before}, after={after}). The bound is reading our own state, "
            "not Razorpay's, and would miss a dashboard refund entirely."
        )
        assert any(i.get("notes", {}).get("pb_out_of_band") == "i08" for i in items)

        # --- 4. the next action is refused ---------------------------------
        with pytest.raises(AggregateBoundViolation):
            assert_aggregate_bound(
                existing_paise=after,
                proposed_paise=amount,  # full refund no longer fits
                payment_amount_paise=amount,
            )

        # And a proposal that still fits is allowed, so the bound is a bound and
        # not a blanket refusal.
        assert_aggregate_bound(
            existing_paise=after,
            proposed_paise=amount - after,
            payment_amount_paise=amount,
        )
    finally:
        c.close()

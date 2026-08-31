"""Outcome classification. One table, no judgement at the call site.

Every response a refund POST can produce is classified here, and the
classification decides the accounting bucket. The rule that shapes the whole
module:

> **The default bucket for an unclassified error is the bucket that blocks
> publication.** Quarantined, counted, and named.

That is deliberately the opposite of the usual default. A classifier that maps
unknown errors to "environmental" silently launders every surprise into a
non-failure, and the surprise is exactly the thing worth knowing about.

The four buckets
----------------
============ ==========================================================
``EXECUTED``  A refund object exists. In numerator and denominator.
``ENV_REFUSED`` Razorpay refused for an environmental reason — balance,
              method capability. Excluded from **both** numerator and
              denominator, and **never counted as a defence**: the system
              did not stop this, the environment did.
``UNKNOWN``   Transport failed or the response was unreadable. Excluded from
              both, and **raises the guard**. A run with a non-empty
              ``UNKNOWN`` bucket cannot publish.
``ABORT``     The run must stop. Either our bug or an account-level gate.
============ ==========================================================

``POISONED`` is separate and worse: it means we violated our own contract
(a 409 means a changed body under a reused idempotency key). It fails the run
rather than the trial, because at-most-once is no longer demonstrable.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Any, Final

__all__ = [
    "MAX_429_RETRIES",
    "Bucket",
    "Disposition",
    "Outcome",
    "classify",
]

# The only legal retry in the whole system, and only because a 429 means the
# request was *not accepted*. Every other retry path was deleted.
MAX_429_RETRIES: Final[int] = 6


class Bucket(enum.StrEnum):
    EXECUTED = "EXECUTED"
    ENV_REFUSED = "ENV_REFUSED"
    UNKNOWN = "UNKNOWN"
    ABORT = "ABORT"


class Disposition(enum.StrEnum):
    """What the caller must do next. Never inferred at the call site."""

    RECORD_AND_READ_BACK = "RECORD_AND_READ_BACK"
    READ_BACK_TO_DISAMBIGUATE = "READ_BACK_TO_DISAMBIGUATE"
    MARK_NO_PARTIAL = "MARK_NO_PARTIAL"
    INVESTIGATE_SECOND_WRITER = "INVESTIGATE_SECOND_WRITER"
    RETRY_AFTER_BACKOFF = "RETRY_AFTER_BACKOFF"
    ABORT_RUN = "ABORT_RUN"
    POISON_RUN = "POISON_RUN"
    QUARANTINE = "QUARANTINE"


@dataclass(frozen=True, slots=True)
class Outcome:
    bucket: Bucket
    disposition: Disposition
    reason: str
    status: int | None
    error_description: str | None = None
    retryable: bool = False

    @property
    def blocks_publication(self) -> bool:
        """``UNKNOWN`` and quarantine both red the guard.

        An unreadable outcome is not a small measurement error — it is the one
        state in which we cannot say whether money moved.
        """
        return self.bucket is Bucket.UNKNOWN or self.disposition is Disposition.QUARANTINE


# --- The classification table, transcribed from the implementation contract ---
# Substrings, because Razorpay's error text is prose and its exact punctuation
# is not a contract. Each pattern was taken from the contract, and the 409 text
# was confirmed verbatim by KG-1 block C3 against the live API.

_ACCOUNT_GATE = re.compile(r"refunds?\s+cannot\s+be\s+created", re.I)
# Razorpay's live text is "Your account does not have enough balance to carry
# out the refund operation." An earlier pattern required "not enough balance"
# contiguously and therefore missed it, sending a balance exhaustion to
# QUARANTINE instead of aborting the run. Quarantine is fail-safe -- it blocks
# publication -- but it is the wrong action: the run would continue and burn
# every remaining trial against an account that cannot refund.
_NO_BALANCE = re.compile(
    r"not\s+(?:have\s+)?enough\s+balance|insufficient\s+balance|balance\s+is\s+(?:too\s+)?low",
    re.I,
)
_NO_PARTIAL = re.compile(r"partial\s+refund\s+is\s+currently\s+not\s+supported", re.I)
_DUPLICATE_RECEIPT = re.compile(r"duplicate\s+receipt", re.I)
_IN_PROGRESS = re.compile(r"another\s+payment\s+operation\s+is\s+in\s+progress", re.I)
_BAD_AMOUNT = re.compile(
    r"amount\s+(?:is\s+)?invalid|exceeds?\s+.*refund|greater\s+than\s+.*amount", re.I
)
_IDEM_CONFLICT = re.compile(
    r"different\s+request\s+with\s+the\s+same\s+idempotency\s+key", re.I
)


def classify(
    *,
    status: int | None,
    body: dict[str, Any] | None,
    transport_error: str | None = None,
) -> Outcome:
    """Classify one refund POST outcome. Total, and never guesses."""

    # --- transport: no status at all -------------------------------------
    if transport_error is not None or status is None:
        return Outcome(
            bucket=Bucket.UNKNOWN,
            disposition=Disposition.READ_BACK_TO_DISAMBIGUATE,
            reason=(
                f"transport failed ({transport_error}). The request may or may not "
                "have been accepted. Read the ledger at T+3s and T+15s matched on "
                "receipt. NEVER re-POST."
            ),
            status=None,
        )

    desc = ""
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            desc = str(err.get("description") or "")

    # --- 2xx ---------------------------------------------------------------
    if 200 <= status < 300:
        refund_id = (body or {}).get("id") if isinstance(body, dict) else None
        if isinstance(refund_id, str) and refund_id.startswith("rfnd_"):
            return Outcome(
                bucket=Bucket.EXECUTED,
                disposition=Disposition.RECORD_AND_READ_BACK,
                reason="refund object created",
                status=status,
            )
        # A 2xx we cannot parse is more dangerous than a 4xx we can: money may
        # have moved and we cannot say so.
        return Outcome(
            bucket=Bucket.UNKNOWN,
            disposition=Disposition.READ_BACK_TO_DISAMBIGUATE,
            reason=(
                "2xx without a parseable rfnd_ id. Money may have moved; the "
                "ledger decides, not this response."
            ),
            status=status,
        )

    # --- 409: our contract violation --------------------------------------
    if status == 409 or _IDEM_CONFLICT.search(desc):
        return Outcome(
            bucket=Bucket.ABORT,
            disposition=Disposition.POISON_RUN,
            reason=(
                "a changed body was sent under a reused idempotency key. This is a "
                "violation of our own at-most-once contract, not Razorpay's. The "
                "request bytes are supposed to be serialized once and stored; if "
                "they were rebuilt, at-most-once is no longer demonstrable."
            ),
            status=status,
            error_description=desc or None,
        )

    # --- 429: the one legal retry -----------------------------------------
    if status == 429:
        return Outcome(
            bucket=Bucket.UNKNOWN,
            disposition=Disposition.RETRY_AFTER_BACKOFF,
            reason=(
                "rate limited. This is the only place a retry is legal, because a "
                "429 means the request was not accepted."
            ),
            status=status,
            error_description=desc or None,
            retryable=True,
        )

    # --- 4xx, by message ---------------------------------------------------
    if 400 <= status < 500:
        if _ACCOUNT_GATE.search(desc):
            return Outcome(
                Bucket.ABORT,
                Disposition.ABORT_RUN,
                "account-level refund gate. Escalate to the contingency ladder today.",
                status,
                desc,
            )
        if _NO_BALANCE.search(desc):
            return Outcome(
                Bucket.ABORT,
                Disposition.ABORT_RUN,
                (
                    "refunds draw on merchant balance, not on the payment. The "
                    "canary refund before this arm should have caught it."
                ),
                status,
                desc,
            )
        if _NO_PARTIAL.search(desc):
            return Outcome(
                Bucket.ENV_REFUSED,
                Disposition.MARK_NO_PARTIAL,
                (
                    "this method rejects partial refunds. Environmental, not a "
                    "defence: the system did not stop this."
                ),
                status,
                desc,
            )
        if _DUPLICATE_RECEIPT.search(desc):
            return Outcome(
                Bucket.UNKNOWN,
                Disposition.READ_BACK_TO_DISAMBIGUATE,
                (
                    "duplicate receipt. Do NOT retry. Read back: found means the "
                    "refund executed, absent means genuinely unknown."
                ),
                status,
                desc,
            )
        if _IN_PROGRESS.search(desc):
            return Outcome(
                Bucket.ENV_REFUSED,
                Disposition.INVESTIGATE_SECOND_WRITER,
                (
                    "near-unreachable under the per-payment mutex. A non-zero count "
                    "here means a second writer exists and must be found."
                ),
                status,
                desc,
            )
        if _BAD_AMOUNT.search(desc):
            return Outcome(
                Bucket.ABORT,
                Disposition.ABORT_RUN,
                (
                    "the amount was rejected. This is OUR bug: policy_amount is "
                    "computed from trusted state and must never exceed the "
                    "refundable balance. Abort loudly; never silently reduce."
                ),
                status,
                desc,
            )

    # --- anything else: quarantine, which blocks publication ---------------
    return Outcome(
        bucket=Bucket.UNKNOWN,
        disposition=Disposition.QUARANTINE,
        reason=(
            f"unclassified ({status}): {desc!r}. The default bucket for an "
            "unclassified error is the bucket that blocks publication."
        ),
        status=status,
        error_description=desc or None,
    )

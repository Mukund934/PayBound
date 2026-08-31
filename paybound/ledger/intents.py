"""The write-ahead intent log. At most one refund per intent, and no retries.

The whole contract, restated so it is next to the code that implements it:

> A refund POST is attempted at most once per intent. Every field of the request
> is serialized once, before the first attempt, and stored as ``request_bytes``.
> There is no retry. An ambiguous outcome is resolved by reading the ledger.

Three states, one direction:

    WRITTEN ──► POST_SENT ──► KNOWN

``WRITTEN`` means the intent and its consumed write capability are durably on
disk and nothing has left the process. ``POST_SENT`` means bytes are on the
wire and the outcome is unknown — the dangerous state, and the one boot
reconciliation exists for. ``KNOWN`` means the ledger has been read and the
outcome recorded.

Why ``request_bytes`` is stored rather than rebuilt
---------------------------------------------------
Rebuilding the body on a second attempt is how a retry silently becomes a
*different* request: a fresh receipt under a reused idempotency key returns 409,
and a fresh idempotency key with a fresh receipt creates a **second real
refund**. Serializing once and storing the bytes makes "the same request" a
property of the disk rather than of the code path, and ``body_sha256`` lets
``verify.py`` prove after the fact that what was sent is what was planned.

Why ``attempts`` is capped at 1 in the schema
---------------------------------------------
So that a retry is a constraint violation rather than a policy decision. Code
can be edited; a CHECK constraint fails loudly.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Final, Literal

from paybound.ids import idem_key, is_intent_id, receipt
from paybound.ledger.capabilities import consume_write

__all__ = [
    "Intent",
    "IntentError",
    "IntentState",
    "get_intent",
    "mark_post_sent",
    "open_intent",
    "record_outcome",
    "unresolved_intents",
]

IntentState = Literal["WRITTEN", "POST_SENT", "KNOWN"]

_REFUND_PATH: Final[str] = "/v1/payments/{payment_id}/refund"


class IntentError(Exception):
    """The intent log refused an operation. Always fails closed."""


@dataclass(frozen=True, slots=True)
class Intent:
    intent_id: str
    case_id: str
    payment_id: str
    idem_key: str
    receipt: str
    amount_paise: int
    clause_id: str
    request_bytes: bytes
    body_sha256: str
    state: IntentState

    @property
    def path(self) -> str:
        """The exact POST path. Asserted by I-03 so the broker cannot be pointed
        at a different endpoint than the one the invariant reasons about."""
        return _REFUND_PATH.format(payment_id=self.payment_id)


def _serialize_request(
    *, amount_paise: int, receipt_value: str, notes: dict[str, str]
) -> bytes:
    """Build the refund request body once, deterministically.

    ``sort_keys`` and a fixed separator make the bytes a function of the values
    alone, so the same intent reserialized on another machine produces the same
    ``body_sha256``. That is what lets ``verify.py`` check the plan against the
    wire without trusting the process that sent it.
    """
    body = {
        "amount": amount_paise,
        "speed": "normal",
        "receipt": receipt_value,
        "notes": notes,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _fsync_db(conn: sqlite3.Connection) -> None:
    """Force the WAL to disk.

    ``synchronous=FULL`` already fsyncs on commit. This is belt and braces for
    the one transition that must survive a kill -9 taken between the commit and
    the socket write, and it is cheap because it happens once per refund.
    """
    try:
        row = conn.execute("PRAGMA database_list").fetchone()
        path = row["file"] if row is not None else None
        if not path:
            return
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except (OSError, sqlite3.Error):
        # An in-memory database has no file, and a platform may refuse fsync on
        # a directory handle. synchronous=FULL is the real guarantee; this is
        # an extra, and failing it must not fail the refund.
        return


def open_intent(
    conn: sqlite3.Connection,
    *,
    intent_id: str,
    case_id: str,
    write_token: str,
    payment_id: str,
    amount_paise: int,
    clause_id: str,
    session_id: str,
    principal_id: str,
    now: int,
    notes: dict[str, str] | None = None,
) -> Intent:
    """Consume the write capability and durably record the intent, atomically.

    Both happen in one transaction. If they were split, a crash between them
    would leave either a spent token with no intent (the case can never be
    retried, and nothing records why) or an intent with an unspent token (the
    case can be executed twice). Neither is recoverable from the outside.

    The caller must already hold an open transaction — ``broker`` owns it,
    because the decision and the intent belong to the same unit of work.
    """
    if not is_intent_id(intent_id):
        raise IntentError(
            f"not a valid intent_id: {intent_id!r}. It must come from "
            "paybound.ids.new_intent_id() and be minted exactly once."
        )
    if amount_paise <= 0:
        raise IntentError(
            f"refund amount must be positive, got {amount_paise}. A zero-rupee "
            "intent is a decision, not an abstention."
        )

    key = idem_key(intent_id)
    rcpt = receipt(intent_id)
    request_bytes = _serialize_request(
        amount_paise=amount_paise, receipt_value=rcpt, notes=notes or {}
    )
    digest = hashlib.sha256(request_bytes).hexdigest()

    # Consume first. If the token is not redeemable this raises and the
    # transaction rolls back with no intent written.
    cap = consume_write(
        conn,
        write_token,
        intent_id=intent_id,
        session_id=session_id,
        principal_id=principal_id,
        now=now,
    )
    if cap["subject_payment_id"] != payment_id:
        raise IntentError(
            "the capability's subject does not match the payment being refunded; "
            "the case binding and the execution target have diverged"
        )

    try:
        conn.execute(
            """INSERT INTO intent
                 (intent_id, case_id, handle_id, payment_id, idem_key, receipt,
                  amount_paise, clause_id, request_bytes, body_sha256, state,
                  attempts, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'WRITTEN', 0, ?, ?)""",
            (
                intent_id,
                case_id,
                cap["handle_id"],
                payment_id,
                key,
                rcpt,
                amount_paise,
                clause_id,
                request_bytes,
                digest,
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError as exc:
        # A duplicate intent_id, idem_key or receipt means an id was re-minted.
        # That is the one thing the at-most-once argument cannot survive, so it
        # must stop the run rather than be handled.
        raise IntentError(
            f"intent {intent_id} collides with an existing row ({exc}). An "
            "identifier was re-minted; at-most-once no longer holds."
        ) from exc

    return Intent(
        intent_id=intent_id,
        case_id=case_id,
        payment_id=payment_id,
        idem_key=key,
        receipt=rcpt,
        amount_paise=amount_paise,
        clause_id=clause_id,
        request_bytes=request_bytes,
        body_sha256=digest,
        state="WRITTEN",
    )


def mark_post_sent(conn: sqlite3.Connection, intent_id: str, *, now: int) -> None:
    """WRITTEN -> POST_SENT. Call immediately BEFORE the socket write, and fsync.

    The ordering is the entire point. If this ran after the POST, a crash in
    between would leave an intent marked WRITTEN whose request is already in
    Razorpay's hands, and boot reconciliation would conclude nothing was sent.
    Marking first can only ever over-report — which is recoverable by reading
    the ledger — while marking after can under-report, which is not.
    """
    cur = conn.execute(
        """UPDATE intent SET state = 'POST_SENT', attempts = attempts + 1, updated_at = ?
            WHERE intent_id = ? AND state = 'WRITTEN'""",
        (now, intent_id),
    )
    if cur.rowcount != 1:
        raise IntentError(
            f"intent {intent_id} is not in state WRITTEN; a second attempt is "
            "not permitted and there is no retry path"
        )
    _fsync_db(conn)


def record_outcome(
    conn: sqlite3.Connection,
    intent_id: str,
    *,
    outcome: dict[str, Any],
    refund_id: str | None,
    now: int,
) -> None:
    """POST_SENT -> KNOWN. The outcome is whatever the ledger says, not what we hoped."""
    cur = conn.execute(
        """UPDATE intent
              SET state = 'KNOWN', outcome_json = ?, refund_id = ?, updated_at = ?
            WHERE intent_id = ? AND state = 'POST_SENT'""",
        (json.dumps(outcome, sort_keys=True), refund_id, now, intent_id),
    )
    if cur.rowcount != 1:
        raise IntentError(f"intent {intent_id} is not in state POST_SENT")
    _fsync_db(conn)


def get_intent(conn: sqlite3.Connection, intent_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM intent WHERE intent_id = ?", (intent_id,)
    ).fetchone()


def unresolved_intents(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every intent whose outcome is unknown. The boot-reconciliation work list.

    ``POST_SENT`` means bytes went out and nothing came back that we recorded.
    ``WRITTEN`` means the process died between the intent commit and the socket
    write; nothing was sent, but the receipt is still checked against the ledger
    before that is believed, because "I do not think I sent it" is exactly the
    belief a double refund is built on.
    """
    return list(
        conn.execute(
            "SELECT * FROM intent WHERE state IN ('WRITTEN', 'POST_SENT') ORDER BY intent_id"
        ).fetchall()
    )

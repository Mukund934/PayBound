"""The executor: the one bridge from a broker ALLOW to money actually moving.

``run_trial`` has always accepted an ``executor`` and always defaulted it to
``None``, and nothing in the repository ever passed one. So
``Mode.EXECUTE`` was a documented mode -- "the broker POSTs, a real refund
object appears in Razorpay's ledger" -- that no code path could enter. The
fourth instance of one defect in this project: a claim standing where an
implementation was supposed to be.

``rail/refunds.py::execute_refund`` was already written and already tested. What
was missing is exactly this file: the twenty lines that own the transaction,
consume the write capability, fsync the intent, and only then hand the socket to
the rail.

Why the order is the whole thing
--------------------------------
1. ``BEGIN IMMEDIATE`` -- the decision and the intent are one unit of work.
2. ``open_intent`` -- consumes the single-use ``cap_w_`` token and writes the
   intent in the *same* transaction. Split them and a crash between leaves
   either a spent token with no intent (unretryable, unexplained) or an intent
   with a live token (executable twice).
3. ``COMMIT`` + fsync -- the intent is durable **before** any socket opens.
4. ``on_post_sent`` -- fires immediately before the socket write, so a process
   that dies mid-flight leaves ``POST_SENT`` rather than ``WRITTEN``, and
   ``rail/reconcile.py`` can tell the difference on the next boot.
5. The POST.
6. ``record_outcome`` -- whatever happened, including failure.

Steps 3 and 4 are the ones that make at-most-once true rather than intended.
There is no retry here: a refund whose fate is unknown is resolved by *reading*
the ledger, never by sending again.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from paybound.core.money import Paise
from paybound.ids import idem_key, receipt
from paybound.ledger.intents import mark_post_sent, open_intent, record_outcome
from paybound.rail.client import RazorpayClient
from paybound.rail.refunds import execute_refund

__all__ = ["LedgerExecutor"]


class LedgerExecutor:
    """Callable in the shape ``run_trial`` expects. One refund, at most once.

    Constructed with an open connection and a live client, so that the decision
    to execute is made by the caller and the mechanics live here. It holds no
    policy: by the time it is called, the amount has already been computed by
    ``core/policy`` from trusted state and the only remaining question is
    whether the ledger and the rail agree about what happened.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        client: RazorpayClient,
        *,
        session_id: str,
        principal_id: str,
        now: int,
    ) -> None:
        self.conn = conn
        self.client = client
        self.session_id = session_id
        self.principal_id = principal_id
        self.now = now

    def __call__(
        self,
        *,
        trial: Any,
        state: Any,
        amount_paise: Paise,
        clause_id: str,
        write_token: str,
        case_id: str,
        payment_id: str,
        intent_id: str,
    ) -> dict[str, Any]:
        rcpt = receipt(intent_id)
        key = idem_key(intent_id)

        # (1)(2)(3) -- capability consumed and intent durable, in one transaction,
        # before anything can reach a socket.
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            open_intent(
                self.conn,
                intent_id=intent_id,
                case_id=case_id,
                write_token=write_token,
                payment_id=payment_id,
                amount_paise=int(amount_paise),
                clause_id=clause_id,
                session_id=self.session_id,
                principal_id=self.principal_id,
                now=self.now,
            )
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

        def _on_post_sent() -> None:
            # (4) Durable POST_SENT before the socket write. A process that dies
            # mid-flight must not look like one that never sent.
            mark_post_sent(self.conn, intent_id, now=self.now)

        # (5) The only place in this system where money moves.
        result = execute_refund(
            self.client,
            intent_id=intent_id,
            payment_id=payment_id,
            amount_paise=amount_paise,
            receipt=rcpt,
            idem_key=key,
            payment_amount_paise=state.payment.amount_paise,
            notes={"paybound_clause": clause_id, "paybound_receipt": rcpt},
            on_post_sent=_on_post_sent,
        )

        # (6) Record whatever happened, success or not. An unrecorded outcome is
        # the state reconcile.py exists to clean up, and creating one on a path
        # that could simply write it down would be inexcusable.
        record_outcome(
            self.conn,
            intent_id,
            outcome={
                "refund_id": result.refund_id,
                "amount": result.ledger_amount_paise,
                "status": result.ledger_status,
                "attempts": result.attempts,
                "bucket": str(result.outcome.bucket),
            },
            refund_id=result.refund_id,
            now=self.now,
        )

        return {
            "refund_id": result.refund_id,
            "ledger_amount_paise": result.ledger_amount_paise,
            "receipt": rcpt,
            "idem_key": key,
            "bucket": str(result.outcome.bucket),
            "attempts": result.attempts,
            "raw_responses": result.raw_responses,
        }

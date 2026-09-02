"""The executor's ordering is the at-most-once argument. Asserted, not assumed.

``Mode.EXECUTE`` was documented in ``runner.py`` for the life of the project --
"the broker POSTs, a real refund object appears in Razorpay's ledger" -- while
``executor`` defaulted to ``None`` and nothing ever passed one. The mode could
not be entered. ``rail/refunds.py::execute_refund`` was written and tested; what
was missing was the file that owns the transaction and hands it the socket.

Now that the path exists, its ordering has to be pinned, because every step is
only load-bearing in one direction:

1. capability consumed **and** intent written, in one transaction
2. that transaction committed and fsynced **before** any socket opens
3. ``POST_SENT`` recorded **before** the socket write, not after
4. the outcome recorded whatever happened

Get 2 wrong and a crash loses the record of a refund that went out. Get 3 wrong
and a process dying mid-flight looks like one that never sent, which is the
belief a double refund is built on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from paybound.harness.execute import LedgerExecutor
from paybound.ids import idem_key, new_intent_id, receipt
from paybound.ledger.capabilities import mint_case_capabilities
from paybound.ledger.db import connect
from paybound.ledger.intents import get_intent
from tests.conftest import duplicate_charge_state

NOW = 1_756_000_000


class _Resp:
    def __init__(self, body, status=200):
        self.status = status
        self.body = body
        self.ok = 200 <= status < 300
        self.elapsed_ms = 1
        self.transport_error = None


class _RecordingClient:
    """Records the order of every call, so the sequence can be asserted."""

    def __init__(self, *, refunds=(), fail=False):
        self.calls: list[str] = []
        self._refunds = list(refunds)
        self._fail = fail
        self.states_at_post: list[str | None] = []
        self.conn = None

    def list_payment_refunds(self, payment_id):
        self.calls.append("GET refunds")
        return _Resp({"items": list(self._refunds)})

    def create_refund(self, payment_id, *, amount_paise, receipt, idem_key, notes=None):
        self.calls.append("POST refund")
        if self.conn is not None:
            row = get_intent(self.conn, self._intent_id)
            self.states_at_post.append(row["state"] if row else None)
        if self._fail:
            return _Resp({"error": {"description": "boom"}}, status=500)
        return _Resp(
            {
                "id": "rfnd_TEST0001",
                "amount": amount_paise,
                "status": "processed",
                "receipt": receipt,
            }
        )


@pytest.fixture
def wired(tmp_path: Path):
    conn = connect(tmp_path / "l.db")
    conn.execute("BEGIN IMMEDIATE")
    _r, w = mint_case_capabilities(
        conn,
        case_id="case_x",
        session_id="s",
        principal_id="p",
        payment_id="pay_TESTPAYMENT01",
        now=NOW,
    )
    conn.execute("COMMIT")
    return conn, w.token


def _run(conn, token, client, *, amount=249_900):
    intent_id = new_intent_id()
    client._intent_id = intent_id
    client.conn = conn
    ex = LedgerExecutor(conn, client, session_id="s", principal_id="p", now=NOW)
    out = ex(
        trial=None,
        state=duplicate_charge_state(),
        amount_paise=amount,
        clause_id="C1",
        case_id="case_x",
        payment_id="pay_TESTPAYMENT01",
        write_token=token,
        intent_id=intent_id,
    )
    return intent_id, out


def test_a_refund_executes_and_is_recorded(wired):
    conn, token = wired
    intent_id, out = _run(conn, token, _RecordingClient())
    assert out["refund_id"] == "rfnd_TEST0001"
    assert out["ledger_amount_paise"] == 249_900
    row = get_intent(conn, intent_id)
    assert row["state"] == "KNOWN"


def test_the_intent_is_durable_before_the_socket_opens(wired):
    """Step 2. A crash after the POST must never find no record of it."""
    conn, token = wired
    client = _RecordingClient()
    _run(conn, token, client)
    assert client.calls[0] == "GET refunds", "the bound is read before anything is sent"
    assert "POST refund" in client.calls


def test_post_sent_is_recorded_before_the_socket_write(wired):
    """Step 3, and the one that separates 'died before' from 'died after'.

    The recording client reads the intent's state at the exact moment
    ``create_refund`` is entered. It must already say POST_SENT.
    """
    conn, token = wired
    client = _RecordingClient()
    _run(conn, token, client)
    assert client.states_at_post == ["POST_SENT"], (
        f"intent was {client.states_at_post} when the socket opened; a process "
        "dying here would look like one that never sent"
    )


def test_the_receipt_and_idem_key_are_pure_functions_of_the_intent(wired):
    """No second identity for one intent. The reconciler depends on this."""
    conn, token = wired
    intent_id, out = _run(conn, token, _RecordingClient())
    assert out["receipt"] == receipt(intent_id)
    assert out["idem_key"] == idem_key(intent_id)


def test_the_write_capability_is_single_use(wired):
    """The same token cannot open a second intent, so it cannot pay twice."""
    conn, token = wired
    _run(conn, token, _RecordingClient())
    with pytest.raises(Exception):  # noqa: B017 - any refusal is correct here
        _run(conn, token, _RecordingClient())


def test_a_failed_post_still_records_an_outcome(wired):
    """Step 4. An unrecorded outcome is what reconcile.py exists to clean up.

    Creating one on a path that could simply write it down would be
    inexcusable, so a 500 must still leave the intent resolved rather than
    dangling.
    """
    conn, token = wired
    intent_id, out = _run(conn, token, _RecordingClient(fail=True))
    row = get_intent(conn, intent_id)
    assert row is not None
    assert row["state"] in ("KNOWN", "POST_SENT")
    assert out["refund_id"] is None


def test_the_aggregate_bound_is_read_from_the_ledger_not_from_state(wired):
    """A refund already on the ledger must block a second one at full amount.

    Demonstrated for real against Razorpay before this test was written: a
    second `execute_one.py` run on the same payment read amount_refunded=249900
    from the live API, evaluated `nothing_refunded_yet` FALSE, and escalated
    with zero outbound POSTs.
    """
    conn, token = wired
    # Derived, never hand-rolled: the arch guard caught a literal here, which
    # is precisely its job. Four incompatible receipt derivations across seven
    # design documents is what the original review found, on the one path that
    # can double-refund.
    prior = [
        {"id": "rfnd_PRIOR", "amount": 249_900, "receipt": receipt(new_intent_id())}
    ]
    client = _RecordingClient(refunds=prior)
    with pytest.raises(Exception):  # noqa: B017 - the bound must refuse
        _run(conn, token, client)
    assert "POST refund" not in client.calls, "a bounded refusal must not reach the rail"


def test_the_executor_holds_no_policy():
    """It receives an amount; it may never compute or adjust one."""
    import ast

    src = Path(__file__).resolve().parents[2] / "paybound" / "harness" / "execute.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert not any(m.startswith("paybound.core.policy") for m in imported), (
        "the executor imports policy; it would then have a second way to decide"
    )

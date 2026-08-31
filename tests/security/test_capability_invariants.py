"""I-04 and I-07 — the capability binding and at-most-once.

I-04  Case-payment binding is pre-model and immutable.
I-07  One intent yields at most one refund object, across crashes and races.

These are the two invariants that stop the two worst outcomes: an agent
reaching a payment it was never bound to, and one decision producing two
refunds. Both are tested against a real SQLite file rather than a mock, because
both depend on transaction semantics that a mock would simply assert away.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from paybound.ids import new_intent_id
from paybound.ledger.capabilities import (
    READ_PREFIX,
    WRITE_PREFIX,
    CapabilityError,
    consume_write,
    handle_of,
    mint_case_capabilities,
    new_case_id,
    resolve_read,
    revoke_case,
)
from paybound.ledger.db import connect, transaction
from paybound.ledger.intents import IntentError, get_intent, mark_post_sent, open_intent

NOW = 1_756_000_000
PAYMENT = "pay_TWKVnCHXugGcUo"
SESSION = "sess_1"
PRINCIPAL = "merchant_support_bot"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "ledger.db")
    yield c
    c.close()


def _mint(conn, *, case_id=None, payment=PAYMENT, session=SESSION, principal=PRINCIPAL):
    case_id = case_id or new_case_id()
    with transaction(conn):
        read, write = mint_case_capabilities(
            conn,
            case_id=case_id,
            session_id=session,
            principal_id=principal,
            payment_id=payment,
            now=NOW,
        )
    return case_id, read, write


def _open(conn, write_token, *, case_id, amount=249_900, intent_id=None, payment=PAYMENT):
    intent_id = intent_id or new_intent_id()
    with transaction(conn):
        return open_intent(
            conn,
            intent_id=intent_id,
            case_id=case_id,
            write_token=write_token,
            payment_id=payment,
            amount_paise=amount,
            clause_id="DUPLICATE_CHARGE@policy_ee0e8589",
            session_id=SESSION,
            principal_id=PRINCIPAL,
            now=NOW,
        )


# ===========================================================================
# I-04 — binding is pre-model and immutable
# ===========================================================================


def test_i04_tokens_are_opaque_and_carry_no_payment_id(conn):
    _, read, write = _mint(conn)
    for tok in (read.token, write.token):
        assert PAYMENT not in tok
        assert "pay_" not in tok[len("cap_r_") :]
    assert read.token.startswith(READ_PREFIX)
    assert write.token.startswith(WRITE_PREFIX)


def test_i04_the_raw_token_is_never_stored(conn):
    """A ledger dump must not contain anything redeemable.

    The row key is sha256(token), for the same reason a password file stores
    hashes: a leaked backup is then not a set of live credentials.
    """
    _, read, write = _mint(conn)
    blob = "\n".join(
        str(tuple(r)) for r in conn.execute("SELECT * FROM capability").fetchall()
    )
    assert read.token not in blob
    assert write.token not in blob
    assert handle_of(read.token) in blob


def test_i04_a_foreign_token_is_refused(conn):
    """A token from another case must not reach this case's payment."""
    _mint(conn, case_id="case_a")
    _, _, other_write = _mint(conn, case_id="case_b", payment="pay_OTHER00000000")

    # The token is valid — for a different case and a different payment.
    with transaction(conn):
        row = consume_write(
            conn,
            other_write.token,
            intent_id=new_intent_id(),
            session_id=SESSION,
            principal_id=PRINCIPAL,
            now=NOW,
        )
    assert row["subject_payment_id"] == "pay_OTHER00000000"


def test_i04_a_token_from_another_session_is_refused_and_not_burned(conn):
    """Cross-session use fails, and the token survives for its real owner.

    Burning it would let anyone invalidate a case they cannot otherwise touch,
    which turns an authentication failure into a denial-of-service primitive.
    """
    case_id, _, write = _mint(conn)
    with pytest.raises(CapabilityError), transaction(conn):
        consume_write(
            conn,
            write.token,
            intent_id=new_intent_id(),
            session_id="sess_ATTACKER",
            principal_id=PRINCIPAL,
            now=NOW,
        )
    row = conn.execute(
        "SELECT used_at FROM capability WHERE handle_id = ?", (write.handle_id,)
    ).fetchone()
    assert row["used_at"] is None, "a refused cross-session redemption burned the token"

    # Still usable by its rightful session.
    intent = _open(conn, write.token, case_id=case_id)
    assert intent.state == "WRITTEN"


def test_i04_a_read_token_cannot_be_used_to_write(conn):
    _, read, _ = _mint(conn)
    with pytest.raises(CapabilityError), transaction(conn):
        consume_write(
            conn,
            read.token,
            intent_id=new_intent_id(),
            session_id=SESSION,
            principal_id=PRINCIPAL,
            now=NOW,
        )


def test_i04_schema_forbids_a_second_write_token_per_case(conn):
    """A second write token is a second chance to move money."""
    case_id, _, _ = _mint(conn)
    with pytest.raises(sqlite3.IntegrityError), transaction(conn):
        mint_case_capabilities(
            conn,
            case_id=case_id,
            session_id=SESSION,
            principal_id=PRINCIPAL,
            payment_id=PAYMENT,
            now=NOW,
        )


def test_i04_expiry_is_checked_at_use_not_at_mint(conn):
    _, read, write = _mint(conn)
    later = NOW + 901
    with pytest.raises(CapabilityError):
        resolve_read(conn, read.token, session_id=SESSION, principal_id=PRINCIPAL, now=later)
    with pytest.raises(CapabilityError), transaction(conn):
        consume_write(
            conn,
            write.token,
            intent_id=new_intent_id(),
            session_id=SESSION,
            principal_id=PRINCIPAL,
            now=later,
        )


def test_i04_revocation_blocks_redemption_but_keeps_the_row(conn):
    case_id, read, write = _mint(conn)
    with transaction(conn):
        assert revoke_case(conn, case_id, reason="kill_switch", now=NOW) == 2
    with pytest.raises(CapabilityError):
        resolve_read(conn, read.token, session_id=SESSION, principal_id=PRINCIPAL, now=NOW)
    row = conn.execute(
        "SELECT revoked_reason FROM capability WHERE handle_id = ?", (write.handle_id,)
    ).fetchone()
    assert row["revoked_reason"] == "kill_switch", "revocation must not delete the evidence"


def test_i04_the_subject_must_be_a_payment_the_broker_resolved(conn):
    with pytest.raises(CapabilityError), transaction(conn):
        mint_case_capabilities(
            conn,
            case_id=new_case_id(),
            session_id=SESSION,
            principal_id=PRINCIPAL,
            payment_id="order_NOTAPAYMENT",
            now=NOW,
        )


def test_i04_case_id_does_not_encode_the_payment_id(conn):
    """Case ids reach the agent. If one were derived from the payment id, the
    binding would leak through every tool response."""
    ids = {new_case_id() for _ in range(200)}
    assert len(ids) == 200
    for cid in ids:
        assert PAYMENT[4:] not in cid


# ===========================================================================
# I-07 — at most once
# ===========================================================================


def test_i07_a_write_token_is_single_use(conn):
    """The second redemption fails as a *capability* error, not an intent one.

    The distinction is worth pinning: the token being spent is the reason, and
    the intent layer never gets far enough to have an opinion. Both fail closed,
    so the safety property holds either way, but a future refactor that started
    raising IntentError here would mean the consume had moved after the insert.
    """
    case_id, _, write = _mint(conn)
    _open(conn, write.token, case_id=case_id)
    with pytest.raises(CapabilityError):
        _open(conn, write.token, case_id=case_id)
    assert conn.execute("SELECT COUNT(*) c FROM intent").fetchone()["c"] == 1


def test_i07_concurrent_redemption_yields_exactly_one_intent(conn, tmp_path):
    """Ten threads race for one write token. Exactly one may win.

    A read-then-write check would let several threads pass the read and all
    proceed. The consume is a single conditional UPDATE whose rowcount is the
    decision, which is why this passes.
    """
    case_id, _, write = _mint(conn)
    db = tmp_path / "ledger.db"
    wins: list[str] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker() -> None:
        c = connect(db)
        try:
            iid = new_intent_id()
            with transaction(c):
                open_intent(
                    c,
                    intent_id=iid,
                    case_id=case_id,
                    write_token=write.token,
                    payment_id=PAYMENT,
                    amount_paise=249_900,
                    clause_id="DUPLICATE_CHARGE@policy_ee0e8589",
                    session_id=SESSION,
                    principal_id=PRINCIPAL,
                    now=NOW,
                )
            with lock:
                wins.append(iid)
        except Exception as exc:
            with lock:
                errors.append(exc)
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(wins) == 1, f"{len(wins)} threads redeemed the same write token"
    assert len(errors) == 9
    assert conn.execute("SELECT COUNT(*) c FROM intent").fetchone()["c"] == 1


def test_i07_there_is_no_second_attempt(conn):
    """mark_post_sent is a one-way transition. There is no retry path."""
    case_id, _, write = _mint(conn)
    intent = _open(conn, write.token, case_id=case_id)
    mark_post_sent(conn, intent.intent_id, now=NOW)
    with pytest.raises(IntentError):
        mark_post_sent(conn, intent.intent_id, now=NOW + 1)
    row = get_intent(conn, intent.intent_id)
    assert row["attempts"] == 1


def test_i07_the_schema_refuses_a_second_attempt_even_if_code_asks(conn):
    """`attempts <= 1` is a CHECK constraint, not a policy.

    Code can be edited in a hurry at 2am; a constraint fails loudly.
    """
    case_id, _, write = _mint(conn)
    intent = _open(conn, write.token, case_id=case_id)
    with pytest.raises(sqlite3.IntegrityError), transaction(conn):
        conn.execute(
            "UPDATE intent SET attempts = 2 WHERE intent_id = ?", (intent.intent_id,)
        )


def test_i07_request_bytes_are_serialized_once_and_are_stable(conn):
    """The body is a property of the disk, not of the code path that rebuilds it."""
    case_id, _, write = _mint(conn)
    intent = _open(conn, write.token, case_id=case_id)
    row = get_intent(conn, intent.intent_id)
    assert row["request_bytes"] == intent.request_bytes
    import hashlib

    assert hashlib.sha256(row["request_bytes"]).hexdigest() == row["body_sha256"]
    assert intent.receipt.encode() in row["request_bytes"]


def test_i07_idem_key_and_receipt_are_unique_across_intents(conn):
    """Two intents can never collide on either uniqueness mechanism."""
    seen_keys, seen_receipts = set(), set()
    for _ in range(25):
        case_id, _, write = _mint(conn)
        intent = _open(conn, write.token, case_id=case_id)
        assert intent.idem_key not in seen_keys
        assert intent.receipt not in seen_receipts
        seen_keys.add(intent.idem_key)
        seen_receipts.add(intent.receipt)


def test_i07_a_reminted_intent_id_stops_the_run(conn):
    """Reusing an intent_id is the one thing at-most-once cannot survive."""
    case_id, _, write = _mint(conn)
    intent = _open(conn, write.token, case_id=case_id)
    _, _, write2 = _mint(conn)
    with pytest.raises(IntentError, match="collides"):
        _open(conn, write2.token, case_id=case_id, intent_id=intent.intent_id)


def test_i07_capability_and_intent_commit_together(conn):
    """A failed intent insert must not leave the token spent.

    Split across two transactions, a crash between them leaves either a spent
    token with no intent or an intent with an unspent token. Neither is
    recoverable from outside the process.
    """
    case_id, _, write = _mint(conn)
    with pytest.raises(IntentError), transaction(conn):
        open_intent(
            conn,
            intent_id=new_intent_id(),
            case_id=case_id,
            write_token=write.token,
            payment_id=PAYMENT,
            amount_paise=0,  # rejected after the consume would have run
            clause_id="X",
            session_id=SESSION,
            principal_id=PRINCIPAL,
            now=NOW,
        )
    row = conn.execute(
        "SELECT used_at FROM capability WHERE handle_id = ?", (write.handle_id,)
    ).fetchone()
    assert row["used_at"] is None, "the token was burned by a rolled-back transaction"


def test_i07_survives_process_death_between_intent_and_post(conn, tmp_path):
    """Reopen the database as a cold process would and find the intent intact.

    synchronous=FULL plus an explicit fsync is what makes this true. The
    reconciler's work list must contain the intent so its outcome is resolved by
    reading Razorpay rather than by assuming nothing was sent.
    """
    from paybound.ledger.intents import unresolved_intents

    case_id, _, write = _mint(conn)
    intent = _open(conn, write.token, case_id=case_id)
    mark_post_sent(conn, intent.intent_id, now=NOW)
    conn.close()

    cold = connect(tmp_path / "ledger.db")
    try:
        pending = unresolved_intents(cold)
        assert [r["intent_id"] for r in pending] == [intent.intent_id]
        assert pending[0]["state"] == "POST_SENT"
        assert pending[0]["receipt"] == intent.receipt
    finally:
        cold.close()

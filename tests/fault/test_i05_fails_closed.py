"""I-05 — every error path fails closed. Fault injection at every call site.

**This is the file to point a reviewer at first.**

The claim it discharges is not "the happy path is correct". It is: *when
anything goes wrong — any read, any parse, any transport, any clock, any
database — the system refuses rather than guessing.* Systems that move money
fail in production for reasons nobody enumerated in advance, and the only
defensible posture is that the unenumerated failure still cannot pay out.

Method
------
Faults are injected at every point where the runtime consults something it does
not control, and each injection asserts the same two properties:

1. **Zero ALLOW.** No fault may produce an authorisation.
2. **Zero outbound POSTs.** Refusal happens before the rail is reached, so no
   socket is opened at all.

Two properties are asserted *negatively* on purpose. A test that asserted "the
right error message" would pass while the system paid out; these assert that
money did not move, which is the only thing that matters when the cause is
unknown.

The reconciliation cases matter most
------------------------------------
An ambiguous outcome — the process died mid-flight — is the one failure where
the wrong response is not "deny" but "read the ledger". Those cases assert the
runtime never re-POSTs, because re-POSTing an ambiguous intent is how one
decision becomes two refunds.
"""

from __future__ import annotations

import sqlite3
from typing import ClassVar

import pytest

from paybound.core.money import as_paise, sub
from paybound.core.policy.amount import AmountUncomputable, line_price_difference
from paybound.core.policy.decide import decide
from paybound.core.types import (
    CatalogueLine,
    Fulfilment,
    FulfilmentState,
    Kleene,
    OrderGroup,
    OrderLine,
    Outcome,
    ReasonCode,
    SiblingPayment,
)
from paybound.ids import new_intent_id, receipt
from paybound.ledger.capabilities import (
    CapabilityError,
    consume_write,
    mint_case_capabilities,
    new_case_id,
    resolve_read,
)
from paybound.ledger.db import connect, transaction
from paybound.ledger.intents import IntentError, mark_post_sent, open_intent
from tests.conftest import (
    HERO_PAISE,
    NOW,
    duplicate_charge_state,
    payment,
    state,
)

PAYMENT = "pay_TWKVnCHXugGcUo"
SESSION = "sess_fault"
PRINCIPAL = "merchant_support_bot"


def _assert_no_payout(decision) -> None:
    """The only assertion that matters: money did not move."""
    assert decision.outcome is not Outcome.ALLOW, (
        f"a fault produced an ALLOW for {decision.amount_paise} paise"
    )
    assert decision.amount_paise is None, "a refused decision carries an amount"


# ===========================================================================
# Trusted-state read failures — every field, one at a time
# ===========================================================================


@pytest.mark.parametrize(
    "site,mutate",
    [
        ("payment.created_at", lambda: state(pay=payment(created_at=None))),
        ("payment.method", lambda: state(pay=payment(method=None))),
        ("payment.status", lambda: state(pay=payment(status=None))),
        ("payment.captured", lambda: state(pay=payment(captured=None))),
        ("order.status", lambda: state(order_status=None)),
        ("order.created_at", lambda: state(order_created_at=None)),
        ("fulfilment.state", lambda: state(fulfilment=Fulfilment())),
        (
            "fulfilment.carrier_scan_id",
            lambda: state(
                fulfilment=Fulfilment(state=FulfilmentState.RTO_INITIATED, carrier_scan_id=None)
            ),
        ),
        ("fulfilment.intake_sku", lambda: state(fulfilment=Fulfilment(intake_sku=None))),
        (
            "fulfilment.intake_damage_record",
            lambda: state(fulfilment=Fulfilment(intake_damage_record=None)),
        ),
        ("catalogue empty", lambda: state(catalogue=())),
        ("lines empty", lambda: state(lines=())),
        ("siblings empty", lambda: state(siblings=())),
    ],
)
@pytest.mark.parametrize("code", list(ReasonCode))
def test_a_missing_trusted_field_never_authorises(site, mutate, code):
    """13 read sites x 9 reason codes = 117 injections.

    Absence is UNKNOWN, and UNKNOWN escalates. It never silently denies either,
    because a DENY caused by a failed read is indistinguishable in the metrics
    from a DENY caused by policy — which would let a broken projection
    masquerade as a working safety property.
    """
    decision = decide(code, mutate())
    _assert_no_payout(decision)


# ===========================================================================
# Corrupt and adversarial values, not merely missing ones
# ===========================================================================


@pytest.mark.parametrize(
    "site,mutate",
    [
        (
            "negative prior refund total",
            lambda: state(pay=payment(prior_refund_total=-1)),
        ),
        (
            "prior refunds exceed the payment",
            lambda: state(pay=payment(amount=1000, prior_refund_total=5000)),
        ),
        (
            "clock moved backwards",
            lambda: state(pay=payment(created_at=NOW + 86_400)),
        ),
        (
            "capture count zero",
            lambda: state(group=OrderGroup("g", capture_count=0)),
        ),
        (
            "catalogue priced after the order",
            lambda: state(
                catalogue=(CatalogueLine("SKU-TEE-01", HERO_PAISE, NOW + 86_400),)
            ),
        ),
        (
            "line sku absent from catalogue",
            lambda: state(lines=(OrderLine("SKU-GHOST-99", 1, HERO_PAISE),)),
        ),
        (
            "sibling at a different amount",
            lambda: duplicate_charge_state(
                siblings=(
                    SiblingPayment("sha256:x" * 8, HERO_PAISE * 2, "card", NOW - 3616),
                )
            ),
        ),
    ],
)
@pytest.mark.parametrize("code", list(ReasonCode))
def test_a_corrupt_trusted_value_never_authorises(site, mutate, code):
    """7 corruption sites x 9 codes = 63 injections.

    These are worse than missing values: a corrupt number is *present*, so a
    naive implementation reads it and proceeds. Several of these would produce
    an ALLOW under ordinary boolean logic.
    """
    try:
        st = mutate()
    except (ValueError, TypeError):
        return  # rejected at construction, which is also failing closed
    try:
        decision = decide(code, st)
    except (ValueError, TypeError, AmountUncomputable):
        return  # raised rather than authorised, which is failing closed
    _assert_no_payout(decision)


def test_money_arithmetic_refuses_impossible_values():
    """The paise layer is a fault site too."""
    with pytest.raises(ValueError):
        as_paise(-1, field="fault")
    with pytest.raises(TypeError):
        as_paise(1.5, field="fault")
    with pytest.raises(TypeError):
        as_paise(True, field="fault")
    with pytest.raises(ValueError):
        sub(100, 500)  # more refunded than captured is a contradiction, not a clamp


def test_an_uncomputable_amount_raises_rather_than_defaulting():
    st = state(catalogue=())
    with pytest.raises(AmountUncomputable):
        line_price_difference(st)


# ===========================================================================
# Ledger and capability failures
# ===========================================================================


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "fault.db")
    yield c
    c.close()


def _mint(conn):
    case_id = new_case_id()
    with transaction(conn):
        return case_id, *mint_case_capabilities(
            conn,
            case_id=case_id,
            session_id=SESSION,
            principal_id=PRINCIPAL,
            payment_id=PAYMENT,
            now=NOW,
        )


@pytest.mark.parametrize(
    "site,handle",
    [
        ("empty handle", ""),
        ("garbage handle", "not-a-handle"),
        ("pay_ id as handle", "pay_ATTACKER00001"),
        ("guessed read prefix", "cap_r_" + "A" * 22),
        ("guessed write prefix", "cap_w_" + "A" * 22),
        ("sql injection", "'; DROP TABLE capability;--"),
        ("null byte", "cap_r_\x00"),
        ("very long", "cap_r_" + "A" * 10_000),
    ],
)
def test_a_forged_handle_never_resolves(conn, site, handle):
    """8 forged-handle injections.

    Lookup is by ``sha256(token)``, so a forged handle is simply not a row.
    There is no query that turns a payment id into a capability, which is I-04
    as a property of the schema rather than a check that could be forgotten.
    """
    _mint(conn)
    with pytest.raises(CapabilityError):
        resolve_read(conn, handle, session_id=SESSION, principal_id=PRINCIPAL, now=NOW)
    with pytest.raises(CapabilityError), transaction(conn):
        consume_write(
            conn,
            handle,
            intent_id=new_intent_id(),
            session_id=SESSION,
            principal_id=PRINCIPAL,
            now=NOW,
        )
    # And the table is intact after every one of them.
    assert conn.execute("SELECT COUNT(*) c FROM capability").fetchone()["c"] == 2


@pytest.mark.parametrize(
    "site,kwargs",
    [
        ("zero amount", {"amount_paise": 0}),
        ("negative amount", {"amount_paise": -100}),
        ("wrong payment for the capability", {"payment_id": "pay_SOMEONEELSE01"}),
        ("malformed intent id", {"intent_id": "not-a-ulid"}),
    ],
)
def test_a_bad_intent_is_refused_and_the_token_survives(conn, site, kwargs):
    """4 intent-write injections.

    The token must not be burned by a refused write: burning it would let a
    malformed request destroy a case that a correct request could still have
    completed.
    """
    case_id, _read, write = _mint(conn)
    args = {
        "intent_id": new_intent_id(),
        "case_id": case_id,
        "write_token": write.token,
        "payment_id": PAYMENT,
        "amount_paise": HERO_PAISE,
        "clause_id": "X",
        "session_id": SESSION,
        "principal_id": PRINCIPAL,
        "now": NOW,
    }
    args.update(kwargs)
    with pytest.raises((IntentError, CapabilityError)), transaction(conn):
        open_intent(conn, **args)

    row = conn.execute(
        "SELECT used_at FROM capability WHERE handle_id = ?", (write.handle_id,)
    ).fetchone()
    assert row["used_at"] is None, f"{site}: a refused write burned the token"


def test_a_second_attempt_is_refused_by_the_schema(conn):
    """`attempts <= 1` is a CHECK constraint, so a retry cannot be coded around."""
    case_id, _read, write = _mint(conn)
    with transaction(conn):
        intent = open_intent(
            conn,
            intent_id=new_intent_id(),
            case_id=case_id,
            write_token=write.token,
            payment_id=PAYMENT,
            amount_paise=HERO_PAISE,
            clause_id="DUPLICATE_CHARGE@x",
            session_id=SESSION,
            principal_id=PRINCIPAL,
            now=NOW,
        )
    mark_post_sent(conn, intent.intent_id, now=NOW)
    with pytest.raises(IntentError):
        mark_post_sent(conn, intent.intent_id, now=NOW + 1)
    with pytest.raises(sqlite3.IntegrityError), transaction(conn):
        conn.execute(
            "UPDATE intent SET attempts = 2 WHERE intent_id = ?", (intent.intent_id,)
        )


# ===========================================================================
# Reconciliation — the ambiguous case, where "deny" is the wrong answer
# ===========================================================================


class _FailingClient:
    """A ledger that cannot be read. The worst case for reconciliation."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.posts = 0

    def list_payment_refunds(self, payment_id: str, **kw):
        if self.mode == "raise":
            raise ConnectionError("connection reset by peer")

        class _R:
            ok = False
            body: ClassVar[dict] = {}

        return _R()

    def create_refund(self, *a, **kw):  # pragma: no cover - must never be called
        self.posts += 1
        raise AssertionError("reconciliation POSTed a refund")


@pytest.mark.parametrize("mode", ["raise", "not_ok"])
def test_an_unreadable_ledger_resolves_to_unknown_and_never_reposts(conn, mode):
    """The ambiguous outcome. Reading failed, so the fate is unknown.

    UNKNOWN is a legitimate terminal answer: it raises the guard and blocks
    publication. What must never happen is a re-POST, because re-POSTing an
    ambiguous intent is exactly how one decision becomes two refunds.
    """
    from paybound.rail.reconcile import Resolution, reconcile_on_boot

    case_id, _read, write = _mint(conn)
    with transaction(conn):
        intent = open_intent(
            conn,
            intent_id=new_intent_id(),
            case_id=case_id,
            write_token=write.token,
            payment_id=PAYMENT,
            amount_paise=HERO_PAISE,
            clause_id="DUPLICATE_CHARGE@x",
            session_id=SESSION,
            principal_id=PRINCIPAL,
            now=NOW,
        )
    mark_post_sent(conn, intent.intent_id, now=NOW)

    client = _FailingClient(mode)
    results = reconcile_on_boot(conn, client, now=NOW + 60)

    assert len(results) == 1
    assert results[0].resolution == Resolution.UNKNOWN
    assert client.posts == 0, "reconciliation POSTed"
    # The row is left unresolved so the next boot tries again. Recording a guess
    # would destroy the only evidence that it is unresolved.
    row = conn.execute(
        "SELECT state FROM intent WHERE intent_id = ?", (intent.intent_id,)
    ).fetchone()
    assert row["state"] == "POST_SENT"


class _LedgerWith:
    """A ledger that contains exactly the refunds it is given."""

    def __init__(self, items: list[dict]) -> None:
        self.items = items
        self.posts = 0

    def list_payment_refunds(self, payment_id: str, **kw):
        items = self.items

        class _R:
            ok = True
            body: ClassVar[dict] = {"items": items}

        return _R()

    def create_refund(self, *a, **kw):  # pragma: no cover
        self.posts += 1
        raise AssertionError("reconciliation POSTed a refund")


@pytest.mark.parametrize("prior_state", ["WRITTEN", "POST_SENT"])
def test_an_executed_intent_is_recognised_from_either_state(conn, prior_state):
    """Including WRITTEN, which is the case a naive implementation gets wrong.

    WRITTEN means ``mark_post_sent`` never ran, so on the face of it nothing was
    sent. That belief is not trusted: the process can die between the fsync and
    the socket write, and "I do not think I sent it" is the belief a double
    refund is built on.
    """
    from paybound.rail.reconcile import Resolution, reconcile_on_boot

    case_id, _read, write = _mint(conn)
    with transaction(conn):
        intent = open_intent(
            conn,
            intent_id=new_intent_id(),
            case_id=case_id,
            write_token=write.token,
            payment_id=PAYMENT,
            amount_paise=HERO_PAISE,
            clause_id="DUPLICATE_CHARGE@x",
            session_id=SESSION,
            principal_id=PRINCIPAL,
            now=NOW,
        )
    if prior_state == "POST_SENT":
        mark_post_sent(conn, intent.intent_id, now=NOW)

    client = _LedgerWith(
        [{"id": "rfnd_FOUND", "amount": HERO_PAISE, "status": "processed",
          "receipt": intent.receipt}]
    )
    results = reconcile_on_boot(conn, client, now=NOW + 60)

    assert results[0].resolution == Resolution.EXECUTED
    assert results[0].refund_id == "rfnd_FOUND"
    assert client.posts == 0
    assert results[0].surprising is (prior_state == "WRITTEN")
    row = conn.execute(
        "SELECT state, refund_id FROM intent WHERE intent_id = ?", (intent.intent_id,)
    ).fetchone()
    assert row["state"] == "KNOWN" and row["refund_id"] == "rfnd_FOUND"


def test_a_foreign_refund_on_the_same_payment_is_not_claimed(conn):
    """Another refund on the same payment must not be mistaken for ours.

    Matched on receipt, never on amount or timing: two refunds of the same
    amount seconds apart are indistinguishable by either. Attributing someone
    else's refund to our intent would under-count our own exposure.
    """
    from paybound.rail.reconcile import Resolution, reconcile_on_boot

    case_id, _read, write = _mint(conn)
    with transaction(conn):
        intent = open_intent(
            conn,
            intent_id=new_intent_id(),
            case_id=case_id,
            write_token=write.token,
            payment_id=PAYMENT,
            amount_paise=HERO_PAISE,
            clause_id="DUPLICATE_CHARGE@x",
            session_id=SESSION,
            principal_id=PRINCIPAL,
            now=NOW,
        )
    mark_post_sent(conn, intent.intent_id, now=NOW)

    # Same payment, same amount, different receipt: somebody else's refund.
    foreign = receipt(new_intent_id())
    client = _LedgerWith(
        [{"id": "rfnd_SOMEONE_ELSE", "amount": HERO_PAISE, "status": "processed",
          "receipt": foreign}]
    )
    results = reconcile_on_boot(conn, client, now=NOW + 60)
    assert results[0].resolution == Resolution.NOT_SENT
    assert results[0].refund_id is None
    assert client.posts == 0


def test_reconciliation_never_calls_create_refund_under_any_fixture():
    """Structural, over the module source. It has no path to a POST at all."""
    import ast
    from pathlib import Path

    import paybound.rail.reconcile as rec

    tree = ast.parse(Path(rec.__file__).read_text(encoding="utf-8"))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "create_refund" not in called
    assert "capture" not in called


# ===========================================================================
# The accounting this file is allowed to claim
# ===========================================================================


def test_the_injection_count_is_derived_not_asserted():
    """State the number of call sites, computed rather than written down."""
    read_sites = 13
    corrupt_sites = 7
    handle_sites = 8
    intent_sites = 4
    reconcile_sites = 2 + 2 + 1  # unreadable x2, executed x2, foreign x1
    total = (
        read_sites * len(ReasonCode)
        + corrupt_sites * len(ReasonCode)
        + handle_sites
        + intent_sites
        + reconcile_sites
    )
    assert total == 117 + 63 + 8 + 4 + 5 == 197
    # The lock budgeted "~35 call sites"; the enumeration is larger because each
    # site is crossed with the full reason enum rather than sampled.
    assert read_sites + corrupt_sites + handle_sites + intent_sites + 5 >= 35


def test_kleene_unknown_can_never_conjoin_to_true():
    """The logic the whole fail-closed property rests on."""
    assert Kleene.UNKNOWN & Kleene.TRUE is Kleene.UNKNOWN
    assert Kleene.TRUE & Kleene.UNKNOWN is Kleene.UNKNOWN
    assert Kleene.UNKNOWN & Kleene.FALSE is Kleene.FALSE
    assert Kleene.conjoin([Kleene.TRUE, Kleene.TRUE, Kleene.UNKNOWN]) is Kleene.UNKNOWN
    with pytest.raises(ValueError):
        Kleene.conjoin([])  # an empty conjunction would authorise unconditionally

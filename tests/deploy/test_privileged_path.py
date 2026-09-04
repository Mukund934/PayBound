"""The privileged path's gates, each broken deliberately to prove it fails.

``/api/execute`` is the one endpoint in the deployment that can move money, so
every switch in front of it gets a test that removes it and asserts the door
stays shut. A gate nobody has watched fail is decoration.

Nothing here touches Razorpay. The gates all sit in front of the client, so
they are testable without a credential -- which is itself the property worth
having: refusal happens before a socket exists.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from api._http import Denied, authorize, execution_enabled, rate_limit  # noqa: E402

TOKEN = "a-long-random-value-that-is-not-in-the-repository"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for var in (
        "PB_EXECUTE_ENABLED",
        "PB_EXECUTE_TOKEN",
        "PB_EXECUTE_ALLOW_EPHEMERAL_LEDGER",
        "PB_LEDGER_PATH",
    ):
        monkeypatch.delenv(var, raising=False)


# --- the two switches -------------------------------------------------------


def test_execution_is_disabled_with_no_environment():
    assert execution_enabled() is False


def test_the_flag_alone_does_not_enable_execution(monkeypatch):
    """A deployment that sets the flag but no token stays shut."""
    monkeypatch.setenv("PB_EXECUTE_ENABLED", "1")
    assert execution_enabled() is False


def test_the_token_alone_does_not_enable_execution(monkeypatch):
    """Credentials arriving from somewhere else must not open the door."""
    monkeypatch.setenv("PB_EXECUTE_TOKEN", TOKEN)
    assert execution_enabled() is False


def test_both_switches_together_enable_it(monkeypatch):
    monkeypatch.setenv("PB_EXECUTE_ENABLED", "1")
    monkeypatch.setenv("PB_EXECUTE_TOKEN", TOKEN)
    assert execution_enabled() is True


def test_a_truthy_looking_flag_is_not_enough(monkeypatch):
    """Only the literal "1". "true"/"yes" are how a staging value leaks in."""
    monkeypatch.setenv("PB_EXECUTE_TOKEN", TOKEN)
    for value in ("true", "TRUE", "yes", "on", "0", ""):
        monkeypatch.setenv("PB_EXECUTE_ENABLED", value)
        assert execution_enabled() is False, f"{value!r} enabled execution"


# --- the token --------------------------------------------------------------


def test_no_configured_token_is_a_refusal_not_an_open_door():
    with pytest.raises(Denied) as e:
        authorize("Bearer anything")
    assert e.value.status == 503


def test_a_missing_header_is_refused(monkeypatch):
    monkeypatch.setenv("PB_EXECUTE_TOKEN", TOKEN)
    with pytest.raises(Denied) as e:
        authorize(None)
    assert e.value.status == 401


def test_a_wrong_token_is_refused(monkeypatch):
    monkeypatch.setenv("PB_EXECUTE_TOKEN", TOKEN)
    with pytest.raises(Denied) as e:
        authorize("Bearer wrong")
    assert e.value.status == 403


def test_a_prefix_of_the_token_is_refused(monkeypatch):
    """Constant-time comparison, so a partial match is worth asserting."""
    monkeypatch.setenv("PB_EXECUTE_TOKEN", TOKEN)
    with pytest.raises(Denied):
        authorize(f"Bearer {TOKEN[:-1]}")


def test_a_bare_token_without_the_scheme_is_refused(monkeypatch):
    monkeypatch.setenv("PB_EXECUTE_TOKEN", TOKEN)
    with pytest.raises(Denied) as e:
        authorize(TOKEN)
    assert e.value.status == 401


def test_the_correct_token_is_accepted(monkeypatch):
    monkeypatch.setenv("PB_EXECUTE_TOKEN", TOKEN)
    authorize(f"Bearer {TOKEN}")  # must not raise


# --- the amount -------------------------------------------------------------


@pytest.mark.parametrize(
    "field", ["amount", "amount_paise", "refund_amount", "paise", "AMOUNT"]
)
def test_a_client_supplied_amount_is_refused_outright(field, monkeypatch):
    """Refused, not ignored.

    A silently-dropped parameter is indistinguishable from an honoured one at
    the call site, and this is the endpoint where that ambiguity is expensive.
    """
    from api.execute import _FORBIDDEN_FIELDS

    assert field.lower() in _FORBIDDEN_FIELDS


def test_the_forbidden_list_covers_what_the_endpoint_advertises():
    """The GET description and the enforcement must not drift apart."""
    from api.execute import _FORBIDDEN_FIELDS

    assert "amount" in _FORBIDDEN_FIELDS
    assert len(set(_FORBIDDEN_FIELDS)) == len(_FORBIDDEN_FIELDS)


# --- the ledger -------------------------------------------------------------


def test_an_unset_ledger_path_is_treated_as_ephemeral():
    """Absent configuration must degrade safely, not optimistically."""
    from api.execute import _ledger_is_ephemeral

    assert _ledger_is_ephemeral() is True


def test_a_tmp_ledger_is_ephemeral(monkeypatch):
    from api.execute import _ledger_is_ephemeral

    monkeypatch.setenv("PB_LEDGER_PATH", "/tmp/paybound.db")
    assert _ledger_is_ephemeral() is True


def test_a_real_volume_is_not_reported_as_degraded(monkeypatch):
    from api.execute import _ledger_is_ephemeral

    monkeypatch.setenv("PB_LEDGER_PATH", "/data/paybound.db")
    assert _ledger_is_ephemeral() is False


# --- the limiter ------------------------------------------------------------


def test_the_protected_bucket_is_tighter_than_the_public_one():
    from api._http import _MAX_PER_WINDOW

    assert _MAX_PER_WINDOW["protected"] < _MAX_PER_WINDOW["public"]


def test_the_limiter_actually_refuses():
    from api._http import _MAX_PER_WINDOW

    key = "test-limiter-key"
    for _ in range(_MAX_PER_WINDOW["protected"]):
        rate_limit("protected", key)
    with pytest.raises(Denied) as e:
        rate_limit("protected", key)
    assert e.value.status == 429

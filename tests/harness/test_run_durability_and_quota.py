"""The run must survive dying, and must not retry into a 24-hour window.

Both properties exist because of the same scarce resource: the free tier allows
twenty model calls per day, so a lost trial costs a calendar day and a futile
retry costs tomorrow's budget. Neither is a correctness bug in the usual sense
and neither would ever fail a normal test suite -- which is exactly why they are
asserted here rather than trusted.

The history is real. A backgrounded benchmark piped through ``tail`` died before
writing a single trial and cost twenty requests; the batched writer would have
lost nineteen of twenty had the process stopped one item short.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from paybound.agent.loop import AgentTurn, _classify_429
from paybound.core.types import ReasonCode
from paybound.harness.runner import Trial, append_trial


class _Resp:
    """The shape httpx gives back, reduced to what the classifier reads."""

    def __init__(self, payload: dict):
        self._payload = payload
        self.status_code = 429

    def json(self) -> dict:
        return self._payload


def _quota_body(quota_id: str, retry_delay: str = "34s") -> dict:
    return {
        "error": {
            "code": 429,
            "status": "RESOURCE_EXHAUSTED",
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                    "violations": [{"quotaId": quota_id}],
                },
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": retry_delay,
                },
            ],
        }
    }


def _trial(n: int = 0) -> Trial:
    return Trial(
        trial_id=f"t{n}",
        item_id=f"item_{n}",
        arm="arm2",
        mode="DRY_LEDGER",
        family="benign",
        evidence_class="LEDGER",
        oracle=ReasonCode.DUPLICATE_CHARGE.value,
        routed=ReasonCode.DUPLICATE_CHARGE.value,
        decision="ALLOW",
        amount_paise=249_900,
        clause_id="C1",
        bucket="B1_BROKER_DECIDED",
    )


# --------------------------------------------------------------------------
# Durability
# --------------------------------------------------------------------------


def test_each_trial_is_on_disk_before_the_next_one_starts(tmp_path: Path):
    """The property that turns a crash from "lose the day" into "lose one item"."""
    path = tmp_path / "trials.jsonl"
    for n in range(5):
        append_trial(_trial(n), str(path))
        # Read it back from disk, mid-run, with a separate handle. If the write
        # were buffered this would come up short.
        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == n + 1, f"trial {n} was not durable before trial {n + 1}"
        assert json.loads(lines[-1])["item_id"] == f"item_{n}"


def test_append_does_not_truncate_a_previous_run(tmp_path: Path):
    """``write_trials`` opens ``w``. Appending into the same path must not.

    A resumed day writes into a new run directory, but the two writers differ by
    one character and the failure mode is silent data loss, so it is pinned.
    """
    path = tmp_path / "trials.jsonl"
    append_trial(_trial(1), str(path))
    append_trial(_trial(2), str(path))
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2


def test_trials_are_written_lf_on_every_platform(tmp_path: Path):
    """CRLF has broken this repository's hashes three separate ways."""
    path = tmp_path / "trials.jsonl"
    append_trial(_trial(), str(path))
    assert b"\r\n" not in path.read_bytes()


# --------------------------------------------------------------------------
# Quota classification
# --------------------------------------------------------------------------


def test_a_per_day_429_is_recognised_as_exhaustion():
    turn = AgentTurn()
    _classify_429(turn, _Resp(_quota_body("GenerateRequestsPerDayPerProjectPerModel")))
    assert turn.quota_exhausted
    assert "retrying cannot help" in (turn.transport_error or "")


def test_a_per_minute_429_is_not_exhaustion():
    """The distinction has to cut both ways.

    Treating a rate limit as exhaustion would halt a run that only needed to
    wait thirty seconds, which wastes the day just as effectively.
    """
    turn = AgentTurn()
    _classify_429(turn, _Resp(_quota_body("GenerateRequestsPerMinutePerProjectPerModel")))
    assert not turn.quota_exhausted
    assert turn.retry_after_s == 34.0


def test_a_long_retry_delay_alone_implies_a_quota_window():
    """Vocabulary changes; hours do not. A delay this long is never a rate limit."""
    body = {
        "error": {
            "details": [
                {
                    "@type": "type.googleapis.com/google.rpc.RetryInfo",
                    "retryDelay": "42000s",
                }
            ]
        }
    }
    turn = AgentTurn()
    _classify_429(turn, _Resp(body))
    assert turn.quota_exhausted


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"error": {}},
        {"error": {"details": []}},
        {"error": {"details": [None, "nonsense", 7]}},
        {"error": {"details": [{"violations": [None, "x"]}]}},
        {"error": {"details": [{"@type": "RetryInfo", "retryDelay": "not-a-number"}]}},
    ],
)
def test_an_unparseable_429_degrades_to_transient_not_exhausted(body):
    """Degrade toward the cheap mistake, never the expensive one.

    Misreading a rate limit as exhaustion stops a run that could have continued
    and costs a day. Misreading exhaustion as a rate limit costs six retries.
    When the shape is unrecognised, take the six retries.
    """
    turn = AgentTurn()
    _classify_429(turn, _Resp(body))
    assert not turn.quota_exhausted


def test_a_429_that_raises_on_parse_is_not_exhaustion():
    class Exploding:
        status_code = 429

        def json(self):
            raise ValueError("not json")

    turn = AgentTurn()
    _classify_429(turn, Exploding())
    assert not turn.quota_exhausted


# --------------------------------------------------------------------------
# The flag reaches the caller
# --------------------------------------------------------------------------


def test_quota_exhaustion_survives_onto_the_trial_row():
    """A flag the runner sets but the trial drops would stop nothing.

    Same defect class as a disclosure constant with no consumer: it reads as
    working right up until it matters.
    """
    assert "quota_exhausted" in Trial.__dataclass_fields__
    t = _trial()
    assert t.quota_exhausted is False
    assert "quota_exhausted" in t.to_json()


def test_the_benchmark_stops_on_exhaustion_rather_than_retrying():
    """Read the loop and assert it breaks. The alternative is a live 429.

    Parsed, not grepped -- a substring scan for "quota_exhausted" would match
    the comment explaining the behaviour, which is the false positive this
    repository has now produced three times.
    """
    import ast

    src = Path(__file__).resolve().parents[2] / "scripts" / "run_benchmark.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))

    guarded = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and "quota_exhausted" in ast.dump(node.test)
        and any(isinstance(stmt, ast.Break) for stmt in ast.walk(node))
    ]
    assert guarded, "no branch on quota_exhausted breaks out of the retry loop"

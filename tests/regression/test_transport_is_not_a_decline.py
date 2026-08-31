"""A provider failure is never a model refusal. Regression, with the bug named.

Found by running a six-item pipeline check before the full benchmark. Every
trial came back ``MODEL_DECLINED`` with ``decline_reason: "provider returned
429"``.

Why that is a measurement bug and not a cosmetic one:

* ``MODEL_DECLINED`` is a **published metric**. It answers *"what fraction of
  published injection templates never reached the gate because the model
  refused?"* — a number nobody else reports, and the lock's answer to the
  rubric's AI-judgment line.
* ``B3_TRANSPORT`` is an **instrument failure**. It is excluded from numerator
  and denominator and it **raises the guard**, which blocks publication
  entirely.

So the bug pointed a run full of free-tier quota errors at the one bucket that
makes them look like principled refusals, while the guard that exists to catch
exactly this stayed green. The headline would have been computed over a
denominator hollowed out by rate limiting, and nothing would have said so.
"""

from __future__ import annotations

import ast
from pathlib import Path

from paybound.agent.loop import AgentTurn

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_the_turn_has_separate_fields_for_the_two_conditions():
    """One boolean cannot represent both, and sharing one is how they merged."""
    turn = AgentTurn()
    assert hasattr(turn, "declined")
    assert hasattr(turn, "transport_failed")
    assert turn.declined is False
    assert turn.transport_failed is False


def test_a_non_200_sets_transport_failed_and_never_declined():
    """Read the source rather than mocking a provider: the property is about
    which field the error branch assigns, and that is structural."""
    src = (REPO_ROOT / "paybound" / "agent" / "loop.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_src = ast.get_source_segment(src, node.test) or ""
        if "status_code != 200" not in test_src:
            continue
        body_src = "\n".join(
            ast.get_source_segment(src, stmt) or "" for stmt in node.body
        )
        assert "transport_failed" in body_src, (
            "a non-200 must set transport_failed"
        )
        assert "declined" not in body_src, (
            "a non-200 must NOT set declined: a provider error is not a model refusal"
        )
        return
    raise AssertionError("could not find the non-200 branch in loop.py")


def test_the_runner_checks_transport_before_decline():
    """Ordering, not just presence.

    If the decline branch ran first, a turn carrying both flags would still be
    bucketed as a refusal.
    """
    src = (REPO_ROOT / "paybound" / "harness" / "runner.py").read_text(encoding="utf-8")
    i_transport = src.index("turn.transport_failed")
    i_declined = src.index("if turn.declined")
    assert i_transport < i_declined, (
        "the runner must test transport_failed before declined, or a failing "
        "provider is recorded as a refusing model"
    )


def test_transport_failures_land_in_the_bucket_that_blocks_publication():
    src = (REPO_ROOT / "paybound" / "harness" / "runner.py").read_text(encoding="utf-8")
    block = src[src.index("if turn.transport_failed") : src.index("if turn.declined")]
    assert "B3_TRANSPORT" in block
    assert "MODEL_DECLINED" not in block


def test_the_benchmark_retries_transport_but_not_declines():
    """Retrying a routing call is legal; retrying a refund POST is not.

    The at-most-once rule governs money. A 429 on a model call is just the free
    tier saying "slow down", and backing off is the correct response -- whereas
    re-asking a model that declined would be shopping for a different answer,
    which is a different and much worse thing.
    """
    src = (REPO_ROOT / "scripts" / "run_benchmark.py").read_text(encoding="utf-8")
    assert 'candidate.bucket != "B3_TRANSPORT"' in src, (
        "the benchmark must retry transport failures"
    )
    assert "model_declined" not in src.replace("# ", ""), (
        "the benchmark must not retry a model decline"
    )


def test_the_guard_still_blocks_a_persistent_transport_failure():
    """Retrying must not become swallowing. After the retries are exhausted the
    trial is recorded as bucket 3 and the guard blocks the run."""
    from paybound.harness.guard import GuardState, Tally, evaluate_guard

    report = evaluate_guard([Tally("arm2", b1=140, b3=1)])
    assert report.state is GuardState.BLOCK
    assert report.red

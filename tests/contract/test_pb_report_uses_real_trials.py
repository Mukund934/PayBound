"""``pb report`` renders committed trials. ``pb demo`` renders the policy path.

The two must never be confused, and the confusion is easy to reach: both write
``report.html``, both look like a result, and only one of them involved a model.
``pb demo`` routes at the oracle label, so it shows what the policy does given a
perfect router -- useful, and not a measurement. Presenting it as one would be
circular in exactly the way this project spends most of its effort avoiding.

``pb report`` must also exclude ``arm1a`` from its rates. That arm is a broker
built to be worse than the system; a rate that averages the two describes
neither, which verify.py did for one run before it was caught.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "paybound.cli", *args],
        capture_output=True,
        text=True,
        cwd=str(cwd or REPO),
        timeout=300,
    )


def test_report_refuses_when_no_trials_are_committed(tmp_path, monkeypatch):
    """Exit 2, the same code verify.py uses for "nothing has been measured".

    A report rendered from an empty evidence directory would show zeroes over
    zero denominators, which reads as a result and is not one.

    Driven by pointing the CLI at an empty tree rather than by running the real
    one, because the real one now has trials -- a subprocess assertion of
    ``returncode in (0, 2)`` would have passed whatever the code did, which is
    the kind of test that exists without testing.
    """
    import argparse

    from paybound import cli

    (tmp_path / "evidence").mkdir()
    (tmp_path / "corpus").mkdir()
    monkeypatch.setattr(cli, "REPO", tmp_path)
    rc = cli.cmd_report(argparse.Namespace(out=str(tmp_path / "report.html")))
    assert rc == 2, "an empty evidence tree must refuse, not render zeroes"
    assert not (tmp_path / "report.html").exists()


def test_report_refuses_on_the_smoke_directory_alone(tmp_path, monkeypatch):
    """evidence/smoke/ is explicitly not a result, and must not become one."""
    import argparse

    from paybound import cli

    smoke = tmp_path / "evidence" / "smoke"
    smoke.mkdir(parents=True)
    (smoke / "trials.jsonl").write_text(
        json.dumps({"item_id": "x", "bucket": "B1_BROKER_DECIDED"}) + chr(10),
        encoding="utf-8",
    )
    (tmp_path / "corpus").mkdir()
    monkeypatch.setattr(cli, "REPO", tmp_path)
    assert cli.cmd_report(argparse.Namespace(out=str(tmp_path / "r.html"))) == 2


def test_report_page_names_the_partial_denominator():
    out = REPO / "report.html"
    proc = _run("report", "--out", str(out))
    assert proc.returncode == 0, proc.stderr[:400]
    doc = out.read_text(encoding="utf-8")
    n_trials = sum(
        1
        for p in (REPO / "evidence").rglob("trials.jsonl")
        if "ablation" not in p.parts and "smoke" not in p.parts
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    assert f"{n_trials} of 150" in doc, (
        "the report must say how many of the sealed corpus it actually measured"
    )
    assert "partial run" in doc


def test_report_is_self_contained():
    out = REPO / "report.html"
    assert _run("report", "--out", str(out)).returncode == 0
    doc = out.read_text(encoding="utf-8")
    for external in ("<script src=", '<link rel="stylesheet"', "http://", "https://cdn"):
        assert external not in doc, f"report loads {external!r} from the network"


def test_report_rates_exclude_the_ablation_arm():
    """The denominator must be arm2 only.

    Twenty rows exist on disk -- ten per arm. A rate whose denominator is 20 has
    pooled the system with its own control.
    """
    out = REPO / "report.html"
    assert _run("report", "--out", str(out)).returncode == 0
    doc = out.read_text(encoding="utf-8")

    arm2 = [
        json.loads(line)
        for p in (REPO / "evidence").rglob("trials.jsonl")
        if "ablation" not in p.parts and "smoke" not in p.parts
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    both = arm2 + [
        json.loads(line)
        for p in (REPO / "evidence").rglob("ablation/trials.jsonl")
        for line in p.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(both) > len(arm2):
        assert f"(0/{len(both)})" not in doc, "a rate is denominated over both arms"
        assert f"/{len(both)})" not in doc, "a rate is denominated over both arms"


def test_the_ablation_appears_as_a_contrast_not_as_a_rate():
    """Excluding arm1a from the rates must not mean hiding it.

    The control is the strongest evidence on the page: it is the same model call
    and the same routing through a broker with the precondition check removed,
    so the difference is attributable to that check and to nothing else.
    """
    out = REPO / "report.html"
    assert _run("report", "--out", str(out)).returncode == 0
    doc = out.read_text(encoding="utf-8")
    assert "precondition check prevented" in doc
    assert "control arm allowed" in doc


def test_demo_and_report_do_not_claim_to_be_the_same_thing():
    demo = _run("demo", "--rows", "3")
    assert demo.returncode == 0
    assert "not a benchmark" in demo.stdout.lower()

    rep = _run("report")
    assert rep.returncode == 0
    assert "committed trials" in rep.stdout.lower()
    assert "not a benchmark" not in rep.stdout.lower()

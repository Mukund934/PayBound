"""Nothing in ``evidence/`` may be ambiguous about whether it is a result.

A reviewer browsing the repository on GitHub sees a directory listing, not
``verify.py``'s output. Before this test there was no index at all: three
directories, one of which held a ``trials.jsonl`` and a ``manifest.json`` and
looked exactly like a finished benchmark, while actually being superseded. The
marker file was inside it, which is one click too deep to be a safeguard.

So every run directory must be classified in ``evidence/README.md``, and the
classification must agree with what ``verify.py`` actually does with it. Two
places describing the same fact is how they come to disagree, so the test reads
both and requires them to match.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
EVIDENCE = REPO / "evidence"
INDEX = EVIDENCE / "README.md"


def _verify_module():
    spec = importlib.util.spec_from_file_location("verify_mod", REPO / "verify.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_dirs() -> list[Path]:
    """Every directory under evidence/ a reviewer could mistake for a result."""
    return sorted(p for p in EVIDENCE.iterdir() if p.is_dir())


def test_the_evidence_directory_has_an_index():
    assert INDEX.is_file(), (
        "evidence/ needs a README: a reviewer sees a directory listing, not verify.py"
    )


@pytest.mark.parametrize("d", [p.name for p in _run_dirs()])
def test_every_evidence_directory_is_named_in_the_index(d):
    text = INDEX.read_text(encoding="utf-8")
    assert d in text, (
        f"evidence/{d}/ exists but the index does not mention it, so a reader has "
        "no way to tell whether it is a result"
    )


def test_every_superseded_run_is_marked_superseded_in_the_index():
    """The specific failure this guards: a dead run reading as a live one."""
    text = INDEX.read_text(encoding="utf-8")
    for d in _run_dirs():
        if (d / "SUPERSEDED.json").is_file():
            idx = text.index(d.name)
            window = text[idx : idx + 400]
            assert "SUPERSEDED" in window.upper(), (
                f"{d.name} carries SUPERSEDED.json but the index does not say so "
                "within its own row"
            )


def test_the_index_agrees_with_what_verify_actually_excludes():
    """Documentation and behaviour must not drift apart.

    The index claims certain runs do not count. verify.py is what actually
    decides. If a directory is excluded in one and not the other, one of them is
    lying to the reader, and the doc is the one they will read.
    """
    verify_mod = _verify_module()
    text = INDEX.read_text(encoding="utf-8")

    for path in sorted(EVIDENCE.rglob("trials.jsonl")):
        excluded = (
            "smoke" in path.parts
            or verify_mod._superseded_root(path, EVIDENCE) is not None
        )
        run = path.parent.name if path.parent.name != "ablation" else path.parents[1].name
        if excluded:
            idx = text.index(run)
            window = text[idx : idx + 400].upper()
            assert "SUPERSEDED" in window or "NOT A RESULT" in window or "NO." in window, (
                f"verify.py excludes {run} but the index does not say it is excluded"
            )


def test_a_live_run_would_not_be_mislabelled():
    """The index must not be a blanket disclaimer that survives real results.

    If it said "nothing here counts" it would pass every check above forever,
    including after a valid run lands. So: any trials file that verify.py would
    actually score must not sit under a heading calling it superseded.
    """
    verify_mod = _verify_module()
    text = INDEX.read_text(encoding="utf-8")
    for path in sorted(EVIDENCE.rglob("trials.jsonl")):
        if "smoke" in path.parts:
            continue
        if verify_mod._superseded_root(path, EVIDENCE) is not None:
            continue
        run = path.parent.name if path.parent.name != "ablation" else path.parents[1].name
        if run in text:
            idx = text.index(run)
            window = text[idx : idx + 300].upper()
            assert "SUPERSEDED" not in window, (
                f"{run} is a live run that verify.py would score, but the index "
                "calls it superseded"
            )


def test_the_index_does_not_claim_a_number_verify_will_not_print():
    """No percentage may appear in the index while verify.py exits 2."""
    import re
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, str(REPO / "verify.py")],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=300,
    )
    if proc.returncode != 2:
        pytest.skip("a run is committed; rate claims are permitted")
    rates = re.findall(r"\b\d{1,3}(?:\.\d+)?%", INDEX.read_text(encoding="utf-8"))
    assert not rates, (
        f"the evidence index quotes {rates} while verify.py exits 2, meaning it "
        "cannot reproduce any of them"
    )

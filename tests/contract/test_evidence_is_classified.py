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



def _lines_mentioning(text: str, name: str) -> list[str]:
    """Only the lines that actually name this run.

    A fixed character window was the first attempt and it was wrong: the index
    is a table, so a 300-character window starting at one row runs into the
    next one and reads its neighbour's status. Line scoping is what "the row
    for this run" actually means.
    """
    return [ln for ln in text.splitlines() if name in ln]


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
            rows = _lines_mentioning(text, d.name)
            assert rows, f"{d.name} is not mentioned in the index at all"
            assert any("SUPERSEDED" in r.upper() for r in rows), (
                f"{d.name} carries SUPERSEDED.json but no line naming it says so"
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
            rows = [r.upper() for r in _lines_mentioning(text, run)]
            assert rows, f"verify.py excludes {run} but the index never names it"
            assert any(
                "SUPERSEDED" in r or "NOT A RESULT" in r or "NO." in r for r in rows
            ), f"verify.py excludes {run} but the index does not say it is excluded"


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
        rows = [r.upper() for r in _lines_mentioning(text, run)]
        if rows:
            assert not any("SUPERSEDED" in r for r in rows), (
                f"{run} is a live run that verify.py would score, but a line "
                "naming it calls it superseded"
            )
            assert any("LIVE" in r or "YES" in r for r in rows), (
                f"{run} is scored by verify.py but the index does not present it "
                "as a result"
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

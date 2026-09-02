"""Everything this repository promises to a user must actually resolve.

``pyproject.toml`` declared ``pb = "paybound.cli:main"`` for days while
``paybound/cli.py`` did not exist, and the README documented ``pb demo``. Anyone
who ran ``pip install -e .`` got a ``pb`` command that raised ImportError, and a
documented command that cannot run is worse than an undocumented one — it tells
a reviewer the repository was never used the way it says it should be.

Same defect class as a disclosure constant with no consumer: it reads as working
until someone tries it. These tests try it.
"""

from __future__ import annotations

import importlib
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_every_declared_console_script_resolves():
    """The regression. A declared entry point must import and be callable."""
    scripts = _pyproject().get("project", {}).get("scripts", {})
    assert scripts, "no console scripts declared; this test guards them"
    for name, target in scripts.items():
        module_name, _, attr = target.partition(":")
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:  # pragma: no cover - the failure we guard
            pytest.fail(f"`{name}` declares {target!r} but {module_name} does not import: {exc}")
        assert hasattr(module, attr), f"`{name}`: {module_name} has no {attr!r}"
        assert callable(getattr(module, attr)), f"`{name}`: {target} is not callable"


def test_every_declared_dependency_is_importable():
    """A dependency listed but never imported is dead weight a reviewer installs."""
    deps = _pyproject().get("project", {}).get("dependencies", [])
    names = [re.split(r"[<>=!\[]", d)[0].strip() for d in deps]
    alias = {"python-dotenv": "dotenv"}
    for name in names:
        mod = alias.get(name, name.replace("-", "_"))
        try:
            importlib.import_module(mod)
        except ImportError:
            pytest.fail(f"declared dependency {name!r} is not importable as {mod!r}")


@pytest.mark.parametrize("cmd", ["status", "score", "verify"])
def test_each_cli_verb_runs(cmd):
    """Run them, rather than asserting the parser knows their names.

    ``verify`` exits 2 when no run is committed, which is correct and not a
    failure -- the repository is not yet claiming a number.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "paybound.cli", cmd],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
    )
    assert proc.returncode in (0, 2), f"pb {cmd} failed: {proc.stderr[:400]}"


def test_pb_demo_writes_a_self_contained_report(tmp_path):
    out = tmp_path / "report.html"
    proc = subprocess.run(
        [sys.executable, "-m", "paybound.cli", "demo", "--out", str(out), "--rows", "4"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[:400]
    assert out.is_file()
    doc = out.read_text(encoding="utf-8")
    for external in ("<script src=", '<link rel="stylesheet"', "http://"):
        assert external not in doc


def test_pb_demo_says_it_is_not_a_benchmark():
    """The demo routes at the oracle label. Presenting that as a measurement
    would be circular, so the page and the command must both say so."""
    proc = subprocess.run(
        [sys.executable, "-m", "paybound.cli", "demo", "--rows", "3"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        timeout=300,
    )
    assert proc.returncode == 0
    assert "not a benchmark" in proc.stdout.lower()
    report = (REPO_ROOT / "report.html").read_text(encoding="utf-8")
    assert "NOT a benchmark" in report or "not a measurement" in report


def test_every_command_the_readme_documents_exists():
    """The README is a promise. Check the commands it names are real."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"`pb (\w+)`", readme))
    from paybound.cli import main

    for verb in documented:
        proc = subprocess.run(
            [sys.executable, "-m", "paybound.cli", verb, "--help"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=120,
        )
        assert proc.returncode == 0, f"README documents `pb {verb}` but it does not run"
    assert callable(main)


DOCS = ("README.md", "AGENTS.md", "LIMITS.md", "PREREG.md", "docs/CITATIONS.md")


@pytest.mark.parametrize("doc", DOCS)
def test_every_path_a_document_references_exists(doc):
    """Documentation is a promise. These assert the promise resolves.

    Generalised from the README to every committed doc after AGENTS.md landed
    referencing four test files and two scripts: a map that points at files
    which have moved is worse than no map, because it is trusted.
    """
    path = REPO_ROOT / doc
    if not path.is_file():
        pytest.skip(f"{doc} not present")
    text = path.read_text(encoding="utf-8")

    patterns = (
        r"(scripts/[\w_]+\.py)",
        r"`(tests/[\w/]+\.py)`",
        r"`(paybound/[\w/]+\.py)`",
        r"\[`?([A-Z_]+\.md)`?\]",
    )
    for pattern in patterns:
        for rel in sorted(set(re.findall(pattern, text))):
            assert (REPO_ROOT / rel).is_file(), (
                f"{doc} references {rel}, which does not exist"
            )


@pytest.mark.parametrize("doc", ("README.md", "AGENTS.md"))
def test_every_pb_verb_a_document_names_actually_runs(doc):
    path = REPO_ROOT / doc
    if not path.is_file():
        pytest.skip(f"{doc} not present")
    text = path.read_text(encoding="utf-8")
    verbs = set(re.findall(r"`pb (\w+)`", text)) | set(re.findall(r"^pb (\w+)", text, re.M))
    for verb in sorted(verbs):
        proc = subprocess.run(
            [sys.executable, "-m", "paybound.cli", verb, "--help"],
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=120,
        )
        assert proc.returncode == 0, f"{doc} names `pb {verb}` but it does not run"


def test_every_file_line_citation_in_a_document_resolves():
    """A `path.py:NN` in prose is an instruction to look somewhere. It must be right.

    The locked demo spec put the hero beat's camera on
    ``core/policy/table.py:41`` -- which is ``__all__ = [``, an export list, and
    not the amount logic at all. The real function for the hero case
    (DUPLICATE_CHARGE) is ``full_payment`` in ``core/policy/amount.py``. Line 41
    of *that* file is ``line_price_difference``, a different clause entirely, so
    the citation was wrong twice over and would have been discovered on camera.

    Checked structurally: the file must exist, the line must exist, and when the
    citation names a function, that function must be defined at or near it.
    """
    import ast

    pattern = re.compile(r"([a-z_][a-z_0-9/]*\.py):(\d+)(?:\s+(\w+)\(\))?")
    for doc in (*DOCS, "IMPLEMENTATION_CONTRACT.md"):
        path = REPO_ROOT / doc
        if not path.is_file():
            continue
        for rel, lineno, func in pattern.findall(path.read_text(encoding="utf-8")):
            candidates = [REPO_ROOT / rel, REPO_ROOT / "paybound" / rel]
            target = next((c for c in candidates if c.is_file()), None)
            assert target is not None, f"{doc} cites {rel}, which does not exist"

            lines = target.read_text(encoding="utf-8").splitlines()
            n = int(lineno)
            assert 1 <= n <= len(lines), (
                f"{doc} cites {rel}:{n} but the file has {len(lines)} lines"
            )
            if not func:
                continue
            tree = ast.parse(target.read_text(encoding="utf-8"))
            defs = {
                node.name: node.lineno
                for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef)
            }
            assert func in defs, f"{doc} cites {rel}:{n} {func}(), which is not defined there"
            assert abs(defs[func] - n) <= 2, (
                f"{doc} points at {rel}:{n} for {func}(), which is at line {defs[func]}"
            )

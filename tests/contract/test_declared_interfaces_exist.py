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


def test_scripts_referenced_by_the_readme_exist():
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    for rel in set(re.findall(r"(scripts/[\w_]+\.py)", readme)):
        assert (REPO_ROOT / rel).is_file(), f"README references {rel}, which does not exist"
    for rel in set(re.findall(r"\[`?([A-Z_]+\.md)`?\]", readme)):
        assert (REPO_ROOT / rel).is_file(), f"README links {rel}, which does not exist"

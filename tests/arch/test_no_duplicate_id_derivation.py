"""Architectural guard: identifier derivation may exist in exactly one place.

The architecture review found four incompatible receipt/idempotency derivations
across seven design documents — on the one code path that can double-refund a
real customer. This test makes that class of divergence impossible to
reintroduce by accident.

It is deliberately a *test*, not a convention or a code-review habit, because a
convention does not fail CI at 2 am on day 6.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "paybound"
CANONICAL = PACKAGE / "ids.py"

# The prefixes only this module may spell.
PREFIX_LITERAL = re.compile(r"""["']pb[ir]_""")

# Constructing a header or a receipt anywhere other than ids.py.
SUSPICIOUS = [
    (re.compile(r"X-Refund-Idempotency\s*['\"]?\s*:\s*['\"]?\s*(?!\s*idem_key)"),
     "builds the idempotency header without calling ids.idem_key()"),
    (re.compile(r"\breceipt\s*=\s*(?!receipt\()(?!None)(?!_)"),
     "assigns a receipt without calling ids.receipt()"),
]


def _python_sources(root: Path) -> list[Path]:
    return [
        p
        for p in root.rglob("*.py")
        if ".venv" not in p.parts and "__pycache__" not in p.parts
    ]


def test_prefix_literals_appear_only_in_ids_module():
    offenders: list[str] = []
    for path in _python_sources(PACKAGE):
        if path == CANONICAL:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PREFIX_LITERAL.search(line):
                rel = path.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "The 'pbi_' / 'pbr_' prefixes are defined in paybound/ids.py and nowhere "
        "else. A second derivation is how a retry silently changes the request "
        "body and creates a second refund object.\n\nFound:\n  "
        + "\n  ".join(offenders)
    )


def test_scripts_and_tests_do_not_hand_roll_identifiers():
    """The spike scripts and the harness must use the canonical functions too —
    the KG-1 spike is where the idempotency contract is actually measured, so it
    has to measure the real thing."""
    offenders: list[str] = []
    for root in (REPO_ROOT / "scripts", REPO_ROOT / "tests"):
        if not root.exists():
            continue
        for path in _python_sources(root):
            if path.name in {"test_ids.py", Path(__file__).name}:
                continue
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1
            ):
                if PREFIX_LITERAL.search(line):
                    rel = path.relative_to(REPO_ROOT)
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Hand-rolled identifier literals outside paybound/ids.py:\n  "
        + "\n  ".join(offenders)
    )


def test_canonical_module_is_dependency_free():
    """ids.py must import nothing from the rest of the package.

    It is the bottom of the dependency graph. If it ever imports upward, an
    import cycle can make identifier derivation depend on configuration — and a
    configurable identifier is not retry-invariant.
    """
    source = CANONICAL.read_text(encoding="utf-8")
    bad = re.findall(r"^\s*(?:from|import)\s+paybound[.\s]", source, re.MULTILINE)
    assert not bad, f"ids.py must not import from paybound.*: {bad}"


def test_ids_module_exists_where_every_other_test_expects_it():
    assert CANONICAL.is_file(), "paybound/ids.py is the contract; it must exist"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))

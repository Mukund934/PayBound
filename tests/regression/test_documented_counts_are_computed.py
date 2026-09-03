"""Every quantity a document asserts must be recomputable from the repository.

Generalised from the defect that reached published evidence: a provenance record
named a campaign, the campaign did not exist, and nothing failed. The specific
fix was a test that imports the module the record names. This is the general
one -- a number in prose is a claim, and a claim nobody can check is how the
gap between what a repository says and what it contains opens up.

It also catches ordinary staleness, which is how it found ``AGENTS.md`` still
saying "~1100 tests" when the suite had grown past 1400. A doc that undercounts
by a third is not lying, but it is evidence nobody re-read it, and a reviewer
who checks one number and finds it stale will not check the second.

Two registries below. ``COMPUTED`` holds claims with a derivation. ``EXTERNAL``
holds claims that cannot be computed here -- a provider's quota, a
pre-registered budget -- and each names why. A number in a document that is in
neither fails the build, so a new claim must be registered before it can be
made.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
DOCS = ("README.md", "LIMITS.md", "AGENTS.md", "PREREG.md")


# --------------------------------------------------------------------------
# derivations
# --------------------------------------------------------------------------


def _c1_assertions() -> int:
    from tests.security.test_c1_scripted_hostile_arm import DEPTH1, DEPTH2

    return len(DEPTH1) + len(DEPTH2)


def _sweep_variants() -> int:
    from paybound.harness.sweep_r import expand

    return len(expand())


def _corpus_items() -> int:
    return sum(
        1
        for name in ("benign.jsonl", "attack.jsonl")
        for line in (REPO / "corpus" / name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    )


def _reason_codes() -> int:
    from paybound.core.types import ReasonCode

    return len(list(ReasonCode))


def _fault_injections() -> int:
    from tests.fault.test_i05_fails_closed import injection_total

    return injection_total()


def _collected_tests() -> int:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=600,
    )
    total = 0
    for line in proc.stdout.splitlines():
        m = re.match(r"^tests/.*: (\d+)$", line.strip())
        if m:
            total += int(m.group(1))
    assert total > 0, "could not count collected tests"
    return total


COMPUTED = {
    "648": _c1_assertions,
    "150 variants": _sweep_variants,
    "150 items": _corpus_items,
    "197 injections": _fault_injections,
}

EXTERNAL = {
    # Provider facts and pre-registered plans. Not computable here, and each
    # says where it comes from rather than standing as a bare number.
    "20 requests": "Gemini free tier, GenerateRequestsPerDayPerProjectPerModel-FreeTier",
    "156 calls": "PREREG.md budget for a campaign that has not run",
    "150 request": "PREREG.md variant cap, pre-committed",
}


@pytest.mark.parametrize("claim", sorted(COMPUTED))
def test_each_computed_claim_matches_its_derivation(claim):
    expected = COMPUTED[claim]()
    number = int(re.match(r"(\d+)", claim).group(1))
    assert number == expected, (
        f"documents claim {claim!r} but the repository computes {expected}"
    )


@pytest.mark.parametrize("claim", sorted(COMPUTED))
def test_each_computed_claim_actually_appears_in_a_document(claim):
    """A registry entry for a claim nobody makes is dead weight that reads as coverage."""
    number = re.match(r"(\d+)", claim).group(1)
    found = any(
        number in (REPO / doc).read_text(encoding="utf-8")
        for doc in DOCS
        if (REPO / doc).is_file()
    )
    assert found, f"{claim!r} is registered but appears in no document"


def test_the_test_count_in_agents_md_is_not_stale():
    """Stated as a bound, because an exact count in prose drifts every commit.

    The suite may not have fewer tests than AGENTS.md advertises, and may not
    have grown so far past it that the figure misleads. Both directions matter:
    undercounting reads as nobody re-reading the file, and overcounting is a
    claim about coverage that does not exist.
    """
    agents = (REPO / "AGENTS.md").read_text(encoding="utf-8")
    m = re.search(r"~(\d[\d,]*) tests", agents)
    assert m, "AGENTS.md no longer states an approximate test count"
    claimed = int(m.group(1).replace(",", ""))
    actual = _collected_tests()
    assert claimed <= actual, (
        f"AGENTS.md advertises ~{claimed} tests; only {actual} are collected"
    )
    assert actual <= claimed * 1.25, (
        f"AGENTS.md says ~{claimed} tests but {actual} are collected; update it"
    )


def test_the_nine_clause_table_is_actually_nine():
    for doc in DOCS:
        path = REPO / doc
        if path.is_file() and "nine" in path.read_text(encoding="utf-8").lower():
            assert _reason_codes() == 9
            return
    pytest.skip("no document says 'nine'")


# --------------------------------------------------------------------------
# the part that makes the registry mandatory
# --------------------------------------------------------------------------

# Nouns denoting a countable artifact this repository contains. A number in
# front of one of these is a checkable claim, so it must be registered.
_ARTIFACT_NOUN = re.compile(
    r"(\d[\d,]*)[- ](assertion|injection|variant|clause)s?\b", re.I
)


@pytest.mark.parametrize("doc", DOCS)
def test_no_document_makes_an_unregistered_countable_claim(doc):
    """A new number about an artifact must be registered before it can be made.

    This is the rule the SWEEP-R defect would have had to break twice over
    rather than once, and it is deliberately narrow: only nouns naming something
    this repository actually contains and can count. Widening it to every number
    in prose would produce false positives on dates, prices and version strings,
    and a check that cries wolf gets suppressed rather than fixed.
    """
    path = REPO / doc
    if not path.is_file():
        pytest.skip(f"{doc} not present")
    text = path.read_text(encoding="utf-8")

    known = {re.match(r"(\d+)", k).group(1) for k in COMPUTED}
    known |= {re.match(r"(\d+)", k).group(1) for k in EXTERNAL}

    for number, noun in _ARTIFACT_NOUN.findall(text):
        assert number.replace(",", "") in known, (
            f"{doc} claims {number} {noun}s, which is not in COMPUTED or EXTERNAL. "
            "Register it with a derivation, or say where the figure comes from."
        )


def test_the_scanner_is_not_vacuous():
    """Plant an unregistered claim and confirm the pattern catches it."""
    found = _ARTIFACT_NOUN.findall("the suite runs 9999 assertions across the board")
    assert found == [("9999", "assertion")]
    known = {re.match(r"(\d+)", k).group(1) for k in COMPUTED} | {
        re.match(r"(\d+)", k).group(1) for k in EXTERNAL
    }
    assert "9999" not in known


def test_the_readme_test_count_row_is_bounded_not_frozen():
    """The README table has a Tests row and it drifted twice.

    It read "268 passing" against a suite of 1531, then "1531 collected" against
    1547 sixteen commits later. A literal in a table nobody recomputes goes
    stale on the next commit, and it sits in the row whose whole purpose is to
    say "check me".

    Stated approximately and bounded here, the same way AGENTS.md is. Exact is
    not achievable in prose; honest is.
    """
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    row = re.search(r"\|\s*Tests\s*\|([^|]+)\|", readme)
    assert row, "the README no longer has a Tests row"
    m = re.search(r"~?([\d,]+)", row.group(1))
    assert m, f"the Tests row states no number: {row.group(1)!r}"
    claimed = int(m.group(1).replace(",", ""))
    actual = _collected_tests()
    assert claimed <= actual, (
        f"README advertises {claimed} tests; only {actual} are collected"
    )
    assert actual <= claimed * 1.15, (
        f"README says ~{claimed} but {actual} are collected; update the row"
    )

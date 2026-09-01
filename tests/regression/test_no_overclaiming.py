"""No document in this repository may make a claim the evidence cannot carry.

The architecture lock lists phrases that must never appear as assertions. This
enforces that, and it is scheduled to run on every commit rather than as a
final-day grep, because a forbidden phrase written on day three is a phrase
somebody has to notice on day nine.

**Quoted mentions are not claims.** `LIMITS.md` §11 and the implementation
contract both *enumerate* the banned phrases in order to forbid them — naming a
phrase to rule it out is the opposite of asserting it. A naive substring scan
flags those and fires on the two documents most committed to not overclaiming,
which is how a real check gets switched off for being noisy. The rule is
therefore: **an occurrence outside quotation marks is a claim; one inside them
is a mention.**
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# From the lock's "Also forbidden" list. Each is a claim this project cannot
# support, and several killed earlier versions of it.
FORBIDDEN = (
    "novel architecture",
    "first firewall",
    "provably secure",
    "solved prompt injection",
    "100% blocked",
    "nobody measures false refusals",
    "strictly stronger than",
    "this is action-selector",
    "unbreakable",
    "cannot be bypassed",
)

# Spans delimited by a pair of quote characters. Straight, curly and backtick.
# Matched as REGIONS rather than by inspecting adjacent characters: a first
# attempt looked three characters either side of the phrase and missed
# `"strictly stronger than Agent-Sentry"`, where the closing quote sits past the
# end of the phrase. What matters is whether the occurrence falls inside a
# quoted span, not whether a quote happens to touch it.
# Curly quotes are written as escapes: ruff flags the literal characters as
# ambiguous, and it is right to -- a curly quote that looks like a backtick in a
# regex is exactly the sort of thing nobody spots in review.
_QUOTE_SPAN = re.compile(
    "\"[^\"]*\""
    "|'[^']*'"
    "|“[^”]*”"  # curly double
    "|‘[^’]*’"  # curly single
    "|`[^`]*`"
)


def _markdown_files() -> list[Path]:
    return [
        p
        for p in REPO_ROOT.rglob("*.md")
        if not any(x in p.parts for x in (".venv", ".git", "node_modules"))
    ]


def _is_mention(line: str, phrase: str) -> bool:
    """True when every occurrence is inside a quoted span — named, not asserted.

    A claim reads ``PayBound is a novel architecture``. A prohibition reads
    ``the words "novel architecture" do not appear``. One bare occurrence makes
    the whole line a claim, so this is conjunctive over occurrences.
    """
    quoted = [m.span() for m in _QUOTE_SPAN.finditer(line)]
    lowered = line.lower()
    idx = lowered.find(phrase)
    while idx != -1:
        end = idx + len(phrase)
        if not any(start <= idx and end <= stop for start, stop in quoted):
            return False
        idx = lowered.find(phrase, idx + 1)
    return True


@pytest.mark.parametrize("phrase", FORBIDDEN)
def test_no_document_asserts_a_forbidden_claim(phrase: str):
    offenders: list[str] = []
    for path in _markdown_files():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
        ):
            if phrase in line.lower() and not _is_mention(line, phrase):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {line.strip()[:100]}")
    assert not offenders, (
        f"{phrase!r} is asserted, not merely named:\n  " + "\n  ".join(offenders)
    )


def test_the_prohibition_lists_themselves_are_not_flagged():
    """The check must tolerate the documents that enumerate the banned phrases.

    If this fails, the scan has become the kind that gets disabled for crying
    wolf on correct work.
    """
    limits = REPO_ROOT / "LIMITS.md"
    assert limits.is_file()
    text = limits.read_text(encoding="utf-8")
    assert "novel architecture" in text.lower(), "LIMITS should still name them"
    for line in text.splitlines():
        for phrase in FORBIDDEN:
            if phrase in line.lower():
                assert _is_mention(line, phrase), (
                    f"LIMITS.md names {phrase!r} unquoted, which reads as a claim: {line.strip()}"
                )


def test_the_scan_would_catch_a_real_claim(tmp_path):
    """A gate that cannot fail is decoration. Prove this one fires."""
    bad = "PayBound is a novel architecture that is provably secure."
    assert not _is_mention(bad, "novel architecture")
    assert not _is_mention(bad, "provably secure")

    good = 'The words "novel architecture" and "provably secure" do not appear.'
    assert _is_mention(good, "novel architecture")
    assert _is_mention(good, "provably secure")


def test_readme_concedes_prior_art_before_any_result():
    """The lock's ordering rule: concessions come before claims.

    Having a reviewer make the prior-art comparison is fatal; volunteering it is
    the strongest move available.
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    prior_art = readme.lower().index("prior art, conceded by name")
    measured = readme.lower().index("what is actually measured")
    assert prior_art < measured, "prior art must appear before any measurement"
    for name in ("pact", "camel", "fides", "aegis", "pace", "ap2"):
        assert name in readme.lower(), f"{name} is not conceded by name"


def test_readme_states_what_is_not_measured():
    """And states it as a count that goes stale loudly.

    This used to assert the literal heading "Not yet measured", which was a
    string match on a heading that had to change the moment anything *was*
    measured -- so it enforced the wording and not the honesty. The number is
    the better test: it cannot drift quietly, and it fails the build on the one
    occasion that matters, which is a run landing and the README still
    describing the state before it.
    """
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "LIMITS.md" in readme
    assert "still unmeasured" in readme.lower(), (
        "the README must say what it has not measured"
    )

    corpus_total = sum(
        1
        for name in ("benign.jsonl", "attack.jsonl")
        if (REPO_ROOT / "corpus" / name).is_file()
        for line in (REPO_ROOT / "corpus" / name)
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    )
    measured = {
        json.loads(line)["item_id"]
        for path in (REPO_ROOT / "evidence").rglob("trials.jsonl")
        if "ablation" not in path.parts
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    remaining = corpus_total - len(measured)
    assert f"{remaining} items" in readme, (
        f"{remaining} of {corpus_total} corpus items are unmeasured and the README "
        "does not say so. Update it, or the next run silently publishes a stale "
        "denominator."
    )


def test_every_arxiv_id_cited_is_recorded_as_verified():
    """No arXiv identifier may appear without a verification record.

    A wrong arXiv number is a fabricated citation even when the paper is real.
    """
    citations = (REPO_ROOT / "docs" / "CITATIONS.md").read_text(encoding="utf-8")
    pattern = re.compile(r"arXiv:(\d{4}\.\d{4,5})", re.I)
    cited: set[str] = set()
    for path in _markdown_files():
        if path.name == "CITATIONS.md":
            continue
        cited |= set(pattern.findall(path.read_text(encoding="utf-8", errors="ignore")))
    missing = {c for c in cited if c not in citations}
    assert not missing, (
        f"arXiv ids cited but not recorded as verified in CITATIONS.md: {sorted(missing)}"
    )

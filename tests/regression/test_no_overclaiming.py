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
        # A superseded run is not measured evidence. It is kept and readable,
        # but verify.py will not reproduce a number from it, so the README may
        # not count it as measured either -- the two must agree or one of them
        # is lying.
        and not (path.parent / "SUPERSEDED.json").is_file()
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


def test_no_document_denies_human_labelling_without_qualifying_it():
    """The corpus discloses its own provenance. The prose must not contradict it.

    Two flagship documents said "No LLM judge, no human labelling" beside the
    ground-truth claim. That is true of *refund existence*, which Razorpay
    settles. It is false of the routing oracle, which is authored by hand and
    says so in every corpus item's own ``origin`` field --

        {"by": "builder", "kind": "authored"}

    -- so a reviewer opening ``corpus/benign.jsonl``, which is committed and one
    click away, catches the contradiction in about thirty seconds. The project
    disclosed the authoring in LIMITS and PREREG and then overstated it in the
    two documents most likely to be read first.

    A document may still make the claim. It must qualify it in the same
    document, because a reader who stops after the headline is the reader this
    guard exists for.
    """
    import json

    corpus = REPO_ROOT / "corpus" / "benign.jsonl"
    if corpus.is_file():
        first = json.loads(corpus.read_text(encoding="utf-8").splitlines()[0])
        assert first["origin"]["kind"] == "authored", (
            "the corpus no longer records authored provenance; this guard needs rewriting"
        )

    denial = re.compile(r"no human label(l)?ing", re.I)
    for doc in ("README.md", "IMPLEMENTATION_CONTRACT.md", "LIMITS.md", "PREREG.md"):
        path = REPO_ROOT / doc
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if not denial.search(text):
            continue
        qualified = any(
            phrase in text.lower()
            for phrase in ("authored by hand", "authored by the builder", "hand-authored")
        )
        assert qualified, (
            f"{doc} says 'no human labelling' but never states that the routing "
            "oracle is authored by hand, which the corpus itself records"
        )


def test_the_prereg_predates_every_committed_trial():
    """Pre-registration is only worth something if the order is checkable.

    Asserted against git rather than against a date written in prose, because a
    date in prose is a claim and a commit order is a fact.
    """
    import subprocess

    def _added(path: str) -> str | None:
        out = subprocess.run(
            ["git", "log", "--diff-filter=A", "--format=%ct", "--", path],
            capture_output=True, text=True, cwd=REPO_ROOT, timeout=120,
        ).stdout.split()
        return out[-1] if out else None

    prereg = _added("PREREG.md")
    if prereg is None:
        pytest.skip("not a git checkout")

    trials = [
        p.relative_to(REPO_ROOT).as_posix()
        for p in (REPO_ROOT / "evidence").rglob("trials.jsonl")
        if "smoke" not in p.parts and not (p.parent / "SUPERSEDED.json").is_file()
    ]
    for t in trials:
        added = _added(t)
        if added is None:
            continue
        assert int(prereg) < int(added), (
            f"PREREG.md was committed after {t}, so it did not pre-register anything"
        )


def test_no_document_names_sweep_r_as_the_adversary_while_it_has_not_run():
    """The fourth instance, given a consumer.

    README.md's summary table credited the run's adversary to SWEEP-R while
    INCIDENTS.md described that exact substitution as the repository's
    highest-severity defect, ATTACKER_PROVENANCE stamped BUILT_NOT_RUN, and
    verify.py printed the real adversary. The remediation commit edited that
    file by 69 lines and walked past the row.

    A sibling test already fails the build if ``attacker_stamp()`` contains
    "sweep" while the status is not RUN. The README asserted in prose the exact
    string the suite fails the build for in code -- and no test read prose.
    This one does.
    """
    from paybound.agent.models import ATTACKER_PROVENANCE

    if ATTACKER_PROVENANCE["sweep_r_status"] == "RUN":
        return

    adversary_line = re.compile(r"^\s*\|\s*Adversary\s*\|(.+)\|\s*$", re.I | re.M)
    for doc in ("README.md", "LIMITS.md", "AGENTS.md", "IMPLEMENTATION_CONTRACT.md"):
        path = REPO_ROOT / doc
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for cell in adversary_line.findall(text):
            assert "sweep-r" not in cell.lower() or "not_run" in cell.lower(), (
                f"{doc} names SWEEP-R as the adversary, but its status is "
                f"{ATTACKER_PROVENANCE['sweep_r_status']}. That is the claim "
                "INCIDENTS.md already retracted."
            )


def test_the_readme_names_the_real_adversary_somewhere():
    """Retracting the wrong answer is not the same as giving the right one.

    After the bad row was found, ``grep -c corpus_attack_items README.md``
    returned 0 -- the README's only answer to "who was the adversary" was the
    wrong one, and deleting it would have left no answer at all.
    """
    from paybound.agent.models import ATTACKER_PROVENANCE

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert ATTACKER_PROVENANCE["adversary_of_record"] in readme, (
        "the README never names the adversary the trials actually ran against"
    )


def test_limits_does_not_undercount_the_refunds_that_exist():
    """LIMITS said "exactly one refund exists" while three did.

    It understated the project's own headline result by Rs 2,499, in the
    document the README points at as the honesty backstop. Counted from
    evidence rather than from prose, because prose is what drifted.
    """
    import re as _re

    ids = set()
    for f in (REPO_ROOT / "evidence").rglob("*.json"):
        text = f.read_text(encoding="utf-8", errors="ignore")
        ids |= set(_re.findall(r"rfnd_[A-Za-z0-9]+", text))
    if not ids:
        pytest.skip("no refund objects in evidence")

    limits = (REPO_ROOT / "LIMITS.md").read_text(encoding="utf-8")
    assert "Exactly one refund exists" not in limits, (
        f"LIMITS claims one refund; {len(ids)} exist in evidence/"
    )
    for ident in ids:
        assert ident in limits, (
            f"{ident} exists in evidence/ but LIMITS.md does not account for it"
        )

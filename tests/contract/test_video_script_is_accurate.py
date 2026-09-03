"""The run sheet is a document, so it gets a consumer like every other one.

``VIDEO_SCRIPT.md`` is the highest-stakes prose in the repository: it is read
aloud, on camera, once, with no chance to correct it. Every other document here
that lacked a mechanical consumer eventually drifted — five times — and this one
would drift in front of a judge.

So every identifier it quotes must exist in committed evidence, every figure
must match what ``verify.py`` computes, and every command must be one that
actually runs.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "VIDEO_SCRIPT.md"


@pytest.fixture(scope="module")
def script() -> str:
    if not SCRIPT.is_file():
        pytest.skip("VIDEO_SCRIPT.md not present")
    return SCRIPT.read_text(encoding="utf-8")


def _evidence_blob() -> str:
    parts = []
    for f in (REPO / "evidence").rglob("*.json"):
        parts.append(f.read_text(encoding="utf-8", errors="ignore"))
    for f in (REPO / "evidence").rglob("*.jsonl"):
        parts.append(f.read_text(encoding="utf-8", errors="ignore"))
    return "\n".join(parts)


def test_every_identifier_read_aloud_exists_in_evidence(script):
    """No id gets spoken on camera that is not in a committed artifact."""
    blob = _evidence_blob()
    idents = set(re.findall(r"\b(?:rfnd|pay|pbr)_[A-Za-z0-9]+", script))
    assert idents, "the script quotes no identifiers at all"
    for ident in idents:
        assert ident in blob, f"{ident} is read aloud but exists in no evidence file"


def test_the_item_count_matches_the_committed_trials(script):
    """'Sixteen items of a hundred and fifty' has to be sixteen."""
    ids = set()
    for p in (REPO / "evidence").rglob("trials.jsonl"):
        if "ablation" in p.parts or "smoke" in p.parts:
            continue
        if (p.parent / "SUPERSEDED.json").is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                ids.add(json.loads(line)["item_id"])
    assert f"{len(ids)} of 150 items" in script or f"{len(ids)} items" in script, (
        f"{len(ids)} items are committed; the script does not say so"
    )


def test_the_ablation_figures_match_verify(script):
    """The one comparative claim in the script, checked against the verifier."""
    proc = subprocess.run(
        [sys.executable, str(REPO / "verify.py")],
        capture_output=True, text=True, cwd=REPO, timeout=300,
    )
    if proc.returncode != 0:
        pytest.skip("no verified run to check against")
    out = proc.stdout

    m = re.search(r"arm2_allow\s+([\d.]+)% \((\d+)/(\d+)\)", out)
    n = re.search(r"arm1a_allow\s+([\d.]+)% \((\d+)/(\d+)\)", out)
    assert m and n, "verify.py no longer prints the ablation contrast"
    assert f"{m.group(2)}/{m.group(3)}" in script, (
        f"script must quote arm2 as {m.group(2)}/{m.group(3)}"
    )
    assert f"{n.group(2)}/{n.group(3)}" in script, (
        f"script must quote arm1a as {n.group(2)}/{n.group(3)}"
    )

    prevented = re.search(r"prevented\s+(\d+) \(", out)
    assert prevented, "verify.py no longer reports prevented ALLOWs"
    n = prevented.group(1)

    # Positional, not "appears somewhere in the file". The loose version was
    # satisfied while the script said "prevented seven approvals", because the
    # digit 4 still occurred elsewhere on the page. A number that gets read
    # aloud has to be checked where it is read.
    words = {"1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
             "6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten"}
    allowed = {n, words.get(n, n)}

    spoken = re.findall(r"prevented\s+(\w+)\s+approv", script, re.I)
    # Digits only in the table form: "(\w+) prevented" also matches the prose
    # "the precondition check prevented", and "check" is not a count.
    tabled = re.findall(r"(\d+)\s+prevented", script, re.I)
    assert spoken or tabled, "the script no longer says how many ALLOWs were prevented"
    for found in spoken + tabled:
        assert found.lower() in allowed, (
            f"the script says {found!r} ALLOWs were prevented; verify.py says {n}"
        )


def test_the_rule_of_three_ceiling_quoted_is_the_one_printed(script):
    """'ceiling 49.9%' must be a number verify.py actually prints."""
    proc = subprocess.run(
        [sys.executable, str(REPO / "verify.py")],
        capture_output=True, text=True, cwd=REPO, timeout=300,
    )
    if proc.returncode != 0:
        pytest.skip("no verified run")
    printed = set(re.findall(r"ub ([\d.]+)%", proc.stdout))
    for quoted in re.findall(r"ceiling \*\*([\d.]+)%\*\*|ceiling ([\d.]+)%", script):
        value = next(v for v in quoted if v)
        assert value in printed, (
            f"the script says ceiling {value}%, which verify.py does not print. "
            f"It prints: {sorted(printed)}"
        )


def test_every_command_in_the_script_resolves(script):
    """A command read aloud must exist. `--help` is enough to prove it."""
    for verb in sorted(set(re.findall(r"^pb (\w+)", script, re.M))):
        proc = subprocess.run(
            [sys.executable, "-m", "paybound.cli", verb, "--help"],
            capture_output=True, text=True, cwd=REPO, timeout=120,
        )
        assert proc.returncode == 0, f"the script runs `pb {verb}`, which does not exist"

    for rel in sorted(set(re.findall(r"python3? (scripts/[\w_]+\.py)", script))):
        assert (REPO / rel).is_file(), f"the script runs {rel}, which does not exist"

    for flag_line in re.findall(r"python scripts/execute_one\.py ([^\n]+)", script):
        for flag in re.findall(r"--([a-z-]+)", flag_line):
            src = (REPO / "scripts" / "execute_one.py").read_text(encoding="utf-8")
            assert f'"--{flag}"' in src, (
                f"the script passes --{flag} to execute_one.py, which does not accept it"
            )


_NEGATION = re.compile(
    r"\b(not|never|does not|doesn't|no claim|denies|without|refus)", re.I
)


def _sentences(text: str) -> list[str]:
    """Split on sentence enders, keeping enough context to see a negation."""
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n\n+", text) if s.strip()]


def test_the_script_asserts_none_of_the_claims_the_repo_forbids(script):
    """Checked per sentence, because the script *disclaims* these on purpose.

    A substring scan fails here and would deserve to: the strongest line in the
    close is "What I am not claiming: that this prevents prompt injection."
    Flagging that would be the fourth time this repository produced a false
    positive by grepping source that discusses the property it is scanning for.
    The unit that carries the assertion is the sentence, so that is the unit.
    """
    body = script.split("## Do not say")[0]
    forbidden = ("prevents prompt injection", "100% blocked", "provably secure",
                 "cannot be bypassed", "guaranteed")

    for sentence in _sentences(body):
        low = sentence.lower()
        for phrase in forbidden:
            if phrase in low and not _NEGATION.search(sentence):
                pytest.fail(
                    f"the script asserts {phrase!r} without negation: {sentence!r}"
                )


def test_the_negation_check_is_not_vacuous():
    """It must still catch a bare assertion, or it protects nothing."""
    bare = "PayBound prevents prompt injection."
    assert not _NEGATION.search(bare)
    disclaimed = "I am not claiming that this prevents prompt injection."
    assert _NEGATION.search(disclaimed)


def test_sweep_r_is_only_ever_mentioned_as_unrun(script):
    from paybound.agent.models import ATTACKER_PROVENANCE

    if ATTACKER_PROVENANCE["sweep_r_status"] == "RUN":
        return
    for line in script.split("## Do not say")[0].splitlines():
        if "SWEEP-R" in line:
            assert re.search(r"\bunrun\b|\bnot\b|\bnever\b", line, re.I), (
                f"the script mentions SWEEP-R without saying it is unrun: {line!r}"
            )

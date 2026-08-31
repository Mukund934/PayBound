"""The corpus seal covers what a reviewer will actually download.

A seal exists to make one claim checkable: *these are the exact items, and their
labels were fixed before anything was scored.* Two ways that claim can quietly
become false, both of which are tested here:

1. **The seal hashes something other than the file.** The first version hashed
   the in-memory string while ``write_text`` applied universal-newline
   translation, so on Windows the file on disk was CRLF and the seal covered the
   LF version. The seal did not match its own artifact, and it would have
   verified on Linux CI while failing on the machine that produced it.
2. **The attack payloads predate the seal.** If attacks are authored first, the
   oracle labels can be tuned to them and the whole benchmark is circular.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "corpus"
BENIGN = CORPUS / "benign.jsonl"
STATES = CORPUS / "fixtures" / "states.jsonl"
SEAL = CORPUS / "SEAL.json"

pytestmark = pytest.mark.skipif(
    not SEAL.is_file(), reason="corpus not built yet (run scripts/build_corpus.py --benign)"
)


def _seal() -> dict:
    return json.loads(SEAL.read_text(encoding="utf-8"))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_seal_matches_the_bytes_on_disk():
    """The regression. Hash the file, not a string that resembled it."""
    seal = _seal()
    assert seal["benign_jsonl_sha256"] == _sha(BENIGN)
    assert seal["states_jsonl_sha256"] == _sha(STATES)


def test_corpus_files_are_lf_only():
    """Cross-platform seal stability.

    A CRLF corpus hashes differently on Windows and Linux, so the seal would
    verify in one place and fail in the other for reasons that have nothing to
    do with the corpus content.
    """
    for path in (BENIGN, STATES):
        assert b"\r\n" not in path.read_bytes(), f"{path.name} contains CRLF"


def test_seal_pins_the_generator_not_only_the_output():
    """Two corpora with identical items but different generators are not the
    same experiment, and a reviewer must be able to tell."""
    gen = _seal()["generator_sha256"]
    for name in ("scenarios.py", "prose.py", "build.py", "types.py"):
        assert name in gen and len(gen[name]) == 64
    assert gen["types.py"] == _sha(REPO_ROOT / "paybound" / "core" / "types.py")


def test_seal_pins_the_policy_hash():
    from paybound.core.policy.table import POLICY_SHA256

    assert _seal()["policy_sha256"] == POLICY_SHA256


def test_the_corpus_declares_two_ground_truths_and_no_disposition():
    """``corpus_gen`` may not import ``core/policy``, so it cannot know the
    disposition. A corpus shipping expected APPROVE/DENY would have read the
    defence it is meant to be independent of."""
    declares = _seal()["declares"]
    assert "oracle" in declares and "claim_is_true" in declares
    assert "NOT declared" in declares["disposition"]

    for line in BENIGN.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        assert "oracle" in item and "claim_is_true" in item
        for forbidden in ("expected_decision", "disposition", "expected_outcome"):
            assert forbidden not in item, (
                f"{item['item_id']} ships a disposition; the corpus has read the policy"
            )


def test_composition_is_the_locked_one():
    from collections import Counter

    items = [json.loads(x) for x in BENIGN.read_text(encoding="utf-8").splitlines()]
    assert len(items) == 80
    strata = Counter(i["stratum"] for i in items)
    assert strata == {
        "DUPLICATE_CHARGE": 10,
        "PRICE_MISMATCH": 10,
        "NOT_DELIVERED": 10,
        "CANCELLED_IN_WINDOW": 10,
        "TESTIMONIAL": 20,
        "DISCORDANT": 15,
        "UNDERSPEC": 5,
    }
    assert sum(1 for i in items if not i["claim_is_true"]) == 15


def test_every_item_id_is_unique_and_every_anchor_survives():
    items = [json.loads(x) for x in BENIGN.read_text(encoding="utf-8").splitlines()]
    ids = [i["item_id"] for i in items]
    assert len(set(ids)) == len(ids)
    for i in items:
        assert i["anchor"] in i["prose"], f"{i['item_id']}: the grammar mutated the anchor"


def test_every_item_has_a_fixture():
    items = [json.loads(x) for x in BENIGN.read_text(encoding="utf-8").splitlines()]
    fixtures = {
        json.loads(x)["scenario_id"] for x in STATES.read_text(encoding="utf-8").splitlines()
    }
    missing = {i["scenario_id"] for i in items} - fixtures
    assert not missing, f"items with no trusted-state fixture: {sorted(missing)}"


def test_the_corpus_regenerates_byte_for_byte():
    """The reproducibility claim, executed rather than asserted.

    A reviewer regenerates all 80 messages on a clean clone with no API key and
    gets the same bytes. No model-authored corpus could have this property, and
    it is the reason the generator is a committed slot grammar.
    """
    before = BENIGN.read_bytes()
    states_before = STATES.read_bytes()
    seal_before = SEAL.read_bytes()
    try:
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "build_corpus.py"), "--benign"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=180,
        )
        assert proc.returncode == 0, proc.stderr
        assert BENIGN.read_bytes() == before, "regeneration changed the corpus bytes"
        assert STATES.read_bytes() == states_before, "regeneration changed the fixtures"

        # The seal's CONTENT reproduces; only `sealed_at` moves. Asserting the
        # whole file were byte-identical would be asserting that a timestamp
        # does not advance, which is a false claim about a true property.
        after = json.loads(SEAL.read_text(encoding="utf-8"))
        original = json.loads(seal_before.decode("utf-8"))
        for key in set(original) | set(after):
            if key == "sealed_at":
                continue
            assert after[key] == original[key], f"regeneration changed {key}"
    finally:
        # Restore, so running the suite never leaves the committed seal with a
        # different timestamp than the one the attack seal back-references.
        with SEAL.open("wb") as fh:
            fh.write(seal_before)


def test_attacks_are_refused_while_the_benign_seal_is_missing(tmp_path):
    """Ordering is enforced by the tool, not by discipline.

    Authoring attacks before the seal would let the oracle labels be tuned to
    them. The script refuses rather than relying on the author remembering.
    """
    script = (REPO_ROOT / "scripts" / "build_corpus.py").read_text(encoding="utf-8")
    assert "REFUSED: corpus/SEAL.json does not exist" in script
    assert "no longer matches SEAL.json" in script


@pytest.mark.skipif(
    not (CORPUS / "SEAL.attack.json").is_file(), reason="attacks not built yet"
)
def test_the_attack_seal_back_references_the_benign_seal():
    """Git history proves the ordering; the back-reference makes it checkable
    from the artifacts alone."""
    attack_seal = json.loads((CORPUS / "SEAL.attack.json").read_text(encoding="utf-8"))
    ref = attack_seal["back_reference"]
    # Content identity, not the seal file's bytes: SEAL.json timestamps itself,
    # so its byte hash moves on every rebuild while the corpus does not.
    assert ref["benign_jsonl_sha256"] == _seal()["benign_jsonl_sha256"] == _sha(BENIGN)
    assert ref["benign_policy_sha256"] == _seal()["policy_sha256"]
    assert attack_seal["families_declared_zero_by_construction"] == [
        "attack_A",
        "attack_H",
        "attack_P",
    ]

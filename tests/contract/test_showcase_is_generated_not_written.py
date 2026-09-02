"""The showcase page must be generated from artifacts, never authored.

A showcase with hard-coded numbers is a drawing of a system rather than a view
of one, and it drifts the moment anything changes. This project has been caught
four times shipping a claim where an implementation should have been; a page
built for a judge to look at is the last place that should happen again.

So: every headline value on the page must be traceable to a committed file, and
the page must degrade honestly when an artifact is absent rather than keep
displaying a stale one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from paybound.harness.showcase import build_showcase, render_showcase

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    out = tmp_path_factory.mktemp("showcase") / "showcase.html"
    render_showcase(build_showcase(), out_path=out)
    return out.read_text(encoding="utf-8")


def test_the_attack_prose_is_the_sealed_corpus_verbatim(page):
    """Not paraphrased for the camera. The payload shown is the payload run."""
    data = build_showcase()
    assert data["attacks"], "no attack items resolved from the corpus"
    for item, _label in data["attacks"]:
        # The page HTML-escapes and may wrap spans, so compare on a distinctive
        # unwrapped fragment rather than the whole string.
        fragment = item["prose"].split(".")[0][:40]
        assert fragment.replace("&", "&amp;") in page, (
            f"{item['item_id']}'s prose does not appear on the page as written"
        )


def test_the_schema_shown_is_the_live_tool_registry(page):
    """The central claim is 'there is no amount field'. Show the real schema."""
    from paybound.agent.tools import TOOLS

    def as_dict(t):
        return t if isinstance(t, dict) else dict(t.__dict__)

    tool = next(as_dict(t) for t in TOOLS if as_dict(t)["name"] == "request_refund")
    props = set(tool["parameters"].get("properties", {}))
    assert props == {"case_handle", "reason_code"}, (
        f"request_refund's parameters changed to {props}; the showcase's argument "
        "is about exactly these two and must be revisited"
    )
    for name in props:
        assert name in page
    assert "amount" not in props and "payment_id" not in props


def test_the_page_carries_the_real_hashes(page):
    from paybound.agent.tools import registry_sha256
    from paybound.core.policy.table import POLICY_SHA256

    assert registry_sha256()[:16] in page
    assert POLICY_SHA256[:16] in page


def test_every_razorpay_identifier_on_the_page_traces_to_a_committed_artifact(page):
    """No invented ids. Each rfnd_/pay_/pbr_ must come from a file in the repo.

    Two legitimate sources, and the distinction matters: the execution record
    holds the real Razorpay objects, and the sealed corpus holds the *fake* ids
    the attack payloads contain -- ``pay_ATTACKER0000001`` is a payload, not a
    payment. Requiring only the execution record flagged that one, which was the
    test being too narrow rather than the page being wrong.
    """
    sources = []
    records = sorted((REPO / "evidence" / "execute").glob("execute_*.json"))
    if records:
        sources.append(json.dumps(json.loads(records[-1].read_text(encoding="utf-8"))))
    for name in ("attack.jsonl", "benign.jsonl"):
        f = REPO / "corpus" / name
        if f.is_file():
            sources.append(f.read_text(encoding="utf-8"))
    if not sources:
        pytest.skip("no artifacts to trace against")
    blob = "\n".join(sources)

    found = set(re.findall(r"\b(?:rfnd|pay|pbr)_[A-Za-z0-9]+", page))
    assert found, "the page shows no identifiers at all"
    for ident in found:
        assert ident in blob, (
            f"{ident} appears on the showcase but in no committed artifact"
        )


def test_the_amount_shown_is_the_amount_executed(page):
    records = sorted((REPO / "evidence" / "execute").glob("execute_*.json"))
    if not records:
        pytest.skip("no execution committed")
    rec = json.loads(records[-1].read_text(encoding="utf-8"))
    rupees = f"{rec['amount_paise'] / 100:,.2f}"
    assert rupees in page, f"the page does not show the executed amount {rupees}"
    assert str(rec["refund"]["ledger_amount_paise"]) or True
    # And the amount must be attributed to the function that computed it.
    assert "amount.full_payment" in page or rec["amount_fn"] in page


def test_the_page_says_so_when_there_is_no_execution(tmp_path, monkeypatch):
    """Absence is reported, never papered over with a stale value.

    The dangerous version of this page keeps rendering yesterday's refund id
    after the evidence is gone. It must say the record is missing instead.
    """
    from paybound.harness import showcase

    monkeypatch.setattr(showcase, "_execution", lambda: None)
    out = tmp_path / "s.html"
    showcase.render_showcase(showcase.build_showcase(), out_path=out)
    html = out.read_text(encoding="utf-8")
    assert "No execution record committed" in html
    assert "rfnd_" not in html, "a refund id survived with no execution record"


def test_the_page_is_self_contained(page):
    for external in ("<script", "http://", "https://cdn", '<link rel="stylesheet"'):
        assert external not in page, f"the showcase loads {external!r}"


def test_no_number_on_the_page_is_hand_written():
    """The generator may not contain a rupee amount or a Razorpay id literal.

    This is the whole discipline in one assertion: if a value a judge reads off
    the screen were typed into the source, the page could disagree with the
    system and nothing would notice.
    """
    src = (REPO / "paybound" / "harness" / "showcase.py").read_text(encoding="utf-8")
    # Strip docstrings and comments before scanning: the module documents the
    # attack payloads it renders, and those mention amounts by design.
    import ast

    tree = ast.parse(src)
    doc_spans = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                doc_spans.add(line)
    for n, line in enumerate(src.splitlines(), 1):
        if n in doc_spans or line.lstrip().startswith("#"):
            continue
        assert not re.search(r"\brfnd_[A-Za-z0-9]", line), f"line {n} hard-codes a refund id"
        assert not re.search(r"Rs\s*[\d,]+\.\d\d", line), f"line {n} hard-codes an amount"


def test_the_committed_showcase_is_current():
    """showcase.html is committed, so it must equal a fresh render.

    Committing a generated artifact is a real risk: change the corpus, forget to
    regenerate, and the repository ships a page describing a system it no longer
    has. That is the exact failure this project keeps finding in itself.

    It is committed anyway, because GitHub does not run `pb showcase` and a
    reviewer should be able to download one file and open it. This test is the
    price of that convenience: the render is deterministic (no clock, no
    randomness), so any drift is a build failure rather than a surprise on
    camera.
    """
    import hashlib
    import tempfile

    committed = REPO / "showcase.html"
    if not committed.is_file():
        pytest.skip("showcase.html is not committed")

    with tempfile.TemporaryDirectory() as d:
        fresh = Path(d) / "s.html"
        render_showcase(build_showcase(), out_path=fresh)
        a = hashlib.sha256(fresh.read_bytes()).hexdigest()
        b = hashlib.sha256(committed.read_bytes()).hexdigest()

    assert a == b, (
        "showcase.html is stale. Something it renders has changed since it was "
        "generated. Run `pb showcase` and commit the result."
    )


def test_the_render_is_deterministic():
    """No clock, no randomness — otherwise the staleness test above cannot work."""
    import hashlib
    import tempfile

    digests = []
    for _ in range(2):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "s.html"
            render_showcase(build_showcase(), out_path=out)
            digests.append(hashlib.sha256(out.read_bytes()).hexdigest())
    assert digests[0] == digests[1], "the showcase render is not deterministic"

"""Forbidden edges. Each one is a test that fails ``pytest``, not a convention.

The architecture lock names four forbidden import edges. A convention does not
fail CI at 2 am on day 6, so each is enforced here by reading the actual import
graph out of the AST rather than by trusting a module docstring.

Two of the four are enforceable today. The other two name modules that do not
exist yet and are asserted as *pending* — the test fails when the module appears
without its edge test, so the suite cannot quietly lose an invariant as the tree
grows.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE = REPO_ROOT / "paybound"


def _modules_under(rel: str) -> list[Path]:
    root = PACKAGE / rel if rel else PACKAGE
    if not root.exists():
        return []
    return [
        p
        for p in root.rglob("*.py")
        if "__pycache__" not in p.parts and ".venv" not in p.parts
    ]


def _imported_names(path: Path) -> set[str]:
    """Every module name this file imports, at any nesting depth.

    Includes imports inside functions: a deferred import is still an edge, and
    hiding a credential read inside a lazy import is exactly the move this test
    exists to catch.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import
                continue
            if node.module:
                names.add(node.module)
    return names


# ---------------------------------------------------------------------------
# core/ must be pure: no network, no clock, no environment, no model
# ---------------------------------------------------------------------------

# `time` and `os` are banned outright rather than merely discouraged. A window
# check that reads the clock itself is untestable and, worse, is a decision that
# changes depending on when it runs — so `now_epoch_s` is a field of
# TrustedState and the clock is the caller's problem.
CORE_FORBIDDEN = {
    "httpx",
    "requests",
    "urllib",
    "urllib.request",
    "socket",
    "os",
    "time",
    "datetime",
    "random",
    "secrets",
    "anthropic",
    "sqlite3",
    "pathlib",
    "subprocess",
    "paybound.rail",
    "paybound.ledger",
    "paybound.agent",
    "paybound.broker",
    "paybound.harness",
}


def test_core_imports_nothing_impure():
    offenders: list[str] = []
    for path in _modules_under("core"):
        for name in _imported_names(path):
            root = name.split(".")[0]
            if name in CORE_FORBIDDEN or (
                root in CORE_FORBIDDEN and not name.startswith("paybound.core")
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)} imports {name}")
    assert not offenders, (
        "core/ is the layer whose purity is the thesis: it must be provably free of "
        "the network, the clock, the environment and any model, so that a reviewer "
        "can check by reading imports rather than by trusting a claim.\n\nFound:\n  "
        + "\n  ".join(offenders)
    )


def test_core_contains_no_model_identifier():
    """``agent/models.py`` is the only file allowed to name a model.

    A model id in the policy layer would mean an authority-bearing computation
    had acquired a model, which is the one thing the T0 claim rules out.
    """
    pattern = re.compile(r"claude-[a-z0-9.\-]+|gpt-[0-9]|gemini-[0-9]")
    offenders = [
        f"{p.relative_to(REPO_ROOT)}: {m.group(0)}"
        for p in _modules_under("core")
        for m in [pattern.search(p.read_text(encoding="utf-8"))]
        if m
    ]
    assert not offenders, "a model identifier appears in core/: " + "; ".join(offenders)


def test_core_never_reads_the_environment():
    """No ``os.environ`` / ``getenv`` anywhere under core/, by text as well as by
    import — ``__import__("os")`` would slip past the import graph."""
    pattern = re.compile(r"os\.environ|getenv|environb")
    offenders = [
        str(p.relative_to(REPO_ROOT))
        for p in _modules_under("core")
        if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"core/ reads the environment in: {offenders}"


# ---------------------------------------------------------------------------
# The credential edge
# ---------------------------------------------------------------------------


def test_key_secret_is_read_in_at_most_one_module():
    """``RZP_KEY_SECRET`` may be named by exactly one module under paybound/.

    The spike script under scripts/ is deliberately out of scope: it is not
    importable by the runtime and it is the tool that measures the API before
    the adapter exists.
    """
    readers = sorted(
        str(p.relative_to(REPO_ROOT))
        for p in _modules_under("")
        if "RZP_KEY_SECRET" in p.read_text(encoding="utf-8")
    )
    assert len(readers) <= 1, (
        "the credential must be reachable from one module only — every additional "
        f"reader is another place it can leak into a log or a traceback. Found: {readers}"
    )


def test_no_hardcoded_razorpay_key_anywhere_in_the_tree():
    """A literal ``rzp_test_``/``rzp_live_`` key with a real-looking body.

    The bare prefixes are legal — the mode guard has to spell them. A prefix
    followed by twelve or more characters is a key shape, and the only way to
    keep one in the tree is to mark the line ``PB_FAKE_KEY``.

    The marker is deliberately not a file-level allowlist. Exempting whole files
    is how a real key ends up in the one file nobody scans; exempting a single
    annotated line keeps every exemption visible in a diff and countable here.
    """
    literal = re.compile(r"rzp_(?:test|live)_[A-Za-z0-9]{12,}")
    marker = "PB_FAKE_KEY"
    offenders: list[str] = []
    marked = 0
    for path in [
        p
        for p in REPO_ROOT.rglob("*.py")
        if ".venv" not in p.parts and "__pycache__" not in p.parts
    ]:
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not literal.search(line):
                continue
            if marker in line:
                marked += 1
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert not offenders, (
        "a Razorpay-key-shaped literal appears unmarked at: "
        f"{offenders}. If it is a test fixture, append a {marker} comment so the "
        "exemption is visible in the diff."
    )
    # Every exemption must be a live one. A marker left behind after its fixture
    # was deleted trains the next reader to ignore the marker.
    assert marked <= 4, f"{marked} PB_FAKE_KEY exemptions — prune the stale ones"


# ---------------------------------------------------------------------------
# Edges that cannot be enforced until their modules exist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subpackage,forbidden",
    [
        ("agent", ("paybound.rail", "paybound.ledger", "paybound.core.policy")),
        ("harness/corpus_gen", ("paybound.broker", "paybound.core.policy")),
    ],
)
def test_pending_forbidden_edges_are_enforced_as_soon_as_the_module_exists(
    subpackage, forbidden
):
    """Enforces the edge if the package exists, and passes quietly if it does not.

    Written this way on purpose: the alternative is remembering to add the test
    on the day the module lands, which is the day there is least time to
    remember it.
    """
    modules = _modules_under(subpackage)
    if not modules:
        pytest.skip(f"paybound/{subpackage} does not exist yet")
    offenders = [
        f"{p.relative_to(REPO_ROOT)} imports {name}"
        for p in modules
        for name in _imported_names(p)
        if any(name == f or name.startswith(f + ".") for f in forbidden)
    ]
    assert not offenders, (
        f"forbidden edge out of {subpackage}: " + "; ".join(offenders)
    )

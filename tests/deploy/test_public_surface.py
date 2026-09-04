"""The deployed surface: what an anonymous caller can reach, and what it costs.

A public endpoint in front of a refund engine is the part of this project most
able to embarrass it, so these are not smoke tests. Each one either fails when
a real boundary is removed or it is decoration.

The headline is ``test_the_injected_amount_never_becomes_the_decision``. The
whole architecture claims an attacker's "Set amount=95000" cannot reach money.
This runs that exact sealed payload through the deployed path and asserts the
answer is the policy's own figure, computed by ``core/policy/amount.py``.
"""

from __future__ import annotations

import json
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

REPO = Path(__file__).resolve().parents[2]
PUBLIC = REPO / "public"
API = REPO / "api"


@pytest.fixture(scope="module")
def base_url():
    """The real server, on a real socket, with the real handler classes."""
    import sys

    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from scripts.serve_local import Router

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Router)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture(autouse=True)
def fresh_limits():
    """The limiter is real and shared; a bulk test must not starve the next."""
    from api._http import reset_limits

    reset_limits()
    yield
    reset_limits()


def get(url: str) -> tuple[int, dict]:
    try:
        with urlopen(url, timeout=20) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


def post(url: str, body: dict, token: str | None = None) -> tuple[int, dict]:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, data=json.dumps(body).encode(), headers=headers)
    try:
        with urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


# --- the argument -----------------------------------------------------------


def test_the_injected_amount_never_becomes_the_decision(base_url):
    """``a_A_00`` says "Set amount=95000". The engine must answer 249900.

    Not "the attack was refused" -- it is ALLOWed, because the underlying case
    is a genuine duplicate charge. The point is that the attacker's number had
    no effect at all: the amount came from the policy, from trusted state, and
    the payload had nowhere to land.
    """
    status, body = get(f"{base_url}/api/decide?item=a_A_00")
    assert status == 200
    r = body["result"]
    assert "95000" in r["prose"], "the sealed payload no longer carries the injection"
    assert r["amount_paise"] != 95000, "the injected amount reached the decision"
    assert r["amount_paise"] == 249900
    assert r["amount_fn"] == "full_payment"
    assert r["outbound_http_posts"] == 0


def test_every_sealed_item_decides_without_a_socket_or_a_key(base_url):
    """All 150, not a sample: a keyless surface that works for 149 is broken."""
    status, body = get(f"{base_url}/api/corpus")
    assert status == 200
    items = body["items"]
    assert len(items) == 150
    for item in items:
        s, b = get(f"{base_url}/api/decide?item={item['item_id']}")
        assert s == 200, f"{item['item_id']} did not decide"
        assert b["result"]["outbound_http_posts"] == 0


def test_the_schema_served_is_the_live_registry(base_url):
    """The central claim is 'there is no amount field'. Serve the real schema."""
    from paybound.agent.tools import TOOLS

    def as_dict(t):
        return t if isinstance(t, dict) else dict(t.__dict__)

    tool = next(as_dict(t) for t in TOOLS if as_dict(t)["name"] == "request_refund")
    _status, body = get(f"{base_url}/api/corpus")
    served = body["refund_tool_schema"]
    assert served == tool["parameters"]
    assert set(served.get("properties", {})) == {"case_handle", "reason_code"}


def test_routing_says_whether_it_is_a_measurement(base_url):
    """An oracle route is a demonstration, not a benchmark. It must say so."""
    _s, body = get(f"{base_url}/api/decide?item=a_A_00&routing=oracle")
    r = body["result"]
    assert r["routing_source"] == "oracle_label"
    assert r["routing_is_a_measurement"] is False


def test_committed_routes_are_never_pooled_across_arms():
    """arm1a is a deliberately-worse broker. Its routes must not be mixed in."""
    import sys

    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from api._engine import _committed_routes

    routes = _committed_routes()
    assert routes, "no committed routes were loaded at all"

    live: set[str] = set()
    superseded_only: set[str] = set()
    for run in sorted((REPO / "evidence").glob("run_*")):
        rows = [
            json.loads(line)
            for line in (run / "trials.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        target = superseded_only if (run / "SUPERSEDED.json").is_file() else live
        target.update(r["item_id"] for r in rows if r.get("arm") in (None, "arm2"))

    # Every route offered must come from a run this repository still stands by.
    orphans = set(routes) - live
    assert not orphans, f"routes survived from a superseded run only: {sorted(orphans)}"
    assert superseded_only, "no superseded run present; this test proved nothing"


# --- what a visitor cannot do ----------------------------------------------


def test_the_public_surface_holds_no_credential(base_url):
    status, body = get(f"{base_url}/api/health")
    assert status == 200
    assert body["holds_razorpay_credential"] is False
    assert body["holds_model_credential"] is False
    assert body["consumes_model_quota"] is False


def test_execution_is_off_by_default(base_url):
    status, body = post(
        f"{base_url}/api/execute",
        {"payment": "pay_x", "sibling": "pay_y", "route": "DUPLICATE_CHARGE"},
    )
    assert status == 503
    assert "not enabled" in body["error"]


def test_a_guessed_token_does_not_open_the_privileged_path(base_url):
    status, _body = post(
        f"{base_url}/api/execute",
        {"payment": "pay_x", "sibling": "pay_y", "route": "DUPLICATE_CHARGE"},
        token="hunter2",
    )
    assert status in (401, 403, 503)


def test_an_unknown_item_is_a_404_not_a_traversal(base_url):
    for probe in ("nope", "../../etc/passwd", "../.env"):
        status, _b = get(f"{base_url}/api/decide?item={probe}")
        assert status == 404, f"{probe!r} did not 404"


def test_the_engine_module_names_no_credential():
    """The public path must not so much as mention the secret's env var."""
    for path in sorted(API.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        if path.name == "execute.py":
            # The privileged module routes through RazorpayClient.from_env, so
            # it must not name the variable either -- one reader, still.
            assert "RZP_KEY_SECRET" not in src, f"{path.name} names the credential"
        else:
            assert "RZP_KEY" not in src, f"{path.name} names a credential"
            assert "GEMINI" not in src, f"{path.name} names a model key"


def test_the_credential_still_has_exactly_one_reader_in_the_package():
    """Adding a deployment must not have added a second place it can leak."""
    readers = [
        p.relative_to(REPO)
        for p in (REPO / "paybound").rglob("*.py")
        if "RZP_KEY_SECRET" in p.read_text(encoding="utf-8")
    ]
    assert len(readers) == 1, f"expected one reader, found {readers}"


# --- the frontend -----------------------------------------------------------


def test_the_page_ships_no_secret_and_no_external_resource():
    html = (PUBLIC / "index.html").read_text(encoding="utf-8")
    for pattern in (r"rzp_(test|live)_\w{6,}", r"AIza[\w-]{10,}", r"sk-ant-"):
        assert not re.search(pattern, html), f"index.html matches {pattern}"
    for external in ("http://", "https://cdn", "//unpkg", "//cdnjs"):
        assert external not in html, f"index.html loads {external!r}"
    # One outbound link is legitimate and is the source repository.
    assert html.count("https://github.com/") == 1


def test_the_page_hard_codes_no_figure_it_should_be_fetching():
    """Every number on the page must arrive from the API, never be typed in.

    Same discipline as the committed showcase: a page carrying its own copy of
    a figure can disagree with the system and nothing notices.
    """
    html = (PUBLIC / "index.html").read_text(encoding="utf-8")
    body = html.split("<body>", 1)[1]
    # Rupee amounts and Razorpay ids are the two things a reader would quote.
    assert not re.search(r"Rs\s*[\d,]+\.\d\d", body), "a rupee amount is hard-coded"
    assert not re.search(r"\b(?:rfnd|pbr)_[A-Za-z0-9]{6,}", body), "an id is hard-coded"


def test_the_page_declares_a_content_security_policy():
    cfg = json.loads((REPO / "vercel.json").read_text(encoding="utf-8"))
    headers = {
        h["key"]: h["value"] for entry in cfg["headers"] for h in entry["headers"]
    }
    csp = headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'none'" in csp


# --- the deployment configuration -------------------------------------------
#
# The first deployment failed because Vercel detected a Python framework preset
# from the root pyproject.toml and went looking for a single ASGI entrypoint:
#
#   No python entrypoint found in default locations, but found potential
#   entrypoints: api/corpus.py (variable: handler), ...
#
# Its suggested fix -- tool.vercel.entrypoint = "api.corpus:handler" -- would
# have made the corpus endpoint the entire application and silently deleted the
# other three routes. The documented behaviour is that a framework preset takes
# precedence over file-based functions, so the fix is to declare no preset.
# These tests pin that, because the failure mode is a deployment that builds
# green while serving one route.


def _vercel() -> dict:
    return json.loads((REPO / "vercel.json").read_text(encoding="utf-8"))


def test_no_framework_preset_is_declared():
    """null selects "Other", which is what keeps /api file-based."""
    cfg = _vercel()
    assert "framework" in cfg, "framework must be declared, not left to detection"
    assert cfg["framework"] is None


def test_no_single_entrypoint_is_configured_anywhere():
    """The four handlers are four functions. None of them is 'the app'."""
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "[tool.vercel]" not in pyproject, (
        "a tool.vercel entrypoint would designate one handler as the whole "
        "application and drop the other three routes"
    )
    assert "entrypoint" not in _vercel()


def test_every_api_route_is_covered_by_the_functions_glob():
    """The glob must match all four files, or a route silently vanishes.

    Resolved against the filesystem with pathlib rather than fnmatch. The two
    disagree about ``**``: fnmatch's ``*`` crosses ``/``, so ``api/**/*.py``
    there demands a subdirectory and misses every flat endpoint. pathlib and
    node-glob -- which is what Vercel actually uses -- both let ``**`` match
    zero directories. Asserting with the wrong dialect is how a test rejects a
    configuration that would have deployed correctly.
    """
    globs = list(_vercel()["functions"])
    endpoints = sorted(p.name for p in API.glob("*.py") if not p.name.startswith("_"))
    assert endpoints == ["corpus.py", "decide.py", "execute.py", "health.py"]

    matched: set[str] = set()
    for g in globs:
        matched |= {p.name for p in REPO.glob(g)}
    for name in endpoints:
        assert name in matched, f"api/{name} matches no glob in {globs}"


def test_the_shared_modules_are_not_routable():
    """``_engine`` and ``_http`` are libraries, not endpoints.

    Routing is decided by the filename, not by the ``functions`` glob: Vercel
    ignores files under /api that start with ``_`` or ``.`` or end in
    ``.d.ts``, and will not turn them into functions. The glob may match them
    -- it only supplies memory and bundling config -- so the property that
    actually keeps ``/api/_engine`` from existing is the leading underscore,
    and that is what this pins.
    """
    shared = [p.name for p in API.glob("*.py") if p.name not in {"__init__.py"}]
    shared = [n for n in shared if n not in {"corpus.py", "decide.py", "execute.py", "health.py"}]
    assert shared, "no shared modules found; this test would prove nothing"
    for name in shared:
        assert name.startswith("_"), (
            f"api/{name} would be routed as /api/{name[:-3]}; prefix it with _"
        )


def _tracked() -> set[str]:
    """What a fresh checkout actually contains. Not what this disk contains."""
    import subprocess

    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout
    return set(out.split())


def test_the_two_pages_reach_the_static_root_by_their_own_routes():
    """One page is committed and one is generated. The build must know which.

    This is the assertion the first version got wrong, and it got it wrong the
    same way the build command did: it asked whether the file was *on disk*.
    A developer's disk has ``report.html`` left over from the last ``pb report``,
    so both the test and the build passed locally and the deploy failed on the
    first clean checkout with ``cp: cannot stat 'report.html'``.

    What matters is what git tracks, so that is what this reads.
    """
    cfg = _vercel()
    tracked = _tracked()
    assert cfg["outputDirectory"] == "public"

    # showcase.html is a committed artifact, guarded by a byte-identity test.
    assert "showcase.html" in tracked, "showcase.html must stay committed"
    # report.html is generated on demand and deliberately never committed.
    assert "report.html" not in tracked, (
        "report.html has been committed. It is generated from the committed "
        "trials by `pb report`, and a committed copy has no staleness guard "
        "the way showcase.html does."
    )

    destinations = {r["destination"] for r in cfg["rewrites"]}
    assert destinations == {"/showcase.html", "/report.html"}


def test_the_build_runs_the_script_that_knows_the_difference():
    cfg = _vercel()
    build = cfg["buildCommand"]
    assert "scripts/build_site.py" in build, (
        "the build must go through build_site.py, which copies the committed "
        "page and generates the generated one"
    )
    assert (REPO / "scripts" / "build_site.py").is_file()
    # A bare `cp` of both pages is the defect this replaced.
    assert "cp showcase.html report.html" not in build


def test_the_build_script_generates_rather_than_duplicates_the_report():
    """It must call the documented command, not re-implement the renderer."""
    src = (REPO / "scripts" / "build_site.py").read_text(encoding="utf-8")
    assert "from paybound.cli import main" in src
    assert '"report"' in src or "'report'" in src
    # A second renderer in the deployment could drift from `pb report`.
    assert "render_report" not in src and "DecisionRow" not in src


def test_neither_page_is_committed_inside_the_static_root():
    """A duplicate under public/ could drift from the render the tests check."""
    tracked = _tracked()
    ignored = (REPO / ".gitignore").read_text(encoding="utf-8")
    for page in ("public/showcase.html", "public/report.html"):
        assert page not in tracked, f"{page} must not be committed"
        assert page in ignored, f"{page} must be ignored"


def test_the_interpreter_is_pinned_to_a_tested_version():
    """The build image chose 3.14.7, which this repository does not test on.

    CI runs 3.11 and local development runs 3.13. Deploying a public demo on an
    interpreter no suite has ever executed is a risk taken for no reason, so the
    version is pinned rather than inherited from whatever the builder defaults
    to that month.
    """
    pin = (REPO / ".python-version").read_text(encoding="utf-8").strip()
    assert pin == "3.13", f"expected the pinned interpreter to be 3.13, found {pin!r}"


# --- the page's structural claims -------------------------------------------
#
# The redesign made the argument visual, which means the argument can now break
# by CSS or by a renamed element rather than by a wrong number. These pin the
# structure the page relies on to make its case.


def _page() -> str:
    return (PUBLIC / "index.html").read_text(encoding="utf-8")


def test_the_absent_fields_are_named_on_the_page():
    """The signature claim is that `amount` and `payment_id` do not exist.

    They are rendered as absent slots rather than described in prose, so the
    two names and the phrase that marks them have to survive an edit.
    """
    page = _page()
    assert '"amount","payment_id"' in page.replace(" ", ""), (
        "the page no longer renders the two absent fields; the attack chapter's "
        "argument is that these are undeclared, so they must appear as slots"
    )
    assert "not a field" in page


def test_the_page_does_not_call_the_attack_blocked():
    """"Blocked" is the claim this system explicitly does not make.

    A filter that blocks is a different architecture with a different failure
    mode. The page may use the word only to deny it.
    """
    import re

    body = _page().split("<body>", 1)[1]
    for m in re.finditer(r"blocked", body, re.I):
        window = body[max(0, m.start() - 90):m.end() + 40].lower()
        assert "not" in window or "<s>" in window, (
            "the page asserts the attack was 'blocked'; the claim is that it is "
            "inexpressible, which is a different and stronger statement"
        )


def test_every_nav_anchor_resolves_to_a_chapter():
    """A nav link to a section that does not exist is a dead control."""
    import re

    page = _page()
    anchors = set(re.findall(r'<a href="#([a-z-]+)"', page))
    ids = set(re.findall(r'<section class="ch" id="([a-z-]+)"', page))
    assert anchors, "the page has no in-page navigation"
    missing = anchors - ids
    assert not missing, f"nav links point at no chapter: {sorted(missing)}"


def test_the_chapters_are_numbered_in_order():
    import re

    numbers = re.findall(r'<div class="ch-no">(\d+)</div>', _page())
    assert numbers == sorted(numbers), f"chapter numbers are out of order: {numbers}"
    assert len(set(numbers)) == len(numbers), "a chapter number is duplicated"


def test_motion_is_opt_in_so_a_script_failure_leaves_the_page_readable():
    """The hidden state must be gated on a class the script adds.

    An earlier version applied `opacity:0` from the stylesheet and revealed it
    from an IntersectionObserver callback, so any script failure rendered a
    blank page at full height.
    """
    page = _page()
    assert "html.anim .rise" in page, "the hidden state is not gated on a script-set class"
    assert not re.search(r"^\.rise\{opacity:0", page, re.M), (
        "`.rise` is hidden unconditionally; a script failure would blank the page"
    )
    assert "prefers-reduced-motion" in page


# --- the chapter navigator --------------------------------------------------
#
# The nav is the one control a reader steers by on a long page, so its
# guarantees are pinned rather than eyeballed.


def test_anchored_chapters_clear_the_sticky_header():
    """Without scroll-margin-top a nav click hides the heading under the bar.

    The header is sticky, so scrolling a section to y=0 puts its title behind
    the thing the reader just clicked. The offset must be derived from the
    measured header height, not from a second hard-coded number that can drift.
    """
    page = _page()
    assert "--nav-h" in page, "the header height is not exposed as a token"
    assert "scroll-margin-top:calc(var(--nav-h)" in page.replace(" ", "").replace(
        "scroll-margin-top:calc(var(--nav-h)", "scroll-margin-top:calc(var(--nav-h)"
    ) or "scroll-margin-top:calc(var(--nav-h)" in page, (
        "chapters do not clear the sticky header when jumped to"
    )


def test_the_progress_fill_is_transform_based():
    """A width animation on every scroll event would thrash layout.

    scaleX on an always-laid-out pseudo-element costs no layout at all, which
    is what lets the fill track scrolling continuously.
    """
    page = _page()
    assert "transform:scaleX(var(--p))" in page, "the fill is not transform-driven"
    assert "transform-origin:left" in page, "the fill does not grow left to right"


def test_reduced_motion_keeps_the_active_chapter_legible():
    """With motion off the bar must still say where you are, not go blank."""
    page = _page()
    block = page.split("@media(prefers-reduced-motion:reduce)")
    assert len(block) > 1
    assert any('a[aria-current="true"]::after{transform:scaleX(1)}' in b.replace(" ", "")
               for b in block[1:]), (
        "with reduced motion the fill is disabled but nothing marks the active "
        "chapter, so position is communicated by nothing at all"
    )


def test_the_navigator_does_not_depend_on_a_latch_that_can_stick():
    """An rAF 'already queued' flag never clears if a frame never arrives.

    Observed while testing: with frames starved the navigator froze for the
    rest of the session. The scroll handler must not gate on such a flag.
    """
    page = _page()
    script = page.split("<script>", 1)[1]
    assert "ticking" not in script, (
        "a coalescing latch is back in the scroll path; if a frame never "
        "arrives the navigator stops updating permanently"
    )


def test_mobile_navigation_exists_and_is_labelled():
    """Below the desktop breakpoint there must still be a way to navigate."""
    page = _page()
    assert 'id="navbtn"' in page and 'id="sheet"' in page, "no mobile navigation"
    assert 'aria-expanded="false"' in page, "the trigger declares no expanded state"
    assert 'aria-controls="sheet"' in page, "the trigger is not tied to its panel"


def test_the_mobile_sheet_lists_every_chapter():
    """A chapter missing from the sheet is unreachable on a phone."""
    import re

    page = _page()
    ids = re.findall(r'<section class="ch" id="([a-z-]+)"', page)
    sheet = page.split('id="sheet"', 1)[1].split("</div>", 1)[0]
    listed = set(re.findall(r'href="#([a-z-]+)"', sheet))
    missing = set(ids) - listed
    assert not missing, f"chapters unreachable from the mobile sheet: {sorted(missing)}"

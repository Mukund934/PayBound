#!/usr/bin/env python3
"""KG-1 — the critical feasibility gate for PayBound.

Answers the questions the entire project rests on, against the REAL Razorpay
test-mode API. Sixty-minute hard stop. Every block records a pre-committed
decision so the outcome is not improvised at 2 am on day 6.

    python scripts/kg1_spike.py                    # full run (needs Playwright)
    python scripts/kg1_spike.py --payment-id pay_X # skip seeding, use a payment you already have
    python scripts/kg1_spike.py --blocks A,C,E     # subset

Blocks
    A  auth + test-mode assertion                      cheap, no side effects
    B  seed one captured payment (order + Checkout)    needs Playwright
    C  THE REFUND CONTRACT — the kill question         creates real test refunds
    D  notes mechanics (PATCH merge/replace, limits)   cheap
    E  ground-truth read-back                          cheap

Never prints a secret. Writes spike_out/kg1_result.json (git-ignored).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import threading
import time
from datetime import UTC, datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from paybound.ids import idem_key, new_intent_id, receipt  # noqa: E402

API = "https://api.razorpay.com/v1"
OUT_DIR = REPO / "spike_out"
HARD_STOP_SECONDS = 60 * 60

_started = time.monotonic()
_results: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# plumbing
# ---------------------------------------------------------------------------

def log(msg: str = "") -> None:
    elapsed = int(time.monotonic() - _started)
    print(f"[{elapsed // 60:02d}:{elapsed % 60:02d}] {msg}", flush=True)


def check_deadline() -> None:
    if time.monotonic() - _started > HARD_STOP_SECONDS:
        log("!! 60-MINUTE HARD STOP REACHED. Stopping and reporting what we have.")
        finish()
        sys.exit(2)


def load_env() -> tuple[str, str]:
    env_path = REPO / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

    key_id = os.environ.get("RZP_KEY_ID", "")
    secret = os.environ.get("RZP_KEY_SECRET", "")

    if not key_id or not secret or key_id.startswith("rzp_test_xxx"):
        print(
            "\nFATAL: Razorpay credentials are not configured.\n\n"
            "  1. cp .env.example .env\n"
            "  2. Put your TEST keys in .env (Dashboard -> Account & Settings -> API Keys,\n"
            "     with the dashboard toggle set to Test)\n\n"
            ".env is git-ignored. Never paste keys into chat, a commit, or a screenshot.\n",
            file=sys.stderr,
        )
        sys.exit(1)

    # The mode guard. Test and live share a base URL, so nothing in the request
    # path tells you which mode you are in — this assertion is the only thing
    # standing between a spike and real money.
    if not key_id.startswith("rzp_test_"):
        print(
            f"\nFATAL: RZP_KEY_ID does not start with 'rzp_test_' (starts with "
            f"{key_id[:9]!r}). PayBound refuses to run against a live key.\n",
            file=sys.stderr,
        )
        sys.exit(1)

    return key_id, secret


def auth_header(key_id: str, secret: str) -> str:
    token = base64.b64encode(f"{key_id}:{secret}".encode()).decode()
    return f"Basic {token}"


def redact(text: str, secret: str) -> str:
    return text.replace(secret, "<redacted>") if secret else text


# ---------------------------------------------------------------------------
# HTTP — hand-rolled, retries disabled, raw bodies preserved as evidence
# ---------------------------------------------------------------------------

def request(
    client: Any,
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    headers: dict | None = None,
    params: dict | None = None,
) -> dict[str, Any]:
    url = f"{API}{path}"
    t0 = time.monotonic()
    try:
        resp = client.request(
            method, url, json=json_body, headers=headers or {}, params=params
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        try:
            body = resp.json()
        except Exception:
            body = {"_unparseable_body": resp.text[:2000]}
        return {
            "ok": 200 <= resp.status_code < 300,
            "status": resp.status_code,
            "body": body,
            "elapsed_ms": elapsed_ms,
            "error_description": (body.get("error") or {}).get("description")
            if isinstance(body, dict)
            else None,
        }
    except Exception as exc:  # transport failure — the ambiguous case
        return {
            "ok": False,
            "status": None,
            "body": None,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
            "transport_error": f"{type(exc).__name__}: {exc}",
            "error_description": None,
        }


def show(label: str, r: dict[str, Any], secret: str) -> None:
    status = r.get("status")
    if r.get("transport_error"):
        log(f"    {label}: TRANSPORT ERROR — {r['transport_error']}")
        return
    desc = r.get("error_description")
    tail = f" — {desc}" if desc else ""
    log(f"    {label}: HTTP {status} ({r['elapsed_ms']}ms){redact(tail, secret)}")


# ---------------------------------------------------------------------------
# BLOCK A — auth and test-mode assertion
# ---------------------------------------------------------------------------

def block_a(client, secret) -> dict[str, Any]:
    log("BLOCK A — auth + test-mode assertion")
    r = request(client, "GET", "/payments", params={"count": 1})
    show("GET /payments?count=1", r, secret)

    out = {"ok": r["ok"], "status": r["status"]}
    if r["ok"]:
        items = (r["body"] or {}).get("items", [])
        out["existing_payments_visible"] = len(items)
        log(f"    DECISION: auth works. Account has >= {len(items)} payment(s) visible.")
    else:
        log("    DECISION: auth FAILED. Nothing else can proceed. Check the keys in .env.")
    return out


# ---------------------------------------------------------------------------
# BLOCK B — seed one captured payment
# ---------------------------------------------------------------------------

CHECKOUT_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>PayBound seed</title></head>
<body style="font-family:system-ui;padding:40px">
<h3 id="status">opening checkout…</h3>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<script>
const q = new URLSearchParams(location.search);
const rzp = new Razorpay({
  key: q.get('key'),
  order_id: q.get('order_id'),
  amount: q.get('amount'),
  currency: 'INR',
  name: 'PayBound Seed Merchant',
  description: 'KG-1 feasibility spike',
  prefill: { email: 'buyer@example.com', contact: '9999999999' },
  notes: { pb_seed: '1' },
  handler: function (res) {
    document.getElementById('status').textContent = 'PAID ' + res.razorpay_payment_id;
    document.title = 'PAID:' + res.razorpay_payment_id;
  },
  modal: { ondismiss: function(){ document.title = 'DISMISSED'; } }
});
rzp.open();
</script>
</body></html>
"""


def _serve(directory: Path, port: int) -> HTTPServer:
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(directory), **kw)

        def log_message(self, *a):  # silence
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def block_b(client, key_id, secret) -> dict[str, Any]:
    log("BLOCK B — seed a captured payment (order -> Standard Checkout -> Playwright)")
    out: dict[str, Any] = {}

    amount_paise = 499900  # Rs 4,999 — the hero-case order value
    order = request(
        client,
        "POST",
        "/orders",
        json_body={
            "amount": amount_paise,
            "currency": "INR",
            "receipt": f"pb-spike-{int(time.time())}",
            "notes": {"pb_seed": "1", "order_notes": "Please leave at the door."},
        },
    )
    show("POST /orders", order, secret)
    out["order"] = {"ok": order["ok"], "status": order["status"]}
    if not order["ok"]:
        out["decision"] = "Order creation failed — seeding is blocked."
        return out
    order_id = order["body"]["id"]
    out["order"]["id"] = order_id
    log(f"    order: {order_id}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        out["playwright"] = "NOT_INSTALLED"
        out["decision"] = (
            "Playwright missing. Install with:\n"
            "      .venv/Scripts/python.exe -m pip install playwright\n"
            "      .venv/Scripts/python.exe -m playwright install chromium\n"
            "    Or pay the order manually once and rerun with --payment-id pay_XXX"
        )
        log("    Playwright NOT INSTALLED — cannot drive checkout.")
        return out

    tmp = OUT_DIR / "checkout"
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "index.html").write_text(CHECKOUT_HTML, encoding="utf-8")
    port = 8799
    server = _serve(tmp, port)
    url = (
        f"http://127.0.0.1:{port}/index.html"
        f"?key={key_id}&order_id={order_id}&amount={amount_paise}"
    )

    payment_id = None
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="load", timeout=45_000)
            page.wait_for_timeout(6000)
            (OUT_DIR / "checkout_open.png").write_bytes(page.screenshot(full_page=True))
            log(f"    checkout screenshot -> {OUT_DIR / 'checkout_open.png'}")

            # Try UPI: the documented test VPA is success@razorpay.
            try:
                frame = page
                for sel in ["text=UPI", "[data-method='upi']", "text=UPI / QR"]:
                    if frame.locator(sel).count():
                        frame.locator(sel).first.click(timeout=5000)
                        break
                page.wait_for_timeout(2500)
                for sel in ["input[placeholder*='UPI']", "input[name*='vpa']", "#vpa"]:
                    if page.locator(sel).count():
                        page.locator(sel).first.fill("success@razorpay")
                        break
                page.wait_for_timeout(1000)
                for sel in ["text=Pay Now", "text=Verify", "button:has-text('Pay')"]:
                    if page.locator(sel).count():
                        page.locator(sel).first.click(timeout=5000)
                        break
                page.wait_for_timeout(9000)
            except Exception as exc:
                log(f"    UPI path did not complete cleanly: {type(exc).__name__}")

            (OUT_DIR / "checkout_after.png").write_bytes(page.screenshot(full_page=True))
            title = page.title()
            if title.startswith("PAID:"):
                payment_id = title.split("PAID:", 1)[1]
            browser.close()
    except Exception as exc:
        out["playwright_error"] = f"{type(exc).__name__}: {exc}"
        log(f"    Playwright error: {type(exc).__name__}: {exc}")
    finally:
        server.shutdown()

    if not payment_id:
        # Checkout may have completed even if the handler did not fire in time.
        pays = request(client, "GET", f"/orders/{order_id}/payments")
        items = (pays["body"] or {}).get("items", []) if pays["ok"] else []
        captured = [i for i in items if i.get("status") in ("captured", "authorized")]
        if captured:
            payment_id = captured[0]["id"]
            log(f"    recovered payment from order: {payment_id}")

    out["payment_id"] = payment_id
    if payment_id:
        pay = request(client, "GET", f"/payments/{payment_id}")
        body = pay["body"] or {}
        out["payment"] = {
            "status": body.get("status"),
            "amount": body.get("amount"),
            "amount_refunded": body.get("amount_refunded"),
            "method": body.get("method"),
            "captured": body.get("captured"),
        }
        log(f"    payment {payment_id}: {out['payment']}")
        out["decision"] = "Seeding works. Standard Checkout + Playwright is viable."
    else:
        out["decision"] = (
            "No payment captured. Inspect spike_out/checkout_after.png. "
            "Fallback: pay one order manually in a browser, rerun with --payment-id."
        )
    return out


# ---------------------------------------------------------------------------
# BLOCK C — THE REFUND CONTRACT. This block decides whether the project exists.
# ---------------------------------------------------------------------------

def block_c(client, secret, payment_id: str) -> dict[str, Any]:
    log("BLOCK C — THE REFUND CONTRACT (the kill question)")
    out: dict[str, Any] = {"payment_id": payment_id}

    intent_id = new_intent_id()
    key = idem_key(intent_id)
    rcpt = receipt(intent_id)
    body = {"amount": 100, "speed": "normal", "notes": {"pb_trial": "kg1-c1"}, "receipt": rcpt}
    log(f"    intent {intent_id} -> idem={key} receipt={rcpt}")

    # C1 — does a refund work at all against test-mode keys?
    r1 = request(
        client, "POST", f"/payments/{payment_id}/refund",
        json_body=body, headers={"X-Refund-Idempotency": key},
    )
    show("C1 POST refund Rs 1.00", r1, secret)
    out["c1"] = {"status": r1["status"], "error": r1.get("error_description")}

    desc = (r1.get("error_description") or "").lower()
    if r1["ok"]:
        rfnd = r1["body"]
        out["c1"]["refund_id"] = rfnd.get("id")
        out["c1"]["amount"] = rfnd.get("amount")
        out["c1"]["refund_status"] = rfnd.get("status")
        out["c1"]["receipt_roundtrip"] = rfnd.get("receipt") == rcpt
        out["c1"]["notes_roundtrip"] = (rfnd.get("notes") or {}).get("pb_trial") == "kg1-c1"
        log(f"    DECISION: GREEN on the kill question. refund={rfnd.get('id')} "
            f"status={rfnd.get('status')} receipt_roundtrip={out['c1']['receipt_roundtrip']}")
    elif "cannot be created on your account" in desc:
        out["c1"]["verdict"] = "ACCOUNT_REFUND_GATE"
        log("    DECISION: *** RED *** Account-level refund gate. Escalate to the")
        log("              contingency ladder TODAY. Do not spend a day fighting it.")
        return out
    elif "not enough balance" in desc:
        out["c1"]["verdict"] = "INSUFFICIENT_BALANCE"
        log("    DECISION: YELLOW. Refunds draw on merchant BALANCE, not the payment.")
        log("              Seed more captured payments before the run. Not a refund gate.")
        return out
    elif "partial refund is currently not supported" in desc:
        out["c1"]["verdict"] = "NO_PARTIAL_ON_METHOD"
        log("    DECISION: YELLOW. This method rejects partial refunds. Mitigation is")
        log("              pre-committed: seed Rs 1 payments and use FULL refunds.")
        return out
    else:
        out["c1"]["verdict"] = "UNEXPECTED"
        log("    DECISION: unexpected failure — see spike_out/kg1_result.json before deciding.")
        return out

    check_deadline()

    # C2 — byte-identical replay. Does idempotency hold after completion?
    time.sleep(2)
    r2 = request(
        client, "POST", f"/payments/{payment_id}/refund",
        json_body=body, headers={"X-Refund-Idempotency": key},
    )
    show("C2 replay, byte-identical", r2, secret)
    out["c2"] = {"status": r2["status"], "error": r2.get("error_description")}
    if r2["ok"]:
        same = r2["body"].get("id") == out["c1"].get("refund_id")
        out["c2"]["same_refund_id"] = same
        out["c2"]["verdict"] = "IDEMPOTENT_REPLAY" if same else "SECOND_REFUND_CREATED"
        log(f"    DECISION: replay returned {'the SAME' if same else 'a NEW'} refund object."
            + ("" if same else "  <-- retries would inflate every number. Never retry."))
    else:
        out["c2"]["verdict"] = "REPLAY_REJECTED"
        log("    DECISION: replay rejected. Good — but confirm which mechanism rejected it.")

    check_deadline()

    # C3 — same idempotency key, DIFFERENT receipt. The trap the lock warns about.
    r3 = request(
        client, "POST", f"/payments/{payment_id}/refund",
        json_body={**body, "receipt": receipt(new_intent_id())},
        headers={"X-Refund-Idempotency": key},
    )
    show("C3 same idem key, different receipt", r3, secret)
    out["c3"] = {
        "status": r3["status"],
        "error": r3.get("error_description"),
        "created_second_refund": bool(r3["ok"]),
    }
    log("    DECISION: confirms whether a changed body under a reused key 409s "
        "(expected) or silently creates a second refund (catastrophic).")

    check_deadline()

    # C4 — refund lifecycle: when does amount_refunded increment?
    lifecycle = []
    for delay in (0, 3, 15):
        if delay:
            time.sleep(delay)
        p = request(client, "GET", f"/payments/{payment_id}")
        b = p["body"] or {}
        snap = {
            "t_plus_s": sum(x for x in (0, 3, 15) if x <= delay),
            "payment_status": b.get("status"),
            "amount_refunded": b.get("amount_refunded"),
            "refund_status": b.get("refund_status"),
        }
        lifecycle.append(snap)
        log(f"    C4 t+{snap['t_plus_s']}s: amount_refunded={snap['amount_refunded']} "
            f"refund_status={snap['refund_status']} payment_status={snap['payment_status']}")
    out["c4_lifecycle"] = lifecycle
    log("    DECISION: sets whether the aggregate bound may read amount_refunded "
        "immediately or must sum the refunds collection.")

    return out


# ---------------------------------------------------------------------------
# BLOCK D — notes mechanics
# ---------------------------------------------------------------------------

def block_d(client, secret, payment_id: str) -> dict[str, Any]:
    log("BLOCK D — notes mechanics")
    out: dict[str, Any] = {}

    r1 = request(client, "PATCH", f"/payments/{payment_id}",
                 json_body={"notes": {"pb_a": "1", "pb_b": "2"}})
    show("D1 PATCH notes {pb_a,pb_b}", r1, secret)

    r2 = request(client, "PATCH", f"/payments/{payment_id}",
                 json_body={"notes": {"pb_c": "3"}})
    show("D2 PATCH notes {pb_c}", r2, secret)
    if r2["ok"]:
        notes = (r2["body"] or {}).get("notes") or {}
        merged = "pb_a" in notes
        out["merge_semantics"] = "MERGE" if merged else "REPLACE"
        out["notes_after"] = notes
        log(f"    DECISION: PATCH is {out['merge_semantics']}."
            + ("  Every trial must send the FULL 8-key map or the 15-key ceiling is hit."
               if merged else "  Full-map writes are equivalent; simplest case."))

    check_deadline()

    for n in (240, 300, 520):
        r = request(client, "PATCH", f"/payments/{payment_id}",
                    json_body={"notes": {"pb_len": "A" * n}})
        ok = r["ok"]
        out[f"value_len_{n}"] = {"status": r["status"], "ok": ok,
                                 "error": r.get("error_description")}
        log(f"    D3 notes value length {n}: {'accepted' if ok else 'REJECTED'}")
    log("    DECISION: sets the per-key payload ceiling for the corpus generator.")
    return out


# ---------------------------------------------------------------------------
# BLOCK E — ground truth read-back
# ---------------------------------------------------------------------------

def block_e(client, secret, payment_id: str) -> dict[str, Any]:
    log("BLOCK E — ground-truth read-back (this IS the measurement)")
    out: dict[str, Any] = {}

    r = request(client, "GET", f"/payments/{payment_id}/refunds", params={"count": 100})
    show("E1 GET /payments/:id/refunds", r, secret)
    if r["ok"]:
        items = (r["body"] or {}).get("items", [])
        out["count"] = len(items)
        out["items"] = [
            {"id": i.get("id"), "amount": i.get("amount"), "status": i.get("status"),
             "receipt": i.get("receipt"), "notes": i.get("notes"),
             "created_at": i.get("created_at")}
            for i in items
        ]
        have_receipt = sum(1 for i in items if i.get("receipt"))
        have_notes = sum(1 for i in items if i.get("notes"))
        out["receipt_roundtrips"] = have_receipt
        out["notes_roundtrips"] = have_notes
        log(f"    {len(items)} refund(s); receipt present on {have_receipt}, "
            f"notes present on {have_notes}")
        log("    DECISION: if receipt and notes do not round-trip here, there is no "
            "zero-labelling ground truth and the central claim dies.")
        (OUT_DIR / "evidence_refunds.json").write_text(
            json.dumps(r["body"], indent=2), encoding="utf-8")
    return out


# ---------------------------------------------------------------------------

_finished = False


def finish() -> None:
    """Write the results file. Idempotent — early-return paths and the finally
    block both call this, and writing twice would double the log noise."""
    global _finished
    if _finished:
        return
    _finished = True
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _results["finished_at"] = datetime.now(UTC).isoformat()
    _results["elapsed_seconds"] = int(time.monotonic() - _started)
    (OUT_DIR / "kg1_result.json").write_text(json.dumps(_results, indent=2), encoding="utf-8")
    log(f"results -> {OUT_DIR / 'kg1_result.json'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--payment-id", help="use an existing captured payment; skips block B")
    ap.add_argument("--blocks", default="A,B,C,D,E")
    args = ap.parse_args()
    blocks = {b.strip().upper() for b in args.blocks.split(",")}

    key_id, secret = load_env()
    try:
        import httpx
    except ImportError:
        print("FATAL: httpx missing.  .venv/Scripts/python.exe -m pip install httpx",
              file=sys.stderr)
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _results["started_at"] = datetime.now(UTC).isoformat()
    _results["key_id_prefix"] = key_id[:13] + "..."  # never the whole key, never the secret

    log(f"PayBound KG-1 — test mode confirmed ({key_id[:13]}...). 60-minute hard stop.")
    log("")

    client = httpx.Client(
        transport=httpx.HTTPTransport(retries=0),  # a hidden retry is how a 502 becomes two refunds
        timeout=httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=5.0),
        headers={"Authorization": auth_header(key_id, secret),
                 "Content-Type": "application/json"},
    )

    try:
        if "A" in blocks:
            _results["A"] = block_a(client, secret)
            if not _results["A"].get("ok"):
                finish()
                return 1
            log("")
            check_deadline()

        payment_id = args.payment_id
        if "B" in blocks and not payment_id:
            _results["B"] = block_b(client, key_id, secret)
            payment_id = _results["B"].get("payment_id")
            log("")
            check_deadline()

        if not payment_id:
            log("No captured payment available — blocks C/D/E cannot run.")
            log("Pay one order manually in a browser, then rerun with --payment-id pay_XXX")
            finish()
            return 3

        if "C" in blocks:
            _results["C"] = block_c(client, secret, payment_id)
            log("")
            check_deadline()
        if "D" in blocks:
            _results["D"] = block_d(client, secret, payment_id)
            log("")
            check_deadline()
        if "E" in blocks:
            _results["E"] = block_e(client, secret, payment_id)
            log("")
    finally:
        client.close()
        finish()

    c1 = (_results.get("C") or {}).get("c1", {})
    log("=" * 68)
    if c1.get("refund_id"):
        log("KG-1: GREEN on the kill question — a refund object exists in Razorpay's")
        log("      ledger, created against test-mode keys. Read kg1_result.json for")
        log("      the idempotency, lifecycle and notes findings before Phase 2.")
    elif c1.get("verdict"):
        log(f"KG-1: NOT GREEN — {c1['verdict']}. Use the pre-committed branch.")
    else:
        log("KG-1: INCOMPLETE — see kg1_result.json.")
    log("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

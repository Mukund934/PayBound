#!/usr/bin/env python3
"""Drive one Razorpay Standard Checkout to a captured payment, headless.

This is the seeder's core loop, extracted so it can be debugged on its own. The
architecture lock gates the whole corpus on this working (risk R5), and the
first attempt inside the KG-1 spike stalled on a contact-details modal that the
`prefill` block was supposed to prevent.

    python scripts/seed_one.py                 # headless, one payment
    python scripts/seed_one.py --headed        # watch it
    python scripts/seed_one.py --amount 249900 # the hero-case amount

Every step writes a numbered screenshot to `spike_out/seed/`, because a headless
checkout that fails at step 4 of 7 is otherwise a black box, and "it did not
work" is not a diagnosis you can act on at 2 am.

Test instruments (Razorpay's published test-mode values, not real ones):
    card  5267 3181 8797 5449 (DOMESTIC India). 4111... is international
          and Razorpay rejects it with 'International cards are not supported'.
    upi   success@razorpay
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

API = "https://api.razorpay.com/v1"
OUT = REPO / "spike_out" / "seed"
PORT = 8801

_step = 0


def log(msg: str) -> None:
    print(f"    {msg}", flush=True)


def shot(page: Any, name: str) -> None:
    global _step
    _step += 1
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{_step:02d}_{name}.png"
    try:
        path.write_bytes(page.screenshot(full_page=False))
    except Exception as exc:  # a closed page still must not kill the run
        log(f"screenshot {name} failed: {type(exc).__name__}")
        return
    log(f"[shot] {path.name}")


def load_env() -> tuple[str, str]:
    env_path = REPO / ".env"
    values: dict[str, str] = {}
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = v.strip().strip('"').strip("'")
    key_id = values.get("RZP_KEY_ID", "")
    secret = values.get("RZP_KEY_SECRET", "")
    if not key_id or not secret:
        sys.exit("FATAL: RZP_KEY_ID / RZP_KEY_SECRET missing from .env")
    if not key_id.startswith("rzp_test_"):
        sys.exit("FATAL: not a test key. Refusing to open a socket.")
    return key_id, secret


# The checkout host page. `prefill.contact` carries the country code — without
# it Razorpay shows a "Contact details" modal and the automation stalls there,
# which is exactly what happened on the first attempt.
CHECKOUT_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>PayBound seed</title></head>
<body style="font-family:system-ui;padding:24px">
<h3 id="status">opening checkout...</h3>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<script>
const q = new URLSearchParams(location.search);
const rzp = new Razorpay({
  key: q.get('key'),
  order_id: q.get('order_id'),
  amount: q.get('amount'),
  currency: 'INR',
  name: 'PayBound Seed Merchant',
  description: 'PayBound seeded order',
  image: '',
  prefill: {
    name: 'PayBound Buyer',
    email: 'buyer@example.com',
    contact: '9845276391'
  },
  notes: { pb_seed: '1' },
  theme: { color: '#12326e' },
  handler: function (res) {
    document.getElementById('status').textContent = 'PAID ' + res.razorpay_payment_id;
    document.title = 'PAID:' + res.razorpay_payment_id;
  },
  modal: { escape: false, ondismiss: function(){ document.title = 'DISMISSED'; } }
});
rzp.open();
</script>
</body></html>
"""


def serve(directory: Path, port: int) -> HTTPServer:
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, directory=str(directory), **kw)

        def log_message(self, *a: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def create_env_client(key_id: str, secret: str) -> Any:
    """An httpx client with the auth header and retries disabled."""
    import httpx

    token = base64.b64encode(f"{key_id}:{secret}".encode()).decode()
    return httpx.Client(
        transport=httpx.HTTPTransport(retries=0),
        timeout=httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=5.0),
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
    )


def create_order(client: Any, amount: int, receipt: str) -> str:
    r = client.post(
        f"{API}/orders",
        json={
            "amount": amount,
            "currency": "INR",
            "receipt": receipt,
            "notes": {"pb_seed": "1"},
        },
    )
    r.raise_for_status()
    return str(r.json()["id"])


def _contexts(page: Any) -> list[Any]:
    """The page and every frame, checkout frames first.

    Razorpay Standard Checkout renders inside an iframe served from
    api.razorpay.com. Page-level selectors therefore match nothing at all, which
    is not a "selector drifted" failure — it is a "you are looking in the wrong
    document" failure, and it looks identical from the outside. The first
    version of this seeder reported "no contact modal (prefill took)" while a
    screenshot showed the modal open on screen.
    """
    frames = [f for f in page.frames if "razorpay.com" in (f.url or "")]
    others = [f for f in page.frames if f not in frames]
    return [*frames, page, *others]


def _click_first(page: Any, selectors: list[str], *, timeout: int = 4000) -> bool:
    """Click the first selector present and visible in any document.

    Razorpay's checkout markup is not a stable contract, so every interaction is
    a list of candidates rather than one selector. A miss is normal and must not
    raise — the caller decides whether it mattered.
    """
    for ctx in _contexts(page):
        for sel in selectors:
            try:
                loc = ctx.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=timeout)
                    return True
            except Exception:
                continue
    return False


def _fill_first(page: Any, selectors: list[str], value: str) -> bool:
    """Type ``value`` into the first matching field, with real key events.

    ``fill()`` sets ``.value`` and dispatches a single synthetic ``input``
    event. Razorpay's checkout validates on the key sequence, so a filled field
    keeps its "Please enter a valid mobile number" error even when the value
    sitting in it is valid — which is what the screenshots showed for two runs
    with two different, both-legitimate, numbers. Clicking, clearing and typing
    reproduces what a person does, and the validator agrees.
    """
    for ctx in _contexts(page):
        for sel in selectors:
            try:
                loc = ctx.locator(sel).first
                if not (loc.count() and loc.is_visible()):
                    continue
                loc.click(timeout=4000)
                loc.press("Control+a")
                loc.press("Delete")
                loc.type(value, delay=60)
                loc.press("Tab")  # blur, so validation runs before the submit
                return True
            except Exception:
                continue
    return False


def drive_checkout(page: Any, url: str) -> str | None:
    """Walk the checkout to a captured payment. Returns a payment id or None."""
    page.goto(url, wait_until="load", timeout=45_000)
    page.wait_for_timeout(6000)
    shot(page, "checkout_open")

    # --- Step 1: the contact-details modal, if it appeared ------------------
    # The field names below came out of scripts/diag_checkout.py, which dumped
    # every input in the checkout frame. Guessing at them cost three runs: the
    # card inputs are `card.number`, not `card[number]`, and both the card
    # number and the contact field are `type=tel`, so a `input[type='tel']`
    # fallback silently targets the wrong one.
    if _fill_first(page, ["input[name='contact']"], "9845276391"):
        log("contact modal appeared -> filled")
        page.wait_for_timeout(800)
        _click_first(page, ["button:has-text('Continue')", "text=Continue"])
        page.wait_for_timeout(3500)
        shot(page, "after_contact")
    else:
        log("no contact modal (prefill took)")

    # --- Step 2: choose Card -----------------------------------------------
    # Card is the most deterministic method in test mode: a published test PAN
    # and a simulator page with an explicit Success button. UPI's success@razorpay
    # is fewer steps but depends on UPI being enabled on the account.
    if _click_first(page, ["div[data-method='card']", "text=Cards", "text=Card"]):
        log("selected Card")
    else:
        log("could not select Card - may already be on the card form")
    page.wait_for_timeout(2500)
    shot(page, "card_selected")

    # --- Step 3: card details ----------------------------------------------
    if not _fill_first(page, ["input[name='card.number']"], "5267318187975449"):
        log("card number field not found")
        shot(page, "card_number_missing")
        return None
    _fill_first(page, ["input[name='card.expiry']"], "1230")
    _fill_first(page, ["input[name='card.cvv']"], "123")
    _fill_first(page, ["input[name='card.name']"], "PayBound Buyer")
    page.wait_for_timeout(800)
    shot(page, "card_filled")

    # --- Step 4: pay --------------------------------------------------------
    if not _click_first(
        page,
        [
            "button:has-text('Pay')",
            "text=Pay Now",
            "button[type='submit']",
            "#redesign-v15-cta",
        ],
        timeout=6000,
    ):
        log("pay button not found")
        shot(page, "pay_missing")
        return None
    log("clicked Pay")
    page.wait_for_timeout(7000)
    shot(page, "after_pay")

    # --- Step 5: the test-mode 3DS simulator -------------------------------
    # Razorpay's test bank page offers Success / Failure. It may be in an
    # iframe, so both the page and every frame are searched.
    for attempt in range(3):
        clicked = _click_first(
            page,
            [
                "button:has-text('Success')",
                "text=Success",
                "input[value='Success']",
                "a:has-text('Success')",
            ],
            timeout=4000,
        )
        if not clicked:
            for frame in page.frames:
                try:
                    loc = frame.locator("button:has-text('Success'), text=Success").first
                    if loc.count():
                        loc.click(timeout=4000)
                        clicked = True
                        break
                except Exception:
                    continue
        if clicked:
            log(f"clicked Success on the simulator (attempt {attempt + 1})")
            page.wait_for_timeout(6000)
            shot(page, f"after_success_{attempt + 1}")
            break
        page.wait_for_timeout(3000)

    page.wait_for_timeout(4000)
    shot(page, "final")

    title = page.title()
    if title.startswith("PAID:"):
        return title.split("PAID:", 1)[1]
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--amount", type=int, default=249_900, help="paise")
    ap.add_argument("--headed", action="store_true")
    args = ap.parse_args()

    key_id, secret = load_env()
    import httpx
    from playwright.sync_api import sync_playwright

    token = base64.b64encode(f"{key_id}:{secret}".encode()).decode()
    client = httpx.Client(
        transport=httpx.HTTPTransport(retries=0),
        timeout=httpx.Timeout(connect=10.0, read=45.0, write=10.0, pool=5.0),
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
    )

    receipt = f"pb-seed-{int(time.time())}"
    order_id = create_order(client, args.amount, receipt)
    log(f"order {order_id} for {args.amount} paise")

    host = OUT / "host"
    host.mkdir(parents=True, exist_ok=True)
    (host / "index.html").write_text(CHECKOUT_HTML, encoding="utf-8")
    server = serve(host, PORT)
    url = (
        f"http://127.0.0.1:{PORT}/index.html"
        f"?key={key_id}&order_id={order_id}&amount={args.amount}"
    )

    payment_id: str | None = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headed)
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            page.on("console", lambda m: log(f"[console] {m.type}: {m.text[:160]}"))
            try:
                payment_id = drive_checkout(page, url)
            finally:
                browser.close()
    finally:
        server.shutdown()

    # The handler may not have fired even though the payment went through, so
    # the order is the source of truth, not the page title.
    if not payment_id:
        log("handler did not fire - asking the order what happened")
        r = client.get(f"{API}/orders/{order_id}/payments")
        items = r.json().get("items", []) if r.status_code == 200 else []
        for it in items:
            log(f"  payment {it.get('id')} status={it.get('status')}")
        good = [i for i in items if i.get("status") in ("captured", "authorized")]
        if good:
            payment_id = good[0]["id"]

    if not payment_id:
        log("NO PAYMENT CAPTURED. Inspect spike_out/seed/*.png")
        client.close()
        return 1

    pay = client.get(f"{API}/payments/{payment_id}").json()
    log(
        f"CAPTURED {payment_id} status={pay.get('status')} "
        f"amount={pay.get('amount')} method={pay.get('method')}"
    )
    (OUT / "last_payment.json").write_text(json.dumps(pay, indent=2), encoding="utf-8")
    print(payment_id)
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

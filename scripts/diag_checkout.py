#!/usr/bin/env python3
"""Time-boxed diagnostic for the Standard Checkout contact gate.

Two runs of the seeder typed two different valid Indian mobile numbers with real
key events and both kept the error "Please enter a valid mobile number". Either
the field wants a different shape, or the submit path is gated by something
other than the field's contents. This prints what the DOM actually holds instead
of guessing at selectors.

    python scripts/diag_checkout.py
"""

from __future__ import annotations

import json
import sys
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from seed_one import API, CHECKOUT_HTML, create_env_client, load_env  # noqa: E402

OUT = REPO / "spike_out" / "diag"
PORT = 8802


def serve(directory: Path, port: int) -> HTTPServer:
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a: Any, **kw: Any) -> None:
            super().__init__(*a, directory=str(directory), **kw)

        def log_message(self, *a: Any) -> None:
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    key_id, secret = load_env()
    client = create_env_client(key_id, secret)
    r = client.post(
        f"{API}/orders",
        json={"amount": 249900, "currency": "INR", "receipt": f"pb-diag-{int(time.time())}"},
    )
    r.raise_for_status()
    order_id = r.json()["id"]
    print(f"order {order_id}")

    OUT.mkdir(parents=True, exist_ok=True)
    host = OUT / "host"
    host.mkdir(parents=True, exist_ok=True)
    (host / "index.html").write_text(CHECKOUT_HTML, encoding="utf-8")
    server = serve(host, PORT)
    url = f"http://127.0.0.1:{PORT}/index.html?key={key_id}&order_id={order_id}&amount=249900"

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(url, wait_until="load", timeout=45_000)
        page.wait_for_timeout(8000)

        print("\n--- frames ---")
        for f in page.frames:
            print(f"  {f.url[:110]}")

        target = None
        for f in page.frames:
            if "razorpay.com" in (f.url or "") and "checkout" in (f.url or ""):
                target = f
                break
        if target is None:
            print("no checkout frame found")
            browser.close()
            server.shutdown()
            return 1

        print("\n--- every input in the checkout frame ---")
        info = target.evaluate(
            """() => Array.from(document.querySelectorAll('input')).map(function(el){
                 const cs = getComputedStyle(el);
                 return {
                   name: el.name, id: el.id, type: el.type,
                   placeholder: el.placeholder, value: el.value,
                   maxLength: el.maxLength, required: el.required,
                   ariaInvalid: el.getAttribute('aria-invalid'),
                   visible: cs.display !== 'none' && cs.visibility !== 'hidden',
                 };
               })"""
        )
        for i in info:
            print("  " + json.dumps(i))

        print("\n--- visible error text ---")
        errs = target.evaluate(
            """() => Array.from(document.querySelectorAll('*'))
                 .filter(function(e){ return e.children.length===0 && e.textContent
                   && /valid|error|invalid|required/i.test(e.textContent); })
                 .map(function(e){ return e.textContent.trim(); }).slice(0,12)"""
        )
        for e in errs:
            print(f"  {e!r}")

        print("\n--- automation surface as the page sees it ---")
        print("  navigator.webdriver =", page.evaluate("() => navigator.webdriver"))

        # Set the value through the native setter and dispatch React's events,
        # which is what a framework-controlled input actually listens for.
        print("\n--- native setter + react events on the contact field ---")
        res = target.evaluate(
            """() => {
                 const el = document.querySelector("input[name='contact'], input[type='tel']");
                 if (!el) return 'no contact input';
                 const setter = Object.getOwnPropertyDescriptor(
                   window.HTMLInputElement.prototype, 'value').set;
                 setter.call(el, '9876543210');
                 el.dispatchEvent(new Event('input', {bubbles:true}));
                 el.dispatchEvent(new Event('change', {bubbles:true}));
                 el.dispatchEvent(new Event('blur', {bubbles:true}));
                 return 'set to ' + el.value;
               }"""
        )
        print("  ", res)
        page.wait_for_timeout(2500)
        (OUT / "after_native_set.png").write_bytes(page.screenshot())

        errs2 = target.evaluate(
            """() => Array.from(document.querySelectorAll('*'))
                 .filter(function(e){ return e.children.length===0 && e.textContent
                   && /valid mobile/i.test(e.textContent); })
                 .map(function(e){ return e.textContent.trim(); })"""
        )
        print("  error after native set:", errs2)

        browser.close()
    server.shutdown()
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

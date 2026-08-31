#!/usr/bin/env python3
"""Create a Payment Link and drive its hosted page to a captured payment.

Standard Checkout's contact gate could not be driven headless (four variants,
all rejected a valid Indian mobile). The Payment Link hosted page is a different
surface served from rzp.io, and the account probe showed Payment Links are the
one payment instrument this test account can create by API — QR codes and S2S
payment creation both return "URL not found".

    python scripts/pay_link.py            # create a link and try to pay it
    python scripts/pay_link.py --headed   # watch it
    python scripts/pay_link.py --link-only  # just print a link for a human

Screenshots land in spike_out/link/.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from seed_one import API, create_env_client, load_env  # noqa: E402

from paybound.rail.modeguard import assert_test_mode  # noqa: E402

OUT = REPO / "spike_out" / "link"
_step = 0


def log(msg: str) -> None:
    print(f"    {msg}", flush=True)


def shot(page: Any, name: str) -> None:
    global _step
    _step += 1
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        (OUT / f"{_step:02d}_{name}.png").write_bytes(page.screenshot())
        log(f"[shot] {_step:02d}_{name}.png")
    except Exception:
        pass


def contexts(page: Any) -> list[Any]:
    """Every document, Razorpay's first. No URL filter.

    Filtering frames by hostname cost a run: the fully-filled card form's
    "Continue" button sat in a frame the filter excluded, so every click missed
    while the screenshot showed an enabled button. Searching everything is
    cheap; guessing which document holds the control is not.
    """
    preferred = [f for f in page.frames if "razorpay" in (f.url or "") or "rzp.io" in (f.url or "")]
    rest = [f for f in page.frames if f not in preferred]
    return [page, *preferred, *rest]


def click_first(page: Any, selectors: list[str], *, timeout: int = 5000) -> bool:
    for ctx in contexts(page):
        for sel in selectors:
            try:
                loc = ctx.locator(sel).first
                if loc.count() and loc.is_visible():
                    loc.click(timeout=timeout)
                    return True
            except Exception:
                continue
    # Playwright refuses a click when something invisible overlays the target.
    # The control is real and enabled, so dispatch the click directly as a last
    # resort rather than failing the seed.
    for ctx in contexts(page):
        for sel in selectors:
            try:
                loc = ctx.locator(sel).first
                if loc.count():
                    loc.dispatch_event("click")
                    return True
            except Exception:
                continue
    return False


def click_by_text(page: Any, label: str) -> bool:
    """Click any element whose trimmed text is exactly ``label``.

    Razorpay's submit controls are not always ``<button>``. This walks the DOM
    of every frame and clicks the smallest element carrying the label, which
    survives a div-styled-as-a-button.
    """
    for ctx in contexts(page):
        try:
            hit = ctx.evaluate(
                """(label) => {
                     const els = Array.from(document.querySelectorAll('button,div,span,a'));
                     const m = els.filter(e => e.textContent && e.textContent.trim() === label
                                && e.offsetParent !== null);
                     if (!m.length) return false;
                     m[m.length - 1].click();
                     return true;
                   }""",
                label,
            )
            if hit:
                return True
        except Exception:
            continue
    return False


def type_first(page: Any, selectors: list[str], value: str) -> bool:
    for ctx in contexts(page):
        for sel in selectors:
            try:
                loc = ctx.locator(sel).first
                if not (loc.count() and loc.is_visible()):
                    continue
                loc.click(timeout=4000)
                loc.press("Control+a")
                loc.press("Delete")
                loc.type(value, delay=50)
                return True
            except Exception:
                continue
    return False


def create_link(client: Any, amount: int) -> dict[str, Any]:
    r = client.post(
        f"{API}/payment_links",
        json={
            "amount": amount,
            "currency": "INR",
            "accept_partial": False,
            "description": "PayBound seeded order",
            "customer": {
                "name": "PayBound Buyer",
                "email": "buyer@example.com",
                "contact": "+919845276391",
            },
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "notes": {"pb_seed": "1"},
        },
    )
    r.raise_for_status()
    return dict(r.json())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--amount", type=int, default=249_900)
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--link-only", action="store_true")
    args = ap.parse_args()

    key_id, secret = load_env()
    assert_test_mode(key_id, operation="pay_link")
    client = create_env_client(key_id, secret)

    link = create_link(client, args.amount)
    url = link["short_url"]
    log(f"payment link {link['id']} -> {url}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "last_link.json").write_text(json.dumps(link, indent=2), encoding="utf-8")

    if args.link_only:
        print(url)
        client.close()
        return 0

    from playwright.sync_api import sync_playwright

    paid = False
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not args.headed)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        try:
            page.goto(url, wait_until="load", timeout=60_000)
            page.wait_for_timeout(6000)
            shot(page, "link_open")

            # The hosted page usually opens with a Pay Now button that launches
            # the same checkout widget.
            if click_first(page, ["button:has-text('Pay Now')", "text=Pay Now", "text=Pay "]):
                log("clicked Pay Now")
                page.wait_for_timeout(5000)
                shot(page, "after_pay_now")

            # Contact gate, if this surface has one.
            if type_first(page, ["input[name='contact']"], "9845276391"):
                log("filled contact")
                page.wait_for_timeout(600)
                click_first(page, ["button:has-text('Continue')", "text=Continue"])
                page.wait_for_timeout(3500)
                shot(page, "after_contact")

            if click_first(page, ["div[data-method='card']", "text=Cards", "text=Card"]):
                log("selected Card")
                page.wait_for_timeout(2500)

            if type_first(page, ["input[name='card.number']"], "5267318187975449"):
                log("filled card number")
                type_first(page, ["input[name='card.expiry']"], "1230")
                type_first(page, ["input[name='card.cvv']"], "123")
                type_first(page, ["input[name='card.name']"], "PayBound Buyer")
                # The link surface asks for an email on the card form itself and
                # will not submit without it, even though the link was created
                # with a customer email.
                type_first(
                    page,
                    ["input[name='email']", "input[placeholder='Enter Email']"],
                    "buyer@example.com",
                )
                page.wait_for_timeout(800)
                shot(page, "card_filled")
                # The submit control on this surface reads "Continue", not
                # "Pay" — the button label differs from Standard Checkout's.
                submitted = click_first(
                    page,
                    [
                        "button:has-text('Continue')",
                        "text=Continue",
                        "button:has-text('Pay')",
                        "button[type='submit']",
                    ],
                ) or click_by_text(page, "Continue")
                if submitted:
                    log("submitted the card form")
                    page.wait_for_timeout(7000)
                    shot(page, "after_submit")

                    # Everything after submit is a sequence of interstitials
                    # whose order is not guaranteed: a save-card upsell, then
                    # the test-mode 3DS simulator. Rather than hard-coding the
                    # order, sweep for whichever is on screen until the payment
                    # resolves. Each label was observed in a screenshot; none is
                    # guessed.
                    steps = [
                        ("Maybe later", "declined the save-card upsell"),
                        ("Skip", "skipped an optional step"),
                        ("Success", "clicked Success on the 3DS simulator"),
                    ]
                    for sweep in range(8):
                        acted = False

                        # The OTP gate. Razorpay's test mode accepts 1234; there
                        # is no real message and no real phone involved.
                        if type_first(
                            page,
                            [
                                "input[name='otp']",
                                "input[placeholder='Enter OTP']",
                                "input[placeholder*='OTP']",
                            ],
                            "1234",
                        ):
                            log("entered the test-mode OTP")
                            page.wait_for_timeout(700)
                            click_by_text(page, "Continue") or click_first(
                                page, ["button:has-text('Continue')", "text=Continue"]
                            )
                            acted = True
                            page.wait_for_timeout(8000)
                            shot(page, f"sweep{sweep + 1}_otp")

                        if not acted:
                            for label, message in steps:
                                if click_by_text(page, label) or click_first(
                                    page,
                                    [
                                        f"button:has-text('{label}')",
                                        f"a:has-text('{label}')",
                                        f"input[value='{label}']",
                                    ],
                                ):
                                    log(message)
                                    acted = True
                                    page.wait_for_timeout(6000)
                                    shot(page, f"sweep{sweep + 1}_{label.split()[0].lower()}")
                                    break
                        if not acted:
                            page.wait_for_timeout(3000)
                        if page.url and "payment_link_status" in (page.url or ""):
                            break
            else:
                log("card number field not reachable on this surface either")
            shot(page, "final")
        finally:
            browser.close()

    # The link is the source of truth, not the page.
    time.sleep(2)
    st = client.get(f"{API}/payment_links/{link['id']}").json()
    log(f"link status: {st.get('status')}  amount_paid={st.get('amount_paid')}")
    payments = st.get("payments") or []
    for pmt in payments:
        log(f"  payment {pmt.get('payment_id')} status={pmt.get('status')}")
        if pmt.get("status") == "captured":
            paid = True
            print(pmt["payment_id"])

    if not paid:
        log("NOT PAID. Inspect spike_out/link/*.png")
        log(f"A human can pay this link in ~60s: {url}")
    client.close()
    return 0 if paid else 1


if __name__ == "__main__":
    raise SystemExit(main())

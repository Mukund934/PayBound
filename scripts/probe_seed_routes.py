#!/usr/bin/env python3
"""Probe every API-only route to a captured test-mode payment.

Standard Checkout could not be driven headless: the contact-details gate rejects
a valid Indian mobile across four variants of prefill and input technique. Risk
R5 in the architecture lock, and the lock's instruction is explicit — do not
spend a day fighting it, spend ten minutes on BharatQR and then take the
pre-committed branch.

This script is those ten minutes, widened to every documented API-only route,
because a browserless seeder is worth more than the browser one even if the
browser one could be made to work: it deletes Playwright from the critical path
entirely.

Every call is read-only or creates a test-mode object. Nothing here can move
real money — the mode guard refuses a non-test key before the first socket.

    python scripts/probe_seed_routes.py
"""

from __future__ import annotations

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

OUT = REPO / "spike_out" / "probe"
RESULTS: dict[str, Any] = {}


def show(label: str, r: Any) -> dict[str, Any]:
    try:
        body = r.json()
    except Exception:
        body = {"_raw": r.text[:400]}
    desc = ""
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        desc = body["error"].get("description", "")
    ok = 200 <= r.status_code < 300
    print(f"  [{r.status_code}] {label}" + (f" -- {desc}" if desc else ""))
    return {"status": r.status_code, "ok": ok, "error": desc, "body": body}


def main() -> int:
    key_id, secret = load_env()
    assert_test_mode(key_id, operation="probe_seed_routes")
    client = create_env_client(key_id, secret)
    OUT.mkdir(parents=True, exist_ok=True)

    print("\n=== ROUTE 1: QR code + test-pay simulation ===")
    # A QR code is a first-class payment instrument with a documented test-mode
    # simulator. If it works it produces a real captured payment with no browser.
    qr = client.post(
        f"{API}/payments/qr_codes",
        json={
            "type": "upi_qr",
            "name": "PayBound seed",
            "usage": "multiple_use",
            "fixed_amount": False,
            "description": "PayBound seeding",
            "notes": {"pb_seed": "1"},
        },
    )
    RESULTS["qr_create"] = show("POST /payments/qr_codes", qr)
    qr_id = qr.json().get("id") if RESULTS["qr_create"]["ok"] else None

    if qr_id:
        print(f"  qr_code: {qr_id}")
        for path, payload in [
            (f"/payments/qr_codes/{qr_id}/test", {"amount": 249900}),
            ("/payments/bharatqr/pay/test", {"amount": 249900, "qr_code_id": qr_id}),
        ]:
            r = client.post(f"{API}{path}", json=payload)
            RESULTS[f"qr_pay::{path}"] = show(f"POST {path}", r)
            if 200 <= r.status_code < 300:
                print("  *** SIMULATED PAYMENT CREATED ***")
                break

    print("\n=== ROUTE 2: Payment Links ===")
    # The lock permits at most two Payment Links burned in the spike. A link is
    # created by API but still needs a browser to pay, so this probe only
    # establishes whether the endpoint is available on the account.
    pl = client.post(
        f"{API}/payment_links",
        json={
            "amount": 249900,
            "currency": "INR",
            "accept_partial": False,
            "description": "PayBound seed link",
            "customer": {"name": "PayBound Buyer", "email": "buyer@example.com",
                         "contact": "+919876543210"},
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "notes": {"pb_seed": "1"},
        },
    )
    RESULTS["payment_link"] = show("POST /payment_links", pl)
    if RESULTS["payment_link"]["ok"]:
        body = pl.json()
        print(f"  link: {body.get('short_url')}  id={body.get('id')}")
        RESULTS["payment_link"]["short_url"] = body.get("short_url")

    print("\n=== ROUTE 3: server-to-server payment creation ===")
    # Only available on accounts with S2S enabled. Expected to 400 on a fresh
    # account; probed so the failure is recorded rather than assumed.
    order = client.post(
        f"{API}/orders",
        json={"amount": 249900, "currency": "INR", "receipt": f"pb-probe-{int(time.time())}"},
    )
    RESULTS["order"] = show("POST /orders", order)
    if RESULTS["order"]["ok"]:
        oid = order.json()["id"]
        s2s = client.post(
            f"{API}/payments/create/upi",
            json={
                "amount": 249900,
                "currency": "INR",
                "order_id": oid,
                "email": "buyer@example.com",
                "contact": "+919876543210",
                "method": "upi",
                "upi": {"flow": "collect", "vpa": "success@razorpay"},
            },
        )
        RESULTS["s2s_upi"] = show("POST /payments/create/upi", s2s)

    print("\n=== ROUTE 4: what the account actually has ===")
    for label, path in [
        ("payments", "/payments?count=3"),
        ("refunds", "/refunds?count=3"),
        ("settlements", "/settlements?count=1"),
    ]:
        r = client.get(f"{API}{path}")
        RESULTS[f"read::{label}"] = show(f"GET {path}", r)
        if 200 <= r.status_code < 300:
            items = r.json().get("items", [])
            print(f"       {len(items)} item(s)")
            if label == "payments" and items:
                for it in items:
                    print(f"       - {it.get('id')} {it.get('status')} {it.get('amount')}")

    (OUT / "probe_results.json").write_text(json.dumps(RESULTS, indent=2), encoding="utf-8")
    print(f"\nresults -> {OUT / 'probe_results.json'}")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

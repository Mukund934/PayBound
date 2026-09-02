#!/usr/bin/env python3
"""Drive ONE case to a real refund object in Razorpay's ledger.

    python scripts/execute_one.py --payment pay_X --sibling pay_Y --route DUPLICATE_CHARGE
    python scripts/execute_one.py --payment pay_X --sibling pay_Y            # model routes

This is the demo's hero beat and, until now, the only part of the architecture
that had never actually run. ``Mode.EXECUTE`` was documented in ``runner.py``
-- "the broker POSTs, a real refund object appears in Razorpay's ledger" -- but
``executor`` defaulted to ``None`` and nothing in the repository ever passed
one, so the mode could not be entered from anywhere.

**Trusted state is read from Razorpay, not from a fixture.** The amount, the
capture timestamps and the existing refund total all come from
``GET /v1/payments/:id``. The only merchant-side facts supplied here are the
ones a merchant genuinely owns and Razorpay does not know: the order lines and
the fulfilment record.

``--route`` supplies the reason code without a model call, for when the free
tier is exhausted. It changes **nothing** about the authority argument: the
amount is still computed by ``core/policy/amount.py`` from trusted state, the
preconditions are still re-verified, and the refund still goes through the
single-use capability and the write-ahead intent. It does mean that particular
run did not exercise the router, and the trial row records which happened.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from paybound.core.money import format_inr  # noqa: E402
from paybound.core.policy.decide import decide  # noqa: E402
from paybound.core.types import (  # noqa: E402
    CatalogueLine,
    Fulfilment,
    FulfilmentState,
    OrderGroup,
    OrderLine,
    Outcome,
    PaymentFacts,
    ReasonCode,
    SiblingPayment,
    TrustedState,
)
from paybound.harness.execute import LedgerExecutor  # noqa: E402
from paybound.ids import new_intent_id  # noqa: E402
from paybound.ledger.capabilities import mint_case_capabilities  # noqa: E402
from paybound.ledger.db import connect  # noqa: E402
from paybound.rail.client import RazorpayClient  # noqa: E402

EVIDENCE = REPO / "evidence" / "execute"


def load_env() -> tuple[str, str]:
    values = {}
    for line in (REPO / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip()
    return values["RZP_KEY_ID"], values["RZP_KEY_SECRET"]


def fetch(client: RazorpayClient, payment_id: str) -> dict:
    resp = client.get_payment(payment_id)
    if not resp.ok:
        sys.exit(f"FATAL: could not read {payment_id} (status {resp.status})")
    return resp.body or {}


def build_state(payment: dict, sibling: dict, *, now: int) -> TrustedState:
    """Trusted state from Razorpay's own facts plus merchant-owned records.

    ``sha256:`` prefix on the sibling: ``core/`` must never hold a raw ``pay_``
    id, and I-04 is discharged by grepping for that string outside the adapter.
    """
    import hashlib

    sib_hash = "sha256:" + hashlib.sha256(sibling["id"].encode()).hexdigest()[:32]
    amount = int(payment["amount"])
    return TrustedState(
        now_epoch_s=now,
        payment=PaymentFacts(
            amount_paise=amount,
            amount_refunded_paise=int(payment.get("amount_refunded") or 0),
            prior_refund_total_paise=int(payment.get("amount_refunded") or 0),
            created_at_epoch_s=int(payment["created_at"]),
            method=str(payment.get("method") or "card"),
            status=str(payment.get("status") or "captured"),
            captured=bool(payment.get("captured")),
        ),
        order_status="paid",
        order_created_at_epoch_s=int(payment["created_at"]),
        lines=(OrderLine(sku="SKU-DEMO", qty=1, unit_price_paid_paise=amount),),
        catalogue=(
            CatalogueLine(
                sku="SKU-DEMO",
                unit_price_paise=amount,
                effective_from_epoch_s=int(payment["created_at"]) - 86_400,
            ),
        ),
        # Merchant-owned facts. Razorpay does not know these and never asserts
        # them; they are exactly the half of the world a payments API cannot see.
        fulfilment=Fulfilment(state=FulfilmentState.NOT_PICKED_UP, carrier_scan_id=None),
        group=OrderGroup(group_id="grp_demo", capture_count=2, settled=False),
        siblings=(
            SiblingPayment(
                payment_id_hash=sib_hash,
                amount_paise=int(sibling["amount"]),
                method=str(sibling.get("method") or "card"),
                created_at_epoch_s=int(sibling["created_at"]),
            ),
        ),
        prior_refund_reasons=(),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--payment", required=True, help="the payment to refund")
    ap.add_argument("--sibling", required=True, help="the duplicate capture")
    ap.add_argument("--route", default=None, help="reason code, skipping the model")
    ap.add_argument("--dry", action="store_true", help="decide, do not execute")
    args = ap.parse_args()

    key_id, secret = load_env()
    client = RazorpayClient(key_id=key_id, key_secret=secret)

    payment = fetch(client, args.payment)
    sibling = fetch(client, args.sibling)
    now = int(time.time())
    state = build_state(payment, sibling, now=now)

    print(f"payment  {args.payment}  {format_inr(state.payment.amount_paise)}  "
          f"{state.payment.status}  refunded={state.payment.amount_refunded_paise}")
    print(f"sibling  {args.sibling}  {format_inr(int(sibling['amount']))}")
    print()

    if args.route:
        reason = ReasonCode(args.route)
        routing = "supplied on the command line (--route), no model call"
    else:
        sys.exit(
            "the model path needs quota; pass --route to run the policy and rail "
            "without it, and the trial will record that no router was exercised"
        )

    decision = decide(reason, state)
    print(f"routed   {reason.value}  ({routing})")
    print(f"decision {decision.outcome}  clause={decision.clause_id}")
    for p in decision.predicates:
        print(f"           {p.result!s:<8} {p.name}  {p.source_field}  {p.observed}")
    if decision.amount_paise is not None:
        print(f"amount   {format_inr(decision.amount_paise)}  "
              "computed by core/policy/amount.py, not by any model")
    print()

    if decision.outcome is not Outcome.ALLOW:
        print("not an ALLOW; nothing is sent. Zero outbound POSTs.")
        return 0
    if args.dry:
        print("--dry: stopping before the socket.")
        return 0

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    conn = connect(EVIDENCE / "intents.db")
    case_id = f"case_demo_{int(time.time())}"
    conn.execute("BEGIN IMMEDIATE")
    _read_cap, write_cap = mint_case_capabilities(
        conn,
        case_id=case_id,
        session_id="demo",
        principal_id="demo",
        payment_id=args.payment,
        now=now,
    )
    conn.execute("COMMIT")
    write_token = write_cap.token
    intent_id = new_intent_id()

    executor = LedgerExecutor(
        conn, client, session_id="demo", principal_id="demo", now=now
    )
    outcome = executor(
        trial=None,
        state=state,
        amount_paise=decision.amount_paise,
        clause_id=decision.clause_id,
        case_id=case_id,
        payment_id=args.payment,
        write_token=write_token,
        intent_id=intent_id,
    )

    print(f"refund   {outcome['refund_id']}  "
          f"{format_inr(outcome['ledger_amount_paise'] or 0)}  "
          f"bucket={outcome['bucket']}")
    print(f"receipt  {outcome['receipt']}")

    readback = client.list_payment_refunds(args.payment)
    record = {
        "executed_at": now,
        "payment_id": args.payment,
        "sibling_id": args.sibling,
        "routed": reason.value,
        "routing_provenance": routing,
        "clause_id": decision.clause_id,
        "amount_paise": decision.amount_paise,
        "amount_fn": "paybound.core.policy.amount.full_payment",
        "intent_id": intent_id,
        "refund": {k: v for k, v in outcome.items() if k != "raw_responses"},
        "razorpay_readback": readback.body,
    }
    out = EVIDENCE / f"execute_{intent_id}.json"
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {out.relative_to(REPO)}")
    print("read back from GET /v1/payments/:id/refunds — external ground truth.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

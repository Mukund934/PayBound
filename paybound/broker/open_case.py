"""Case opening and the projection the agent is allowed to see.

Everything in this module happens **before the first model call**. That ordering
is the whole security argument, so it is worth stating precisely what it buys:
by the time a model sees anything, the payment has already been chosen by
deterministic lookup, the capability tokens have already been minted and written
to disk, and the projection has already been narrowed to typed facts. There is
no later step at which the model's output could widen any of those.

The projection
--------------
``CaseView`` is what ``get_case`` returns. It carries **derived typed facts
only**: no ``pay_`` id, no raw Razorpay JSON, no PII beyond what the case needs.
Two consequences the tests pin:

* A ``pay_`` id can never appear in agent-visible bytes, so the I-04 grep over
  ``events.jsonl`` returns zero outside adapter events.
* Amounts are rendered in **rupees, as strings, for reading**. The authoritative
  quantity stays in paise inside ``core/``. If a number the model saw ever
  became the number that was refunded, that would be the whole failure, so the
  two are kept in different units and different types.

The untrusted span
------------------
``customer_message`` is tagged ``L0_UNTRUSTED`` **at construction**, in code, not
by convention and not by a downstream classifier. It is the only field in the
view that did not come from the merchant's own records, and it is the only field
the policy layer never reads.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Final

from paybound.core.money import Paise, format_inr, sub
from paybound.core.types import TrustedState

__all__ = [
    "CaseLine",
    "CaseView",
    "OpenCase",
    "UntrustedSpan",
    "build_case_view",
    "span_id_of",
]

TRUST_LABEL: Final[str] = "L0_UNTRUSTED"


def span_id_of(text: str) -> str:
    """A stable id for an untrusted span, derived from its content.

    Content-derived so the same message in two runs gets the same id, which
    makes the corpus joinable across runs without a side table.
    """
    return "spn_" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class UntrustedSpan:
    """Customer prose. Tagged at ingestion, in code.

    ``sha256`` and ``chars`` exist so the event log can record *which* span was
    involved without embedding the text. An event stream that carries injection
    payloads is itself a re-injection surface the moment somebody pastes logs
    into an assistant to debug them — a security artifact whose logs are an
    attack vector has not finished the job.
    """

    span_id: str
    channel: str
    text: str
    trust: str = TRUST_LABEL

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()

    @property
    def chars(self) -> int:
        return len(self.text)

    def for_event(self) -> dict[str, Any]:
        """The event-log shape. Deliberately excludes ``text``."""
        return {
            "span_id": self.span_id,
            "channel": self.channel,
            "label": self.trust,
            "sha256": self.sha256,
            "chars": self.chars,
        }


@dataclass(frozen=True, slots=True)
class CaseLine:
    """One order line as the agent sees it. Rupee strings, for reading."""

    sku: str
    qty: int
    unit_price_paid_inr: str


@dataclass(frozen=True, slots=True)
class CaseView:
    """Exactly what ``get_case`` returns. Nothing here designates a payment."""

    case_id: str
    currency: str
    paid_inr: str
    days_since_capture: int | None
    fulfilment_status: str | None
    prior_refund_total_inr: str
    refundable_headroom_inr: str
    line_items: tuple[CaseLine, ...]
    customer_message: dict[str, Any]

    def to_tool_payload(self) -> dict[str, Any]:
        """The JSON the model actually receives.

        Built explicitly rather than by ``asdict`` so that adding a field to the
        dataclass cannot silently widen what the model sees. A projection that
        grows by default is not a projection.
        """
        return {
            "case_id": self.case_id,
            "currency": self.currency,
            "paid": self.paid_inr,
            "days_since_capture": self.days_since_capture,
            "fulfilment_status": self.fulfilment_status,
            "prior_refund_total": self.prior_refund_total_inr,
            "refundable_headroom": self.refundable_headroom_inr,
            "line_items": [
                {"sku": ln.sku, "qty": ln.qty, "unit_price_paid": ln.unit_price_paid_inr}
                for ln in self.line_items
            ],
            "customer_message": self.customer_message,
        }


@dataclass(frozen=True, slots=True)
class OpenCase:
    """The broker's private record. Never crosses to the agent.

    ``payment_id`` lives here and in the capability table. The agent is handed
    ``read_token`` and ``write_token`` and the ``CaseView``; it is never handed
    this object, which is why ``to_tool_payload`` exists on the view rather than
    here.
    """

    case_id: str
    payment_id: str
    session_id: str
    principal_id: str
    read_token: str
    write_token: str
    state: TrustedState
    span: UntrustedSpan
    view: CaseView = field(repr=False)


def _days_since(now_epoch_s: int, then_epoch_s: int | None) -> int | None:
    """Whole days, or None. None stays None rather than becoming 0.

    A zero here would read to the model as "captured today", which is a
    materially different case from "we could not read the capture time".
    """
    if then_epoch_s is None:
        return None
    return max(0, (now_epoch_s - then_epoch_s) // 86_400)


def build_case_view(
    *,
    case_id: str,
    state: TrustedState,
    span: UntrustedSpan,
) -> CaseView:
    """Project trusted state down to what the agent may read.

    The headroom is computed here rather than by the model precisely because the
    model must never be in a position to reason about how much room is left —
    that reasoning is the aggregate bound's job, and it happens after routing.
    It is shown at all because a support agent that cannot see whether a refund
    has already been issued would escalate everything, which would make the
    false-refusal number a measurement of the projection rather than of the
    policy.
    """
    headroom: Paise = sub(state.payment.amount_paise, state.payment.prior_refund_total_paise)
    return CaseView(
        case_id=case_id,
        currency="INR",
        paid_inr=format_inr(state.payment.amount_paise),
        days_since_capture=_days_since(state.now_epoch_s, state.payment.created_at_epoch_s),
        fulfilment_status=(
            str(state.fulfilment.state) if state.fulfilment.state is not None else None
        ),
        prior_refund_total_inr=format_inr(state.payment.prior_refund_total_paise),
        refundable_headroom_inr=format_inr(headroom),
        line_items=tuple(
            CaseLine(
                sku=ln.sku,
                qty=ln.qty,
                unit_price_paid_inr=format_inr(ln.unit_price_paid_paise),
            )
            for ln in state.lines
        ),
        customer_message={
            "span_id": span.span_id,
            "trust": span.trust,
            "text": span.text,
        },
    )

"""The trusted-state vocabulary and the three-valued logic over it.

``core/`` is provably free of the network, the clock, the filesystem and any
model. Everything a decision depends on therefore has to arrive as an argument,
which is why ``TrustedState`` is a large frozen dataclass rather than something
the policy layer goes and fetches. That shape is the point: a reviewer can read
one struct and know the complete set of facts that can influence a refund.

The three-valued logic
----------------------
Preconditions return ``Kleene``, not ``bool``. The reason is a specific failure
the architecture review found: a missing ``captured_at`` compared against a
24-hour window with ordinary boolean logic yields ``False``, which routes to
DENY — and a DENY on missing data looks like a working safety property while
actually being a silent read failure. Under Kleene, absence is ``UNKNOWN``,
``UNKNOWN and TRUE`` is ``UNKNOWN``, and ``UNKNOWN`` escalates to a human.
Absence of evidence is not evidence of absence, encoded.

Positive records only
---------------------
``NOT_DELIVERED`` requires a carrier scan id that *says* the parcel came back.
It never fires on the absence of a delivery scan, because absence is also what a
carrier integration outage looks like, and an outage must not become a refund.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Final, Literal

from paybound.core.money import Paise

__all__ = [
    "LEDGER_NONDELIVERY_STATES",
    "CatalogueLine",
    "Decision",
    "EvidenceClass",
    "Fulfilment",
    "FulfilmentState",
    "Kleene",
    "OrderGroup",
    "OrderLine",
    "Outcome",
    "PaymentFacts",
    "PredicateResult",
    "ReasonCode",
    "SiblingPayment",
    "Tier",
    "TrustedState",
]


# ---------------------------------------------------------------------------
# The enum. Nine members, closed, hashed into the corpus seal.
# ---------------------------------------------------------------------------


class ReasonCode(enum.StrEnum):
    """The complete set of things the agent under test is allowed to say.

    This frozen set *is* the model's entire influence over money: log2(9) is
    about 3.17 bits per case. Anything outside it is a hard refusal with an
    ``ENUM_VIOLATION`` audit event and a session strike — never a silent
    downgrade to ``UNCLASSIFIED``, because a coercion target turns an
    out-of-band string into a legal move.
    """

    DUPLICATE_CHARGE = "DUPLICATE_CHARGE"
    PRICE_MISMATCH = "PRICE_MISMATCH"
    NOT_DELIVERED = "NOT_DELIVERED"
    CANCELLED_IN_WINDOW = "CANCELLED_IN_WINDOW"
    WRONG_ITEM = "WRONG_ITEM"
    ARRIVED_DAMAGED = "ARRIVED_DAMAGED"
    QUALITY_NOT_AS_DESCRIBED = "QUALITY_NOT_AS_DESCRIBED"
    CHANGED_MIND_LATE = "CHANGED_MIND_LATE"
    UNCLASSIFIED = "UNCLASSIFIED"


EvidenceClass = Literal["ledger", "testimonial"]
Tier = Literal["T0", "T1", "T2", "NEVER"]


# ---------------------------------------------------------------------------
# Kleene's strong three-valued logic
# ---------------------------------------------------------------------------


class Kleene(enum.Enum):
    """TRUE / FALSE / UNKNOWN, with a conjunction that propagates UNKNOWN.

    Only ``TRUE`` authorises. ``FALSE`` takes the clause's ``on_fail`` branch.
    ``UNKNOWN`` always escalates, regardless of ``on_fail`` — a clause author
    cannot opt out of that, because "deny on missing data" and "allow on missing
    data" are both wrong answers to "I could not read the field."
    """

    TRUE = "TRUE"
    FALSE = "FALSE"
    UNKNOWN = "UNKNOWN"

    def __and__(self, other: Kleene) -> Kleene:
        if self is Kleene.FALSE or other is Kleene.FALSE:
            return Kleene.FALSE
        if self is Kleene.UNKNOWN or other is Kleene.UNKNOWN:
            return Kleene.UNKNOWN
        return Kleene.TRUE

    @staticmethod
    def of(value: bool | None) -> Kleene:
        """Lift an optional bool. ``None`` — the missing field — is UNKNOWN."""
        if value is None:
            return Kleene.UNKNOWN
        return Kleene.TRUE if value else Kleene.FALSE

    @staticmethod
    def conjoin(results: list[Kleene]) -> Kleene:
        """Conjunction over a clause's preconditions.

        An empty conjunction is TRUE in classical logic. Here it raises: a
        clause with no preconditions is an unbounded refund, and the table must
        not be able to express one by omission.
        """
        if not results:
            raise ValueError(
                "a clause with zero preconditions authorises unconditionally; "
                "the policy table must not be able to express that by omission"
            )
        out = Kleene.TRUE
        for r in results:
            out = out & r
        return out


# ---------------------------------------------------------------------------
# Fulfilment vocabulary
# ---------------------------------------------------------------------------


class FulfilmentState(enum.StrEnum):
    """Carrier-scan vocabulary. Transcribed, with every deviation declared.

    Which refund reasons become decidable at which evidence tier has to be a
    fact about Indian logistics rather than a fact about this repository. So
    every member below is either a token published by a real carrier or
    aggregator, or is explicitly marked derived.

    **What could not be sourced, stated first.** There is no public Indian
    carrier tracking *API* whose status vocabulary can be cited: Delhivery's
    API reference is behind a login at ``ucp.delhivery.com/developer-portal``
    and ``apidocs.shiprocket.in`` is a client-rendered shell. The sources below
    are therefore *operator-facing* status vocabularies — weaker than an API
    schema, and much stronger than an invented list. See ``docs/CITATIONS.md``
    for URLs, retrieval dates and archive snapshots.

    ============================== ==========================================
    ``IN_TRANSIT``                 published: Shiprocket ``In-Transit:``
    ``DELIVERED``                  published: Shiprocket ``Delivered:``
    ``RTO_INITIATED``              published: Shiprocket ``RTO Initiated:``
    ``UNDELIVERED_CONSIGNEE_REFUSED`` **declared conjunction** — see below
    ``LOST``                       published: ClickPost ``16 Lost`` (terminal)
    ``NOT_PICKED_UP``              **derived** — no carrier publishes it
    ============================== ==========================================

    ``UNDELIVERED_CONSIGNEE_REFUSED`` is a conjunction, not a token, and saying
    so matters. Shiprocket's published ``Undelivered`` is a **superset** that
    includes parcels in an active three-attempt reattempt cycle. A merchant who
    maps it straight onto this member would put a parcel that is about to be
    delivered into ``LEDGER_NONDELIVERY_STATES`` and authorise a full refund on
    it. The intended composition is ``FailedDelivery`` **and** a
    rejected-by-customer non-delivery code — refusal, not mere failure.

    ``NOT_PICKED_UP`` is author-defined: everything before a pickup scan. No
    source publishes "not dispatched" or any synonym. It is deliberately *not*
    named after ClickPost's ``PickupPending``, because adopting a published
    token for an aggregation the author invented is a subtler fabrication than
    an obviously-derived name.

    Positive records only
    ---------------------
    ``LEDGER_NONDELIVERY_STATES`` is the set that authorises ``NOT_DELIVERED``.
    ``IN_TRANSIT`` is absent from it: a parcel in transit for three weeks is
    indistinguishable, from the ledger, from a carrier whose webhook broke, and
    an outage must not become a refund.
    """

    NOT_PICKED_UP = "not_picked_up"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    RTO_INITIATED = "rto_initiated"
    LOST = "lost"
    UNDELIVERED_CONSIGNEE_REFUSED = "undelivered_consignee_refused"


LEDGER_NONDELIVERY_STATES: Final[frozenset[FulfilmentState]] = frozenset(
    {
        FulfilmentState.RTO_INITIATED,
        FulfilmentState.LOST,
        FulfilmentState.UNDELIVERED_CONSIGNEE_REFUSED,
    }
)
"""The three states that are *positive records of non-delivery*.

``IN_TRANSIT`` is deliberately absent. A parcel that has been in transit for
three weeks is indistinguishable, from the ledger, from a carrier whose webhook
broke — and the second case must reach a human.

**A published carrier-vocabulary defect that reaches this set.** Shiprocket
publishes a single combined status, ``Lost/Damaged``, where ClickPost separates
``16 Lost`` from ``17 Damaged``. On a Shiprocket-fed merchant, a *damaged*
parcel therefore arrives here as ``LOST`` — authorising ``NOT_DELIVERED`` at
the full payment amount for goods the customer is physically holding. This is
not a PayBound schema choice; it is a defect in the published vocabulary, and it
is a second bridge into the highest-paying clause. It is measured rather than
assumed: ``LIMITS.md`` names it and the corpus carries items for it.
"""


# ---------------------------------------------------------------------------
# Trusted state
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CatalogueLine:
    """A merchant-authored catalogue entry, priced from a point in time."""

    sku: str
    unit_price_paise: Paise
    effective_from_epoch_s: int


@dataclass(frozen=True, slots=True)
class OrderLine:
    sku: str
    qty: int
    unit_price_paid_paise: Paise

    @property
    def line_total_paise(self) -> Paise:
        return self.unit_price_paid_paise * self.qty


@dataclass(frozen=True, slots=True)
class Fulfilment:
    """The carrier record. Every field optional — absence is UNKNOWN, not False."""

    state: FulfilmentState | None = None
    carrier_scan_id: str | None = None
    intake_sku: str | None = None
    intake_damage_record: str | None = None


@dataclass(frozen=True, slots=True)
class OrderGroup:
    """The object that closes precondition manufacture.

    A buyer who deliberately pays twice and then claims non-delivery would
    otherwise collect the goods and both payments back. Any group with two or
    more captures is tier NEVER for every clause except ``DUPLICATE_CHARGE``,
    and a settled ``DUPLICATE_CHARGE`` blocks every other clause for the group.
    """

    group_id: str
    capture_count: int
    settled: bool = False


@dataclass(frozen=True, slots=True)
class SiblingPayment:
    """A second capture in the same order group, as the ledger reports it.

    ``payment_id_hash`` rather than ``payment_id``: ``core/`` must never hold a
    raw ``pay_`` id, because I-04 is discharged by grepping for that string
    outside the adapter.
    """

    payment_id_hash: str
    amount_paise: Paise
    method: str
    created_at_epoch_s: int


@dataclass(frozen=True, slots=True)
class PaymentFacts:
    """Razorpay's own view of the payment. Never our cache of it.

    ``amount_refunded_paise`` and ``prior_refund_total_paise`` are separate on
    purpose. KG-1 block C4 exists to settle when Razorpay increments
    ``amount_refunded`` relative to ``refund.status``; until that is answered,
    the aggregate bound uses ``prior_refund_total_paise`` — summed from the
    per-payment refunds collection — because a bound that reads a field which
    lags by seconds is a bound that can be beaten by issuing two requests
    quickly. If KG-1 shows the field is synchronous, the cheaper read becomes
    available; the conservative path is correct either way, so nothing here is
    blocked on that answer.
    """

    amount_paise: Paise
    amount_refunded_paise: Paise
    prior_refund_total_paise: Paise
    created_at_epoch_s: int | None
    method: str | None
    status: str | None
    captured: bool | None


@dataclass(frozen=True, slots=True)
class TrustedState:
    """Everything, and only what, a refund decision may depend on.

    Nothing here originates in customer prose. The untrusted span is carried
    beside this struct, never inside it, and the policy layer is not given a
    reference to it — which is why ``request_refund`` has no ``amount``
    parameter to argue about.
    """

    now_epoch_s: int
    payment: PaymentFacts
    order_status: str | None
    order_created_at_epoch_s: int | None
    lines: tuple[OrderLine, ...]
    catalogue: tuple[CatalogueLine, ...]
    fulfilment: Fulfilment
    group: OrderGroup
    siblings: tuple[SiblingPayment, ...] = ()
    prior_refund_reasons: frozenset[ReasonCode] = field(default_factory=frozenset)
    return_window_days: int = 7

    def catalogue_price_at(self, sku: str, at_epoch_s: int | None) -> Paise | None:
        """The catalogue price for ``sku`` in force at ``at_epoch_s``, or None.

        None is a real answer and it must stay None rather than becoming zero: a
        zero catalogue price would make every paid price look like an overcharge
        and turn ``PRICE_MISMATCH`` into a full refund.
        """
        if at_epoch_s is None:
            return None
        candidates = [
            c
            for c in self.catalogue
            if c.sku == sku and c.effective_from_epoch_s <= at_epoch_s
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda c: c.effective_from_epoch_s).unit_price_paise


# ---------------------------------------------------------------------------
# Decisions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PredicateResult:
    """One row of the audit gold: what was checked, against what, what was seen."""

    name: str
    source_field: str
    observed: object
    result: Kleene


class Outcome(enum.StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True, slots=True)
class Decision:
    """The broker's complete answer.

    ``ESCALATE`` counts as a refusal in every ceiling metric — it is not a third
    outcome, or the headline number becomes tunable by relabelling.
    """

    outcome: Outcome
    reason_code: ReasonCode
    amount_paise: Paise | None
    clause_id: str | None
    predicates: tuple[PredicateResult, ...]
    rationale: str
    aggregate_existing_paise: Paise | None = None
    aggregate_cap_paise: Paise | None = None

    @property
    def is_allow(self) -> bool:
        return self.outcome is Outcome.ALLOW

"""Paise arithmetic. Integers only, forever.

Every monetary quantity in PayBound is an ``int`` number of paise. There is no
float anywhere on an authority-bearing path, and this module exists so that
statement is enforceable rather than aspirational.

Why a whole module for integer addition
---------------------------------------
Razorpay's API is already paise-denominated, so the temptation is to treat
amounts as bare ``int`` and move on. The failure this module prevents is not an
arithmetic error — it is a *representation* error that only appears at the
boundary:

  * ``2499.0 * 100`` is ``249899.99999999997`` on this machine. A single float
    round-trip through a display helper, a JSON parse, or a CSV import turns
    Rs 2,499.00 into a refund of Rs 2,498.99 — which then fails the byte-exact
    assertion in I-03 and looks like a policy bug rather than a units bug.
  * A negative amount reaching ``POST /refund`` is a 400 from Razorpay, but a
    negative amount reaching the *aggregate bound* is worse: it makes the bound
    pass when it should fail.

So the invariant is: paise enter this codebase as ``int``, are validated once,
and never become anything else. Formatting to rupees happens only in the report
and the video, never before a decision.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "ZERO",
    "Paise",
    "add",
    "as_paise",
    "format_inr",
    "minimum",
    "sub",
]

# A documentation alias. Deliberately not a NewType: mypy's NewType would force
# a cast at every Razorpay boundary, and a cast is exactly the place where an
# unchecked value slips in. `as_paise()` is the checked boundary instead.
Paise = int

ZERO: Final[Paise] = 0

# Rs 10,00,000. No single order in the catalogue is within three orders of
# magnitude of this. A value above it means a units error upstream (rupees
# passed where paise were expected, or a float that overflowed a parse), and
# failing loudly here is far cheaper than discovering it in a refund body.
_SANITY_CEILING: Final[int] = 100_000_000


def as_paise(value: object, *, field: str) -> Paise:
    """Validate ``value`` as a paise amount, or raise.

    This is the *only* sanctioned way for an external number — a Razorpay
    response field, a catalogue price, a fixture — to become an amount PayBound
    will reason about. ``field`` names the source so a failure says where the
    bad value came from.

    ``bool`` is rejected explicitly: it is a subclass of ``int`` in Python, so
    ``as_paise(True)`` would silently yield 1 paise.
    """
    if isinstance(value, bool):
        raise TypeError(f"{field}: bool is not a paise amount (got {value!r})")
    if not isinstance(value, int):
        raise TypeError(
            f"{field}: paise must be int, got {type(value).__name__} ({value!r}). "
            "Floats are rejected on purpose — 2499.0 * 100 is 249899.99999999997."
        )
    if value < 0:
        raise ValueError(
            f"{field}: paise must be non-negative, got {value}. A negative amount "
            "makes the aggregate bound pass when it should fail."
        )
    if value > _SANITY_CEILING:
        raise ValueError(
            f"{field}: {value} paise exceeds the sanity ceiling of {_SANITY_CEILING}. "
            "This is almost always rupees passed where paise were expected."
        )
    return value


def add(a: Paise, b: Paise) -> Paise:
    """Add two validated paise amounts, re-checking the ceiling.

    The re-check matters: the aggregate bound sums prior refunds, and a sum that
    silently exceeds the ceiling is the shape of an attribution bug (foreign
    refunds counted into our window).
    """
    return as_paise(a + b, field="add")


def sub(a: Paise, b: Paise) -> Paise:
    """``a - b``, refusing to go negative.

    Used for refundable headroom. A negative headroom is not a small number, it
    is a contradiction — it means the ledger says more has been refunded than
    was ever captured — and it must stop the run rather than clamp to zero.
    """
    if b > a:
        raise ValueError(
            f"headroom underflow: {b} > {a}. More refunded than captured means the "
            "ledger read is wrong or foreign refunds were attributed to this run."
        )
    return as_paise(a - b, field="sub")


def minimum(*values: Paise) -> Paise:
    """The min-clamp primitive. Requires at least one value.

    An empty min is silently ``0`` in some formulations, and a zero-rupee
    ALLOW is a decision, not an abstention. Refuse it.
    """
    if not values:
        raise ValueError("minimum() over no clauses — the caller must check first")
    return min(values)


def format_inr(paise: Paise) -> str:
    """Render paise as ``Rs 2,499.00``, Indian digit grouping.

    Display only. Never call this before a decision, and never parse the result
    back — that round trip is the units error this module exists to prevent.
    """
    as_paise(paise, field="format_inr")
    rupees, remainder = divmod(paise, 100)
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join([*groups, tail])
    return f"Rs {digits}.{remainder:02d}"

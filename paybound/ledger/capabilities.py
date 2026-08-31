"""Capability minting and redemption. Two tokens per case, minted pre-model.

The design point, stated once: **a raw ``pay_`` id is ambient authority.**
Designation is authorization, scope is global, the id is reachable by an
adversary (a customer can paste one into their own support ticket), and it never
expires. Razorpay publishes that no API key scoping of any kind exists — no
restricted keys, no read-only keys, no per-endpoint permissions, one key per
merchant id. That absence is the strongest available argument for a broker, and
it is Razorpay's own documentation rather than ours.

So the agent never sees a payment id. It sees two opaque bearer tokens, minted
by deterministic merchant-side code **before any model call**, and the mapping
from token to payment lives in one column of one table that nothing above the
broker reads.

Storage
-------
The row key is ``sha256(token)``. The token itself is never written down. A
ledger dump, a backup, or a leaked events file therefore does not contain
anything that can redeem a capability — the same reason a password file stores
hashes.

Redemption
----------
Consuming the write token is a single conditional UPDATE whose row count is the
authorization decision. It is never read-then-write: two concurrent redemptions
of the same token would both pass a read check and both proceed, which is a
double refund. The UPDATE runs in the same transaction as the intent insert, so
"the token was spent" and "the intent exists" commit together or not at all.
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
import sqlite3
from dataclasses import dataclass
from typing import Final, Literal

__all__ = [
    "READ_PREFIX",
    "TTL_SECONDS",
    "WRITE_PREFIX",
    "Capability",
    "CapabilityError",
    "consume_write",
    "handle_of",
    "mint_case_capabilities",
    "resolve_read",
    "revoke_case",
]

READ_PREFIX: Final[str] = "cap_r_"
WRITE_PREFIX: Final[str] = "cap_w_"

# Fifteen minutes. Long enough for a model turn plus a retry of the *harness*,
# short enough that a token captured out of a log is worthless by the time
# anyone reads the log.
TTL_SECONDS: Final[int] = 900

_TOKEN_BYTES: Final[int] = 16


class CapabilityError(Exception):
    """A capability could not be minted or redeemed. Always fails closed."""


@dataclass(frozen=True, slots=True)
class Capability:
    """A minted token and its handle. The token exists only in memory."""

    token: str
    handle_id: str
    verb: Literal["read", "write"]
    case_id: str
    expires_at: int


def _mint_token(prefix: str) -> str:
    return prefix + base64.urlsafe_b64encode(os.urandom(_TOKEN_BYTES)).decode().rstrip("=")


def handle_of(token: str) -> str:
    """``sha256(token)``. The only way a token becomes a row key."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def mint_case_capabilities(
    conn: sqlite3.Connection,
    *,
    case_id: str,
    session_id: str,
    principal_id: str,
    payment_id: str,
    now: int,
    ttl_seconds: int = TTL_SECONDS,
) -> tuple[Capability, Capability]:
    """Mint the read and write tokens for one case. Returns ``(read, write)``.

    Case-shaped, not list-shaped: the caller has already resolved exactly one
    payment by deterministic lookup. A capability that designated a *set* of
    payments would make I-04 pass vacuously, because there would be nothing for
    a foreign token to fail to reach.

    Must be called before the first model call. Nothing enforces that at this
    layer — the broker's ordering does — but the two-token split is what makes
    the ordering checkable: a read call cannot return the write token, so a
    model that has only ever seen tool output cannot hold one.
    """
    if not payment_id.startswith("pay_"):
        raise CapabilityError(
            f"subject must be a Razorpay payment id, got {payment_id[:12]!r}. "
            "The broker resolves the payment; it is never taken from input."
        )
    expires_at = now + ttl_seconds
    read = Capability(_mint_token(READ_PREFIX), "", "read", case_id, expires_at)
    write = Capability(_mint_token(WRITE_PREFIX), "", "write", case_id, expires_at)
    read = Capability(read.token, handle_of(read.token), "read", case_id, expires_at)
    write = Capability(write.token, handle_of(write.token), "write", case_id, expires_at)

    for cap in (read, write):
        conn.execute(
            """INSERT INTO capability
                 (handle_id, session_id, case_id, principal_id, subject_payment_id,
                  verb, issued_at, expires_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cap.handle_id,
                session_id,
                case_id,
                principal_id,
                payment_id,
                cap.verb,
                now,
                expires_at,
            ),
        )
    return read, write


def resolve_read(
    conn: sqlite3.Connection,
    token: str,
    *,
    session_id: str,
    principal_id: str,
    now: int,
) -> sqlite3.Row:
    """Resolve a read token to its capability row, or raise.

    Multi-use, so there is no consumption here. The presented handle must match
    the authenticated principal *of this session*: a token that leaked from one
    session must not work in another, which is what makes the foreign-token case
    in I-04 a real test rather than a formality.
    """
    row = conn.execute(
        """SELECT * FROM capability
            WHERE handle_id = ? AND verb = 'read' AND revoked_at IS NULL""",
        (handle_of(token),),
    ).fetchone()
    if row is None:
        raise CapabilityError("unknown or revoked read capability")
    if row["expires_at"] <= now:
        raise CapabilityError("read capability expired")
    if row["session_id"] != session_id or row["principal_id"] != principal_id:
        raise CapabilityError("capability does not belong to this session's principal")
    return row


def consume_write(
    conn: sqlite3.Connection,
    token: str,
    *,
    intent_id: str,
    session_id: str,
    principal_id: str,
    now: int,
) -> sqlite3.Row:
    """Atomically spend the write token, binding it to ``intent_id``.

    The UPDATE's row count *is* the decision. Anything other than exactly one
    row means the token was already spent, revoked, expired, or is not a write
    token — all of which are DENY, and none of which are distinguishable from
    each other to the caller on purpose: a redemption error that says *which*
    condition failed is an oracle.

    Must run inside the same transaction as the intent insert. This function
    does not open one; the caller owns the transaction so that both writes
    commit together.
    """
    handle = handle_of(token)

    # Ownership is checked separately from consumption so that a token belonging
    # to another session is refused without being burned. Burning it would let
    # anyone invalidate a case they cannot otherwise touch.
    owner = conn.execute(
        "SELECT session_id, principal_id FROM capability WHERE handle_id = ? AND verb = 'write'",
        (handle,),
    ).fetchone()
    if owner is None:
        raise CapabilityError("write capability not redeemable")
    if owner["session_id"] != session_id or owner["principal_id"] != principal_id:
        raise CapabilityError("write capability not redeemable")

    cur = conn.execute(
        """UPDATE capability
              SET used_at = ?, bound_intent_id = ?
            WHERE handle_id = ?
              AND verb = 'write'
              AND used_at IS NULL
              AND revoked_at IS NULL
              AND expires_at > ?""",
        (now, intent_id, handle, now),
    )
    if cur.rowcount != 1:
        raise CapabilityError("write capability not redeemable")

    row = conn.execute("SELECT * FROM capability WHERE handle_id = ?", (handle,)).fetchone()
    assert row is not None  # the UPDATE just matched it
    return row


def revoke_case(
    conn: sqlite3.Connection, case_id: str, *, reason: str, now: int
) -> int:
    """Revoke every capability on a case. Idempotent; returns rows affected.

    Used by case close, the kill switch, and the three-strikes rule. Revocation
    is deliberately not deletion: the row is the evidence that the token existed
    and why it stopped working.
    """
    cur = conn.execute(
        """UPDATE capability
              SET revoked_at = ?, revoked_reason = ?
            WHERE case_id = ? AND revoked_at IS NULL""",
        (now, reason, case_id),
    )
    return int(cur.rowcount)


def new_case_id() -> str:
    """An opaque case identifier. Not derived from the payment id.

    A case id derived from a payment id would leak the payment id to anything
    that can see a case id, which includes the agent's tool output.
    """
    return "case_" + secrets.token_hex(8)

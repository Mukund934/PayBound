"""SQLite connection management. Durability settings are not negotiable here.

``synchronous=FULL`` costs an fsync per commit and is the reason a process
killed between the intent write and the POST comes back to a *complete* intent
row rather than a truncated one. ``NORMAL`` would be faster and would lose the
last transaction on power loss — which is precisely the transaction that says
"I am about to move money."

WAL is on because the reconciler reads while the broker writes, and because a
WAL's recovery story is the one being relied on.

No ORM, no migrations. The schema is committed and versioned by
``PRAGMA user_version``; a mismatch raises rather than silently upgrading, since
an automatic migration on a ledger is an unlogged rewrite of the evidence.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

__all__ = ["SCHEMA_VERSION", "LedgerVersionMismatch", "connect", "transaction"]

SCHEMA_VERSION: Final[int] = 1
_SCHEMA_PATH: Final[Path] = Path(__file__).with_name("schema.sql")


class LedgerVersionMismatch(RuntimeError):
    """The database on disk was written by a different schema version."""


def connect(path: str | Path, *, create: bool = True) -> sqlite3.Connection:
    """Open the ledger with the durability settings the invariants assume.

    ``isolation_level=None`` turns off the driver's implicit transaction
    management so that ``BEGIN IMMEDIATE`` in :func:`transaction` is the only
    thing that opens a write transaction. Implicit transactions are how a
    capability consume and an intent insert end up in *different* transactions,
    which is the one thing I-07 cannot survive.
    """
    path = Path(path)
    if not create and not path.exists():
        raise FileNotFoundError(path)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")
    conn.execute("PRAGMA foreign_keys = ON")
    # Without this, two writers racing on the write-token consume get
    # SQLITE_BUSY instead of serialising, and the loser's DENY looks like a bug.
    conn.execute("PRAGMA busy_timeout = 30000")

    existing = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if existing == 0:
        conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    elif existing != SCHEMA_VERSION:
        conn.close()
        raise LedgerVersionMismatch(
            f"ledger at {path} is schema version {existing}, this build expects "
            f"{SCHEMA_VERSION}. Migrating a ledger in place rewrites evidence; "
            "start a new run directory instead."
        )
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A single write transaction, committed on success and rolled back on error.

    ``BEGIN IMMEDIATE`` takes the write lock up front. The alternative — a
    deferred transaction that upgrades on first write — can fail *mid*
    transaction with SQLITE_BUSY, which would leave the capability consumed and
    the intent unwritten if the two were split across a retry.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")

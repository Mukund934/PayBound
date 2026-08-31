-- PayBound ledger schema. Committed, versioned by PRAGMA user_version, no migrations.
--
-- Two properties this schema enforces structurally rather than in application code:
--
--   1. A capability cannot be looked up by anything except its handle. There is no
--      index from case_id or session_id to a token, and `subject_payment_id` lives
--      here and nowhere above. That is invariant I-04: the agent holds an opaque
--      handle and there is no query it could issue that returns a pay_ id.
--
--   2. One intent can produce at most one refund. `intent_id` is the primary key of
--      `intent`, `idem_key` and `receipt` are UNIQUE, and `capability.bound_intent_id`
--      is written in the same transaction that consumes the write token. That is
--      invariant I-07.

PRAGMA user_version = 1;

-- ---------------------------------------------------------------------------
-- Capabilities. Two rows per case: one read (multi-use), one write (single-use).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS capability (
    handle_id           TEXT PRIMARY KEY,          -- sha256(token). The token itself is never stored.
    session_id          TEXT NOT NULL,
    case_id             TEXT NOT NULL,
    principal_id        TEXT NOT NULL,
    subject_payment_id  TEXT NOT NULL,             -- pay_… — the ONLY place this mapping exists
    verb                TEXT NOT NULL CHECK (verb IN ('read', 'write')),
    issued_at           INTEGER NOT NULL,
    expires_at          INTEGER NOT NULL,
    used_at             INTEGER,
    bound_intent_id     TEXT,
    revoked_at          INTEGER,
    revoked_reason      TEXT,

    -- A write token that has been consumed must name the intent it authorised.
    -- Without this, a consumed-but-unbound row is indistinguishable from a bug.
    CHECK (used_at IS NULL OR bound_intent_id IS NOT NULL)
);

-- Case close and the kill switch revoke by case. This index exists for that path
-- only; there is deliberately no index that maps a case to a *token*.
CREATE INDEX IF NOT EXISTS idx_capability_case ON capability (case_id);

-- At most one write capability per case. A second write token is a second chance
-- to move money, so the schema refuses to hold one.
CREATE UNIQUE INDEX IF NOT EXISTS idx_capability_one_write_per_case
    ON capability (case_id) WHERE verb = 'write';

-- ---------------------------------------------------------------------------
-- Write-ahead intent log. Written and fsynced BEFORE the first byte leaves.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS intent (
    intent_id       TEXT PRIMARY KEY,              -- ULID from paybound.ids
    case_id         TEXT NOT NULL,
    handle_id       TEXT NOT NULL,                 -- the write capability consumed for this intent
    payment_id      TEXT NOT NULL,
    idem_key        TEXT NOT NULL UNIQUE,          -- pure function of intent_id
    receipt         TEXT NOT NULL UNIQUE,          -- pure function of intent_id
    amount_paise    INTEGER NOT NULL CHECK (amount_paise > 0),
    clause_id       TEXT NOT NULL,
    request_bytes   BLOB NOT NULL,                 -- serialized ONCE, before the first attempt
    body_sha256     TEXT NOT NULL,
    state           TEXT NOT NULL CHECK (state IN ('WRITTEN', 'POST_SENT', 'KNOWN')),
    attempts        INTEGER NOT NULL DEFAULT 0 CHECK (attempts <= 1),
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    outcome_json    TEXT,
    refund_id       TEXT,

    FOREIGN KEY (handle_id) REFERENCES capability (handle_id)
);

CREATE INDEX IF NOT EXISTS idx_intent_state ON intent (state);
CREATE UNIQUE INDEX IF NOT EXISTS idx_intent_refund ON intent (refund_id)
    WHERE refund_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Append-only event log, mirrored to events.jsonl by a single emit().
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS event (
    event_id        TEXT PRIMARY KEY,
    ts              TEXT NOT NULL,
    trace_id        TEXT NOT NULL,
    run_id          TEXT,
    session_id      TEXT,
    trial_id        TEXT,
    actor           TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    payload_json    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_event_trace ON event (trace_id, ts);

-- ---------------------------------------------------------------------------
-- Run row. The reproducibility contract in one record.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS run (
    run_id                TEXT PRIMARY KEY,
    git_sha               TEXT NOT NULL,
    policy_sha            TEXT NOT NULL,
    corpus_sha            TEXT,
    tool_registry_sha     TEXT,
    model_id              TEXT,
    sdk_version           TEXT,
    prompt_sha            TEXT,
    key_id_public_prefix  TEXT NOT NULL,
    window_from           INTEGER NOT NULL,
    window_to             INTEGER,
    arm                   TEXT NOT NULL,
    started_at            INTEGER NOT NULL,
    finished_at           INTEGER
);

-- NightShift orchestrator state (FROZEN). SQLite, run in WAL mode.
-- Owned by T1; the `ledger` table is shared with T4.

CREATE TABLE IF NOT EXISTS workers (
    worker_id      TEXT PRIMARY KEY,
    token          TEXT NOT NULL,
    capability_json TEXT NOT NULL,
    class_weight   REAL NOT NULL DEFAULT 1,   -- server-assigned (GPU=5, CPU=1); never the agent's claim
    idle           INTEGER NOT NULL DEFAULT 1,
    cpu_pct        REAL,
    gpu_pct        REAL,
    on_ac          INTEGER NOT NULL DEFAULT 1,
    blacklisted    INTEGER NOT NULL DEFAULT 0,
    last_heartbeat TEXT,
    registered_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    manifest_json   TEXT NOT NULL,
    input_json      TEXT,
    state           TEXT NOT NULL DEFAULT 'queued',  -- queued | leased | completed | failed
    units           INTEGER NOT NULL DEFAULT 1,
    assigned_worker TEXT,
    lease_expires   TEXT,
    result_json     TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Append-only rewards ledger (shared with T4). credits = accepted_units * class_weight.
CREATE TABLE IF NOT EXISTS ledger (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_id  TEXT NOT NULL,
    job_id     TEXT,
    credits    REAL NOT NULL,
    reason     TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_jobs_state    ON jobs(state);
CREATE INDEX IF NOT EXISTS idx_ledger_worker ON ledger(worker_id);

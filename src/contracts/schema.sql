-- OneCompute orchestrator state (FROZEN). SQLite, run in WAL mode.
-- Owned by T1; the `ledger` table is shared with T4.

CREATE TABLE IF NOT EXISTS workers (
    worker_id      TEXT PRIMARY KEY,
    token          TEXT NOT NULL,
    capability_json TEXT NOT NULL,
    class_weight   REAL NOT NULL DEFAULT 1,   -- capability tier from self-reported has_gpu (GPU=5, CPU=1); drives scheduling only, NOT credit
    free_ram_gb    REAL,                       -- live available RAM (updated on heartbeat); drives free-RAM gating
    idle           INTEGER NOT NULL DEFAULT 1,
    cpu_pct        REAL,
    gpu_pct        REAL,
    on_ac          INTEGER NOT NULL DEFAULT 1,
    blacklisted    INTEGER NOT NULL DEFAULT 0,
    approved       INTEGER NOT NULL DEFAULT 1,  -- dashboard-approval gate; 1 keeps non-gated flows unchanged
    device_code    TEXT,                        -- short human code shown while pending approval
    last_heartbeat TEXT,
    registered_at  TEXT NOT NULL,
    cert_fingerprint TEXT                        -- lowercase hex SHA-256 of the worker's TLS client-cert DER; binds device identity (STRIDE Spoofing / B3). NULL = unbound
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,
    manifest_json   TEXT NOT NULL,
    input_json      TEXT,
    state           TEXT NOT NULL DEFAULT 'queued',  -- queued | leased | completed | failed
    units           INTEGER NOT NULL DEFAULT 1,
    workload_id     TEXT,                            -- groups jobs launched together via POST /workloads
    assigned_worker TEXT,
    lease_expires   TEXT,
    result_json     TEXT,
    attempts        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- Append-only rewards ledger (shared with T4). credits = accepted_units * job GPU weight
-- (5 for a job that requires a GPU, else 1), derived from the signed manifest server-side; the
-- worker's self-reported class_weight drives scheduling only, never credit.
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

-- Activity feed for the live dashboard (served via GET /events).
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,
    type      TEXT NOT NULL,   -- registered | approved | submitted | assigned | completed | yielded | failed | blacklisted
    worker_id TEXT,
    job_id    TEXT,
    detail    TEXT,
    prev_hash TEXT,            -- tamper-evident chain: hash of the immediately preceding event (genesis for the first)
    hash      TEXT             -- sha256_hex(canonical_bytes([prev_hash, ts, type, worker_id, job_id, detail]))
);

-- Opt-in measurement pilot: the latest on-device usage envelope per worker (derived hour-of-week
-- stats only, never raw activity). One row per worker, replaced on each POST /profile. Read by
-- GET /measurement to roll up fleet-wide measured idle headroom.
CREATE TABLE IF NOT EXISTS worker_profiles (
    worker_id    TEXT PRIMARY KEY,
    buckets_json TEXT NOT NULL,               -- JSON list of populated buckets (see UsageBucket)
    coverage     INTEGER NOT NULL DEFAULT 0,  -- populated hour-of-week buckets in this profile
    updated_at   TEXT NOT NULL
);

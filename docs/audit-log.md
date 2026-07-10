# Tamper-evident audit log

The orchestrator emits an append-only audit event stream (registered, approved, submitted,
assigned, completed, yielded, failed, blacklisted, removed, auth_failed) that the live dashboard
reads via `GET /events`. This document describes the tamper-evident hash chain woven through that
stream, how to verify it, how to export it for a SIEM, and the honest scope of the guarantee.

## Hash-chain design

Every row in the `events` table carries two additional columns (`src/contracts/schema.sql`):

- `prev_hash TEXT` -- the `hash` of the immediately preceding event (by `id`), or a fixed genesis
  constant for the very first event.
- `hash TEXT` -- this event's own hash.

Both columns are nullable and appended after the original columns, so the change is additive and
backward-compatible: any pre-existing database and every existing `GET /events` consumer keeps
working unchanged.

### Fields hashed (canonical input)

Each event's `hash` is computed over a canonical tuple, in this exact order:

```
hash = sha256_hex(canonical_bytes([
    prev_hash,   # the previous event's hash, or the genesis constant for the first
    ts,          # event timestamp (ISO-8601 UTC)
    type,        # event type
    worker_id,   # or "" when NULL
    job_id,      # or "" when NULL
    detail,      # or "" when NULL
]))
```

`canonical_bytes` (`src/contracts/hashing.py`) is the same frozen canonical JSON encoder used for
manifest signing: sorted keys, no extra whitespace, UTF-8. Nullable fields are coalesced to `""`
so a stored `NULL` and an empty string hash identically. The single helper `_audit_event_hash`
(`src/orchestrator/app.py`) is used both when writing (`_emit`) and when re-deriving
(`verify_audit_chain`), so the write path and the check path can never drift.

### Genesis

The first event's `prev_hash` is the fixed constant `AUDIT_GENESIS_HASH = "0" * 64` (64 zero hex
characters, the width of a SHA-256 digest). This anchors the chain so the first link is verifiable
rather than open-ended.

### Ordering under the write lock

`_emit` reads the tail (`SELECT hash FROM events ORDER BY id DESC LIMIT 1`), computes the new hash,
and inserts the row all inside the existing re-entrant `write_lock` (`src/orchestrator/db.py`). The
lock already serializes every write in the orchestrator, so concurrent emits from the FastAPI
threadpool cannot interleave: each new event chains densely onto the true immediate predecessor and
the chain has no gaps or races.

### Backward-compat ALTER / backfill

`open_serialized_db` runs the schema with `CREATE TABLE IF NOT EXISTS`, which will not add columns
to an `events` table that already exists in a persistent database from a prior version. To keep such
a database openable, `init_db` (`src/orchestrator/db.py`) runs a tiny idempotent backfill after the
schema script:

```python
for column in ("prev_hash", "hash"):
    try:
        conn.execute(f"ALTER TABLE events ADD COLUMN {column} TEXT")
    except sqlite3.OperationalError:
        pass  # column already exists on a current DB
```

A fresh database gets the columns straight from `schema.sql` and the `ALTER` is a no-op (it raises
"duplicate column name", which is swallowed). An older database gets the columns added as nullable.
Rows written before the upgrade keep `NULL` prev_hash/hash; new events chain forward from the
genesis anchor. (Verifying a database that mixes pre-upgrade NULL rows with new rows will report the
first NULL row as the break; the chain guarantee is exact for events written under this version.)

## How to verify

Two equivalent entry points re-derive the whole chain and report the first broken link:

- Function: `verify_audit_chain(conn)` in `src/orchestrator/app.py` returns
  `{"ok": bool, "count": int, "broken_at": id | None}`. It walks every event by ascending `id`,
  recomputes each hash from the stored fields plus the running `prev_hash` (seeded with the genesis
  anchor), and returns `ok=False` with `broken_at` set to the `id` of the first event whose stored
  `prev_hash` or `hash` does not match the re-derivation.
- Endpoint: `GET /events/verify` returns that same result as JSON. It is read-only.

Any post-hoc edit to a stored row (for example `UPDATE events SET detail = ...`) changes that row's
recomputed hash, so verification fails at that `id`. Because each event also binds the previous
event's hash, deleting or reordering rows is detected too.

## JSONL export and Microsoft Sentinel / SIEM alignment

`GET /events/export` streams the full audit log as newline-delimited JSON (JSONL): one event object
per line, ordered by `id`, with `Content-Type: application/x-ndjson`. Each line includes
`id`, `ts`, `type`, `worker_id`, `job_id`, `detail`, `prev_hash`, and `hash`.

JSONL is the native ingestion shape for Microsoft Sentinel and most SIEM / log pipelines (one event
per line, no wrapping array to stream around). Because every line carries the chain fields, an
analyst in Sentinel can independently re-run the same `sha256_hex(canonical_bytes([...]))`
derivation to confirm the exported log was not altered in transit or at rest in the SIEM.

A thin CLI wrapper is provided for pilots:

```
uv run python scripts/export_audit.py --url http://<host-ip>:8080 --out audit.jsonl
```

## STRIDE: Repudiation mapping

This feature addresses the **Repudiation** category of STRIDE. The audit stream already records who
did what and when (registration, approval, assignment, completion, yield, failure, blacklisting,
removal, and authentication failures). The hash chain makes that record tamper-evident: an operator,
a worker, or an intruder cannot silently rewrite history to deny an action, because any edit,
deletion, or reordering of a stored event breaks the re-derivable chain at a detectable `id`.
Exporting the chain-linked log to an external SIEM (Microsoft Sentinel) extends non-repudiation
beyond the orchestrator's own database.

## Honest scope

This is **in-database tamper-evidence**. It reliably detects *post-hoc* edits to the stored log:
if someone changes, deletes, or reorders rows after they were written, verification pinpoints the
first broken link.

It is **not** tamper-proofing against an attacker who holds live write access to the database. Such
an attacker could edit a row and then recompute every subsequent `hash`/`prev_hash` to produce a
fresh, internally consistent chain, because the genesis anchor and the hashing logic are not secret.
Defeating that requires moving the anchor out of the attacker's reach:

- external anchoring (periodically publishing the latest `hash` to an append-only external store),
- WORM (write-once-read-many) storage for the log, and/or
- a Rekor-style transparency log with independent witnesses.

Those are the roadmap steps beyond this change. The current guarantee is a strong, low-cost first
line: it turns silent post-hoc tampering into a detectable, localizable event and makes the audit
stream SIEM-exportable for external retention.

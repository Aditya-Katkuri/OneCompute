# SQLite queue design and scale boundary

## PoC stance

SQLite is the correct PoC store because T1 owns one FastAPI process on one LAN PC. The app server serializes high-level queue operations, and WAL gives concurrent dashboard reads with writes. The danger is not SQLite itself; the danger is sloppy claim transactions.

## Required pragmas/configuration

Use the existing project convention `init_db(... WAL ...)` and ensure:

```sql
PRAGMA journal_mode=WAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;
```

Keep write transactions short. Do not do worker HTTP calls, signature verification, expensive result checking, or dashboard aggregation inside a transaction.

## Atomic claim pattern

Pseudo-flow for `GET /jobs/next`:

```text
BEGIN IMMEDIATE;
  UPDATE jobs
     SET state='queued', assigned_worker=NULL, lease_expires=NULL
   WHERE state='leased' AND lease_expires < now;

  SELECT first matching queued job for this worker;

  UPDATE jobs
     SET state='leased', assigned_worker=:worker_id,
         lease_expires=:now_plus_lease, attempts=attempts+1
   WHERE job_id=:job_id AND state='queued';
COMMIT;
```

If the update affects zero rows, another transaction won; retry once or return 204.

## Result acceptance pattern

```text
BEGIN IMMEDIATE;
  SELECT job state and assigned_worker;
  if completed: return already accepted / no new credit
  if not valid terminal transition: record diagnostic / no credit
  insert result with unique(job_id, accepted=true) or equivalent guard
  update job to completed
  insert ledger row with unique(job_id)
COMMIT;
```

A unique ledger key on `job_id` is the simplest double-credit guard.

## Indexes

- `jobs(state, created_at)` for oldest queued selection.
- `jobs(state, lease_expires)` for reaper.
- `jobs(assigned_worker)` for heartbeat/result checks.
- `ledger(job_id)` unique for credit-once.
- `results(job_id)` for audit/debug.

## When SQLite breaks

Move off SQLite only when one of these becomes true:

1. More than one orchestrator process must claim jobs.
2. Poll/result write concurrency creates measurable `SQLITE_BUSY` despite short transactions.
3. The dashboard needs heavy analytics that hold long read snapshots.
4. Queue semantics need broker-native delayed redelivery, max delivery, or dead-letter handling.

Preferred path: NATS JetStream WorkQueue for broker semantics; Postgres for relational concurrency and analytics.

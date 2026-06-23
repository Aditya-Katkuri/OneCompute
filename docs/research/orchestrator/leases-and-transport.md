# Leases, heartbeats, preemption, and transport notes

## Correctness contract

NightShift should promise:

- **At-least-once execution**: a job may run more than once if a worker vanishes or yields.
- **At-most-once accepted result and credit**: only one valid terminal result changes the job to `completed` and inserts ledger credit.
- **Fast human yield**: worker reports policy violation or stops heartbeating; orchestrator requeues.

Do not promise exactly-once execution.

## PoC timing

- Worker short-polls `/jobs/next` every 1–2 s while idle and registered.
- Lease duration: 20–30 s.
- Heartbeat cadence while running: ~5 s.
- Reaper runs on every poll and/or as an in-process periodic task.

## State transitions

```text
queued --claim--> leased --result accepted--> completed
queued --claim--> leased --yield result--> queued
queued --claim--> leased --lease expired--> queued
queued --cancel--> cancelled
leased --invalid result/challenge fail--> failed or queued, depending policy
```

Every transition should be a transaction. The reaper must only update jobs still in `leased` and expired by orchestrator time.

## Heartbeat handling

`POST /heartbeat` should:

1. Update `workers.last_seen` and volatile state.
2. If `current_job_id` is leased to this worker, extend `lease_expires`.
3. Return `preempt=true` when worker reports not idle, not on AC, locked/unlocked state conflict, over util cap, or an operator/admin policy says to drain.

## Transport decision

Plain short-poll wins for the hackathon because:

- It is outbound-only from workers.
- It works with normal HTTP tooling and corporate proxies more often than upgraded/streaming transports.
- It makes failure states inspectable: 204 means no match, 200 means lease assigned, timeout means try again.
- It scales enough for 2–3 workers without broker complexity.

## Tests to write

- Worker A claims job; no heartbeat; reaper returns it to queued; Worker B claims it.
- Worker A yields; job is queued and can be reassigned.
- Duplicate result after Worker B completion does not double-credit Worker A.
- Heartbeat from wrong worker does not renew another worker's lease.
- Poll returns 204 when no capability match exists.

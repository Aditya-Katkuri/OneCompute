# Signed manifests, idempotency, and duplicate-safe results

## Why T1 cares

The manifest is the shared software contract that turns heterogeneous, untrusted-ish employee hardware into a controlled compute fabric. It tells the worker what to run, what input to use, what resources and sandbox policy apply, and when the assignment expires.

## PoC manifest checklist

Include and sign:

- `job_id`
- `kind`
- `code_sha256`
- `input_sha256`
- `requires`
- `limits`
- `sandbox`
- `issued_at`
- `expires_at`

Use local Ed25519 for the hackathon. The point is a visible refusal on tamper, not production OIDC/cosign.

## Idempotency requirements

Every PoC job kind should be safe to execute more than once:

- `data.transform`: deterministic input -> output.
- `challenge`: deterministic known answer.
- AI/eval slices: deterministic enough for acceptance or explicitly tolerance-based.

Reject or mark roadmap any job with external side effects unless it has an explicit dedupe key and compensating behavior.

## Result handling rules

1. Worker submits result with status, output hash, units, and proof fields.
2. Orchestrator verifies job exists and terminal transition is allowed.
3. Orchestrator validates output/challenge according to job kind.
4. Orchestrator credits exactly once using server-assigned class weight.
5. Later duplicate results are audit records or ignored responses, never new ledger rows.

## Security boundary reminder

T1 should not present Job Objects as the security boundary. Job Objects are process-group control and resource governance. Sandbox/container policy is the isolation story; manifest signing and hashing are integrity; duplicate-safe result acceptance is ledger correctness.

# NightShift — Operating Doctrine (read first)

> Auto-loaded for **every** agent working in this repo. This is the constitution: mission,
> chain of command, the quality bar, and how we report. Role-specific charters live in
> `.github/agents/*.md`. Work distribution lives in `docs/execution-plan.md`.

---

## 1. Mission & North Star

**NightShift — "Let your compute work when you are not."** Harvest idle CPU/GPU on opt-in
employee PCs into a privacy-preserving internal compute grid; pay employees in points.
Full concept in `docs/idea.md`; system design in `docs/architecture.md`.

**North Star = a green end-to-end demo** (`architecture.md` §9 + §13):

> submit → worker pulls → sandboxed run → result → points tick → **mouse-touch → instant yield**.

**The demo path is sacred.** Every decision is judged by whether it makes that 4–5 minute
demo more reliable. Throughput claims are reported honestly (measured harvested throughput
*alongside* the 1.8-ExaOPS theoretical ceiling — never instead of it).

---

## 2. Chain of command

```
CEO (human)  →  Chief of Staff (lead session)  →  5 Staff Engineers  →  elite-engineer subagents
```

- **Orders flow down. Working software + honest status flow up.**
- **Every level quality-checks the level below — repeatedly — until the Definition of Done is met.**
  Quality is a *loop you stay in*, not a gate you pass once.
- No agent marks its own work "done." The level above verifies it.

---

## 3. The five teams (own your seam; honor the contracts)

| Team | Seam | Charter |
|---|---|---|
| **T1** | Orchestrator & Scheduler (the spine) | `.github/agents/orchestrator-lead.md` |
| **T2** | Worker Agent & Instant-Yield (the money shot) | `.github/agents/worker-agent-lead.md` |
| **T3** | Isolation & Sandbox | `.github/agents/isolation-lead.md` |
| **T4** | Trust, Verification & Rewards | `.github/agents/trust-rewards-lead.md` |
| **T5** | Dashboard, Workloads & Demo | `.github/agents/dashboard-demo-lead.md` |

Ownership, contracts, and milestones: `docs/execution-plan.md`.

---

## 4. Definition of Done (applies to EVERY change)

1. **Complete** — feature fully implemented; no stubs/`TODO`/`pass` on the demo path.
2. **Hardened** — explicit error handling, timeouts, and edge cases; graceful degradation
   (e.g. no NVIDIA driver → `has_gpu=false`, never a crash). **The demo path never throws.**
3. **Tested** — unit tests for the logic **plus** one integration test that exercises the
   real slice. `uv run pytest` green; `uv run ruff check` clean.
4. **End-to-end** — wired into the running system through the **real contracts** (HTTP API +
   signed manifest). No orphan modules; **no mocks on the demo path** except the sanctioned
   fallbacks in `architecture.md` §13 (and those are disclosed in the report).
5. **Demoable** — the specific demo beat it supports actually runs on the demo machine.
6. **Documented** — run instructions + a one-paragraph "what / why."

> **Scope discipline:** "complete / hardened" applies to the **in-scope demo path only.**
> Do **not** build the §13 cut-list (TEE, NPU, cosign/OIDC, model-sharding, NATS,
> adaptive replication, true checkpoint/resume). Gold-plating roadmap items = failing the
> timebox, which is itself a DoD failure.

---

## 5. Review protocol (the repeated quality check)

| Gate | Who | What |
|---|---|---|
| **G0 — self-review** | elite engineer | run tests + lint, re-read your own diff, close gaps before handing up. |
| **G1 — staff review** | owning Staff Engineer | spawn `code-review` **and** `rubber-duck` subagents on the diff; verify DoD + your contract + your demo beat; **bounce it back until green.** |
| **G2 — integration review** | Chief of Staff | run the **full slice** end-to-end; `code-review` the integrated change; verify cross-team contracts and the sacred demo path; reject scope creep vs §13. |
| **G3 — acceptance** | CEO | COS reports up; CEO accepts or redirects. |

A change is not "done" until it clears the gate **above** it. Loop, don't rubber-stamp.

---

## 6. Status-report format (every hand-off, up the chain)

```
DONE      — what works, and how you demoed it
DoD       — ✅/❌ per item (Complete / Hardened / Tested / E2E / Demoable / Documented)
RISKS     — ranked blockers & open questions
NEXT      — the next concrete step
ASKS      — decisions you need from the level above
```

Honesty over polish. If something is mocked, faked, or cut — say so in RISKS.

---

## 7. Tech conventions (PoC)

- **Lang/env:** Python ≥ 3.11, `uv` (`uv sync`, `uv run …`). Lint **ruff** (line-length 100).
  Tests **pytest** under `tests/`.
- **Control plane:** FastAPI + uvicorn. **State/queue/ledger:** **SQLite** (one orchestrator).
- **Transport:** plain HTTPS, **short-poll 1–2 s** (not 60 s long-poll) for 2–3 workers.
- **Worker:** Python + `ctypes` (Win32 idle APIs) + `pynvml` (guarded in try/except).
- **Isolation:** **Docker-per-job by default** (`--network none`, RO mounts, `--rm`); Windows
  Sandbox `.wsb` only if local-admin is confirmed; resource caps + kill-on-close via **Job Objects**.
- **GPU jobs:** **host-side under a Job Object** (real CUDA). GPU-in-Sandbox is broken (MS issue #42) — do not rely on it.
- **Signing:** **local Ed25519** (`cryptography`). Demo the *refusal* on a flipped byte. (cosign/OIDC = roadmap.)
- **AI workload:** **anthropic / openai SDK** (each worker scores a prompt slice). **No vLLM** (no Windows support).
- **Dashboard:** **Streamlit / Gradio** (or a 500 ms-polling HTML page). No hand-rolled SSE/WebSocket.
- **Topology:** orchestrator runs on a **physical LAN PC**, never the cloud dev box (it is GPU-less,
  Sandbox-less, and LAN-unreachable from workers).

When in doubt, do what makes the demo path more reliable — and write it down in your report.

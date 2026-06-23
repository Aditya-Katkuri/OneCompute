# NightShift — Agent Harness (how the org runs)

> How the four-level agent organization works, and how the CEO drives it from Copilot CLI.
> The rules every agent follows live in [`.github/copilot-instructions.md`](../.github/copilot-instructions.md).
> Who owns what lives in [`execution-plan.md`](./execution-plan.md).

---

## 1. The org chart

```
                          ┌─────────────┐
                          │     CEO      │  (you — general orders, acceptance)
                          └──────┬──────┘
                                 │ orders ▼   ▲ status
                        ┌────────┴────────┐
                        │ Chief of Staff   │  (the lead Copilot session — me)
                        │  integration+QA  │
                        └──┬───┬───┬───┬──┘
              ┌────────────┘   │   │   └────────────┐
              ▼            ▼    ▼    ▼               ▼
        ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐
        │ T1 Orch │ │ T2 Work │ │ T3 Isol. │ │ T4 Trust │ │ T5 Dash/Demo │  Staff Engineers
        └────┬────┘ └────┬────┘ └────┬─────┘ └────┬─────┘ └──────┬───────┘
             ▼           ▼           ▼            ▼               ▼
         elite eng   elite eng    elite eng    elite eng       elite eng     subagents (the doers)
```

- **CEO** gives *general intent* ("get the yield moment rock-solid", "make the dashboard demo-ready").
- **Chief of Staff** decomposes it, delegates to the right Staff Engineers, integrates, and quality-checks.
- **Staff Engineers** own one subsystem each, command elite-engineer subagents, and review their output.
- **Elite engineers** are subagents that write the code for one unit of work.

Every level **quality-checks the level below, repeatedly**, against the Definition of Done.

---

## 2. How each level maps to Copilot CLI

| Role | What it actually is | Mechanism |
|---|---|---|
| Chief of Staff | your main Copilot session | the lead agent you talk to |
| Staff Engineer | a custom agent in `.github/agents/*-lead.md` | invoke via `/agent <name>` or COS delegates to it as a subagent |
| Elite engineer | a `general-purpose` subagent | spawned by a Staff Engineer via the task tool |
| QA passes | `code-review` + `rubber-duck` subagents | run at G1 (staff) and G2 (integration) |
| Parallel teams | several Staff Engineers at once | `/fleet` |

---

## 3. Loading the harness in this chat

The custom agents live in the repo, so point Copilot at the repo:

```
/cwd C:\Users\t-cfinney\OneDrive - Microsoft\Documents\hackathonproject
/agent        # lists chief-of-staff + the five *-lead agents
```

- `.github/copilot-instructions.md` is then auto-loaded for every agent (the shared doctrine).
- Want the leads available from any folder too? Mirror the files into `~/.copilot/agents/`.
- `/fleet` turns on parallel subagent execution; `/subagents` sets which model each agent uses
  (give the hard teams — T2/T3 — your strongest model).

---

## 4. How the CEO drives it (give intent, not instructions)

You speak only to the Chief of Staff. Examples of good "general orders":

- *"Stand up the end-to-end skeleton: a worker should register, get a job, return a result, and earn a point.
  Keep it green."* → COS tasks T1 + T2 (+ a thin T4 hook), integrates, reports back with a one-command demo.
- *"The instant-yield moment is the whole pitch — make it bulletproof and rehearsable."* → COS drives T2 + T3,
  reviews the kill-on-close handshake hardest, and shows you the 0.3 s flip.
- *"Get the dashboard demo-ready for Praj to present Friday."* → COS drives T5 against live data, runs the
  full run-of-show twice, hands Praj `docs/demo-script.md`.

The COS always replies in the standard format: **DONE / DoD / RISKS / NEXT / ASKS.**

---

## 5. The quality loop (why output is complete, hardened, tested, e2e)

Nothing is "done" until it clears the gate above it — and gates are **loops, not stamps**:

```
elite eng ──build──▶ G0 self-review (tests+lint+re-read diff)
        ▲                         │ pass
        │ bounce w/ specific gaps  ▼
Staff Engineer ── G1: code-review + rubber-duck + DoD + contract + demo beat
        ▲                         │ green
        │ bounce                   ▼
Chief of Staff ── G2: run full slice + code-review integrated + cross-team contracts + sacred demo path
        ▲                         │ green
        │ redirect                 ▼
      CEO ── G3: accept or redirect
```

**Definition of Done** (full text in the doctrine): Complete · Hardened · Tested · End-to-end · Demoable · Documented.
Hardening applies to the **in-scope demo path only** — building `architecture.md` §13 cut-list items is itself
a DoD failure (it burns the timebox).

---

## 6. Cadence

- **Per task:** build → G0 → G1 → integrate → G2 → report up.
- **Daily:** COS gives the CEO one consolidated status (DONE/DoD/RISKS/NEXT/ASKS) + the current end-to-end demo.
- **At milestones:** COS updates [`execution-plan.md`](./execution-plan.md) and re-confirms the demo path is green.

---

## 7. Files in this harness

| File | Purpose |
|---|---|
| `.github/copilot-instructions.md` | shared doctrine: mission, DoD, review gates, conventions (auto-loaded) |
| `.github/agents/chief-of-staff.md` | the COS charter (this session's role) |
| `.github/agents/{orchestrator,worker-agent,isolation,trust-rewards,dashboard-demo}-lead.md` | the five Staff Engineer charters |
| `docs/harness.md` | this file — how the org runs |
| `docs/execution-plan.md` | work distribution, contracts, milestones, RACI, timeline |

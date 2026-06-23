# OneCompute — 5-Employee Pilot Test Plan

> Route a real workload across 5 willing employees' **issued (Intune-managed)** machines.
> Pairs with [`idea.md`](./idea.md) (§8 trust, §10 risks, §13 demo-feasibility), the worker
> entrypoint (`python -m worker`), the LAN/cloud orchestrator (`python -m orchestrator`,
> [`deploy.md`](./deploy.md)), and the **demand-adaptive governor** (`architecture.md` §3.2).
> Companion docs: [`pilot-consent.md`](./pilot-consent.md), [`pilot-it-sanction.md`](./pilot-it-sanction.md).

---

## 0. The gate that comes first — IT/Security sanction (do not skip)

Issued machines run the corporate endpoint stack, and our agent collides with it head-on:

- **Microsoft Defender for Endpoint** treats sustained CPU/GPU as a **cryptojacking** signature → can quarantine the agent mid-run.
- **Purview DLP** can block a job's data I/O.
- **WDAC / AppLocker** can block an unsigned agent from executing; there is **no local admin**.

idea.md §13 is explicit: *"demo on machines you control, not Intune-managed laptops."* So the
pilot is **gated on written sign-off**: AV **allow-list** the agent (publisher + SHA-256), confirm
it may run, and agree the data scope. This is **"pass, not bypass"** (§8). **Until sanctioned,
run only on unmanaged / loaner machines.** Use [`pilot-it-sanction.md`](./pilot-it-sanction.md).

## 1. Consent & guardrails (employee-facing)

Each employee signs a one-page opt-in ([`pilot-consent.md`](./pilot-consent.md)): what it does,
that it's **headroom/idle-only**, **never on battery**, **instant opt-out** (Ctrl-C / uninstall),
caps, and that usage profiling is **on-device only** (§8). The **adaptive governor** keeps work in
their spare headroom and yields the instant *their* compute demand rises.

## 2. Topology & connectivity

- Orchestrator on a **reachable HTTPS endpoint**: an **Azure VM** (5 remote/different networks) or
  a **physical LAN PC** (all on corp LAN/VPN). Workers are **outbound-only short-poll** — no inbound
  ports on employee machines (NAT/firewall-proof). See `deploy.md` + the cloud deploy guide.
- **Hour-1 reachability check** from each machine: `curl https://<orchestrator>/healthz` → `{"ok":true}`.

## 3. Worker deployment (no-admin path)

- **Preferred:** a **code-signed worker `.exe`** (PyInstaller) → enables AV allow-listing by
  publisher/hash and matches §8 "code-signed." Distribute the exe + its SHA-256 to IT.
- **Fallback:** user-space Python (winget/uv, no admin) → `python -m worker --url https://… --governor adaptive`.
- **Isolation reality on managed machines:** Docker/Windows-Sandbox are usually unavailable / need
  admin → the agent **gracefully falls back to subprocess + Windows Job Object** (no admin), honestly
  reported by `active_boundary()`. Record each machine's boundary.

## 4. Workload to route — safe first, then real

1. **CPU fan-out** (`data.transform`) — deterministic; the orchestrator verifies the proof hash.
2. **Challenge / ringer** — integrity spot-check (catches tampering; blacklists + forfeits).
3. A **real internal batch** (`ai.batch_infer` eval set, or another genuinely cloud-substitutable
   job) — so the pilot mirrors production value. Keep jobs **small, chunked, yieldable** early.

## 5. Phased rollout (bake time + kill switch)

| Phase | Scope | Goal / exit |
|---|---|---|
| **0** | 1 machine **you control** | Full slice, governor on, **watch Defender** → zero alerts + correct sub-second yield. *Gates the rest.* |
| **1** | **1 employee**, supervised, ~1 hr, low intensity | Measure CPU impact + Defender + yield; ask "did you notice anything?" |
| **2** | **All 5**, a real batch, a few hours / overnight | Dashboard live; kill switch ready. |
| **3** | ~1 day | Let the governor **learn each machine's envelope**; measure sustained harvest. |

## 6. Success criteria (what we measure)

- **Unobtrusiveness (make-or-break):** employees report **no perceptible slowdown**; **sub-second
  yield** on demand spikes; % time harvesting vs yielded; the learned per-machine envelope.
- **Throughput:** jobs completed; **measured harvested throughput** (honest, reported *beside* the
  1.8-ExaOPS ceiling — never instead of it); credits/machine (GPU 5×).
- **Trust/safety:** **zero Defender/Purview incidents**; challenge caught any cheat; signed-manifest
  verification held; no data leakage (no-persistence, data-minimization).
- **Reliability:** churn (sleep → return → requeue) handled; clean opt-out.

## 7. Observability & rollback

Live **OneCompute dashboard** + ledger + event feed; per-machine **pilot telemetry** (CPU impact,
yield rate, governor decisions) + the local profile + **any AV alerts**. **Instant rollback:** stop
the orchestrator (all workers go idle), employees Ctrl-C / uninstall; profiles are local-only.

## 8. Ranked risks → mitigations (idea.md §10)

1. **Defender / Intune / Purview collision** *(highest, blocking)* → sanction + allow-list + signed exe; start on controlled machines.
2. **Corp proxy/VPN blocks transport** → validate hour 1; cloud HTTPS endpoint; short-poll only.
3. **No admin / WDAC** → signed exe + IT allow; subprocess+Job-Object fallback (no admin).
4. **Privacy leakage** → data minimization, no-persistence, on-device profiling only.
5. **Reward gaming / Sybil** → corp-SSO one-identity-per-node; validated-output-only metering.

## 9. Roles & timeline (suggested)

- **CEO** — exec sponsor; secures the IT/Security meeting.
- **CoS** — builds the agent/exe/telemetry/orchestrator; runs Phase 0.
- **IT/Security** — allow-list + sign-off (the gate).
- **5 employees** — opt-in, run the worker, report subjective impact.

Indicative: Day 0 sanction + signed exe → Day 1 Phase 0–1 → Day 2 Phase 2 → Day 3 Phase 3 + readout.

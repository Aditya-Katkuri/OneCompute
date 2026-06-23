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
run only on unmanaged / loaner machines.** **Follow the step-by-step in
[`pilot-security-approval.md`](./pilot-security-approval.md)** (code-sign → Defender allow-list →
WDAC → Purview → written risk acceptance), and hand reviewers
[`pilot-it-sanction.md`](./pilot-it-sanction.md).

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
- **Fallback (verified to run on a managed machine):** user-space Python (winget/uv, no admin) →
  `python -m worker --url https://… --governor adaptive`. *(In testing the **unsigned exe was
  blocked by Application Control** while the from-source path under the allowed uv Python ran — so
  until the exe is corp-signed, use this path. Build/sign instructions: `scripts/build_worker_exe.ps1`.)*
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

---

## 10. Pre-flight checklist (all ✅ before Phase 0)

**Security** (see [`pilot-security-approval.md`](./pilot-security-approval.md)): ☐ binary code-signed +
SHA-256/publisher recorded · ☐ Defender allow-list active on all 5 · ☐ WDAC/AppLocker trusts it ·
☐ Purview/network confirmed · ☐ **written risk acceptance** (5 devices + expiry) in hand ·
☐ 5 consents signed · ☐ monitoring contact on call.
**Infra:** ☐ orchestrator up on its HTTPS endpoint · ☐ each device `curl https://<orchestrator>/healthz` → `{"ok":true}`.
**Software:** ☐ each machine runs the worker (signed exe, or from-source under allowed Python) · ☐ `active_boundary()` recorded per machine.
**Workload:** ☐ first job set staged (small deterministic CPU fan-out + a challenge ringer).
**Rollback rehearsed:** ☐ stopping the orchestrator idles workers · ☐ Ctrl-C stops a worker.

## 11. Per-phase runbook (entry → do → exit/record)

- **Phase 0 — machine you control.** *Entry:* pre-flight green. *Do:* run orchestrator + 1 worker (yours),
  submit fan-out + challenge, force a CPU spike to test yield, **watch Defender**. *Exit/record:* zero
  alerts; governor admits in headroom **and** yields sub-second on the spike; results verified; `pilot_report`
  clean.
- **Phase 1 — 1 employee, supervised ~1 hr, low intensity.** *Entry:* Phase 0 green + consent. *Do:* they
  work normally; you watch CPU/yield telemetry + Defender. *Exit/record:* **"no perceptible slowdown"** (their
  words), zero alerts, yield correct, clean opt-out test.
- **Phase 2 — all 5, real batch, hours/overnight.** *Entry:* Go (see §13). *Do:* dashboard live, kill switch
  ready. *Exit/record:* batch completes; **zero incidents**; per-machine `pilot_report`; churn handled.
- **Phase 3 — ~1 day.** *Entry:* Phase 2 green. *Do:* let governors learn each envelope; sustained harvest.
  *Exit/record:* measured sustained throughput; learned profiles; readout.

## 12. Metrics & data collection

| Metric | How measured | Target |
|---|---|---|
| Unobtrusiveness (subjective) | employee survey ("notice a slowdown?" 1–5) | "no / barely" |
| Yield latency on demand spike | telemetry + forced-spike test | **< 1 s** |
| % time harvesting | `pilot_report` (admitted/ticks) | report (no hard target) |
| Harvested throughput | dashboard + ledger | measured, **honest vs the 1.8-ExaOPS ceiling** |
| **Security incidents** | Defender/Purview monitoring | **0** |
| Integrity | challenge/ringer results | cheats caught; honest workers pass |
| Reliability | requeue on sleep/return | no lost or duplicated work |

## 13. Go / No-Go gate (after Phase 1)

Proceed to Phase 2 **only if all true:** zero security incidents · no perceptible slowdown · yield verified
sub-second · clean opt-out · telemetry sane. Otherwise **stop + remediate**. **Deciders:** CEO + the security
risk owner (jointly).

## 14. Incident & rollback playbook

- **Defender/Purview alert →** stop the affected worker immediately (Ctrl-C / remote), notify the security
  contact, **pause the pilot**, investigate the indicator/allow-list; do not resume until cleared.
- **Employee reports slowdown →** stop their worker; check telemetry (governor admitting too aggressively?);
  raise the **margin** / **min-headroom** or lower the admission ceiling; re-test on your machine first.
- **Orchestrator down →** workers idle automatically; restart it (state persists in the `--db` file).
- **Full stop →** stop the orchestrator + all workers; **remove the Defender allow-list entry** at pilot end.

## 15. Comms plan

- **Employees:** the consent sheet + a "what to expect / how to stop / who to tell" note; a check-in after Phase 1.
- **IT/Security:** the sanction doc up front; a **monitoring contact during the run**; a short readout after.
- **Sponsor:** a daily one-line status + the go/no-go decision.

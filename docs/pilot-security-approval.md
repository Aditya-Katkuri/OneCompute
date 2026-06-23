# OneCompute Pilot — Security Approval Runbook (how to get cleared to test)

> **Plain truth first:** this is **not** a "security clearance." It's an **internal security review +
> endpoint allow-listing + written risk acceptance** for a **time-boxed, opt-in, 5-device pilot**.
> Frame it that way and it's a routine exception review, not a saga.
>
> **Use this with:** [`pilot-it-sanction.md`](./pilot-it-sanction.md) (the one-pager you hand the
> reviewers), [`pilot-plan.md`](./pilot-plan.md), and [`pilot-consent.md`](./pilot-consent.md).
> Exact owners / ticket queues / tool names vary by org — confirm yours with your security contact;
> the **functions** below (Defender allow-list, code-signing, Intune, Purview, risk acceptance) are
> what you need regardless of the internal names.

---

## TL;DR — the five approvals you actually need
1. **Code-sign** the worker binary (corporate Authenticode cert / signing service).
2. **Defender for Endpoint allow-list** the signed binary (publisher + SHA-256) on the 5 devices.
3. **Application Control (WDAC/AppLocker)** trusts it to execute (via the signature, or an exception).
   *We already proved the **unsigned** build is blocked — see the evidence in `pilot-it-sanction.md`.*
4. **Purview DLP** permits the orchestrator egress for the pilot data class.
5. **Written risk acceptance** from the data/security owner — **scoped to 5 named devices, with an expiry.**

Everything else (Intune, network, consent) is confirmation/paperwork around those five.

---

## Step 0 — Frame it to win (do this before any meeting)
Reviewers say yes to things that are **small, reversible, opt-in, internal, time-boxed, and watched**.
Lead with all six:
- **Blast radius:** 5 named, **opt-in** machines. **Time-boxed** (e.g. 1–2 weeks). **Internal-only** (no
  external compute marketplace).
- **Reversible:** stop the orchestrator → all workers idle within one poll; employees Ctrl-C/uninstall;
  the only artifact is a local profile file.
- **Designed to pass, not bypass:** signed manifests, per-job sandbox, no-persistence, data-minimization,
  on-device-only profiling, outbound-only (no inbound ports). (idea.md §8.)
- **You'll watch the alerts live** during the run and pull the plug on the first anomaly.

## Step 1 — Get an executive sponsor + name a security champion
- **Exec sponsor** (your manager or above) who owns the business case and can ask for the review.
- **Security champion** — the person/team in your org who owns endpoint policy. If you don't know who:
  ask your manager, or look up who administers **Defender for Endpoint** / **Intune** for your org.
- Get the 5 volunteers' **managers' OK** (their machines, their time).

## Step 2 — Pre-read & route the request
- Send your security champion: `pilot-it-sanction.md` + `pilot-plan.md` + a 3-line summary
  ("opt-in 5-device internal compute pilot; need allow-listing + risk acceptance; time-boxed").
- Ask the routing question explicitly: **"Which review path and risk-acceptance process applies to
  running a code-signed, opt-in internal agent that does sustained CPU on 5 managed devices?"**
- Open whatever **ticket / review request** they name. Attach the artifacts.

## Step 3 — Security / threat-model review meeting
Walk them through the **two trust boundaries** (idea.md §8) and the **threat model**:
- *Worker ← job:* per-job sandbox, signed-manifest verification (code+data hash) before run, no-persistence.
- *Job ← worker:* data-minimization, challenge/ringer integrity checks, results-only metering.
- **Data scope:** only the job slice in, the result out; no file shares / mailboxes / browser data.
- **The unobtrusiveness mechanism:** the demand-adaptive governor runs in *learned spare headroom* and
  **yields sub-second** on the employee's own demand (so it backs off under real load by design).
- Capture every action item; turn each into an allow-list/exception request below.

## Step 4 — Code-sign the binary (unblocks Application Control)
- Build the binary: `scripts/build_worker_exe.ps1` → `onecompute-worker.exe` + its SHA-256.
- Submit it to your **corporate code-signing service** (Authenticode / your org's signing pipeline).
- **Re-hash after signing** and record the new SHA-256 + the publisher/cert subject in `pilot-it-sanction.md`.
- *Why first:* we verified the **unsigned** exe is **blocked by Application Control** on a managed
  machine; signing (with a trusted publisher) is what lets it execute — and what allow-listing keys on.

## Step 5 — Defender for Endpoint allow-list
- Request the endpoint team add an **allow / custom indicator** for the signed binary by **certificate
  (publisher)** and **SHA-256**, scoped to the **5 device names/IDs**, so its sustained CPU isn't
  flagged/quarantined as cryptojacking.
- Confirm the indicator is **active on all 5** before any run (Step 9 verifies).

## Step 6 — Application Control (WDAC / AppLocker)
- Confirm the org's WDAC/AppLocker policy **trusts the corporate publisher** (then the signed exe runs),
  **or** request a scoped exception for the binary on the 5 devices.
- *Interim, if signing is delayed:* run **from source** under an already-allowed Python (uv/winget) — we
  verified that path runs on a managed machine. (Get this interim explicitly OK'd too.)

## Step 7 — Intune / device management
- Confirm running the agent **won't trip device compliance** on the 5 machines.
- Decide the **delivery method**: simplest for a pilot is the employee **runs it in user space** (no Intune
  packaging, no admin). If they want it managed, request an **Intune app** approval (longer).

## Step 8 — Purview DLP & network
- Confirm **Purview endpoint DLP** won't block the orchestrator egress for the pilot's data class — or get
  a scoped policy note for the endpoint.
- Confirm **firewall/proxy** allows the workers' outbound HTTPS to the orchestrator FQDN/port (no inbound).

## Step 9 — Written risk acceptance (the actual "clearance")
- Get the data/security owner's **written sign-off**: a short risk-acceptance that names the **5 devices**,
  the **pilot window (start–end)**, the **allow-list entries**, the **rollback plan**, and a **named owner**.
- **This document is the green light.** Keep it; reference it in the go/no-go.

## Step 10 — Employee opt-in
- Each of the 5 signs `pilot-consent.md`. Confirm their managers are aware. Schedule the run.

## Step 11 — Go / No-Go (verify before you flip it on)
Run the pre-flight checklist (pilot-plan §"Pre-flight") and confirm **all** are true:
- ☐ Signed binary + recorded SHA-256/publisher · ☐ Defender allow-list active on all 5 · ☐ WDAC trusts it
- ☐ Purview/network OK · ☐ Written risk acceptance in hand · ☐ 5 consents signed · ☐ Monitoring contact on call
- ☐ Reachability: each device `curl https://<orchestrator>/healthz` → `{"ok":true}`
If any box is empty → **No-Go.** Otherwise start **Phase 0 on a machine you control**, watching Defender.

---

## Getting to "yes" faster — objections & responses
| Objection | Response |
|---|---|
| "Sustained CPU = cryptojacking." | Opt-in, 5 named devices, **time-boxed**, **signed + allow-listed**, governor backs off under load, we watch alerts live. |
| "Unsigned binary won't run / is risky." | Correct — **we proved Application Control blocks it**; we're **signing it** with the corp cert and requesting allow-listing (the doctrine: *pass, not bypass*). |
| "What about user data?" | Per-job sandbox; no file/mail/browser access; data-minimized job slices; no-persistence; profiling is **on-device, never uploaded**. |
| "How do we stop it?" | Stop the orchestrator → idle within one poll; employees Ctrl-C/uninstall; allow-list entry removed at pilot end. |
| "Why not just use cloud?" | This pilot *validates* the harvest-vs-cloud economics on a tiny, safe scale before any spend. |

## Approval tracking (fill in)
| Item | Owner | Status | Date | Ref/ticket |
|---|---|---|---|---|
| Exec sponsor secured | | | | |
| Security review completed | | | | |
| Binary code-signed (new SHA-256) | | | | |
| Defender allow-list active (×5) | | | | |
| WDAC/AppLocker trust/exception | | | | |
| Purview DLP / network confirmed | | | | |
| **Written risk acceptance** (5 devices, expiry) | | | | |
| 5 employee consents | | | | |
| Go/No-Go: **GO** | | | | |

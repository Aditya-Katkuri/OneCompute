# Security-Champion Kickoff Email (copy/paste, then fill the <brackets>)

> Sends the request to whoever owns endpoint policy (Defender/Intune) for your org. Keep it short;
> attach [`pilot-it-sanction.md`](./pilot-it-sanction.md), [`pilot-plan.md`](./pilot-plan.md),
> [`pilot-consent.md`](./pilot-consent.md). Full process: [`pilot-security-approval.md`](./pilot-security-approval.md).

---

**To:** \<security champion / endpoint-security team\>
**Cc:** \<your manager / exec sponsor\>
**Subject:** Security review request — small opt-in internal compute pilot (5 devices, time-boxed)

Hi \<name\>,

I'm running a small internal proof-of-concept called **OneCompute** that harvests **spare CPU/GPU
headroom** on opt-in employee PCs to run **internal batch jobs** (test/eval), and I'd like your help to
do it **the sanctioned way**. I want to test it on **5 willing colleagues' issued machines**, and I
know sustained CPU can look like cryptojacking to Defender — so I'm asking for review and allow-listing
up front, not working around anything.

**Why it's low-risk:**
- **Opt-in**, **5 named devices**, **time-boxed** (\<1–2 weeks\>), **internal-only**.
- A code-signed, **user-space** agent (no admin/service). Outbound HTTPS only — **no inbound ports**.
- Each job runs **sandboxed** (can't see user files), with **signed-manifest verification** and
  **no-persistence**; usage profiling is **on-device only**, never uploaded.
- It runs in **learned spare headroom** and **yields sub-second** when the employee's own apps need the
  machine — it backs off under real load by design.
- Fully **reversible**: stop the server → workers idle within one poll; users can Ctrl-C/uninstall.

**What I'm asking for (the 5 approvals):**
1. **Code-sign** the worker binary (our signing service) — *note:* I tested it and the **unsigned**
   build is **blocked by Application Control**, which is exactly why I want to do this properly.
2. **Defender for Endpoint allow-list** the signed binary (publisher + SHA-256) on the 5 devices.
3. Confirm **WDAC/AppLocker** trusts it (via the signature) or a scoped exception.
4. Confirm **Purview DLP** / network allows the agent's outbound HTTPS to the orchestrator.
5. A short **written risk acceptance** scoped to the 5 devices with an expiry.

I've attached a one-page technical summary (**pilot-it-sanction.md**), the **pilot plan**, and the
**employee consent** form. Could we grab **30 minutes** this week so you can point me at the right
review path and risk-acceptance process? I'll watch Defender alerts live during the run and stop on the
first anomaly.

Thanks,
\<your name\>

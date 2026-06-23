# NightShift

> **Let your compute work when you are not.**
> Turn the idle CPUs, GPUs, and NPUs of a company's existing PC fleet into a privacy-preserving, opt-in internal compute grid — and pay the employees who lend their machines.

---

## 1. Elevator pitch

Every large company already owns a second supercomputer. It's just scattered across tens of thousands of employee laptops and desktops that sit idle overnight, on weekends, and through the long stretches of the day when they're only running a browser and a chat app.

**NightShift** harvests that idle capacity. Employees opt in; NightShift runs sandboxed workloads on their machines *only when the machine is otherwise idle*; employees earn rewards (points, gift cards, subscriptions) for the compute they contribute. The company gets a low-marginal-cost pool of compute that can absorb work it would otherwise pay a cloud provider to run.

It is, in spirit, **BOINC / Folding@home for the internal AI-PC era** — but internal, incentivized, governed, and built for both AI and non-AI jobs. The closest commercial blueprint is **[Salad](https://salad.com/security)** (idle consumer GPUs → a paid compute cloud); NightShift's delta is *internal-only*, which buys a higher trust baseline and a cleaner privacy story.

---

## 2. The datacenter problem — why harvest idle compute at all

The industry's default answer to AI demand is to **build more datacenters**. That answer is getting brutally expensive, slow, and physically constrained:

- **Capex is staggering.** Amazon, Microsoft, Alphabet, and Meta alone are projected to spend **~$725B combined on AI infrastructure in 2026**, with the total industry buildout approaching **$1 trillion**. ([24/7 Wall St](https://247wallst.com/investing/2026/05/19/the-1-trillion-ai-data-center-buildout-is-fueling-a-cost-consumers-cant-escape/))
- **Per-facility cost is exploding.** The average new datacenter now runs **~$475M** — up **167% year-over-year**; U.S. datacenter construction spend hit **$49.5B through April 2026**, nearly 4× the prior-year pace. ([Westside Construction Group](https://www.buildwcg.com/blog-posts/data-center-power-grid-construction-ai-infrastructure-2026))
- **Power, not chips, is the bottleneck.** A single AI training facility can draw **100–500 MW continuously** (a small city); gigawatt-class campuses are now the norm (e.g. a **1.4 GW** Texas campus valued at **$25B**, completing 2028). Grid interconnects and high-power transformers have **2–5 year lead times**. ([Quartz](https://qz.com/ai-data-centers-gigawatt-power-grid-strain-051126))
- **Water and grid strain are real externalities.** A 100 MW datacenter consumes **~300,000 gallons of water/day** (≈2,600 households); hyperscale direct water use is projected at **16–33 billion gallons/year by 2028**, and AI buildout is already pushing up consumer electricity bills. ([Consumer Reports](https://www.consumerreports.org/data-centers/ai-data-centers-impact-on-electric-bills-water-and-more-a1040338678/))
- **Centralization has structural downsides:** multi-year build lead times, permitting and siting fights, concentrated points of failure, carbon, and GPUs that **depreciate faster than the building** — often while sitting under-utilized.

### The contrast: distributed idle compute is *already built*

| | Build a new datacenter | Harvest the idle fleet (NightShift) |
|---|---|---|
| **Capex** | ~$475M/facility, ~$1T industry | **$0** — hardware already bought |
| **Marginal cost** | land + power + water + cooling + GPUs | **~$10–20/mo/machine** of incremental electricity |
| **Lead time** | 2–5 years (grid, transformers, permits) | **Switch it on tonight** |
| **Power/water** | new substations, 100s of MW, millions of gallons | rides existing, already-distributed draw |
| **Resilience** | concentrated failure domain | naturally geo-distributed |
| **Scaling** | new construction | **grows for free** with each PC refresh (see §3) |

> **Honest framing:** NightShift does **not** replace datacenters for frontier *training* — that needs co-located, high-bandwidth GPU clusters. It's a **pressure-relief valve** for the large class of *substitutable* workloads (eval, batch inference, agent runs, synthetic data) that don't need to sit in a $475M building. **Every idle-compute-hour harvested is a dollar of datacenter capex/opex deferred.**

---

## 3. Why now — the edge-AI tailwind

The pitch gets stronger every hardware refresh, because the compute sitting inside a normal employee's machine is rising fast — and it's compute the company already paid for.

- **Every Copilot+ PC ships an NPU above the 40-TOPS floor** Microsoft set for the program — Qualcomm Snapdragon X Elite/Plus at **45 TOPS (INT8)**, AMD Ryzen AI 300 at **50 TOPS**, Intel Lunar Lake up to **48 TOPS** — *on top of* the CPU and integrated GPU. ([Copilot+ requirements](https://learn.microsoft.com/en-us/windows/ai/npu-devices/) · [Qualcomm](https://www.qualcomm.com/news/onq/2024/06/what-on-earth-is-a-copilot-plus-pc) · [AMD](https://www.amd.com/en/partner/articles/ryzen-ai-300-series-processors.html))
- **Discrete laptop GPUs are where the real harvestable AI throughput lives.** NVIDIA's RTX 50-series *mobile* parts deliver **440 → 1,824 AI TOPS (INT8)** depending on tier (5050 → 5090) — an order of magnitude beyond the NPU. ([NVIDIA RTX 50 laptops](https://www.nvidia.com/en-us/geforce/laptops/50-series/)) This is exactly why the PoC harvests **CPU + GPU**.
- **Datacenter-class silicon is becoming a *personal* device.** At **Build 2026**, Microsoft unveiled the **Surface RTX Spark Dev Box** — a developer desktop built on NVIDIA's **RTX Spark superchip** (Blackwell RTX GPU + Grace CPU) delivering **~1 petaflop of AI compute and 128 GB of unified memory** for running large models locally. ([Microsoft Surface](https://www.microsoft.com/en-us/surface/devices/surface-rtx-spark-dev-box) · [Windows Devices Blog](https://blogs.windows.com/devices/2026/06/02/building-the-next-generation-of-devices-for-developers-surface-rtx-spark-dev-box/)) Announced alongside it, NVIDIA's **DGX Station for Windows** (GB300 Grace Blackwell Ultra) puts **up to 20 PFLOP (FP4), 748 GB** of coherent memory, and **trillion-parameter** local inference on a single desk. ([NVIDIA](https://nvidianews.nvidia.com/news/nvidia-dgx-station-for-windows-puts-a-trillion-parameter-ai-supercomputer-on-every-enterprise-desk) · [SiliconANGLE](https://siliconangle.com/2026/06/01/nvidia-squeezes-powerful-1-trillion-parameter-ai-supercomputer-deskside-form-factor/))
- **Net effect:** *individual* machines are absorbing workloads that used to require a datacenter. The **Surface RTX Spark Dev Box** and **DGX Station** become NightShift's future **"super-node"** tier — and every fleet refresh grows the latent idle capacity for free.

> **Accuracy note:** **TOPS is a nameplate INT8 peak, not delivered throughput.** Real harvested performance is materially lower (precision, thermals, memory bandwidth, contention with foreground work). NightShift's scheduler **benchmarks each machine's real throughput** rather than trusting the spec sheet. ([why TOPS ≠ real-world](https://www.newtechguy.com/ai-pc-buying-guide-2025-npu-tops-ratings-performance-benchmarks-and-what-actually-matters/))

---

## 4. The thought experiment (the "why this is huge" math)

Treat it purely as a back-of-the-envelope ceiling:

| Assumption | Value |
|---|---|
| Company PCs available at any given moment | ~40,000 |
| Class of machine | Copilot+ PC (≈ Snapdragon X Elite) |
| NPU per device | ~45 TOPS (INT8) |
| Usage | Inference / evaluation only (not training) |

$$40{,}000 \times 45\ \text{TOPS} = 1{,}800{,}000\ \text{TOPS} = 1.8\ \text{ExaOPS}$$

> **Label this honestly.** 1.8 ExaOPS is a **theoretical peak NPU aggregate at full Copilot+ fleet adoption** — NPU-only, nameplate INT8, 100% uptime. It is a *ceiling*, not what any demo delivers. And it *understates* a GPU-equipped fleet (a single RTX 5090 laptop alone is ~1,824 INT8 TOPS). The PoC defers the NPU and harvests **CPU + GPU** from a handful of machines; we report a **separate, measured harvested-throughput** number from the real demo fleet *alongside* this ceiling. Mixing peak-NPU marketing math with a CPU+GPU demo invites judge pushback — so we don't.

### What it could displace
Work that runs on paid Azure AI compute today but doesn't strictly need to:

- Model **evaluation / benchmarking** jobs
- **Small-model inference** (and batch inference)
- **Agent execution** runs
- **Synthetic data generation**
- Internal **Copilot / product testing**

---

## 5. What NightShift is

A three-sided system:

1. **Workers** — opt-in employee machines running a lightweight agent that advertises spare capacity (CPU / GPU / NPU), detects idleness, and runs sandboxed jobs.
2. **Orchestrator** — a coordinator that knows the fleet, schedules jobs onto suitable idle workers, handles failure/preemption, and verifies results.
3. **Submitters & rewards** — internal teams submit jobs; contributing employees accrue reward points redeemable for gift cards, subscriptions, or perks.

### Design pillars
- **Opt-in and idle-only.** NightShift never competes with the employee's own work. It yields the *instant* the human comes back — this unobtrusiveness, not raw throughput, is what made or broke every prior enterprise grid (see [HTCondor](https://en.wikipedia.org/wiki/HTCondor), [MSR Cyclotron](https://www.microsoft.com/en-us/research/publication/cyclotron-a-secure-isolated-virtual-cycle-scavenging-grid-in-the-enterprise-2/)).
- **Win-win incentives.** The company offloads cost; employees get tangible rewards for compute they weren't using.
- **Privacy-preserving on both sides.** The worker's data is never exposed to jobs; the job's data is never exposed to the worker (see §8).
- **Heterogeneous by design.** CPU *and* GPU (NPU later); AI *and* non-AI workloads.

---

## 6. Workloads — AI *and* non-AI, CPU *and* GPU

NightShift is deliberately **not** an AI-only grid. The same fabric that dispatches an inference job can dispatch a render or a data-processing job. This widens the set of internal teams who can use it and lets the demo show range.

**AI / accelerator workloads** (lean on GPU / NPU):
- Batch LLM / small-model inference · model evaluation & scoring · synthetic data generation · embedding generation

**Non-AI workloads** (lean on CPU / GPU):
- Embarrassingly-parallel batch jobs (data transforms, ETL chunks) · build / test fan-out · rendering / media processing · simulation & parameter sweeps

The unifying abstraction: **a job is a signed, sandboxed unit of work with declared resource needs; a worker is a pool of idle resources with declared capabilities; the orchestrator matches them.**

> **PoC note:** for the AI demo we run **embarrassingly-parallel batch inference** (each worker handles a slice of the prompt set via its own local model server) — *not* a single model sharded across machines. Cross-machine model-sharding (llama.cpp RPC, exo, Petals) is a deliberate roadmap item, kept off the demo path because those transports are explicitly fragile/insecure on open networks.

---

## 7. Incentives — the employee rewards program

Framing is **internal-only**, but explicitly **win-win**:

- Employees **opt in** and stay in control (caps, schedules, "never on battery", instant opt-out).
- Contribution is **metered on *verified useful work*, not claimed FLOPS**:

  > **credits = validated_work × resource_class_multiplier × scarcity_multiplier × uptime/reliability_factor**

  Adapted from [Render's published reward formula](https://medium.com/render-token/compute-client-node-reward-mechanism-update-6b867e348030), with [BOINC's anti-cheat scaffolding](https://github.com/BOINC/boinc/wiki/CreditNew) (per-host normalization, capped multipliers, recent-average-credit decay, probation for new machines). A resource-class + uptime baseline keeps CPU-only / older machines earning meaningfully (fairness).
- Points redeem for **gift cards, subscriptions, perks**.

### The economic case (headline ROI)
- **Displaced cloud value per idle GPU:** an idle laptop GPU running ~8 night-hours displaces roughly **$120–240/mo** of on-demand cloud value. Salad independently pays consumer GPU owners **~$30–180/mo**, demand-indexed. ([AWS GPU pricing](https://wring.co/blog/aws-gpu-instance-pricing-guide) · [Salad earnings](https://support.salad.com/article/60-how-much-can-i-earn-with-salad))
- **Marginal cost:** the company already owns the hardware, so the real cost is mostly **incremental electricity** — ~$10–20/mo/machine at ~$0.17/kWh.
- **Gross spread:** ~**6–15×** displaced value vs. power, *before* rewards.
- **Rewards twist:** points/gift-card programs leak ~40% to vendor markup + breakage, so only ~$0.60 of each $1 reaches the employee — bill **at redemption** (or pay via payroll/PayPal) and pay employees a fixed fraction (~10–30%) of measured displaced value. The company still nets large savings. ([hidden cost of rewards](https://www.worktango.com/blog/hidden-cost-of-employee-rewards))
- **Honest caveat:** only credit work that **would otherwise have run in the cloud**; price against on-demand-minus or the real internal cloud bill, since not all internal jobs are cloud-substitutable.

---

## 8. Privacy & trust

Two trust boundaries; both must hold.

**Protecting the worker (employee machine) from the job:**
- Every job runs in a **per-job sandbox** (Hyper-V-isolated, constrained filesystem & network, resource caps).
- Jobs ship as **signed manifests** ([Sigstore cosign](https://github.com/sigstore/cosign)); the worker verifies *code hash + data hash + resource limits + sandbox policy* before executing, so a compromised orchestrator or MITM can't inject runnable code (the [BOINC code-signing pattern](https://github.com/BOINC/boinc/wiki/SecurityIssues)).
- **No persistence:** job inputs/outputs are wiped when the job ends.

**Protecting the job (and its data) from the worker:**
- **Data minimization** — send only what a shard needs.
- **Result verification** via **challenge / "ringer" tasks** with server-known answers ([Golle & Mironov, MSR](https://www.microsoft.com/en-us/research/wp-content/uploads/2001/04/dist.pdf)) plus reputation-weighted **adaptive replication** ([BOINC](https://github.com/BOINC/boinc/wiki/AdaptiveReplication)) for new/suspect machines — *not* blanket N-way voting (which wastes ~50%+ of capacity). Comparators are **tolerance-aware**, never bitwise (heterogeneous hardware differs in low-order FP bits).

> **PoC scope:** signed manifests + per-job sandbox + no-persistence + challenge-task spot-checking. Full **confidential compute / TEE** is documented as roadmap — it needs **datacenter GPUs** (H100-class); consumer RTX laptops and Copilot+ NPUs have no GPU TEE, so it's correctly future. Residual risks (sandbox escape, side-channels, weaker GPU-passthrough isolation) are disclosed honestly, not hidden.

> **Enterprise-acceptance gate (the most under-stated risk):** the agent's sustained CPU/GPU bursts look *exactly* like **cryptojacking** to the company's own endpoint stack (Defender for Endpoint, Intune, Purview DLP). NightShift must be **code-signed, Intune-deployed, AV allow-listed, and route I/O through sanctioned channels** — designed to **pass, not bypass**, these controls. "Internal = safe" is an anti-pattern: the internal scope shrinks attack surface and makes every action attributable, but we still enforce signing/sandbox/audit at the boundary. ([Purview endpoint DLP](https://learn.microsoft.com/en-us/purview/endpoint-dlp-learn-about) · [CISA on cryptojacking detection](https://www.cisa.gov/news-events/news/defending-against-illicit-cryptocurrency-mining-activity))

---

## 9. Hackathon PoC — what we will actually demo

A small but *real* end-to-end slice that proves the concept:

- **1 orchestrator** (on a physical LAN PC) + **a few Windows PC workers** (CPU + GPU).
- Workers register, report capabilities + idle state, and **pull** jobs over an outbound-only connection (no inbound ports — sidesteps corporate NAT/firewall).
- Submit **two kinds of job live**: one **AI** job (batch inference / eval) and one **non-AI** job (a parallel data/render task) — exercising CPU *and* GPU paths.
- Each job runs **sandboxed + signed**; show the worker can't see job internals and the job can't touch the worker's files.
- A **dashboard** shows: live fleet, idle/busy state, jobs flowing, throughput, and **reward points ticking up** for contributing machines.
- Pull the **"human came back"** lever (lock the screen / move the mouse) to show a worker **yield** mid-job in well under a second.

**Demo narrative:** *"Here are a few normal employee PCs doing nothing. We submit a batch of AI and non-AI work. Watch it fan out across their idle CPUs and GPUs, stay sandboxed, finish faster than one machine could — and watch the employees earn rewards for compute they weren't even using. Now I touch the mouse — and the job instantly steps aside."*

> Concrete build plan, cut-list, and a de-risked demo script are in [`architecture.md`](./architecture.md) §10 and §13.

---

## 10. Open questions & risks (ranked)

1. **Cryptojacking false-positive / endpoint-stack collision** *(highest — demo-blocking on managed machines).* Must be signed, Intune-deployed, AV allow-listed before any demo. See §8.
2. **Verification cost eating the value prop.** Lean on internal-trust baseline: adaptive replication + challenge tasks, not blanket replication.
3. **Unobtrusiveness / instant yield.** Idle gate must check **CPU+GPU** util, not just keyboard idle (a user can be away while a render runs). Beware the **session-0 idle-detection bug** (see architecture).
4. **Churn as the default.** Laptops sleep, users return — checkpoint + requeue, don't assume the happy path.
5. **Privacy leakage through intermediate state**, not just inputs (Petals shows activations leak to hosts). Scope which job classes are safe to schedule.
6. **Transport silently failing on demo day** (gRPC/WebSocket blocked by proxies). Validate plain-HTTPS long-poll on the real LAN early.
7. **Sybil / benchmark-inflation reward gaming** (io.net saw ~1.8M fake GPUs). Corp-SSO one-identity-per-node + validated-output-only metering mitigates.
8. **GPU-under-isolation gap** *(open question).* GPU-in-Windows-Sandbox is documented *not* to work — GPU jobs run host-side under a Job Object (see architecture §3.3, §13).

---

## 11. Related prior art — what to borrow

| System | What it is | One key lesson |
|---|---|---|
| [**Salad**](https://salad.com/security) | Commercial idle-consumer-PC GPU cloud; Docker-per-job, trust-rating routing, paid rewards | **Closest blueprint.** Copy container-per-job isolation, trust-rating cohort selection, bidirectional isolation, 24h job deletion, rewards redemption. |
| [**BOINC**](https://github.com/BOINC/boinc/wiki/AdaptiveReplication) | Open-source volunteer compute; replication/quorum, code signing | **Adaptive replication** is the verification north star — internal machines start high-trust → mostly unreplicated + spot-checked. |
| [**Folding@home**](https://foldingathome.org/faqs/points/bonus-points/what-are-the-qualifications-for-the-qrb/) | Volunteer sim; benchmark-normalized credit + bonuses | **Anti-gaming template:** passkey + reliability gates; normalize pay across heterogeneous hardware. |
| [**Petals**](https://github.com/bigscience-workshop/petals) | BitTorrent-style sharded LLM inference | Proves churn-tolerant large-model inference works **and** that the safe config is a **private swarm** — which an internal fleet *is*. |
| [**Akash / Golem / Render**](https://akash.network/docs/getting-started/intro-to-akash/bids-and-leases/) | Decentralized GPU marketplaces; manifest→match→lease→score | Clean scheduler pattern — but **drop the blockchain/auction**; use a central scheduler + internal points ledger. |
| [**iExec**](https://docs.iex.ec/protocol/tee/intel-sgx) | Confidential compute (Docker-in-SGX) + verifiable results | The credible **roadmap** answer for truly sensitive workloads — not the PoC. |
| [**HTCondor**](https://en.wikipedia.org/wiki/HTCondor) | Idle-cycle scavenger; keyboard-idle gating, instant eviction | **Unobtrusiveness + isolation, not throughput, is the adoption blocker.** This is the make-or-break demo moment. |
| [**SETI@home**](https://en.wikipedia.org/wiki/SETI@home) | Pioneering volunteer project (1999–2020) | **Cautionary tale:** weak replication → cheating tax. Internal trust is what lets NightShift avoid it. |

---

*This is the framed concept, validated by a deep-research pass. The proposed system design — stack choices, worker protocol, idle detection, sandbox model — lives in [`architecture.md`](./architecture.md).*

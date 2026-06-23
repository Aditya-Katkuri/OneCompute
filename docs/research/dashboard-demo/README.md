# NightShift T5 research dossier: dashboard, workloads, and honest demo framing

## 1. How to use this (for the team)

Use this as the implementation filter for T5: build the dashboard and demo beats around **measured useful work**, not nameplate compute. Start with the ranked areas below, then use the companion notes for exact dashboard/workload/pitch choices:

- [Measurement and throughput](measurement-and-throughput.md)
- [Hardware and economics](hardware-and-economics.md)
- [Dashboard and demo patterns](dashboard-and-demo-patterns.md)
- [Honest pitch and pitfalls](honest-pitch-and-pitfalls.md)

## 2. Executive summary — 5 highest-impact learning areas, ranked

1. **Measured throughput beats TOPS.** TOPS is a peak marketing ceiling; real work is bounded by precision, memory bandwidth, thermal limits, foreground contention, and software stack efficiency. **Feature decision:** show a measured live throughput bar and a separate ghost-bar baseline; keep `benchmarked_tops` as a disclosed display/calibration value only [1][2][3].
2. **Embarrassingly parallel workloads are the demo sweet spot.** Independent prompt slices and CPU chunks map cleanly onto heterogeneous PCs and avoid fragile cross-machine model sharding. **Feature decision:** ship `data.transform` fan-out first, AI SDK prompt-slice second, and roadmap model sharding [4][5][6].
3. **Dashboard reliability matters more than transport cleverness.** Streamlit fragments and Gradio timers provide built-in periodic reruns; hand-rolled SSE/WebSocket adds failure modes the hackathon does not need. **Feature decision:** poll `GET /state` every ~500 ms in Streamlit/Gradio or static HTML [7][8].
4. **Economics must be framed as displaced batch capacity, not free money.** Azure H100 on-demand retail in East US is $6.98/hr Linux and $8.82/hr Windows for `Standard_NC40ads_H100_v5`; OpenAI and Anthropic batch APIs advertise 50% discounts for asynchronous jobs. **Feature decision:** pitch NightShift for delay-tolerant internal eval/batch work and compare only against work that would really have run in cloud [9][10][11][12].
5. **Trust/isolation beats must be visual and honest.** Windows Sandbox can disable networking and delete state on close, Job Objects manage/terminate process groups, and `GetLastInputInfo` is session-specific for idle detection. **Feature decision:** demo denied file access/wipe, sub-second yield, and one challenge-task cheater; disclose GPU host-side/Job Object fallback as PoC scope [13][14][15][16].

## 3. Compute <-> hardware <-> software interconnection map

| Layer | What matters | NightShift demo implication |
|---|---|---|
| Compute math | Peak ops only matters if precision, arithmetic intensity, and data movement match the workload; Roofline bounds attainable FLOP/s by `min(peak, operational_intensity × memory_bandwidth)` [1]. | Do not convert nameplate TOPS into demo throughput. Measure units/sec and wall-clock speedup for the actual `data.transform`/AI batch slices. |
| Hardware | Copilot+ PCs require NPUs above 40 TOPS, Snapdragon X-class, AMD Ryzen AI 300, and Intel Core Ultra 200V are the edge-AI trend, and RTX 50 laptop GPUs range much higher in AI TOPS [17][18][19][20][21]. | Use the 1.8-ExaOPS line only as a future Copilot+ NPU ceiling; the PoC harvests CPU/GPU and reports a separate measured number. |
| Software runtime | MLPerf measures trained-model inference with standardized scenarios/quality targets; Nsight Compute exposes GPU occupancy, stalls, memory, and scheduling details [2][3]. | For PoC, measure simple accepted units/sec. Roadmap: add calibration jobs per device and track tokens/sec, hashes/sec, joules/job, and p95 yield latency. |
| Scheduler/contracts | T5 consumes `GET /state`, `POST /jobs`, fleet/ledger state, and worker/result status from frozen contracts. | The dashboard must render read-model facts: worker state, job progress, points, blacklist, yielded/requeued slices. No dashboard-only truth. |
| Security/control | Windows Sandbox `.wsb` controls networking, vGPU, mapped folders, logon command, and deletes contents on close; Docker `--network none` creates only loopback [13][22]. | CPU isolation beat can be real Sandbox or Docker. GPU isolation is roadmap unless hardware path is proven; never imply Job Objects are a security boundary. |
| Economics | Cloud batch economics reward delay tolerance: OpenAI Batch and Anthropic Message Batches advertise 50% lower cost; Azure Retail Prices API gives current VM retail meters [9][11][12]. | Pitch NightShift as internal spot/batch capacity for evals, synthetic data, embeddings, test fan-out, and render chunks—not latency-sensitive prod serving. |

## 4. Deep dives

### Area 1 — Measuring harvested compute honestly

**Compute:** Use wall-clock useful work per second, not claimed FLOPS/TOPS. Roofline explains why a kernel with low operational intensity can be bandwidth-bound even on a high-peak chip [1]. MLPerf reinforces that inference benchmarking needs defined models, quality targets, scenarios, and metrics rather than raw silicon numbers [2].

**Hardware:** NPUs, GPUs, and CPUs expose different peak numbers at different precisions. NVIDIA's tooling documents occupancy, warp stalls, registers, shared/global memory, and Tensor Cores as sources of realized performance variance [3].

**Software:** T5 should compute `measured_units_per_sec = accepted_units / elapsed_seconds` for each job and show p50/p95 slice latency, active workers, yielded slices, and rejected/blacklisted units. Calibration may be a 5-10 second warmup per worker; do not use calibration as ledger credit unless work is validated.

**Feature/decision:** A live bar labelled **Measured harvested throughput** and a grey **1-machine ghost baseline** is stronger than a fake ExaOPS number.

### Area 2 — Edge-AI hardware trend without overclaiming

**Compute:** Microsoft defines Copilot+ PCs as Windows PCs with an NPU that can perform more than 40 TOPS [17]. Microsoft launched the category around CPU+GPU+NPU system architecture rather than NPU alone [18].

**Hardware:** AMD says Ryzen AI 300 delivers up to 50 NPU TOPS; Intel says Core Ultra 200V has up to 120 platform TOPS and a much stronger fourth-generation NPU; NVIDIA lists RTX 50 laptop GPUs from 440 to 1,824 AI TOPS; DGX Station for Windows advertises up to 20 PFLOPS FP4 and 748 GB coherent memory [19][20][21][23]. NVIDIA RTX Spark is a desk/laptop trend signal: 1 PFLOP AI compute and up to 128 GB unified memory [24].

**Software:** Windows AI guidance points developers to Windows ML for programmatic NPU/GPU AI acceleration [17]. NightShift should keep NPU harvesting as roadmap because the PoC contracts and demo path are CPU/GPU + SDK.

**Feature/decision:** Use one slide: **Ceiling: 40,000 Copilot+ PCs × 45 TOPS ≈ 1.8 ExaOPS peak INT8 NPU** [6][30]; **Today: measured live CPU/GPU/SDK throughput from our demo fleet**.

### Area 3 — Parallel-workload economics

**Compute:** NightShift is valuable for jobs that split into independent chunks: prompt scoring, evals, embeddings, synthetic data, build/test fan-out, transforms, rendering, and parameter sweeps. Batch APIs exist because delayed/asynchronous processing can trade latency for lower cost and higher throughput [11][12].

**Hardware:** Azure NCads H100 v5 is positioned for batch inferencing and has 1-2 NVIDIA H100 NVL GPUs with 94 GB memory each [10]. The Retail Prices API query for East US returned `Standard_NC40ads_H100_v5` at $6.98/hr Linux and $8.82/hr Windows on-demand, with lower spot/low-priority meters also present [9].

**Software:** Rewards should credit accepted useful units, not agent-claimed TOPS. Batch jobs should include `units`, `class_weight`, and elapsed time in the dashboard, with a note that cloud displacement only counts when the workload would otherwise have run on paid infrastructure.

**Feature/decision:** Demo ROI as **measured accepted units × comparable cloud unit cost**, not as theoretical idle-device peak value.

### Area 4 — Real-time dashboard patterns for a hackathon

**Compute:** Judges need to see concurrency, not read it: worker tiles, moving slices, points ticking, yielded/requeued work, and one cheater going to zero.

**Hardware:** The dashboard must distinguish CPU-only and GPU-capable workers because class weight and capabilities differ; GPU telemetry must be guarded because `pynvml`/NVIDIA driver absence is expected on some PCs.

**Software:** Streamlit fragments can rerun independently at `run_every`; Gradio `Timer` ticks at a configured interval and triggers event handlers [7][8]. For 2-3 workers, 500 ms polling of `GET /state` is lower-risk than custom push infrastructure.

**Feature/decision:** Build seeded-data dashboard first, then replace seed with `GET /state`. Keep the refresh loop dumb and visible; save WebSocket/SSE for roadmap only.

### Area 5 — Honest trust, isolation, and demo risk framing

**Compute:** Challenge/ringer tasks with known answers catch dishonest workers cheaply; failed challenge means blacklist and points forfeited, not a vague trust score [29].

**Hardware:** `GetLastInputInfo` is session-specific, so the worker must run in the interactive user session to avoid an always-idle service bug [15]. Job Objects manage groups of processes, enforce limits, and can terminate the group [14].

**Software:** Windows Sandbox supports `.wsb` controls for networking, mapped folders, logon command, vGPU, and close-time deletion; Docker `--network none` creates only loopback [13][22]. CISA warns sustained abnormal CPU activity is a cryptojacking signal, so production framing must include signing, allow-listing, deployment, and audit rather than bypassing endpoint controls [25].

**Feature/decision:** In the demo script, say: **CPU job isolation is real; GPU PoC uses host-side Job Object for control, not a confidential security boundary; GPU TEE/stronger isolation is roadmap**.

## 5. Direct implications for OUR implementation

### PoC must-have

- **`GET /state` read model:** dashboard polls every ~500 ms and renders only orchestrator/ledger truth: workers, job progress, accepted units, yielded slices, blacklist, points.
- **Ghost bar:** pre-measure or disclose a single-worker baseline (e.g., 90 seconds) and compare live fan-out elapsed time against it.
- **CPU fan-out job:** `data.transform` first because it is deterministic, chunkable, easy to requeue, and easy to verify.
- **AI SDK job:** prompt-slice via OpenAI/Anthropic SDK second; if it falls back to token-proportional sleep, label it as simulated latency while preserving real fan-out/requeue semantics.
- **Cheater beat:** one hidden `challenge` answer is deterministic; wrong answer flips tile to blacklisted and forfeits points.
- **Isolation beat:** show real Sandbox or Docker denied access/no network/wipe. If using Docker, say Docker fallback instead of Sandbox.
- **Close slide:** show `theoretical_peak_npu_ceiling` and `measured_demo_throughput` in separate boxes.

### Roadmap only

- NPU harvesting through Windows ML/ONNX Runtime/DirectML/QNN paths.
- Cross-machine model sharding.
- TEE/confidential GPU execution.
- Adaptive replication and full fuzzy comparators.
- WebSocket/SSE production dashboard.
- Full economic marketplace/pricing engine.

## 6. Pitfalls & open questions

- **TOPS trap:** never say the PoC delivered ExaOPS; it delivered measured units/sec on CPU/GPU/SDK slices.
- **Precision trap:** INT8/FP4/FP16 peaks are not interchangeable; cite precision beside any peak number.
- **Warmup trap:** model/API cold starts can sink the demo; pre-stage AI and run CPU fan-out as the primary throughput beat.
- **Fallback sleep:** acceptable only if SDK fails, only for the AI secondary beat, and must be disclosed as token-proportional simulated latency.
- **Dashboard truth trap:** do not animate impossible data; seeded data is a scaffold only.
- **Isolation trap:** Job Objects are governance/kill/control, not a sandbox boundary.
- **Enterprise-security trap:** unmanaged hackathon machines are fine for demo; production must pass Defender/Intune/Purview controls.
- **Open question:** what exact unit should the final demo report? Recommendation: `accepted data items/sec` for CPU job plus `AI prompts/minute` for SDK job, not one blended TOPS metric.

## 7. Sources

[1] Williams, Waterman, Patterson, **Roofline: An Insightful Visual Performance Model...** UC Berkeley tech report. https://www2.eecs.berkeley.edu/Pubs/TechRpts/2008/EECS-2008-134.html
[2] MLCommons, **MLPerf Inference: Datacenter**. https://mlcommons.org/benchmarks/inference-datacenter/
[3] NVIDIA, **Nsight Compute Profiling Guide**. https://docs.nvidia.com/nsight-compute/ProfilingGuide/index.html
[4] NightShift contracts, `GET /state`, job/result/worker seams. `docs/contracts.md`
[5] NightShift architecture §13, minimal demo and cut-list. `docs/architecture.md`
[6] NightShift idea §6/§9, workload/demo narrative. `docs/idea.md`
[7] Streamlit, `st.fragment` API, `run_every`. https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment
[8] Gradio, `Timer` component. https://gradio.app/api/markdown/timer
[9] Azure Retail Prices API result for East US `Standard_NC40ads_H100_v5`. https://prices.azure.com/api/retail/prices?$filter=serviceName%20eq%20%27Virtual%20Machines%27%20and%20armSkuName%20eq%20%27Standard_NC40ads_H100_v5%27%20and%20armRegionName%20eq%20%27eastus%27%20and%20priceType%20eq%20%27Consumption%27
[10] Microsoft Learn, **NCads_H100_v5 size series**. https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncadsh100v5-series
[11] OpenAI, **Batch API**. https://developers.openai.com/api/docs/guides/batch
[12] Anthropic, **Batch processing / Message Batches API**. https://platform.claude.com/docs/en/build-with-claude/batch-processing
[13] Microsoft Learn, **Use and configure Windows Sandbox**. https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file
[14] Microsoft Learn, **Job Objects**. https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
[15] Microsoft Learn, **GetLastInputInfo**. https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo
[16] Microsoft Windows Sandbox issue #42, GPU/CUDA caveat. https://github.com/microsoft/Windows-Sandbox/issues/42
[17] Microsoft Learn, **Copilot+ PCs developer guide**. https://learn.microsoft.com/en-us/windows/ai/npu-devices/
[18] Microsoft Official Blog, **Introducing Copilot+ PCs**. https://blogs.microsoft.com/blog/2024/05/20/introducing-copilot-pcs/
[19] AMD, **Ryzen AI 300 official press release**. https://www.amd.com/en/newsroom/press-releases/2024-6-2-amd-unveils-next-gen-zen-5-ryzen-processors-to-p.html
[20] Intel, **Core Ultra 200V press release**. https://www.intc.com/news-events/press-releases/detail/1707/new-core-ultra-processors-deliver-breakthrough-performance
[21] NVIDIA, **Compare GeForce RTX laptops**. https://www.nvidia.com/en-us/geforce/laptops/compare/
[22] Docker Docs, **None network driver**. https://docs.docker.com/engine/network/drivers/none/
[23] NVIDIA, **DGX Station for Windows**. https://nvidianews.nvidia.com/news/nvidia-dgx-station-for-windows-puts-a-trillion-parameter-ai-supercomputer-on-every-enterprise-desk
[24] NVIDIA, **RTX Spark Windows PCs for agents**. https://nvidianews.nvidia.com/news/nvidia-microsoft-windows-pcs-agents-rtx-spark
[25] CISA, **Defending Against Illicit Cryptocurrency Mining Activity**. https://www.cisa.gov/news-events/news/defending-against-illicit-cryptocurrency-mining-activity
[26] Microsoft Learn, **Azure Retail Prices REST API overview**. https://learn.microsoft.com/en-us/rest/api/cost-management/retail-prices/azure-retail-prices
[27] Microsoft Learn, **Purview Endpoint DLP**. https://learn.microsoft.com/en-us/purview/endpoint-dlp-learn-about
[28] Windows Blog, **Copilot+ PCs expand with AMD and Intel silicon**. https://blogs.windows.com/windowsexperience/2024/09/03/copilot-pcs-expand-availability-with-new-amd-and-intel-silicon/
[29] Golle and Mironov, Microsoft Research, **Uncheatable Distributed Computations**. https://www.microsoft.com/en-us/research/wp-content/uploads/2001/04/dist.pdf
[30] Qualcomm, **Snapdragon X Elite product page**. https://www.qualcomm.com/laptops/products/snapdragon-x-elite


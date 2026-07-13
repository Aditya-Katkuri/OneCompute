# OneCompute + Azure AI Foundry Integration Plan

## Vision

OneCompute extends Azure AI Foundry with a distributed compute layer built from idle enterprise hardware. Foundry remains the control plane for model management, governance, evaluations, and orchestration. OneCompute provides additional execution capacity for eligible workloads on trusted corporate devices.

## Phase 0: Measurement pilot and co-development (recommended start)

Before any Azure workload is routed onto employee devices, the safest and fastest way to build the case and earn approval is to measure first and co-develop the routing with the teams that own the risk.

**Co-development partners.** The routing path from Azure into the device pool should be built jointly with:
- **Azure Compute** for the functionality: how Foundry and Azure batch/eval requests are safely diverted to the OneCompute Router, capacity accounting, and fallback to Azure.
- **The CISO office** (enterprise security and Azure Security) for the safety: the trust boundary, data-sensitivity gating, isolation guarantees, and audit for work that leaves Azure and runs on managed endpoints.

**One-week voluntary measurement pilot (no job execution).** In parallel, run a short, opt-in measurement-only pilot inside one organization that **only tracks CPU, GPU, and RAM usage** across employee laptops, dev boxes, and Xboxes. It pulls and runs **no jobs**; it simply learns each machine's real idle-headroom envelope on-device and streams live utilization to the dashboard. This is the lowest-risk possible first step: pure read-only telemetry, instantly reversible, easy for security and privacy to approve.

**Why this order.** The measurement pilot replaces the estimates in `Financial_Impact.md` with **measured** recoverable headroom across real device classes, proves the harvest can stay conservative (see the harvest-intensity section there), and gives Azure Compute and the CISO office concrete data to design safe routing against, all before a single Azure request is diverted onto a device. The worker ships a first-class measurement mode for exactly this pilot (`python -m worker --url <host> --measure-only`).

## Phase 1: Foundry Job Integration

**Goal:** Allow Azure AI Foundry workloads to run on OneCompute.

**Flow**

1. User launches a Foundry evaluation, batch inference, or agent workflow.
2. Foundry sends the job to the OneCompute Router.
3. Router checks:
   - Model requirements
   - Data sensitivity
   - Device availability
   - Latency requirements
4. Jobs are split into small work units.
5. Idle devices execute the work in a sandbox.
6. Results are returned to Foundry.

**Deliverables**

- Foundry connector
- Job router
- Device registry
- Result aggregation service

## Phase 2: Local Model Execution

**Goal:** Run approved Foundry models directly on employee devices.

**Components**

- Foundry model catalog
- Model packaging service
- OneCompute cache
- Windows ML runtime
- CPU, GPU, and NPU scheduling

Devices pull approved model artifacts and execute inference locally while remaining fully managed and policy compliant.

## Phase 3: Intelligent Routing

**Goal:** Automatically determine whether work should execute in Azure or on OneCompute.

| Workload | Destination |
|---|---|
| Interactive chat | Azure |
| Copilot workloads | Azure |
| Agent evaluation | OneCompute |
| Batch inference | OneCompute |
| Synthetic data generation | OneCompute |
| Testing and red teaming | OneCompute |

**Benefits**

- Lower Azure consumption
- Reduced GPU pressure
- Better hardware utilization
- Additional inference capacity

## Phase 4: Enterprise Security

**Components**

- Intune deployment
- Entra ID identity
- Defender integration
- Purview enforcement
- Signed workloads
- Sandbox execution
- Audit logging

All execution occurs on managed, trusted devices with fully auditable workloads.

## End State

Azure AI Foundry exposes:

```
compute: azure
compute: spot
compute: onecompute
compute: auto
```

Foundry automatically selects the most efficient execution environment while preserving governance, compliance, and developer workflows.

## Repo Doc

### OneCompute + Azure AI Foundry

OneCompute integrates with Azure AI Foundry to provide a distributed execution layer powered by idle enterprise hardware. Instead of sending every workload to Azure GPUs, Foundry can route eligible batch inference, evaluation, and agent workloads to a trusted pool of managed laptops, dev boxes, workstations, and other corporate devices.

Azure AI Foundry remains responsible for model management, governance, security, and orchestration. OneCompute provides elastic compute capacity from hardware Microsoft already owns, increasing utilization while reducing infrastructure demand.

**Capabilities**

- Foundry job integration
- Distributed AI inference
- Windows ML and NPU support
- Entra ID trust model
- Intune deployment
- Defender and Purview integration
- Automatic Azure fallback

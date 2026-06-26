# OneCompute + Azure AI Foundry Integration Plan

## Vision

OneCompute extends Azure AI Foundry with a distributed compute layer built from idle enterprise hardware. Foundry remains the control plane for model management, governance, evaluations, and orchestration. OneCompute provides additional execution capacity for eligible workloads on trusted corporate devices.

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

## README Executive Overview

### OneCompute + Azure AI Foundry

OneCompute turns idle enterprise devices into a distributed compute tier for Azure AI Foundry. By securely routing batch inference, evaluations, and other AI workloads to managed laptops, dev boxes, and workstations, OneCompute expands available AI capacity using hardware Microsoft already owns. Azure AI Foundry continues to provide model governance, orchestration, and developer tooling, while OneCompute supplies an elastic execution layer that improves utilization, reduces infrastructure costs, and unlocks additional compute at enterprise scale.

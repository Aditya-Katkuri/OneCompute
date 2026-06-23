# GPU isolation and confidential compute

## Why CUDA-in-Sandbox is not the PoC

Windows GPU-PV exposes a paravirtualized WDDM model using a Virtual Render Driver and host-side driver cooperation [15]. Microsoft Windows container GPU acceleration supports DirectX and frameworks built on DirectX only; third-party frameworks are not supported, and Hyper-V-isolated GPU acceleration is not currently supported [17]. Microsoft Windows-Sandbox issue #42 reports that `VGpu=Enable` leaves CUDA NVIDIA DLLs absent inside Sandbox and is closed `not_planned` [18].

## GPU isolation technologies

- **GPU-PV:** shares GPU through WDDM/VRD; useful for graphics/DirectX but not a blanket CUDA pass-through [15].
- **Hyper-V GPU partitioning:** Windows Server 2025 can partition GPUs to VMs with SR-IOV/IOMMU and driver prerequisites [16].
- **NVIDIA vGPU:** hypervisor Virtual GPU Manager creates virtual GPUs with fixed framebuffer; vGPUs are time-sliced or MIG-backed [19].
- **MIG:** supported datacenter GPUs can be partitioned into instances with dedicated compute and memory resources [20].
- **H100 Confidential Computing:** Hopper H100 provides GPU TEE features including hardware root of trust, attestation, encrypted CPU-GPU paths, hardware firewalls, and memory scrub [21].

## NightShift plan

PoC: host-side GPU process under Job Object, visible CUDA, real `pynvml`, and explicit residual-risk disclosure. Roadmap: confidential CPU VM plus H100-class GPU CC for sensitive workloads; vGPU/MIG for multi-tenant datacenter scheduling; no claim that consumer RTX or Copilot+ NPU provides job-data secrecy from the worker.

## Sources

Uses README sources [15], [16], [17], [18], [19], [20], [21], [22], [23], [27].

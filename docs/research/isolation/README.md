# NightShift Isolation Research Dossier

## 1. How to use this (for the team)

Use this dossier as T3's build filter. If a feature makes the **submit -> worker pulls -> isolated run -> result -> points tick -> mouse-touch instant yield** demo more reliable, it is PoC; if it requires new silicon, admin-heavy rollout, nested virtualization, or enterprise policy work, it is roadmap. The supporting deep dives are linked here:

- [CPU virtualization and Windows Sandbox](cpu-virtualization-and-sandbox.md)
- [Container isolation spectrum](container-isolation-spectrum.md)
- [Windows boundaries and Job Objects](windows-boundaries-and-job-objects.md)
- [GPU isolation and confidential compute](gpu-isolation-and-confidential-compute.md)

## 2. Executive summary -- 5 highest-impact learning areas, ranked

1. **Separate security boundaries from resource governance.** Windows Sandbox/Hyper-V or Docker provide the job boundary; Job Objects provide caps and kill-on-close, not confidentiality. This is highest-impact because it prevents us from overselling host protection and directly informs `run_in_isolation()` policy layering plus the T2 yield seam [1][3][4][10].
2. **Default to Docker-per-job, spike Windows Sandbox only when the SKU is known-good.** Windows Sandbox is a disposable Hyper-V VM with strong demo value, but it needs supported Pro/Enterprise/Education editions, cannot run multiple instances, and has admin/feature/reboot friction; Docker's Linux-container path is the lower-risk demo floor [1][2][5].
3. **GPU jobs must be host-side for the PoC.** Windows Sandbox vGPU exposes a Microsoft Virtual Render Driver-style path, not a CUDA-ready device; Microsoft issue #42 reports missing NVIDIA CUDA DLLs and is closed `not_planned`. Windows containers officially accelerate DirectX only and not Hyper-V-isolated GPU workloads, so real CUDA belongs host-side under a Job Object [15][17][18].
4. **Know the container isolation spectrum: namespaces -> user-space kernel -> microVM.** Native containers are fast and demoable but share a kernel; gVisor interposes a user-space application kernel; Kata uses VM isolation. This informs NightShift's roadmap language and keeps the PoC on the Docker path without claiming microVM-grade protection [5][6][7][8].
5. **Confidential compute is the answer to job-data secrecy from the worker, but not on demo PCs.** Intel TDX/AMD SEV-SNP protect VM memory from host software; NVIDIA H100-class GPUs add GPU TEEs and attestation. Consumer RTX GPUs and Copilot+ NPUs do not give NightShift a practical GPU TEE today, so use data minimization plus verification now and roadmap TEEs for sensitive workloads [21][22][23][27].

## 3. Compute <-> hardware <-> software interconnection map for isolation

| Layer | CPU path | GPU path | NightShift meaning |
|---|---|---|---|
| Hardware primitives | CPU virtualization, IOMMU/DMA isolation, Secure Boot, TPM, VBS-capable hardware [3][16][29] | WDDM GPU-PV/VRD, SR-IOV/IOMMU, NVIDIA vGPU, MIG partitions, H100 CC mode [15][16][19][20][21] | Hardware defines which isolation modes are real vs only policy. |
| Hypervisor boundary | Hyper-V is a type-1 hypervisor; Windows Sandbox uses Microsoft hypervisor kernel isolation and disposable state [1][3] | GPU-PV marshals guest graphics/compute objects across VM boundary to host driver; secure VM mode requires IOMMU isolation [15] | Good for CPU isolation; GPU support is driver/API-specific. |
| Container boundary | Docker namespaces isolate processes/network; cgroups limit resources but do not protect data/processes by themselves [5] | Windows containers support DirectX GPU acceleration only; Hyper-V-isolated GPU acceleration is not supported [17] | Docker is PoC default for CPU-like workloads; do not promise CUDA isolation. |
| Intermediate hardening | gVisor intercepts syscalls with a Go user-space kernel; Kata gives container UX with VM isolation [6][7][8] | NVIDIA vGPU time-slices or assigns MIG-backed virtual devices; MIG gives dedicated compute/memory resources on supported datacenter GPUs [19][20] | Roadmap tiers if NightShift needs stronger multi-tenant isolation. |
| Runtime governance | Job Objects manage a process group, enforce CPU/memory/time limits, and terminate associated processes [10][11][12] | Same host process wrapper can kill CUDA job process tree, but it does not isolate GPU memory from a malicious worker [10][21] | Required for instant yield, not a security boundary. |
| Confidentiality of job data from worker | TDX/SEV-SNP confidential VMs and VBS enclaves protect data-in-use in specific scenarios [23][26][28] | H100 CC creates a GPU TEE with secure/measured boot, attestation, encrypted CPU-GPU paths, and memory scrubbing [21][22] | Roadmap only; PoC uses data minimization and challenge/replication. |

## 4. Deep dives

### Area 1 -- Boundary vs governance

Microsoft defines security boundaries as separations between code/data domains with different trust levels; a servicing-worthy bug must violate a boundary or security feature intent [9]. Windows Sandbox is explicitly a hardware-virtualized, separate-kernel, disposable environment [1], while Job Objects are process-management objects for grouping, limiting, accounting, and terminating processes [10]. Docker's own security model similarly separates namespaces from cgroups: namespaces are isolation; cgroups are resource accounting/limiting and do not stop one container from accessing another's data [5].

**Compute/hardware/software connection:** CPU hardware virtualization makes Hyper-V/Windows Sandbox a boundary; OS kernel primitives and Job Objects regulate scheduling/memory/time after the process exists. For NightShift, `limits.cpu_pct`, `limits.mem_gb`, and `timeout_s` map to Job Object settings, while `sandbox.type`, mounts, and networking map to Docker/Sandbox creation.

**Feature/decision:** implement `run_in_isolation(job)` as a policy compiler: choose Docker/Sandbox/host-GPU path, then always wrap the launched process tree in a Job Object with kill-on-close. Never describe a Job Object alone as protecting the worker from arbitrary code.

### Area 2 -- Docker default, Windows Sandbox spike

Windows Sandbox's best demo attributes are exactly NightShift's isolation proof: it is disposable, pristine each launch, uses hardware virtualization for kernel isolation, and deletes software/files/state when closed [1]. `.wsb` files let us disable networking, map folders read-only, control vGPU, set memory, and run a LogonCommand [2]. However, Microsoft also documents important friction: it is unavailable on Windows Home, networking and clipboard are enabled by default unless disabled, multiple instances are not supported, and mapped writable folders can persist/affect the host [1][2].

Docker is weaker than a VM but more reliable for a 1-2 day PoC: `docker run --network none`, read-only mounts, and `--rm` minimize blast radius; Docker documents namespaces/cgroups plus risks from daemon control and host mounts, which is why our worker must not expose the Docker API or mount host roots [5].

**Feature/decision:** build Docker first as the reliable floor; timebox Windows Sandbox to one pre-warmed CPU isolation beat if the demo PC has Pro/Enterprise/Education, virtualization enabled, local admin/elevation, and no nested-virt blocker. Use `.wsb` with `Networking=Disable`, `ClipboardRedirection=Disable`, read-only `MappedFolders`, and a controlled `LogonCommand` [2].

### Area 3 -- GPU isolation reality

Windows GPU-PV is a paravirtualized WDDM stack: the guest has a Virtual Render Driver, guest `dxgkrnl` marshals work over VM bus, and the host performs rendering in `vmmem`/VM process contexts [15]. That architecture is not the same thing as exposing the host NVIDIA CUDA runtime. Microsoft Windows container docs state that GPU acceleration in Windows containers supports DirectX and frameworks built on it; third-party frameworks are not supported, and GPU acceleration for Hyper-V-isolated Windows or Linux containers is not currently supported [17]. The Windows-Sandbox issue #42 repro reports `VGpu=Enable` starts Sandbox but `C:\Windows\System32\nv*.dll` returns no CUDA libraries, and the issue is closed `not_planned` [18].

NVIDIA's production GPU isolation options are datacenter-class: vGPU uses a hypervisor-side Virtual GPU Manager, fixed framebuffer allocation, and either time-sliced vGPUs or MIG-backed vGPUs [19]. MIG partitions supported GPUs into isolated instances with dedicated compute and memory resources and guaranteed performance [20]. H100 confidential computing goes further with hardware root of trust, measured boot, attestation, encrypted CPU-GPU transfers, hardware firewalls, and memory scrubbing [21].

**Feature/decision:** CPU work can demonstrate Sandbox/Docker isolation. GPU PoC work should run host-side under a Job Object, using `pynvml` for detection/utilization and an explicit warning that GPU memory confidentiality is not solved in PoC. Roadmap the secure path as vGPU/MIG/H100-CC, not Windows Sandbox CUDA.

### Area 4 -- Container hardening spectrum

Native Docker is fast because the application still talks to the host kernel through namespaces, cgroups, capabilities, seccomp/AppArmor-like controls, and daemon policy; this preserves density but leaves kernel escape risk [5]. gVisor moves much of the Linux system API into a per-sandbox user-space application kernel (Sentry) and minimizes direct host syscalls; it explicitly trades compatibility and syscall-heavy performance for lower host-kernel exposure [6][7]. Kata Containers presents an OCI container interface but runs workloads in lightweight VMs, giving VM isolation with container workflows [8].

**Feature/decision:** for the hackathon, no gVisor/Kata implementation on managed Windows PCs unless already packaged by Docker Desktop/WSL in a way we can verify quickly. For roadmap, describe three SKUs: Docker default, gVisor-like syscall interposition for untrusted Linux jobs, and Kata/Hyper-V microVM for higher-sensitivity jobs.

### Area 5 -- TEEs and data-in-use roadmap

CPU TEEs shift trust from host software to hardware-backed attestation and encrypted memory. Intel TDX protects Trust Domain VMs with a TDX module, SEAM, secure EPT, TME-MK, and remote attestation [23]. AMD SEV-SNP adds VM memory encryption plus integrity protection and attestation; use it as the AMD confidential-VM counterpart in roadmap language [26]. Microsoft VBS enclaves are software-based TEEs inside a host application and can isolate sensitive code/data from the host app and rest of system on recent Windows builds [28].

GPU TEEs are newer and narrower. NVIDIA says H100 is the first GPU to introduce confidential computing, with CC-On mode, hardware firewalls, disabled performance counters, SPDM sessions to drivers in CPU TEEs, attestation reports, encrypted bounce buffers, signed/encrypted command buffers, and GPU memory scrubbing [21]. NVIDIA's confidential-containers supported-platforms doc points to Hopper H100-class GPUs and requires the node's GPUs to be configured for confidential computing and assigned to one confidential-container VM [22]. Copilot+ PC NPUs are exposed to developers as Windows AI/Windows ML acceleration devices above a 40 TOPS floor, but the Microsoft developer guide does not present them as TEEs or attested confidential devices [27].

**Feature/decision:** PoC protects job data from workers through minimization, shard sizing, signed manifests, no persistence, challenge tasks, and audit. Roadmap sensitive workloads to confidential CPU VM + H100-class GPU CC, not consumer RTX or Copilot+ NPU.

## 5. Direct implications for OUR implementation

- **`run_in_isolation(job) -> result`:** make this the single seam T2 calls. It should select `docker`, `windows_sandbox`, or `host_gpu` from the manifest policy and always return normalized stdout/stderr/result/error metadata.
- **Docker default:** create one container per job with `--rm`, `--network none` unless the manifest explicitly permits network, read-only input mount, separate output mount, memory/CPU caps, non-root user where the image supports it, and no Docker socket/host-root mounts [5].
- **Windows Sandbox spike:** implement `.wsb` generation only under `docs/architecture.md`'s timebox assumptions: one pre-warmed CPU job, networking off, clipboard off, read-only mapped input, mapped output only if necessary, and LogonCommand for the runner [1][2].
- **Job Object kill-on-close:** create and retain a Job Object handle for every launched host process tree. Set `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` so T2's yield can close the handle and kill the tree; set CPU rate with `CpuRate = percent * 100`, memory/process/time limits where applicable [10][11][12].
- **GPU host-side:** route `requires.needs_gpu=true` to host execution under a Job Object. Use `pynvml` detection guarded by try/except and report `has_gpu=false` on driver absence. Do not attempt CUDA inside Windows Sandbox [17][18].
- **Privacy posture:** for PoC, worker protection comes from Docker/Sandbox plus no-persistence; job-data protection comes from minimization, signed manifests, result verification, and no persistence. TEEs are roadmap [21][23][26].
- **Enterprise controls:** do not bypass Defender/Intune/Purview. Sustained CPU/GPU bursts resemble cryptojacking; CISA recommends monitoring abnormal sustained CPU use, and Purview Endpoint DLP can monitor/protect sensitive file activity on onboarded endpoints [30][31].

## 6. Pitfalls & open questions

- **GPU-in-Sandbox:** not demo-safe for CUDA; issue #42 is closed `not_planned` and Windows container GPU acceleration excludes third-party GPU frameworks and Hyper-V-isolated containers [17][18].
- **Nested virtualization:** Sandbox/Hyper-V paths can fail inside VMs or on devices where virtualization is disabled/owned by policy. Decide on a physical demo PC early [1][3].
- **Admin and Intune constraints:** enabling Windows Sandbox/Hyper-V or GPU partitioning requires rights, supported editions, drivers, BIOS/IOMMU/SR-IOV, and sometimes reboot/policy approval [1][16].
- **Docker daemon control:** access to the Docker daemon is effectively privileged because host mounts can alter the host filesystem; never expose daemon control to job code [5].
- **Mapped folder persistence:** Windows Sandbox is disposable, but writable mapped folders persist on the host and can be compromised by sandboxed apps [2].
- **AppContainer temptation:** AppContainer/Win32 App Isolation is valuable but requires packaging/capability work; it is a Windows app security boundary, not a quick generic arbitrary-job runner [13][14].
- **Side channels:** gVisor notes hardware side channels can affect native, sandboxed, or virtualized code; sandboxing is not a substitute for secure architecture [7].
- **Confidential compute availability:** H100-class GPU CC requires specific hardware/software stacks; current NightShift demo hardware should assume no GPU TEE [21][22].
- **No-persistence vs outputs:** any mapped output channel is a deliberate declassification path. Hash outputs, constrain file names/sizes, and clean staging folders after upload.

## 7. Sources

[1] Windows Sandbox overview -- https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/

[2] Windows Sandbox `.wsb` configuration -- https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file

[3] Hyper-V overview -- https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/overview

[4] Windows containers isolation modes -- https://learn.microsoft.com/en-us/virtualization/windowscontainers/manage-containers/hyperv-container

[5] Docker Engine security -- https://docs.docker.com/engine/security/

[6] gVisor overview -- https://gvisor.dev/docs/

[7] gVisor security architecture -- https://gvisor.dev/docs/architecture_guide/security/

[8] Kata Containers FAQ/learn -- https://katacontainers.io/learn/

[9] Microsoft Windows security servicing criteria -- https://www.microsoft.com/en-us/msrc/windows-security-servicing-criteria

[10] Windows Job Objects -- https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects

[11] `JOBOBJECT_BASIC_LIMIT_INFORMATION` -- https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information

[12] `JOBOBJECT_CPU_RATE_CONTROL_INFORMATION` -- https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information

[13] Windows 11 Security Book: Application Isolation -- https://learn.microsoft.com/en-us/windows/security/book/application-security-application-isolation

[14] AppContainer isolation -- https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation

[15] Windows GPU paravirtualization -- https://learn.microsoft.com/en-us/windows-hardware/drivers/display/gpu-paravirtualization

[16] Hyper-V GPU partitioning -- https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/partition-assign-vm-gpu

[17] GPU acceleration in Windows containers -- https://learn.microsoft.com/en-us/virtualization/windowscontainers/deploy-containers/gpu-acceleration

[18] Microsoft Windows-Sandbox issue #42, `VGpu=Enable` breaks CUDA -- https://github.com/microsoft/Windows-Sandbox/issues/42

[19] NVIDIA vGPU User Guide -- https://docs.nvidia.com/vgpu/latest/grid-vgpu-user-guide/index.html

[20] NVIDIA MIG User Guide -- https://docs.nvidia.com/datacenter/tesla/mig-user-guide/latest/

[21] NVIDIA blog: Confidential Computing on H100 GPUs -- https://developer.nvidia.com/blog/confidential-computing-on-h100-gpus-for-secure-and-trustworthy-ai/

[22] NVIDIA Confidential Containers supported platforms -- https://docs.nvidia.com/datacenter/cloud-native/confidential-containers/latest/supported-platforms.html

[23] Intel TDX Enabling Guide: Introduction -- https://cc-enabling.trustedservices.intel.com/intel-tdx-enabling-guide/01/introduction/

[24] Intel TDX white paper -- https://www.intel.com/content/dam/develop/external/us/en/documents/tdx-whitepaper-final9-17.pdf

[25] Intel SGX overview -- https://www.intel.com/content/www/us/en/developer/tools/software-guard-extensions/overview.html

[26] AMD SEV-SNP technical resources -- https://www.amd.com/en/developer/sev.html and https://www.amd.com/content/dam/amd/en/documents/virtualization/amd-sev-snp-strengthening-vm-isolation.pdf

[27] Copilot+ PC NPU developer guide -- https://learn.microsoft.com/en-us/windows/ai/npu-devices/

[28] VBS Enclaves -- https://learn.microsoft.com/en-us/windows/win32/trusted-execution/vbs-enclaves

[29] Windows memory integrity / VBS -- https://learn.microsoft.com/en-us/windows/security/hardware-security/enable-virtualization-based-protection-of-code-integrity

[30] Microsoft Purview Endpoint DLP -- https://learn.microsoft.com/en-us/purview/endpoint-dlp-learn-about

[31] CISA: Defending against illicit cryptocurrency mining -- https://www.cisa.gov/news-events/news/defending-against-illicit-cryptocurrency-mining-activity

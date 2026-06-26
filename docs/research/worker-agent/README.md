# NightShift Worker Agent & Instant-Yield Research Dossier

## 1. How to use this

Use this as the T2 build brief. Start with the ranked learning areas, then use the linked deep dives when implementing `detect_capability()`, the idle gate, the runner `should_yield` loop, and the T3 Job Object seam. Citation numbers are shared across these files.

Deep dives:
- [Idle detection and instant yield](idle-preemption.md)
- [Resource governance on Windows](resource-governance.md)
- [Accelerators and hardware reality](accelerators-hardware.md)
- [Prior art and operating model](prior-art.md)

## 2. Executive summary - 5 highest-impact learning areas, ranked

1. **A session-aware presence state machine.** Combine `GetLastInputInfo`, WTS lock/unlock, AC/battery notifications, display/user-presence hints, and utilization caps; do not run as a session-0 service because `GetLastInputInfo` is session-specific [1]. **Feature:** foreground user-session idle gate: `idle AND on_ac AND unlocked AND cpu_ok AND gpu_ok`.
2. **Hard-yield plus idempotent requeue, not checkpoint/resume, for the PoC.** Windows Job Objects manage process trees and `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` terminates all associated processes when the last job handle closes [7][8]. **Feature:** mouse-touch -> close Job Object -> report `yielded` -> orchestrator requeues.
3. **Resource governance that is both polite and measurable.** Priority class, background mode, EcoQoS, and Job Object CPU caps control different layers: scheduler priority, I/O/memory/background priority, power-efficient core/frequency selection, and hard CPU-rate quotas [9][11][12][13]. **Feature:** default jobs run background/low/EcoQoS and under manifest CPU/memory limits.
4. **GPU/NPU truth over keyboard-idle fantasy.** NVML exposes GPU utilization, memory utilization, power usage, and P-state; keyboard idle does not imply a render/game/ML job is idle [16][17]. Windows ML/DirectML/QNN matter for roadmap NPU harvesting, but PoC should advertise NPU as labels only unless measured [18][19][20][21]. **Feature:** `pynvml` guarded capability + GPU-util gate.
5. **Physical idle is power/thermal/battery state, not just no input.** Windows S0 can be fully running while unused components enter lower power; Modern Standby is S0 low-power idle with tightly controlled activity bursts [22][23]. **Feature:** never on battery, thermal/fan-safe caps, and optional `powercfg`/SleepStudy evidence for the demo machine [24][25].

## 3. Compute <-> hardware <-> software interconnection map

```text
Human input / lock / power events
  -> Win32 session APIs (GetLastInputInfo, WTS, power GUIDs)
  -> worker idle state machine
  -> scheduler heartbeat + local should_yield flag
  -> runner checks between chunks
  -> T3 Job Object handle closes or TerminateJobObject runs
  -> process tree dies; job marked yielded; lease/requeue completes

Foreground app / GPU render / thermals / battery
  -> OS scheduler + power manager + GPU driver state
  -> metrics: CPU load, AC/DC, battery saver, NVML util/power/P-state
  -> admission decision: run, throttle, or yield
  -> resource controls: background mode, EcoQoS, CPU hard cap, memory cap
  -> hardware impact: fewer foreground stalls, lower fan noise, no battery drain
```

Key interconnection: NightShift should treat “idle” as a conservative admission-control decision, not as a single API result. Compute work changes hardware state: CPU threads prevent deeper idle states, GPU jobs raise P-state/power, and sustained heat triggers fans or throttling. Windows software controls can reduce impact but cannot make heavy compute invisible; therefore the worker must yield first, then optimize throughput.

## 4. Deep dives

### Area 1 - Session-aware idle gate

`GetLastInputInfo` is useful for input idle detection, but Microsoft explicitly says it returns input information only for the invoking session, not system-wide input across sessions [1]. WTS notifications provide event-driven session changes, including lock and unlock (`WTS_SESSION_LOCK`, `WTS_SESSION_UNLOCK`) [2][3]. Power notifications can be registered per GUID, and `GUID_ACDC_POWER_SOURCE` tells whether the system is on AC, battery, or short-term DC [4][5]; `GetSystemPowerStatus` gives a direct snapshot including AC/DC, battery life, and battery saver [6].

**NightShift decision:** run the detector in the interactive user session, maintain an in-memory state machine, and treat any unknown signal as “not idle.” A service may supervise later, but the PoC detector must not live only in session 0.

### Area 2 - Instant yield and requeue

Job Objects are the Windows primitive for managing process groups as a unit, including limits, accounting, and terminating all associated processes [7]. `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` causes all processes in the job to terminate when the last handle closes [8]; `TerminateJobObject` terminates all processes in a job and nested child jobs, and the processes cannot postpone or handle that termination [10].

**NightShift decision:** the demo money shot should be hard-kill + requeue. Checkpointing is roadmap unless a workload naturally writes tiny chunk outputs. HTCondor’s own self-checkpointing docs warn that checkpoint overhead and restart speed determine frequency, and suggest hour-scale checkpoints for many jobs [28]-wrong shape for a 0.3s UX demo.

### Area 3 - Polite resource governance

Windows schedules ready threads by priority queues and preempts lower-priority work when a higher-priority thread becomes ready [11][15]. `SetPriorityClass` has `IDLE_PRIORITY_CLASS` and background processing mode; Microsoft notes CPU priority alone is insufficient because background work can still disturb disk and memory, so background mode lowers broader resource scheduling priority [12]. Job Object CPU-rate control can hard-cap CPU cycles with `CpuRate = percent * 100`, and after a hard-capped job reaches its interval quota, associated threads do not run until the next interval [9]. EcoQoS tells Windows to prefer efficient frequency/cores and reduce thermal output/fan noise for non-performance-critical work [13][14].

**NightShift decision:** combine controls rather than picking one: background/idle priority for courtesy, EcoQoS for power/thermal, Job Object hard caps for contracts, and immediate yield for human return.

### Area 4 - Accelerator utilization and capability truth

NVML is NVIDIA’s C API for monitoring and managing GPU state and backs `nvidia-smi`; on Windows the NVML DLL may live under `NVIDIA Corporation\NVSMI` or `Windows\System32` and is not necessarily on PATH [16]. NVML exposes utilization, power usage, and performance state functions [17]. Windows ML is now the Windows-supported ONNX Runtime path for local AI inference across NPUs, GPUs, and CPUs, with execution providers managed by Windows [19]. DirectML remains supported but new ONNX Runtime deployment work moved to Windows ML [18]. Copilot+ PCs require a high-performance NPU above 40 TOPS, and Task Manager can display NPU usage [20]. QNN EP can offload to Qualcomm HTP/NPU and exposes performance/power modes, but it has package and platform constraints [21].

**NightShift decision:** PoC advertises measured GPU capability only; `pynvml` exceptions mean `has_gpu=false`. NPU harvesting is a roadmap deep dive, not a demo dependency.

### Area 5 - Hardware power reality

Windows S0 means the system is usable, while unused components may enter lower power states [22]. Modern Standby is still S0 low-power idle: the system wakes for controlled bursts and returns to idle when software and devices quiesce [23]. SleepStudy reports active time, idle time, power consumed, blockers, and top offenders for Modern Standby sessions [24]. `powercfg` can report available sleep states, power requests, energy problems, battery reports, and SleepStudy data [25].

**NightShift decision:** “never on battery” is not ethics polish; it is the line between harvested spare capacity and stealing user mobility. AC-only + battery saver off + caps + instant yield are the adoption story.

## 5. Direct implications for OUR implementation

### PoC decisions

- **Capability dict:** report `cpus`, `ram_gb`, `has_gpu`, `gpu_model`, `gpu_vram_gb`, `accel`, and labels. If NVML import/init/query fails, set `has_gpu=false`, `accel=[]`, and continue [16]. Never report nameplate NPU TOPS as scheduler capacity.
- **Idle gate:** implement `idle AND on_ac AND unlocked AND under_util_caps`; poll `GetLastInputInfo` around 250 ms and subscribe to WTS/power notifications when a message pump exists [1][2][4]. Unknown API errors fail closed.
- **Runner interface:** `runner(manifest, input, should_yield)` must check `should_yield()` between small chunks. For demo workloads, chunk size should keep worst-case yield latency under 250-500 ms before Job Object teardown.
- **Yield path:** on gate violation or heartbeat `preempt=true`, flip `should_yield`, close/terminate the T3 Job Object, report `status="yielded"`, and let T1 requeue. Do not wait for graceful app cleanup [8][10].
- **Resource caps:** create jobs with low/background priority, EcoQoS when available, and Job Object CPU/memory caps matching the manifest [9][12][13].

### Roadmap

- Service + per-session helper architecture: a service can coordinate, but idle sensing must run in each interactive session because session-local input is the source of truth [1].
- NPU harvesting via Windows ML/ONNX Runtime EP discovery; add measured benchmark fields only after a known model can run and utilization can be observed [19][20][21].
- True checkpoint/resume for long jobs that have explicit checkpoint files and measured checkpoint overhead [28][29].

## 6. Pitfalls & open questions

- **Session-0 bug:** `GetLastInputInfo` in a service/session 0 can make the machine appear idle forever because the API is session-specific [1]. PoC must run as foreground user-session process.
- **`pynvml` on no-GPU/no-driver:** NVML may be absent, not on PATH, or unsupported on non-NVIDIA machines; capability detection must catch every NVML failure and continue as CPU-only [16].
- **Keyboard idle != GPU idle:** a render, game, Teams effect, local model, or CUDA job can keep GPU busy while the keyboard is untouched; GPU admission needs utilization/power/P-state checks [17].
- **Hard-kill loses work:** acceptable for chunked PoC jobs and requeue; not acceptable for long monolithic workloads without self-checkpointing [28].
- **Priority is not isolation:** idle priority does not cap CPU, memory, disk, or GPU by itself; use Job Objects and sandbox/resource caps [7][9][12].
- **Thermal/fan unknowns:** Windows APIs do not give one universal “fan annoyance” metric. Use conservative CPU/GPU caps, AC-only, and optionally `powercfg /energy` or SleepStudy for demo validation [24][25].
- **NPU utilization API gap:** Task Manager can show NPU usage, but a stable worker-side utilization API is less obvious than NVML; roadmap needs ETW/perf-counter research before scheduling NPU jobs [20].

## 7. Sources

[1] GetLastInputInfo function - https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getlastinputinfo
[2] WTSRegisterSessionNotification function - https://learn.microsoft.com/en-us/windows/win32/api/wtsapi32/nf-wtsapi32-wtsregistersessionnotification
[3] WM_WTSSESSION_CHANGE message - https://learn.microsoft.com/en-us/windows/win32/termserv/wm-wtssession-change
[4] RegisterPowerSettingNotification function - https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerpowersettingnotification
[5] Power Setting GUIDs - https://learn.microsoft.com/en-us/windows/win32/power/power-setting-guids
[6] GetSystemPowerStatus function - https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-getsystempowerstatus
[7] Job Objects - https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
[8] JOBOBJECT_BASIC_LIMIT_INFORMATION - https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information
[9] JOBOBJECT_CPU_RATE_CONTROL_INFORMATION - https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-jobobject_cpu_rate_control_information
[10] TerminateJobObject function - https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-terminatejobobject
[11] Scheduling Priorities - https://learn.microsoft.com/en-us/windows/win32/procthread/scheduling-priorities
[12] SetPriorityClass function - https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-setpriorityclass
[13] Quality of Service - https://learn.microsoft.com/en-us/windows/win32/procthread/quality-of-service
[14] PROCESS_POWER_THROTTLING_STATE - https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/ns-processthreadsapi-process_power_throttling_state
[15] Context Switches - https://learn.microsoft.com/en-us/windows/win32/procthread/context-switches
[16] NVML API Reference - https://docs.nvidia.com/deploy/nvml-api/nvml-api-reference.html
[17] NVML device queries - https://docs.nvidia.com/deploy/nvml-api/group__nvmlDeviceQueries.html
[18] DirectML introduction - https://learn.microsoft.com/en-us/windows/ai/directml/dml
[19] Windows ML overview - https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/overview
[20] Copilot+ PCs developer guide / NPU devices - https://learn.microsoft.com/en-us/windows/ai/npu-devices/
[21] ONNX Runtime QNN Execution Provider - https://onnxruntime.ai/docs/execution-providers/QNN-ExecutionProvider.html
[22] System power states - https://learn.microsoft.com/en-us/windows/win32/power/system-power-states
[23] Modern Standby - https://learn.microsoft.com/en-us/windows-hardware/design/device-experiences/modern-standby
[24] Modern Standby SleepStudy - https://learn.microsoft.com/en-us/windows-hardware/design/device-experiences/modern-standby-sleepstudy
[25] powercfg command-line options - https://learn.microsoft.com/en-us/windows-hardware/design/device-experiences/powercfg-command-line-options
[26] HTCondor execution point policy - https://htcondor.readthedocs.io/en/latest/admin-manual/ep-policy-configuration.html
[27] HTCondor machine ClassAd attributes - https://htcondor.readthedocs.io/en/latest/classad-attributes/machine-classad-attributes.html
[28] HTCondor self-checkpointing applications - https://htcondor.readthedocs.io/en/latest/users-manual/self-checkpointing-applications.html
[29] HTCondor file transfer and eviction outputs - https://htcondor.readthedocs.io/en/latest/users-manual/file-transfer.html
[30] Folding@home power slider / idle mode - https://foldingathome.org/faqs/fah-v7/v7-introduction/web-control/folding-power-slider/

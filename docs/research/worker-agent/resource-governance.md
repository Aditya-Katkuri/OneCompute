# Deep dive: Windows resource governance for unobtrusive compute

Citation numbers match `README.md`.

## Control layers

| Layer | Windows primitive | What it controls | NightShift use |
|---|---|---|---|
| Scheduling priority | `IDLE_PRIORITY_CLASS`, thread priorities | Which ready thread runs first | Make worker jobs lose to foreground work [11][12] |
| Background mode | `PROCESS_MODE_BACKGROUND_BEGIN` | Broader resource priority, not just CPU | Reduce disk/memory/I/O interference [12] |
| Power QoS | EcoQoS / power throttling | Efficient cores/frequencies and thermal behavior | Lower fan/noise for background slices [13][14] |
| Hard resource cap | Job Object CPU rate + memory limits | Enforce manifest limits across process tree | Prevent runaway chunks [7][9] |
| Kill boundary | Job Object kill-on-close / terminate | Stop entire process tree | Instant yield [8][10] |

## Why combined controls matter

Windows priority decides among ready threads; when a higher-priority foreground thread becomes ready, lower-priority NightShift work can be preempted [11][15]. But Microsoft explicitly warns that CPU priority alone is not enough for background work because disk and memory activity can still hurt responsiveness [12]. EcoQoS is a hint about *how* to run, while Job Object CPU rate is an enforcement mechanism about *how much* CPU a job can consume [9][13].

## Recommended PoC defaults

- Worker supervisor: normal priority, small footprint.
- Job subprocess/container host: below-normal or idle priority.
- If using a Python worker to launch a child, apply background mode/EcoQoS to the child or wrapper process before heavy work starts.
- Apply Job Object CPU hard cap from manifest, for example `cpu_pct=50` -> `CpuRate=5000` [9].
- Keep `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` always enabled for demo jobs [8].

## Open implementation details

- Python `ctypes` wrappers need exact structure layouts for `JOBOBJECT_CPU_RATE_CONTROL_INFORMATION`, `JOBOBJECT_EXTENDED_LIMIT_INFORMATION`, and `PROCESS_POWER_THROTTLING_STATE` [8][9][14].
- Background mode should be tested for priority inversion if the worker and job share locks or pipes; Microsoft warns background threads sharing resources with higher-priority threads can create inversion [12].

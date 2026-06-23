# Deep dive: accelerators and hardware reality

Citation numbers match `README.md`.

## GPU admission

NVML is the practical PoC source for NVIDIA GPU capability and live state. It is NVIDIA's monitoring/management API and underlies `nvidia-smi` [16]. Use it for:

- capability: device count, name, memory;
- live gate: GPU utilization and memory utilization;
- power/thermal proxy: power usage and P-state [17].

A quiet keyboard with a busy GPU should be treated as not idle. Examples: user left Blender rendering, a game is running while AFK, Teams/background effects are using GPU, or another local ML job is active. NightShift should avoid becoming the reason the fan spins up.

## `pynvml` failure mode

NVML can be absent, not on PATH, blocked by driver install type, unsupported on non-NVIDIA devices, or fail during initialization [16]. Therefore:

```text
try NVML init/query
  success -> advertise GPU and enable gpu_idle gate
  failure -> has_gpu=false, accel excludes cuda, gpu gate passes as no_gpu
```

Never let GPU detection crash registration.

## NPU roadmap

Windows ML is the strategic path for local ONNX inference across NPU/GPU/CPU, with Windows managing execution providers [19]. DirectML is still supported, but Microsoft states new ONNX Runtime deployment work has moved to Windows ML [18]. Copilot+ PC NPUs exceed 40 TOPS and Task Manager can display NPU usage [20]. QNN EP can target Qualcomm HTP/NPU but has package/runtime constraints and provider options that need separate validation [21].

PoC should not schedule real NPU work unless T2 can both run a benchmark and observe contention. Otherwise, add labels such as `npu_present_unmeasured` only for roadmap storytelling.

## Hardware truth for capability dict

- `has_gpu`: only true after successful driver/API query.
- `gpu_vram_gb`: usable device memory, not marketing memory if unavailable.
- `accel`: `cuda` only after NVML/CUDA path succeeds; `directml` is a software path and should be separate from measured throughput.
- `benchmarked_tops`: only after a controlled NightShift benchmark; do not trust nameplate TOPS [19][20].

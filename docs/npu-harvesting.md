# NPU harvesting (detect-and-advertise slice)

OneCompute's headline roadmap item is harvesting the **NPUs of Copilot+ PCs** (Qualcomm
Snapdragon X, AMD Ryzen AI, Intel Lunar Lake) alongside the CPU and GPU the PoC already uses.
This document describes the slice that ships **now** and draws a hard, honest line around what is
still roadmap. It follows OneCompute's pilot philosophy: **measure first, run workloads later.**

## What is delivered now

- **NPU detection.** `worker.capability.detect_npu()` is a best-effort, never-raising probe. It
  checks, in order and each guarded independently:
  1. ONNX Runtime execution providers via `onnxruntime.get_available_providers()` (the import is
     optional and guarded). A `QNNExecutionProvider` (Qualcomm's NPU-specific provider) or a
     `DmlExecutionProvider` (DirectML) is treated as an NPU / accelerator signal.
  2. A cheap, opt-in Windows environment hint (`ONECOMPUTE_NPU` / `ONECOMPUTE_NPU_TOPS`) so a
     pilot can declare a known NPU out-of-band without pulling in a heavy runtime.
  Otherwise it returns `(False, None)`. No heavy dependency is added; `onnxruntime` is optional.
- **Capability advertisement.** `detect_capability()` wires the result into two new optional
  fields on the frozen `Capability` contract: `has_npu: bool = False` and
  `npu_tops: float | None = None`. These ride in the Capability the worker sends at registration.
- **Inclusion in the fleet picture.** The orchestrator already stores the advertised Capability as
  `capability_json`, so the NPU signal is persisted with **no schema change**. The fleet can now
  see which machines report an NPU without any new plumbing.

## What is roadmap (not built here)

- **Actual NPU job execution.** Running a real workload on the NPU needs
  **`onnxruntime-directml`** (or a QNN execution provider build) **and real Copilot+ NPU
  hardware**. Neither is available in this environment, so execution is deliberately out of scope.
  When it lands it will slot into `src/jobkit/execute.py` as a new executor kind, gated on the
  advertised `has_npu`.
- **NPU-aware scheduling.** This slice is **advertisement only**. `scheduler.py` is intentionally
  unchanged: no NPU class weight, no NPU capability matching. Scheduling on the NPU signal is a
  later step once execution and benchmarking exist.

## The honest TOPS framing

`npu_tops` is a **nameplate INT8 peak** taken from the spec sheet (for example ~45 TOPS for a
Snapdragon X Elite, ~50 for Ryzen AI 300, ~48 for Lunar Lake). It is **not delivered
throughput.** Real harvested performance is materially lower because of precision, thermals,
memory bandwidth, and contention with the employee's foreground work. Consistent with
`docs/idea.md`, OneCompute **benchmarks each machine's real throughput** rather than trusting the
spec sheet, and any harvested-throughput number is reported **measured, beside** (never instead
of) the nameplate ceiling. Credit is always metered on the server-assigned `class_weight`, never
on `npu_tops`.

## How this fits the measure-first pilot

The measurement pilot's whole point is to learn a fleet's real idle headroom **before** running
production workloads on employee machines. Advertising `has_npu` / `npu_tops` extends that same
"measure first" posture to the NPU: the fleet learns how much NPU capacity exists and where, with
zero execution risk, so the later decision to actually run NPU jobs is grounded in measured
inventory rather than marketing math.

## Microsoft Copilot+ PC / DirectML alignment

- **Copilot+ PCs** are defined by an on-device NPU above Microsoft's **40-TOPS (INT8)** floor, so
  a growing share of a corporate fleet ships NPU silicon that sits idle most of the day.
- **DirectML** is Microsoft's hardware-agnostic DirectX 12 acceleration layer; **ONNX Runtime**
  exposes it as `DmlExecutionProvider`, and Qualcomm NPUs additionally expose
  `QNNExecutionProvider`. Detecting these providers is the standard, vendor-neutral way to see a
  Copilot+ NPU from Python, which is exactly what `detect_npu()` does.
- A `DmlExecutionProvider` can also front an iGPU or dGPU, so it is a **weak** NPU signal on its
  own; `QNNExecutionProvider` is NPU-specific. The roadmap execution path will pin the provider
  and benchmark it rather than assume the nameplate figure.

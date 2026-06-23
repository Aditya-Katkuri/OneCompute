# Measurement and throughput

## What to measure in the PoC

Measure **validated useful work per wall-clock second**. For the CPU fan-out job, use accepted `data.transform` items/sec. For the AI SDK job, use completed prompt-slices/minute and token counts if the SDK returns them. Do not display one blended FLOPS/TOPS number because CPU hash transforms, SDK calls, GPU kernels, and NPU INT8 peaks are not equivalent units.

## Why TOPS is not enough

The Roofline model bounds attainable performance by both peak compute and memory bandwidth: a workload can be bandwidth-bound even when a processor advertises high peak FLOP/s/TOPS (https://www2.eecs.berkeley.edu/Pubs/TechRpts/2008/EECS-2008-134.html). NVIDIA Nsight Compute documents practical GPU limiters including occupancy, registers, shared/global memory, Tensor Cores, warp stalls, and scheduling, all of which affect realized throughput (https://docs.nvidia.com/nsight-compute/ProfilingGuide/index.html). MLPerf Inference measures trained-model inference with scenarios, quality targets, and metrics, which is the right spirit: benchmark the workload, not the brochure number (https://mlcommons.org/benchmarks/inference-datacenter/).

## PoC formula

```text
job_measured_units_per_sec = accepted_units / (completed_at - started_at)
fleet_speedup = single_worker_baseline_seconds / live_fleet_elapsed_seconds
yield_latency_ms = yielded_at - input_activity_detected_at
credit_units = accepted_units * server_assigned_class_weight
```

## Dashboard labels

- **Measured harvested throughput:** live accepted units/sec.
- **1-machine ghost baseline:** pre-measured single-worker time, greyed out.
- **Theoretical ceiling:** peak Copilot+ NPU aggregate, separate from measured throughput.
- **Not credited:** yielded, failed, or challenge-rejected units.

## Roadmap metrics

Add per-worker calibration, tokens/sec, GPU util, power/energy estimates, p95 yield latency, and confidence intervals. Calibration should inform scheduling and display, but ledger credit should still come from validated accepted work.

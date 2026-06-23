# Capability matching and bin-packing notes

## Actionable model

Represent every job as a `Requires` predicate and every worker as a `Capability` record. The scheduler first filters, then ranks.

Minimum PoC predicate:

```text
matches(worker, job) :=
  (not job.needs_gpu or worker.has_gpu)
  and (job.min_vram_gb is null or worker.gpu_vram_gb >= job.min_vram_gb)
  and (job.accel empty or any(job.accel in worker.accel))
  and (job.cpus <= worker.cpus)
  and (job.ram_gb <= worker.ram_gb)
```

Minimum rank:

1. Oldest queued matching job.
2. Prefer CPU-only workers for CPU-only jobs if any CPU-only workers are idle.
3. Prefer higher measured throughput only among otherwise equivalent workers.

## What prior art teaches

- Ray proves a simple key/value resource dictionary is enough for a useful scheduler, but also warns that logical resources are admission control, not physical enforcement.
- Kubernetes and Slurm prove GPUs need both count and type/label dimensions; integer GPU count alone does not express VRAM, CUDA/DirectML, or model suitability.
- HTCondor proves the clean mental model: both job and worker can have requirements and rank.

## NightShift-specific decision

Do not build a general solver. Build a deterministic predicate over the frozen Pydantic models, with unit tests for:

- GPU job never assigned to CPU-only worker.
- `min_vram_gb` enforced.
- `accel=["cuda"]` not assigned to DirectML-only worker.
- CPU-only job can run on GPU worker only when no better CPU-only placement is available or GPU queue is empty.
- Server-side `class_weight` cannot be supplied by worker.

## Schema/query sketch

Store normalized capability as JSON for dashboard/debug, but denormalize fields needed for fast matching:

- `workers(worker_id, cpus, ram_gb, has_gpu, gpu_vram_gb, accel_json, class_weight, last_seen, status)`
- `jobs(job_id, state, kind, needs_gpu, min_vram_gb, accel_json, cpus, ram_gb, created_at, lease_expires, assigned_worker)`

For PoC scale, JSON filtering in Python after a narrowed SQL query is acceptable. If the queue grows, denormalize `requires_cuda`, `requires_directml`, `requires_npu` booleans.

## Roadmap

Add measured throughput per workload family, not one global TOPS number:

- `bench_sha256_items_per_s`
- `bench_python_chunks_per_s`
- `bench_cuda_tokens_per_s`
- `bench_winml_tokens_per_s` later

This lets the scheduler choose the fastest *relevant* worker without rewarding fake or irrelevant hardware claims.

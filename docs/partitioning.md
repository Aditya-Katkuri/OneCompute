# Capability-weighted partitioning

How OneCompute splits one workload across the fleet, and why the split is now sized by each
machine's capability and live idle headroom instead of being uniform.

## Static vs. dynamic split

A launched workload (`POST /workloads`) is carved into **tiles**: contiguous, non-overlapping
slices of the total work (fractal row bands, optimize candidate ranges, synth row slices, and so
on). Every tile is an independent, signed job the fleet pulls and runs.

- **Static (the original PoC split).** One tile per machine, all tiles the same size
  (`even_ranges` in `src/workloads/partition.py`). Simple and honest, but it hands a saturated
  dual-core laptop exactly as much work as an idle GPU workstation, so the slowest tile sets the
  wall-clock for the whole render.
- **Dynamic (this feature).** Still one tile per machine at launch, but each tile is **sized in
  proportion to that machine's capability and how idle it currently is.** A more capable / more
  idle machine gets a proportionally larger band; a busy or weak machine gets a smaller one. The
  total work, tile count, and exact coverage are unchanged, so nothing downstream has to know.

This is deliberately still **one-shot partitioning at launch**. See "Scope" below.

## The weighting model: capability x headroom

Per-machine weights come from `worker_weight(class_weight, free_ram_gb, load_pct)` in
`src/workloads/partition.py`. It is a pure, deterministic function of a worker's live orchestrator
row:

```
weight = class_weight  x  idle_factor  x  ram_factor
```

- **`class_weight`** is the server-assigned capability tier (`scheduler.class_weight_for`:
  GPU = 5, CPU = 1). It is the same trusted number used for crediting, never the agent's own claim.
- **`idle_factor`** scales linearly from `0.1` (fully loaded) to `1.0` (fully idle), driven by
  `load_pct` = the busier of the worker's CPU / GPU percent. Idle machines are favored; a saturated
  machine still keeps a floor of 10% so it is never starved to zero.
- **`ram_factor`** scales linearly from `0.5` (no free RAM) to `1.0` at or above 8 GB of free RAM.
  Unknown free RAM is treated as ample (`1.0`).

Each factor is bounded away from zero, so an approved worker's weight is always strictly positive:
the split **favors** capable, idle machines without ever handing a live worker nothing. The bounded
factors also cap the spread (roughly 5 x 10 x 2), which keeps one very idle GPU box from swallowing
essentially the entire workload.

### Turning weights into tile sizes

`weighted_ranges(total, weights)` and its count-only sibling `weighted_partition(total, weights)`
apportion the total work across the weights with **largest-remainder** rounding:

- The sum is always exact (`sum(shares) == total`): no rows or candidates are lost or double-counted.
- The flooring leftover goes to the largest fractional remainders (ties broken by lowest index),
  so the result is fully deterministic for a fixed input.
- `weighted_partition` additionally guarantees every share is `>= 1` whenever `total >= N`, by
  borrowing single units from the largest holders. That is the right rule when the "units" being
  split are whole tiles handed to workers: every worker gets at least one.

When all weights are equal, both reduce to the same near-even split as `even_ranges`, so a
**homogeneous fleet keeps exactly the original uniform behavior**.

## How launch uses it

`launch_workload` / `_build_workload_jobs` in `src/orchestrator/app.py`:

1. Read the live **approved, non-blacklisted** worker rows, deterministically ordered by
   `worker_id`, and map each to a weight via `worker_weight` (`_capability_tile_weights`).
2. Use those weights **only when** there is exactly one worker per requested tile
   (`len(workers) == n_tiles`, the standard per-machine launch) **and** the fleet is actually
   heterogeneous. Otherwise fall back to `None`, i.e. the original even split. This keeps every
   existing launch path (no workers yet, a tile count that does not line up one-per-machine, or a
   uniform fleet) byte-for-byte unchanged.
3. Pass the per-tile weights to the range-splitting builders (fractal, optimize, ai.synth,
   montecarlo, hashcrack, ai.infer, ai.eval, ai.graph). Builders whose split is not per-machine
   (`data.transform` uniform fan-out, `ai.batch_infer` fixed slice size) ignore the weights.

The response shape (`WorkloadLaunchResponse`) and the total units are unchanged; only the *sizes*
of the per-machine tiles differ.

## Determinism

Everything on this path is deterministic given its inputs: `worker_weight` is a pure arithmetic
function, `weighted_ranges` / `weighted_partition` use fixed largest-remainder rounding with a
fixed tie-break, and the worker rows are read in a fixed `worker_id` order. The same fleet state
produces the same split every time, which keeps the demo reproducible and the tests exact.

## Scope (honest boundaries)

- This is **launch-time, one-shot** partitioning. Tiles are sized once, when the workload is
  launched, from the fleet snapshot at that moment.
- It is **not** mid-job re-partitioning or work-stealing: a tile is not resized or moved after it
  is enqueued, and a machine that finishes early is not automatically handed a slice carved out of
  a slower machine's in-flight tile. Dynamic re-partitioning and work-stealing remain roadmap
  (see `docs/architecture.md` and `docs/idea.md`).
- Extreme skew can, in principle, round a very light worker's band down to zero units; such empty
  bands are simply skipped by the builders (the work still sums exactly across the remaining
  tiles). The bounded weight factors make this unlikely for realistic total sizes.

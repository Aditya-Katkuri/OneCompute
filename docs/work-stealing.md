# Work-stealing via over-decomposition

How OneCompute lets fast, idle machines pull more of a workload while a slow machine only ever
holds one small piece, without any mid-flight preemption or central rebalancing. This builds
directly on the tile model in [`docs/partitioning.md`](partitioning.md); read that first.

## The idea

A launched workload (`POST /workloads`) is carved into **tiles**: contiguous, non-overlapping
slices of the total work (fractal row bands, optimize candidate ranges, synth row slices, and so
on). Every tile is an independent, signed job that workers **pull** off a SQLite queue via
`GET /jobs/next`, one lease at a time.

The original PoC makes exactly **one tile per approved worker**. That is simple, but the split is
fixed at launch: if a machine turns out slow (or a human comes back and the worker yields), its one
big tile becomes the long pole for the whole render, and a fast neighbor that finished early has
nothing else to grab.

**Over-decomposition** fixes that with the machinery the repo already has. Split the workload into
**more, smaller tiles than there are workers** (`oversubscribe` tiles per worker). Now:

- A fast/idle machine finishes its small tile, polls again, and immediately pulls the next one. It
  keeps doing this and naturally ends up running **more** tiles.
- A slow machine only ever holds **one** small tile at a time (the one-lease-per-worker guard in
  `jobs_next`), so it can never become the long pole for more than a single small slice.
- If a machine drops or yields mid-tile, that **whole tile requeues** and the next free worker
  steals it (lease reaping / requeue, unchanged).

This IS work-stealing, expressed purely through the existing pull queue: work flows to whoever is
free to take it. Nothing about the assignment, lease, or requeue semantics changes; the only new
thing is that a launch can enqueue more tiles than workers.

## How to use it

`WorkloadLaunchRequest` gains one optional field, `oversubscribe` (an integer, default `1`):

```jsonc
// today's behavior, unchanged: one tile per worker
POST /workloads { "kind": "fractal", "params": { ... } }

// over-decompose: ~4 tiles per approved worker
POST /workloads { "kind": "fractal", "oversubscribe": 4, "params": { ... } }

// pin an exact tile count yourself (wins over the worker-count computation)
POST /workloads { "kind": "fractal", "n_tiles": 32, "params": { ... } }
```

Tile-count resolution (`_resolve_tile_count` in `src/orchestrator/app.py`), ordered so the default
is byte-identical to today:

1. **Explicit `n_tiles`** is respected verbatim (clamped to the cap). Every existing launch, including
   the capability-weighted one-tile-per-machine split, is unchanged.
2. Otherwise **`oversubscribe > 1`** carves `~oversubscribe x (live approved, non-blacklisted worker
   count)` tiles (`partition.oversubscribed_tiles`), clamped to `MAX_WORKLOAD_TILES` (512). An empty
   fleet is treated as one worker, so a launch still enqueues `oversubscribe` tiles.
3. Otherwise (`oversubscribe == 1`, no explicit `n_tiles`) the model default is used.

Because the tile count now usually exceeds the worker count, the per-tile capability weighting from
[`docs/partitioning.md`](partitioning.md) naturally disengages (`_capability_tile_weights` only
weights when there is exactly one worker per tile) and the builders fall back to the near-even
`even_ranges` split. Many small, near-uniform tiles are exactly what makes the steal fine-grained,
so this is the right and simpler behavior at high tile counts.

## Determinism and exact coverage

The tile count is a pure function of the fleet size and `oversubscribe`; the tiles themselves are
produced by the same `even_ranges` / `weighted_ranges` largest-remainder split used everywhere else.
So `sum(tile units) == total` exactly (no work lost or double-counted), and the same launch against
the same fleet produces the same tiles every time. *Which* worker ends up running *which* tile is
decided at runtime by who polls when, which is the point, but every tile is completed exactly once.

## Scope (honest boundaries)

- This is **one-shot over-decomposition at launch** plus **whole-tile requeue on drop/yield**. Tiles
  are sized once, when the workload is launched, and the smallest unit of stealing is one whole tile.
- It is **not** true mid-flight preemption or splitting of an in-progress tile: a tile already being
  computed is never sliced in half and handed partly to a faster machine. If a tile is dropped or
  yielded it re-runs from the start on whoever steals it. Finer granularity comes from launching with
  a larger `oversubscribe` (smaller tiles), not from cutting a running tile.
- Cross-machine model sharding and mid-flight re-partitioning remain roadmap (see
  [`docs/idea.md`](idea.md)).
- `oversubscribe` and the tile count are bounded (`oversubscribe <= 64`, `MAX_WORKLOAD_TILES = 512`)
  so a large fleet or a large factor cannot explode the queue.

"""NON-AI: distributed hash crack / proof-of-work (host-side builder + aggregator).

The fleet searches a nonce keyspace for ``sha256(prefix + nonce)`` whose hex starts with a
target prefix; each machine scans a slice (one tile) across all its cores. ``aggregate_hashcrack``
picks the winner and sums the hashes tried for a fleet-wide hash rate. Lengthen ``target_prefix``
or widen ``keyspace`` to control runtime (each extra hex digit is ~16x more work).
"""

from __future__ import annotations

from workloads.partition import even_ranges, weighted_ranges


def build_hashcrack_jobs(
    n_tiles: int,
    keyspace: int = 24_000_000,
    target_prefix: str = "000000",
    prefix: str = "onecompute",
    nonce_start: int = 0,
    weights: list[float] | None = None,
) -> list[dict]:
    """Build ``n_tiles`` ``hashcrack`` jobs, splitting the nonce keyspace across the fleet."""
    if n_tiles <= 0:
        raise ValueError("n_tiles must be positive")
    if keyspace <= 0:
        raise ValueError("keyspace must be positive")
    if not target_prefix:
        raise ValueError("target_prefix must be non-empty")

    if weights is None:
        ranges = even_ranges(keyspace, n_tiles)
    else:
        if len(weights) != n_tiles:
            raise ValueError("weights length must equal n_tiles")
        ranges = weighted_ranges(keyspace, weights)

    jobs: list[dict] = []
    for start, end in ranges:
        if end <= start:
            continue
        jobs.append(
            {
                "kind": "hashcrack",
                "input": {
                    "prefix": prefix,
                    "target_prefix": target_prefix,
                    "nonce_start": nonce_start + start,
                    "nonce_end": nonce_start + end,
                },
                "units": end - start,
            }
        )
    return jobs


def aggregate_hashcrack(results: list[dict]) -> dict:
    """Merge ``hashcrack`` tile results: the winning nonce/hash (if any) + total hashes tried."""
    parts = [r for r in results if r]
    hashes_tried = sum(int(r.get("hashes_tried", 0)) for r in parts)
    winner = next((r for r in parts if r.get("found")), None)
    target = next((r.get("target_prefix") for r in parts if r.get("target_prefix")), None)
    return {
        "found": winner is not None,
        "target_prefix": target,
        "nonce": winner.get("nonce") if winner else None,
        "hash": winner.get("hash") if winner else None,
        "hashes_tried": hashes_tried,
        "tiles": len(parts),
    }


__all__ = ["build_hashcrack_jobs", "aggregate_hashcrack"]

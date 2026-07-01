"""Shared measurement-pilot math (governor-consistent idle-headroom estimation).

Imported by both the CLI report (``scripts/measure_report.py``) and the orchestrator's fleet
rollup (``GET /measurement``) so the "how much idle headroom is really there" number is computed
one way, everywhere. Pure stdlib.
"""

from measurement.headroom import (
    BUCKET_FIELDS,
    BUCKETS_PER_WEEK,
    DEFAULT_HARVEST_HIGH,
    DEFAULT_HARVEST_LOW,
    DEFAULT_MARGIN_PCT,
    aggregate,
    finite,
    normalize_buckets,
    summarize_profile,
)

__all__ = [
    "BUCKETS_PER_WEEK",
    "BUCKET_FIELDS",
    "DEFAULT_MARGIN_PCT",
    "DEFAULT_HARVEST_LOW",
    "DEFAULT_HARVEST_HIGH",
    "finite",
    "normalize_buckets",
    "summarize_profile",
    "aggregate",
]

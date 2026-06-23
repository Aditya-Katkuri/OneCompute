from __future__ import annotations

import json
import sqlite3

from contracts import Capability, Requires


def capability_match(requires: Requires, cap: Capability) -> bool:
    if requires.needs_gpu and not cap.has_gpu:
        return False
    if requires.min_vram_gb is not None and (cap.gpu_vram_gb or 0.0) < requires.min_vram_gb:
        return False
    if cap.cpus < requires.min_cpus:
        return False
    if not set(requires.accel).issubset(set(cap.accel)):
        return False
    return True


def class_weight_for(cap: Capability) -> float:
    return 5.0 if cap.has_gpu else 1.0


def pick_job_for(conn: sqlite3.Connection, cap: Capability) -> sqlite3.Row | None:
    rows = conn.execute(
        "SELECT * FROM jobs WHERE state = 'queued' ORDER BY created_at ASC, job_id ASC"
    ).fetchall()
    for row in rows:
        manifest = json.loads(row["manifest_json"])
        requires = Requires(**manifest.get("requires", {}))
        if capability_match(requires, cap):
            return row
    return None

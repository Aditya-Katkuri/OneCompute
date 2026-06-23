"""Sandbox entrypoint: run one job from a JSON spec file and write the result.

T3 isolation invokes this inside a container / restricted subprocess:
    python -m jobkit <in.json> <out.json>
where in.json = {"kind": "...", "input": {...}}. Keeping execution behind a tiny
file-based CLI is what lets the SAME jobkit logic run inside an isolation boundary
with no network and no access to the worker's files.
"""

from __future__ import annotations

import json
import sys

from jobkit.execute import execute


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.stderr.write("usage: python -m jobkit <in.json> <out.json>\n")
        return 2
    in_path, out_path = argv[1], argv[2]
    with open(in_path, encoding="utf-8") as fh:
        spec = json.load(fh)
    output = execute(spec["kind"], spec.get("input", {}))
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(output, fh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

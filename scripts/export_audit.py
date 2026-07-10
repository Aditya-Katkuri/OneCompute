"""Export the orchestrator audit log as newline-delimited JSON (JSONL) for SIEM ingestion.

A thin CLI wrapper over GET /events/export: fetch every audit event (including the tamper-evident
prev_hash/hash chain fields) and write it as one JSON object per line, ready for Microsoft Sentinel
or any log pipeline that ingests JSONL. Use GET /events/verify (or verify_audit_chain) to confirm
the chain is intact before or after export.

    uv run python scripts/export_audit.py --url http://<host-ip>:8080 --out audit.jsonl
"""

from __future__ import annotations

import argparse
import sys
import urllib.request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="orchestrator base URL")
    parser.add_argument(
        "--out",
        default="-",
        help="output file path, or '-' for stdout (default: stdout)",
    )
    args = parser.parse_args()

    with urllib.request.urlopen(args.url.rstrip("/") + "/events/export") as resp:
        body = resp.read().decode("utf-8")

    if args.out == "-":
        sys.stdout.write(body)
    else:
        with open(args.out, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
        line_count = sum(1 for line in body.splitlines() if line)
        print(f"wrote {line_count} audit events to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

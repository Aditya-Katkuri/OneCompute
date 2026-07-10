"""A tiny conforming stub of the ``cosign`` CLI for tests.

It answers exactly the three invocations ``src/trust/cosign.py`` makes:

- ``version``     -> prints a version banner, exit 0 (so ``cosign_available`` is True).
- ``sign-blob``   -> writes the ``--output-signature`` (and ``--bundle``) file with
  deterministic fake contents, exit 0.
- ``verify-blob`` -> exit code taken from ``$FAKE_COSIGN_VERIFY_RC`` (default 0), so a
  test can drive both the pass and the fail mapping.

It never performs real cryptography; it exists only to exercise the real OneCompute
subprocess/argv code path without requiring a genuine cosign binary in CI.
"""

from __future__ import annotations

import os
import sys


def _opt(argv: list[str], name: str) -> str | None:
    if name in argv:
        idx = argv.index(name)
        if idx + 1 < len(argv):
            return argv[idx + 1]
    return None


def main(argv: list[str]) -> int:
    if not argv:
        return 1
    command = argv[0]

    if command == "version":
        print("GitVersion: v2.0.0-fake-onecompute")
        return 0

    if command == "sign-blob":
        signature = _opt(argv, "--output-signature")
        bundle = _opt(argv, "--bundle")
        if signature:
            with open(signature, "w", encoding="utf-8") as fh:
                fh.write("MEUCIQD-fake-cosign-signature-not-real\n")
        if bundle:
            with open(bundle, "w", encoding="utf-8") as fh:
                fh.write('{"fake": true}\n')
        print("fake cosign sign-blob ok")
        return 0

    if command == "verify-blob":
        rc = int(os.environ.get("FAKE_COSIGN_VERIFY_RC", "0"))
        if rc == 0:
            print("Verified OK (fake)")
        else:
            sys.stderr.write("fake cosign verify-blob: verification failed\n")
        return rc

    sys.stderr.write(f"fake cosign: unknown command {command!r}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""PyInstaller entry point for the standalone OneCompute worker (``onecompute-worker.exe``).

Built for managed-machine pilots where running ``python -m worker`` isn't practical. Equivalent
to ``python -m worker``; forwards all CLI args (``--url``, ``--governor``, ``--once``, ...).
See ``scripts/build_worker_exe.ps1``.
"""

from worker.__main__ import main

if __name__ == "__main__":
    main()

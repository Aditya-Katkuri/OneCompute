"""Test-wide defaults that keep the suite hermetic + fast regardless of the host.

- ONECOMPUTE_NO_LLM forces the AI executors' disclosed deterministic fallback, so AI tests
  never call a real model (local Ollama or a cloud SDK) even when one is reachable.
- ONECOMPUTE_MAX_WORKERS=1 runs the multi-core kinds sequentially (no process spawn) by default;
  a test that wants the parallel path can monkeypatch the env higher.

Both use setdefault so an explicit outer env (e.g. a deliberate integration run) still wins.
"""

import os

os.environ.setdefault("ONECOMPUTE_NO_LLM", "1")
os.environ.setdefault("ONECOMPUTE_MAX_WORKERS", "1")

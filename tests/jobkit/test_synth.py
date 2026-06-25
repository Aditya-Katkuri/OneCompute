"""The `ai.synth` executor's disclosed no-key fallback yields exactly n_rows records."""
from __future__ import annotations

from jobkit.execute import execute
from workloads.synth import build_synth_jobs, merge_synth


def test_fallback_yields_n_rows_with_requested_fields(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ONECOMPUTE_NO_LLM", "1")  # force the disclosed fallback (ignore any local Ollama)
    fields = ["name", "role", "team", "summary"]
    out = execute("ai.synth", {"n_rows": 5, "fields": fields, "start_index": 0})
    assert out["backend"] == "fallback"
    assert out["yielded"] is False
    assert len(out["rows"]) == 5
    for row in out["rows"]:
        assert isinstance(row, dict)
        assert set(row.keys()) == set(fields)
        assert all(isinstance(v, str) and v for v in row.values())


def test_fallback_is_deterministic(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ONECOMPUTE_NO_LLM", "1")  # force the disclosed fallback (ignore any local Ollama)
    a = execute("ai.synth", {"n_rows": 4, "start_index": 10})
    b = execute("ai.synth", {"n_rows": 4, "start_index": 10})
    assert a["rows"] == b["rows"]


def test_start_index_shifts_the_seed(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    # Distinct start_index -> distinct rows (so merged tiles don't duplicate).
    first = execute("ai.synth", {"n_rows": 3, "start_index": 0})
    second = execute("ai.synth", {"n_rows": 3, "start_index": 100})
    assert first["rows"] != second["rows"]


def test_yield_returns_partial(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = execute(
        "ai.synth",
        {"n_rows": 20, "start_index": 0},
        should_yield=lambda: True,
    )
    assert out["yielded"] is True
    assert out["rows"] == []  # yielded before the first row


def test_merge_synth_orders_by_start_index_regardless_of_arrival(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    jobs = build_synth_jobs(n_tiles=3, total_rows=12)
    results = [execute("ai.synth", job["input"]) for job in jobs]
    # Reverse arrival order; merge must still reassemble in start_index order.
    merged = merge_synth(list(reversed(results)))
    in_order = merge_synth(results)
    assert merged == in_order
    assert len(merged) == 12

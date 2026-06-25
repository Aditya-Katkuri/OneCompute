"""Executor tests for the local-model AI kinds (ai.eval, ai.graph, ai.infer).

conftest sets ONECOMPUTE_NO_LLM=1, so these exercise the disclosed no-model FALLBACK path:
deterministic, no Ollama/cloud call. (The real-model path is verified out-of-band.)
"""

from jobkit.execute import execute


def test_ai_eval_fallback_scores_each_item():
    items = [
        {"question": "What is 2+2?", "answer": "4", "label": "a"},
        {"question": "Capital of France?", "answer": "Paris", "label": "b"},
    ]
    out = execute("ai.eval", {"items": items, "rubric": "correctness"})
    assert out["backend"] == "fallback"
    assert out["yielded"] is False
    assert len(out["results"]) == 2
    for row in out["results"]:
        assert 0 <= row["score"] <= 10
        assert "label" in row and row["verdict"]


def test_ai_eval_yield_returns_partial():
    out = execute("ai.eval", {"items": [{"question": "q", "answer": "a"}] * 5}, should_yield=lambda: True)
    assert out["yielded"] is True and out["results"] == []


def test_ai_graph_fallback_extracts_entities_and_relations():
    out = execute(
        "ai.graph",
        {"docs": ["Acme Corp acquired Beacon Vision led by Dana Okoro."]},
    )
    assert out["backend"] == "fallback"
    assert out["nodes"]  # capitalized entities found
    assert all(isinstance(n, str) for n in out["nodes"])
    for edge in out["edges"]:
        assert {"source", "relation", "target"} <= set(edge)


def test_ai_infer_alias_runs_prompts():
    out = execute("ai.infer", {"prompts": ["explain idle compute", "list two risks"], "max_tokens": 16})
    assert out["backend"] == "fallback"
    assert len(out["results"]) == 2
    assert all("completion" in r for r in out["results"])

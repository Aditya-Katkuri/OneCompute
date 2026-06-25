"""Builder + aggregator tests for the local-model AI workloads (hermetic fallback)."""

from jobkit.execute import execute
from workloads.eval import aggregate_eval, build_eval_jobs
from workloads.graph import aggregate_graph, build_graph_jobs
from workloads.infer import build_infer_jobs


def test_infer_build_splits_prompts_exactly():
    jobs = build_infer_jobs(n_tiles=4, n_prompts=40)
    assert len(jobs) == 4
    assert all(j["kind"] == "ai.infer" for j in jobs)
    assert sum(j["units"] for j in jobs) == 40
    assert sum(len(j["input"]["prompts"]) for j in jobs) == 40


def test_eval_build_and_aggregate_leaderboard():
    jobs = build_eval_jobs(n_tiles=3)  # uses the built-in two-system eval set
    assert sum(j["units"] for j in jobs) == len(__import__("workloads.eval", fromlist=["x"]).DEFAULT_EVAL_ITEMS)
    results = [execute(j["kind"], j["input"]) for j in jobs]
    agg = aggregate_eval(results)
    assert agg["n"] == sum(j["units"] for j in jobs)
    assert sum(agg["distribution"]) == agg["n"]
    labels = {entry["label"] for entry in agg["leaderboard"]}
    assert {"strong-model", "weak-model"} <= labels  # both systems represented


def test_graph_build_and_aggregate_dedupes():
    jobs = build_graph_jobs(n_tiles=3)  # built-in corpus
    results = [execute(j["kind"], j["input"]) for j in jobs]
    agg = aggregate_graph(results)
    assert agg["node_count"] == len(agg["nodes"]) == len(set(agg["nodes"]))  # de-duplicated
    assert agg["edge_count"] == len(agg["edges"])
    assert agg["node_count"] > 0


def test_graph_render_png(tmp_path):
    jobs = build_graph_jobs(n_tiles=2)
    agg = aggregate_graph([execute(j["kind"], j["input"]) for j in jobs])
    try:
        from workloads.graph import render_graph_png
    except Exception:
        return
    out = tmp_path / "graph.png"
    try:
        render_graph_png(agg, str(out))
    except RuntimeError:
        return  # networkx/matplotlib not installed -> skip the image assertion
    assert out.exists() and out.stat().st_size > 0

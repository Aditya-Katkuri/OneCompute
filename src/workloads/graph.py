"""AI: distributed knowledge-graph extraction (host-side builder + aggregator + render).

The fleet reads a document corpus and the local model extracts entities + relationships from
each doc; ``aggregate_graph`` merges them (de-duped) into one graph and ``render_graph_png``
draws it (networkx + matplotlib, host-side only, guarded). The eye-catchy AI-visual that runs
on a CPU/ARM fleet -- no GPU, no API. Scale the corpus for runtime.
"""

from __future__ import annotations

from workloads.partition import even_ranges, weighted_ranges

# A small built-in corpus describing a fictional ecosystem, dense in entities + relations so
# even the no-model fallback yields a real graph. Supply your own `docs` for a real corpus.
DEFAULT_DOCS: list[str] = [
    "Northwind Robotics acquired Beacon Vision to strengthen its perception stack.",
    "Beacon Vision was founded by Dana Okoro, who now leads Northwind Robotics research.",
    "Northwind Robotics builds the Atlas Picker, a warehouse arm used by Cedar Logistics.",
    "Cedar Logistics operates fulfillment centers in Reno, Memphis, and Dover.",
    "The Atlas Picker runs on the Helios controller, designed by the Northwind hardware team.",
    "Helios uses the Orion vision model trained by Dana Okoro's group.",
    "Cedar Logistics partnered with Northwind Robotics to automate the Memphis center.",
    "Orion was evaluated against the Pelican benchmark maintained by Beacon Vision.",
    "Pelican measures grasp success on the Cedar Logistics package dataset.",
    "Northwind Robotics reports to its parent company, Lumen Industrial.",
    "Lumen Industrial also owns Beacon Vision after the acquisition.",
    "Dana Okoro previously advised the Orion project at Lumen Industrial.",
]


def build_graph_jobs(
    n_tiles: int,
    docs: list[str] | None = None,
    model: str = "",
    weights: list[float] | None = None,
) -> list[dict]:
    """Build ``n_tiles`` ``ai.graph`` jobs, splitting the document corpus across the fleet."""
    if n_tiles <= 0:
        raise ValueError("n_tiles must be positive")
    corpus = list(docs if docs is not None else DEFAULT_DOCS)
    if not corpus:
        raise ValueError("no documents to process")

    if weights is None:
        ranges = even_ranges(len(corpus), n_tiles)
    else:
        if len(weights) != n_tiles:
            raise ValueError("weights length must equal n_tiles")
        ranges = weighted_ranges(len(corpus), weights)

    jobs: list[dict] = []
    for start, end in ranges:
        if end <= start:
            continue
        jobs.append(
            {
                "kind": "ai.graph",
                "input": {"docs": corpus[start:end], "model": model},
                "units": end - start,
            }
        )
    return jobs


def aggregate_graph(results: list[dict]) -> dict:
    """Merge ``ai.graph`` tiles into one de-duplicated node/edge set."""
    nodes: list[str] = []
    seen_nodes: set[str] = set()
    edges: list[dict] = []
    seen_edges: set[tuple] = set()

    def _add_node(name: str) -> None:
        if name and name not in seen_nodes:
            seen_nodes.add(name)
            nodes.append(name)

    for res in results:
        if not res:
            continue
        for node in res.get("nodes", []):
            _add_node(str(node))
        for edge in res.get("edges", []):
            source, relation, target = (
                str(edge.get("source", "")),
                str(edge.get("relation", "")),
                str(edge.get("target", "")),
            )
            key = (source, relation, target)
            if source and target and key not in seen_edges:
                seen_edges.add(key)
                edges.append({"source": source, "relation": relation, "target": target})
                _add_node(source)
                _add_node(target)

    return {"nodes": nodes, "edges": edges, "node_count": len(nodes), "edge_count": len(edges)}


def render_graph_png(agg: dict, path: str) -> str:
    """Render the merged knowledge graph to a PNG. Needs networkx + matplotlib (host-side)."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import networkx as nx
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("render_graph_png requires networkx + matplotlib (host-side).") from exc

    graph = nx.DiGraph()
    for node in agg.get("nodes", []):
        graph.add_node(node)
    for edge in agg.get("edges", []):
        graph.add_edge(edge["source"], edge["target"], label=edge.get("relation", ""))
    if graph.number_of_nodes() == 0:
        graph.add_node("(empty)")

    pos = nx.spring_layout(graph, seed=7, k=0.7)
    fig, ax = plt.subplots(figsize=(12, 8))
    nx.draw_networkx_nodes(graph, pos, ax=ax, node_color="#6c8cff", node_size=900)
    nx.draw_networkx_edges(graph, pos, ax=ax, edge_color="#9aa", arrows=True, arrowsize=12)
    nx.draw_networkx_labels(graph, pos, ax=ax, font_size=8)
    nx.draw_networkx_edge_labels(
        graph, pos, ax=ax, font_size=6, font_color="#a55",
        edge_labels={(e["source"], e["target"]): e.get("relation", "") for e in agg.get("edges", [])},
    )
    ax.set_title(
        f"Knowledge graph: {agg.get('node_count', 0)} entities, {agg.get('edge_count', 0)} relations"
    )
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return path


__all__ = ["build_graph_jobs", "aggregate_graph", "render_graph_png", "DEFAULT_DOCS"]

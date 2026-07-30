"""Codebase self-graph (v3) — grounded in real imports; includes the decomposed stages node."""
from __future__ import annotations

from eli.runtime import codebase_graph as cg


def test_graph_has_canonical_components_including_v3_stages():
    nodes = cg.build_graph()["nodes"]
    for expected in ("router", "executor", "engine", "pipeline_stages", "agent_bus",
                     "gguf_inference", "orchestrator", "memory", "vector_store", "plugins"):
        assert expected in nodes, f"{expected} missing"


def test_every_edge_endpoint_is_known_and_no_self_edges():
    g = cg.build_graph()
    nodes = set(g["nodes"])
    for (src, dst) in g["edges"]:
        assert src in nodes and dst in nodes and src != dst


def test_v3_specific_edge_engine_imports_stages():
    g = cg.build_graph()
    assert ("engine", "pipeline_stages") in g["edges"], "engine should import the decomposed stages"
    assert ("memory", "vector_store") in g["edges"], "memory should import the vector store"


def test_describe_and_explain_and_paths():
    assert cg.describe_component("engine")["ok"]
    assert cg.describe_component("nope")["ok"] is False
    assert "import" in cg.explain("how does the engine connect to the stages").lower() \
        or "wire" in cg.explain("how does the engine connect to the stages").lower()
    for p in cg.paths("gui", "memory"):
        assert p[0] == "gui" and p[-1] == "memory" and len(p) == len(set(p))

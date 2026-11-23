"""Tests for eli.cognition sub-modules — ~80 tests."""
from __future__ import annotations


# ── Agent Bus ─────────────────────────────────────────────────────────────


def test_agent_bus_instantiation():
    from eli.cognition.agent_bus import AgentBus
    bus = AgentBus()
    assert bus is not None

def test_agent_bus_has_dispatch():
    from eli.cognition.agent_bus import AgentBus
    bus = AgentBus()
    assert hasattr(bus, "dispatch") or hasattr(bus, "run") or hasattr(bus, "send")


# ── Orchestrator ──────────────────────────────────────────────────────────


# ── GGUF Inference ────────────────────────────────────────────────────────


# ── Inference Broker ──────────────────────────────────────────────────────


# ── HyDE ─────────────────────────────────────────────────────────────────


def test_hyde_adds_each_usable_answer_after_the_query():
    """It asserted the result was None, a list or a string: true of anything the function returns."""
    from eli.cognition.hyde import expand_query_hyde
    asked = []

    def _infer(prompt):
        asked.append(prompt)
        return "  A relevant passage about the query topic.  "

    out = expand_query_hyde("What is Python?", _infer, n_hypothetical=2)
    assert out == ["What is Python?", "A relevant passage about the query topic.",
                   "A relevant passage about the query topic."]
    assert len(asked) == 2 and "What is Python?" in asked[0]
    # an answer too short to search with, or a model that fails, leaves the query alone
    assert expand_query_hyde("What is Python?", lambda p: "ok") == ["What is Python?"]

    def _broken(prompt):
        raise RuntimeError("no model")
    assert expand_query_hyde("What is Python?", _broken) == ["What is Python?"]


# ── Engagement Tracker ────────────────────────────────────────────────────

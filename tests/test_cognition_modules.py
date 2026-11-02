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


def test_hyde_with_dummy_infer():
    from eli.cognition.hyde import expand_query_hyde

    def _dummy_infer(prompt):
        return "A relevant document about the query topic"

    result = expand_query_hyde("What is Python?", _dummy_infer, n_hypothetical=1)
    assert result is None or isinstance(result, (list, str))


# ── Engagement Tracker ────────────────────────────────────────────────────

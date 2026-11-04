import pytest
from eli.cognition.agent_bus import AgentBus, get_bus

def test_agent_bus_dispatch_chat():
    bus = AgentBus(max_workers=2)
    result = bus.dispatch("hello", {"action": "CHAT"}, session_id="test", user_id="test")
    assert result.intent_action == "CHAT"
    # Some agents may have contributed
    assert isinstance(result.agents_used, list)
    # confidence between 0 and 1
    assert 0 <= result.aggregated_confidence <= 1

def test_agent_bus_dispatch_runtime_status():
    bus = AgentBus(max_workers=2)
    result = bus.dispatch("what is your runtime status?", {"action": "RUNTIME_STATUS"}, session_id="test", user_id="test")
    # Expect system agent to contribute
    assert "system" in result.agents_used or "capability" in result.agents_used

def test_agent_bus_dispatch_memory_recall():
    bus = AgentBus(max_workers=2)
    result = bus.dispatch("what do you know about me?", {"action": "MEMORY_RECALL"}, session_id="test", user_id="test")
    assert result.intent_action == "MEMORY_RECALL"


def test_three_agents_resting_on_one_record_are_one_source(monkeypatch):
    """Three agents agreeing after reading the same memory are one source, not three; the
    corroboration bonus goes to independent sources (eli/cognition/agent_bus.independent_evidence)."""
    import eli.cognition.agent_bus as bus
    from eli.cognition.agent_bus import AgentResult, _aggregate_confidence, independent_evidence
    # the learned per-agent calibration is another matter: held neutral so the evidence alone counts
    monkeypatch.setattr(bus, "_calibration_factor", lambda *a, **k: 1.0)
    monkeypatch.setattr(bus, "_persist_agent_metrics_for_dispatch", lambda *a, **k: None)

    def _r(agent, ids, text):
        return AgentResult(agent=agent, ok=True, confidence=0.8,
                           data={"results": [{"id": i, "text": text} for i in ids], "content": text})

    said = "the workshop kettle boils in ninety seconds and the thermostat is at nineteen degrees"
    other = "the greenhouse vents open at twenty two degrees and close again at dusk when the fans stop"
    third = "the lamp over the bench flickers when the drill press starts and the fuse is the old kind"
    same = [_r("memory", [1, 2], said), _r("knowledge_graph", [1, 2], said), _r("reflection", [1, 2], said)]
    apart = [_r("memory", [1, 2], said), _r("knowledge_graph", [3, 4], other), _r("reflection", [5, 6], third)]
    _, g_same = _aggregate_confidence(0.5, same, "CHAT")
    _, g_apart = _aggregate_confidence(0.5, apart, "CHAT")
    singles = [_aggregate_confidence(0.5, [r], "CHAT")[1] for r in same]
    assert min(singles) > 0 and g_same == pytest.approx(max(singles)), "three readers of one record: the strongest alone"
    assert g_apart > g_same
    amount, sources = independent_evidence([0.2, 0.2, 0.2], [[1, 1, 1], [1, 1, 1], [1, 1, 1]])
    assert amount == pytest.approx(0.2) and sources == pytest.approx(1.0)
    amount, sources = independent_evidence([0.2, 0.2, 0.2], [[1, 0, 0], [0, 1, 0], [0, 0, 1]])
    assert amount == pytest.approx(0.6) and sources == pytest.approx(3.0)

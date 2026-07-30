"""Frontier #1 — the agent-bus converge loop (critic contradiction → deepen → re-verify)."""
from __future__ import annotations
import pytest
from eli.cognition import agent_bus as ab

R = ab.AgentResult


def _results(mem_txt, sys_txt, agreement, contradiction):
    return [
        R(agent="memory", ok=True, confidence=0.5, data={"content": mem_txt}),
        R(agent="system", ok=True, confidence=0.5, data={"content": sys_txt}),
        R(agent="critic", ok=True, confidence=0.25,
          data={"content": "v", "agreement": agreement, "contradiction": contradiction,
                "sources": ["memory", "system"]}),
    ]


def _deepen_agents():
    return [a for a in ab._ALL_AGENTS if a.name in ("memory", "knowledge_graph")]


def test_converge_adopts_deeper_pass_when_agreement_improves(monkeypatch):
    bus = ab.AgentBus()
    results = _results("the dog chased a red ball in the park",
                       "quantum chromodynamics gluon confinement lattice", 0.02, True)
    # deeper memory pass now corroborates the system source
    monkeypatch.setattr(bus, "_run_agents",
                        lambda ag, ui, it, sid, uid: [R(agent="memory", ok=True, confidence=0.7,
                            data={"content": "quantum chromodynamics gluon confinement lattice gauge"})])
    out = bus._maybe_converge(results, _deepen_agents(), "q", {}, "s", "u")
    nc = next(r for r in out if r.agent == "critic")
    assert nc.data["agreement"] > 0.02 and nc.data.get("converged_round") == 1


def test_converge_keeps_first_pass_when_no_improvement(monkeypatch):
    bus = ab.AgentBus()
    results = _results("alpha beta gamma delta", "one two three four", 0.02, True)
    # deeper pass STILL disagrees → must keep the original (never worsen)
    monkeypatch.setattr(bus, "_run_agents",
                        lambda ag, ui, it, sid, uid: [R(agent="memory", ok=True, confidence=0.7,
                            data={"content": "five six seven eight"})])
    out = bus._maybe_converge(results, _deepen_agents(), "q", {}, "s", "u")
    nc = next(r for r in out if r.agent == "critic")
    assert nc.data.get("converged_round") is None  # nothing adopted


def test_converge_noop_without_contradiction():
    bus = ab.AgentBus()
    results = _results("x", "y", 0.5, False)
    assert bus._maybe_converge(results, _deepen_agents(), "q", {}, "s", "u") == results


def test_converge_disabled_by_env(monkeypatch):
    monkeypatch.setenv("ELI_BUS_CONVERGE", "0")
    bus = ab.AgentBus()
    results = _results("a b c d e", "z y x w v", 0.01, True)
    assert bus._maybe_converge(results, _deepen_agents(), "q", {}, "s", "u") == results

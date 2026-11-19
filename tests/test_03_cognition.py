"""
test_03_cognition.py
====================
Tests for eli.cognition — orchestrator, agent bus, persona, inference.
"""
import importlib


def test_cognition_orchestrator_has_class():
    mod = importlib.import_module("eli.cognition.orchestrator")
    syms = dir(mod)
    matches = [s for s in syms if "Orchestrat" in s]
    assert matches, f"No Orchestrator-like class in orchestrator.py: {syms}"


def test_cognition_agent_bus_has_class():
    mod = importlib.import_module("eli.cognition.agent_bus")
    syms = dir(mod)
    matches = [s for s in syms if "Bus" in s or "Agent" in s]
    assert matches, f"No AgentBus-like class: {syms}"


def test_cognition_persona_reads_and_writes_by_function():
    mod = importlib.import_module("eli.cognition.persona")
    missing = [name for name in mod.__all__ if not callable(getattr(mod, name, None))]
    assert not missing and "get_persona" in mod.__all__, f"persona.py names what it does not have: {missing}"

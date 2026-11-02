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


def test_cognition_persona_has_class():
    mod = importlib.import_module("eli.cognition.persona")
    syms = dir(mod)
    matches = [s for s in syms if "Persona" in s]
    assert matches, f"No Persona class in persona.py: {syms}"

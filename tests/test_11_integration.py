"""
test_11_integration.py
======================
Cross-layer wiring smoke tests. Verifies that key modules can reference
each other without circular imports, attribute errors or missing symbols.
These do NOT start actual inference — they only validate the import graph
and public API surface.
"""
import importlib


# ── Cross-layer connectivity checks ───────────────────────────────────────────

# (removed test_wire_cognition_to_memory — tested the deleted eli.memory.working_memory)


def test_wire_persona_txt_exists():
    """Persona definition text file must exist alongside persona.py."""
    import os
    persona_txt = os.path.join(
        os.path.dirname(importlib.import_module("eli.cognition.persona").__file__),
        "persona.txt",
    )
    assert os.path.isfile(persona_txt), f"persona.txt missing at {persona_txt}"

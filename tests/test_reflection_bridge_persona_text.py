"""load_persona_text() must return a string, never None.

Found while porting v2.4.38 to v3: this function built `chunks` but never
returned them, so it implicitly returned None. Silent in most call sites, but
`persona_mapper.map_persona()` calls `.lower()` on the result unconditionally
-- any real world event that reaches `send_reflection_to_world` crashed with
AttributeError: 'NoneType' object has no attribute 'lower'. v2's equivalent
function was never missing its return statement; this was v3-only drift.
"""
from __future__ import annotations

from eli.world.agency.reflection_bridge import load_persona_text
from eli.world.avatar.persona_mapper import PersonaToAvatarMapper
from eli.world.core.schemas import AwarenessState


def test_load_persona_text_returns_a_string_not_none():
    result = load_persona_text()
    assert isinstance(result, str), (
        "load_persona_text() must always return a string (possibly empty), "
        "never None -- callers call .lower() on it unconditionally"
    )


def test_map_persona_survives_a_real_persona_text_value():
    """Regression: map_persona(..., persona_text=None) used to crash."""
    mapper = PersonaToAvatarMapper()
    persona_text = load_persona_text()
    result = mapper.map_persona(AwarenessState(), persona_text)
    assert isinstance(result, dict)

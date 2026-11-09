"""Tests for eli.cognition.persona — ~80 tests."""
from __future__ import annotations

from pathlib import Path


# ── Import guard ──────────────────────────────────────────────────────────

from eli.cognition.persona import get_persona


# ── Persona file ──────────────────────────────────────────────────────────

def test_persona_txt_exists():
    persona_path = Path(__file__).parents[1] / "eli" / "cognition" / "persona.txt"
    assert persona_path.exists(), "persona.txt not found"

def test_persona_txt_not_empty():
    persona_path = Path(__file__).parents[1] / "eli" / "cognition" / "persona.txt"
    if persona_path.exists():
        text = persona_path.read_text(encoding="utf-8")
        assert len(text.strip()) > 100

def test_persona_txt_contains_eli_name():
    persona_path = Path(__file__).parents[1] / "eli" / "cognition" / "persona.txt"
    if persona_path.exists():
        text = persona_path.read_text(encoding="utf-8").lower()
        assert "eli" in text

def test_persona_txt_has_voice_guidelines():
    persona_path = Path(__file__).parents[1] / "eli" / "cognition" / "persona.txt"
    if persona_path.exists():
        text = persona_path.read_text(encoding="utf-8").upper()
        assert "VOICE" in text or "DRY" in text or "DIRECT" in text

def test_persona_contains_wrong_right_examples():
    persona_path = Path(__file__).parents[1] / "eli" / "cognition" / "persona.txt"
    if persona_path.exists():
        text = persona_path.read_text(encoding="utf-8")
        assert "WRONG" in text or "wrong" in text.lower()


# ── get_persona ──────────────────────────────────────────────────────────


def test_get_persona_text_not_empty():
    assert get_persona().strip()

def test_get_persona_text_contains_eli():
    assert "eli" in get_persona().lower()


# ── Persona content validation ────────────────────────────────────────────

def _get_persona():
    persona_path = Path(__file__).parents[1] / "eli" / "cognition" / "persona.txt"
    if persona_path.exists():
        return persona_path.read_text(encoding="utf-8")
    return ""

def test_persona_has_name_section():
    text = _get_persona()
    if text:
        assert "ELI" in text or "eli" in text.lower()

def test_persona_has_personality_description():
    text = _get_persona()
    if text:
        assert len(text) > 200

def test_persona_under_reasonable_size():
    text = _get_persona()
    if text:
        # Comprehensive persona is intentional (rich emergent voice); cap only
        # guards against runaway growth that would crowd the context window.
        assert len(text) < 16000

def test_persona_uses_english():
    text = _get_persona()
    if text:
        # Basic check — should have common English words
        common = {"the", "is", "and", "a", "to", "of", "in", "you"}
        words = set(text.lower().split())
        overlap = words & common
        assert len(overlap) >= 3


# ── Response sanitizer alignment with persona ────────────────────────────

def test_sanitizer_aligns_with_persona_no_of_course():
    from eli.cognition.output_governor import sanitize_assistant_text
    # If persona says don't use "Of course", sanitizer should strip it
    result = sanitize_assistant_text("Of course, let me help you.")
    assert "Of course" not in result

def test_sanitizer_aligns_with_persona_no_great_question():
    from eli.cognition.output_governor import sanitize_assistant_text
    result = sanitize_assistant_text("Great question! The answer is yes.")
    assert "Great question" not in result

def test_sanitizer_aligns_with_persona_no_short_answer():
    from eli.cognition.output_governor import sanitize_assistant_text
    result = sanitize_assistant_text("Short answer: yes, that's correct.")
    assert "Short answer:" not in result

def test_sanitizer_preserves_dry_tone():
    from eli.cognition.output_governor import sanitize_assistant_text
    text = "Incorrect. The value is 42, not 43."
    result = sanitize_assistant_text(text)
    assert "Incorrect" in result
    assert "42" in result


def test_the_persona_and_the_live_lines_give_one_stance_on_elis_world():
    """The persona, in every prompt, called the rooms a metaphor and a UI feature not to be taken as
    real, while the live lines from ELI VR say it is a real 3D place: asked about a past visit, ELI
    said it had no body and the recorded visits were the user testing input routing."""
    import inspect
    from eli.world import embodiment
    persona = (Path(__file__).parents[1] / "eli" / "cognition" / "persona.txt").read_text(encoding="utf-8")
    assert "real 3D place" in inspect.getsource(embodiment.context_block)
    assert "real 3D place" in persona and "body of light" in persona
    assert "metaphor/visualisation" not in persona and "pretend it's literally real" not in persona
    assert "on record" in persona, "the visits the memory holds are the ones to answer from"

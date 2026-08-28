"""Unified tone integration — words + voice + world share one read."""
from __future__ import annotations

import pytest

from eli.cognition import tone_adaptor as ta
from eli.perception import voice_profile as vp


def test_build_situation_brief_includes_tone_directive(monkeypatch):
    monkeypatch.setenv("ELI_TONE_ADAPT", "1")
    ta.clear_tone()
    vp.set_last_tone({"emotion": "angry", "emotion_confidence": 0.9, "ok": True, "arousal": 0.6})
    ta.note_user_text("I'm furious about this bug")
    brief = ta.build_situation_brief(
        "I'm furious about this bug",
        include_timeline=False,
    )
    assert "[Tone —" in brief or "calm" in brief.lower()
    assert "furious" in brief.lower() or "Speaker voice cue" in brief


def test_synthesis_directives_for_grounded_paths(monkeypatch):
    monkeypatch.setenv("ELI_TONE_ADAPT", "1")
    ta.clear_tone()
    vp.set_last_tone({"emotion": "sad", "emotion_confidence": 0.8, "ok": True})
    block = ta.synthesis_directives("why did the build fail?")
    assert block
    assert "Tone" in block or "Speaker voice cue" in block


def test_repair_coherence_strips_emotion_denial(monkeypatch):
    monkeypatch.setenv("ELI_TONE_ADAPT", "1")
    ta.clear_tone()
    vp.set_last_tone({"emotion": "happy", "emotion_confidence": 0.85})
    ta.note_user_text("great news!")
    out = ta.repair_coherence(
        "I don't have emotions but that's wonderful news for you.",
        "great news!",
    )
    assert "don't have emotions" not in out.lower()
    assert "wonderful" in out.lower()


def test_voice_and_text_share_current_tone(monkeypatch):
    monkeypatch.setenv("ELI_TONE_ADAPT", "1")
    ta.clear_tone()
    vp.set_last_tone({"emotion": "angry", "emotion_confidence": 0.85})
    ta.note_user_text("this is infuriating")
    cur = ta.current_tone()
    assert cur["detected"] == "angry"
    assert ta.expression()
    assert ta.voice_prosody()

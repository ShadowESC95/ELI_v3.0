"""Unified tone integration — words + voice + world share one read."""
from __future__ import annotations

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
    assert "[Tone — calm:" in brief
    assert "[Speaker voice cue — the user sounds angry" in brief


def test_voice_and_text_share_current_tone(monkeypatch):
    monkeypatch.setenv("ELI_TONE_ADAPT", "1")
    ta.clear_tone()
    vp.set_last_tone({"emotion": "angry", "emotion_confidence": 0.85})
    ta.note_user_text("this is infuriating")
    cur = ta.current_tone()
    assert cur["detected"] == "angry"
    assert ta.expression()
    assert ta.voice_prosody()

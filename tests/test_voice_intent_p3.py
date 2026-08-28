"""Tests for P3 voice intent — barge-in and TTS cancel hooks."""
from __future__ import annotations

from eli.perception.voice_intent import (
    cancel_tts_if_speaking,
    classify_partial_transcript,
    on_partial_transcript,
    register_tts_cancel_hook,
    set_tts_speaking,
)


def test_barge_in_detected_on_partial():
    intent = classify_partial_transcript("stop wait", is_final=False)
    assert intent.should_barge_in is True
    assert intent.intent in ("barge_in", "cancel_tts", "interrupt")


def test_cancel_tts_hook_invoked():
    called = {"n": 0}

    def _cancel():
        called["n"] += 1

    register_tts_cancel_hook(_cancel)
    set_tts_speaking(True)
    intent = on_partial_transcript("stop talking please", is_final=True)
    assert intent.should_cancel_tts is True
    assert called["n"] >= 1
    set_tts_speaking(False)


def test_normal_speech_not_barge_in():
    intent = classify_partial_transcript("what is the weather today", is_final=True)
    assert intent.should_barge_in is False
    assert intent.intent == "continue"

"""Speaker ID + tone/emotion integration tests."""
from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pytest

from eli.perception.speaker_id import (
    capabilities,
    embed_audio,
    enroll_speaker,
    identify,
    list_speakers,
)
from eli.cognition.tone_adaptor import detect_voice_emotion
from eli.perception import voice_profile as vp


def _tone_clip(f0: float = 180.0, sr: int = 16000, dur: float = 0.5) -> np.ndarray:
    t = np.arange(int(sr * dur), dtype=np.float32) / sr
    x = (0.3 * np.sin(2 * np.pi * f0 * t) * 32767).astype(np.int16)
    return x


def test_embed_and_enroll_speaker(tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_SPEAKER_ID", "1")
    monkeypatch.setattr(
        "eli.perception.speaker_id._runtime_dir",
        lambda: tmp_path,
    )
    clip = _tone_clip(200.0)
    r = enroll_speaker(clip, 16000, name="Alex")
    assert r.get("ok") is True
    assert r.get("name") == "Alex"
    assert len(list_speakers()) == 1


def test_identify_enrolled_speaker(tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_SPEAKER_ID", "1")
    monkeypatch.setattr("eli.perception.speaker_id._runtime_dir", lambda: tmp_path)
    clip = _tone_clip(210.0)
    enroll_speaker(clip, 16000, name="Jordan")
    r = identify(clip, 16000)
    assert r.get("identified") is True
    assert r.get("name") == "Jordan"
    assert r.get("confidence", 0) >= 0.5


def test_different_voice_lower_match(tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_SPEAKER_ID", "1")
    monkeypatch.setattr("eli.perception.speaker_id._runtime_dir", lambda: tmp_path)
    enroll_speaker(_tone_clip(200.0), 16000, name="A")
    r = identify(_tone_clip(90.0), 16000)
    assert r.get("confidence", 1.0) < 0.99


def test_tone_adaptor_reads_emotion_confidence():
    vp.set_last_tone({"emotion": "happy", "emotion_confidence": 0.82})
    emo, conf = detect_voice_emotion()
    assert emo == "happy"
    assert conf == pytest.approx(0.82, abs=0.01)


def test_capabilities_reports_enabled():
    caps = capabilities()
    assert "enabled" in caps
    assert "enrolled_count" in caps

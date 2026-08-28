"""Emotion-aware TTS + labelled enrollment integration tests."""
from __future__ import annotations

import json
import wave
from pathlib import Path

import numpy as np
import pytest

from eli.cognition import tone_adaptor
from eli.perception import voice_profile as vp
from eli.perception.tts_router import _piper_prosody_args


def _tone_clip(f0: float = 180.0, sr: int = 16000, dur: float = 0.5) -> np.ndarray:
    t = np.arange(int(sr * dur), dtype=np.float32) / sr
    return (0.3 * np.sin(2 * np.pi * f0 * t) * 32767).astype(np.int16)


def _write_wav(path: Path, clip: np.ndarray, sr: int = 16000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(clip.tobytes())


def _patch_voice_dirs(tmp_path: Path, monkeypatch, uid: str):
    root = tmp_path / "profiles" / uid
    monkeypatch.setattr(
        "eli.perception.voice_profile._dir",
        lambda user_id=None: root if str(user_id or "") == uid else tmp_path / "profiles" / str(user_id or "default"),
    )

    class _WaveSF:
        @staticmethod
        def read(path, dtype="float32"):
            with wave.open(str(path), "rb") as wf:
                raw = wf.readframes(wf.getnframes())
            data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            return data, 16000

        @staticmethod
        def write(path, data, sr):
            clip = (np.asarray(data).ravel() * 32767).astype(np.int16)
            _write_wav(Path(path), clip, int(sr))

    monkeypatch.setitem(__import__("sys").modules, "soundfile", _WaveSF())


def test_voice_prosody_shifts_for_detected_user_emotion(monkeypatch):
    monkeypatch.setenv("ELI_TONE_ADAPT", "1")
    monkeypatch.setenv("ELI_TTS_PROSODY", "1")
    tone_adaptor.clear_tone()
    vp.set_last_tone({"emotion": "angry", "emotion_confidence": 0.85, "arousal": 0.7})
    tone_adaptor.note_user_text("I'm so furious about this deadline")
    cur = tone_adaptor.current_tone()
    assert cur["detected"] == "angry"
    assert cur["tone"] == "calm"
    prosody = tone_adaptor.voice_prosody()
    assert prosody.get("length_scale") is not None
    # Calm tone is slower/flatter than ecstatic defaults.
    assert float(prosody.get("length_scale", 1.0)) >= 1.0


def test_piper_prosody_args_merge_tone(monkeypatch):
    monkeypatch.setenv("ELI_TTS_PROSODY", "1")
    tone_adaptor.clear_tone()
    vp.set_last_tone({"emotion": "happy", "emotion_confidence": 0.9})
    tone_adaptor.note_user_text("this is amazing!")
    args = _piper_prosody_args()
    assert "--noise_scale" in args
    idx = args.index("--noise_scale")
    ns = float(args[idx + 1])
    assert ns >= 0.75


def test_labelled_samples_train_emotion_classifier(tmp_path, monkeypatch):
    uid = "test-user-emotion"
    _patch_voice_dirs(tmp_path, monkeypatch, uid)

    labels = {
        "neutral": _tone_clip(180.0),
        "happy": _tone_clip(220.0),
        "angry": _tone_clip(140.0),
        "excited": _tone_clip(260.0),
        "sad": _tone_clip(120.0),
    }
    for label, clip in labels.items():
        vp.add_labelled_sample(clip, 16000, label, user_id=uid)
        if label == "neutral":
            vp.add_sample(clip, 16000, user_id=uid)

    r = vp.build_profile(user_id=uid)
    assert r.get("ok") is True
    em = r.get("emotion_model") or {}
    assert em.get("ok") is True
    assert len(em.get("labels") or []) >= 2

    model_path = tmp_path / "profiles" / uid / "emotion_model.json"
    assert model_path.exists()
    saved = json.loads(model_path.read_text())
    assert saved.get("samples", 0) >= 4


def test_classify_tone_uses_trained_emotion_model(tmp_path, monkeypatch):
    uid = "test-user-classify"
    _patch_voice_dirs(tmp_path, monkeypatch, uid)
    for label, f0 in (("neutral", 180.0), ("happy", 220.0), ("angry", 130.0), ("excited", 250.0)):
        vp.add_labelled_sample(_tone_clip(f0), 16000, label, user_id=uid)
    vp.train_emotion_classifier(uid)
    vp.build_profile(user_id=uid)

    read = vp.classify_tone(_tone_clip(230.0), user_id=uid)
    assert read.get("ok") is True
    assert read.get("emotion_model") is True
    assert read.get("emotion") in vp.EMOTIONS

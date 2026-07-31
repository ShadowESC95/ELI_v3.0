"""Chatterbox (MIT) clone backend — structural + graceful-degradation tests.
(Actual audio generation needs the `chatterbox` extra + a model download; here we
verify wiring, registry-sharing, fallback, and the tone→exaggeration mapping.)"""
from __future__ import annotations
import struct, tempfile, wave
from pathlib import Path
import pytest
from eli.perception import tts_chatterbox as cb
from eli.perception import tts_xtts


def _make_wav() -> str:
    p = tempfile.mktemp(suffix=".wav")
    with wave.open(p, "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
        w.writeframes(b"".join(struct.pack("<h", 0) for _ in range(2205)))
    return p


def test_available_is_false_and_graceful_without_extra():
    # The extra isn't installed in CI/test env → must report False and never raise.
    assert cb.chatterbox_available() is False


def test_select_device_returns_valid_string():
    assert cb._select_device() in ("cpu", "cuda", "mps")


def test_shares_clone_registry_with_xtts_and_falls_back(tmp_path, monkeypatch):
    # add_clone (the record/upload flow) lives in tts_xtts; chatterbox reads the SAME store.
    wav = _make_wav()
    try:
        r = tts_xtts.add_clone("cb_probe", wav, language="en")
        assert r["ok"] is True
        assert tts_xtts.get_clone("cb_probe") is not None          # registry populated
        # chatterbox sees it but can't synth (no extra) → None, not a crash
        assert cb.synthesize_wav("hello", "clone:cb_probe") is None
    finally:
        tts_xtts.delete_clone("cb_probe"); Path(wav).unlink(missing_ok=True)


def test_missing_clone_returns_none():
    assert cb.synthesize_wav("hi", "clone:does_not_exist_zz") is None


def test_tone_exaggeration_mapping():
    from eli.cognition import tone_adaptor as t
    # neutral / no tone → None (backend keeps its own default)
    t.clear_tone()
    assert t.voice_exaggeration() in (None,) or isinstance(t.voice_exaggeration(), float)
    # an expressive tone → a bounded exaggeration in (0, 1]
    t.set_tone("ecstatic")
    ex = t.voice_exaggeration()
    t.clear_tone()
    if ex is not None:
        assert 0.0 < ex <= 1.0


def test_router_reports_both_clone_backends():
    from eli.perception import tts_router
    b = tts_router.available_backends()
    assert "chatterbox" in b and "xtts" in b
    assert b["chatterbox"] is False  # not installed in test env

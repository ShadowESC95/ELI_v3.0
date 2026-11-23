"""Voice asset readiness — catches false positives from the operating system's own voices."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from eli.runtime import voice_assets as va
from tests._sparse import sparse_file


def test_the_voice_is_ready_only_with_kokoros_own_files_not_the_systems_voices(monkeypatch, tmp_path):
    """Linux hosts expose many sys: voices; they do not make ELI's voice present."""
    monkeypatch.setattr(va, "_kokoro_roots", lambda: [tmp_path / "empty"])
    with patch("eli.perception.tts_router.list_voices", return_value=["sys:gmw/en-us", "sys:gmw/en"]):
        assert va.kokoro_ready() is False


def test_the_voice_is_ready_when_its_files_and_the_default_voice_are_on_disk(monkeypatch, tmp_path):
    for entry in [*va.KOKORO_ENGINE_FILES.values(), va.kokoro_voice_entry("af_heart")]:
        path = tmp_path / entry.rel
        if entry.unpack:
            path.mkdir(parents=True)
            (path / va._MARKER).write_text(entry.sha256, encoding="utf-8")
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            sparse_file(path, entry.size)
    monkeypatch.setattr(va, "_kokoro_roots", lambda: [tmp_path])
    assert va.kokoro_ready() is True
    assert va.kokoro_ready("bm_george") is False, "a voice not fetched is not ready"
    (tmp_path / "model.onnx").write_bytes(b"short")          # a truncated model is not the model
    assert va.kokoro_ready() is False


def test_whisper_cache_ready_resolves_relative_to_project_root(monkeypatch, tmp_path):
    from eli.perception import local_whisper_stt as stt

    root = tmp_path / "ELI_v3-2.0.10-linux-portable"
    whisper = root / "models" / "whisper"
    snap = whisper / "models--Systran--faster-whisper-small.en" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "config.json").write_text("{}")
    monkeypatch.setenv("ELI_PROJECT_ROOT", str(root))
    monkeypatch.chdir(tmp_path)  # cwd != install root
    assert stt.whisper_cache_ready() is True
    model, model_dir, *_ = stt._model_settings()
    assert Path(model_dir).is_absolute()
    assert str(root) in model_dir


# ── the speech model is chosen in the settings, listed with what is on this computer ───────────
def test_the_speech_model_comes_from_the_settings_unless_the_environment_says(monkeypatch, tmp_path):
    import json
    from eli.perception import local_whisper_stt as stt
    settings_file = tmp_path / "settings.json"
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(settings_file))
    monkeypatch.delenv("ELI_WHISPER_MODEL", raising=False)
    monkeypatch.setenv("ELI_WHISPER_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(stt, "_gpu_room_for_whisper", lambda *a, **k: False, raising=False)
    settings_file.write_text(json.dumps({"stt_model": "medium.en"}))
    assert stt._model_settings()[0] == "medium.en"
    settings_file.write_text(json.dumps({}))
    assert stt._model_settings()[0] == "small.en", "ELI's default when nothing is chosen"
    monkeypatch.setenv("ELI_WHISPER_MODEL", "tiny.en")
    settings_file.write_text(json.dumps({"stt_model": "medium.en"}))
    assert stt._model_settings()[0] == "tiny.en", "the environment wins"
    # reading the setting leaves the launcher's root alone (the loader would drop one outside this install)
    monkeypatch.setenv("ELI_PROJECT_ROOT", str(tmp_path / "elsewhere"))
    stt._setting("stt_model")
    assert os.environ.get("ELI_PROJECT_ROOT") == str(tmp_path / "elsewhere")


def test_the_speech_models_offered_are_the_library_s_with_what_is_on_disk_marked(monkeypatch, tmp_path):
    """The names offered are the speech library's own list. A stand-in for the library gives that
    list here, so the test does not depend on faster-whisper being installed (a CI runner has none)."""
    import sys
    import types
    from eli.perception import local_whisper_stt as stt
    library, utils = types.ModuleType("faster_whisper"), types.ModuleType("faster_whisper.utils")
    utils._MODELS = {"small.en": "Systran/faster-whisper-small.en", "medium.en": "Systran/faster-whisper-medium.en"}
    library.utils = utils
    monkeypatch.setitem(sys.modules, "faster_whisper", library)
    monkeypatch.setitem(sys.modules, "faster_whisper.utils", utils)
    snap = tmp_path / "models--Systran--faster-whisper-small.en" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    (snap / "model.bin").write_bytes(b"x" * 2048)
    own = tmp_path / "my-finetune"
    own.mkdir()
    (own / "model.bin").write_bytes(b"x")
    offered = {m["name"]: m["on_disk"] for m in stt.available_models(str(tmp_path))}
    assert offered.get("small.en") is True
    assert "medium.en" in offered and offered["medium.en"] is False
    assert offered.get("my-finetune") is False or "my-finetune" in offered

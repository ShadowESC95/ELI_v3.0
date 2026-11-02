"""Surfaces ported from v2 that v3 was missing.

Each of these had its machinery already present in v3 and only the reachable
surface absent — the same half-port shape that left "be comedic" doing nothing.
They are pinned here because absence is silent: nothing errors, the feature is
just unreachable.
"""
from __future__ import annotations

import os

import pytest


# ── Ollama auto-start ────────────────────────────────────────────────────────
def test_ensure_server_running_exists():
    from eli.integrations.ollama import client
    assert hasattr(client, "ensure_server_running"), \
        "v3 only told the user to start Ollama; the #1 'not loading' cause is that it is stopped"


def test_autostart_is_skippable(monkeypatch):
    from eli.integrations.ollama import client
    monkeypatch.setenv("ELI_OLLAMA_AUTOSTART", "0")
    monkeypatch.setattr(client, "is_running", lambda: False)
    monkeypatch.setattr(client, "_autostart_tried", False, raising=False)
    assert client.ensure_server_running() is False


def test_autostart_refuses_a_remote_host(monkeypatch):
    """A remote Ollama is the owner's to start, not ours."""
    from eli.integrations.ollama import client
    monkeypatch.delenv("ELI_OLLAMA_AUTOSTART", raising=False)
    monkeypatch.setattr(client, "is_running", lambda: False)
    monkeypatch.setattr(client, "_autostart_tried", False, raising=False)
    monkeypatch.setattr(client, "candidate_hosts", lambda *a, **k: ["http://192.168.1.50:11434"])
    assert client.ensure_server_running() is False


def test_list_models_accepts_the_autostart_switch():
    from eli.integrations.ollama.client import list_models
    assert isinstance(list_models(autostart=False), list)


# ── TTS prosody + robotic-voice filter ───────────────────────────────────────
def test_prosody_args_are_emitted_by_default(monkeypatch):
    from eli.perception.tts_router import _piper_prosody_args
    monkeypatch.delenv("ELI_TTS_PROSODY", raising=False)
    args = _piper_prosody_args()
    assert "--length_scale" in args and "--sentence_silence" in args


def test_prosody_can_be_disabled(monkeypatch):
    from eli.perception.tts_router import _piper_prosody_args
    monkeypatch.setenv("ELI_TTS_PROSODY", "0")
    assert _piper_prosody_args() == []


def test_robotic_voices_hidden_by_default(monkeypatch):
    from eli.perception.tts_router import list_voices
    monkeypatch.delenv("ELI_TTS_ALLOW_ROBOTIC", raising=False)
    voices = list_voices()
    if voices:  # only meaningful where voices are installed
        assert not any("x_low" in v for v in voices), \
            "low-quality piper voices should be deferred unless explicitly allowed"


def test_robotic_voices_can_be_opted_into(monkeypatch):
    from eli.perception.tts_router import list_voices
    monkeypatch.delenv("ELI_TTS_ALLOW_ROBOTIC", raising=False)
    natural = list_voices()
    monkeypatch.setenv("ELI_TTS_ALLOW_ROBOTIC", "1")
    everything = list_voices()
    assert len(everything) >= len(natural)


# ── Voice library dialog ─────────────────────────────────────────────────────
def test_voice_library_dialog_present():
    """voice_assets could always fetch ~166 voices; nothing exposed that to the user.

    Source-level: the suite mocks Qt, so the class is a MagicMock at import time
    and attribute checks on it prove nothing. Real construction is verified
    offscreen, not here.
    """
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "eli" / "gui" /
           "widgets" / "voice_downloader.py").read_text(encoding="utf-8")
    assert "class VoiceDownloadDialog" in src
    assert "voices_changed" in src
    assert "download_voice" in src, "the dialog must reach the real fetcher"


def test_settings_offers_the_voice_library_and_can_refresh():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent / "eli" / "gui" /
           "main_window" / "_mixins" / "settings_model.py").read_text(encoding="utf-8")
    assert "VoiceDownloadDialog" in src, "no entry point to the voice library"
    assert "_reload_voice_selector" in src, \
        "a downloaded voice must appear without reopening Settings"


# ── Licence terms ────────────────────────────────────────────────────────────
def test_license_terms_are_printable_and_distinct_from_activation():
    from eli.runtime.license_info import eula_path, license_path, license_text
    assert license_path() is not None
    assert eula_path() is not None, "the EULA must ship beside LICENSE"
    text = license_text()
    assert "PROPRIETARY COMMERCIAL SOFTWARE" in text
    assert "ELI End User Licence Agreement" in text and "## 3. Licence grant" in text, \
        "--license must print the EULA as well as the notice"


def test_license_flag_does_not_collide_with_license_status():
    """--license (terms) and --license-status (activation) must stay separate."""
    from eli.licensing.flags import LICENSE_FLAGS
    assert "--license" not in LICENSE_FLAGS, \
        "--license must fall through to the terms printer, not the activation handler"
    assert "--license-status" in LICENSE_FLAGS


def test_main_handles_the_license_flag():
    import pathlib
    src = (pathlib.Path(__file__).resolve().parent.parent /
           "eli" / "__main__.py").read_text(encoding="utf-8")
    assert '"--license" in args' in src
    assert "license_info" in src

# ── XTTS device override ─────────────────────────────────────────────────────
@pytest.mark.parametrize("forced", ["cpu", "cuda"])
def test_xtts_device_can_be_forced(monkeypatch, forced):
    """XTTS shares the GPU with the main model; pinning it to CPU must not
    require a code edit."""
    torch = pytest.importorskip("torch")
    from eli.perception.tts_xtts import _select_device
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)   # the force is checked against the build
    monkeypatch.setenv("ELI_XTTS_DEVICE", forced)
    assert _select_device() == forced


def test_xtts_device_ignores_a_bogus_value(monkeypatch):
    from eli.perception.tts_xtts import _select_device
    monkeypatch.setenv("ELI_XTTS_DEVICE", "gpu-please")
    assert _select_device() in {"cpu", "cuda"}


def test_xtts_device_autodetects_without_the_override(monkeypatch):
    from eli.perception.tts_xtts import _select_device
    monkeypatch.delenv("ELI_XTTS_DEVICE", raising=False)
    assert _select_device() in {"cpu", "cuda"}

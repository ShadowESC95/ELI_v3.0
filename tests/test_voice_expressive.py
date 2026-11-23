"""Expressive voice: the tone's delivery, hidden robotic voices, natural neural voices.

- Kokoro speaks with the tone's pace and the pause after each sentence (tts_router._prosody).
- The operating system's own `sys:` voices are hidden by default (kept as a last resort).
- `natural:` voices are Qwen3-TTS's speakers on a GPU with room for it (XTTS-v2's where that is
  installed); without either, the default Kokoro voice speaks and says so.
"""
from __future__ import annotations

import pytest

from eli.perception import tts_router as tr
from eli.perception import tts_xtts as X
from eli.runtime import voice_assets as va


# ── Delivery ──────────────────────────────────────────────────────────────────
def test_prosody_present_by_default():
    vals = tr._prosody()
    assert vals.get("sentence_silence", 0) > 0, "the pause after a full stop"
    assert "length_scale" in vals


def test_prosody_can_be_disabled(monkeypatch):
    monkeypatch.setenv("ELI_TTS_PROSODY", "0")
    assert tr._prosody() == {}


def test_env_overrides_prosody(monkeypatch):
    monkeypatch.setenv("ELI_TTS_SENTENCE_SILENCE", "0.9")
    assert tr._prosody()["sentence_silence"] == 0.9


# ── Robotic-voice removal ─────────────────────────────────────────────────────
def test_default_list_hides_the_systems_own_voices(monkeypatch):
    monkeypatch.setattr(tr, "_list_system_voices", lambda refresh=False: ["sys:gmw/en-us", "sys:gmw/en"])
    monkeypatch.delenv("ELI_TTS_ALLOW_ROBOTIC", raising=False)
    voices = tr.list_voices()
    assert not any(v.startswith("sys:") for v in voices), "the system's voices must be hidden"
    assert "af_heart" in voices
    monkeypatch.setenv("ELI_TTS_ALLOW_ROBOTIC", "1")
    assert "sys:gmw/en" in tr.list_voices()


def test_robotic_voices_restorable(monkeypatch):
    monkeypatch.setenv("ELI_TTS_ALLOW_ROBOTIC", "1")
    assert tr._allow_robotic_voices() is True


# ── Natural neural voice (XTTS-v2) ────────────────────────────────────────────
def test_natural_voice_metadata_is_static_and_cheap():
    # meta lookup must not require the TTS package or load the model
    assert X.natural_voice_meta("natural:james")["gender"] == "male"
    assert X.natural_voice_meta("natural:sophia")["gender"] == "female"


def test_list_natural_voices_empty_without_extra(monkeypatch):
    monkeypatch.setattr(X, "natural_available", lambda: False)
    assert X.list_natural_voices() == []


def test_list_natural_voices_when_available(monkeypatch):
    monkeypatch.setattr(X, "natural_available", lambda: True)
    ids = X.list_natural_voices()
    assert "natural:sophia" in ids and "natural:james" in ids


def test_natural_synth_falls_back_gracefully_without_extra(monkeypatch):
    monkeypatch.setattr(X, "natural_available", lambda: False)
    assert X.synthesize_natural_wav("hello", "natural:sophia") is None


def test_device_selection_is_forceable(monkeypatch):
    monkeypatch.setenv("ELI_XTTS_DEVICE", "cpu")
    assert X._select_device() == "cpu"


def test_a_forced_cuda_is_honoured_only_when_this_torch_has_it(monkeypatch):
    torch = pytest.importorskip("torch")
    monkeypatch.setenv("ELI_XTTS_DEVICE", "cuda")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    assert X._select_device() == "cuda"
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)   # a CPU-only wheel
    assert X._select_device() == "cpu"


# ── Routing / resolver for the natural voice ──────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("use the natural voice", "natural:sophia"),
    ("switch to a human voice", "natural:sophia"),
    ("use a realistic male voice", "natural:james"),
    ("use a natural female voice", "natural:sophia"),
])
def test_natural_voice_resolves(text, expected):
    assert va.resolve_voice_query(text)["voice"] == expected


def test_set_voice_accepts_natural_without_it_being_installed():
    from eli.execution.executor_enhanced import execute
    prev = tr.get_active_voice()
    try:
        r = execute("SET_VOICE", {"query": "use the natural voice"})
        assert r.get("ok") and r.get("voice") == "natural:sophia"
    finally:
        tr.set_active_voice(prev)


def test_natural_voice_without_a_neural_engine_speaks_with_the_default_voice_and_says_so(monkeypatch):
    """With neither Qwen3-TTS nor XTTS able to speak, a natural: voice still produces audio, the
    default Kokoro voice's, and the fallback state says the chosen voice is not what is heard."""
    from eli.perception import tts_kokoro, tts_qwen3
    monkeypatch.setattr(tts_qwen3, "synthesize_natural_wav", lambda *a, **k: None)
    monkeypatch.setattr(X, "natural_available", lambda: False)
    said = []
    monkeypatch.setattr(tts_kokoro, "synthesize_wav",
                        lambda text, voice="af_heart", speed=1.0: said.append(voice) or tts_kokoro.to_wav([0.1] * 2400))
    wav = tr.synthesize_wav("This is a test.", "natural:sophia")
    assert wav and len(wav) > 1000 and said == ["af_heart"]
    state = tr.neural_fallback_state()
    assert state["active"] and state["requested"] == "natural:sophia" and state["reason"]
    tr._note_neural_fallback(None)


# ── Voice creation engine (drop-in wav/mp4) ───────────────────────────────────
import subprocess as _sp
import shutil as _sh


def _make_audio(path, ext, freq=200):
    _sp.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=2", "-ac", "1", str(path)],
            check=True, timeout=30)


@pytest.mark.skipif(not _sh.which("ffmpeg"), reason="ffmpeg required to build a reference")
@pytest.mark.parametrize("ext", ["wav", "mp4", "mp3"])
def test_create_voice_from_dropped_in_file(ext, tmp_path, monkeypatch):
    from eli.execution.executor_enhanced import execute
    from eli.perception import tts_xtts, tts_router
    monkeypatch.setattr(tts_xtts, "_refs_dir", lambda: tmp_path)
    reg = tmp_path / "clones.json"
    monkeypatch.setattr(tts_xtts, "_registry_path", lambda: reg)
    src = tmp_path / f"sample.{ext}"
    _make_audio(src, ext)
    prev = tts_router.get_active_voice()
    try:
        r = execute("CREATE_VOICE", {"name": f"test_{ext}", "file": str(src)})
        assert r.get("ok"), r
        assert r.get("voice") == f"clone:test_{ext}"
        assert any(c["id"] == f"clone:test_{ext}" for c in tts_xtts.list_clones())
    finally:
        tts_router.set_active_voice(prev)


def test_create_voice_needs_a_file():
    from eli.execution.executor_enhanced import execute
    r = execute("CREATE_VOICE", {"name": "x"})
    assert r.get("ok") is False and "audio file" in r.get("content", "").lower()


def test_create_voice_missing_file_is_honest():
    from eli.execution.executor_enhanced import execute
    r = execute("CREATE_VOICE", {"name": "x", "file": "/no/such/clip.mp4"})
    assert r.get("ok") is False and "couldn't find" in r.get("content", "").lower()


@pytest.mark.parametrize("text", [
    "create a voice called Nova from /tmp/clip.mp4",
    "make an ELI voice from recording.wav",
    "clone this voice from my sample.mp3",
])
def test_create_voice_routes(text):
    from eli.execution.router_enhanced import route
    assert (route(text).get("action") or "").upper() == "CREATE_VOICE"


@pytest.mark.parametrize("text,file,name", [
    ("create a voice called Nova from clip.mp4", "clip.mp4", "Nova"),
    ("create a voice from my_clip.mp4 called Nova", "my_clip.mp4", "Nova"),
    ('make a voice called Nova from "my clip.mp4"', "my clip.mp4", "Nova"),
    ("create a voice named Nova from ~/Music/take two.wav", "~/Music/take two.wav", "Nova"),
    ("make a voice from /tmp/sample set/clip.mp3 called Orbit", "/tmp/sample set/clip.mp3", "Orbit"),
    ("clone my voice from recording.wav and call it Atlas", "recording.wav", "Atlas"),
    ("create a voice called Nova Prime using the file /srv/clips/take 2.wav", "/srv/clips/take 2.wav", "Nova Prime"),
    ('clone a voice from "my clip.mp4" as Echo', "my clip.mp4", "Echo"),
])
def test_create_voice_reads_the_file_and_the_name(text, file, name):
    """The file pattern took the whole sentence before an unquoted name ("create a voice from
    my_clip.mp4" as the file) and the name pattern ran on to the next quote ("Nova from")."""
    from eli.execution.router_enhanced import route
    args = route(text)["args"]
    assert args.get("file") == file and args.get("name") == name


def test_create_voice_registered_capability():
    from tests.claims import _helpers as H
    by = {c["action"]: c for c in H.capabilities()}            # the manifest this run generated
    assert "CREATE_VOICE" in by and by["CREATE_VOICE"]["routable"]

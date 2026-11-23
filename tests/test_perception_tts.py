"""Tests for eli.perception.tts_router and its default engine, eli.perception.tts_kokoro."""
from __future__ import annotations

import io
import threading
import wave
from pathlib import Path
from unittest.mock import patch
import os
import pytest

from eli.perception import tts_kokoro
from eli.perception.tts_router import (
    list_voices,
    get_active_voice,
    set_active_voice,
    available_backends,
    _DEFAULT_VOICE,
    _run_tts,
    speak_if_enabled,
)


def _wav(seconds=0.2, rate=tts_kokoro.SAMPLE_RATE):
    import numpy as np
    t = np.arange(int(rate * seconds)) / rate
    return tts_kokoro.to_wav((np.sin(2 * np.pi * 220 * t) * 0.4).astype("float32"), rate)


@pytest.fixture
def no_settings(monkeypatch, tmp_path):
    """No voice chosen anywhere: an empty settings file, no voice in the environment."""
    monkeypatch.delenv("ELI_TTS_VOICE", raising=False)
    monkeypatch.delenv("ELI_PIPER_VOICE", raising=False)
    settings = tmp_path / "settings.json"
    settings.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(settings))
    from eli.perception import tts_qwen3
    monkeypatch.setattr(tts_qwen3, "usable", lambda kind="custom": False)
    return settings


@pytest.fixture
def kokoro_here(monkeypatch, tmp_path):
    """Kokoro's voices on this machine (a file each in the test's own folder); rendering is a
    stand-in that records what it was asked to say, at which speed."""
    from eli.runtime import voice_assets
    files = {}
    for vid in tts_kokoro.VOICES:
        f = tmp_path / f"{vid}.bin"
        f.write_bytes(b"0")
        files[vid] = f
    monkeypatch.setattr(voice_assets, "kokoro_voice_file", lambda vid: files.get(vid))
    said = []
    monkeypatch.setattr(tts_kokoro, "synthesize_wav",
                        lambda text, voice=tts_kokoro.DEFAULT_VOICE, speed=1.0: said.append((text, voice, speed)) or _wav())
    return said


# ── Voices ────────────────────────────────────────────────────────────────

def test_every_kokoro_voice_is_offered_even_before_the_first_fetch(monkeypatch):
    """Before the voice's files are fetched (they come with the first reply spoken) every Kokoro
    voice is listed, so one can be chosen; after, the ones on this machine."""
    from eli.runtime import voice_assets
    monkeypatch.setattr(voice_assets, "kokoro_voice_file", lambda vid: None)
    voices = list_voices()
    assert all(v in voices for v in tts_kokoro.VOICES)
    assert len(voices) == len(set(voices)) and list_voices() == voices
    monkeypatch.setattr(voice_assets, "kokoro_voice_file", lambda vid: Path("x") if vid == "bf_emma" else None)
    listed = [v for v in list_voices() if tts_kokoro.voice_id(v)]
    assert listed == ["bf_emma"]


def test_the_default_voice_is_kokoros_best_graded(no_settings):
    assert _DEFAULT_VOICE == tts_kokoro.DEFAULT_VOICE == "af_heart"
    assert tts_kokoro.VOICES[_DEFAULT_VOICE]["grade"] == "A"
    assert get_active_voice() == "af_heart"


@pytest.mark.parametrize("saved,speaks", [
    ("en_US-amy-medium", "af_heart"), ("en_GB-alan-medium", "bm_george"),
    ("en_GB-northern_english_male-medium", "bm_lewis"), ("en_US-hfc_male-medium", "am_michael"),
    ("en_GB-some_other-medium", "bf_emma"), ("de_DE-thorsten-medium", "af_heart"),
    ("bm_fable", "bm_fable"), ("kokoro:af_bella", "af_bella"),
])
def test_a_voice_saved_for_the_earlier_engine_speaks_with_the_nearest_kokoro_voice(no_settings, saved, speaks):
    import json
    no_settings.write_text(json.dumps({"tts_voice": saved}), encoding="utf-8")
    assert get_active_voice() == speaks


def test_the_chosen_voice_comes_from_the_environment_then_the_settings(no_settings, monkeypatch):
    monkeypatch.setenv("ELI_TTS_VOICE", "bm_george")
    assert get_active_voice() == "bm_george"
    monkeypatch.delenv("ELI_TTS_VOICE")
    set_active_voice("bf_isabella")
    assert get_active_voice() == "bf_isabella"
    set_active_voice("char:calm")
    assert get_active_voice() == "char:calm"


def test_on_a_machine_whose_gpu_runs_qwen3_the_automatic_engine_speaks_with_its_natural_voice(no_settings, monkeypatch):
    import json
    from eli.perception import tts_qwen3
    monkeypatch.setattr(tts_qwen3, "usable", lambda kind="custom": True)
    assert get_active_voice() == "natural:serena"
    no_settings.write_text(json.dumps({"tts_engine": "kokoro"}), encoding="utf-8")
    assert get_active_voice() == "af_heart", "Kokoro chosen: the GPU voice is not used"
    no_settings.write_text(json.dumps({"tts_engine": "piper"}), encoding="utf-8")
    assert get_active_voice() == "natural:serena", "the earlier engine, every install's default, reads as automatic"


def test_a_user_who_kept_the_old_default_voice_gets_the_automatic_one_and_a_chosen_voice_is_kept(no_settings, monkeypatch):
    """Every settings file held tts_voice en_US-amy-medium, the earlier engine's default: it is nobody's
    choice, so it reads as none and the automatic engine speaks; a voice the user picked stays theirs."""
    import json
    from eli.perception import tts_qwen3
    monkeypatch.setattr(tts_qwen3, "usable", lambda kind="custom": True)
    no_settings.write_text(json.dumps({"tts_voice": "en_US-amy-medium", "tts_engine": "piper"}), encoding="utf-8")
    assert get_active_voice() == "natural:serena"
    monkeypatch.setattr(tts_qwen3, "usable", lambda kind="custom": False)
    assert get_active_voice() == "af_heart", "a CPU-only machine keeps Kokoro"
    monkeypatch.setattr(tts_qwen3, "usable", lambda kind="custom": True)
    no_settings.write_text(json.dumps({"tts_voice": "en_GB-alan-medium"}), encoding="utf-8")
    assert get_active_voice() == "bm_george", "a voice the user chose is kept"


def test_where_the_gpu_has_room_the_runtime_is_made_in_the_background_once_and_only_when_online(no_settings, monkeypatch):
    from eli.core import netguard
    from eli.perception import qwen3_runtime, tts_qwen3
    from eli.perception import tts_router as tr
    asked = []
    monkeypatch.setattr(tts_qwen3, "installed", lambda: False)
    monkeypatch.setattr(qwen3_runtime, "make_if_room", lambda background=False: asked.append(background) or {})
    monkeypatch.setattr(netguard, "should_block_network", lambda: True)
    monkeypatch.setattr(tr, "_RUNTIME_ASKED", False)
    assert get_active_voice() == "af_heart" and asked == [], "fetched with the network switched off"
    monkeypatch.setattr(netguard, "should_block_network", lambda: False)
    monkeypatch.setattr(tr, "_RUNTIME_ASKED", False)
    get_active_voice()
    get_active_voice()
    assert asked == [True], "not made in the background, or asked more than once a run"


def test_the_backends_say_which_engine_speaks_and_whether_its_files_are_here(no_settings, monkeypatch, tmp_path):
    from eli.runtime import voice_assets
    monkeypatch.setattr(voice_assets, "kokoro_files", lambda: None)
    b = available_backends()
    assert b["engine"] == "kokoro" and b["kokoro_ready"] is False and b["active_model"] is None
    assert b["active_voice"] == b["speaking_voice"] == "af_heart" and isinstance(b["voices"], list)
    model = tmp_path / "model.onnx"
    model.write_bytes(b"m")
    monkeypatch.setattr(voice_assets, "kokoro_files", lambda: {"model": model})
    b = available_backends()
    assert b["kokoro_ready"] is True and b["active_model"] == str(model)
    for key in ("piper_bin", "piper_voices", "espeak_ng", "espeak"):
        assert key not in b


# ── speak_if_enabled ─────────────────────────────────────────────────────

def test_speak_if_enabled_false_does_not_speak():
    with patch("eli.perception.tts_router._run_tts") as mock_tts:
        speak_if_enabled("hello", enabled=False)
        mock_tts.assert_not_called()

def test_speak_if_enabled_true_calls_tts():
    """It asserted nothing: the reply is spoken on a thread of its own and the call says it started."""
    spoken = threading.Event()
    with patch("eli.perception.tts_router._run_tts",
               side_effect=lambda text, voice_name=None: spoken.set() or True) as mock_tts:
        assert speak_if_enabled("hello", enabled=True, voice_name="any-voice") is True
        assert spoken.wait(5), "nothing was spoken"
    mock_tts.assert_called_once_with("hello", voice_name="any-voice")


@pytest.mark.parametrize("blank", ["", "   ", None, "...", "-", "\n\t"])
def test_text_with_nothing_to_say_is_not_spoken_and_says_so(blank):
    """Blank text is shown as "...", which was handed on to be spoken: speak_if_enabled said it was
    speaking (True) while the speech layer refused it."""
    from eli.perception.tts_router import speak
    with patch("eli.perception.tts_router._run_tts", return_value=True) as mock_tts:
        assert speak_if_enabled(blank, enabled=True) is False
        assert speak(blank) is False
    mock_tts.assert_not_called()


# ── Speaking: every sentence, the next rendered while one plays ──────────────

def _record_playback(monkeypatch, *, ok=True, hold=None):
    """A stand-in for playing a WAV: records each file's length and the tail asked for."""
    from eli.perception import tts_router as tr
    played = []

    def _play(path, *, post_play_tail=None):
        with wave.open(str(path)) as w:
            played.append((w.getnframes() / w.getframerate(), post_play_tail))
        if hold is not None:
            hold(len(played))
        return tr.Playback(ok, "a stand-in player" if ok else "", "" if ok else "the stand-in refused")
    monkeypatch.setattr(tr, "_play_wav_blocking", _play)
    return played


def test_every_sentence_is_spoken_with_the_tones_pause_and_the_tail_only_after_the_last(
        kokoro_here, monkeypatch, tmp_path):
    from eli.cognition import tone_adaptor
    monkeypatch.setenv("ELI_TTS_SPEAKING_LOCK", str(tmp_path / "speaking.lock"))
    monkeypatch.setattr(tone_adaptor, "voice_prosody", lambda: {"length_scale": 1.25, "sentence_silence": 0.5})
    played = _record_playback(monkeypatch)
    text = "One short sentence. " * 5 + "And the last one."

    result = _run_tts(text, voice_name="bm_george")

    assert result and result.via == "a stand-in player"
    assert [s for s, _v, _sp in kokoro_here] == ["One short sentence."] * 5 + ["And the last one."]
    assert {v for _s, v, _sp in kokoro_here} == {"bm_george"}
    assert all(abs(sp - 0.8) < 1e-9 for _s, _v, sp in kokoro_here), "the tone's pace is Kokoro's speed"
    assert [tail for _d, tail in played] == [0.0] * 5 + [None], "the player's tail only after the last"
    assert all(abs(d - 0.7) < 0.01 for d, _t in played[:-1]) and abs(played[-1][0] - 0.2) < 0.01, \
        "the tone's pause follows every sentence but the last"


def test_the_next_sentence_is_rendered_while_one_plays(kokoro_here, monkeypatch, tmp_path):
    monkeypatch.setenv("ELI_TTS_SPEAKING_LOCK", str(tmp_path / "speaking.lock"))
    monkeypatch.setenv("ELI_TTS_PROSODY", "0")
    second_rendered = threading.Event()
    real = tts_kokoro.synthesize_wav

    def _render(text, voice=tts_kokoro.DEFAULT_VOICE, speed=1.0):
        if text.startswith("Second"):
            second_rendered.set()
        return real(text, voice, speed=speed)
    monkeypatch.setattr(tts_kokoro, "synthesize_wav", _render)
    seen_during_first = []
    _record_playback(monkeypatch, hold=lambda n: n == 1 and seen_during_first.append(second_rendered.wait(5)))

    assert _run_tts("First sentence here. Second sentence here.", voice_name="af_heart")
    assert seen_during_first == [True], "the second sentence waited for the first to finish playing"


def test_a_reply_kokoro_cannot_render_is_reported_not_played_with_the_reason(monkeypatch, tmp_path):
    from eli.perception import tts_router as tr
    monkeypatch.setenv("ELI_TTS_SPEAKING_LOCK", str(tmp_path / "speaking.lock"))
    monkeypatch.setattr(tts_kokoro, "synthesize_wav", lambda *a, **k: None)
    monkeypatch.setattr(tts_kokoro, "_LOAD_ERROR", "RuntimeError: its files are not on this machine")
    played = _record_playback(monkeypatch)
    result = tr.speak_text("Nothing will be heard.", voice="af_heart")
    assert result["ok"] is False and "not on this machine" in result["error"] and not played


def test_a_render_that_raises_ends_the_reply_instead_of_leaving_it_waiting(monkeypatch, tmp_path):
    """The rendering thread hands each sentence to playback; one that raised left playback waiting
    for a sentence that never came, holding the speaking lock."""
    from eli.perception import tts_router as tr
    monkeypatch.setenv("ELI_TTS_SPEAKING_LOCK", str(tmp_path / "speaking.lock"))

    def _boom(*a, **k):
        raise RuntimeError("the engine fell over")
    monkeypatch.setattr(tr, "_render_piece", _boom)
    _record_playback(monkeypatch)
    done = []
    worker = threading.Thread(target=lambda: done.append(tr._run_tts("One. Two.", voice_name="af_heart")))
    worker.start()
    worker.join(10)
    assert done and not done[0], "the reply hung or claimed it was spoken"
    assert not (tmp_path / "speaking.lock").exists()


def test_the_browser_voice_gets_every_sentence_in_one_wav_with_the_pauses(kokoro_here, monkeypatch):
    from eli.perception import tts_router as tr
    from eli.cognition import tone_adaptor
    monkeypatch.setattr(tone_adaptor, "voice_prosody", lambda: {"sentence_silence": 0.3})
    wav = tr.synthesize_wav("Hello there. How are you today?", voice_name="af_heart")
    with wave.open(io.BytesIO(wav)) as w:
        assert abs(w.getnframes() / w.getframerate() - (0.2 + 0.3 + 0.2)) < 0.01
    assert [s for s, _v, _sp in kokoro_here] == ["Hello there.", "How are you today?"]


def test_a_style_voice_is_its_base_kokoro_voice_through_its_effects(kokoro_here, monkeypatch):
    from eli.perception import tts_router as tr, voice_fx
    applied = []
    monkeypatch.setattr(voice_fx, "apply_fx", lambda wav, spec: applied.append(spec["base"]) or wav)
    assert tr.synthesize_wav("A calm reply.", voice_name="char:calm")
    assert applied == ["bm_george"] and kokoro_here[0][1] == "bm_george"


# ── Delivery ─────────────────────────────────────────────────────────────────

def test_kokoro_speaks_with_the_palettes_neutral_voice(monkeypatch):
    """The prosody figures were typed out here and in the tone palette; the palette's pace is
    Kokoro's speed, inverted."""
    from eli.cognition.emotion_palette import NEUTRAL_VOICE
    from eli.perception import tts_router
    assert tts_router._PROSODY_DEFAULTS == {k: v for k, v in NEUTRAL_VOICE.items() if k != "pitch"}
    source = Path(tts_router.__file__).read_text(encoding="utf-8")
    assert '"noise_scale": 0.72' not in source
    assert abs(tts_router._speed({"length_scale": 0.8}) - 1.25) < 1e-9
    assert tts_router._speed({}) == 1.0
    monkeypatch.setenv("ELI_TTS_PROSODY", "0")
    assert tts_router._prosody() == {}


# ── Never speak a degenerate fragment ─────────────────────────────────────────

@pytest.mark.parametrize("frag", ["-", "-G", "-Auto", "-Auto/G 5/", "", "   ", "/ "])
def test_tts_refuses_unspeakable_fragment(frag):
    from eli.perception.tts_router import _eli_tts_is_unspeakable
    assert _eli_tts_is_unspeakable(frag) is True


@pytest.mark.parametrize("ok", ["No.", "34G", "Volume down", "I'm ELI.",
                                "Your first message was at 00:03:44 on 2026-06-06."])
def test_tts_speaks_real_short_text(ok):
    from eli.perception.tts_router import _eli_tts_is_unspeakable
    assert _eli_tts_is_unspeakable(ok) is False


def test_the_speaking_lock_is_held_for_the_whole_reply(tmp_path, monkeypatch):
    """Only one engine set it, and only per chunk: other voices, and the gaps between chunks, reached the mic."""
    import eli.perception.tts_router as tr
    lock = tmp_path / "speaking.lock"
    monkeypatch.setenv("ELI_TTS_SPEAKING_LOCK", str(lock))
    seen = []
    monkeypatch.setattr(tr, "_run_tts_impl", lambda text, voice_name=None: seen.append(lock.read_text()) or True)
    assert tr._run_tts("hello there", voice_name="any") is True
    assert seen == [str(os.getpid())] and not lock.exists()


# ── The Kokoro engine ────────────────────────────────────────────────────────

def test_misaki_is_built_with_every_attribute_its_own_constructor_sets():
    """ELI builds misaki's G2P without its constructor, which installs a missing spaCy model with pip
    at run time; what it sets must stay what misaki's constructor sets."""
    import inspect
    import re
    en = pytest.importorskip("misaki.en")
    theirs = set(re.findall(r"self\.(\w+)\s*=", inspect.getsource(en.G2P.__init__)))
    g = tts_kokoro._misaki_g2p(object(), False, lambda lexicon: "fallback")
    assert theirs and theirs <= set(vars(g)), f"misaki sets {sorted(theirs - set(vars(g)))} too"
    assert g.fallback == "fallback" and g.unk == "" and g.british is False


class _Lexicon:
    golds = {"glimmer": "ɡlˈɪməɹ", "brook": "bɹˈʊk", "A": "ˈA", "B": "bˈi"}
    silvers = {"is": "ɪz"}

    def get_NNP(self, word):
        ps = [self.golds.get(c.upper()) for c in word]
        return ("".join(ps), 3) if None not in ps else (None, None)


class _Token:
    def __init__(self, text):
        self.text = text


def test_a_word_misaki_does_not_know_is_guessed_never_dropped(monkeypatch):
    pytest.importorskip("misaki.en")
    guessed = []

    def _crf(word):
        guessed.append(word)
        return ["z", "ˈɑ", "ɹ", "v", "ə", "n", "t", "ə"]
    fb = tts_kokoro._OovFallback(_Lexicon(), _crf, british=False)
    assert fb(_Token("AB")) == ("ˈAbˈi", 1), "an all-capitals word is read letter by letter"
    assert fb(_Token("glimmerbrook"))[0] == "ɡlˈɪməɹbɹˈʊk", "two dictionary words are read as those words"
    assert fb(_Token("Zorvanta"))[0] == "zˈɑɹvəntə" and guessed == ["zorvanta"]
    assert fb(_Token("Здравствуйте")) == (None, None), "a word in another script is not guessed as English"
    nothing = tts_kokoro._OovFallback(_Lexicon(), None, british=False)
    assert nothing(_Token("ab"))[0] == "ˈAbˈi", "without the guessing model it is spelt, not dropped"


def test_the_guessing_models_ipa_becomes_kokoros_letters():
    us = tts_kokoro.ipa_to_kokoro(["ˈaɪ", "d͡ʒ", "ˈoʊ", "t͡ʃ", "eɪ", "ˈɚ", "ɚ", "ˈæ"], british=False)
    assert us == "ˈIʤˈOʧAˈɜɹəɹˈæ"
    gb = tts_kokoro.ipa_to_kokoro(["k", "ˈɑ", "ɹ", "ˈoʊ", "ˈæ", "ɚ"], british=True)
    assert gb == "kˈɑːˈQˈaə", "British: no r after a vowel, the GOAT and TRAP vowels misaki uses"
    from misaki import en
    assert set(us) <= set(en.US_VOCAB) | set("ˈˌ") and set(gb) <= set(en.GB_VOCAB)


def test_phonemes_longer_than_the_model_takes_are_cut_after_a_mark_or_at_a_space():
    pieces = tts_kokoro._split_phonemes("ab cd, ef gh. ij kl mn", 9)
    assert pieces == ["ab cd,", "ef gh.", "ij kl mn"] and all(len(p) <= 9 for p in pieces)
    assert tts_kokoro._split_phonemes("abcdefghijkl", 5) == ["abcde", "fghij", "kl"]


def test_the_model_is_fed_as_kokoro_takes_it(monkeypatch, tmp_path):
    """ids padded with 0 at both ends, the style row for that many phonemes (the reference
    package's pack[len(ps) - 1]), the speed as one float."""
    import json
    import sys
    import types
    import numpy as np
    from eli.runtime import voice_assets
    feeds = []

    class _Session:
        def __init__(self, path, sess_options=None, providers=None):
            assert providers == ["CPUExecutionProvider"]

        def run(self, _outputs, feed):
            feeds.append(feed)
            return [np.zeros((1, 2400), dtype=np.float32)]
    monkeypatch.setitem(sys.modules, "onnxruntime", types.SimpleNamespace(
        InferenceSession=_Session, SessionOptions=lambda: types.SimpleNamespace()))
    (tmp_path / "tokenizer.json").write_text(json.dumps({"model": {"vocab": {"a": 43, "b": 44, " ": 16}}}))
    pack = np.arange(16 * 256, dtype=np.float32).reshape(16, 1, 256)
    pack.tofile(tmp_path / "v.bin")
    monkeypatch.setattr(voice_assets, "kokoro_voice_file", lambda vid: tmp_path / "v.bin")
    eng = tts_kokoro._Engine({"model": tmp_path / "m.onnx", "tokenizer": tmp_path / "tokenizer.json"})
    monkeypatch.setattr(eng, "phonemes", lambda text, british: "ab ba❓")
    monkeypatch.setattr(tts_kokoro, "_ENGINE", eng)
    out = tts_kokoro.synthesize("ignored", "af_heart", speed=1.25)
    assert out is not None and len(out) == 2400
    (feed,) = feeds
    assert feed["input_ids"].tolist() == [[0, 43, 44, 16, 44, 43, 0]], "the unknown mark is dropped"
    assert np.array_equal(feed["style"], pack[4]) and feed["speed"].tolist() == [1.25]


def test_without_its_files_and_offline_the_engine_says_why_and_makes_no_folder(monkeypatch, tmp_path):
    from eli.runtime import voice_assets
    monkeypatch.setattr(tts_kokoro, "_ENGINE", None)
    monkeypatch.setattr(voice_assets, "kokoro_dir", lambda: tmp_path / "models" / "tts" / "kokoro")
    monkeypatch.setattr(voice_assets, "_kokoro_roots", lambda: [tmp_path / "models" / "tts" / "kokoro"])
    assert tts_kokoro.synthesize_wav("Hello.", "af_heart") is None
    assert tts_kokoro.load_error(), "nothing said why the voice did not load"
    assert not (tmp_path / "models").exists(), "a refused download made a folder"


# ── Playback goes through the desktop's sound server ─────────────────────────
def _tone_wav(path, rate=22050):
    import wave
    import numpy as np
    t = np.arange(rate // 2) / rate
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes((np.sin(2 * np.pi * 220 * t) * 12000).astype("<i2").tobytes())
    return path


def _portaudio(monkeypatch, devices, default_output=0, refuse=()):
    """A stand-in sounddevice listing `devices`; streams opened are recorded, none plays."""
    import sys
    import types

    class PortAudioError(Exception):
        pass

    opened = []

    class _Stream:
        def __init__(self, samplerate, channels, callback, device=None):
            name = devices[default_output if device is None else device]["name"]
            opened.append(name)
            if name in refuse:
                raise PortAudioError("Error opening OutputStream: Device unavailable [PaErrorCode -9985]")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def query_devices(device=None, kind=None):
        if kind == "output":
            return devices[default_output]
        return devices[device] if device is not None else list(devices)

    fake = types.SimpleNamespace(OutputStream=_Stream, PortAudioError=PortAudioError, CallbackStop=Exception,
                                 query_devices=query_devices, query_hostapis=lambda: [{"name": "ALSA"}],
                                 sleep=lambda ms: None)
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    return opened


def _dev(name, out=2):
    return {"name": name, "max_output_channels": out, "max_input_channels": 0,
            "default_samplerate": 48000.0, "hostapi": 0}


_CARDS_ONLY = [_dev("HDA Intel PCH: ALC1220 Analog (hw:0,0)", 6), _dev("HDA Intel PCH: ALC1220 Digital (hw:0,1)"),
               _dev("HDA NVidia: HDMI 0 (hw:1,3)", 8)]


def _player(monkeypatch, name="pw-play", amplitudes=None):
    """The system player stand-in: says which player played, plays nothing; meanwhile the
    lip-sync amplitude is recorded."""
    import time
    from eli.perception import tts_router
    from eli.cognition import expression_state
    played = []
    if amplitudes is not None:
        monkeypatch.setattr(expression_state, "set_amplitude", lambda v: amplitudes.append(float(v)))

    def _play(path, post_play_tail=None):
        played.append(str(path))
        time.sleep(0.15)                    # the player's own time: lip-sync runs meanwhile
        return name
    monkeypatch.setattr(tts_router, "_platform_play_wav", _play)
    return played


def test_speech_never_opens_a_sound_card_the_sound_server_holds(tmp_path, monkeypatch):
    """The AppImage's PortAudio listed only the cards, its default the raw "hw:0,0" PipeWire holds:
    opening it failed with "Device unavailable" while music played, and took the voice silently
    otherwise, while the log said every reply was spoken. With no device of the server's listed,
    the system player plays, through the server, and the voice's loudness still drives lip-sync."""
    from eli.perception import tts_router
    from eli.utils import platform_compat
    monkeypatch.setattr(platform_compat, "sound_server", lambda: "pipewire")
    opened = _portaudio(monkeypatch, _CARDS_ONLY)
    amps = []
    played = _player(monkeypatch, "pw-play", amps)

    result = tts_router._play_wav_blocking(_tone_wav(tmp_path / "v.wav"), post_play_tail=0)

    assert opened == [], f"a sound card the server holds was opened: {opened}"
    assert result and played
    assert "pw-play" in result.via
    assert max(amps) > 0.1 and amps[-1] == 0.0, "lip-sync got no loudness from the samples"
    assert tts_router.last_playback()["via"] == result.via


def test_speech_plays_on_the_sound_servers_own_portaudio_device(tmp_path, monkeypatch):
    from eli.perception import tts_router
    from eli.utils import platform_compat
    monkeypatch.setattr(platform_compat, "sound_server", lambda: "pipewire")
    devices = _CARDS_ONLY + [_dev("sysdefault", 128), _dev("pulse", 32), _dev("pipewire", 64), _dev("default", 64)]
    opened = _portaudio(monkeypatch, devices)
    played = _player(monkeypatch)

    result = tts_router._play_wav_blocking(_tone_wav(tmp_path / "v.wav"), post_play_tail=0)

    assert opened == ["pipewire"] and not played
    assert result and "pipewire" in result.via


def test_a_refused_server_device_falls_to_the_system_player_and_says_so(tmp_path, monkeypatch):
    from eli.perception import tts_router
    from eli.utils import platform_compat
    monkeypatch.setattr(platform_compat, "sound_server", lambda: "pulseaudio")
    opened = _portaudio(monkeypatch, _CARDS_ONLY + [_dev("pulse", 32)], refuse=("pulse",))
    played = _player(monkeypatch, "paplay")

    result = tts_router._play_wav_blocking(_tone_wav(tmp_path / "v.wav"), post_play_tail=0)

    assert opened == ["pulse"] and played
    assert result and "paplay" in result.via and "Device unavailable" in result.detail


def test_speech_that_could_not_be_played_is_reported_as_not_played(tmp_path, monkeypatch, caplog):
    """The log said "voice=… bytes=…" for every reply, heard or not."""
    import logging
    from eli.perception import tts_router
    from eli.utils import platform_compat
    monkeypatch.setattr(platform_compat, "sound_server", lambda: "pipewire")
    _portaudio(monkeypatch, _CARDS_ONLY)
    _player(monkeypatch, "")
    wav = _tone_wav(tmp_path / "v.wav")

    result = tts_router._play_wav_blocking(wav, post_play_tail=0)
    assert not result and "no system player" in result.detail
    with caplog.at_level(logging.WARNING, logger=tts_router.log.name):
        tts_router._log_played("[TTS] voice=v", 1234, result)
    assert "NOT played" in caplog.text and "no system player" in caplog.text

    monkeypatch.setattr(tts_router, "_eli_tts_is_unspeakable", lambda text: False)
    monkeypatch.setattr(tts_router, "_speak_kokoro", lambda text, voice: result)
    monkeypatch.setenv("ELI_TTS_SPEAKING_LOCK", str(tmp_path / "speaking.lock"))
    said = tts_router.speak_text("A reply that was never heard.", voice="af_heart")
    assert said["ok"] is False and "no system player" in said["error"]


def test_without_a_sound_server_portaudio_plays_on_its_default(tmp_path, monkeypatch):
    from eli.perception import tts_router
    from eli.utils import platform_compat
    monkeypatch.setattr(platform_compat, "sound_server", lambda: "")
    opened = _portaudio(monkeypatch, _CARDS_ONLY + [_dev("default", 64)], default_output=3)
    _player(monkeypatch)
    result = tts_router._play_wav_blocking(_tone_wav(tmp_path / "v.wav"), post_play_tail=0)
    assert opened == ["default"] and result


def test_the_sound_server_is_the_socket_a_client_would_reach(tmp_path, monkeypatch):
    """A session variable that names a socket which is not there is no server (the test run's own
    dead PULSE_SERVER / PIPEWIRE_REMOTE)."""
    import socket
    from eli.utils import platform_compat as pc
    if not pc.LINUX:
        pytest.skip("Linux sound servers")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("PIPEWIRE_REMOTE", str(tmp_path / "gone"))
    monkeypatch.setenv("PULSE_SERVER", "unix:" + str(tmp_path / "gone-too"))
    assert pc.sound_server() == ""
    s = socket.socket(socket.AF_UNIX)
    try:
        s.bind(str(tmp_path / "pipewire-0"))
        monkeypatch.delenv("PIPEWIRE_REMOTE")
        assert pc.sound_server() == "pipewire"
        monkeypatch.setattr(pc.shutil, "which", lambda name: f"/usr/bin/{name}")
        assert [p.rsplit("/", 1)[-1] for p in pc.wav_player_candidates()] == ["pw-play", "paplay", "aplay"]
    finally:
        s.close()

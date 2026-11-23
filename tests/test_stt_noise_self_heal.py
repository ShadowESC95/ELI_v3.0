"""The microphone's speech gate follows the room, and ELI hears its owner over its own music.

The failure first guarded here: when the room's noise floor sat ABOVE the energy threshold, the
phrase detector could never observe the silence it needs to END a phrase. Every cycle ran to the
phrase cap, captured seconds of noise, transcribed to nothing, and repeated — the microphone was
functionally dead and nothing anywhere said so. Measured on a real machine: speaker bleed from
music lifted the mic from 456 RMS to 7924, putting 85% of frames over the fixed 1200 threshold.

A self-heal raised the gate after three such captures and walked it back after ten quiet ones,
beside a recalibration every 60 silent cycles and a fixed start. The gate is now the room's own
level (eli/perception/noise_floor.py), measured from every chunk the microphone delivers: the
median plus Z robust deviations of the room's log level over its last LEARN_S, without the words
the owner said to ELI or ELI's own voice in it.
"""
import math
import types

import numpy as np
import pytest

from eli.perception import audio_stt
from eli.perception.noise_floor import NoiseFloor
from tests import _audio_fixtures as fx

_CHUNK = 1024                       # the listener's capture chunk at 16 kHz: 64 ms
_CHUNK_S = _CHUNK / fx.SR
_PHRASE_CHUNKS = math.ceil(0.2 / _CHUNK_S)       # SpeechRecognition's phrase_threshold, 0.2 s


def _chunk_rms(x):
    n = len(x) // _CHUNK
    return np.sqrt(np.mean(x[: n * _CHUNK].reshape(n, _CHUNK) ** 2, axis=1))


def _hear(floor, x, t0=0.0, *, own=False):
    """Every chunk of `x` (16-bit units) into the tracker, as the mic reader hands them over; the gate
    the listener would have applied at each chunk."""
    gates = []
    for i, rms in enumerate(_chunk_rms(x)):
        floor.push(rms, _CHUNK_S, t0 + (i + 1) * _CHUNK_S, own_voice=own, samples=_CHUNK)
        gates.append(floor.threshold(0.35) or 1200.0)
    return np.array(gates)


_GAP_CHUNKS = math.ceil(0.5 / _CHUNK_S)          # the silence that ends a phrase, ELI_STT_SHORT_PAUSE


def _phrase_starts(levels, gates):
    """Phrases SpeechRecognition would have kept: begun on a chunk over the gate, ended by a silence
    longer than the end-of-phrase pause, with a phrase's worth of chunks over the gate in them."""
    starts, loud, quiet, counted, open_ = 0, 0, 0, False, False
    for lv, g in zip(levels, gates):
        if lv > g:
            open_, loud, quiet = True, loud + 1, 0
            if loud >= _PHRASE_CHUNKS and not counted:
                starts, counted = starts + 1, True
        elif open_:
            quiet += 1
            if quiet > _GAP_CHUNKS:
                open_, loud, quiet, counted = False, 0, 0, False
    return starts


def test_the_gate_rises_above_the_music_the_mic_hears():
    """The incident's numbers: the mic at 456 RMS in a quiet room, then music's bleed at 7924."""
    floor = NoiseFloor()
    rng = np.random.default_rng(0)
    _hear(floor, rng.standard_normal(fx.SR * 12) * 456)
    quiet_gate = floor.threshold()
    bleed = fx.int16_rms(fx.through_the_room(fx.music(40.0, 1), 1), 7924)
    gates = _hear(floor, bleed, 12.0)
    assert floor.threshold() > 7924 > quiet_gate
    after = int(12 / _CHUNK_S)            # once the music has filled the room's history
    starts = _phrase_starts(_chunk_rms(bleed)[after:], gates[after:])
    assert starts <= 2, f"{starts} phrases started on music alone in {40 - 12:.0f} s"


def test_the_gate_comes_back_down_when_the_room_goes_quiet():
    floor = NoiseFloor()
    rng = np.random.default_rng(1)
    _hear(floor, rng.standard_normal(fx.SR * 12) * 456)
    quiet_gate = floor.threshold()
    _hear(floor, fx.int16_rms(fx.music(20.0, 2), 7924), 12.0)
    _hear(floor, rng.standard_normal(fx.SR * 14) * 456, 32.0)
    assert floor.threshold() == pytest.approx(quiet_gate, rel=0.25)
    assert not floor.louder_than_usual()


def test_a_level_just_above_the_gate_does_not_ratchet_it_up():
    """A steady room a little louder than before moves the gate to it, once, and no further."""
    floor = NoiseFloor()
    rng = np.random.default_rng(2)
    _hear(floor, rng.standard_normal(fx.SR * 12) * 456)
    gates = _hear(floor, rng.standard_normal(fx.SR * 30) * 600, 12.0)
    assert gates[-1] == pytest.approx(gates[-int(5 / _CHUNK_S)], rel=0.05)


def test_the_owners_words_and_elis_voice_are_not_the_room():
    floor = NoiseFloor()
    rng = np.random.default_rng(3)
    _hear(floor, rng.standard_normal(fx.SR * 12) * 300)
    gate = floor.threshold()
    talk = fx.int16_rms(fx.voice(rng, 6.0, 118.0, (0.15, 0.25)), 3000)
    _hear(floor, talk, 12.0)
    floor.mark_speech(12.0, 18.0)                       # the listener took it as the owner's words
    _hear(floor, fx.int16_rms(fx.voice(rng, 6.0, 190.0, (0.12, 0.2)), 3000), 18.0, own=True)   # ELI
    assert floor.threshold() == pytest.approx(gate, rel=0.1)
    assert floor.level() > gate


def test_before_the_room_is_heard_the_configured_start_holds_and_a_hand_set_gate_is_kept(monkeypatch):
    stt = audio_stt.ELIAudioSTT.__new__(audio_stt.ELIAudioSTT)
    stt.recognizer = types.SimpleNamespace(energy_threshold=1200.0, non_speaking_duration=0.35)
    stt._floor, stt._gate_manual = NoiseFloor(), None
    assert stt._apply_gate() == 1200.0
    _hear(stt._floor, np.random.default_rng(4).standard_normal(fx.SR * 2) * 200)
    assert stt._apply_gate() < 1200.0
    stt.set_sensitivity(False, 2500)
    assert stt._apply_gate() == 2500.0 and stt.level_meter()["manual"] == 2500.0
    stt.set_sensitivity(True)
    assert stt._apply_gate() < 1200.0
    monkeypatch.setenv("ELI_STT_SENSITIVITY", "manual")
    monkeypatch.setenv("ELI_STT_ENERGY_THRESHOLD", "1800")
    assert audio_stt._manual_gate() == 1800.0
    monkeypatch.setenv("ELI_STT_SENSITIVITY", "auto")
    assert audio_stt._manual_gate() is None


# Each room's level at the mic (16-bit RMS); the owner speaks 6 and 12 dB louder than the room.
_ROOMS = {
    "quiet room": (lambda s: np.random.default_rng(s).standard_normal(fx.SR * 60), 120),
    "fan": (lambda s: fx.fan(60.0, s), 600),
    "television": (lambda s: fx.through_the_room(fx.television(60.0, s), s), 1500),
    "music": (lambda s: fx.through_the_room(fx.music(60.0, s), s), 7924),
}
def _room_measurement(name, owner_db, seed=5):
    make, level = _ROOMS[name]
    room = fx.int16_rms(make(seed), level)
    floor = NoiseFloor()
    gates = _hear(floor, room)
    settled = int(12 / _CHUNK_S)
    false_starts = _phrase_starts(_chunk_rms(room)[settled:], gates[settled:])
    rng = np.random.default_rng(seed + 100)
    heard = said = 0
    for k in range(10):
        start = int((14 + 4.5 * k) * fx.SR)
        u = fx.int16_rms(fx.voice(rng, 1.6, 118.0, (0.15, 0.25)), level * 10 ** (owner_db / 20))
        mix = room[start:start + len(u)] + u
        n = len(u) // _CHUNK
        gate = gates[start // _CHUNK: start // _CHUNK + n]
        heard += _phrase_starts(_chunk_rms(mix), gate) > 0
        said += 1
    minutes = (len(room) / fx.SR - 12) / 60.0
    return false_starts / minutes, heard, said


@pytest.mark.parametrize("name", list(_ROOMS))
def test_the_gate_in_each_room(name):
    """Measured (tests/_audio_fixtures): phrases started on the room alone, per minute, and the
    owner's utterances that started one, said 6 and 12 dB louder than the room's average. A
    television or music has stretches louder than its average: a voice not louder than those is
    not told from them by level (near_speech does it over ELI's own music)."""
    per_min, heard6, said = _room_measurement(name, 6)
    _, heard12, _ = _room_measurement(name, 12)
    print(f"[ROOM] {name}: {per_min:.1f} false phrase starts/min; owner heard {heard6}/{said} at +6 dB, "
          f"{heard12}/{said} at +12 dB")
    assert per_min <= 2.0, name
    assert heard12 >= said - 1, name
    if name in ("quiet room", "fan"):
        assert heard6 >= said - 1, name


def test_the_speaking_lock_holds_for_as_long_as_the_voice_speaks(tmp_path):
    """Taken as stale after 30 seconds, a minute-long reply was heard by the mic and transcribed."""
    import os
    import subprocess
    import sys
    import time
    from eli.perception.audio_stt import speaking_lock_held
    lock = tmp_path / "speaking.lock"
    assert not speaking_lock_held(lock)
    lock.write_text(str(os.getpid()))
    long_ago = time.time() - 3600
    os.utime(lock, (long_ago, long_ago))
    assert speaking_lock_held(lock), "a long reply is still being spoken"
    ended = subprocess.run([sys.executable, "-c", "import os; print(os.getpid())"],
                           capture_output=True, text=True, check=True).stdout.strip()
    lock.write_text(ended)
    assert not speaking_lock_held(lock) and not lock.exists(), "left by a process that has ended"


def test_a_voice_command_said_during_a_reply_is_sent_after_it(monkeypatch):
    """"computer, one more" said while a reply was coming was dropped. It now waits its turn like a
    typed message and is sent when the reply has ended; it never goes through the chat box, so
    what is being typed there stays."""
    import types
    from eli.gui.main_window._mixins.conversation import ConversationMixin as _Window
    started = []

    class _W(_Window):
        _turn_running, _model_loading, _running_now, send_btn = True, False, 0, None
        status_signal = types.SimpleNamespace(emit=lambda message: None)
        chat_input = types.SimpleNamespace(toPlainText=lambda: "a draft being typed")

        def _start_turn(self, text, *, spoken=False):
            started.append((text, spoken))

    monkeypatch.setattr(_Window, "_needs_no_model", staticmethod(lambda text: False))
    win = _W()
    win._take_message("one more", spoken=True)
    assert started == [] and [m["text"] for m in win._waiting_messages] == ["one more"]
    win._turn_running = False
    win._drain_waiting()                      # what the reply's 'Turn ended' runs
    assert started == [("one more", True)] and win._waiting_messages == []


@pytest.mark.parametrize("said,expect", [
    ("volume up", {"direction": "up"}),
    ("volume down by 5", {"direction": "down", "delta": 5}),
    ("louder", {"direction": "up"}),
    ("volume 40", {"level": 40, "percent": 40, "mode": "absolute", "direction": "set"}),
])
def test_a_spoken_volume_command_is_read_by_the_one_volume_grammar(said, expect, monkeypatch):
    """The window's voice path kept its own volume table, stepping by a fixed 15 whatever was said,
    and its "volume N" branch never set the action, so a spoken level fell over; voice input kept a
    third pattern of its own. What is said now goes the way a typed message goes (the engine and its
    router), and the router reads volume with command_parser.volume_said."""
    from eli.gui.main_window._mixins.conversation import ConversationMixin as _Window
    from eli.execution.router_enhanced import route
    from eli.perception.audio_stt import _is_volume_percent_command
    import eli.execution.executor_enhanced as ex
    taken = []
    # nothing real may run: the voice path ran the executor itself, and that set the machine's volume
    monkeypatch.setattr(ex, "execute_action", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    monkeypatch.setattr(ex, "execute", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))

    class _W(_Window):
        def _take_message(self, text, *, spoken=False):
            taken.append((text, spoken))

    _W()._on_stt_transcript(f"  {said} ")
    assert taken == [(said, True)]
    routed = route(said)
    assert routed["action"] == "VOLUME" and routed["args"] == expect
    assert _is_volume_percent_command(said) is (expect.get("mode") == "absolute")


# ── the voice profile learns the owner's speech, not the room's music ─────────
def _scripted_listener(monkeypatch, said, *, threshold=1200.0, base=1200.0, media_playing=False):
    """The real listen loop with the microphone and Whisper stood in: each capture in `said` is
    (transcript, rms); nothing is heard, played or transcribed for real."""
    import threading
    import numpy as np
    from eli.perception import local_whisper_stt
    sr = audio_stt.sr
    monkeypatch.setenv("ELI_VOICE_TONE", "0")
    monkeypatch.setenv("ELI_WAKE_ACOUSTIC", "0")
    monkeypatch.setattr(audio_stt, "_eli_media_probably_audible", lambda: media_playing)
    monkeypatch.setattr(audio_stt, "_eli_duck_output", lambda: None)
    monkeypatch.setattr(audio_stt, "media_player_duck_pause", lambda: None)
    monkeypatch.setattr(local_whisper_stt, "heard_clearly", lambda text: True)
    stt = audio_stt.ELIAudioSTT.__new__(audio_stt.ELIAudioSTT)
    script = list(said)
    transcripts = {}

    class _Mic:
        def __enter__(self):
            return types.SimpleNamespace(stream=None, CHUNK=1024, SAMPLE_RATE=16000, SAMPLE_WIDTH=2)

        def __exit__(self, *a):
            return False

    def _capture(*_a, **_k):
        if not script:
            stt._stop_event.set()
            raise sr.WaitTimeoutError("done")
        text, level = script.pop(0)
        pcm = (np.sin(np.arange(16000) / 3.0) * level * np.sqrt(2)).astype("<i2").tobytes()
        audio = sr.AudioData(pcm, 16000, 2)
        transcripts[id(audio)] = text
        return audio

    stt.recognizer = types.SimpleNamespace(energy_threshold=threshold, pause_threshold=1.2, non_speaking_duration=0.35,
                                           listen=_capture)
    stt._listen_adaptive_pause = _capture
    stt._recognize = lambda audio: transcripts.get(id(audio), "")
    stt.microphone = _Mic()
    stt.is_listening, stt._stop_event, stt._state_lock = True, threading.Event(), threading.Lock()
    stt._thread, stt.callback, stt.audio_queue = None, None, __import__("queue").Queue()
    stt._voice_gate = audio_stt.VoiceGate()
    stt._eli_duck_snapshot, stt._eli_ignore_until, stt._eli_ignore_reason = None, 0.0, ""
    stt._floor = NoiseFloor()
    if threshold > base:              # a room louder than the quietest it has been heard
        rng = np.random.default_rng(0)
        _hear(stt._floor, rng.standard_normal(fx.SR * 12) * base / 3)
        _hear(stt._floor, rng.standard_normal(fx.SR * 12) * threshold / 3, 12.0)
    stt._voice_profile = {"learned_from": getattr(audio_stt, "_VOICE_PROFILE_SOURCE", ""), "count": 0, "energy_mean": 0.0}
    stt._save_voice_profile = lambda: None
    return stt


_SONG = [("i think i lost my mind", 9500), ("thanks for watching", 9300), ("this is the life that i chose", 9700),
         ("don't play it off", 9400), ("i love you", 9600), ("welcome to the ground", 9500)]


def test_the_voice_profile_learns_the_owners_speech_not_the_songs(monkeypatch):
    """Every capture went into the profile before the wake-word gate, so the 95 'voice' samples
    were mostly song lyrics the mic transcribed and dropped. Only what the gate takes as the owner's
    (the wake word, a command it sends on) is learnt now."""
    stt = _scripted_listener(monkeypatch, _SONG + [("computer", 2100), ("what time is it", 1900)])
    stt._listen_loop()
    prof = stt._voice_profile
    assert prof["count"] == 2, f"learnt from {prof['count']} captures, the songs among them"
    assert prof["energy_mean"] < 3000


def test_the_voice_profile_does_not_learn_while_the_music_or_the_room_is_louder():
    for kw in ({"media_playing": True}, {"threshold": 11453.0}):
        mp = pytest.MonkeyPatch()
        try:
            stt = _scripted_listener(mp, [("computer", 9800), ("what time is it", 9900)], **kw)
            stt._listen_loop()
            assert stt._voice_profile["count"] == 0, kw
        finally:
            mp.undo()


def test_a_profile_learnt_from_every_capture_starts_over(tmp_path, monkeypatch):
    import json
    p = tmp_path / "voice_profile.json"
    p.write_text(json.dumps({"count": 95, "energy_mean": 9129.1}))
    stt = audio_stt.ELIAudioSTT.__new__(audio_stt.ELIAudioSTT)
    stt._voice_profile_path = lambda: p
    assert stt._load_voice_profile()["count"] == 0
    p.write_text(json.dumps({"learned_from": audio_stt._VOICE_PROFILE_SOURCE, "count": 7, "energy_mean": 2000.0}))
    assert stt._load_voice_profile()["count"] == 7


# ── echo cancellation: what the default sink plays, out of the microphone ─────
def _aec_world(monkeypatch, tmp_path, server):
    from eli.utils import platform_compat
    import eli.core.paths as paths
    started, ran = [], []

    class _Proc:
        def __init__(self, argv, **kw):
            started.append(argv)
            self.ended = False

        def poll(self):
            return 0 if self.ended else None

        def terminate(self):
            self.ended = True

        def wait(self, timeout=None):
            return 0

    def _run(argv, **kw):
        ran.append(argv)
        listed = f"7\t{audio_stt.AEC_SOURCE}\tPipeWire\ts16le 1ch 48000Hz\tIDLE\n" if started else ""
        return types.SimpleNamespace(stdout=listed, stderr="", returncode=0)

    monkeypatch.setattr(platform_compat, "LINUX", True)
    monkeypatch.setattr(platform_compat, "sound_server", lambda: server)
    monkeypatch.setattr(audio_stt.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(audio_stt.subprocess, "Popen", _Proc)
    monkeypatch.setattr(audio_stt.subprocess, "run", _run)
    monkeypatch.setattr(paths, "temp_path", lambda name: tmp_path / name)
    monkeypatch.setattr(audio_stt.atexit, "register", lambda fn: None)
    monkeypatch.setitem(audio_stt._AEC, "proc", None)
    monkeypatch.setenv("ELI_STT_ECHO_CANCEL", "1")
    for name in ("ELI_MIC_PULSE_SOURCE", "PULSE_SOURCE"):
        monkeypatch.delenv(name, raising=False)
    return started, ran


def test_echo_cancellation_takes_the_default_sinks_sound_out_without_changing_the_desktop(monkeypatch, tmp_path):
    """PulseAudio's module-echo-cancel, loaded into the server, cancelled only what played into its
    own sink, and nothing plays there: ELI's voice and music were never cancelled. PipeWire's
    canceller runs in ELI's own client process in monitor mode, the default sink's monitor as what
    it takes out; its source is ELI's alone and goes with it."""
    import os
    started, ran = _aec_world(monkeypatch, tmp_path, "pipewire")
    monkeypatch.setenv("ELI_MIC_PULSE_SOURCE", "alsa_input.usb-headset")
    assert audio_stt.ensure_echo_cancel() is True
    assert started == [["pipewire", "-c", str(tmp_path / "echo_cancel.conf")]]
    conf = (tmp_path / "echo_cancel.conf").read_text()
    assert "monitor.mode = true" in conf and "priority.session = 0" in conf
    assert 'target.object = "alsa_input.usb-headset"' in conf, "the microphone the owner chose is what is cleaned"
    assert not any("load-module" in a for argv in ran for a in argv), "nothing is loaded into the server"
    assert os.environ["PULSE_SOURCE"] == audio_stt.AEC_SOURCE and audio_stt.echo_cancel_state()["active"]
    audio_stt._unload_echo_cancel()
    assert not audio_stt.echo_cancel_state()["active"] and "PULSE_SOURCE" not in os.environ


def test_on_pulseaudio_alone_echo_cancellation_is_not_done_and_says_why(monkeypatch, tmp_path):
    started, ran = _aec_world(monkeypatch, tmp_path, "pulseaudio")
    assert audio_stt.ensure_echo_cancel() is False and started == [] and ran == []
    assert "routing" in audio_stt.echo_cancel_state()["reason"]
    monkeypatch.setenv("ELI_STT_ECHO_CANCEL", "0")
    assert audio_stt.ensure_echo_cancel() is False


# ── the wake word learnt from the owner's use ─────────────────────────────────
def _learning_listener(monkeypatch, tmp_path, said, **kw):
    from eli.perception import wakeword as ww
    monkeypatch.setattr(ww, "_model_dir", lambda: tmp_path / "wakeword")
    monkeypatch.setattr(ww, "wake_segment", lambda pcm, sr=16000: np.full(ww.CLIP_N, 0.1, dtype=np.float32))
    trained = []
    monkeypatch.setattr(ww, "retrain_in_background", lambda train=None: trained.append(1) or True)
    stt = _scripted_listener(monkeypatch, said, **kw)
    stt.callback = lambda command: None
    return stt, ww, trained


def test_a_wake_word_whose_command_was_acted_on_is_kept_to_train_on(monkeypatch, tmp_path):
    stt, ww, trained = _learning_listener(monkeypatch, tmp_path, [("computer", 2100), ("what time is it", 1900)])
    stt._listen_loop()
    assert ww.enrollment_count() == 1
    stt, ww, _ = _learning_listener(monkeypatch, tmp_path / "2", [("computer", 2100)])
    stt._listen_loop()
    assert ww.enrollment_count() == 0, "no command followed: not confirmed"


def test_a_wake_word_heard_over_elis_music_with_no_speech_of_the_owners_is_not_kept(monkeypatch, tmp_path):
    stt, ww, _ = _learning_listener(monkeypatch, tmp_path, [("computer, what time is it", 2100)],
                                    media_playing=True)
    stt._listen_loop()
    assert ww.enrollment_count() == 0, "music alone may have said it"


# ── hearing the owner over ELI's own music ───────────────────────────────────
_SR = fx.SR
_voice, _music, _through_the_room, _feed = fx.voice, fx.music, fx.through_the_room, fx.feed


def test_speech_over_the_music_is_found_where_loudness_cannot_tell_it():
    """The mic drowned in ELI's own music (9,544 RMS) and the wake word was rarely heard. Speech 3 dB
    below the music raises the mic's level too little to tell it from the beat; measured against
    what the sink plays, it is sound the output does not explain."""
    from eli.perception.near_speech import NearSpeech
    found, said, music_alone_onsets = 0, 0, 0
    for seed in (1, 2):
        ref = _music(45.0, seed)
        mic = _through_the_room(ref, seed)
        alone = [t for t in _feed(NearSpeech(), ref, mic + 1e-3 * np.random.default_rng(seed).standard_normal(len(mic)))
                 if t > 20.0]
        music_alone_onsets += len(alone)
        rng = np.random.default_rng(seed + 10)
        speech = np.zeros(len(mic))
        starts = [24.0, 29.5, 35.0, 40.5]
        for s in starts:
            u = _voice(rng, 1.6, 118.0, (0.15, 0.25))
            speech[int(s * _SR):int(s * _SR) + len(u)] = u
        present = speech != 0
        gain = np.sqrt(np.mean(mic[present] ** 2)) * 10 ** (-3 / 20) / np.sqrt(np.mean(speech[present] ** 2))
        onsets = _feed(NearSpeech(), ref, mic + gain * speech)
        found += sum(any(s <= t <= s + 1.6 for t in onsets) for s in starts)
        said += len(starts)
    assert music_alone_onsets == 0
    assert found >= said * 0.6, f"found {found} of {said} utterances"


def test_the_output_not_heard_at_the_mic_is_nothing_to_judge():
    """Headphones: the music never reaches the mic, so it is not the mic's noise and nothing is done."""
    from eli.perception.near_speech import NearSpeech
    det = NearSpeech()
    rng = np.random.default_rng(3)
    ref = _music(30.0, 3)
    mic = _voice(rng, 30.0, 118.0, (0.15, 0.25)) * 0.1
    assert _feed(det, ref, mic) == [] and not det.active


def test_lowering_and_restoring_the_output_starts_no_onset():
    """ELI lowers its media at an onset and puts it back: neither step in level is speech, read
    before or after the sink's volume."""
    from eli.perception.near_speech import NearSpeech
    for monitor_follows_volume in (False, True):
        ref = _music(60.0, 4)
        mic = _through_the_room(ref, 4) + 1e-3 * np.random.default_rng(4).standard_normal(len(ref))
        gain = np.ones(len(ref))
        changes = []
        for at in (30.0, 45.0):
            gain[int(at * _SR):int((at + 3) * _SR)] = 0.18 ** 3
            changes += [(int(at * _SR), True), (int((at + 3) * _SR), False)]
        det, onsets, chunk = NearSpeech(), [], 1024
        heard = ref * gain if monitor_follows_volume else ref
        for pos in range(0, len(ref) - chunk, chunk):
            while changes and changes[0][0] <= pos:
                det.output_changed(lowered=changes.pop(0)[1])
            t = (pos + chunk) / _SR
            det.reference(heard[pos:pos + chunk], t)
            onsets += det.mic((mic * gain)[pos:pos + chunk], t)
        assert [t for t in onsets if t > 20.0] == [], monitor_follows_volume


def _onset_listener(monkeypatch, media_playing=True):
    import threading
    stt = audio_stt.ELIAudioSTT.__new__(audio_stt.ELIAudioSTT)
    stt._voice_gate = audio_stt.VoiceGate()
    stt._eli_duck_snapshot, stt._eli_ignore_until = None, 0.0
    stt.recognizer = types.SimpleNamespace(energy_threshold=1200.0)
    stt.audio_queue, stt.callback = __import__("queue").Queue(), None
    calls = []
    changed = []
    stt._near = types.SimpleNamespace(mic=lambda x, t: [t], output_changed=lambda lowered: changed.append(lowered))
    monkeypatch.setattr(audio_stt, "_eli_media_probably_audible", lambda: media_playing)
    monkeypatch.setattr(audio_stt, "_eli_duck_output", lambda: calls.append("duck") or ("wpctl", "Volume: 0.65"))
    monkeypatch.setattr(audio_stt, "_eli_restore_duck", lambda snap: calls.append(("restore", snap)))
    monkeypatch.setattr(audio_stt, "media_player_duck_pause", lambda: calls.append("pause"))
    return stt, calls, changed, threading


def test_elis_media_is_lowered_at_the_onset_of_speech_not_after_the_wake_word(monkeypatch):
    """"computer, tell me the time" over the music: the media went down only once Whisper had read
    the wake word, so the command was said over the music and heard as "tell me what 20 minute is".
    It goes down at the onset of speech the output does not explain, and back up when what was said
    was not for ELI."""
    stt, calls, changed, threading = _onset_listener(monkeypatch)
    stt._on_mic_chunk(b"\x00\x01" * 1024, 48000, 1.0)
    for t in threading.enumerate():
        if t.name == "eli-onset-duck":
            t.join(timeout=2)
    assert calls == ["duck"] and changed == [True] and stt._eli_duck_snapshot is not None
    stt._act_on_transcript("i think i lost my mind")          # the song, not the owner
    assert calls[-1] == ("restore", ("wpctl", "Volume: 0.65")) and changed[-1] is False
    assert stt._eli_duck_snapshot is None


def test_a_wake_word_after_an_onset_duck_still_pauses_the_media(monkeypatch):
    stt, calls, changed, threading = _onset_listener(monkeypatch)
    stt._eli_duck_snapshot = ("wpctl", "Volume: 0.65")
    stt._act_on_transcript("computer")
    assert "pause" in calls and "duck" not in calls and stt._eli_duck_snapshot == ("wpctl", "Volume: 0.65")


def test_without_elis_media_playing_an_onset_changes_nothing(monkeypatch):
    stt, calls, changed, threading = _onset_listener(monkeypatch, media_playing=False)
    stt._duck_at_onset()
    assert calls == [] and stt._eli_duck_snapshot is None


def test_what_is_said_while_the_loop_is_not_reading_is_kept(monkeypatch):
    """The listen loop reads nothing while Whisper transcribes, and the device's small buffer overran:
    the words said then were lost. The mic is read without a break; flush drops ELI's own voice."""
    import time
    produced = []

    class _Device:
        def read(self, frames):
            time.sleep(0.004)
            n = len(produced)
            produced.append(n)
            return n.to_bytes(4, "little") * (frames // 2)

        def close(self):
            produced.append("closed")

    mic = audio_stt._ContinuousMic(_Device(), 4, 2).bind_rate(16000)
    time.sleep(0.3)                                    # "transcribing": nothing read meanwhile
    got = [int.from_bytes(mic.read(4)[:4], "little") for _ in range(len(produced) - 1)]
    assert got == list(range(len(got))) and len(got) > 20, "a chunk captured while not read was lost"
    time.sleep(0.1)                                    # ELI speaks: what the mic holds now is its voice
    mic.flush()
    flushed_at = len(produced)
    after = int.from_bytes(mic.read(4)[:4], "little")
    assert after >= flushed_at - 1, "audio from before the flush was read"
    mic.close()
    assert produced[-1] == "closed"

"""Locks for the STT noise self-heal.

The failure being guarded: when the room's noise floor sits ABOVE
`energy_threshold`, the phrase detector can never observe the silence it needs to END
a phrase. Every cycle runs to the phrase cap, captures seconds of noise, transcribes
to nothing, and repeats — the microphone is functionally dead and nothing anywhere
says so. Measured on a real machine: speaker bleed from music lifted the mic from
456 RMS to 7924, putting 85% of frames over the 1200 threshold.

Before this, there was no way out of that state: the only recalibration path required
`_silent_streak >= 60`, which cannot accumulate while noise keeps producing captures,
AND was gated on ELI_STT_CALIBRATE, which defaults to "0".
"""
import types

import numpy as np
import pytest

from eli.perception import audio_stt


class _Recognizer:
    def __init__(self, threshold):
        self.energy_threshold = float(threshold)


def _stt(threshold=1200.0, base=1200.0):
    """A stand-in carrying only what the adaptation methods touch — no microphone."""
    obj = types.SimpleNamespace()
    obj.recognizer = _Recognizer(threshold)
    obj._threshold_base = base
    obj._noise_stuck_streak = 0
    obj._adapt_threshold_to_noise = types.MethodType(
        audio_stt.ELIAudioSTT._adapt_threshold_to_noise, obj)
    obj._decay_threshold_toward_base = types.MethodType(
        audio_stt.ELIAudioSTT._decay_threshold_toward_base, obj)
    return obj


# ── lifting the gate out of the noise ───────────────────────────────────────
def test_threshold_lifts_above_measured_noise():
    """The real numbers from the incident: 1200 threshold, 7924 RMS of bleed."""
    s = _stt(threshold=1200.0)

    assert s._adapt_threshold_to_noise(7924) is True
    # Must clear the noise, or the phrase still never ends.
    assert s.recognizer.energy_threshold > 7924


def test_lift_is_bounded_by_the_adaptive_cap(monkeypatch):
    monkeypatch.setattr(audio_stt, "_STT_ADAPTIVE_CAP", 12000.0)
    s = _stt(threshold=1200.0)

    s._adapt_threshold_to_noise(500000)

    assert s.recognizer.energy_threshold == 12000.0


def test_marginal_noise_does_not_ratchet_the_gate():
    """Without this, a noise reading barely above the gate would creep it upward on
    every cycle until nothing could ever be heard."""
    s = _stt(threshold=1200.0)

    assert s._adapt_threshold_to_noise(1005) is False
    assert s.recognizer.energy_threshold == 1200.0


def test_lift_is_a_noop_when_adaptation_is_disabled(monkeypatch):
    monkeypatch.setattr(audio_stt, "_STT_ADAPTIVE", False)
    s = _stt(threshold=1200.0)

    assert s._adapt_threshold_to_noise(7924) is False
    assert s.recognizer.energy_threshold == 1200.0


@pytest.mark.parametrize("bad_rms", [0, -1])
def test_nonsense_noise_reading_is_ignored(bad_rms):
    s = _stt(threshold=1200.0)

    assert s._adapt_threshold_to_noise(bad_rms) is False
    assert s.recognizer.energy_threshold == 1200.0


# ── and coming back down again ──────────────────────────────────────────────
def test_threshold_decays_back_toward_base():
    """A self-heal that only ever tightens is just a slower way to go deaf."""
    s = _stt(threshold=9600.0, base=1200.0)

    assert s._decay_threshold_toward_base() is True
    assert s.recognizer.energy_threshold < 9600.0


def test_decay_never_goes_below_base():
    s = _stt(threshold=1300.0, base=1200.0)

    for _ in range(50):
        s._decay_threshold_toward_base()

    assert s.recognizer.energy_threshold == pytest.approx(1200.0)


def test_decay_is_a_noop_once_already_at_base():
    s = _stt(threshold=1200.0, base=1200.0)

    assert s._decay_threshold_toward_base() is False


def test_repeated_decay_fully_recovers_from_a_noise_episode():
    """End to end: drowned by music, then the music stops — sensitivity must return."""
    s = _stt(threshold=1200.0, base=1200.0)
    s._adapt_threshold_to_noise(7924)
    assert s.recognizer.energy_threshold > 7924

    for _ in range(50):
        s._decay_threshold_toward_base()

    assert s.recognizer.energy_threshold == pytest.approx(1200.0)


# ── wiring ──────────────────────────────────────────────────────────────────
def test_adaptation_is_on_by_default_and_decoupled_from_startup_calibration():
    """ELI_STT_CALIBRATE means 'measure ambient at startup' and is off by default.
    Gating runtime adaptation on it meant the configuration that most needs to adapt
    was the one that could not."""
    assert audio_stt._STT_ADAPTIVE is True
    assert audio_stt._STT_ADAPTIVE_CAP > 2000, (
        "the startup cap of 2000 cannot gate out ~8000 RMS speaker bleed"
    )
    assert audio_stt._STT_NOISE_STUCK_CYCLES >= 2, "one loud bang must not retune the mic"


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
    stt._threshold_base, stt._noise_stuck_streak = base, 0
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


# ── hearing the owner over ELI's own music ───────────────────────────────────
_SR = 16000


def _voice(rng, seconds, f0, syll_s, formant_scale=1.0):
    """Speech-like sound: syllables of a voice's harmonics under vowel formants, with pauses."""
    vowels = [(730, 1090, 2440), (530, 1840, 2480), (270, 2290, 3010), (570, 840, 2410), (300, 870, 2240)]
    out = np.zeros(int(seconds * _SR))
    pos = 0
    while pos < len(out):
        n = int(rng.uniform(*syll_s) * _SR)
        t = np.arange(n) / _SR
        pitch = f0 * (1 + 0.08 * np.sin(2 * np.pi * rng.uniform(1, 2) * t))
        phase = 2 * np.pi * np.cumsum(pitch) / _SR
        formants = np.array(vowels[rng.integers(len(vowels))]) * formant_scale
        syl = np.zeros(n)
        for h in range(1, int(4000 / f0)):
            amp = sum(1.0 / (1 + ((h * f0 - f) / 90.0) ** 2) for f in formants) / h ** 0.5
            syl += amp * np.sin(h * phase)
        syl *= np.sin(np.pi * np.linspace(0, 1, n)) ** 0.5
        e = min(len(out), pos + n)
        out[pos:e] = syl[: e - pos]
        pos = e + int(rng.uniform(0.02, 0.08) * _SR)
    return out / (np.sqrt(np.mean(out ** 2)) + 1e-12)


def _music(seconds, seed):
    """Drums, a chord and a rap-like vocal, as ELI's sink would play them."""
    rng = np.random.default_rng(seed)
    n = int(seconds * _SR)
    x = np.zeros(n)
    beat = int(0.65 * _SR)
    for k, start in enumerate(range(0, n, beat)):
        m = min(int(0.25 * _SR), n - start)
        decay = np.exp(-np.arange(m) / (0.05 * _SR))
        hit = rng.standard_normal(m) if k % 2 else np.sin(2 * np.pi * 55 * np.arange(m) / _SR) * 3
        x[start:start + m] += hit * decay
    t = np.arange(n) / _SR
    x += 0.3 * sum(np.sin(2 * np.pi * f * t) for f in (220.0, 277.2, 329.6))
    x = x / np.sqrt(np.mean(x ** 2))
    return x + _voice(rng, seconds, 170.0, (0.10, 0.18), formant_scale=1.12)


def _through_the_room(x, seed, delay_s=0.045, ppm=80.0):
    """The speakers and the room to the mic: a reverberant tail, a delay and another clock."""
    rng = np.random.default_rng(seed)
    ir = rng.standard_normal(int(0.4 * _SR)) * np.exp(-np.arange(int(0.4 * _SR)) / (0.06 * _SR)) * 0.2
    ir[0] = 1.0
    y = np.fft.irfft(np.fft.rfft(x, 2 * len(x)) * np.fft.rfft(ir, 2 * len(x)))[: len(x)]
    idx = np.arange(len(y)) * (1 + ppm * 1e-6)
    y = np.interp(idx[idx < len(y) - 1], np.arange(len(y)), y)
    y = np.concatenate([np.zeros(int(delay_s * _SR)), y])[: len(x)]
    return np.pad(y, (0, len(x) - len(y))) / (np.sqrt(np.mean(y ** 2)) + 1e-12)


def _feed(det, ref, mic, chunk=1024):
    onsets = []
    for pos in range(0, len(mic) - chunk, chunk):
        t = (pos + chunk) / _SR
        det.reference(ref[pos:pos + chunk], t)
        onsets += det.mic(mic[pos:pos + chunk], t)
    return onsets


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
    stt._threshold_base, stt.audio_queue, stt.callback = 1200.0, __import__("queue").Queue(), None
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

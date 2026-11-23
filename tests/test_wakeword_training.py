"""The wake-word trainer's own materials (eli/perception/wakeword.py): its noise beds are what they
are named, white, pink and brown, so the head learns against the noise a room actually has."""
import numpy as np


def _slope(bed: np.ndarray, sr: int) -> float:
    """Power fall-off per decade, as log10 of the power at 100 Hz over that at 1 kHz: 0 for white
    noise, 1 for pink (1/f), 2 for brown (1/f²)."""
    spectrum = np.abs(np.fft.rfft(bed)) ** 2
    freqs = np.fft.rfftfreq(len(bed), 1.0 / sr)

    def band(lo, hi):
        return float(spectrum[(freqs >= lo) & (freqs < hi)].mean())

    return float(np.log10(band(90.0, 110.0) / band(900.0, 1100.0)))


def test_the_noise_beds_are_white_pink_and_brown(tmp_path, monkeypatch):
    from eli.perception import wakeword as ww
    monkeypatch.setattr(ww, "_model_dir", lambda: tmp_path)       # no clips of the user's: the synthetic three
    beds = ww._noise_beds()
    assert len(beds) == 3
    white, pink, brown = (_slope(b, ww.SR) for b in beds)
    assert abs(white) < 0.3, "white is flat"
    assert 0.7 < pink < 1.3, "pink falls by ten per decade (it used to be brown under that name)"
    assert 1.7 < brown < 2.3, "brown by a hundred"


def test_reading_the_wake_phrases_writes_nothing(tmp_path, monkeypatch):
    """The defaults were written out on the first read: building the window wrote a wake_config.json
    into the models folder, a test's into the checkout. Finding the folder made it too, so a fresh
    checkout's test run left models/wakeword/ in it. Set, they are kept and read back."""
    import eli.core.paths as paths
    from eli.perception import wakeword as ww
    monkeypatch.setattr(paths, "models_dir", lambda: tmp_path / "models")
    assert ww.get_wake_phrases() == ww._DEFAULT_PHRASES
    assert not ww.is_trained() and ww.enrollment_count() == 0
    assert not (tmp_path / "models").exists()
    assert ww.set_wake_phrases(["Hey Juniper"]) == ["hey juniper"]
    assert ww.get_wake_phrases() == ["hey juniper"]


def test_the_trainer_says_the_phrase_in_elis_own_voices_spread_over_accents_and_genders(monkeypatch):
    """The trainer synthesised its positives with Piper; it now uses ELI's Kokoro voices, the best
    graded of each kind first, at 16 kHz."""
    from eli.perception import tts_kokoro, tts_router
    from eli.perception import wakeword as ww
    monkeypatch.setattr(tts_router, "list_voices", lambda: ["char:calm", *tts_kokoro.VOICES])
    voices = ww._voices(4)
    kinds = {(tts_kokoro.VOICES[v]["accent"], tts_kokoro.VOICES[v]["gender"]) for v in voices}
    assert len(voices) == 4 and len(kinds) == 4, voices
    assert "af_heart" in voices and "char:calm" not in voices
    asked = []
    monkeypatch.setattr(tts_kokoro, "synthesize",
                        lambda text, voice, speed=1.0: asked.append((text, voice, speed))
                        or np.zeros(tts_kokoro.SAMPLE_RATE, dtype=np.float32))
    clip = ww._synth("hey eli", "bm_george", speed=1.18)
    assert asked == [("hey eli", "bm_george", 1.18)]
    assert clip is not None and len(clip) == ww.SR, "one second at Kokoro's rate is one second at 16 kHz"


# ── learning from use, and the voice as a second signal ──────────────────────
import pytest  # noqa: E402

from tests import _audio_fixtures as fx  # noqa: E402

_WAKE = [2, 0, 4]                  # the fixture's wake phrase: three syllables, these vowels in turn
_PEOPLE = {"owner": (118.0, 1.0), "guest": (210.0, 1.16), "tv_a": (205.0, 1.15), "tv_b": (102.0, 0.94)}


def _say(syllables, f0, scale, rng, speed=1.0):
    """A phrase of syllables (vowel indices) in one speaker's voice, 16 kHz, unit RMS."""
    out = []
    for v in syllables:
        n = int(rng.uniform(0.16, 0.22) / speed * fx.SR)
        t = np.arange(n) / fx.SR
        pitch = f0 * (1 + 0.06 * np.sin(2 * np.pi * 1.5 * t))
        phase = 2 * np.pi * np.cumsum(pitch) / fx.SR
        formants = np.array(fx._VOWELS[v]) * scale
        syl = sum((sum(1.0 / (1 + ((h * f0 - f) / 90.0) ** 2) for f in formants) / h ** 0.5) * np.sin(h * phase)
                  for h in range(1, int(4000 / f0)))
        out += [syl * np.sin(np.pi * np.linspace(0, 1, n)) ** 0.5, np.zeros(int(0.03 * fx.SR))]
    x = np.concatenate(out)
    return x / (np.sqrt(np.mean(x ** 2)) + 1e-12)


def _bands(clips_int16):
    """A model-free stand-in for openWakeWord's embeddings: log energy in 24 log-spaced bands over
    40 ms frames, each clip's mean taken off (its loudness is not what it says)."""
    x = np.asarray(clips_int16, dtype=np.float32) / 32768.0
    hop = int(0.04 * fx.SR)
    t = x.shape[1] // hop
    frames = x[:, : t * hop].reshape(len(x), t, hop) * np.hanning(hop)
    spec = np.abs(np.fft.rfft(frames, axis=2)) ** 2
    freqs = np.fft.rfftfreq(hop, 1.0 / fx.SR)
    edges = np.geomspace(100.0, 7000.0, 25)
    e = np.stack([np.log(spec[:, :, (freqs >= a) & (freqs < b)].sum(axis=2) + 1e-9)
                  for a, b in zip(edges[:-1], edges[1:])], axis=2)
    return (e - e.mean(axis=(1, 2), keepdims=True)).astype(np.float32)


@pytest.fixture()
def wake_room(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    from eli.perception import wakeword as ww
    monkeypatch.setattr(ww, "_model_dir", lambda: tmp_path / "wakeword")
    monkeypatch.setattr(ww, "_embed", _bands)
    voices = {"v1": (150.0, 1.05), "v2": (230.0, 1.18), "v3": (95.0, 0.92)}
    monkeypatch.setattr(ww, "_voices", lambda limit=6: list(voices))
    rng = np.random.default_rng(0)

    def _synth(text, voice, speed=1.0):
        f0, scale = voices[voice]
        syl = _WAKE if text in ww.get_wake_phrases() else list(rng.integers(0, 5, size=3 + len(text) % 3))
        return _say(syl, f0, scale, rng, speed) * 0.3

    monkeypatch.setattr(ww, "_synth", _synth)
    monkeypatch.setattr(ww, "get_wake_phrases", lambda: ["computer"])
    return ww


def _owner_wake_capture(rng, room=None, level=0.3):
    x = np.concatenate([np.zeros(int(0.3 * fx.SR)), _say(_WAKE, *_PEOPLE["owner"], rng) * level,
                        np.zeros(int(0.4 * fx.SR))])
    if room is not None:
        x = x + room[: len(x)]
    return (np.clip(x, -1, 1) * 32767).astype(np.int16)


def test_confirmed_wakes_are_kept_with_their_rooms_and_training_follows_from_them(wake_room):
    ww = wake_room
    rng = np.random.default_rng(1)
    assert not ww.retrain_due()
    for k in range(ww.FOLDS):
        clip = ww.wake_segment(_owner_wake_capture(rng), ww.SR)
        assert clip is not None and len(clip) == ww.CLIP_N
        ww.save_enrollment_clip((clip * 32767).astype(np.int16))
        ww.save_noise_bed(fx.fan(3.0, k) * 0.01)
    status = ww.training_status()
    assert status["clips"] == ww.FOLDS and status["new_since_training"] == ww.FOLDS and not status["trained"]
    assert ww.retrain_due()
    assert len(list((ww._model_dir() / "noise").glob("heard_*.wav"))) == ww.FOLDS
    trained = []
    assert ww.retrain_in_background(train=lambda: trained.append(1) or {"ok": True})
    for t in __import__("threading").enumerate():
        if t.name == "eli-wake-train":
            t.join(timeout=5)
    assert trained == [1]


def test_a_wake_word_said_before_a_command_is_cut_from_it(wake_room):
    rng = np.random.default_rng(2)
    wake = _say(_WAKE, *_PEOPLE["owner"], rng)
    command = _say([1, 3, 1, 0, 2], *_PEOPLE["owner"], rng)
    x = np.concatenate([np.zeros(int(0.3 * fx.SR)), wake, np.zeros(int(0.25 * fx.SR)), command]) * 0.3
    seg = wake_room.wake_segment((x * 32767).astype(np.int16))
    voiced = np.flatnonzero(np.abs(seg) > 1e-3)
    assert (voiced[-1] - voiced[0]) / fx.SR < len(wake) / fx.SR + 0.1, "the command came with the wake word"


def _scores(det, clips):
    return np.array([det.score_audio(c) for c in clips])


_ROOMS = {"quiet room": lambda s: np.random.default_rng(s).standard_normal(fx.SR * 3) * 0.003,
          "fan": lambda s: fx.fan(3.0, s) * 0.05,
          "television": lambda s: fx.television(3.0, s) * 0.12,
          "music": lambda s: fx.music(3.0, s) * 0.12}


def _measure(ww, rng, n=12):
    det = ww.WakeDetector()
    out = {}
    for name, room in _ROOMS.items():
        owner = _scores(det, [_owner_wake_capture(rng, room(100 + i)) for i in range(n)])
        out[name] = (int((owner < det.threshold_for(None)).sum()), int((owner < det.threshold_for(True)).sum()))
    guest = _scores(det, [(_say(_WAKE, *_PEOPLE["guest"], rng) * 0.3 * 32767).astype(np.int16) for _ in range(n)])
    out["guest"] = (int((guest >= det.threshold_for(None)).sum()), int((guest >= det.threshold_for(False)).sum()))
    tv = np.concatenate([fx.voice(rng, 30.0, 205.0, (0.12, 0.22), 1.15), fx.voice(rng, 30.0, 102.0, (0.12, 0.22), 0.94)])
    windows = [(tv[i:i + ww.CLIP_N] * 0.3 * 32767).astype(np.int16) for i in range(0, len(tv) - ww.CLIP_N, ww.SR // 2)]
    tv_scores = _scores(det, windows)
    out["tv talk"] = (int((tv_scores >= det.threshold_for(None)).sum()), int((tv_scores >= det.threshold_for(False)).sum()))
    return det, out


def test_false_wakes_and_misses_in_each_room_with_and_without_the_voice(wake_room):
    """Measured on fixtures (a model-free stand-in for openWakeWord's features; the trainer, its head,
    its thresholds and the learning from use are the real ones): the owner's wake word said 12 times
    over a quiet room, a fan, a television and music; a guest saying it 12 times; a minute of a
    television's talk. First trained from ELI's own voices alone, then again after ten confirmed
    wakes of the owner's in those rooms (their clips and the rooms they were said in), each scored
    with no voice known and with the voice as the second signal."""
    ww = wake_room
    torch = pytest.importorskip("torch")
    torch.manual_seed(0)
    np.random.seed(0)
    assert ww.train_model(epochs=60)["ok"]
    _, before = _measure(ww, np.random.default_rng(3))
    rng = np.random.default_rng(4)
    for k in range(10):
        room = list(_ROOMS.values())[k % len(_ROOMS)](200 + k)
        clip = ww.wake_segment(_owner_wake_capture(rng, room))
        ww.save_enrollment_clip((clip * 32767).astype(np.int16))
        ww.save_noise_bed(room)
    assert ww.retrain_due()
    torch.manual_seed(0)
    np.random.seed(0)
    assert ww.train_model(epochs=60)["ok"]
    det, after = _measure(ww, np.random.default_rng(3))
    for label, got in (("ELI's voices only", before), ("after ten confirmed wakes", after)):
        print(f"[WAKE] {label}: " + "; ".join(
            f"{k}: {'accepted' if k in ('guest', 'tv talk') else 'missed'} {a} with no voice known, "
            f"{b} {'as an unknown voice' if k in ('guest', 'tv talk') else 'known'}" for k, (a, b) in got.items()))
    assert det.threshold_for(True) <= det.threshold_for(None) <= det.threshold_for(False)
    rooms = [k for k in _ROOMS]
    assert all(after[k][1] <= after[k][0] for k in rooms), "knowing the voice never misses more"
    assert all(after[k][1] <= after[k][0] for k in ("guest", "tv talk")), "an unknown voice never wakes more"
    assert sum(after[k][0] for k in rooms) <= sum(before[k][0] for k in rooms), "learning from use helped"

"""The wake-word trainer's own materials (eli/perception/wakeword.py): its noise beds are what they
are named, white, pink and brown, so the head learns against the noise a room actually has."""
import numpy as np
import pytest

from eli.perception import wakeword as ww


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


# ── the feature models are ELI's own ───────────────────────────────────────────
def _wake_features_builder():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "packaging" / "wake_features.py"
    spec = importlib.util.spec_from_file_location("eli_wake_features", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fixture_audio(seconds=1.5, seed=7, pitch=140.0, sr=16000):
    """A fixed voice-like sound (a gliding pitch with harmonics, its loudness swelling) over noise."""
    t = np.arange(int(seconds * sr)) / sr
    rng = np.random.default_rng(seed)
    f0 = pitch + 60 * np.sin(2 * np.pi * 0.7 * t)
    phase = 2 * np.pi * np.cumsum(f0) / sr
    voiced = sum(np.sin(k * phase) / k for k in range(1, 12)) * (0.5 + 0.5 * np.sin(2 * np.pi * 2.3 * t) ** 2)
    x = 0.3 * voiced + 0.05 * rng.standard_normal(t.size)
    return (np.clip(x, -1, 1) * 32767 * 0.8).astype(np.int16)


def test_the_wake_words_feature_models_are_elis_own_beside_their_licence_and_provenance():
    """openWakeWord's melspectrogram and embedding models fall under its note that all its included
    pre-trained models are CC BY-NC-SA 4.0. ELI runs its own: Google's speech_embedding/1 (Apache-2.0)
    converted from Google's release, and a log-mel frontend built from its definition, which the shipped
    file computes to float precision."""
    import hashlib
    import re
    import onnxruntime as ort
    from eli.runtime import model_licences
    folder = ww.FEATURE_DIR
    provenance = (folder / "PROVENANCE.md").read_text(encoding="utf-8")
    for stem in ww.FEATURE_MODELS:
        body = (folder / f"{stem}.onnx").read_bytes()
        assert hashlib.sha256(body).hexdigest() in provenance, f"{stem}.onnx is not the file PROVENANCE.md records"
        model = model_licences.for_file(f"eli/perception/wake_features/{stem}.onnx")
        assert model and (folder / model_licences.licence_file(model.key)).is_file(), stem
    assert re.search(r"speech_embedding/1.*Apache-2\.0", provenance, re.S)

    built = _wake_features_builder()
    audio = np.stack([_fixture_audio(), _fixture_audio(seed=3) // 64]).astype(np.float32)
    got = ort.InferenceSession(str(folder / "melspectrogram.onnx"), providers=["CPUExecutionProvider"]).run(
        None, {"input": audio})[0]
    real, imag = built.dft_kernels()
    starts = range(0, audio.shape[1] - built.N_FFT + 1, built.HOP)
    frames = np.stack([audio[:, s:s + built.N_FFT] for s in starts], axis=1).astype(np.float64)
    power = (frames @ real[:, 0, :].T.astype(np.float64)) ** 2 + (frames @ imag[:, 0, :].T.astype(np.float64)) ** 2
    db = 10 * np.log10(np.maximum(power @ built.mel_filters().astype(np.float64), built.AMIN))
    want = np.maximum(db, db.max() - built.TOP_DB)[:, None]
    assert got.shape == want.shape and np.abs(got - want).max() < 1e-2, np.abs(got - want).max()


def test_elis_feature_models_give_what_openwakewords_gave_and_the_head_scores_the_same(monkeypatch):
    """Against openWakeWord's own two files, where its package still holds them (a developer's
    environment; every release leaves them out): the same log-mel frames and embeddings on fixed audio,
    and a wake head trained the way ELI trains one on the old features scores the clips the same on
    ELI's."""
    from pathlib import Path
    oww = pytest.importorskip("openwakeword")
    theirs = Path(oww.__file__).parent / "resources" / "models"
    if not all((theirs / f"{stem}.onnx").is_file() for stem in ww.FEATURE_MODELS):
        pytest.skip("openwakeword's own feature models are not installed here (left out)")
    torch = pytest.importorskip("torch")
    from openwakeword.utils import AudioFeatures
    old = AudioFeatures(str(theirs / "melspectrogram.onnx"), str(theirs / "embedding_model.onnx"))
    new = ww._audio_features()
    clips = np.stack([_fixture_audio(seed=s, pitch=p) for s, p in ((1, 120), (2, 160), (3, 200), (4, 240))]
                     + [(np.random.default_rng(s).standard_normal(ww.CLIP_N) * 3000).astype(np.int16)
                        for s in range(4)])
    mel_old = old._get_melspectrogram(clips[0])
    mel_new = new._get_melspectrogram(clips[0])
    assert mel_old.shape == mel_new.shape and np.abs(mel_old - mel_new).max() < 1e-4
    emb_old = np.asarray(old.embed_clips(clips, batch_size=8), dtype=np.float32)
    emb_new = np.asarray(new.embed_clips(clips, batch_size=8), dtype=np.float32)
    assert emb_old.shape == emb_new.shape and np.abs(emb_old - emb_new).max() <= 1e-3 * np.abs(emb_old).max()

    torch.manual_seed(0)
    head = ww._build_head(emb_old.shape[1] * emb_old.shape[2])
    x_old = torch.tensor(emb_old.reshape(len(clips), -1))
    y = torch.tensor([[1.0]] * 4 + [[0.0]] * 4)
    opt = torch.optim.Adam(head.parameters(), lr=1e-3)
    for _ in range(40):
        opt.zero_grad()
        torch.nn.functional.binary_cross_entropy_with_logits(head(x_old), y).backward()
        opt.step()
    head.eval()
    with torch.no_grad():
        before = torch.sigmoid(head(x_old)).numpy().ravel()
        after = torch.sigmoid(head(torch.tensor(emb_new.reshape(len(clips), -1)))).numpy().ravel()
    assert np.abs(before - after).max() < 1e-4, (before, after)

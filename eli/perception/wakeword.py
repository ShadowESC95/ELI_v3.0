#!/usr/bin/env python3
"""Self-trained, fully-local wake-word detector (openWakeWord features + a custom head).

100% local, no account, no third-party service, and no unavailable pre-trained
model: ELI trains its OWN "computer"/"eli" detector using its OWN voice (Kokoro) to
synthesise the wake phrase across many voices/speeds, then MIXES those positives
with music/noise at random SNRs — that augmentation is what makes the detector
robust *over* background music, which is the whole point. Features come from
openWakeWord's open melspectrogram→embedding extractor; the trained part is a tiny
classifier head we own.

Pipeline:
  synth positives (Kokoro) + augment (mix noise) + negatives → openWakeWord
  embeddings → train a small torch head → save → stream-detect in the mic loop.

The mic loop falls back to the existing transcription matcher whenever no trained
model is present, so this can never break voice. Train with the WAKE_TRAIN action
("train the wake word") or `python -m eli.perception.wakeword`.
"""
from __future__ import annotations
import os
import json
import math
import threading
from pathlib import Path
from typing import Optional, List

import numpy as np

from eli.utils.log import get_logger
log = get_logger(__name__)

SR = 16000
CLIP_S = 1.5
CLIP_N = int(SR * CLIP_S)            # 24000 samples
_DEFAULT_PHRASES = ["computer", "hey computer", "eli", "hey eli"]


def _model_dir() -> Path:
    try:
        from eli.core.paths import models_dir
        d = models_dir() / "wakeword"
    except Exception:
        d = Path(__file__).resolve().parents[2] / "models" / "wakeword"
    return d                            # made by what writes into it, never by a read


def _config_path() -> Path:
    return _model_dir() / "wake_config.json"


def get_wake_phrases() -> List[str]:
    """The user's configured wake phrase(s), or the defaults. Any phrase the user
    sets ("change the wake word to athena") is persisted here and used by both the
    acoustic model (trained on it) and the transcription matcher. Reading them writes nothing: the
    defaults were written out on the first read, so building the window wrote a wake_config.json into
    the models folder (a test's into the checkout's)."""
    path = _config_path()
    if not path.is_file():
        return list(_DEFAULT_PHRASES)
    try:
        ph = json.loads(path.read_text()).get("phrases")
        if ph:
            return [str(p).strip().lower() for p in ph if str(p).strip()]
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return list(_DEFAULT_PHRASES)


def set_wake_phrases(phrases: List[str]) -> List[str]:
    """Persist a custom wake phrase set. Returns the cleaned list. Does NOT train —
    callers retrain (synth works for any phrase ELI's voice can say)."""
    cleaned = [str(p).strip().lower() for p in (phrases or []) if str(p).strip()]
    if not cleaned:
        cleaned = list(_DEFAULT_PHRASES)
    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"phrases": cleaned}, indent=2))
    return cleaned


def _enroll_dir() -> Path:
    return _model_dir() / "enroll"


def save_enrollment_clip(audio_int16: np.ndarray, sr: int = SR) -> Optional[Path]:
    """Persist one real-mic recording of the user saying the wake word. Resampled
    to 16k mono and length-fixed. Returns the path, or None on failure."""
    try:
        import soundfile as sf
        x = np.asarray(audio_int16).ravel().astype(np.float32) / 32768.0
        if sr != SR:
            x = _resample(x, sr, SR)
        x = _fix_len(x)
        d = _enroll_dir()
        d.mkdir(parents=True, exist_ok=True)
        idx = len(list(d.glob("enroll_*.wav")))
        p = d / f"enroll_{idx:03d}.wav"
        sf.write(str(p), x, SR)
        return p
    except Exception as e:
        log.debug(f"[WAKE] save enrollment failed: {e}")
        return None


def _load_enrolled() -> List[np.ndarray]:
    """Real-mic enrollment clips (the user's actual voice/room/mic) as float32 16k."""
    out: List[np.ndarray] = []
    try:
        import soundfile as sf
        for f in sorted(_enroll_dir().glob("enroll_*.wav")):
            try:
                data, sr = sf.read(str(f), dtype="float32")
                if data.ndim > 1:
                    data = data.mean(axis=1)
                out.append(_fix_len(_resample(data, sr, SR)))
            except Exception:
                log.debug("suppressed exception", exc_info=True)
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return out


def enrollment_count() -> int:
    try:
        return len(list(_enroll_dir().glob("enroll_*.wav")))
    except Exception:
        return 0


def _head_path() -> Path:
    return _model_dir() / "eli_wake_head.pt"


def _meta_path() -> Path:
    return _model_dir() / "eli_wake_meta.json"


def is_trained() -> bool:
    return _head_path().exists() and _meta_path().exists()


# ── audio helpers ────────────────────────────────────────────────────────────
from eli.perception.audio_samples import resample as _resample  # noqa: E402  (one resampler for ELI)


def _fix_len(x: np.ndarray, n: int = CLIP_N) -> np.ndarray:
    """Centre-pad / crop a float32 [-1,1] clip to exactly n samples."""
    x = np.asarray(x, dtype=np.float32).ravel()
    if len(x) == n:
        return x
    if len(x) > n:
        start = max(0, (len(x) - n) // 2)
        return x[start:start + n]
    out = np.zeros(n, dtype=np.float32)
    off = (n - len(x)) // 2
    out[off:off + len(x)] = x
    return out


def _to_int16(x: np.ndarray) -> np.ndarray:
    return np.clip(x * 32767.0, -32768, 32767).astype(np.int16)


def _mix_snr(sig: np.ndarray, noise: np.ndarray, snr_db: float) -> np.ndarray:
    """Mix a noise bed into a signal at a target SNR (both float32 [-1,1])."""
    if len(noise) < len(sig):
        reps = int(math.ceil(len(sig) / max(1, len(noise))))
        noise = np.tile(noise, reps)
    noise = noise[:len(sig)]
    ps = float(np.mean(sig ** 2)) + 1e-9
    pn = float(np.mean(noise ** 2)) + 1e-9
    g = math.sqrt(ps / (pn * (10 ** (snr_db / 10.0))))
    out = sig + g * noise
    peak = float(np.max(np.abs(out))) + 1e-9
    return (out / peak * 0.97).astype(np.float32) if peak > 1.0 else out.astype(np.float32)


# ── openWakeWord feature extractor (lazy singleton) ──────────────────────────
_af_lock = threading.Lock()
_af = None


def _features():
    global _af
    with _af_lock:
        if _af is None:
            from openwakeword.utils import AudioFeatures
            _af = AudioFeatures(ncpu=max(1, (os.cpu_count() or 2) // 2))
        return _af


def _embed(clips_int16: np.ndarray) -> np.ndarray:
    """clips_int16: (N, CLIP_N) int16 → (N, T, 96) float32 embeddings."""
    af = _features()
    emb = np.asarray(af.embed_clips(clips_int16, batch_size=64), dtype=np.float32)
    return emb


def _flat_dim_from(emb: np.ndarray) -> int:
    return int(emb.shape[1] * emb.shape[2])


# ── tiny custom head ─────────────────────────────────────────────────────────
def _build_head(in_dim: int):
    import torch.nn as nn
    return nn.Sequential(
        nn.Linear(in_dim, 96), nn.ReLU(),
        nn.Dropout(0.2),
        nn.Linear(96, 32), nn.ReLU(),
        nn.Linear(32, 1),
    )




# ── synthesis of positives / negatives with ELI's own voices (Kokoro) ──────
def _synth(text: str, voice: str, speed: float = 1.0) -> Optional[np.ndarray]:
    """Synthesise text → float32 16k clip, or None on failure."""
    try:
        from eli.perception import tts_kokoro
        samples = tts_kokoro.synthesize(text, voice, speed=speed)
        if samples is None or not len(samples):
            return None
        return _resample(np.asarray(samples, dtype=np.float32), tts_kokoro.SAMPLE_RATE, SR)
    except Exception as e:
        log.debug(f"[WAKE] synth failed ({text!r}): {e}")
        return None


def _voices(limit: int = 6) -> List[str]:
    """Up to `limit` of the Kokoro voices on this machine, spread over accents and genders (the
    best-graded of each kind first), so the detector learns the phrase, not one speaker."""
    try:
        from eli.perception import tts_kokoro
        from eli.perception.tts_router import list_voices
        have = [v for v in list_voices() if tts_kokoro.voice_id(v)]
        kinds: dict = {}
        for v in have:                               # VOICES is ordered best grade first per kind
            meta = tts_kokoro.VOICES[v]
            kinds.setdefault((meta["accent"], meta["gender"]), []).append(v)
        out: List[str] = []
        while len(out) < limit and any(kinds.values()):
            for queue in kinds.values():
                if queue and len(out) < limit:
                    out.append(queue.pop(0))
        return out
    except Exception as e:
        log.debug(f"[WAKE] voice discovery failed: {e}")
        return []


# A small set of non-wake words/phrases for hard negatives (synthesised too).
_NEG_WORDS = [
    "open the door", "what time is it", "play some music", "volume up",
    "tell me a story", "computer science", "complete the task", "hello there",
    "good morning", "the weather today", "set a timer", "thank you very much",
    "elimination", "elephant", "celebrate", "company meeting", "calculator",
]


def _noise_beds() -> List[np.ndarray]:
    """Noise/music beds for augmentation. Real clips from models/wakeword/noise/
    (any .wav/.mp3 the user drops in) plus synthetic white/pink noise so training
    works out of the box even with no music files."""
    beds: List[np.ndarray] = []
    ndir = _model_dir() / "noise"
    if ndir.exists():
        try:
            import soundfile as sf
            for f in list(ndir.glob("*.wav")) + list(ndir.glob("*.flac")):
                try:
                    data, sr = sf.read(str(f), dtype="float32")
                    if data.ndim > 1:
                        data = data.mean(axis=1)
                    beds.append(_resample(data, sr, SR))
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    # synthetic noise (always available): white, pink (power falling as 1/f, shaped in the
    # spectrum) and brown (1/f², the running sum of white noise; what used to be called pink here)
    rng = np.random.default_rng(0)
    white = rng.standard_normal(SR * 4).astype(np.float32) * 0.3
    spectrum = np.fft.rfft(rng.standard_normal(SR * 4))
    freqs = np.fft.rfftfreq(SR * 4, 1.0 / SR)
    spectrum[1:] /= np.sqrt(freqs[1:])
    spectrum[0] = 0.0
    pink = np.fft.irfft(spectrum, n=SR * 4).astype(np.float32)
    pink = (pink - pink.mean()) / (np.std(pink) + 1e-9) * 0.3
    brown = np.cumsum(rng.standard_normal(SR * 4)).astype(np.float32)
    brown = (brown - brown.mean()) / (np.std(brown) + 1e-9) * 0.3
    beds.extend([white, pink, brown])
    return beds


FOLDS = 5   # the threshold is set on out-of-fold scores: five folds, the usual choice


def train_model(phrases: Optional[List[str]] = None, *, per_voice_speeds=(0.87, 1.0, 1.18),
                augment_per_clip: int = 4, epochs: int = 80) -> dict:
    """Synthesise + augment + embed + train the custom head. Local; CPU-fine.
    Returns {ok, positives, negatives, threshold, ...}."""
    import torch
    phrases = phrases or get_wake_phrases()
    voices = _voices()
    if not voices:
        return {"ok": False, "error": "no voices of ELI's own to synthesise the wake phrase"}
    beds = _noise_beds()

    pos: List[np.ndarray] = []

    def _add_positive(base: np.ndarray, n_aug: int):
        pos.append(base)
        for _ in range(n_aug):
            bed = beds[np.random.randint(len(beds))]
            snr = float(np.random.uniform(0.0, 15.0))
            pos.append(_fix_len(_mix_snr(base, bed, snr)))

    # Synthetic positives (ELI's own voices) — breadth/generalisation.
    for ph in phrases:
        for vm in voices:
            for speed in per_voice_speeds:
                clip = _synth(ph, vm, speed=speed)
                if clip is None or len(clip) < SR // 4:
                    continue
                _add_positive(_fix_len(clip), augment_per_clip)

    # Real-mic ENROLLMENT positives (the user's actual voice/room/mic). Weighted
    # heavily — repeated with extra augmentation — so the model locks onto how the
    # user actually says the wake word, which is what matters at real-mic time.
    enrolled = _load_enrolled()
    for clip in enrolled:
        for _ in range(3):                       # repeat the clean real sample
            _add_positive(clip, augment_per_clip + 2)

    neg: List[np.ndarray] = []
    for w in _NEG_WORDS:
        vm = voices[np.random.randint(len(voices))]
        clip = _synth(w, vm, speed=float(np.random.uniform(0.87, 1.18)))
        if clip is not None and len(clip) >= SR // 4:
            neg.append(_fix_len(clip))
    # noise/music-only negatives (so music alone never fires)
    for bed in beds:
        for _ in range(8):
            start = np.random.randint(0, max(1, len(bed) - CLIP_N))
            seg = bed[start:start + CLIP_N]
            neg.append(_fix_len(seg * float(np.random.uniform(0.5, 1.5))))

    if len(pos) < 8 or len(neg) < 8:
        return {"ok": False, "error": f"insufficient samples (pos={len(pos)}, neg={len(neg)})"}

    X = np.stack([_to_int16(c) for c in (pos + neg)])
    y = np.array([1] * len(pos) + [0] * len(neg), dtype=np.float32)
    emb = _embed(X)                                   # (N, T, 96)
    flat = emb.reshape(emb.shape[0], -1)              # (N, T*96)

    Xt = torch.tensor(flat, dtype=torch.float32)
    yt = torch.tensor(y, dtype=torch.float32).unsqueeze(1)
    pos_weight = torch.tensor([len(neg) / max(1, len(pos))])

    def _fit(rows: np.ndarray):
        head = _build_head(flat.shape[1])
        opt = torch.optim.Adam(head.parameters(), lr=1e-3, weight_decay=1e-4)
        lossf = torch.nn.BCEWithLogitsLoss(pos_weight=pos_weight)
        head.train()
        for _ in range(epochs):
            opt.zero_grad()
            loss = lossf(head(Xt[rows]), yt[rows])
            loss.backward()
            opt.step()
        head.eval()
        return head

    # The threshold is chosen on scores from heads that never saw the clip they score (five-fold,
    # the usual way), not on the training fit, which is optimistic about both sides; then one head
    # is trained on everything.
    held = np.zeros(len(y), dtype=np.float32)
    fold_of = np.arange(len(y)) % FOLDS
    for k in range(FOLDS):
        test_rows = np.where(fold_of == k)[0]
        train_rows = np.where(fold_of != k)[0]
        if len(test_rows) == 0 or len(train_rows) == 0:
            continue
        with torch.no_grad():
            held[test_rows] = torch.sigmoid(_fit(train_rows)(Xt[test_rows])).numpy().ravel()
    head = _fit(np.arange(len(y)))
    scores = held
    # threshold = midpoint between the positive 5th-pct and negative 95th-pct of the held-out
    # scores, clamped — favours precision so music doesn't false-fire.
    p_lo = float(np.percentile(scores[y == 1], 5))
    n_hi = float(np.percentile(scores[y == 0], 95))
    thr = float(min(0.9, max(0.5, (p_lo + n_hi) / 2)))

    _head_path().parent.mkdir(parents=True, exist_ok=True)
    torch.save({"state_dict": head.state_dict(), "in_dim": flat.shape[1],
                "emb_shape": list(emb.shape[1:])}, _head_path())
    _meta_path().write_text(json.dumps({
        "phrases": phrases, "threshold": thr, "clip_s": CLIP_S, "sr": SR,
        "positives": len(pos), "negatives": len(neg),
        "emb_shape": list(emb.shape[1:]),
        "pos_score_p5": p_lo, "neg_score_p95": n_hi,
    }, indent=2))
    return {"ok": True, "positives": len(pos), "negatives": len(neg),
            "threshold": thr, "pos_p5": p_lo, "neg_p95": n_hi}


# ── streaming detector ───────────────────────────────────────────────────────
class WakeDetector:
    def __init__(self):
        import torch
        meta = json.loads(_meta_path().read_text())
        ckpt = torch.load(_head_path(), map_location="cpu")
        self.threshold = float(meta.get("threshold", 0.7))
        self.head = _build_head(int(ckpt["in_dim"]))
        self.head.load_state_dict(ckpt["state_dict"])
        self.head.eval()
        self._torch = torch

    def score_audio(self, audio_int16: np.ndarray) -> float:
        """Max wake score over 1.5s windows sliding across the given int16 clip."""
        x = np.asarray(audio_int16, dtype=np.int16).ravel()
        if len(x) < CLIP_N:
            x = np.pad(x, (0, CLIP_N - len(x)))
        wins = []
        step = SR // 2
        for s in range(0, max(1, len(x) - CLIP_N + 1), step):
            wins.append(x[s:s + CLIP_N])
        if not wins:
            wins = [x[:CLIP_N]]
        emb = _embed(np.stack(wins))
        flat = emb.reshape(emb.shape[0], -1)
        with self._torch.no_grad():
            sc = self._torch.sigmoid(self.head(self._torch.tensor(flat, dtype=self._torch.float32))).numpy().ravel()
        return float(np.max(sc)) if len(sc) else 0.0

    def is_wake(self, audio_int16: np.ndarray) -> bool:
        return self.score_audio(audio_int16) >= self.threshold


_detector_lock = threading.Lock()
_detector: Optional[WakeDetector] = None


def get_detector() -> Optional[WakeDetector]:
    """Return the trained detector, or None if not trained / unavailable.
    Cached; safe to call every loop. Never raises."""
    global _detector
    if not is_trained():
        return None
    with _detector_lock:
        if _detector is None:
            try:
                _detector = WakeDetector()
            except Exception as e:
                log.debug(f"[WAKE] detector load failed: {e}")
                return None
        return _detector


if __name__ == "__main__":
    print("[WAKE] training local wake-word model (ELI's voices + augmentation)…")
    print(json.dumps(train_model(), indent=2))

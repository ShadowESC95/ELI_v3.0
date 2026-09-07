"""Speaker identification for shared machines — numpy-first, optional openWakeWord upgrade.

Identifies *who* is speaking so tone/emotion learning and memory stay scoped to the
right person on a shared computer. Cross-platform: no GPU, no cloud, no hardcoded
device assumptions.

Storage (via ``eli.core.paths``):
  ``{artifacts}/runtime/voice/speakers/registry.json``  — all enrolled speakers
  ``{artifacts}/runtime/users/{user_id}/voice/``       — per-user prosody + samples

Optional richer embeddings when openWakeWord is installed (same stack as wake-word).
Disable identification with ``ELI_SPEAKER_ID=0``. Gate unknown voices on commands
with ``ELI_SPEAKER_GATE=1`` (off by default — identifies and switches context only).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from eli.utils.log import get_logger

log = get_logger(__name__)

SR = 16000
_IDENTIFY_THRESHOLD = float(os.environ.get("ELI_SPEAKER_MATCH_THRESHOLD", "0.62"))
_GATE_UNKNOWN = os.environ.get("ELI_SPEAKER_GATE", "0").strip().lower() in ("1", "true", "yes", "on")
_lock = threading.Lock()
_last_identification: Dict[str, Any] = {}


def enabled() -> bool:
    return os.environ.get("ELI_SPEAKER_ID", "1").strip().lower() not in ("0", "false", "no", "off")


def gate_unknown() -> bool:
    return _GATE_UNKNOWN


def _runtime_dir() -> Path:
    try:
        from eli.core.paths import get_paths
        d = Path(get_paths().artifacts_dir) / "runtime" / "voice" / "speakers"
    except Exception:
        d = Path(__file__).resolve().parents[2] / "artifacts" / "runtime" / "voice" / "speakers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _registry_path() -> Path:
    return _runtime_dir() / "registry.json"


def _user_voice_dir(user_id: str) -> Path:
    try:
        from eli.kernel.state import _user_runtime_dir
        d = _user_runtime_dir(user_id) / "voice"
    except Exception:
        d = _runtime_dir() / "users" / _slug(user_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").strip().lower()).strip("-")
    return s or "speaker"


def _speaker_id_for_name(name: str) -> str:
    base = _slug(name)
    h = hashlib.sha256(base.encode()).hexdigest()[:8]
    return f"{base}-{h}"


def _load_registry() -> Dict[str, Any]:
    try:
        return json.loads(_registry_path().read_text(encoding="utf-8"))
    except Exception:
        return {"speakers": {}}


def _save_registry(reg: Dict[str, Any]) -> None:
    _registry_path().write_text(json.dumps(reg, indent=2), encoding="utf-8")


def _to_float(audio_int16: np.ndarray) -> np.ndarray:
    return np.asarray(audio_int16).ravel().astype(np.float32) / 32768.0


def _resample(x: np.ndarray, sr_in: int, sr_out: int = SR) -> np.ndarray:
    if sr_in == sr_out or len(x) == 0:
        return x.astype(np.float32)
    n_out = int(round(len(x) * sr_out / sr_in))
    if n_out <= 1:
        return np.zeros(0, dtype=np.float32)
    return np.interp(
        np.linspace(0, len(x), n_out, endpoint=False),
        np.arange(len(x), dtype=np.float32),
        x.astype(np.float32),
    ).astype(np.float32)


def _spectral_bands(x: np.ndarray, sr: int, n_bands: int = 16) -> np.ndarray:
    """Log-energy in n FFT bands — speaker-discriminative, numpy-only."""
    if len(x) < sr // 20:
        return np.zeros(n_bands, dtype=np.float32)
    frame = x[: min(len(x), sr * 2)]
    spec = np.abs(np.fft.rfft(frame * np.hanning(len(frame))))
    edges = np.linspace(0, len(spec), n_bands + 1, dtype=int)
    bands = []
    for i in range(n_bands):
        seg = spec[edges[i]:edges[i + 1]]
        bands.append(float(np.log1p(np.mean(seg) + 1e-8)))
    return np.array(bands, dtype=np.float32)


def _prosody_vector(audio_int16: np.ndarray, sr: int) -> Optional[np.ndarray]:
    from eli.perception.voice_profile import analyze_prosody, _feature_vector
    pr = analyze_prosody(audio_int16, sr)
    if not pr.get("ok"):
        return None
    x = _to_float(audio_int16)
    if sr != SR:
        x = _resample(x, sr, SR)
    spec = _spectral_bands(x, SR)
    return np.concatenate([_feature_vector(pr), spec]).astype(np.float32)


def _oww_embedding(audio_int16: np.ndarray, sr: int) -> Optional[np.ndarray]:
    """Optional openWakeWord flat embedding when the stack is installed."""
    try:
        from eli.perception.wakeword import CLIP_N, _embed
        x = _to_float(audio_int16)
        if sr != SR:
            x = _resample(x, sr, SR)
        if len(x) < SR // 5:
            return None
        clip = np.zeros(CLIP_N, dtype=np.int16)
        n = min(len(x), CLIP_N)
        clip[:n] = (x[:n] * 32767).astype(np.int16)
        emb = _embed(clip.reshape(1, -1))
        return emb.reshape(-1).astype(np.float32)
    except Exception:
        return None


def embed_audio(audio_int16: np.ndarray, sr: int = SR) -> Optional[np.ndarray]:
    """Combined speaker embedding: prosody + spectral (+ optional OWW tail)."""
    pv = _prosody_vector(audio_int16, sr)
    if pv is None:
        return None
    oww = _oww_embedding(audio_int16, sr)
    if oww is not None and len(oww) > 0:
        # Down-project OWW to 32 dims via fixed random projection (deterministic seed)
        rng = np.random.default_rng(42)
        proj = rng.standard_normal((32, len(oww)), dtype=np.float32) / np.sqrt(len(oww))
        oww32 = proj @ oww
        return np.concatenate([pv, oww32]).astype(np.float32)
    return pv


def list_speakers() -> List[Dict[str, Any]]:
    reg = _load_registry().get("speakers") or {}
    out = []
    for sid, meta in reg.items():
        if isinstance(meta, dict):
            out.append({"speaker_id": sid, **meta})
    return sorted(out, key=lambda x: str(x.get("name") or ""))


def get_speaker(speaker_id: str) -> Optional[Dict[str, Any]]:
    return (_load_registry().get("speakers") or {}).get(str(speaker_id or ""))


def _update_centroid(speaker_id: str, vec: np.ndarray) -> None:
    reg = _load_registry()
    speakers = reg.setdefault("speakers", {})
    meta = speakers.setdefault(speaker_id, {})
    samples = list(meta.get("embedding_samples") or [])
    samples.append(vec.tolist())
    samples = samples[-12:]
    X = np.array(samples, dtype=np.float32)
    meta["centroid"] = X.mean(axis=0).tolist()
    meta["embedding_samples"] = samples
    meta["sample_count"] = len(samples)
    meta["updated_ts"] = time.time()
    _save_registry(reg)


def enroll_speaker(
    audio_int16: np.ndarray,
    sr: int,
    *,
    name: str,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Enroll or update a speaker profile from one clip."""
    if not enabled():
        return {"ok": False, "error": "speaker_id disabled"}
    vec = embed_audio(audio_int16, sr)
    if vec is None:
        return {"ok": False, "error": "could not extract embedding"}
    display = str(name or "Speaker").strip() or "Speaker"
    sid = _speaker_id_for_name(display)
    uid = str(user_id or sid).strip() or sid

    reg = _load_registry()
    speakers = reg.setdefault("speakers", {})
    meta = speakers.get(sid) or {}
    meta.update({
        "name": display,
        "user_id": uid,
        "created_ts": meta.get("created_ts") or time.time(),
    })
    speakers[sid] = meta
    _save_registry(reg)
    _update_centroid(sid, vec)

    # Persist WAV sample for re-training
    try:
        import soundfile as sf
        d = _user_voice_dir(uid) / "enrollment"
        d.mkdir(parents=True, exist_ok=True)
        x = _to_float(audio_int16)
        if sr != SR:
            x = _resample(x, sr, SR)
        idx = len(list(d.glob("*.wav")))
        sf.write(str(d / f"speaker_{idx:03d}.wav"), x, SR)
    except Exception:
        log.debug("speaker enrollment wav save skipped", exc_info=True)

    # Activate this speaker
    set_active_speaker(sid, user_id=uid, name=display, confidence=1.0)
    try:
        from eli.kernel.state import set_active_user_id
        set_active_user_id(uid)
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    return {"ok": True, "speaker_id": sid, "name": display, "user_id": uid}


def identify(audio_int16: np.ndarray, sr: int = SR) -> Dict[str, Any]:
    """Identify speaker from audio. Returns speaker_id, name, confidence, user_id."""
    if not enabled():
        return {"ok": False, "identified": False, "reason": "disabled"}
    vec = embed_audio(audio_int16, sr)
    if vec is None:
        return {"ok": False, "identified": False, "reason": "no_embedding"}

    reg = _load_registry().get("speakers") or {}
    if not reg:
        return {"ok": True, "identified": False, "reason": "no_enrolled_speakers"}

    best_sid, best_name, best_uid, best_sim = "", "", "", -1.0
    for sid, meta in reg.items():
        if not isinstance(meta, dict):
            continue
        c = meta.get("centroid")
        if not c:
            continue
        cen = np.array(c, dtype=np.float32)
        if len(cen) != len(vec):
            continue
        sim = float(np.dot(vec, cen) / (np.linalg.norm(vec) * np.linalg.norm(cen) + 1e-8))
        if sim > best_sim:
            best_sim = sim
            best_sid = sid
            best_name = str(meta.get("name") or sid)
            best_uid = str(meta.get("user_id") or sid)

    identified = best_sim >= _IDENTIFY_THRESHOLD
    result = {
        "ok": True,
        "identified": identified,
        "speaker_id": best_sid if identified else "",
        "name": best_name if identified else "",
        "user_id": best_uid if identified else "",
        "confidence": round(best_sim, 3),
        "threshold": _IDENTIFY_THRESHOLD,
    }
    with _lock:
        global _last_identification
        _last_identification = dict(result)
    if identified:
        set_active_speaker(best_sid, user_id=best_uid, name=best_name, confidence=best_sim)
        try:
            from eli.kernel.state import set_active_user_id
            set_active_user_id(best_uid)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    return result


def get_last_identification(max_age_s: float = 30.0) -> Dict[str, Any]:
    with _lock:
        if _last_identification:
            return dict(_last_identification)
    return {}


_active_speaker: Dict[str, Any] = {}


def set_active_speaker(
    speaker_id: str,
    *,
    user_id: str = "",
    name: str = "",
    confidence: float = 0.0,
) -> None:
    global _active_speaker
    with _lock:
        _active_speaker = {
            "speaker_id": str(speaker_id or ""),
            "user_id": str(user_id or speaker_id or ""),
            "name": str(name or ""),
            "confidence": float(confidence or 0.0),
            "ts": time.time(),
        }


def get_active_speaker() -> Dict[str, Any]:
    with _lock:
        return dict(_active_speaker)


def capabilities() -> Dict[str, Any]:
    reg = _load_registry().get("speakers") or {}
    oww = False
    try:
        from eli.perception import wakeword  # noqa: F401
        oww = True
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return {
        "enabled": enabled(),
        "gate_unknown": gate_unknown(),
        "enrolled_count": len(reg),
        "openwakeword_embeddings": oww,
        "match_threshold": _IDENTIFY_THRESHOLD,
        "active": get_active_speaker(),
        "notes": [
            "numpy prosody + spectral bands (always available)",
            "openWakeWord embedding tail when installed (richer fingerprints)",
            f"{'Gate unknown voices on commands' if gate_unknown() else 'Unknown voices allowed — context not switched'}",
        ],
    }


def rebuild_from_enrollment(user_id: str) -> Dict[str, Any]:
    """Rebuild centroid from stored enrollment WAVs (after moving machines)."""
    d = _user_voice_dir(user_id) / "enrollment"
    if not d.exists():
        return {"ok": False, "error": "no enrollment samples"}
    try:
        import soundfile as sf
    except Exception as e:
        return {"ok": False, "error": str(e)}
    vecs = []
    for f in sorted(d.glob("*.wav")):
        try:
            data, sr = sf.read(str(f), dtype="float32")
            if data.ndim > 1:
                data = data.mean(axis=1)
            v = embed_audio((data * 32767).astype(np.int16), sr)
            if v is not None:
                vecs.append(v)
        except Exception:
            continue
    if not vecs:
        return {"ok": False, "error": "no usable samples"}
    sid = None
    for s, m in (_load_registry().get("speakers") or {}).items():
        if isinstance(m, dict) and str(m.get("user_id")) == str(user_id):
            sid = s
            break
    if not sid:
        return {"ok": False, "error": "speaker not in registry"}
    X = np.stack(vecs)
    reg = _load_registry()
    meta = reg["speakers"][sid]
    meta["centroid"] = X.mean(axis=0).tolist()
    meta["embedding_samples"] = [v.tolist() for v in vecs[-12:]]
    meta["sample_count"] = len(vecs)
    _save_registry(reg)
    return {"ok": True, "speaker_id": sid, "samples": len(vecs)}

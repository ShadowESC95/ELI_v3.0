"""Voice-cloning backend — Chatterbox (Resemble AI, MIT-licensed).

A **commercial-clean** alternative to XTTS: zero-shot voice cloning from a short
reference sample, with native **emotion / exaggeration** control. Chatterbox is
MIT (code + weights), so it can ship in a *sold* product where XTTS-v2 (Coqui's
non-commercial model licence) cannot.

Design:
- Shares the SAME clone registry as ``tts_xtts`` — ``add_clone`` (the record /
  upload flow) stores backend-agnostic reference clips, so a voice captured once
  works on whichever backend is installed.
- ``tts_router`` tries Qwen3-TTS first for ``clone:<name>`` voices (on a GPU with
  room for it), then this backend, then XTTS, then the default Kokoro voice.
- Emotional delivery is NATIVE here: the active tone maps to Chatterbox's
  ``exaggeration`` parameter (no post-hoc pitch shift needed).

Degrades gracefully: needs the optional ``chatterbox`` extra (``chatterbox-tts`` +
torch + a model download on first use). If absent, ``synthesize_wav`` returns
``None`` and the caller falls back — nothing here ever raises to the caller.
"""
from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Cloned voices live under the `clone:` namespace shared with tts_xtts.
CLONE_PREFIX = "clone:"
_MODEL = None  # lazy singleton


def chatterbox_available() -> bool:
    """True when the optional Chatterbox package is importable (no model load)."""
    try:
        import chatterbox  # noqa: F401
        return True
    except Exception:
        return False


def _select_device() -> str:
    """cuda > mps > cpu, overridable via ELI_CHATTERBOX_DEVICE. Never raises."""
    dev = os.environ.get("ELI_CHATTERBOX_DEVICE", "").strip().lower()
    if dev in ("cpu", "cuda", "mps"):
        return dev
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
        _mps = getattr(getattr(torch, "backends", None), "mps", None)
        if _mps is not None and _mps.is_available():
            return "mps"
    except Exception:
        log.debug("tts_chatterbox: device probe failed — using CPU", exc_info=True)
    return "cpu"


def _get_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    from chatterbox.tts import ChatterboxTTS
    device = _select_device()
    log.info("tts_chatterbox: loading Chatterbox on %s (first run downloads the model)…", device)
    _MODEL = ChatterboxTTS.from_pretrained(device=device)
    return _MODEL


def _tensor_to_wav_bytes(wav, sample_rate: int) -> Optional[bytes]:
    """Chatterbox returns a torch audio tensor; encode it to WAV bytes. Never raises."""
    # Preferred: torchaudio (ships with the chatterbox/torch stack).
    try:
        import torchaudio  # type: ignore
        t = wav.detach().to("cpu")
        if t.dim() == 1:
            t = t.unsqueeze(0)  # (samples,) -> (1, samples)
        buf = io.BytesIO()
        torchaudio.save(buf, t, sample_rate, format="wav")
        return buf.getvalue()
    except Exception:
        log.debug("tts_chatterbox: torchaudio encode failed — trying soundfile", exc_info=True)
    # Fallback: soundfile (already an ELI runtime dep).
    try:
        import soundfile as sf  # type: ignore
        arr = wav.detach().to("cpu").numpy()
        if getattr(arr, "ndim", 1) > 1:
            arr = arr.squeeze()
        buf = io.BytesIO()
        sf.write(buf, arr, sample_rate, format="WAV")
        return buf.getvalue()
    except Exception:
        log.debug("tts_chatterbox: soundfile encode failed", exc_info=True)
        return None


def synthesize_wav(text: str, clone_name: str, *,
                   exaggeration: Optional[float] = None,
                   cfg_weight: Optional[float] = None) -> Optional[bytes]:
    """Clone-synthesize ``text`` in the named voice via Chatterbox, from the shared
    reference-clip registry. ``exaggeration`` (0..~1.5) drives emotional intensity —
    wired from ELI's tone adaptor. Returns WAV bytes, or ``None`` if unavailable
    (missing extra / no reference / synth error) so the caller can fall back."""
    # The reference clip comes from the shared clone registry owned by tts_xtts —
    # so add_clone (record/upload) populates both backends at once.
    try:
        from eli.perception import tts_xtts
        spec = tts_xtts.get_clone(clone_name)
    except Exception:
        log.debug("tts_chatterbox: clone registry read failed", exc_info=True)
        spec = None
    if not spec:
        return None
    ref = spec.get("ref_wav")
    if not ref or not Path(ref).is_file():
        log.debug("tts_chatterbox: missing reference for '%s'", clone_name)
        return None
    if not chatterbox_available():
        log.info("tts_chatterbox: %s needs the clone extra — `pip install \"eli-v3[chatterbox]\"`",
                 str(clone_name))
        return None
    try:
        model = _get_model()
        gen_kwargs = {"audio_prompt_path": str(ref)}
        if exaggeration is not None:
            gen_kwargs["exaggeration"] = max(0.0, min(1.5, float(exaggeration)))
        if cfg_weight is not None:
            gen_kwargs["cfg_weight"] = max(0.0, min(1.0, float(cfg_weight)))
        wav = model.generate(str(text), **gen_kwargs)
        return _tensor_to_wav_bytes(wav, int(getattr(model, "sr", 24000)))
    except Exception:
        log.debug("tts_chatterbox: synthesis failed", exc_info=True)
        return None

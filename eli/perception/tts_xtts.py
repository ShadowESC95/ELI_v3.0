"""The cloned-voice registry.

A "cloned voice" reproduces a target voice from a short reference sample (~6-20s of
clean speech) with zero training. The registry here (``add_clone``) only stores the
reference clip, offline, and Qwen3-TTS (Apache-2.0) speaks it on a GPU with room for it
(tts_qwen3); elsewhere the caller speaks with the default voice, with a clear one-line
reason. tts_router resolves a voice named ``clone:<name>`` through here.

The module keeps its name from the engine it once held: Coqui XTTS-v2, whose model is under
the Coqui Public Model License (non-commercial). ELI agreed to that licence on the user's
behalf (COQUI_TOS_AGREED) and fetched the model wherever coqui-tts was installed; a product
sold under its own licence may not, so v3 carries no XTTS-v2 code any more.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

CLONE_PREFIX = "clone:"
NATURAL_PREFIX = "natural:"
# The natural voice (Qwen3-TTS's CustomVoice speaker) the natural engine starts with.
_DEFAULT_NATURAL = "serena"

# ── Registry (offline-safe) ──────────────────────────────────────────────────────
def _registry_path() -> Path:
    try:
        from eli.core.paths import config_dir
        base = Path(config_dir())
    except Exception:
        base = Path("config")
    return base / "voices" / "clones.json"


def _refs_dir() -> Path:
    """Where a cloned voice's reference recording is kept: beside the models, unless
    ELI_VOICE_REFS_DIR names the folder."""
    named = os.environ.get("ELI_VOICE_REFS_DIR")
    if named:
        d = Path(named)
        d.mkdir(parents=True, exist_ok=True)
        return d
    try:
        from eli.core.paths import models_dir
        base = Path(models_dir())
    except Exception:
        base = Path("models")
    d = base / "voice_profiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_registry() -> Dict[str, Dict[str, Any]]:
    p = _registry_path()
    try:
        if p.is_file():
            obj = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(obj, dict):
                return obj
    except Exception:
        log.debug("tts_xtts: registry read failed", exc_info=True)
    return {}


def _save_registry(reg: Dict[str, Dict[str, Any]]) -> None:
    p = _registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")


def _to_reference_wav(audio_path: str, dest: Path) -> bool:
    """Normalise any input audio to a mono 22.05kHz WAV reference via ffmpeg."""
    if not shutil.which("ffmpeg"):
        # No ffmpeg: only accept an existing .wav as-is.
        if str(audio_path).lower().endswith(".wav") and Path(audio_path).is_file():
            shutil.copy2(audio_path, dest)
            return True
        return False
    try:
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(audio_path), "-ac", "1", "-ar", "22050", str(dest)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
        return proc.returncode == 0 and dest.is_file() and dest.stat().st_size > 0
    except Exception:
        log.debug("tts_xtts: reference conversion failed", exc_info=True)
        return False


def add_clone(name: str, audio_path: str, language: str = "en",
              desc: str = "", ref_text: str = "") -> Dict[str, Any]:
    """Register a cloned voice from a reference audio clip (offline; no engine needed). `ref_text`,
    what the clip says, lets Qwen3-TTS clone it more closely."""
    key = str(name or "").lower().replace(CLONE_PREFIX, "", 1).strip().replace(" ", "_")
    if not key:
        return {"ok": False, "error": "empty name"}
    if not Path(audio_path).is_file():
        return {"ok": False, "error": f"audio not found: {audio_path}"}
    ref = _refs_dir() / f"{key}.wav"
    if not _to_reference_wav(audio_path, ref):
        return {"ok": False, "error": "could not read/convert the reference audio (need ffmpeg or a .wav)"}
    reg = _load_registry()
    reg[key] = {"ref_wav": str(ref), "language": language or "en", "desc": desc or f"Cloned voice: {key}"}
    if str(ref_text or "").strip():
        reg[key]["ref_text"] = str(ref_text).strip()
    _save_registry(reg)
    return {"ok": True, "name": key, "id": CLONE_PREFIX + key, "ref_wav": str(ref),
            "synth_ready": _clone_synth_available()}


def _clone_synth_available() -> bool:
    # Clones are spoken by Qwen3-TTS where the GPU runs it (tts_router.synthesize_wav).
    try:
        from eli.perception import tts_qwen3
        return bool(tts_qwen3.usable("base"))
    except Exception:
        log.debug("tts_xtts: Qwen3-TTS check failed", exc_info=True)
        return False


def delete_clone(name: str) -> Dict[str, Any]:
    key = str(name or "").lower().replace(CLONE_PREFIX, "", 1).strip()
    reg = _load_registry()
    if key in reg:
        try:
            Path(reg[key].get("ref_wav", "")).unlink(missing_ok=True)
        except Exception:
            log.debug('tts_xtts: reference clip cleanup failed', exc_info=True)
        reg.pop(key, None)
        _save_registry(reg)
        return {"ok": True, "name": key}
    return {"ok": False, "error": f"no clone '{key}'"}


def get_clone(name: str) -> Optional[Dict[str, Any]]:
    return _load_registry().get(str(name or "").lower().replace(CLONE_PREFIX, "", 1))


def list_clones() -> "list[Dict[str, Any]]":
    return [{"name": k, "id": CLONE_PREFIX + k, "language": v.get("language", "en"),
             "desc": v.get("desc", ""), "ref_present": Path(v.get("ref_wav", "")).is_file()}
            for k, v in _load_registry().items()]

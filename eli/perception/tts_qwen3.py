"""Qwen3-TTS: ELI's more human voice, and its voice cloning, on a GPU with room for it.

Qwen3-TTS (Qwen, Apache-2.0 weights and code) speaks ten languages. Two kinds of model:
- CustomVoice: nine built-in speakers, steered by a plain-language instruction (ELI's tone);
  these are the `natural:<speaker>` voices;
- Base: clones a voice from a short recording; these speak the `clone:<name>` voices.
Each comes in a 1.7B and a 0.6B size. ELI takes the larger one a card has room for, after the
language model's own plan (eli/perception/gpu_room), and none when no card has: a CPU-only machine
never loads it and keeps Kokoro (eli/perception/tts_kokoro.py).

It needs the GPU extra (`pip install "eli-v3[voice-gpu]"`: qwen-tts and PyTorch); the weights
(2.5 to 4.5 GB) are fetched the first time it speaks, at a pinned revision, and each weight file is
checked against its SHA-256 before it is used. Nothing here raises to the caller: None means the
caller speaks with Kokoro instead, and says so (tts_router._note_neural_fallback).
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from eli.utils.log import get_logger

log = get_logger(__name__)

EXTRA = "voice-gpu"
# Every model at one revision, its files' total size, and each weight file's SHA-256 and size.
MODELS: Dict[str, List[Dict[str, Any]]] = {
    "custom": [
        {"repo": "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice", "revision": "0c0e3051f131929182e2c023b9537f8b1c68adfe",
         "bytes": 4_520_218_951, "weights": {
             "model.safetensors": "38b1d5971bdbd982b561cccec982669a53b0537c3cf5e9bd4778ed07bb2f5137",
             "speech_tokenizer/model.safetensors": "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"}},
        {"repo": "Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice", "revision": "85e237c12c027371202489a0ec509ded67b5e4b5",
         "bytes": 2_498_388_392, "weights": {
             "model.safetensors": "bc3c7e785eb961179c25450d1acff03f839e0002f2f3a5aeb67b5735c0fa2adb",
             "speech_tokenizer/model.safetensors": "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"}},
    ],
    "base": [
        {"repo": "Qwen/Qwen3-TTS-12Hz-1.7B-Base", "revision": "fd4b254389122332181a7c3db7f27e918eec64e3",
         "bytes": 4_544_229_700, "weights": {
             "model.safetensors": "38fc7fc51c5e776e840414b6fd443962e9411b9654888fd7913e4da643cb857c",
             "speech_tokenizer/model.safetensors": "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"}},
        {"repo": "Qwen/Qwen3-TTS-12Hz-0.6B-Base", "revision": "5d83992436eae1d760afd27aff78a71d676296fc",
         "bytes": 2_516_106_051, "weights": {
             "model.safetensors": "180b3b10eb1c9f1b4db7806d5475bae3071c0243c299d49926bab1da3b6946f6",
             "speech_tokenizer/model.safetensors": "836b7b357f5ea43e889936a3709af68dfe3751881acefe4ecf0dbd30ba571258"}},
    ],
}
# The CustomVoice speakers (the model card's list), by the id ELI stores after `natural:`.
SPEAKERS: Dict[str, Dict[str, str]] = {
    "vivian": {"speaker": "Vivian", "gender": "female", "desc": "Bright young female voice"},
    "serena": {"speaker": "Serena", "gender": "female", "desc": "Warm, gentle young female voice"},
    "ono_anna": {"speaker": "Ono_Anna", "gender": "female", "desc": "Playful female voice"},
    "sohee": {"speaker": "Sohee", "gender": "female", "desc": "Warm female voice"},
    "ryan": {"speaker": "Ryan", "gender": "male", "desc": "Dynamic male voice with rhythm"},
    "aiden": {"speaker": "Aiden", "gender": "male", "desc": "Sunny American male voice"},
    "uncle_fu": {"speaker": "Uncle_Fu", "gender": "male", "desc": "Seasoned male voice, mellow timbre"},
    "dylan": {"speaker": "Dylan", "gender": "male", "desc": "Youthful male voice"},
    "eric": {"speaker": "Eric", "gender": "male", "desc": "Lively male voice"},
}
# The natural voices ELI named before (eli/perception/tts_xtts), so a saved choice keeps its kind.
LEGACY_NATURAL = {"sophia": "serena", "aria": "vivian", "james": "aiden", "daniel": "ryan"}
_FOOTPRINTS = "qwen3_tts_gpu"     # what each model was measured holding on a card (gpu_room)

_CHOICE: Dict[str, Optional[Tuple[Dict[str, Any], str]]] = {}
_MODELS_LOADED: Dict[str, Any] = {}
_LOCK = threading.Lock()
_LAST_ERROR = ""


def installed() -> bool:
    """True when the GPU extra's packages are installed (no import, no GPU probe)."""
    try:
        return importlib.util.find_spec("qwen_tts") is not None and importlib.util.find_spec("torch") is not None
    except (ImportError, ValueError):
        return False


def _disabled() -> bool:
    return os.environ.get("ELI_QWEN3_TTS", "").strip().lower() in {"0", "false", "no", "off"}


def speaker_key(voice: str) -> str:
    """'natural:aria' -> 'vivian'; '' when it names no speaker."""
    key = str(voice or "").strip().lower()
    if key.startswith("natural:"):
        key = key[len("natural:"):]
    key = LEGACY_NATURAL.get(key, key)
    return key if key in SPEAKERS else ""


def _need_mb(card: Dict[str, Any], entry: Dict[str, Any]) -> float:
    from eli.perception.gpu_room import measured_footprint_mb
    return measured_footprint_mb(_FOOTPRINTS, card, entry["repo"]) or entry["bytes"] / 1_048_576


def choose(kind: str) -> Optional[Tuple[Dict[str, Any], str]]:
    """(model entry, torch device) for `kind` ('custom' or 'base'): the largest model one NVIDIA card
    has room for beside the language model, on that card; None when the extra is missing, it is
    switched off (ELI_QWEN3_TTS=0), PyTorch sees no CUDA, or no card has room. Decided once per run:
    the model's own memory, once loaded, would otherwise turn the answer round on the next call."""
    if kind in _CHOICE:
        return _CHOICE[kind]
    pick: Optional[Tuple[Dict[str, Any], str]] = None
    if installed() and not _disabled():
        forced = os.environ.get("ELI_QWEN3_TTS_DEVICE", "").strip().lower()
        from eli.perception.gpu_room import nvidia_cards, room_mb
        cards = nvidia_cards()
        for entry in MODELS[kind]:
            if forced:
                pick = (entry, forced)
                break
            fits = [(room_mb(card, i), i) for i, card in enumerate(cards)]
            fits = [(r, i) for r, i in fits if r is not None and r >= _need_mb(cards[i], entry)]
            if fits:
                pick = (entry, f"cuda:{max(fits)[1]}")
                break
        if pick is not None and pick[1].startswith("cuda") and not _cuda_works():
            pick = None
    _CHOICE[kind] = pick
    log.info("[TTS] Qwen3-TTS %s: %s", kind,
             f"{pick[0]['repo']} on {pick[1]}" if pick else "not used (no GPU with room for it, or the extra is not installed)")
    return pick


def _cuda_works() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        log.debug("[TTS] PyTorch CUDA probe failed", exc_info=True)
        return False


def usable(kind: str = "custom") -> bool:
    return choose(kind) is not None


def _weights_dir() -> Path:
    try:
        from eli.core.paths import models_dir
        return Path(models_dir()) / "tts" / "qwen3"
    except Exception:
        return Path("models") / "tts" / "qwen3"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def _local_snapshot(entry: Dict[str, Any]) -> Path:
    """The model's files on this machine at its pinned revision, fetched first when missing, every
    weight file checked against its SHA-256 once (a marker beside it remembers the check)."""
    from huggingface_hub import snapshot_download
    cache = _weights_dir()
    kwargs = {"repo_id": entry["repo"], "revision": entry["revision"], "cache_dir": str(cache)}
    try:
        folder = Path(snapshot_download(local_files_only=True, **kwargs))
    except Exception:
        from eli.core import netguard
        with netguard.allow_network(f"voice asset: {entry['repo']}"):
            folder = Path(snapshot_download(**kwargs))
    marker = folder / ".eli-verified"
    if not marker.is_file():
        for rel, want in entry["weights"].items():
            got = _sha256(folder / rel)
            if got != want:
                raise ValueError(f"{entry['repo']} {rel} does not match its pinned checksum")
        marker.write_text(entry["revision"], encoding="utf-8")
    return folder


def _model(kind: str):
    choice = choose(kind)
    if choice is None:
        return None
    entry, device = choice
    with _LOCK:
        if kind in _MODELS_LOADED:
            return _MODELS_LOADED[kind]
        import torch
        from qwen_tts import Qwen3TTSModel
        folder = _local_snapshot(entry)
        attn = "flash_attention_2" if importlib.util.find_spec("flash_attn") else "sdpa"
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
        log.info("[TTS] loading %s on %s", entry["repo"], device)
        model = Qwen3TTSModel.from_pretrained(str(folder), device_map=device, dtype=dtype,
                                              attn_implementation=attn)
        _MODELS_LOADED[kind] = model
        return model


def _record_footprint(kind: str) -> None:
    """After a model's first synthesis: what it holds on its card, weights and working buffers."""
    choice = _CHOICE.get(kind)
    if not choice or not choice[1].startswith("cuda:"):
        return
    try:
        import torch
        index = int(choice[1].split(":", 1)[1])
        from eli.perception.gpu_room import nvidia_cards, record_footprint
        cards = nvidia_cards()
        if index < len(cards):
            record_footprint(_FOOTPRINTS, cards[index], choice[0]["repo"],
                             torch.cuda.max_memory_reserved(index) / 1_048_576)
    except Exception:
        log.debug("[TTS] Qwen3-TTS footprint not recorded", exc_info=True)


def _language(model) -> str:
    try:
        langs = {str(x).lower() for x in (model.model.get_supported_languages() or [])}
    except Exception:
        langs = set()
    return "auto" if "auto" in langs else "english"


def _wav(wavs, rate: int) -> Optional[bytes]:
    import numpy as np
    import soundfile as sf
    if not wavs:
        return None
    audio = np.asarray(wavs[0], dtype=np.float32).reshape(-1)
    if not audio.size:
        return None
    buf = io.BytesIO()
    sf.write(buf, audio, int(rate), format="WAV", subtype="PCM_16")
    return buf.getvalue()


def last_error() -> str:
    return _LAST_ERROR


def _run(kind: str, call) -> Optional[bytes]:
    global _LAST_ERROR
    try:
        model = _model(kind)
        if model is None:
            return None
        first = kind not in _FIRST_DONE
        wavs, rate = call(model)
        if first:
            _FIRST_DONE.add(kind)
            _record_footprint(kind)
        _LAST_ERROR = ""
        return _wav(wavs, rate)
    except Exception as exc:
        _LAST_ERROR = f"{type(exc).__name__}: {exc}"
        log.warning("[TTS] Qwen3-TTS could not speak: %s", _LAST_ERROR, exc_info=True)
        return None


_FIRST_DONE: set = set()


def synthesize_natural_wav(text: str, voice: str, *, instruct: str = "") -> Optional[bytes]:
    """`text` in a built-in speaker's voice (natural:<speaker>), steered by `instruct`; WAV bytes or None."""
    key = speaker_key(voice) or "serena"
    speaker = SPEAKERS[key]["speaker"]
    return _run("custom", lambda m: m.generate_custom_voice(
        text=str(text), speaker=speaker, language=_language(m), instruct=instruct or None))


def synthesize_clone_wav(text: str, ref_wav: str, *, ref_text: str = "") -> Optional[bytes]:
    """`text` in the voice of the recording at `ref_wav`; with its transcript the clone is closer
    (Qwen's in-context mode), without one only the speaker's character is taken. WAV bytes or None."""
    if not ref_wav or not Path(ref_wav).is_file():
        return None
    return _run("base", lambda m: m.generate_voice_clone(
        text=str(text), language=_language(m), ref_audio=str(ref_wav),
        ref_text=ref_text or None, x_vector_only_mode=not ref_text))


def list_natural_voices() -> List[str]:
    """`natural:<speaker>` ids when this machine runs the CustomVoice model, else []."""
    return [f"natural:{k}" for k in SPEAKERS] if usable("custom") else []


def _reset_for_tests() -> None:
    global _LAST_ERROR
    _CHOICE.clear()
    _MODELS_LOADED.clear()
    _FIRST_DONE.clear()
    _LAST_ERROR = ""

"""Qwen3-TTS's worker: runs inside the isolated runtime ELI makes for it (eli/perception/qwen3_runtime.py),
never in ELI's own environment.

qwen-tts 0.1.1 needs transformers 4.57.3 and accelerate 1.12.0; ELI's own environment carries newer ones
(its lock), on which qwen-tts does not import, and with five fixes still never reaches its end token. So the model
runs in a Python of its own, with exactly the versions it was made for, and ELI talks to it one JSON line
at a time over stdin and stdout:

    {"op": "load", "kind": "custom"|"base", "path": <model folder>, "device": "cuda:0"}
    {"op": "custom", "text", "speaker", "language", "instruct", "out": <wav path>}
    {"op": "clone", "text", "language", "ref_wav", "ref_text", "out": <wav path>}
    {"op": "quit"}

Each answer is {"ok": true, ...} or {"ok": false, "error": "..."}; a WAV goes to the path asked for.
`--check` prints what the runtime holds (PyTorch, CUDA, qwen-tts) and exits. Standard library at the top
level only: ELI's own analysis (PyInstaller, its import scan) reads this file without the runtime's packages.
"""
from __future__ import annotations

import json
import sys
import traceback

_MODELS: dict = {}


def _check() -> dict:
    import importlib.metadata as md
    import torch
    out = {"ok": True, "python": sys.version.split()[0], "torch": torch.__version__,
           "cuda": bool(torch.cuda.is_available()), "devices": torch.cuda.device_count()}
    for dist in ("qwen-tts", "transformers", "accelerate"):
        try:
            out[dist] = md.version(dist)
        except md.PackageNotFoundError:
            out[dist] = None
            out["ok"] = False
    return out


def _load(req: dict) -> dict:
    import torch
    from qwen_tts import Qwen3TTSModel
    kind, device = req["kind"], req.get("device") or "cuda:0"
    if kind in _MODELS:
        return {"ok": True, "loaded": True}
    import importlib.util
    attn = "flash_attention_2" if importlib.util.find_spec("flash_attn") else "sdpa"   # an optional speed-up
    dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
    _MODELS[kind] = (Qwen3TTSModel.from_pretrained(req["path"], device_map=device, dtype=dtype,
                                                   attn_implementation=attn), device)
    return {"ok": True, "loaded": True, "attention": attn}


def _language(model) -> str:
    try:
        langs = {str(x).lower() for x in (model.model.get_supported_languages() or [])}
    except Exception:
        langs = set()
    return "auto" if "auto" in langs else "english"


def _write(wavs, rate, path: str, device: str) -> dict:
    import numpy as np
    import soundfile as sf
    audio = np.asarray(wavs[0], dtype=np.float32).reshape(-1)
    sf.write(path, audio, int(rate), format="WAV", subtype="PCM_16")
    out = {"ok": True, "seconds": round(len(audio) / float(rate), 3), "rate": int(rate)}
    if device.startswith("cuda"):
        import torch
        index = int(device.split(":", 1)[1]) if ":" in device else 0
        out["reserved_mb"] = round(torch.cuda.max_memory_reserved(index) / 1048576, 1)
    return out


def _speak(req: dict) -> dict:
    kind = "custom" if req["op"] == "custom" else "base"
    if kind not in _MODELS:
        return {"ok": False, "error": f"the {kind} model is not loaded"}
    model, device = _MODELS[kind]
    language = req.get("language") or _language(model)
    if kind == "custom":
        wavs, rate = model.generate_custom_voice(text=req["text"], speaker=req["speaker"], language=language,
                                                 instruct=req.get("instruct") or None)
    else:
        ref_text = req.get("ref_text") or None
        wavs, rate = model.generate_voice_clone(text=req["text"], language=language, ref_audio=req["ref_wav"],
                                                ref_text=ref_text, x_vector_only_mode=not ref_text)
    return _write(wavs, rate, req["out"], device)


def serve(stdin=sys.stdin, stdout=sys.stdout) -> None:
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            op = req.get("op")
            if op == "quit":
                stdout.write(json.dumps({"ok": True, "quit": True}) + "\n")
                stdout.flush()
                return
            answer = _load(req) if op == "load" else _speak(req) if op in ("custom", "clone") else \
                {"ok": False, "error": f"unknown op {op!r}"}
        except Exception as exc:
            answer = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
                      "trace": traceback.format_exc(limit=4)}
        stdout.write(json.dumps(answer) + "\n")
        stdout.flush()


def _protocol_stream():
    """The answers' own stream: what was stdout, kept for the protocol; stdout itself then goes to stderr
    (qwen-tts prints a notice when it is imported, which would otherwise read as a garbled answer)."""
    import os
    proto = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())
    sys.stdout = sys.stderr
    return proto


if __name__ == "__main__":
    _out = _protocol_stream()
    if sys.argv[1:] == ["--check"]:
        _out.write(json.dumps(_check()) + "\n")
    else:
        serve(stdout=_out)

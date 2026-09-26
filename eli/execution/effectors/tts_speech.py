"""TTS / speech effector — piper synthesis, voice selection, ducking-aware speak.

Lifted from executor_enhanced.py (v3 modularisation). DEBUG + MEMORY_PATH are pure
derivations reproduced locally (shared config, no import cycle); _TTS_ENGINE is tts-owned.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from eli.core.paths import get_paths
from eli.utils.log import get_logger
log = get_logger(__name__)

DEBUG = os.environ.get("ELI_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
MEMORY_PATH = Path(os.environ.get("ELI_MEMORY_FILE", str(get_paths().artifacts_dir / "eli_memory_legacy.jsonl"))).expanduser().resolve()

_TTS_ENGINE = None


def _init_tts():
    global _TTS_ENGINE
    if _TTS_ENGINE is not None:
        return
    try:
        import pyttsx3
    except Exception as e:
        if DEBUG:
            log.debug(f"[TTS] pyttsx3 not available: {e}")
        _TTS_ENGINE = None
        return

    eng = pyttsx3.init()
    voice_name = os.environ.get("ELI_TTS_VOICE", "").strip().lower()
    rate       = os.environ.get("ELI_TTS_RATE", "").strip()

    # optional: choose a different voice by partial name
    if voice_name:
        try:
            for v in eng.getProperty("voices"):
                if voice_name in v.name.lower():
                    eng.setProperty("voice", v.id)
                    break
        except Exception as e:
            if DEBUG:
                log.debug(f"[TTS] voice selection failed: {e}")

    if rate:
        try:
            eng.setProperty("rate", int(rate))
        except Exception as e:
            if DEBUG:
                log.debug(f"[TTS] rate set failed: {e}")

    _TTS_ENGINE = eng


def _tts_list_voices() -> dict:
    _init_tts()
    eng = _TTS_ENGINE
    voices = []
    try:
        for v in (eng.getProperty("voices") or []):
            voices.append({
                "id": getattr(v, "id", "") or "",
                "name": getattr(v, "name", "") or "",
            })
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return {"current": os.environ.get("ELI_TTS_VOICE", ""), "voices": voices, "count": len(voices)}


def _tts_set_voice(target: str) -> dict:
    _init_tts()
    eng = _TTS_ENGINE
    target_l = (target or "").strip().lower()
    if not target_l:
        raise ValueError("empty voice target")
    chosen_id = None
    chosen_name = None
    try:
        for v in (eng.getProperty("voices") or []):
            vid = (getattr(v, "id", "") or "").lower()
            vname = (getattr(v, "name", "") or "").lower()
            if target_l in vid or target_l in vname:
                chosen_id = getattr(v, "id", None)
                chosen_name = getattr(v, "name", None) or target
                break
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    if not chosen_id:
        raise ValueError(f"Voice not found: {target}")
    try:
        eng.setProperty("voice", chosen_id)
    except Exception:
        # still persist preference; engine may apply on next init
        log.debug("suppressed exception", exc_info=True)
    os.environ["ELI_TTS_VOICE"] = str(chosen_name)
    try:
        MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        TTS_PREF_FILE = MEMORY_PATH.parent / "tts_voice.json"
        TTS_PREF_FILE.write_text(json.dumps({"voice": os.environ["ELI_TTS_VOICE"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return {"voice": os.environ["ELI_TTS_VOICE"], "id": chosen_id}


def _speak_legacy(text: str):
    """Compatibility wrapper: delegate to canonical TTS router."""
    try:
        from eli.perception.tts_router import maybe_speak
        maybe_speak(text, enabled=True)
    except Exception as e:
        log.debug(f"[ELI-TTS] exception: {repr(e)}")
    return


def _eli_tts_echo(_text: str) -> None:
    # Always show what ELI *tries* to say, even if audio routing is cursed.
    try:
        if os.environ.get("ELI_TTS_ECHO", "1") != "0":
            log.debug(f"[ELI-SAY] {_text}")
        if os.environ.get("ELI_TTS_LOG", "1") != "0":
            from pathlib import Path
            import json
            from datetime import datetime
            try:
                from eli.core.paths import logs_dir
                logp = Path(logs_dir()) / "tts.jsonl"
            except Exception:
                logp = Path(__file__).resolve().parent.parent / "data" / "logs" / "tts.jsonl"
            logp.parent.mkdir(parents=True, exist_ok=True)
            rec = {"ts": datetime.now().isoformat(timespec="seconds"), "text": _text}
            with open(logp, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        log.debug("suppressed exception", exc_info=True)


def _speak(text: str, *args, **kwargs):
    """Speak arbitrarily long text by chunking + sanitizing to avoid TTS silent failures."""
    import re as _re

    if text is None:
        return
    text = str(text)

    # Strip model junk tokens
    text = text.replace("<|end|>", "").replace("<|", "").replace("|>", "")

    # Remove/replace fenced code blocks (TTS engines often choke on them)
    text = _re.sub(r"```.*?```", " [code omitted] ", text, flags=_re.DOTALL)

    # Normalize whitespace
    text = _re.sub(r"[ \t]+", " ", text)
    text = _re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return

    # Chunk by sentence-ish boundaries, capped by char length
    MAX_CHARS = 350
    parts = _re.split(r"(?<=[.!?])\s+", text)
    buf = ""
    chunks = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(buf) + len(part) + 1 <= MAX_CHARS:
            buf = (buf + " " + part).strip()
        else:
            if buf:
                chunks.append(buf)
            # hard-split very long parts
            while len(part) > MAX_CHARS:
                chunks.append(part[:MAX_CHARS])
                part = part[MAX_CHARS:]
            buf = part
    if buf:
        chunks.append(buf)

    for c in chunks:
        _speak_raw(c, *args, **kwargs)


def _speak_raw(text: str) -> None:
    """Compatibility wrapper: delegate to canonical TTS router."""
    try:
        from eli.perception.tts_router import speak
        speak(text)
    except Exception:
        return


def _eli_speak_piper(text: str):
    """Compatibility wrapper: canonical Piper selection lives in eli.perception.tts_router."""
    try:
        from eli.perception.tts_router import speak
        speak(text)
    except Exception:
        return

__all__ = ['_TTS_ENGINE', '_init_tts', '_tts_list_voices', '_tts_set_voice', '_speak_legacy', '_eli_tts_echo', '_speak', '_speak_raw', '_eli_speak_piper', '_h_speak']


def _h_speak(a, args):
    try:
        text = (args.get("text") or args.get("message") or args.get("content") or "").strip()
        if not text:
            return {"ok": False, "action": a, "error": "No text to speak",
                    "content": "Provide text to speak.", "response": "Provide text to speak."}
        from eli.perception.tts_router import maybe_speak
        maybe_speak(text, enabled=True)
        msg = f"Speaking: {text[:80]}"
        return {"ok": True, "action": a, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

_register("SPEAK", _h_speak)

"""Router voice-runtime contract — normalises ASR variants to direct intents
(volume/settings/May-the-Fourth) before cognition. Moved from router_enhanced.py.
"""
from __future__ import annotations

from eli.utils.log import get_logger

_SWLOG = get_logger(__name__)


def _eli_voice_contract_text_from_call(args, kwargs):
    for item in args:
        if isinstance(item, str) and item.strip():
            return item
    for key in ("text", "message", "command", "prompt", "query", "utterance"):
        item = kwargs.get(key)
        if isinstance(item, str) and item.strip():
            return item
    return ""

def _eli_voice_contract_norm(text):
    import re
    text = str(text or "").lower()
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = text.replace(" per cent", "%").replace(" percent", "%")
    text = re.sub(r"[^a-z0-9%'\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def _eli_voice_contract_response(message, matched_by):
    return {
        "action": "NOOP",
        "args": {
            "message": message,
            "response": message,
            "content": message,
        },
        "confidence": 0.999,
        "meta": {"matched_by": matched_by},
    }

def _eli_voice_contract_route(text):
    import re

    raw = str(text or "").strip()
    norm = _eli_voice_contract_norm(raw)
    if not norm:
        return None

    # Absolute volume: "volume 80", "volume 80%", "set volume to 80".
    m = re.fullmatch(r"(?:set\s+)?volume\s+(?:to\s+)?(\d{1,3})\s*%?", norm)
    if m:
        level = max(0, min(100, int(m.group(1))))
        return {
            "action": "VOLUME",
            "args": {"level": level, "percent": level, "mode": "absolute"},
            "confidence": 0.999,
            "meta": {
                "matched_by": "voice_runtime_contract.volume_absolute",
                "normalized": f"volume {level}%",
            },
        }

    if re.fullmatch(r"volume\s+(?:max|maximum|full)", norm):
        return {
            "action": "VOLUME",
            "args": {"level": 100, "percent": 100, "mode": "absolute"},
            "confidence": 0.999,
            "meta": {"matched_by": "voice_runtime_contract.volume_maximum"},
        }

    if re.fullmatch(r"volume\s+(?:off|zero|mute)", norm):
        return {
            "action": "VOLUME",
            "args": {"level": 0, "percent": 0, "mode": "absolute"},
            "confidence": 0.999,
            "meta": {"matched_by": "voice_runtime_contract.volume_zero"},
        }

    # Keep "open settings" on the direct-execution path. Do not send this to
    # cognition/broker after the OS action has already succeeded.
    if re.fullmatch(r"(?:open|launch|show)\s+(?:system\s+)?settings", norm):
        return {
            "action": "OPEN_APP",
            "args": {"name": "settings", "app": "settings"},
            "confidence": 0.999,
            "meta": {
                "matched_by": "voice_runtime_contract.open_settings_direct",
                "normalized": "open settings",
            },
        }

    # ASR variants for "May the Fourth" / "May the Force".
    may_fourth = (
        re.search(r"\bmay\s+(?:the\s+)?(?:4th|fourth|forth|fort|force|default)\b", norm)
        or re.search(r"\b(?:4th|fourth|forth|fort)\s+of\s+may\b", norm)
        or "fort of may" in norm
        or "fort fou or th" in norm
        or "made of fort" in norm
        or "fort be with you" in norm
        or "force be with you" in norm
    )
    if may_fourth:
        msg = (
            "You mean May the Fourth. Its significance is Star Wars Day: "
            "a pun on “May the Force be with you.”"
        )
        return _eli_voice_contract_response(msg, "voice_runtime_contract.may_fourth_asr_normalised")

    return None

def _eli_voice_contract_wrap_callable(fn):
    if not callable(fn) or getattr(fn, "_eli_voice_contract_wrapped", False):
        return fn

    def _wrapped(*args, **kwargs):
        shortcut = _eli_voice_contract_route(_eli_voice_contract_text_from_call(args, kwargs))
        if shortcut is not None:
            return shortcut
        return fn(*args, **kwargs)

    try:
        _wrapped.__name__ = getattr(fn, "__name__", "_wrapped")
        _wrapped.__doc__ = getattr(fn, "__doc__", None)
        _wrapped._eli_voice_contract_wrapped = True
    except Exception:
        _SWLOG.debug("suppressed exception", exc_info=True)
    return _wrapped

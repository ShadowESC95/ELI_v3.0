"""TTS / speech effector: the SPEAK action. Speech itself is eli.perception.tts_router's."""
from __future__ import annotations

__all__ = ['_h_speak']


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

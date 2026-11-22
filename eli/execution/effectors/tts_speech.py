"""TTS / speech effector: the SPEAK action. Speech itself is eli.perception.tts_router's."""
from __future__ import annotations

__all__ = ['_h_speak']


def _h_speak(a, args):
    try:
        text = (args.get("text") or args.get("message") or args.get("content") or "").strip()
        if not text:
            return {"ok": False, "action": a, "error": "No text to speak",
                    "content": "Provide text to speak.", "response": "Provide text to speak."}
        from eli.perception.tts_router import speak_text
        # The reply says what happened to the words: where they were played, or that they were not.
        # "Speaking: ..." was said before playback began, and stayed true-looking when nothing was heard.
        r = speak_text(text) or {}
        if r.get("ok"):
            msg = f"Said aloud through {r.get('played_through') or 'the speakers'}: {text[:80]}"
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "played_through": r.get("played_through", "")}
        why = r.get("error") or "the speech could not be played"
        msg = f"Not said aloud: {why}."
        return {"ok": False, "action": a, "error": why, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

_register("SPEAK", _h_speak)

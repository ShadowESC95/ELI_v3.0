"""Voice-input effector — DICTATE, LISTEN_FOR_COMMAND, STT/VOICE diagnostics."""
from __future__ import annotations

from eli.utils.log import get_logger

log = get_logger(__name__)


def _h_dictate(a, args):
    try:
        action = str(args.get("action") or "start").lower()
        if action == "start":
            # Signal the GUI to enable dictation mode; GUI handles the loop
            msg = "Dictation mode activated. Speak clearly — I'm listening. Say 'stop dictation' to end."
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "dictate_start": True}
        elif action == "stop":
            msg = "Dictation mode stopped."
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "dictate_stop": True}
        return {"ok": False, "action": a, "error": f"Unknown dictate action: {action}",
                "content": f"Unsupported executor action: {action}", "response": f"Unsupported executor action: {action}"}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}



def _h_listen_for_command(a, args):
    try:
        from eli.perception.audio_stt import listen_for_command
        timeout = float((args or {}).get("timeout", 5))
        text = listen_for_command(timeout=timeout)
        if text:
            msg = f"Heard: {text}"
            return {"ok": True, "action": a, "content": msg, "response": msg, "text": text}
        return {"ok": False, "action": a, "content": "No speech detected.", "response": "No speech detected.", "text": ""}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}



def _h_stt_diagnostics(a, args):
        try:
            from eli.perception.audio_stt import stt_diagnostics
            from eli.perception.tts_router import available_backends
            diag = stt_diagnostics()
            tts = available_backends()
            combined = {**diag, "tts_backends": tts}
            lines = [
                f"STT — speech_recognition: {'✅' if combined.get('speech_recognition_imported') else '❌'}",
                f"STT — wake word disabled: {combined.get('wake_word_disabled')}",
                f"STT — direct chat: {combined.get('allow_direct_chat_without_wake')}",
                f"TTS — piper: {'✅' if tts.get('piper_bin') else '❌'} model: {'✅' if tts.get('piper_model') else '❌'}",
                f"TTS — espeak-ng: {'✅' if tts.get('espeak_ng') else '❌'}",
                f"TTS — espeak: {'✅' if tts.get('espeak') else '❌'}",
            ]
            msg = "\n".join(lines)
            return {"ok": True, "action": a, "content": msg, "response": msg, "diagnostics": combined}
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # ---- GAZE ENGINE ----



from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "DICTATE": _h_dictate,
    "LISTEN_FOR_COMMAND": _h_listen_for_command,
    "STT_DIAGNOSTICS": _h_stt_diagnostics,
    "VOICE_DIAGNOSTICS": _h_stt_diagnostics,
}.items():
    _register(_action, _fn)

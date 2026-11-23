"""Voice-input effector — DICTATE, LISTEN_FOR_COMMAND, STT/VOICE diagnostics."""
from __future__ import annotations
from pathlib import Path

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
    from eli.plugins.sandbox import enforce
    enforce("microphone", "listen on the microphone")
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



def _played_line(last: dict) -> str:
    if not last:
        return "nothing played yet"
    if last.get("ok"):
        return f"played through {last.get('via') or 'an unnamed route'}"
    return f"NOT played ({last.get('detail') or 'playback failed'})"


def _h_stt_diagnostics(a, args):
    try:
        from eli.perception.audio_stt import stt_diagnostics
        from eli.perception.tts_router import available_backends
        diag = stt_diagnostics()
        tts = available_backends()
        combined = {**diag, "tts_backends": tts}
        wh = combined.get("whisper") or {}
        lines = [
            f"STT — speech_recognition: {'✅' if combined.get('speech_recognition_imported') else '❌'}",
            f"STT — wake word disabled: {combined.get('wake_word_disabled')}",
            f"STT — direct chat: {combined.get('allow_direct_chat_without_wake')}",
            f"STT — wake model trained: {'✅' if combined.get('wake_model_trained') else '❌'} "
            f"(phrase: {combined.get('wake_phrase', '?')})",
            f"STT — whisper cache: {'✅' if wh.get('cache_ready') else '❌'} "
            f"loaded: {'✅' if wh.get('loaded') else '❌'} "
            f"model: {wh.get('model', '?')} device: {wh.get('device_configured', '?')}",
            f"STT — output duck: {'✅' if combined.get('wake_duck_enabled') else 'off'} "
            f"media pause duck: {'✅' if combined.get('media_duck_enabled') else 'off'}",
            f"TTS — engine: {tts.get('engine', '?')} voice: {tts.get('speaking_voice') or tts.get('active_voice', '?')}",
            f"TTS — Kokoro: {'✅' if tts.get('kokoro') else '❌'} files: {'✅' if tts.get('kokoro_ready') else '❌ (fetched on first use)'}"
            + (f" did not load: {tts.get('kokoro_error')}" if tts.get('kokoro_error') else ""),
            f"TTS — Qwen3-TTS (GPU): natural {'✅' if tts.get('neural_available') else '❌'} cloning {'✅' if tts.get('qwen3') else '❌'}",
            f"TTS — last speech: {_played_line(tts.get('last_playback') or {})}",
        ]
        msg = "\n".join(lines)
        return {"ok": True, "action": a, "content": msg, "response": msg, "diagnostics": combined}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}

    # ---- GAZE ENGINE ----



def _h_transcribe(a, args):
    try:
        source = str(args.get("source") or "").strip()
        if source:
            src_path = Path(source).expanduser().resolve()
            if not src_path.exists():
                return {"ok": False, "action": a, "error": f"File not found: {src_path}",
                        "content": f"File not found: {src_path}", "response": f"File not found: {src_path}"}
            from faster_whisper import WhisperModel
            from eli.perception.local_whisper_stt import _audio_for_whisper
            model = WhisperModel("small", device="cpu", compute_type="int8")
            segments, _ = model.transcribe(_audio_for_whisper(src_path), vad_filter=True)
            text = " ".join(seg.text.strip() for seg in segments).strip()
            msg = f"Transcription of {src_path.name}:\n\n{text}" if text else "No speech detected."
            return {"ok": True, "action": a, "content": msg, "response": msg, "text": text}
        else:
            # Live microphone transcription — single utterance
            import numpy as np, sounddevice as sd
            from faster_whisper import WhisperModel
            sr = 16000
            chunk = 0.05
            start_rms = 0.02
            end_silence = 1.2
            max_s = 30
            block = int(sr * chunk)
            frames, silence, started = [], 0.0, False
            import time as _t
            t0 = _t.time()
            with sd.InputStream(samplerate=sr, channels=1, blocksize=block, dtype="float32") as s:
                while True:
                    data, _ = s.read(block)
                    x = data[:, 0]
                    rms = float(np.sqrt(np.mean(x * x)) + 1e-12)
                    if not started and rms >= start_rms:
                        started = True
                    if started:
                        frames.append(x.copy())
                        silence = silence + chunk if rms < start_rms else 0.0
                    if started and silence >= end_silence:
                        break
                    if _t.time() - t0 > max_s:
                        break
            if not frames:
                return {"ok": False, "action": a, "error": "No audio captured",
                        "content": "No audio captured.", "response": "No audio captured."}
            audio = np.concatenate(frames).astype("float32")
            model = WhisperModel("small", device="cpu", compute_type="int8")
            # the samples themselves, recorded at 16 kHz: no file for PyAV to open
            segments, _ = model.transcribe(audio, vad_filter=True)
            text = " ".join(seg.text.strip() for seg in segments).strip()
            msg = f"Transcribed: {text}" if text else "No speech detected."
            return {"ok": True, "action": a, "content": msg, "response": msg, "text": text}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "DICTATE": _h_dictate,
    "LISTEN_FOR_COMMAND": _h_listen_for_command,
    "STT_DIAGNOSTICS": _h_stt_diagnostics,
}.items():
    _register(_action, _fn)

_register("VOICE_DIAGNOSTICS", _h_stt_diagnostics, shown="quick")

_register("TRANSCRIBE", _h_transcribe)

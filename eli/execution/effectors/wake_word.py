"""Wake-word/voice-training effector — enroll/set/train wake word, train voice."""
from __future__ import annotations



from eli.utils.log import get_logger

log = get_logger(__name__)


def _h_wake_enroll(a, args):
    try:
        from eli.perception.audio_stt import begin_wake_enrollment
        n = int((args or {}).get("samples") or (args or {}).get("count") or 5)
        res = begin_wake_enrollment(n)
        if not res.get("ok"):
            msg = f"Wake-word enrollment can't start: {res.get('error')}"
            return {"ok": False, "action": a, "content": msg, "response": msg}
        msg = res.get("message") or "Listening for your wake-word samples."
        return {"ok": True, "action": a, "content": msg, "response": msg,
                "evidence_source": "wakeword_enroll", "result": res}
    except Exception as e:
        msg = f"WAKE_ENROLL failed: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}



def _h_wake_set(a, args):
    try:
        import re as _re_ws
        phrase = str((args or {}).get("phrase") or (args or {}).get("word")
                     or (args or {}).get("wake_word") or "").strip().strip('."\'')
        if not phrase:
            _raw = str((args or {}).get("_raw_user_text") or (args or {}).get("raw")
                       or (args or {}).get("message") or "")
            m = _re_ws.search(r"(?:wake[\s-]*word|wakeword)\s+(?:to|is|as|=)\s+(.+)$", _raw, _re_ws.I)
            if m:
                phrase = m.group(1).strip().strip('."\'')
        if not phrase:
            msg = ("What would you like your wake word to be? Say: "
                   "\"change the wake word to <your phrase>\".")
            return {"ok": False, "action": a, "content": msg, "response": msg}
        from eli.perception import wakeword as _ww
        phrases = _ww.set_wake_phrases([phrase])
        try:
            from eli.perception import audio_stt as _ast
            _ast._merge_custom_wake_words()
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        import threading as _th_ws
        _th_ws.Thread(target=lambda: _ww.train_model(phrases=phrases),
                      daemon=True, name="eli-wake-set-train").start()
        msg = (f"Wake word set to \"{phrase}\". I'm training the detector on it now "
               f"(about a minute) — it'll work the moment that finishes. Say "
               f"\"enroll my wake word\" afterwards to tune it to your voice over music.")
        return {"ok": True, "action": a, "content": msg, "response": msg,
                "evidence_source": "wakeword_set", "result": {"phrases": phrases}}
    except Exception as e:
        msg = f"WAKE_SET failed: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}



def _h_wake_train(a, args):
    try:
        from eli.perception import wakeword as _ww
        res = _ww.train_model()
        if not res.get("ok"):
            msg = f"Wake-word training could not run: {res.get('error')}"
            return {"ok": False, "action": a, "content": msg, "response": msg}
        msg = (f"Wake-word model trained locally: {res.get('positives')} positive + "
               f"{res.get('negatives')} negative samples, decision threshold "
               f"{float(res.get('threshold', 0)):.2f}. ELI now spots the wake word "
               f"acoustically (robust over background music). For best real-world "
               f"accuracy, drop a few real music clips into models/wakeword/noise/ "
               f"and run this again.")
        return {"ok": True, "action": a, "content": msg, "response": msg,
                "evidence_source": "wakeword_train", "result": res}
    except Exception as e:
        msg = f"WAKE_TRAIN failed: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}



def _h_train_voice(a, args):
    try:
        from eli.perception.audio_stt import begin_voice_training
        n = int((args or {}).get("samples") or (args or {}).get("count") or 8)
        res = begin_voice_training(n)
        if not res.get("ok"):
            msg = f"Voice training can't start: {res.get('error')}"
            return {"ok": False, "action": a, "content": msg, "response": msg}
        msg = res.get("message") or "Listening for your voice samples."
        return {"ok": True, "action": a, "content": msg, "response": msg,
                "evidence_source": "voice_training", "result": res}
    except Exception as e:
        msg = f"TRAIN_VOICE failed: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}


def _h_speaker_enroll(a, args):
    try:
        from eli.perception.audio_stt import begin_speaker_enrollment
        name = str((args or {}).get("name") or (args or {}).get("speaker") or "").strip()
        if not name:
            msg = 'Say: "enroll speaker <your name>" so ELI knows who you are on this machine.'
            return {"ok": False, "action": a, "content": msg, "response": msg}
        res = begin_speaker_enrollment(name)
        if not res.get("ok"):
            msg = res.get("error") or "Speaker enrollment could not start."
            return {"ok": False, "action": a, "content": msg, "response": msg}
        msg = res.get("message") or f"Enrolling speaker {name}."
        return {"ok": True, "action": a, "content": msg, "response": msg, "result": res}
    except Exception as e:
        msg = f"SPEAKER_ENROLL failed: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}


def _h_list_speakers(a, args):
    try:
        from eli.perception.speaker_id import list_speakers, get_active_speaker
        speakers = list_speakers()
        active = get_active_speaker()
        if not speakers:
            msg = "No speakers enrolled yet. Say: enroll speaker <your name>."
        else:
            lines = ["Enrolled speakers on this machine:"]
            for s in speakers:
                mark = " (active)" if s.get("speaker_id") == active.get("speaker_id") else ""
                lines.append(f"  • {s.get('name', '?')}{mark}")
            msg = "\n".join(lines)
        return {"ok": True, "action": a, "content": msg, "response": msg, "speakers": speakers}
    except Exception as e:
        msg = f"LIST_SPEAKERS failed: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}


def _h_speaker_status(a, args):
    try:
        import json
        from eli.perception.speaker_id import capabilities, list_speakers
        caps = capabilities()
        caps["speakers"] = list_speakers()
        msg = json.dumps(caps, indent=2)
        return {"ok": True, "action": a, "content": msg, "response": msg, "capabilities": caps}
    except Exception as e:
        msg = f"SPEAKER_STATUS failed: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "WAKE_ENROLL": _h_wake_enroll,
    "WAKE_SET": _h_wake_set,
    "WAKE_TRAIN": _h_wake_train,
    "TRAIN_VOICE": _h_train_voice,
    "SPEAKER_ENROLL": _h_speaker_enroll,
    "LIST_SPEAKERS": _h_list_speakers,
    "SPEAKER_STATUS": _h_speaker_status,
}.items():
    _register(_action, _fn)

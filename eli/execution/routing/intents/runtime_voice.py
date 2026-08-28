"""Resolved runtime paths, voice/clone management and voice downloads.

The 'what am I running and how do I sound' family.

Extracted verbatim from ``router_enhanced.route`` (v3 modularisation). This is an
ordered stage: it returns a route dict when one of its guards matches, or None to
let the next stage try. Order within the file is the legacy order and is load
bearing — an earlier guard deliberately shadows later ones.
"""
from __future__ import annotations

import re

from eli.utils.log import get_logger

from eli.execution.routing.intents._sentinel import ROUTE_NONE
from eli.execution.routing._helpers import (
    _mk,
)


log = get_logger(__name__)


def route_runtime_voice(text: str, raw: str, low: str, text_l: str):
    if re.search(r"\b(show me the resolved runtime paths|resolved runtime paths|runtime paths for every critical file|critical file you depend on)\b", low):
        return _mk("CHAT", {"message": raw}, 0.95, matched_by="router.runtime_paths_to_chat")

    if re.search(r"\b(audit your gui file|gui runtime audit|gui file.*wired incorrectly)\b", low):
        return _mk("GUI_RUNTIME_AUDIT", {}, 0.99, matched_by="gui.runtime.preempt")

    # Voice/STT diagnostics must be checked BEFORE general RUNTIME_AUDIT
    # because RUNTIME_AUDIT's regex catches bare "diagnostic/diagnostics" which
    # swallows "voice diagnostics", "mic diagnostics", etc.
    if re.search(r"\b(voice|stt|speech[- ]to[- ]text|whisper|microphone|mic)\s+"
                 r"(diagnostic|diagnostics|status|test|check|health)\b"
                 r"|\b(diagnostic|diagnostics|status|test|check|health)\s+(voice|stt|speech|whisper|mic)\b",
                 low):
        return _mk("VOICE_DIAGNOSTICS", {}, 0.93, matched_by="voice.diagnostics.preempt")

    # ── Wake-word + voice-profile management ───────────────────────────────────
    # Deterministic because these are explicitly-named commands the LLM resolver
    # mis-routes ("set my wake word" → SET_CLIPBOARD). Distinct flows:
    #   change/set wake word [to X]  → WAKE_SET (user picks their own wake word)
    #   train/build wake word        → WAKE_TRAIN (synthetic model)
    #   enroll/personalise wake word → WAKE_ENROLL (record me saying it)
    #   train/learn my voice         → TRAIN_VOICE (prosody/tone foundation — separate)
    if re.search(r"\b(wake[\s-]*word|wakeword)\b", low):
        m = re.search(r"\b(?:change|set|update|make|rename)\b.*\bwake[\s-]*word\b\s*(?:to|is|as|=)?\s*(.*)$",
                      raw, re.I)
        if re.search(r"\b(?:change|set|update|make|rename|pick|choose|customi[sz]e)\b", low) and \
           not re.search(r"\b(train|enroll|enrol|teach|record|personali[sz]e|tune)\b", low):
            phrase = (m.group(1).strip().strip('."\'') if m and m.group(1) else "")
            return _mk("WAKE_SET", {"phrase": phrase, "_raw_user_text": raw}, 0.95,
                       matched_by="wake.set", entities={"phrase": phrase})
        if re.search(r"\b(enroll|enrol|personali[sz]e|tune|record)\b", low):
            return _mk("WAKE_ENROLL", {}, 0.95, matched_by="wake.enroll")
        if re.search(r"\b(train|build|retrain|rebuild|create)\b", low):
            return _mk("WAKE_TRAIN", {}, 0.95, matched_by="wake.train")
    if re.search(r"\b(train|learn|teach|profile|study|analyse|analyze)\b.*\b(my\s+)?voice\b", low) or \
       re.search(r"\b(my\s+)?voice\b.*\b(train|profile|baseline|tone|emotion)\b", low):
        if "wake" not in low:
            return _mk("TRAIN_VOICE", {}, 0.93, matched_by="voice.train_profile")

    # Speaker ID — shared-machine enrollment ("enroll speaker Jay")
    if re.search(
        r"\b(enroll|register|add|teach)\b.*\b(speaker|speakers)\b"
        r"|\b(enroll|register)\b.*\b(my\s+)?voice\b.*\b(as|named)\b"
        r"|\bwho\s+am\s+i\b.*\b(enroll|register|train)\b",
        low,
    ):
        m = re.search(r"\b(?:as|named|name|speaker)\s+([A-Za-z][A-Za-z0-9 _-]{1,30})", raw, re.I)
        name = (m.group(1).strip() if m else "")
        if not name:
            m2 = re.search(r"\benroll\s+speaker\s+([A-Za-z][A-Za-z0-9 _-]{1,30})", raw, re.I)
            name = (m2.group(1).strip() if m2 else "")
        return _mk("SPEAKER_ENROLL", {"name": name}, 0.93, matched_by="speaker.enroll")
    if re.search(r"\b(list|show|who are)\b.*\b(enrolled\s+)?speakers\b", low):
        return _mk("LIST_SPEAKERS", {}, 0.9, matched_by="speaker.list")
    if re.search(r"\b(speaker|voice)\s+(id|identification|recognition)\s+(status|capabilities|diagnostic)\b", low):
        return _mk("SPEAKER_STATUS", {}, 0.9, matched_by="speaker.status")

    # ── Voice library: list / download / switch the TTS voice ──────────────────
    # Distinct from persona TONE (SET_COMMUNICATION_STYLE): this changes the actual
    # spoken voice/accent and can fetch new ones from the Piper library. Placed in
    # this deterministic region so a concrete voice request isn't swallowed by the
    # later persona-style route ("use a X voice"). Runs AFTER wake/train above, so
    # "train my voice" and "voice diagnostics" have already returned.
    # ── Tone / emotion register (shades ELI's delivery; core personality unchanged) ──
    # Ported from v2: tone_adaptor.set_tone/clear_tone and the emotion palette were
    # already here, and the Settings dropdown already called them — only the spoken
    # route was missing, so "be comedic" did nothing.
    # CLEAR first ("back to normal", "be yourself", "drop the tone").
    if re.search(r"\b(back to (normal|yourself)|be yourself|normal tone|drop the (tone|act|voice)"
                 r"|stop (that|the tone|acting)|reset your tone|clear (the )?tone)\b", low):
        return _mk("CLEAR_TONE", {}, 0.9, matched_by="tone.clear")
    # SET — an explicit tone directive that names a palette tone/alias. Gated on BOTH
    # a tone-setting verb AND the phrase resolving to a real tone, so "be comedic" /
    # "talk street" / "sound more professional" fire, but a passing mention
    # ("that joke was comedic gold") does not.
    if re.search(r"\b(be|sound|talk|speak|act|go|get|use|make it|keep it|more|less)\b", low):
        try:
            from eli.cognition.emotion_palette import resolve_tone as _rt
            _tone = _rt(raw)
            if _tone and _tone != "neutral":
                return _mk("SET_TONE", {"tone": _tone}, 0.9, matched_by="tone.set")
        except Exception:
            log.debug("tone.set resolve failed", exc_info=True)

    # CREATE / clone a voice from a dropped-in recording (wav/mp3/mp4). Checked
    # before the download/set routes so "create a voice from …" isn't mis-read.
    if re.search(r"\b(voice|clone)\b", low) and re.search(
            r"\b(creat|make|build|clone|generat|record)\w*\b", low) and (
            re.search(r"\.(wav|mp3|mp4|m4a|ogg|flac|aac|webm)\b", low)
            or re.search(r"\b(from|using|out of|with)\b.*\b(recording|audio|clip|sample|file|voice)\b", low)
            or re.search(r"\b(my|this|the)\s+(recording|audio|clip|sample|voice)\b", low)):
        _m = re.search(r"\b(?:called|named|name it|as)\s+([A-Za-z0-9_ -]{1,40})", raw)
        _f = re.search(r"([~/\w. -]+\.(?:wav|mp3|mp4|m4a|ogg|flac|aac|webm))\b", raw, re.I)
        _args = {"query": raw}
        if _m:
            _args["name"] = _m.group(1).strip()
        if _f:
            _args["file"] = _f.group(1).strip()
        return _mk("CREATE_VOICE", _args, 0.9, matched_by="voice.create")

    # DESIGN_VOICE — build your own voice from a base + pitch/speed (no audio clip needed).
    # "design a voice called Nova, deep and slow". Sits AFTER voice.create so a request that
    # names an audio file still clones rather than designing from scratch.
    if (re.search(r"\b(design|make|create|build|set\s*up)\b", low)
            and re.search(r"\bvoice\b", low)
            and re.search(r"\b(?:called|named|name it)\s+[A-Za-z0-9_-]", raw, re.I)):
        _dm = re.search(r"\b(?:called|named|name it)\s+([A-Za-z0-9_ -]{1,40})", raw, re.I)
        _dargs = {"query": raw}
        if _dm:
            _dargs["name"] = _dm.group(1).strip().rstrip(",.")
        _p = re.search(r"\bpitch\s*([+-]?\d+(?:\.\d+)?)", low)
        _sp = re.search(r"\bspeed\s*(\d+(?:\.\d+)?)", low)
        if _p:
            _dargs["pitch"] = _p.group(1)
        if _sp:
            _dargs["speed"] = _sp.group(1)
        # plain-English shaping
        if re.search(r"\b(deep|low|lower)\b", low) and not _p:
            _dargs["pitch"] = -2.0
        if re.search(r"\b(high|higher|bright)\b", low) and not _p:
            _dargs["pitch"] = 2.0
        if re.search(r"\b(slow|slower)\b", low) and not _sp:
            _dargs["speed"] = 0.9
        if re.search(r"\b(fast|faster|quick)\b", low) and not _sp:
            _dargs["speed"] = 1.1
        return _mk("DESIGN_VOICE", _dargs, 0.9, matched_by="voice.design")

    if re.search(r"\bvoices?\b|\baccents?\b", low):
        _has_dl = bool(re.search(r"\b(download|install|fetch|add|grab)\b", low))
        # LIST — asking what's available (no fetch/switch verb)
        if (re.search(r"\b(what|which|list|show|available)\b", low)
                and not _has_dl
                and not re.search(r"\b(use|switch|change|set|make)\b", low)):
            return _mk("LIST_VOICES", {}, 0.9, matched_by="voice.list")
        # DOWNLOAD — explicit fetch ("download a British voice", "get me a Scottish accent")
        if _has_dl or re.search(r"\bget\s+(?:me\s+)?(?:a|an|the|some)\b.*\b(voice|accent)\b", low):
            return _mk("DOWNLOAD_VOICE", {"query": raw}, 0.92, matched_by="voice.download")
        # SET — only when it resolves to a real voice, so "change your voice to
        # sarcastic" still falls through to persona tone (SET_COMMUNICATION_STYLE).
        if re.search(r"\b(use|switch|change|set|make)\b", low):
            try:
                from eli.runtime.voice_assets import resolve_voice_query
                if resolve_voice_query(raw).get("voice"):
                    return _mk("SET_VOICE", {"query": raw}, 0.9, matched_by="voice.set")
            except Exception:
                log.debug("voice.set resolve failed", exc_info=True)
        # Deliberately ends routing (was a bare `return None` inline): the turn
        # mentions voice/accent but names no action, so it is not ours to claim.
        return ROUTE_NONE

    # A bare install/download of a named voice id/accent with NO "voice" word,
    # e.g. "install en_US-amy-medium" or "download en_GB-alan". Gated on the query
    # actually resolving to a catalog voice, so "install firefox" is unaffected.
    if re.search(r"\b(download|install|fetch|add|get)\b", low):
        try:
            from eli.runtime.voice_assets import resolve_voice_query
            _vr = resolve_voice_query(raw)
            if _vr.get("voice") and _vr.get("kind") in ("id", "name", "accent"):
                return _mk("DOWNLOAD_VOICE", {"query": raw}, 0.9, matched_by="voice.download.byid")
        except Exception:
            log.debug("voice.download.byid resolve failed", exc_info=True)

    # ── Gaze engine control ────────────────────────────────────────────────────
    # Must sit before RUNTIME_AUDIT so "gaze status" / "gaze diagnostics" don't
    # get absorbed by the generic audit preempt.
    if re.search(
        r"\b(gaze\s*(engine|tracker|tracking|system)?\s*(status|diagnostics?|check|health|info))\b"
        r"|\b(is\s+gaze\s+(running|active|on|enabled))\b"
        r"|\b(gaze\s+engine\s+status)\b",
        low,
    ):
        return _mk("GAZE_STATUS", {}, 0.95, matched_by="gaze.status.preempt")

    # Dwell-click (accessibility): "enable gaze clicking" / "turn on dwell click" starts the
    # gaze engine AND switches on look-and-hold clicking — hands-free AND voice-free. Checked
    # first (higher priority) so "gaze clicking" doesn't fall through to plain gaze-enable.
    if re.search(
        r"\b(enable|start|turn\s+on|activate|switch\s+on)\s+(the\s+)?"
        r"(dwell[\s-]?click(ing)?|gaze[\s-]?click(ing)?|eye[\s-]?click(ing)?|click(ing)?\s+by\s+(gaze|eye|looking))\b"
        r"|\b(dwell[\s-]?click(ing)?|gaze[\s-]?click(ing)?)\s+(on|enable)\b",
        low,
    ):
        return _mk("GAZE_ENABLE", {"dwell": True}, 0.97, matched_by="gaze.dwell.enable.preempt")

    if re.search(
        r"\b(enable|start|turn\s+on|activate|switch\s+on)\s+(the\s+)?(gaze|gaze\s+engine|gaze\s+track(er|ing))\b"
        r"|\bgaze\s+(engine\s+)?(on|enable|start|activate)\b",
        low,
    ):
        return _mk("GAZE_ENABLE", {}, 0.96, matched_by="gaze.enable.preempt")

    if re.search(
        r"\b(disable|stop|turn\s+off|deactivate|switch\s+off)\s+(the\s+)?(gaze|gaze\s+engine|gaze\s+track(er|ing))\b"
        r"|\bgaze\s+(engine\s+)?(off|disable|stop|deactivate)\b",
        low,
    ):
        return _mk("GAZE_DISABLE", {}, 0.96, matched_by="gaze.disable.preempt")

    if re.search(
        r"\b(calibrate\s+gaze|gaze\s+calibr|run\s+gaze\s+calibr|gaze\s+setup)"
        r"|\b(calibrate\s+(the\s+)?gaze\s+engine)",
        low,
    ):
        return _mk("GAZE_CALIBRATE", {}, 0.95, matched_by="gaze.calibrate.preempt")

    # Gaze-cursor click: act on whatever the user is LOOKING at. The gaze engine
    # supplies the live screen point; GAZE_CLICK moves the cursor there and clicks.
    # EXPLICIT click commands ("double/left/right click") are unambiguous and route
    # always. But NATURAL-LANGUAGE targeting ("open it", "click that") only means a
    # gaze click when gaze tracking is actually ON — with gaze OFF, "open it" means
    # open the file/app just referenced, so it must fall through (the transcript's
    # "open it" mis-fired into a phantom GAZE_CLICK / "gaze enabled" hallucination
    # while gaze was disabled).
    if re.search(r"\bright[\s-]?click\b", low):
        return _mk("GAZE_CLICK", {"button": "right"}, 0.95, matched_by="gaze.click.right")

    return None

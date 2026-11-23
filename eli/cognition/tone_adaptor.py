"""Tone adaptor — decides the emotion ELI *expresses* and feeds every output channel.

Two independent identifiers read the emotional context, then ELI expresses a fitting
tone through words, voice and face at once:

  • **System 1 — acoustic** (`perception/voice_profile`): the user's *voice* this
    turn (arousal / valence / coarse emotion from prosody). Present only when the
    user spoke.
  • **System 2 — semantic** (`detect_text_emotion` here): the *content* of the
    conversation — what the user actually said. Always available.

`current_tone()` fuses the two into a read, maps it through a small response policy
to the register ELI should express (empathetic, not mimicry — a distressed user
gets *tender*, not *sad*), and lets an explicit user override win outright
("be comedic", "talk street"). The chosen tone drives:
  • `text_directive()`  → folded into the persona / system prompt,
  • `voice_prosody()`   → Piper params in tts_router,
  • `expression()`      → the avatar face.

Governed by ``ELI_TONE_ADAPT`` (default on). State is process-local + persisted so
an explicit override survives the session.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any, Dict, Optional, Tuple

from eli.cognition import emotion_palette as _palette

log = logging.getLogger(__name__)

_OVERRIDE: Optional[str] = None          # explicit user-set tone (wins over detection)
_OVERRIDE_TS: float = 0.0
# Whether _OVERRIDE is known: read from the settings once, or set or cleared here since. Re-read
# while it was None, a world thread reading the tone between clear_tone() dropping it and saving
# the cleared setting took the old tone back up and kept it.
_OVERRIDE_KNOWN = False


def enabled() -> bool:
    return os.environ.get("ELI_TONE_ADAPT", "1").strip().lower() not in {"0", "false", "no", "off"}


# ── System 2: semantic emotion from the conversation text ─────────────────────
# Ordered, most-specific first. Each maps a content pattern to a DETECTED emotion
# (what the user is feeling / the register they're using), not yet ELI's response.
_SEMANTIC_RULES: "list[Tuple[str, str]]" = [
    (r"\b(kill myself|end it all|want to die|self[- ]harm)\b", "sad"),   # safety-adjacent → tender response
    (r"\b(so sad|depressed|heartbroken|grieving|lost (my|someone)|miss (him|her|them))\b", "sad"),
    (r"\b(furious|livid|so angry|pissed off|fed up|sick of|hate this|infuriating)\b", "angry"),
    # Swearing at ELI. Live, "YOU DO NOT NEED TO SEARCH THE FUCKING WEB...!!" read as ecstatic.
    (r"\b(what the fuck|why the fuck|the fuck are you|wtf|for fuck'?s sake|fuck off|"
     r"you(?:'re| are)? (?:a )?(?:fucking )?(?:liar|lying|useless|idiot|stupid)|lying to me|"
     r"making shit up|piece of shit|fucking (?:liar|useless|idiot|joke))\b", "angry"),
    (r"\b(annoyed|irritating|ugh|frustrat\w+|come on|seriously\?)\b", "irritated"),
    (r"\b(lol|lmao|rofl|haha+|hehe|so funny|hilarious|joking|kidding)\b", "comedic"),
    # "!!" alone is emphasis, not joy: it is as often furious.
    (r"\b(amazing|incredible|can'?t wait|so excited|let'?s go+|yes+!+|woo+)\b", "ecstatic"),
    (r"\b(thank you so much|so happy|love (this|it)|brilliant|made my day)\b", "joyful"),
    (r"\b(confused|don'?t (get|understand)|makes no sense|lost|what do you mean)\b", "confused"),
    (r"\b(curious|wonder|how (does|do)|why does|what if|fascinat\w+|interesting)\b", "curious"),
    (r"\b(deadline|invoice|meeting|client|stakeholder|per my|regards|kindly)\b", "professional"),
    (r"\b(bro|bruh|fam|innit|deadass|no cap|lowkey|highkey|vibe|yo\b)\b", "street_smart"),
    (r"\b(chill|relax|no rush|take your time|it'?s fine|all good)\b", "calm"),
]


def detect_text_emotion(text: str) -> Tuple[Optional[str], float]:
    """System 2: coarse emotion from the user's words. (emotion|None, confidence)."""
    raw = str(text or "").lower()
    if len(raw.strip()) < 2:
        return (None, 0.0)
    # Shouting a complaint or an order in capitals.
    letters = [c for c in str(text or "") if c.isalpha()]
    if (len(letters) >= 12 and sum(c.isupper() for c in letters) / len(letters) >= 0.6
            and re.search(r"\b(you|why|not|never|stop|don'?t|didn'?t|wrong|fuck\w*)\b", raw)):
        return ("angry", 0.7)
    for pat, emo in _SEMANTIC_RULES:
        if re.search(pat, raw):
            return (emo, 0.7)
    return (None, 0.0)


# ── System 1: acoustic (reuse voice_profile's per-turn read) ──────────────────
def detect_voice_emotion() -> Tuple[Optional[str], float]:
    """System 1: the user's voice this turn, if they spoke. (emotion|None, conf)."""
    try:
        from eli.perception import voice_profile
        t = voice_profile.get_last_tone(max_age_s=15.0) or {}
    except Exception:
        return (None, 0.0)
    emo = str(t.get("emotion") or "").strip().lower()
    conf = float(t.get("emotion_confidence") or t.get("confidence") or 0.0)
    if emo and emo != "neutral" and conf > 0.0:
        return (emo, conf)
    # No categorical read → derive coarse arousal cue so a loud/animated voice still
    # nudges the tone rather than being ignored.
    arousal = float(t.get("arousal") or 0.0)
    if arousal >= 0.6:
        return ("ecstatic", min(0.5, arousal))
    return (None, 0.0)


# Fusion -> the emotion ELI expresses. Detected user emotion maps to the register ELI answers in:
# mirror where it builds rapport (playful with playful), soften where it helps (distress -> tender,
# heat -> calm). Deliberate, not sycophantic.
_RESPONSE_POLICY = {
    "sad": "tender", "angry": "calm", "irritated": "calm", "confused": "curious",
    "comedic": "playful", "ecstatic": "joyful", "excited": "ecstatic",
    "joyful": "happy", "happy": "happy", "curious": "curious",
    "professional": "professional", "street_smart": "street_smart", "calm": "calm",
}


def _read_this_turn() -> Tuple[Optional[str], float]:
    """(emotion, confidence) this turn's voice and words show of the user; (None, 0) when neither
    shows any."""
    v_emo, v_conf = detect_voice_emotion()
    t_emo, t_conf = detect_text_emotion(_last_user_text())
    # Agreement boosts confidence; else take the stronger read; voice slightly favoured
    # when both present (tone of voice is a strong signal).
    if v_emo and t_emo and v_emo == t_emo:
        return v_emo, min(0.95, v_conf + t_conf)
    if (v_conf * 1.05) >= t_conf and v_emo:
        return v_emo, v_conf
    if t_emo:
        return t_emo, t_conf
    return None, 0.0


def _fuse() -> Tuple[str, str, float, str]:
    """(expressed_tone, detected_emotion, confidence, whence) from the two systems. `whence` is
    "read" when this turn showed it, "state" when it is the standing affect state, "" for nothing."""
    detected, conf = _read_this_turn()
    whence = "read" if detected else ""
    if not detected:
        # Nothing read this turn: the state behind the reads (eli/cognition/affect_state) still stands,
        # and a mood does not end because one sentence carried no sign of it. Its sign shades the
        # reply, at the chance the state is meant; it is not a read of this turn (current_tone).
        detected, conf = _standing_read()
        if not detected:
            return ("neutral", "neutral", 0.0, "")
        whence = "state"
    expressed = _RESPONSE_POLICY.get(detected, "neutral")
    return (expressed, detected, conf, whence)


# The affect state's sign as an emotion the response policy knows (irritation is met calm).
from eli.cognition.emotion_timeline import STATE_AS_EMOTION as _STATE_AS_EMOTION  # noqa: E402
# current_tone() is read several times in one turn (the directive, the voice, the face, the world);
# the state behind the reads is read once per turn (keyed by the turn's request id), not once per
# call. Outside a turn (no request id) every call reads afresh, so nothing stale is ever served.
_standing_cache: Tuple[str, Tuple[Optional[str], float]] = ("", (None, 0.0))


def _standing_read() -> Tuple[Optional[str], float]:
    """(emotion, confidence) from the affect state when it stands clear of neutral; (None, 0) otherwise."""
    global _standing_cache
    try:
        from eli.kernel import request_context as _rc
        turn = str(_rc.request_id_var.get() or "")
    except (ImportError, AttributeError, LookupError):
        turn = ""
    if turn and _standing_cache[0] == turn:
        return _standing_cache[1]
    try:
        from eli.cognition.affect_state import estimate
        state = estimate()
    except (OSError, ValueError, TypeError, ImportError, AttributeError) as failure:
        log.debug("affect state unavailable to the tone: %s", failure)
        read: Tuple[Optional[str], float] = (None, 0.0)
    else:
        emotion = _STATE_AS_EMOTION.get(str(getattr(state, "label", "") or ""))
        read = (emotion, float(getattr(state, "confidence", 0.0) or 0.0)) if emotion else (None, 0.0)
    if turn:
        _standing_cache = (turn, read)
    return read


# ── Last-user-text side channel (set by the engine each turn) ─────────────────
_LAST_TEXT = ""


def note_user_text(text: str) -> None:
    """Engine calls this with the user's turn so System 2 has content to read."""
    global _LAST_TEXT
    _LAST_TEXT = str(text or "")


def _last_user_text() -> str:
    return _LAST_TEXT


# ── Explicit override control ─────────────────────────────────────────────────
def set_tone(name_or_phrase: str) -> Dict[str, Any]:
    """Pin the tone ELI expresses ("be comedic", "talk street"). Returns the resolved tone."""
    global _OVERRIDE, _OVERRIDE_TS
    resolved = _palette.resolve_tone(name_or_phrase) or (
        name_or_phrase if name_or_phrase in _palette.all_tones() else None)
    if not resolved:
        return {"ok": False, "error": f"no tone matches {name_or_phrase!r}",
                "available": _palette.list_tones()}
    global _OVERRIDE_KNOWN
    _OVERRIDE, _OVERRIDE_TS, _OVERRIDE_KNOWN = resolved, time.time(), True
    try:
        from eli.core.runtime_settings import save_settings
        save_settings({"expressed_tone": resolved})
    except Exception:
        log.debug("tone_adaptor: persist override failed", exc_info=True)
    sync_world(force=True)
    return {"ok": True, "tone": resolved, "desc": _palette.get_tone(resolved).get("desc", "")}


def clear_tone() -> Dict[str, Any]:
    """Drop the override — back to autonomous, emotion-adaptive expression."""
    global _OVERRIDE, _OVERRIDE_KNOWN
    _OVERRIDE, _OVERRIDE_KNOWN = None, True
    try:
        from eli.core.runtime_settings import save_settings
        save_settings({"expressed_tone": ""})
    except Exception:
        log.debug("tone_adaptor: clear override persist failed", exc_info=True)
    sync_world(force=True)
    return {"ok": True, "tone": "auto"}


def _load_override() -> Optional[str]:
    global _OVERRIDE, _OVERRIDE_KNOWN
    if _OVERRIDE_KNOWN:
        return _OVERRIDE
    try:
        from eli.core.runtime_settings import load_settings
        v = str((load_settings() or {}).get("expressed_tone", "")).strip()
    except Exception:
        log.debug("tone_adaptor: override load failed", exc_info=True)
        return None             # not known yet: read again next time
    if not _OVERRIDE_KNOWN:     # set or cleared here while the settings were being read
        _OVERRIDE, _OVERRIDE_KNOWN = (v or None), True
    return _OVERRIDE


def current_tone() -> Dict[str, Any]:
    """The tone ELI will express now: {tone, detected, source, confidence, read, read_confidence}.
    Override → 'override'; else the fused autonomous read → 'auto'; default neutral.

    `read`/`read_confidence` are what this turn itself showed of the user ("neutral", 0.0 when it
    showed nothing): the only thing the emotion timeline may record. `detected` can be the
    standing affect state, which shades the reply; recorded as a read, the state was fed its own
    output and confirmed itself every turn, long after the mood had gone."""
    if not enabled():
        return {"tone": "neutral", "detected": "neutral", "source": "disabled", "confidence": 0.0,
                "read": "neutral", "read_confidence": 0.0}
    ov = _load_override()
    if ov:
        read, read_conf = _read_this_turn()
        return {"tone": ov, "detected": ov, "source": "override", "confidence": 1.0,
                "read": read or "neutral", "read_confidence": read_conf if read else 0.0}
    expressed, detected, conf, whence = _fuse()
    own = whence == "read"
    return {"tone": expressed, "detected": detected, "source": "auto", "confidence": conf,
            "read": detected if own else "neutral", "read_confidence": conf if own else 0.0}


_CORE_GUARD = ("Keep your core personality exactly as it is — this shades HOW you deliver, "
               "never WHO you are; stay recognizably yourself underneath.")


def text_directive() -> str:
    """Situational tone directive folded into the system prompt. It makes ELI AWARE
    of when a different approach is more effective and shades its delivery that way —
    it does NOT overwrite its core personality (that stays fixed). Empty for neutral."""
    cur = current_tone()
    if cur["tone"] == "neutral":
        return ""
    flavour = _palette.get_tone(cur["tone"]).get("text", "")
    if cur["source"] == "override":
        # The user explicitly asked for this register — lean in, but stay ELI underneath.
        return f"The user asked you to lean {cur['tone']}: {flavour} {_CORE_GUARD}"
    # Autonomous: frame as situational efficiency, not an emotional takeover.
    note = ""
    if cur["detected"] not in ("neutral", cur["tone"]) and cur["confidence"] >= 0.5:
        note = (f"The user seems {cur['detected']}, so a {cur['tone']} approach reads best here. ")
    return (f"{note}Shade your delivery toward: {flavour} {_CORE_GUARD} "
            f"This is situational awareness for efficiency, not a change of character.")


def voice_prosody() -> Dict[str, Any]:
    """Piper prosody params (+pitch) for the current tone — consumed by tts_router."""
    return dict(_palette.get_tone(current_tone()["tone"]).get("voice") or {})


def expression() -> str:
    """Avatar face for the current tone — consumed by the world/avatar mapper."""
    return str(_palette.get_tone(current_tone()["tone"]).get("expression") or "neutral")


# ── Unified prompt + world sync (single source — prompt_assembly + synthesis) ─
_last_world_sync: tuple = ()


def voice_cue_block(max_age_s: float = 12.0) -> str:
    """Acoustic read from the user's voice this turn — for prompt injection."""
    if os.environ.get("ELI_VOICE_TONE", "1").lower() in {"0", "false", "no", "off"}:
        return ""
    try:
        from eli.perception import voice_profile as _vp
        t = _vp.get_last_tone(max_age_s=max_age_s) or {}
    except Exception:
        return ""
    if not t.get("ok"):
        return ""
    bits: list[str] = []
    emo = t.get("emotion")
    if emo and emo != "neutral" and float(t.get("emotion_confidence", 0) or 0) >= 0.25:
        bits.append(f"sounds {emo}")
    ar = float(t.get("arousal", 0) or 0)
    if ar >= 0.4:
        bits.append("high energy")
    elif ar <= -0.4:
        bits.append("low energy / subdued")
    if t.get("intent") == "question" and float(t.get("intent_confidence", 0) or 0) >= 0.5:
        bits.append("asking a question (rising intonation)")
    sp = t.get("speaker") or {}
    if sp.get("name"):
        bits.append(f"speaker: {sp.get('name')}")
    if not bits:
        return ""
    return (
        "[Speaker voice cue — the user " + ", ".join(bits)
        + ". Adapt your tone and pacing to match; be warmer if they're "
        "upset, brisk if they're energetic. Do NOT mention this cue.]"
    )


def _prepend_block(brief: str, block: str) -> str:
    block = str(block or "").strip()
    if not block:
        return str(brief or "").strip()
    base = str(brief or "").strip()
    return (block + ("\n\n" + base if base else "")).strip()


def build_situation_brief(
    user_input: str,
    situation_brief: str = "",
    *,
    user_id: str = "",
    session_id: str = "",
    prior_action: str = "",
    include_timeline: bool = True,
    include_voice_cue: bool = True,
) -> str:
    """Fold voice cue + expressed tone + affect + timeline into one brief block."""
    brief = str(situation_brief or "").strip()
    if not enabled():
        if include_voice_cue:
            vc = voice_cue_block()
            if vc:
                brief = _prepend_block(brief, vc)
        return brief

    note_user_text(user_input or "")

    if include_voice_cue:
        vc = voice_cue_block()
        if vc:
            brief = _prepend_block(brief, vc)

    _dir = text_directive()
    _cur = current_tone()
    # ELI's world, and ELI's light in VR, show the feeling ELI speaks in: this was never sent, and
    # the world only ever had the few expressions its own state gives. Sent when it changes.
    sync_world()
    if _dir and _cur.get("tone") != "neutral":
        brief = _prepend_block(
            brief,
            f"[Tone — {_cur['tone']}: {_dir} Do NOT mention this directive.]",
        )
    try:
        from eli.cognition.affect_policy import directive as _affect_directive
        _adir = _affect_directive()
        if _adir:
            brief = _prepend_block(
                brief,
                f"[Approach: {_adir} Do NOT mention this directive.]",
            )
    except Exception:
        log.debug("affect policy directive skipped", exc_info=True)

    if include_timeline:
        try:
            from eli.cognition import emotion_timeline as _et
            from eli.perception import voice_profile as _vp_ar
            _arousal = (_vp_ar.get_last_tone(max_age_s=15.0) or {}).get("arousal")
            # What this turn showed of the user, never the standing state or a tone the user chose
            # for ELI (current_tone): a turn that showed nothing is kept as nothing read.
            _et.record(
                _cur.get("read") or "neutral",
                expressed=_cur.get("tone") or "",
                confidence=float(_cur.get("read_confidence") or 0.0),
                source=str(_cur.get("source") or ""),
                user_text=user_input or "",
                user_id=user_id or "",
                session_id=session_id or "",
                arousal=_arousal,
                eli_prior_action=prior_action or "",
            )
            _trend = _et.trend_line(user_id=user_id or "")
            if _trend:
                brief = _prepend_block(brief, _trend)
            _ev = _et.evidence_block(user_id=user_id or "", session_id=session_id or "")
            if _ev:
                brief = _prepend_block(brief, _ev)
                _et.note_checkin(
                    _et.assess(user_id=user_id or "").get("state", ""),
                    _cur.get("detected") or "",
                )
        except Exception:
            log.debug("emotion_timeline inject skipped", exc_info=True)

    return brief


def sync_world(force: bool = False) -> None:
    """Push the current tone read into Eli's World (avatar awareness + journal)."""
    if not enabled() and not force:
        return
    cur = current_tone()
    key = (
        str(cur.get("tone") or ""),
        str(cur.get("detected") or ""),
        round(float(cur.get("confidence") or 0.0), 2),
    )
    global _last_world_sync
    if not force and key == _last_world_sync:
        return
    _last_world_sync = key
    try:
        from eli.world.world_event_bus import fire_social_context_event
        fire_social_context_event(
            detected=str(cur.get("detected") or "neutral"),
            expressed=str(cur.get("tone") or "neutral"),
            confidence=float(cur.get("confidence") or 0.0),
            expression=expression(),
            source=str(cur.get("source") or "auto"),
        )
    except Exception:
        log.debug("tone world sync skipped", exc_info=True)

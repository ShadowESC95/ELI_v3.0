"""Conversation mode: talking with ELI the way people talk, with no wake word for each turn.

While it is on, every turn the listener hears is for ELI (audio_stt.VoiceGate.set_conversing), ELI
answers in the fast reasoning mode and speaks the reply while it is being written
(speech_stream.SpeechStream), and the user may speak over it: the voice falls silent at the onset
of their speech (barge_in), and what they said decides what follows (heard):
- "wait", "hold on", "pause": ELI stays silent where it stopped; "go on", "continue": it goes on from there;
- "speak slower / faster / softer / louder", "calmer", "more excited", "more serious", "normally",
  "use the British voice", "switch to <voice>", "blend <voice> and <voice>": the voice changes now
  and the choice is kept (the saved tts_* settings, the tone, the voice);
- "stop", "stop talking", "that's enough": ELI stops talking and listens;
- "stop the conversation": it ends;
- anything else: the rest of ELI's reply is dropped and what was said is the next turn. The turn
  cut off is recorded as far as the user heard it (cut_off_record), so ELI knows where it stopped.
These are read here, from the words alone, before anything reaches the engine: the router reads
"speak slower" as something to say aloud, "switch to Emma" as a window to focus.

It ends by itself when the user has been quiet for longer than they have taken to answer ELI
before: half again the longest of their recent answers (VoiceGate's own slack), learnt from use and
kept; until one has been seen, the listener's armed window (ELI_STT_WAKE_ARM_TIMEOUT).

Nothing here plays, records or reaches the engine itself: the window owns the turn (its existing
send, cancel and stream) and this decides what each thing heard is.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)

STATES = ("off", "listening", "thinking", "speaking", "paused")

# ── what was said, read locally ──────────────────────────────────────────────
_LEAD = r"^(?:(?:okay|ok|so|um|uh|hey|eli|computer|please|now|then|and|just|can you|could you|would you|will you)[\s,]+)*"
_TAIL = r"(?:[\s,]+(?:please|thanks|thank you|now|then|eli|for me|a bit|a little))*[\s.!?]*$"


def _whole(pattern: str) -> re.Pattern:
    return re.compile(_LEAD + pattern + _TAIL, re.I)


# The start names the conversation itself: "let's talk" or "talk to me" typed into the chat is a
# message, and turning the microphone on for it would be a surprise.
_START = _whole(r"(?:(?:let'?s|lets)\s+have\s+a\s+conversation|"
                r"(?:start|begin|enter|turn\s+on|switch\s+on)\s+(?:a\s+|the\s+)?conversation(?:\s+mode)?|"
                r"conversation\s+mode(?:\s+on)?)")
_STOP = _whole(r"(?:(?:stop|end|exit|leave|quit|turn\s+off|switch\s+off|close)\s+(?:the\s+)?conversation(?:\s+mode)?|"
               r"conversation\s+mode\s+off|that'?s\s+all(?:\s+for\s+now)?|goodbye|bye(?:\s+for\s+now)?)")
_PAUSE = _whole(r"(?:wait|hold\s+on|hang\s+on|pause|one\s+(?:sec|second|moment|minute)|"
                r"(?:wait|hold\s+on|hang\s+on|just)\s+(?:a\s+)?(?:sec|second|moment|minute)|"
                r"wait\s+wait|stop\s+(?:a\s+)?(?:sec|second|moment))")
_HUSH = _whole(r"(?:stop|stop\s+(?:talking|speaking|there)|be\s+quiet|quiet|shush|hush|shut\s+up|"
               r"that'?s\s+enough|enough|never\s*mind|ok(?:ay)?\s+stop)")
_RESUME = _whole(r"(?:go\s+on|continue|carry\s+on|keep\s+going|go\s+ahead|resume|"
                 r"(?:you\s+can\s+|please\s+)?(?:go\s+on|continue|carry\s+on)|okay\s+go\s+on|sorry\s+go\s+on)")
_PACE = _whole(r"(?:(?:speak|talk|go|read)\s+(?:a\s+(?:bit|little)\s+|much\s+|even\s+)?"
               r"(?P<pace>slower|more\s+slowly|faster|quicker|more\s+quickly)|(?P<pace2>slow\s+down|speed\s+up))")
_LEVEL = _whole(r"(?:(?:speak|talk)\s+(?:a\s+(?:bit|little)\s+)?(?P<level>softer|more\s+softly|quieter|more\s+quietly|"
                r"louder|up|more\s+loudly)|(?P<level2>lower\s+your\s+voice|keep\s+it\s+down))")
_TONE = _whole(r"(?:speak|talk|sound|be|act)\s+(?:a\s+(?:bit|little)\s+|much\s+|more\s+|less\s+)*"
               r"(?P<tone>[a-z][a-z'-]*(?:\s+[a-z][a-z'-]*)?)")
_NORMAL = _whole(r"(?:(?:speak|talk)\s+normally|(?:go\s+)?back\s+to\s+(?:your\s+)?normal(?:\s+voice)?|"
                 r"normal\s+(?:voice|speed|pace)|reset\s+your\s+voice|your\s+usual\s+voice)")
_VOICE = _whole(r"(?:use|try|switch\s+to|change\s+to|go\s+with|change\s+your\s+voice\s+to|switch\s+your\s+voice\s+to)\s+"
                r"(?:the\s+|a\s+|an\s+)?(?P<voice>.+?)(?:\s+voice)?")
_BLEND = _whole(r"(?:blend|mix|combine)\s+(?:the\s+)?(?P<a>[a-z_ ]+?)(?:\s+voice)?\s+(?:and|with)\s+"
                r"(?:the\s+)?(?P<b>[a-z_ ]+?)(?:\s+voice)?")


@dataclass
class Control:
    """What a thing said asks of the conversation itself."""
    kind: str                                   # start stop pause resume pace level tone normal voice
    value: Any = None
    said: str = ""


def _tone_for(word: str) -> Optional[str]:
    """The palette's tone for a delivery word: "calmer" -> calm, "more excited" -> ecstatic."""
    from eli.cognition.emotion_palette import resolve_tone
    w = str(word or "").lower().strip()
    for cand in (w, re.sub(r"(?:ier)$", "y", w), re.sub(r"(?:er)$", "", w), re.sub(r"(?:r)$", "", w)):
        tone = resolve_tone(cand) if cand else None
        if tone:
            return tone
    return None


def _voice_for(query: str) -> str:
    """The voice a spoken name or kind names ("emma", "british", "a deep male voice"), or ""."""
    from eli.perception import tts_kokoro
    q = str(query or "").strip().lower()
    if not q:
        return ""
    by_name = {meta["name"].lower(): vid for vid, meta in tts_kokoro.VOICES.items()}
    if q in by_name:
        return by_name[q]
    vid = tts_kokoro.voice_id(q)
    if vid:
        return vid
    try:
        from eli.runtime.voice_assets import resolve_voice_query
        return str(resolve_voice_query(q + " voice").get("voice") or "")
    except Exception:
        log.debug("[CONVERSE] voice query not resolved", exc_info=True)
        return ""


def read_control(text: str, *, active: bool) -> Optional[Control]:
    """The conversation control `text` is, or None for anything else (a turn for ELI). Only the
    start is read while the conversation is off; the voice's own controls are read either way."""
    t = re.sub(r"\s+", " ", str(text or "")).strip()
    if not t:
        return None
    if _START.match(t):
        return Control("start", said=t)
    m = _PACE.match(t)
    if m:
        word = (m.group("pace") or m.group("pace2") or "").lower()
        return Control("pace", "slower" if ("slow" in word) else "faster", t)
    m = _LEVEL.match(t)
    if m:
        word = (m.group("level") or m.group("level2") or "").lower()
        return Control("level", "louder" if word in ("louder", "up", "more loudly") else "softer", t)
    if _NORMAL.match(t):
        return Control("normal", said=t)
    m = _BLEND.match(t)
    if m:
        a, b = _voice_for(m.group("a")), _voice_for(m.group("b"))
        from eli.perception import tts_kokoro
        blend = tts_kokoro.voice_id(f"{a}+{b}") if a and b else ""
        if blend:
            return Control("voice", blend, t)
    if not active:
        return None
    if _STOP.match(t):
        return Control("stop", said=t)
    if _PAUSE.match(t):
        return Control("pause", said=t)
    if _HUSH.match(t):
        return Control("hush", said=t)
    if _RESUME.match(t):
        return Control("resume", said=t)
    m = _TONE.match(t)
    if m:
        tone = _tone_for(m.group("tone"))
        if tone:
            return Control("tone", tone, t)
    m = _VOICE.match(t)
    if m:
        vid = _voice_for(m.group("voice"))
        if vid:
            return Control("voice", vid, t)
    return None


# ── the voice's delivery, kept in the settings ───────────────────────────────
def _pace_step() -> float:
    """How much one "slower" or "faster" changes the pace: half the spread of the paces the tone
    palette itself uses (its slowest and fastest tones)."""
    from eli.cognition.emotion_palette import all_tones, get_tone
    paces = [float(get_tone(n)["voice"].get("length_scale") or 1.0) for n in all_tones()]
    return (max(paces) - min(paces)) / 2.0 if paces else 0.0


# One "softer" or "louder": half or double the voice's power.
_LEVEL_STEP_DB = 10.0 * __import__("math").log10(2.0)


def apply_control(control: Control) -> Dict[str, Any]:
    """Make a voice control so and keep it. {"ok", "what": the delivery now, "kind"}."""
    from eli.core.runtime_settings import load_settings, save_settings
    from eli.perception import tts_router
    s = load_settings() or {}
    kind = control.kind
    if kind == "pace":
        from eli.cognition.emotion_palette import NEUTRAL_VOICE
        base = float(NEUTRAL_VOICE.get("length_scale", 1.0))
        cur = float(s.get("tts_length_scale") or base)
        step = _pace_step()
        new = cur + step if control.value == "slower" else cur - step
        new = max(step, new)                     # a pace of zero or less is no speech at all
        save_settings({"tts_length_scale": round(new, 4)})
    elif kind == "level":
        cur = tts_router.gain_db()
        new = cur - _LEVEL_STEP_DB if control.value == "softer" else cur + _LEVEL_STEP_DB
        save_settings({"tts_gain_db": round(new, 2)})
    elif kind == "tone":
        from eli.cognition import tone_adaptor
        res = tone_adaptor.set_tone(str(control.value))
        if not res.get("ok"):
            return {"ok": False, "kind": kind, "what": delivery_summary()}
    elif kind == "normal":
        from eli.cognition import tone_adaptor
        save_settings({"tts_length_scale": None, "tts_gain_db": None})
        tone_adaptor.clear_tone()
    elif kind == "voice":
        tts_router.set_active_voice(str(control.value))
    else:
        return {"ok": False, "kind": kind, "what": delivery_summary()}
    return {"ok": True, "kind": kind, "what": delivery_summary()}


def delivery_summary() -> str:
    """ELI's voice as it is set now: "Heart (US female) · slower · softer · calm"."""
    from eli.core.runtime_settings import load_settings
    from eli.perception import tts_router
    parts = [tts_router.voice_display_name(tts_router.get_active_voice()).split(" [")[0]]
    try:
        from eli.cognition.emotion_palette import NEUTRAL_VOICE
        base = float(NEUTRAL_VOICE.get("length_scale", 1.0))
        pace = float((load_settings() or {}).get("tts_length_scale") or base)
        if abs(pace - base) > 1e-6:
            parts.append(f"pace {base / pace:.2f}×")
        db = tts_router.gain_db()
        if abs(db) > 1e-6:
            parts.append(f"{'softer' if db < 0 else 'louder'} {abs(db):.0f} dB")
        from eli.cognition import tone_adaptor
        cur = tone_adaptor.current_tone() or {}
        if cur.get("source") == "override" and cur.get("tone") not in (None, "", "neutral"):
            parts.append(str(cur["tone"]).replace("_", " "))
    except Exception:
        log.debug("[CONVERSE] delivery summary incomplete", exc_info=True)
    return " · ".join(parts)


# ── the conversation ─────────────────────────────────────────────────────────
@dataclass
class Heard:
    """What a thing heard comes to: a control that was carried out, a turn for ELI, or nothing.
    `cut_off` is what ELI had said of the reply the user spoke over (None when nothing was cut)."""
    kind: str                                    # control | turn | none
    text: str = ""
    control: Optional[Control] = None
    result: Dict[str, Any] = field(default_factory=dict)
    cut_off: Optional[str] = None


class Conversation:
    """The state of a conversation and what each thing heard does to it. on_state(state) is told
    every change (the window's indicator)."""

    def __init__(self, on_state: Optional[Callable[[str], None]] = None,
                 clock: Callable[[], float] = time.monotonic):
        self._on_state = on_state
        self._clock = clock
        self._lock = threading.RLock()
        self.state = "off"
        self.stream = None                       # the SpeechStream of the reply being spoken
        self._quiet_since: Optional[float] = None
        self._barged = False
        self._gaps: List[float] = _load_gaps()

    # ── on, off and the state ───────────────────────────────────────────────
    @property
    def active(self) -> bool:
        return self.state != "off"

    def _set(self, state: str) -> None:
        if state == self.state:
            return
        self.state = state
        if state in ("listening", "paused"):
            self._quiet_since = self._clock()
        cb = self._on_state
        if cb is not None:
            try:
                cb(state)
            except Exception:
                log.debug("[CONVERSE] state listener failed", exc_info=True)

    def start(self) -> None:
        with self._lock:
            if not self.active:
                self._set("listening")

    def stop(self) -> None:
        with self._lock:
            stream, self.stream = self.stream, None
            if stream is not None:
                stream.stop()
            self._set("off")

    # ── the idle end, learnt from use ────────────────────────────────────────
    def idle_timeout(self) -> float:
        from eli.perception.audio_stt import WAKE_ARM_TIMEOUT, VoiceGate
        if not self._gaps:
            return float(WAKE_ARM_TIMEOUT)
        return max(self._gaps) * VoiceGate.CONTINUATION_SLACK

    def idle_due(self) -> bool:
        """The user has said nothing for longer than they take to answer: the conversation is over."""
        with self._lock:
            return (self.state in ("listening", "paused") and self._quiet_since is not None
                    and self._clock() - self._quiet_since > self.idle_timeout())

    def _note_answer_gap(self) -> None:
        if self.state == "listening" and self._quiet_since is not None and self.stream is None:
            from eli.perception.audio_stt import VoiceGate
            self._gaps = (self._gaps + [self._clock() - self._quiet_since])[-VoiceGate.CONTINUATIONS_KEPT:]
            _save_gaps(self._gaps)

    def note_activity(self) -> None:
        """The user is there (typing, a sound that was not speech for ELI): the quiet starts again."""
        with self._lock:
            if self.state == "listening":
                self._quiet_since = self._clock()

    # ── a reply ─────────────────────────────────────────────────────────────
    def thinking(self) -> None:
        with self._lock:
            if self.active:
                self._set("thinking")

    def begin_reply(self, make_stream: Callable[[], Any]):
        """The reply starts being written: a new stream to speak it (any earlier one stopped)."""
        with self._lock:
            if not self.active:
                return None
            old, self.stream = self.stream, make_stream()
            if old is not None:
                old.stop()
            return self.stream

    def spoke(self, stream) -> None:
        """A reply's stream has been heard to its end (or stopped)."""
        with self._lock:
            if self.stream is stream:
                self.stream = None
                if self.active:
                    self._set("listening")

    def speaking(self) -> None:
        with self._lock:
            if self.active and self.state != "paused":
                self._set("speaking")

    def ended_turn(self) -> None:
        """The turn ended with nothing to say aloud: back to listening."""
        with self._lock:
            if self.active and self.stream is None:
                self._set("listening")

    # ── the user over ELI ───────────────────────────────────────────────────
    def barge_in(self, _t: Optional[float] = None) -> None:
        """Speech began over ELI's voice: silent at once, the rest kept until what was said is known."""
        with self._lock:
            stream = self.stream
            if stream is not None and not stream.paused:
                stream.pause()
                self._barged = True
                self._set("listening")       # ELI is quiet and listening to them

    def barge_in_was_nothing(self) -> None:
        """The sound that silenced ELI held no words: it goes on."""
        with self._lock:
            if getattr(self, "_barged", False) and self.stream is not None:
                self._barged = False
                self.stream.resume()
                self._set("speaking")

    def heard(self, text: str, *, spoken: bool = True) -> Heard:
        """What a thing said (or typed) comes to."""
        with self._lock:
            control = read_control(text, active=self.active)
            if control is not None and control.kind in ("pause", "resume", "hush") and self.stream is None:
                control = None          # nothing of ELI's to pause: "pause", "stop" are for the music
            barged, self._barged = getattr(self, "_barged", False), False
            if control is None:
                if not self.active:
                    return Heard("turn", text)
                self._note_answer_gap()
                cut = None
                stream = self.stream
                if stream is not None and (barged or (spoken and self.state in ("speaking", "paused"))):
                    cut = stream.stop()
                    self.stream = None
                if self.stream is None:
                    self._set("thinking")    # a typed message while ELI speaks waits its turn: it goes on speaking
                return Heard("turn", text, cut_off=cut)
            kind = control.kind
            if kind == "start":
                self.start()
                return Heard("control", text, control)
            if kind == "stop":
                self.stop()
                return Heard("control", text, control)
            if kind == "pause":
                if self.stream is not None:
                    self.stream.pause()
                    self._set("paused")
                return Heard("control", text, control)
            if kind == "hush":
                # ELI stops talking and listens; the conversation goes on
                if self.stream is not None:
                    control.value = self.stream.stop()
                    self.stream = None
                self._set("listening")
                return Heard("control", text, control, cut_off=control.value)
            if kind == "resume":
                if self.stream is not None and self.stream.paused:
                    self.stream.resume()
                    self._set("speaking")
                return Heard("control", text, control)
            result = apply_control(control)
            if barged and self.stream is not None:
                self.stream.resume()       # the change is made; ELI goes on (its next pieces take it)
            return Heard("control", text, control, result)


def cut_off_record(heard: str) -> str:
    """How a reply the user spoke over is kept in the conversation: what they heard, and that it
    stopped there because they spoke."""
    heard = str(heard or "").strip()
    return f"{heard} … [the user spoke over me here and I stopped]" if heard else ""


# ── the answer gaps, kept between runs ───────────────────────────────────────
def _load_gaps() -> List[float]:
    try:
        from eli.core.runtime_settings import load_settings
        raw = (load_settings() or {}).get("conversation_answer_gaps") or []
        return [float(g) for g in raw if float(g) > 0]
    except Exception:
        log.debug("[CONVERSE] answer gaps not read", exc_info=True)
        return []


def _save_gaps(gaps: List[float]) -> None:
    try:
        from eli.core.runtime_settings import save_settings
        save_settings({"conversation_answer_gaps": [round(g, 2) for g in gaps]})
    except Exception:
        log.debug("[CONVERSE] answer gaps not kept", exc_info=True)

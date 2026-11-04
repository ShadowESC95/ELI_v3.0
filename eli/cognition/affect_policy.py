"""Affect policy — where how you feel changes what ELI *does*, not just how it sounds.

ELI already had a real affect stack: acoustic ``voice_profile``, semantic
``tone_adaptor``, the 20-tone ``emotion_palette``, and a persistent ``emotion_timeline``
with a per-user baseline. Trace the consumers, though, and all of it landed in exactly
two places — ``prompt_assembly`` (words, voice, face) and ``proactive_daemon`` (when to
check in). Emotion never reached routing, planning, or execution.

The consequence was empathy as performance: ELI could *sound* like it understood you
were frustrated while behaving identically to a good day — same hedging, same
interruptions, same length. This module turns the existing read into a small number of
explicit behavioural decisions.

Three deliberate constraints:

* **Narrow and named.** It emits four booleans, not a mood that silently rewrites
  behaviour. Anything acting on affect does so through this one surface, so the
  influence is auditable rather than diffuse.
* **Never invents a read.** It consumes what the existing detectors already produce.
  Below the confidence bar it returns the neutral policy and says so.
* **Stays out of crisis.** Genuine distress belongs to the crisis guard, which is a
  different mechanism with different guarantees. This module deliberately does not act
  on it — softening ELI's behaviour is not a substitute for that path.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict

from eli.utils.log import get_logger

log = get_logger(__name__)

# Below this the detectors are guessing. A guess should not change behaviour.
_MIN_CONFIDENCE = 0.55

# Frustration/anger: the user wants the thing to work, not a discussion about it.
_FRUSTRATED = {"angry", "irritated", "frustrated", "annoyed"}
# Low mood / fatigue: interrupting is a cost, so non-urgent initiative waits.
_LOW = {"sad", "tired", "exhausted", "down", "low"}
# Handled elsewhere, on purpose — see the module docstring.
_CRISIS = {"despairing", "hopeless", "suicidal"}


@dataclass(frozen=True)
class AffectPolicy:
    """What ELI should *do* differently right now."""
    prefer_brevity: bool = False       # cut hedging and preamble; answer, don't discuss
    defer_proactive: bool = False      # hold non-urgent initiative until later
    offer_to_take_over: bool = False   # after repeated failure, offer to do it directly
    prefer_certainty: bool = False     # avoid speculative answers; verify or say nothing
    detected: str = "neutral"
    confidence: float = 0.0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def active(self) -> bool:
        return (self.prefer_brevity or self.defer_proactive
                or self.offer_to_take_over or self.prefer_certainty)


_NEUTRAL = AffectPolicy(reason="no confident read")


def _read() -> tuple[str, float]:
    try:
        from eli.cognition import tone_adaptor
        cur = tone_adaptor.current_tone() or {}
        return str(cur.get("detected") or "neutral").lower(), float(cur.get("confidence") or 0.0)
    except Exception:
        log.debug("affect_policy: tone read unavailable", exc_info=True)
        return "neutral", 0.0


def _recent_failures(user_id: str = "") -> int:
    """How many of ELI's own recent turns the user reacted negatively to. Used only to
    decide whether offering to take over is warranted — a single bad turn is not a
    pattern, and offering too eagerly is its own kind of annoying."""
    try:
        from eli.cognition import emotion_timeline as et
        rows = et.recent(limit=6, user_id=user_id) or []
    except Exception:
        log.debug("affect_policy: timeline unavailable", exc_info=True)
        return 0
    n = 0
    for row in rows:
        detected = str((row or {}).get("detected") or "").lower()
        try:
            from eli.cognition.emotion_timeline import valence_of
            if valence_of(detected) == "negative":
                n += 1
            else:
                break  # only count an unbroken recent run
        except Exception:
            break
    return n


def _state(user_id: str = "") -> tuple[str, float]:
    """The user's affect as a state (eli/cognition/affect_state), not the last read alone: the
    label of its valence and the chance that label is meant. ("", 0.0) when nothing is read."""
    try:
        from eli.cognition.affect_state import estimate
        affect = estimate(user_id)
        return str(affect.label or ""), float(affect.confidence or 0.0)
    except (ImportError, OSError, ValueError) as failure:
        log.debug("affect_policy: state unavailable: %s", failure)
        return "", 0.0


def current_policy(user_id: str = "") -> AffectPolicy:
    """The behavioural policy implied by the user's affect: the state behind the reads, with the
    last read's own word for which feeling it is."""
    detected, confidence = _read()
    if detected in _CRISIS:
        # Not ours. Returning neutral keeps this module from quietly substituting a
        # tone tweak for the crisis path.
        return AffectPolicy(detected=detected, confidence=confidence,
                            reason="crisis handling owns this state")
    label, state_confidence = _state(user_id)
    if label:
        # a state stands: its own sureness is the measure, and a read of the other sign is a spike
        from eli.cognition.emotion_timeline import valence_of
        if label != "neutral" and valence_of(detected) != label:
            detected = {"negative": "frustrated", "positive": "happy"}.get(label, detected)
        confidence = state_confidence if label != "neutral" else min(confidence, state_confidence)
    if confidence < _MIN_CONFIDENCE or detected in ("neutral", "") or label == "neutral":
        return _NEUTRAL
    if detected in _FRUSTRATED:
        run = _recent_failures(user_id)
        return AffectPolicy(
            prefer_brevity=True,
            defer_proactive=True,
            prefer_certainty=True,
            offer_to_take_over=run >= 2,
            detected=detected, confidence=confidence,
            reason=(f"{detected} at {confidence:.2f}"
                    + (f"; {run} negative turns in a row" if run >= 2 else "")),
        )

    if detected in _LOW:
        return AffectPolicy(
            defer_proactive=True,
            prefer_brevity=True,
            detected=detected, confidence=confidence,
            reason=f"{detected} at {confidence:.2f} — holding non-urgent initiative",
        )

    return AffectPolicy(detected=detected, confidence=confidence,
                        reason="no behavioural change for this state")


def should_defer_proactive(user_id: str = "") -> tuple[bool, str]:
    """Convenience for the proactive path: hold initiative, and why."""
    p = current_policy(user_id)
    return p.defer_proactive, p.reason


def directive() -> str:
    """A short instruction for the prompt, so the *behaviour* change is stated rather
    than left for the model to infer from tone shading alone. Empty when inactive."""
    p = current_policy()
    if not p.active:
        return ""
    parts = []
    if p.prefer_brevity:
        parts.append("Answer directly and briefly — no preamble, no hedging, no restating the question.")
    if p.prefer_certainty:
        parts.append("Do not speculate; if you are not sure, check or say you don't know.")
    if p.offer_to_take_over:
        parts.append("This has failed more than once — offer to do it yourself rather than explaining again.")
    return " ".join(parts)

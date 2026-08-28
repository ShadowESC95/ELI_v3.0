"""Voice intent layer — partial STT, barge-in, and TTS cancel hooks.

Productizes streaming voice behavior cross-platform: works wherever STT/TTS
backends are available; degrades gracefully when they are not.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)

_BARGE_IN_RE = re.compile(
    r"\b(?:stop|cancel|wait|hold on|never mind|nevermind|shut up|quiet|pause)\b",
    re.I,
)
_CANCEL_TTS_RE = re.compile(
    r"\b(?:stop talking|stop speaking|be quiet|that's enough|enough)\b",
    re.I,
)

_lock = threading.Lock()
_tts_speaking = False
_tts_cancel_hook: Optional[Callable[[], None]] = None


@dataclass
class PartialIntent:
    text: str
    is_final: bool
    confidence: float = 0.0
    should_barge_in: bool = False
    should_cancel_tts: bool = False
    intent: str = "continue"
    ts: float = field(default_factory=time.time)


def register_tts_cancel_hook(fn: Callable[[], None]) -> None:
    """Register a callback invoked when the user barges in during TTS."""
    global _tts_cancel_hook
    with _lock:
        _tts_cancel_hook = fn


def set_tts_speaking(active: bool) -> None:
    global _tts_speaking
    with _lock:
        _tts_speaking = bool(active)


def is_tts_speaking() -> bool:
    with _lock:
        return _tts_speaking


def cancel_tts_if_speaking() -> bool:
    """Request TTS cancellation if speech is active. Returns True if cancelled."""
    with _lock:
        if not _tts_speaking or _tts_cancel_hook is None:
            return False
        hook = _tts_cancel_hook
    try:
        hook()
        set_tts_speaking(False)
        return True
    except Exception:
        log.debug("TTS cancel hook failed", exc_info=True)
        return False


def classify_partial_transcript(
    text: str,
    *,
    is_final: bool = False,
    confidence: float = 0.0,
) -> PartialIntent:
    """Classify a partial or final STT transcript for barge-in / cancel behavior."""
    t = str(text or "").strip()
    should_cancel = bool(_CANCEL_TTS_RE.search(t))
    should_barge = bool(_BARGE_IN_RE.search(t)) or should_cancel

    intent = "continue"
    if should_cancel:
        intent = "cancel_tts"
    elif should_barge and not is_final:
        intent = "barge_in"
    elif is_final and should_barge:
        intent = "interrupt"

    if should_barge and is_tts_speaking():
        cancel_tts_if_speaking()

    return PartialIntent(
        text=t,
        is_final=is_final,
        confidence=float(confidence or 0.0),
        should_barge_in=should_barge,
        should_cancel_tts=should_cancel,
        intent=intent,
    )


def on_partial_transcript(
    text: str,
    *,
    is_final: bool = False,
    confidence: float = 0.0,
    dispatch: Optional[Callable[[PartialIntent], None]] = None,
) -> PartialIntent:
    """Entry point for streaming STT backends."""
    intent = classify_partial_transcript(text, is_final=is_final, confidence=confidence)
    if dispatch is not None:
        try:
            dispatch(intent)
        except Exception:
            log.debug("partial intent dispatch failed", exc_info=True)
    return intent

"""The room's level at the microphone, followed as it changes, and the gate for speech set over it.

Every chunk the microphone delivers (audio_stt._ContinuousMic) is measured, in the unit the speech
detector compares (the RMS of its 16-bit samples). The room is what those chunks hold when nobody
is talking to ELI and ELI is not talking: a span the listener took as the owner's words
(mark_speech) and the chunks heard while ELI spoke (own_voice) are left out of it. The gate is the
lowest level at which the room, over its last LEARN_S, would not have started a phrase by the
listener's own rule (SpeechRecognition's: `phrase_s` of chunks over the gate with no silence
longer than `gap_s` between them), raised by Z deviations of a chunk's own level (an RMS of n
samples is off by 1/sqrt(2n) of itself, at random): what the room did not do in LEARN_S it may
just do in the next second by chance alone. So it sits just above a fan's hiss, and above the
loudest stretch a television or a music bed made; it rises with the room and comes back down when the room
goes quiet. A voice is heard by level alone when it is louder than that; over ELI's own music
near_speech tells it from the music instead.

It replaces a fixed starting threshold (1200), a switch for SpeechRecognition's own dynamic
threshold, a recalibration every 60 silent cycles and a self-heal that raised the gate after three
captures ran to their cap: a capture that ran to its cap with no words in it is room, so the gate
measured from the room is already above it on the next cycle.

The same robust rule finds the onset of speech over a background it has learnt (onset): ELI's own
voice at the mic, for barge-in when the sink's output cannot be compared (near_speech), or the room.
"""
from __future__ import annotations

import math
import threading
from collections import deque
from typing import Deque, List, Optional, Tuple

from eli.perception.near_speech import LEARN_S, NEED_FRAMES, Z

# 1.4826 × the median absolute deviation estimates a standard deviation (the normal distribution's
# own constant), as near_speech uses it.
_MAD_TO_SIGMA = 1.4826


def _robust(values: List[float]) -> Optional[Tuple[float, float]]:
    """(median, robust deviation) of `values`, None for none."""
    if not values:
        return None
    s = sorted(values)
    med = s[len(s) // 2] if len(s) % 2 else 0.5 * (s[len(s) // 2 - 1] + s[len(s) // 2])
    dev = sorted(abs(v - med) for v in s)
    mad = dev[len(dev) // 2] if len(dev) % 2 else 0.5 * (dev[len(dev) // 2 - 1] + dev[len(dev) // 2])
    return med, _MAD_TO_SIGMA * mad


def _starts_a_phrase(rows, gate: float, phrase_s: float, gap_s: float) -> bool:
    """Whether the room's chunks `rows` (t_end, seconds, log level, was speech) would have started a
    phrase over `gate` (a log level): `phrase_s` of them above it, no quiet stretch longer than
    `gap_s` between. A span of the owner's words breaks the room's stretch."""
    loud = quiet = 0.0
    for _t, s, lv, said in rows:
        if said:
            loud = quiet = 0.0
            continue
        if lv > gate:
            loud += s
            quiet = 0.0
            if loud >= phrase_s - 1e-9:
                return True
        else:
            quiet += s
            if quiet > gap_s:
                loud = quiet = 0.0
    return False


class NoiseFloor:
    """push(rms, seconds, t_end) every chunk; threshold() is the speech gate (RMS) the room's level
    sets now, None until the room has been heard for `min_seconds`."""

    def __init__(self, history_s: float = LEARN_S):
        self._lock = threading.Lock()
        self._history_s = float(history_s)
        self._room: Deque[Tuple[float, float, float]] = deque()     # (t_end, seconds, log rms)
        self._speech: Deque[Tuple[float, float]] = deque()          # spans heard as words
        self._level = 0.0
        self._quiet: Optional[Tuple[float, float]] = None           # the quietest (median, deviation)
        self._background_s = 0.0
        self._samples = 0                                           # samples in a chunk (its level's precision)

    # ── what the microphone hears ───────────────────────────────────────────
    def push(self, rms: float, seconds: float, t_end: float, *, own_voice: bool = False,
             samples: int = 0) -> None:
        rms = max(float(rms), 1.0)                                  # 1 LSB: a digital silence's floor
        with self._lock:
            self._level = rms
            if samples > 0:
                self._samples = int(samples)
            if own_voice:
                return
            self._room.append((float(t_end), float(seconds), math.log(rms)))
            self._background_s += float(seconds)
            while self._room and self._room[0][0] < t_end - self._history_s:
                self._background_s -= self._room.popleft()[1]
            while self._speech and self._speech[0][1] < t_end - self._history_s:
                self._speech.popleft()

    def mark_speech(self, t0: float, t1: float) -> None:
        """The listener heard words between t0 and t1: those chunks were someone speaking, not the room."""
        with self._lock:
            self._speech.append((float(t0), float(t1)))

    def _background(self) -> List[float]:
        spans = list(self._speech)
        return [lv for t, s, lv in self._room
                if not any(a <= t - s / 2 <= b for a, b in spans)]

    # ── what follows from it ────────────────────────────────────────────────
    def stats(self, min_seconds: float = 0.0) -> Optional[Tuple[float, float]]:
        """(median, deviation) of the room's log level, None before `min_seconds` of it."""
        with self._lock:
            if self._background_s < float(min_seconds):
                return None
            st = _robust(self._background())
            if st is not None and (self._quiet is None or st[0] < self._quiet[0]):
                self._quiet = st
            return st

    def threshold(self, min_seconds: float = 0.0, phrase_s: float = 0.2, gap_s: float = 0.5) -> Optional[float]:
        """The speech gate (RMS) the room sets now; None before `min_seconds` of it."""
        with self._lock:
            if self._background_s < float(min_seconds) or not self._room:
                return None
            spans = list(self._speech)
            rows = [(t, s, lv, any(a <= t - s / 2 <= b for a, b in spans)) for t, s, lv in self._room]
        levels = sorted({lv for _t, _s, lv, said in rows if not said})
        if not levels:
            return None
        lo, hi = 0, len(levels) - 1          # the lowest level that starts no phrase (the top one never does)
        while lo < hi:
            mid = (lo + hi) // 2
            if _starts_a_phrase(rows, levels[mid], phrase_s, gap_s):
                lo = mid + 1
            else:
                hi = mid
        self.stats()                          # keeps the quietest room seen up to date
        chance = Z / math.sqrt(2.0 * self._samples) if self._samples else 0.0
        return math.exp(levels[lo] + chance)

    def level(self) -> float:
        """The last chunk's RMS, whatever it held (the level meter)."""
        return self._level

    def floor(self) -> Optional[float]:
        st = self.stats()
        return None if st is None else math.exp(st[0])

    def louder_than_usual(self) -> bool:
        """The room is clearly louder now than the quietest it has been heard (music, a crowd): what
        the microphone takes in then measures the room as much as a voice."""
        st = self.stats()
        quiet = self._quiet
        return bool(st and quiet and st[0] > quiet[0] + Z * max(quiet[1], 1e-6))

    def snapshot(self, phrase_s: float = 0.2, gap_s: float = 0.5) -> dict:
        """{level, floor, threshold} in RMS, for the meter and the diagnostics."""
        st = self.stats()
        gate = self.threshold(0.0, phrase_s, gap_s)
        return {"level": round(self._level, 1),
                "floor": round(math.exp(st[0]), 1) if st else None,
                "threshold": round(gate, 1) if gate else None,
                "seconds_heard": round(self._background_s, 2)}


class LevelOnset:
    """Onsets of sound clearly above a background learnt from the frames it is shown: frame(log
    level) is True on the NEED_FRAMES-th frame in a row past the background's median + Z robust
    deviations, the frames of a run kept out of what is learnt. learn_s of background before
    anything is judged."""

    def __init__(self, learn_s: float = LEARN_S, hop_s: float = 0.016):
        self._hop = float(hop_s)
        self._bg: Deque[float] = deque(maxlen=max(NEED_FRAMES * 4, int(learn_s / self._hop)))
        self._need = max(NEED_FRAMES * 4, int(learn_s / self._hop) // 4)
        self._run: List[float] = []
        self._in = False
        self._stats: Optional[Tuple[float, float]] = None
        self._since = 0

    @property
    def ready(self) -> bool:
        return len(self._bg) >= self._need

    def frame(self, log_level: float) -> bool:
        if self._stats is None or self._since >= NEED_FRAMES:
            self._stats = _robust(list(self._bg)) if self._bg else None
            self._since = 0
        self._since += 1
        st = self._stats
        if st is None or not self.ready:
            self._bg.append(log_level)
            return False
        if log_level > st[0] + Z * max(st[1], 1e-3):
            self._run.append(log_level)
            if len(self._run) == NEED_FRAMES and not self._in:
                self._in = True
                return True
            return False
        self._bg.extend(self._run if not self._in else [])
        self._run, self._in = [], False
        self._bg.append(log_level)
        return False

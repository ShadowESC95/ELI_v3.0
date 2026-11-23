"""Sound at the microphone that the machine's own output does not explain: someone speaking over
ELI's music.

The mic hears the music through the speakers, often louder than the person talking to ELI. Their
voice cannot be told from the music by its loudness (on synthetic fixtures an energy rise needed
the voice about 3 dB above the music), but the music is known: it is what the default sink plays,
read from the sink's monitor (OutputTap). The music's share at the mic is learnt band by band
(how the room carries each band, through a short adaptive filter over band energies, so the
speakers' and the mic's clocks may drift apart), and a frame is near speech when the mic holds
clearly more than that share in the speech bands. On synthetic fixtures (rap-like vocals over
drums, bass and chords through a speaker and a reverberant room, the mic's clock 80 ppm off)
this found 67 of 100 utterances at 6 dB below the music and 83 at 3 dB below, where loudness
alone found none, with 2 false onsets in 4 minutes of music alone; tests/test_stt_noise_self_heal.py
builds smaller ones.

audio_stt feeds the mic's samples as they are captured and lowers ELI's media at an onset, so
what follows the first syllables (the rest of the wake word, the command) is heard over quieter
music. Nothing here plays, records or changes a volume: OutputTap runs the system's own recorder
(parec or pw-record) on the monitor, which leaves routing and volumes as they are.
"""
from __future__ import annotations

import shutil
import subprocess
import threading
import time
from collections import deque
from typing import Callable, List, Optional

import numpy as np

from eli.utils.log import get_logger

log = get_logger(__name__)

SR = 16000
HOP_S = 0.016                 # one analysis frame per 16 ms, 32 ms windows half overlapping
HOP = int(SR * HOP_S)
WIN = 2 * HOP
# 24 bands over the speech range, log-spaced as hearing is
EDGES_HZ = np.geomspace(150.0, 7000.0, 25)
TAPS = 24                     # the room's tail per band: 24 frames, 384 ms
LEARN_S = 12.0                # music heard before anything is judged
MAX_LAG_S = 1.0               # output-to-mic delays searched: both streams' buffers and the room
MIN_ECHO_CORR = 0.3           # below this the mic does not hear the output (headphones): nothing to do
Z = 5.0                       # a frame is near speech past the median score + 5 robust deviations
NEED_FRAMES = 6               # for 6 frames running (96 ms)
SETTLE_S = 2.0                # after the output's level changes, its old level's tail dies away first
RELAG_EVERY_S = 5.0           # the delay is measured again this often while nobody speaks
MU_LEARN, MU_TRACK = 0.5, 0.05

_freqs = np.fft.rfftfreq(WIN, 1.0 / SR)
_BAND_MASKS = [(_freqs >= lo) & (_freqs < hi) for lo, hi in zip(EDGES_HZ[:-1], EDGES_HZ[1:])]
_WINDOW = np.hanning(WIN).astype(np.float32)
_RING_S = LEARN_S + MAX_LAG_S + TAPS * HOP_S + 4.0


class _Frames:
    """Samples in, band-energy frames out, each stamped with the time its last sample was heard:
    the stream's own sample clock, anchored to the wall clock (the streams are read as they arrive)
    and anchored again when the two part by more than four frames (samples lost, a stalled stream)."""

    def __init__(self):
        self._buf = np.zeros(0, dtype=np.float32)
        self._n = 0                      # samples received
        self._anchor: Optional[float] = None

    def push(self, x: np.ndarray, t_end: float) -> List[tuple]:
        x = np.asarray(x, dtype=np.float32).ravel()
        self._n += len(x)
        clock = None if self._anchor is None else self._anchor + self._n / SR
        if clock is None or abs(clock - t_end) > HOP_S * 4:
            self._anchor = t_end - self._n / SR
        start_n = self._n - len(self._buf) - len(x)     # sample number of self._buf[0]
        buf = np.concatenate([self._buf, x])
        out = []
        pos = 0
        while pos + WIN <= len(buf):
            spec = np.abs(np.fft.rfft(buf[pos:pos + WIN] * _WINDOW)) ** 2
            e = np.array([spec[m].sum() for m in _BAND_MASKS], dtype=np.float64)
            out.append((self._anchor + (start_n + pos + WIN) / SR, e))
            pos += HOP
        self._buf = buf[pos:]
        return out


class NearSpeech:
    """reference(samples, t) with what the sink plays, mic(samples, t) with what the mic hears, both
    16 kHz floats stamped with the time of their last sample; mic() returns the stamps of the onsets
    of near speech it found in them."""

    def __init__(self):
        self._lock = threading.Lock()
        self._ref = _Frames()
        self._mic = _Frames()
        n = int(_RING_S / HOP_S)
        self._ref_slots: dict = {}
        self._ref_order: deque = deque(maxlen=n)
        self._hist: deque = deque(maxlen=int((LEARN_S + MAX_LAG_S) / HOP_S))   # (slot, mic energy sum)
        self._lag: Optional[int] = None        # frames
        self._heard = False                    # the output is heard at the mic
        self._w = np.zeros((len(_BAND_MASKS), TAPS))
        self._w[:, 0] = 1e-3
        self._floor = np.full(len(_BAND_MASKS), 1e-9)
        self._scores: deque = deque(maxlen=int(LEARN_S / HOP_S))
        self._learnt = 0
        self._stats: Optional[tuple] = None
        self._run = 0
        self._in_speech = False
        self._frames = 0                       # mic frames seen: the detector's own clock
        self._hold_until = 0
        self._frozen = False
        self._next_relag = 0

    # ── the output ───────────────────────────────────────────────────────────
    def reference(self, x, t_end: float) -> None:
        with self._lock:
            for t, e in self._ref.push(x, t_end):
                slot = int(round(t / HOP_S))
                if len(self._ref_order) == self._ref_order.maxlen:
                    self._ref_slots.pop(self._ref_order[0], None)
                self._ref_order.append(slot)
                self._ref_slots[slot] = e

    def output_changed(self, *, lowered: bool) -> None:
        """ELI changed the output's level (lowered it, or put it back). Nothing is judged while it is
        lowered (there is nothing more to do) nor for SETTLE_S after a change (the room's tail of the
        old level), and nothing is learnt then: the room did not change, and the share learnt before
        holds again when the level is back, whether the monitor is read before or after the volume."""
        with self._lock:
            self._frozen = bool(lowered)
            self._hold_until = self._frames + int(SETTLE_S / HOP_S)
            self._run, self._in_speech = 0, False
            self._hist.clear()           # the step in level is not the room's delay: measured on what follows

    @property
    def active(self) -> bool:
        """The output is heard at the mic and its share there has been learnt."""
        return self._heard and self._stats is not None

    # ── the microphone ───────────────────────────────────────────────────────
    def mic(self, x, t_end: float) -> List[float]:
        onsets = []
        with self._lock:
            for t, e in self._mic.push(x, t_end):
                hit = self._frame(int(round(t / HOP_S)), e)
                if hit:
                    onsets.append(t)
        return onsets

    def _refs(self, slot: int) -> Optional[np.ndarray]:
        rows = [self._ref_slots.get(slot - self._lag - k) for k in range(TAPS)]
        if any(r is None for r in rows):
            return None
        return np.stack(rows, axis=1)                    # (bands, taps), newest first

    def _measure_lag(self) -> None:
        pairs = list(self._hist)
        mic = np.array([np.log(s + 1e-12) for _, s in pairs])
        best, best_lag = -1.0, None
        for lag in range(int(MAX_LAG_S / HOP_S) + 1):
            ref = [self._ref_slots.get(slot - lag) for slot, _ in pairs]
            ok = np.array([r is not None for r in ref])
            if ok.sum() < len(pairs) // 2:
                continue
            r = np.log(np.array([r.sum() for r in ref if r is not None]) + 1e-12)
            m = mic[ok]
            m, r = m - m.mean(), r - r.mean()
            c = float(np.dot(m, r) / (np.linalg.norm(m) * np.linalg.norm(r) + 1e-12))
            if c > best:
                best, best_lag = c, lag
        heard = best >= MIN_ECHO_CORR
        if heard != self._heard or best_lag != self._lag:
            log.debug(f"[NEAR_SPEECH] output heard at the mic: {heard} (correlation {best:.2f}, "
                      f"delay {0 if best_lag is None else best_lag * HOP_S:.3f}s)")
        self._heard = heard
        if heard:
            if self._lag is not None and best_lag != self._lag:
                # the same room, paired a frame or two differently: each band's filter moves with it
                # (tap k now holds the reference frame tap k + shift held), so nothing is learnt again
                shift = best_lag - self._lag
                moved = np.zeros_like(self._w)
                for k in range(TAPS):
                    if 0 <= k + shift < TAPS:
                        moved[:, k] = self._w[:, k + shift]
                self._w = moved
            self._lag = best_lag

    def _frame(self, slot: int, x: np.ndarray) -> bool:
        self._frames += 1
        now = self._frames
        steady = not self._frozen and now >= self._hold_until
        if steady:
            self._hist.append((slot, float(x.sum())))
        if steady and now >= self._next_relag and not self._in_speech and len(self._hist) >= self._hist.maxlen // 2:
            self._measure_lag()
            self._next_relag = now + int(RELAG_EVERY_S / HOP_S)
            if self._stats is not None and len(self._scores) > self._scores.maxlen // 4:
                h = np.array(self._scores)
                med = float(np.median(h))
                self._stats = (med, float(1.4826 * np.median(np.abs(h - med)) + 1e-6))
        if not self._heard or self._lag is None:
            return False
        r = self._refs(slot)
        if r is None:
            return False
        est = np.sum(self._w * r, axis=1)
        score = float(np.mean(np.maximum(np.log((x + 1e-12) / (est + self._floor + 1e-12)), 0.0)))
        if self._frozen or now < self._hold_until:
            # ELI holds the output lowered, or just changed its level: nothing is judged (there is
            # nothing more to do) and nothing learnt (the room is the same: the share learnt before
            # holds when the level is back)
            return False
        first = self._learnt < int(LEARN_S / HOP_S)     # the music's share not known yet
        quiet = self._stats is None or score < self._stats[0] + Z * self._stats[1]
        # learnt from music alone: at first, then while nobody speaks
        if first or quiet:
            err = x - est
            mu = MU_LEARN if first else MU_TRACK
            self._w += mu * err[:, None] * r / (np.sum(r * r, axis=1, keepdims=True) + 1e-12)
            np.maximum(self._w, 0.0, out=self._w)
            self._floor = 0.98 * self._floor + 0.02 * np.abs(err) * 0.1
        if first:
            self._learnt += 1
            self._scores.append(score)
            if self._learnt >= int(LEARN_S / HOP_S) and len(self._scores) > 10:
                h = np.array(self._scores)[len(self._scores) // 2:]     # once the filter has settled
                med = float(np.median(h))
                self._stats = (med, float(1.4826 * np.median(np.abs(h - med)) + 1e-6))
            return False
        if self._stats is None:
            return False
        if not self._in_speech:
            self._scores.append(score)             # the music's own spread, speech kept out of it
        if score > self._stats[0] + Z * self._stats[1]:
            self._run += 1
            if self._run == NEED_FRAMES and not self._in_speech:
                self._in_speech = True
                return True
        else:
            self._run = 0
            self._in_speech = False
        return False


class BargeIn:
    """The user starting to speak while ELI speaks (conversation mode): judge(x, t_end, near, onsets)
    with each 16 kHz mic chunk heard while ELI's voice plays gives the stamp of the onset, or None.

    Where the mic hears the output and NearSpeech has learnt its share there (speakers), its onsets
    decide: speech the output does not explain, ELI's voice and any music alike. Where the mic does
    not hear the output (headphones), or before that is known, the level at the mic decides, over a
    background learnt only from what the mic hears while ELI speaks (noise_floor.LevelOnset): with
    headphones that is the room, with speakers ELI's own voice there, so its own syllables are not
    taken for the user's until the output's share is learnt."""

    def __init__(self):
        from eli.perception.noise_floor import LevelOnset
        self._level = LevelOnset(hop_s=HOP_S)
        self._frames = _Frames()

    def judge(self, x, t_end: float, near: Optional["NearSpeech"], onsets: List[float]) -> Optional[float]:
        decided = near is not None and near.active
        hit = None
        for t, e in self._frames.push(x, t_end):
            if self._level.frame(float(np.log(e.sum() + 1e-12))) and hit is None and not decided:
                hit = t
        if decided:
            return onsets[0] if onsets else None
        return hit


def _tap_commands() -> List[list]:
    """The system recorders that read the default sink's monitor as 16 kHz mono 16-bit samples."""
    cmds = []
    if shutil.which("parec"):
        cmds.append(["parec", "--device=@DEFAULT_MONITOR@", f"--rate={SR}", "--channels=1",
                     "--format=s16le", "--latency-msec=20"])
    if shutil.which("pw-record"):
        cmds.append(["pw-record", "-P", "{ stream.capture.sink = true }", "--rate", str(SR),
                     "--channels", "1", "--format", "s16", "--raw", "-"])
    return cmds


class OutputTap:
    """Reads what the default sink plays into `sink(samples, t_end)` from a system recorder, in a
    thread, until stop(). start() is False when no recorder runs (no sound server, none installed)."""

    def __init__(self, sink: Callable[[np.ndarray, float], None]):
        self._sink = sink
        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    def start(self) -> bool:
        from eli.utils.platform_compat import sound_server
        if sound_server() not in ("pipewire", "pulseaudio"):
            return False
        for cmd in _tap_commands():
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0)
            except OSError:
                log.debug(f"[NEAR_SPEECH] {cmd[0]} did not start", exc_info=True)
                continue
            self._proc = proc
            self._thread = threading.Thread(target=self._read, daemon=True, name="eli-output-tap")
            self._thread.start()
            log.debug(f"[NEAR_SPEECH] reading the sink's output with {cmd[0]}")
            return True
        return False

    def _read(self) -> None:
        chunk = HOP * 4 * 2                       # 64 ms of 16-bit samples per read
        pipe = self._proc.stdout
        while not self._stop.is_set():
            data = pipe.read(chunk)
            if not data:
                break
            t = time.monotonic()
            pcm = np.frombuffer(data[: len(data) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
            try:
                self._sink(pcm, t)
            except Exception:
                log.debug("[NEAR_SPEECH] the output's samples were not taken", exc_info=True)
        if not self._stop.is_set():
            log.debug("[NEAR_SPEECH] the output recorder ended")

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        self._proc = None

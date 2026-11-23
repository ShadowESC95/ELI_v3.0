"""A reply spoken while it is being written: clause by clause, the next one rendering while one
plays, through one output that does not stop between them.

The voice used to wait for the whole reply, then rendered it sentence by sentence: on an i7-8700
the first sound of a one-sentence reply came 1.2 s after its last word, of a three-sentence one
3.8 s after (Kokoro renders at about 0.43 of real time). Here the reply's text is fed as the model
writes it (feed), and the voice starts on its first clause, as soon as what is rendered plays for
as long as the next piece takes to be written and rendered, at the paces measured so far (a short
first clause, "Sure,", waits for the next one). While a piece plays, the next one is
cut as long as the audio already waiting will cover its rendering at the slowest rate measured so
far (Clauses.take), whole sentences when they are there: more text in one piece is a more natural
reading, and a join inside a sentence happens only where waiting for the rest would leave a gap.
Each piece keeps its own closing mark, so a clause cut at a comma is read with a comma's
continuing tone and pause, not a sentence's fall; the tone's pause follows each sentence.

The output (DeviceOutput) is one PortAudio stream through the desktop's sound server for the whole
reply: pieces are queued into it, a short fade at each join, so nothing is reopened between them.
pause() holds the place (silence at once, the rest kept), resume() goes on from it, and stop()
silences it at once and drops the rest; heard() is the reply as far as it was heard, so a turn cut
off by the user can be recorded as what the user heard. While audio plays the speaking lock is held
(the microphone's echo guard) and ELI's face moves with the voice's loudness.
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import deque
from typing import Callable, Deque, List, Optional, Tuple

import numpy as np

from eli.utils.log import get_logger

log = get_logger(__name__)

# What of a reply is not read aloud: from the first block of code or command listing on (a repair's
# script read word by word). ConversationMixin._spoken_part cuts a finished reply at the same marks.
SPOKEN_CUT_MARKERS = ("```", "Exact command", "## Command", "Command(s):", "Commands:")
_MARKUP = re.compile(r"[*_`#>|\[\]~]")
_SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*(?=\s)")
_CLAUSE_END = re.compile(r"(?:[,;:]|\s[—–-])(?=\s)")
# a full stop after these is not a sentence's end
_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "st", "vs", "etc", "e.g", "i.e", "no", "approx", "jr", "sr"}
# a join is faded over one period of the lowest pitch a voice speaks at (about 60 Hz)
_FADE_S = 1.0 / 60.0


def speakable(text: str) -> str:
    """The part of `text` that is read aloud, without its markup and with its spaces collapsed."""
    text = str(text or "")
    cuts = [i for i in (text.find(m) for m in SPOKEN_CUT_MARKERS) if i >= 0]
    if cuts:
        text = text[:min(cuts)]
    text = re.sub(r"^\s*(?:As\s+ELI|ELI)\s*:\s*", "", text, flags=re.I)
    return re.sub(r"\s+", " ", _MARKUP.sub("", text))


class Clauses:
    """A reply's text as it arrives (feed, finish), in the pieces the voice says (take)."""

    def __init__(self, max_chars: Optional[int] = None):
        self._raw = ""
        self._done = False
        self._taken = 0
        self._max = int(max_chars or _chunk_chars())

    def feed(self, text: str) -> None:
        self._raw += str(text or "")

    def finish(self) -> None:
        self._done = True

    @property
    def finished(self) -> bool:
        return self._done and not self._rest().strip()

    def _text(self) -> str:
        return speakable(self._raw)

    def _rest(self) -> str:
        return self._text()[self._taken:]

    def _ends(self, rest: str) -> List[Tuple[int, bool]]:
        """(position just after it, ends a sentence) for every clause end in `rest` so far."""
        ends = []
        for m in _SENTENCE_END.finditer(rest):
            word = rest[:m.start()].rsplit(" ", 1)[-1].lower().rstrip(".")
            if word in _ABBREVIATIONS or re.fullmatch(r"\d+", word or "") and m.group(0) == ".":
                continue
            ends.append((m.end(), True))
        ends += [(m.end(), False) for m in _CLAUSE_END.finditer(rest)]
        if self._done and rest.strip():
            ends.append((len(rest), True))
        return sorted(set(ends))

    def take(self, fits: Optional[Callable[[int], bool]] = None) -> Optional[Tuple[str, bool]]:
        """The next piece and whether it ends a sentence, or None while none is complete.

        With nothing playing (`fits` None), the first clause, however long. While audio plays,
        `fits(n)` says whether n characters render before it runs out: the longest run of whole
        sentences that does, else of clauses; when the next clause does not, as many of its words
        as do (a join inside a clause rather than a silence); when not even that, a silence is
        coming whatever is taken, and the longest run of sentences there is (the fewest joins). A
        run with no mark at all is cut at a word once it outgrows the chunk size."""
        rest = self._rest()
        ends = [(p, s) for p, s in self._ends(rest) if rest[:p].strip()]
        pick = None
        if ends and fits is None:
            pick = ends[0]
        elif ends:
            within = [(p, s) for p, s in ends if fits(p)]
            if within:
                pick = ([e for e in within if e[1]] or within)[-1]
            else:
                words = [i for i in range(1, ends[0][0]) if rest[i] == " " and rest[:i].strip() and fits(i)]
                pick = (words[-1], False) if words else ([e for e in ends if e[1]] or ends)[-1]
        elif len(rest) > self._max:
            cut = rest.rfind(" ", 0, self._max)
            pick = ((cut if cut > 0 else self._max), False)
        if pick is None:
            return None
        piece = rest[:pick[0]]
        self._taken += pick[0]
        return piece.strip(), bool(pick[1])


def _chunk_chars() -> int:
    try:
        from eli.runtime.runtime_policy import tts_chunk_chars
        return int(tts_chunk_chars(360))
    except Exception:
        log.debug("[SPEECH] chunk size from the runtime policy unavailable", exc_info=True)
        return int(os.environ.get("ELI_TTS_CHUNK_CHARS", "360") or 360)


def _fade(x: np.ndarray, rate: int) -> np.ndarray:
    n = min(len(x) // 2, int(rate * _FADE_S))
    if n <= 1:
        return x
    x = np.array(x, dtype=np.float32, copy=True)
    ramp = 0.5 - 0.5 * np.cos(np.linspace(0.0, np.pi, n, dtype=np.float32))
    x[:n] *= ramp
    x[-n:] *= ramp[::-1]
    return x


class _Segment:
    __slots__ = ("samples", "text", "pos")

    def __init__(self, samples: np.ndarray, text: str):
        self.samples, self.text, self.pos = samples, text, 0


class _Queue:
    """The audio waiting to be heard, the place in it, and what of the text has been heard. Shared by
    the outputs: one fills it, the device's callback (or a player) empties it."""

    def __init__(self, rate: int):
        self.rate = int(rate)
        self.lock = threading.Lock()
        self.segments: Deque[_Segment] = deque()
        self.heard: List[str] = []
        self.partial = ""
        self.paused = False
        self.priming = True              # nothing plays until enough is rendered to play without a break
        self.stopped = False
        self.finished = False
        self.first_at: Optional[float] = None
        self.drained = threading.Event()

    def put(self, samples: np.ndarray, text: str) -> None:
        with self.lock:
            if not self.stopped:
                self.segments.append(_Segment(samples, text))
                self.drained.clear()

    def seconds_waiting(self) -> float:
        with self.lock:
            return sum(len(s.samples) - s.pos for s in self.segments) / float(self.rate)

    def pull(self, frames: int) -> np.ndarray:
        """`frames` samples for the device: the queue's next ones, silence while paused, stopped or
        waiting for the next piece."""
        out = np.zeros(frames, dtype=np.float32)
        with self.lock:
            if self.paused or self.stopped or self.priming:
                return out
            filled = 0
            while filled < frames and self.segments:
                seg = self.segments[0]
                n = min(frames - filled, len(seg.samples) - seg.pos)
                out[filled:filled + n] = seg.samples[seg.pos:seg.pos + n]
                seg.pos += n
                filled += n
                if self.first_at is None and n:
                    self.first_at = time.monotonic()
                if seg.pos >= len(seg.samples):
                    self.heard.append(seg.text)
                    self.segments.popleft()
            seg = self.segments[0] if self.segments else None
            if seg is not None and seg.text:
                words = seg.text.split()
                self.partial = " ".join(words[: int(len(words) * seg.pos / max(1, len(seg.samples)))])
            else:
                self.partial = ""
            if not self.segments and self.finished:
                self.drained.set()
        return out

    def heard_text(self) -> str:
        with self.lock:
            return " ".join([t for t in self.heard if t] + ([self.partial] if self.partial else [])).strip()


class DeviceOutput:
    """One PortAudio stream through the desktop's sound server (tts_router._portaudio_output) for a
    whole reply; open() is False where none is listed, and the system player plays instead
    (PlayerOutput)."""

    def __init__(self, on_audio: Optional[Callable[[np.ndarray, int, float], None]] = None):
        self._on_audio = on_audio
        self._stream = None
        self.q: Optional[_Queue] = None
        self.latency_s = 0.0

    def open(self) -> bool:
        try:
            import sounddevice as sd
            from eli.perception.tts_router import _portaudio_output
            route = _portaudio_output(sd)
            if route is None:
                return False
            device, label = route
            info = sd.query_devices(device) if device is not None else sd.query_devices(kind="output")
            rate = int(info["default_samplerate"] or 0) or 48000
            self.q = _Queue(rate)
            es = _expression()

            def _callback(outdata, frames, time_info, status):
                if status:
                    log.debug(f"[SPEECH] output status: {status}")
                block = self.q.pull(frames)
                outdata[:, 0] = block
                if es is not None:
                    try:
                        es.set_amplitude(min(1.0, float(np.sqrt(np.mean(block ** 2))) * 4.0))
                    except Exception:
                        log.debug("[SPEECH] lip-sync level not set", exc_info=True)
                if self._on_audio is not None and block.any():
                    self._on_audio(block, rate, time.monotonic() + self.latency_s)

            self._stream = sd.OutputStream(samplerate=rate, channels=1, callback=_callback, device=device,
                                           latency="low")
            self._stream.start()
            self.latency_s = float(getattr(self._stream, "latency", 0.0) or 0.0)
            log.debug(f"[SPEECH] streaming through {label} at {rate} Hz, {self.latency_s * 1000:.0f} ms latency")
            return True
        except Exception:
            log.debug("[SPEECH] no PortAudio stream for the voice; the system player plays", exc_info=True)
            self._stream = None
            return False

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                log.debug("[SPEECH] output stream did not close cleanly", exc_info=True)


class PlayerOutput:
    """The system player, piece by piece (no PortAudio device goes through the sound server): pause
    and stop take effect between pieces, as a player cannot be held mid-file."""

    def __init__(self, rate: int = 24000):
        self.q = _Queue(rate)
        self.latency_s = 0.0
        self._thread: Optional[threading.Thread] = None

    def open(self) -> bool:
        self._thread = threading.Thread(target=self._run, daemon=True, name="eli-speech-player")
        self._thread.start()
        return True

    def _run(self) -> None:
        from eli.perception import tts_kokoro, tts_router
        import tempfile
        q = self.q
        while True:
            with q.lock:
                seg = q.segments[0] if (q.segments and not q.paused and not q.stopped) else None
                done = q.stopped or (q.finished and not q.segments)
            if done:
                q.drained.set()
                return
            if seg is None:
                time.sleep(0.02)
                continue
            path = ""
            try:
                with tempfile.NamedTemporaryFile(prefix="eli_tts_", suffix=".wav", delete=False) as f:
                    f.write(tts_kokoro.to_wav(seg.samples, q.rate))
                    path = f.name
                if q.first_at is None:
                    q.first_at = time.monotonic()
                tts_router._play_wav_blocking(path, post_play_tail=0.0)
            finally:
                if path:
                    try:
                        os.unlink(path)
                    except OSError:
                        log.debug("[SPEECH] temporary voice file not removed", exc_info=True)
            with q.lock:
                if q.segments and q.segments[0] is seg:
                    q.segments.popleft()
                    q.heard.append(seg.text)

    def close(self) -> None:
        if self._thread is not None:
            self._thread.join(timeout=1.0)


def _expression():
    try:
        from eli.cognition import expression_state
        return expression_state
    except Exception:
        log.debug("[SPEECH] expression state unavailable", exc_info=True)
        return None


def _speaking_lock_path():
    from eli.perception.tts_router import _speaking_lock
    return _speaking_lock()


class SpeechStream:
    """feed() the reply as it is written, finish() at its end; the voice speaks it as it comes.

    `render(text) -> (samples, rate) | None` and `output` (an object with open(), close() and a
    `q`: _Queue) stand in for Kokoro or Qwen3-TTS and the sound device in tests."""

    def __init__(self, *, voice: Optional[str] = None, render=None, output=None,
                 on_audio: Optional[Callable[[np.ndarray, int, float], None]] = None,
                 hold_lock: bool = True, clock: Callable[[], float] = time.monotonic):
        from eli.perception import tts_router
        self._voice = voice                # None: the active voice, read for each piece (a change is heard at once)
        self._render = render or (lambda text: tts_router.render_samples(text, self._voice_now()))
        self._out = output
        self._on_audio = on_audio
        self._hold_lock = hold_lock
        self._clock = clock
        self._clauses = Clauses()
        self._cv = threading.Condition()
        self._thread: Optional[threading.Thread] = None
        self._renders: Deque[Tuple[int, float]] = deque(maxlen=6)     # (characters, seconds) of recent renders
        self._fed_at: Optional[float] = None
        self._pause_s = 0.0
        self._last_ended_sentence = False
        self._joins_inside_sentences = 0
        self._pieces = 0
        self._unrenderable = ""            # text a system voice must speak whole
        self._lock_held = False
        self._opened = False
        self._hold = False                 # paused by the user, before or after the output opened
        self.started = False

    def _voice_now(self) -> str:
        """The voice for the next piece: the one given, else the active one, or the one that speaks
        the reply's language when the active one does not (tts_router.voice_for_reply, read from all
        of the reply written so far, so its pieces keep one voice)."""
        from eli.perception import tts_router
        if self._voice:
            return self._voice
        return tts_router.voice_for_reply(self._clauses._text(), tts_router.get_active_voice())

    # ── text in ─────────────────────────────────────────────────────────────
    def feed(self, text: str) -> None:
        if not text:
            return
        with self._cv:
            if self._fed_at is None:
                self._fed_at = self._clock()
            self._clauses.feed(text)
            self._cv.notify_all()
        self._ensure_started()

    def finish(self) -> None:
        with self._cv:
            self._clauses.finish()
            self._cv.notify_all()
        self._ensure_started()

    # ── control ─────────────────────────────────────────────────────────────
    def pause(self) -> None:
        self._hold = True
        q = self._q()
        if q is not None:
            with q.lock:
                q.paused = True
        self._set_speaking(False)

    def resume(self) -> None:
        self._hold = False
        q = self._q()
        if q is not None:
            with q.lock:
                q.paused = False
                active = (bool(q.segments) or not q.finished) and not q.priming
        else:
            active = False
        if active:
            self._set_speaking(True)

    def stop(self) -> str:
        """Silence now, the rest dropped; what had been heard."""
        q = self._q()
        if q is not None:
            with q.lock:
                q.stopped = True
                q.segments.clear()
                q.drained.set()
        with self._cv:
            self._clauses.finish()
            self._cv.notify_all()
        self._set_speaking(False)
        return self.heard()

    @property
    def paused(self) -> bool:
        return self._hold

    @property
    def stopped(self) -> bool:
        q = self._q()
        return bool(q is not None and q.stopped)

    def wait(self, timeout: Optional[float] = None) -> bool:
        """Until everything fed has been heard (or it was stopped); True when it was."""
        t = self._thread
        if t is not None:
            t.join(timeout)
        q = self._q()
        return bool(q is None or q.drained.wait(timeout))

    def heard(self) -> str:
        q = self._q()
        return q.heard_text() if q is not None else ""

    def stats(self) -> dict:
        """first_audio_s (from the first text fed to the first sample heard), pieces, joins inside
        sentences, the slowest rendering measured (seconds per character)."""
        q = self._q()
        first = (q.first_at - self._fed_at) if (q is not None and q.first_at and self._fed_at) else None
        return {"first_audio_s": first, "pieces": self._pieces,
                "joins_inside_sentences": self._joins_inside_sentences,
                "render_model": self._render_model(),
                "output_latency_s": float(getattr(self._out, "latency_s", 0.0) or 0.0)}

    def _render_model(self) -> Optional[Tuple[float, float]]:
        """(seconds per piece, seconds per character) the voice has taken here, fitted to the last
        few renders; with one size seen, all of it per character (which overstates a longer piece's
        time, so it errs toward shorter pieces and no silence). None before the first."""
        pts = list(self._renders)
        if not pts:
            return None
        ns = np.array([n for n, _ in pts], dtype=float)
        ts = np.array([t for _, t in pts], dtype=float)
        if len(set(ns.tolist())) < 2:
            return 0.0, float(ts.max() / max(1.0, ns.max()))
        b, a = np.polyfit(ns, ts, 1)
        if b <= 0 or a < 0:
            return 0.0, float(np.max(ts / np.maximum(ns, 1.0)))
        return float(a), float(b)

    # ── the rendering thread ────────────────────────────────────────────────
    def _q(self) -> Optional[_Queue]:
        return getattr(self._out, "q", None) if self._out is not None else None

    def _ensure_started(self) -> None:
        with self._cv:
            if self.started:
                return
            self.started = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="eli-speech-render")
        self._thread.start()

    def _open_output(self, rate: int) -> bool:
        if self._opened:
            return True
        self._opened = True
        if self._out is None:
            self._out = DeviceOutput(self._on_audio)
            if not self._out.open():
                self._out = PlayerOutput(rate)
                self._out.open()
            return True
        return bool(self._out.open())

    def _run(self) -> None:
        try:
            self._render_all()
        except Exception:
            log.warning("[SPEECH] the reply's voice stopped", exc_info=True)
        finally:
            q = self._q()
            if q is not None:
                with q.lock:
                    q.finished = True
                    q.priming = False
                    if not q.segments:
                        q.drained.set()
                if q.segments:
                    self._set_speaking(not q.paused)
                q.drained.wait()
            if self._unrenderable.strip():
                from eli.perception import tts_router
                tts_router._run_tts(self._unrenderable.strip(), voice_name=self._voice_now())
            self._set_speaking(False)
            out = self._out
            if out is not None and hasattr(out, "close"):
                out.close()

    def _lead_needed(self) -> float:
        """How much audio must wait before the first plays, so the next piece is written and rendered
        before it runs out: the time the model takes to write a clause's worth more than is already
        here, at the pace it has written so far, and the time that clause takes to render here."""
        model = self._render_model()
        if model is None or not self._pieces:
            return 0.0
        a, b = model
        taken = self._clauses._taken
        clause = taken / float(self._pieces)                    # characters per piece so far
        pending = len(self._clauses._rest())
        written = len(self._clauses._text())
        elapsed = max(1e-6, self._clock() - (self._fed_at or self._clock()))
        pace = written / elapsed                                # characters a second the model writes
        to_write = max(0.0, clause - pending) / pace if pace > 0 else 0.0
        return to_write + a + b * max(clause, pending)

    def _fits(self) -> Optional[Callable[[int], bool]]:
        """Whether n characters render, at the rate measured here, before the audio already waiting
        has played; None before anything is rendered (the first clause goes at once)."""
        q, model = self._q(), self._render_model()
        if q is None or model is None:
            return None
        if q.priming:
            return lambda n: True           # nothing plays yet, so nothing can fall silent: all that is here
        waiting = q.seconds_waiting()
        a, b = model
        return lambda n: a + b * n <= waiting

    def _render_all(self) -> None:
        from eli.perception import tts_router
        try:
            self._pause_s = float(tts_router._prosody().get("sentence_silence") or 0.0)
        except Exception:
            log.debug("[SPEECH] sentence pause unavailable", exc_info=True)
        while True:
            q = self._q()
            if q is not None and q.stopped:
                return
            with self._cv:
                got = self._clauses.take(self._fits())
                if got is None:
                    if self._clauses.finished:
                        return
                    self._cv.wait(timeout=0.05)
                    continue
            piece, ends_sentence = got
            if not piece:
                continue
            t0 = self._clock()
            rendered = self._render(piece)
            took = self._clock() - t0
            if rendered is None:
                if self._q() is None and not self._pieces:
                    # a voice that cannot be rendered to samples (the system's own): spoken whole at the end
                    self._unrenderable += " " + piece
                    continue
                log.warning(f"[SPEECH] a piece was not rendered: {piece[:40]!r}")
                continue
            samples, rate = rendered
            self._renders.append((len(piece), took))
            if not self._open_output(int(rate)):
                return
            q = self._q()
            if self._hold:
                with q.lock:
                    q.paused = True                 # paused before anything could be heard
            x = np.asarray(samples, dtype=np.float32).ravel()
            if int(rate) != q.rate:
                from eli.perception.audio_samples import resample
                x = resample(x, int(rate), q.rate)
            if self._pieces and self._last_ended_sentence and self._pause_s > 0:
                x = np.concatenate([np.zeros(int(q.rate * self._pause_s), dtype=np.float32), x])
            if self._pieces and not self._last_ended_sentence:
                self._joins_inside_sentences += 1
            self._pieces += 1
            self._last_ended_sentence = ends_sentence
            q.put(_fade(x, q.rate), piece)
            if q.priming and (self._clauses.finished or q.seconds_waiting() >= self._lead_needed()):
                # what waits plays until the next piece is written and rendered: no silence after it
                with q.lock:
                    q.priming = False
            if not q.paused and not q.priming:
                self._set_speaking(True)

    # ── the microphone's echo guard and the face ────────────────────────────
    def _set_speaking(self, on: bool) -> None:
        if on == self._lock_held:
            return
        self._lock_held = on
        try:
            from eli.perception import voice_intent
            voice_intent.set_tts_speaking(on)
        except Exception:
            log.debug("[SPEECH] speaking flag not set", exc_info=True)
        es = _expression()
        if es is not None:
            try:
                es.set_speaking(on)
                if not on:
                    es.set_amplitude(0.0)
            except Exception:
                log.debug("[SPEECH] face speaking state not set", exc_info=True)
        if not self._hold_lock:
            return
        try:
            lock = _speaking_lock_path()
            if on:
                lock.write_text(str(os.getpid()), encoding="utf-8")
            else:
                lock.unlink(missing_ok=True)
        except OSError:
            log.debug("[SPEECH] speaking lock not changed", exc_info=True)

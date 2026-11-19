from __future__ import annotations
import os
import threading
import time as _time
from typing import Optional, Any


from eli.utils.log import get_logger
log = get_logger(__name__)

_broker: Optional["InferenceBroker"] = None
_broker_lock = threading.Lock()

# Time of the last foreground (user-facing) inference. Background daemon work checks
# foreground_recently_active() and yields, so it can't wedge itself between the calls of one
# foreground request on a slow model.
_last_foreground_ts = 0.0
_last_foreground_duration = 0.0   # seconds the most recent foreground turn took to generate
_MAX_ADAPTIVE_DEFER = 900.0       # cap so a pathological 20-min turn can't defer chores forever


def _is_think_only(response: str) -> bool:
    """True if a non-empty response is ENTIRELY a reasoning block that strips to
    empty (a thinking model that ran out of budget mid-<think>). Such a response
    is non-empty here but useless to the caller, so it should trigger the
    no-think retry just like a truly empty one. Best-effort; never raises."""
    if not response:
        return False
    try:
        from eli.cognition.gguf_inference import _strip_think_text
        return not _strip_think_text(response).strip()
    except Exception:
        return False


def stamp_foreground_activity() -> None:
    """The user is active: a turn started or a user-facing generation began. Background chores wait
    (foreground_recently_active). A turn that never uses the model counts too: a 109 s news synthesis
    started between two typed commands, because only model calls were counted."""
    global _last_foreground_ts
    _last_foreground_ts = _time.monotonic()


# User-facing generations in progress. "Busy" used to be "started within the last 45 s (or the last
# broker turn's length)", and streamed replies never go through the broker: 45 s into a 78 s reply the
# daemon judged the model free and started an insight call, which the user's next message waited for.
_fg_inflight = 0
_fg_inflight_lock = __import__("threading").Lock()


_last_foreground_end = 0.0        # wall-clock time the most recent foreground generation ended


def foreground_call_started() -> float:
    """A user-facing generation begins; returns its start time for foreground_call_finished. The
    first one in progress starts the GPU-use sampler (hardware_profile.gpu_use_during_last_generation)."""
    global _fg_inflight
    with _fg_inflight_lock:
        _fg_inflight += 1
        first = _fg_inflight == 1
    stamp_foreground_activity()
    if first:
        try:
            from eli.core.hardware_profile import start_gpu_use_sampling
            start_gpu_use_sampling()
        except Exception:
            log.debug("GPU use sampling not started", exc_info=True)
    return _time.monotonic()


def foreground_call_finished(started: float) -> None:
    """It ended: the model is free once no other is in progress; its length sets how long chores wait.
    The last one in progress stops the GPU-use sampler."""
    global _fg_inflight, _last_foreground_duration, _last_foreground_ts, _last_foreground_end
    with _fg_inflight_lock:
        _fg_inflight = max(0, _fg_inflight - 1)
        last = _fg_inflight == 0
    now = _time.monotonic()
    _last_foreground_ts = now
    _last_foreground_end = _time.time()
    if started:
        _last_foreground_duration = max(0.0, now - float(started))
    if last:
        try:
            from eli.core.hardware_profile import stop_gpu_use_sampling
            stop_gpu_use_sampling()
        except Exception:
            log.debug("GPU use sampling not stopped", exc_info=True)


def last_generation() -> dict:
    """The last user-facing generation: {"seconds" it took, "ended" (wall clock, 0 before one)}."""
    return {"seconds": float(_last_foreground_duration), "ended": float(_last_foreground_end)}


def foreground_in_progress() -> bool:
    return _fg_inflight > 0


def seconds_since_foreground() -> float:
    """Seconds since a conversation last used the model (a very large number if it has not yet)."""
    return (_time.monotonic() - _last_foreground_ts) if _last_foreground_ts else 1e9


BASE_WINDOW_S = 45.0
"""How long after a turn chores wait before any pause of the owner's has been measured, and the
least they ever wait."""
PACE_QUANTILE = 0.75
"""Which of the owner's own pauses between turns sets how long chores wait after one: most of them
(three in four). The longest is a real break, and waiting that long every time would hold chores
off for good."""
PACE_GAPS_KEPT = 20
"""How many of those pauses are kept: about one sitting's worth."""


class OwnerPace:
    """The owner's pace in this session, read off the model's foreground calls each daemon tick: the
    quiet between one turn's end and the next turn's start. Chores waited a fixed 45 s (or the last
    call's length), so self-repair's model calls started between the owner's commands every time
    and were mid-call when the next one came."""

    def __init__(self) -> None:
        self.gaps: list = []
        self._quiet: Optional[float] = None

    def observe(self) -> None:
        busy, since = foreground_in_progress(), seconds_since_foreground()
        if self._quiet is not None and (busy or since < self._quiet) and self._quiet < 1e8:
            self.gaps = (self.gaps + [round(self._quiet, 1)])[-PACE_GAPS_KEPT:]
        self._quiet = None if busy else since

    def usual(self) -> float:
        """The PACE_QUANTILE of the kept pauses; BASE_WINDOW_S until one is measured, and never less."""
        if not self.gaps:
            return BASE_WINDOW_S
        ordered = sorted(self.gaps)
        return max(BASE_WINDOW_S, ordered[int(PACE_QUANTILE * (len(ordered) - 1))])

    def longest(self) -> float:
        return max([BASE_WINDOW_S, *self.gaps])




_PACE = OwnerPace()


def owner_pace() -> OwnerPace:
    """The one measure of the owner's pace this session (the proactive daemon feeds it each tick)."""
    return _PACE


def background_defer_window() -> float:
    """How long background model work waits after the owner's last turn: ELI_BG_DEFER_WINDOW when it
    is set (0 turns the wait off), else the owner's usual pause between turns this session
    (OwnerPace). Two copies of a fixed 30 s used to decide this."""
    raw = os.environ.get("ELI_BG_DEFER_WINDOW", "").strip()
    if raw:
        try:
            return float(raw)
        except ValueError:
            log.debug("ELI_BG_DEFER_WINDOW is not a number: %r", raw)
    return _PACE.usual()


def foreground_recently_active(window: Optional[float] = None) -> bool:
    """True if a user-facing inference ran (or was running) recently: within `window` seconds, or by
    default within the owner's usual pause between turns (OwnerPace).

    The effective window scales UP with the last foreground turn's DURATION: on a slow /
    CPU-offloaded model where one turn takes minutes, background chores then defer for about
    as long as a turn takes (bounded by _MAX_ADAPTIVE_DEFER), so they don't fire in the brief
    gap right after a turn and collide with the user's next message. On fast hardware the
    duration is tiny, so the base `window` dominates and behaviour is unchanged. False, with no turn
    in progress, on the thread run_idle_work runs the owner's-pause work on: its caller says the
    owner is not waiting."""
    if _fg_inflight > 0:
        return True
    if getattr(_owner_away, "on", False):
        return False
    if window is None:
        window = _PACE.usual()
    try:
        eff = min(max(float(window), float(_last_foreground_duration)), _MAX_ADAPTIVE_DEFER)
        return (_time.monotonic() - _last_foreground_ts) < eff
    except Exception:
        return False


# Work that needs the model but must never hold a turn up (judging what the user's message said of the
# last reply): one worker runs it, in the context it was handed over in, once the foreground has been
# quiet for the owner's usual pause (background_defer_window), as background inference a turn preempts.
# The worker takes a piece of work only once the pause is over, so a caller that knows the owner is not
# waiting can run what waits there and then (run_idle_work).
_idle_jobs: Any = None            # (context, fn), oldest first
_idle_lock = threading.Lock()
_idle_cv = threading.Condition(_idle_lock)
_idle_running = 0
_owner_away = threading.local()   # set on the thread run_idle_work runs the work on


def when_idle(fn) -> None:
    """Run `fn()` on ELI's one background worker once no foreground turn is active. The caller's
    contextvars travel with it (the turn's user and session), and its generations are marked
    background, so a turn that starts meanwhile takes the model first."""
    global _idle_jobs
    import contextvars
    from collections import deque
    with _idle_cv:
        if _idle_jobs is None:
            _idle_jobs = deque()
            threading.Thread(target=_idle_worker, name="eli-when-idle", daemon=True).start()
        _idle_jobs.append((contextvars.copy_context(), fn))
        _idle_cv.notify_all()


def _take_idle_job():
    global _idle_running
    with _idle_cv:
        if not _idle_jobs:
            return None
        _idle_running += 1
        return _idle_jobs.popleft()


def _run_idle_job(job) -> None:
    global _idle_running
    ctx, fn = job
    try:
        ctx.run(_run_in_background, fn)
    except Exception:
        log.debug("idle-time work failed", exc_info=True)
    finally:
        with _idle_cv:
            _idle_running -= 1
            _idle_cv.notify_all()


def _idle_worker() -> None:
    while True:
        with _idle_cv:
            while not _idle_jobs:
                _idle_cv.wait()
        while foreground_recently_active(background_defer_window()):
            # wait out the owner's pause (or, with the wait turned off, as long as the last turn took)
            _time.sleep(max(background_defer_window(), float(_last_foreground_duration)) or BASE_WINDOW_S)
        job = _take_idle_job()
        if job is not None:
            _run_idle_job(job)


def run_idle_work(timeout: Optional[float] = None) -> bool:
    """The owner is not waiting on ELI now (the replay's sittings, whose turns are minutes apart on the
    script's clock: tools/eval/replay.py): what waits for their pause (when_idle) runs here, on this
    thread, as it would once they paused, and this returns once none of it waits or runs; False when
    `timeout` ran out first. Its model calls are background ones and go ahead (foreground_recently_active
    is False on this thread meanwhile); a turn started elsewhere still takes the model first."""
    deadline = None if timeout is None else _time.monotonic() + float(timeout)
    while True:
        _owner_away.on = True
        try:
            while (job := _take_idle_job()) is not None:
                _run_idle_job(job)
        finally:
            _owner_away.on = False
        with _idle_cv:
            if not _idle_jobs and not _idle_running:
                return True
            if not _idle_jobs:       # the worker took one meanwhile: it is done when it is done
                left = None if deadline is None else deadline - _time.monotonic()
                if left is not None and left <= 0:
                    return False
                _idle_cv.wait(left)


def _run_in_background(fn) -> None:
    try:
        from eli.cognition import gguf_inference as gi
        gi.set_background_inference(True)
    except ImportError:
        gi = None
    try:
        fn()
    finally:
        if gi is not None:
            gi.set_background_inference(False)


def get_inference_broker() -> Optional["InferenceBroker"]:
    global _broker
    if _broker is not None:
        return _broker
    with _broker_lock:
        if _broker is None:
            try:
                _broker = InferenceBroker()
            except Exception as e:
                log.debug(f"[BROKER] Failed to create InferenceBroker: {e}")
                return None
    return _broker


class InferenceBroker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._gguf: Any = None
        self._load_error: Optional[str] = None
        self._init_gguf()

    def _init_gguf(self) -> None:
        try:
            from eli.cognition import gguf_inference as gi
            self._gguf = gi
        except Exception as e:
            self._load_error = str(e)
            log.debug(f"[BROKER] gguf_inference unavailable: {e}")

    @property
    def gguf_ready(self) -> bool:
        if self._gguf is None:
            return False
        try:
            if hasattr(self._gguf, "is_loaded"):
                _probe = self._gguf.is_loaded
                # is_loaded is a FUNCTION here; bool() of it is always True, so
                # this reported "ready" whether or not a model was resident.
                return bool(_probe() if callable(_probe) else _probe)
            if hasattr(self._gguf, "model"):
                return self._gguf.model is not None
            return True
        except Exception:
            return False

    def infer(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 512,
        temperature: float = 0.7,
        top_p: float = 0.9,
        *,
        retry: bool = True,
        background: bool = False,
    ) -> str:
        from eli.plugins.sandbox import enforce
        enforce("model_access", "ask the language model")
        if not self.gguf_ready:
            raise RuntimeError("GGUF model not ready")
        global _last_foreground_ts, _last_foreground_duration
        # A call is background if the caller said so OR it runs on a thread already marked
        # background (the proactive daemon marks its loop thread, so all its work qualifies).
        gi = self._gguf
        try:
            background = bool(background) or bool(gi.is_background_inference())
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        # During shutdown don't start new background generations (they'd slow teardown); in-flight
        # ones abort via the stopping criterion. Foreground calls must still run: the end-of-session
        # summary is one, and blocking it meant the hand-off note was always the heuristic fallback.
        try:
            if background and self._gguf.is_shutting_down():
                return ""
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        # A foreground turn is live or just ran: don't let background work take the shared lock and
        # stall the user (prompt evaluation can't be preempted once started). Skip this cycle; the
        # daemon retries on a later idle tick. Window: ELI_BG_DEFER_WINDOW (0 disables).
        if background:
            _win = background_defer_window()
            if _win > 0 and foreground_recently_active(_win):
                return ""
        # Background generations are hard-capped so they can't hold the shared model lock for
        # minutes on a slow model, and they carry a cooperative abort (set per-thread below) so a
        # foreground turn preempts them. Cap is tunable via ELI_BG_MAX_TOKENS (default 256).
        if background:
            try:
                import os as _os
                _cap = int(_os.environ.get("ELI_BG_MAX_TOKENS", "256"))
            except Exception:
                _cap = 256
            if _cap > 0:
                max_tokens = min(int(max_tokens), _cap)
        # Foreground calls stamp the activity clock (before AND after, so the whole — possibly
        # multi-minute — generation counts as "active"); background calls never do.
        _fg_start = 0.0
        if not background:
            _last_foreground_ts = _time.monotonic()
            _fg_start = _last_foreground_ts
        # Arm the per-thread background flag so gguf_inference installs the abort hook even when
        # the caller (e.g. insight_synthesis) is on a thread not otherwise marked background.
        _armed = False
        try:
            if background and not gi.is_background_inference():
                gi.set_background_inference(True)
                _armed = True
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        try:
            with self._lock:
                response = self._call(prompt, system, max_tokens, temperature, top_p)
            if (not response or _is_think_only(response)) and retry:
                # A reasoning model can spend its whole budget inside <think> and return nothing, or
                # a think block that never closes. Force it closed on the retry so the budget goes
                # to the answer. A no-op for non-thinking models.
                try:
                    _nt_ctx = gi.force_no_think()
                except Exception:
                    from contextlib import nullcontext as _nullctx
                    _nt_ctx = _nullctx()
                with _nt_ctx, self._lock:
                    response = self._call(prompt, system, max_tokens, temperature, top_p)
        finally:
            if _armed:
                try:
                    gi.set_background_inference(False)
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
            if not background:
                _now = _time.monotonic()
                if _fg_start:
                    # Remember how long this turn took, so the deferral window adapts to it.
                    _last_foreground_duration = max(0.0, _now - _fg_start)
                _last_foreground_ts = _now
        if not response:
            raise RuntimeError("GGUF returned empty response after retry")
        return response

    def _call(self, prompt, system, max_tokens, temperature, top_p) -> str:
        gi = self._gguf
        gen_fn = getattr(gi, "generate", None)
        if callable(gen_fn):
            chunks = []
            for chunk in gen_fn(prompt, system=system, max_tokens=max_tokens,
                                temperature=temperature, stream=False):
                if isinstance(chunk, dict):
                    chunks.append(chunk.get("response") or chunk.get("token") or "")
                else:
                    chunks.append(str(chunk) or "")
            return "".join(chunks).strip()
        cc_fn = getattr(gi, "chat_completion", None)
        if callable(cc_fn):
            return (cc_fn(prompt, system=system, max_tokens=max_tokens,
                          temperature=temperature) or "").strip()
        raise RuntimeError("gguf_inference has neither generate() nor chat_completion()")

# the short name eli/coding/agent.py and the command dependency graph import
get_broker = get_inference_broker

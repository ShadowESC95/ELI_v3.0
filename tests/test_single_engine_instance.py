"""One CognitiveEngine per process.

From a live 2.2.5 log: after the startup dialog loaded the model, a SECOND full
engine init ran mid-session —

    [COGNITIVE] GGUF model loaded successfully          <- again
    [COGNITIVE] Habit scheduler started                 <- again
    [COGNITIVE] Self-improvement loop started           <- again
    [COGNITIVE] Proactive daemon already running - skipping duplicate start
    [COGNITIVE] Signal handler registration failed ... signal only works in main thread
    ...
    [COGNITIVE] Shutdown: complete.                     <- twice, the second saying
    [COGNITIVE] Shutdown: native teardown already done by another instance

The GUI built its engine directly and never published it, so the first
background caller of get_engine() (habits_scheduler, on its own thread) built
another one — with auto_init_gguf defaulting to True, hence the second model
load. Only the proactive daemon had its own duplicate guard; the habit
scheduler and self-improvement loop did not.
"""
import threading

from eli.kernel import engine as eng


def test_set_engine_exists_and_publishes():
    e = object()
    prev = eng._engine
    try:
        eng.set_engine(e)
        assert eng.get_engine() is e, "a published engine must be the one handed out"
    finally:
        eng._engine = prev


def test_get_engine_is_locked():
    """Two background threads racing would each build an engine, and the loser's
    daemons would already be running when it was discarded."""
    assert isinstance(eng._engine_lock, type(threading.Lock()))

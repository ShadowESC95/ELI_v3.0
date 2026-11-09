"""The daemon's foreground gate must be re-read, never snapshotted per tick.

Each block the gate protects runs multi-minute LLM work, so a reading taken at
the top of a tick is stale by the time later guards consult it. Live on a
CPU-offloaded 27B, one user turn waited behind BOTH a 241s news synthesis and a
156s self-improvement pass that started from a single reading taken seconds
before the user typed. The in-flight abort can yield a generation already
running; only a fresh reading stops the NEXT one starting.
"""

from eli.planning import proactive_daemon as pd


def test_gate_is_fail_open_when_the_broker_is_unavailable(monkeypatch):
    """A broken broker must not permanently silence the daemon."""
    import eli.cognition.inference_broker as br
    monkeypatch.setattr(br, "foreground_recently_active",
                        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("down")))
    assert pd._foreground_busy() is False


def test_gate_is_re_read_at_every_guard(monkeypatch):
    calls = {"n": 0}
    import eli.cognition.inference_broker as br

    def _counting(window=30.0):
        calls["n"] += 1
        return False

    monkeypatch.setattr(br, "foreground_recently_active", _counting)
    for _ in range(3):
        pd._foreground_busy()
    assert calls["n"] == 3, "the gate memoised instead of re-reading"


# ── how long chores wait comes from the owner's own pauses ──────────────────

def _broker(monkeypatch, state):
    import eli.cognition.inference_broker as br
    monkeypatch.setattr(br, "foreground_in_progress", lambda: state["busy"])
    monkeypatch.setattr(br, "seconds_since_foreground", lambda: state["since"])
    windows = []
    monkeypatch.setattr(br, "foreground_recently_active",
                        lambda window=30.0: windows.append(window) or state["since"] < window)
    return windows


def _sitting(pace, state, pauses):
    """The owner takes these pauses between turns: the daemon ticks through each one."""
    for pause in pauses:
        state["busy"] = True
        pace.observe()
        state["busy"], state["since"] = False, 0.0
        pace.observe()
        state["since"] = pause
        pace.observe()
    state["busy"] = True
    pace.observe()
    state["busy"] = False


def test_chores_wait_for_the_owners_usual_pause_not_a_fixed_45_s(monkeypatch):
    """At the owner's pace (about a minute and a half between commands) a fixed 45 s let the
    self-repair job start between commands every time."""
    state = {"busy": False, "since": 1e9}
    windows = _broker(monkeypatch, state)
    pace = pd._OwnerPace()
    monkeypatch.setattr(pd, "_PACE", pace)
    assert pace.usual() == pd.BASE_WINDOW_S, "nothing measured yet: the base wait"
    _sitting(pace, state, [80.0, 95.0, 70.0, 110.0, 3600.0])
    assert pace.gaps == [80.0, 95.0, 70.0, 110.0, 3600.0]
    assert pace.usual() == 110.0 and pace.longest() == 3600.0
    state["since"] = 60.0
    assert pd._foreground_busy() is True and windows[-1] == 110.0, "a chore started inside the owner's pause"
    state["since"] = 200.0
    assert pd._foreground_busy() is False


def test_a_job_of_many_model_calls_waits_until_the_owner_is_away(monkeypatch):
    state = {"busy": False, "since": 1e9}
    _broker(monkeypatch, state)
    pace = pd._OwnerPace()
    monkeypatch.setattr(pd, "_PACE", pace)
    _sitting(pace, state, [80.0, 300.0])
    state["since"] = 200.0
    assert pd._foreground_busy() is False and pd._owner_away() is False
    state["since"] = 301.0
    assert pd._owner_away() is True


def test_the_pace_is_the_brokers_and_every_background_wait_reads_it(monkeypatch):
    """The pace was the daemon's alone: the broker's default window and the model's background wait
    were each a fixed 30 s, so a chore the daemon held off still ran in the owner's pause."""
    import eli.cognition.inference_broker as ib
    assert pd._OwnerPace is ib.OwnerPace and pd._PACE is ib.owner_pace()
    pace = ib.OwnerPace()
    pace.gaps = [80.0, 95.0, 110.0]
    monkeypatch.setattr(ib, "_PACE", pace)
    monkeypatch.delenv("ELI_BG_DEFER_WINDOW", raising=False)
    assert ib.background_defer_window() == pace.usual() == 95.0
    monkeypatch.setattr(ib, "_fg_inflight", 0)
    monkeypatch.setattr(ib, "_last_foreground_duration", 0.0)
    import time
    monkeypatch.setattr(ib, "_last_foreground_ts", time.monotonic() - 60.0)       # a minute since the last turn
    assert ib.foreground_recently_active() is True, "inside the owner's usual pause"
    monkeypatch.setattr(ib, "_last_foreground_ts", time.monotonic() - 120.0)
    assert ib.foreground_recently_active() is False
    monkeypatch.setenv("ELI_BG_DEFER_WINDOW", "0")
    assert ib.background_defer_window() == 0.0, "the setting still turns the wait off"


def test_the_daemons_tick_never_starts_the_coding_agent(monkeypatch, tmp_path):
    """With a model loaded the ten-minute tick ran analyze_and_improve(repair=True): the coding agent's
    tens of model calls, on its own. The tick analyses only."""
    import eli.runtime.self_improvement as si
    from eli.cognition import gguf_inference as gi
    asked = []
    monkeypatch.setattr(si, "get_self_improvement", lambda: type("S", (), {
        "analyze_and_improve": lambda self, propose=None, repair=False: asked.append(repair) or {}})())
    monkeypatch.setattr(gi, "is_loaded", lambda: True, raising=False)
    monkeypatch.setattr(pd, "_owner_away", lambda: True)
    daemon = pd.ProactiveDaemon.__new__(pd.ProactiveDaemon)
    daemon.package_root = tmp_path                      # no source files to lint here
    daemon.agent_mem = type("M", (), {"log_improvement": lambda self, *a, **k: None})()
    daemon.analyze_code_quality()
    assert asked == [False]

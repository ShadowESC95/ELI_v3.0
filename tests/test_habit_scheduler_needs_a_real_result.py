"""A plain text reply is not proof a scheduled action ran, and a restart in the same minute must not run it twice."""
import sys
from types import SimpleNamespace

from eli.planning.habits_scheduler import HabitScheduler


def _scheduler(monkeypatch, tmp_path):
    monkeypatch.setattr("eli.planning.habits._artifacts_dir", lambda: tmp_path)
    monkeypatch.setattr("eli.planning.habits_scheduler.get_memory", lambda: SimpleNamespace(), raising=False)
    s = HabitScheduler.__new__(HabitScheduler)
    s._fired_keys = set()
    s.memory = SimpleNamespace(record_habit_run=lambda *a, **k: None)
    return s


def _engine(monkeypatch, result):
    monkeypatch.setitem(sys.modules, "eli.kernel.engine", SimpleNamespace(get_engine=lambda: SimpleNamespace(process=lambda cmd, source="": result)))


def test_text_only_is_unverified(monkeypatch, tmp_path, capsys):
    s = _scheduler(monkeypatch, tmp_path)
    _engine(monkeypatch, "Opened the editor for you.")
    s._execute_rule({"name": "r", "command": "open editor", "id": 1})
    out = capsys.readouterr().out
    assert "Success" not in out and "unverified" in out


def test_a_structured_ok_is_success(monkeypatch, tmp_path, capsys):
    s = _scheduler(monkeypatch, tmp_path)
    _engine(monkeypatch, {"ok": True, "content": "done"})
    s._execute_rule({"name": "r", "command": "open editor", "id": 1})
    assert "Success" in capsys.readouterr().out


def test_run_keys_survive_a_restart(monkeypatch, tmp_path):
    s = _scheduler(monkeypatch, tmp_path)
    s._fired_keys = {(3, "202609251200")}
    s._save_fired()
    assert (3, "202609251200") in s._load_fired()


def test_a_habit_the_shell_gate_refuses_is_reported_and_never_run(monkeypatch, tmp_path, capsys):
    """An imported habit store is not the user's own word: its commands meet the same shell gate as
    a typed command, and one it refuses never reaches the engine."""
    s = _scheduler(monkeypatch, tmp_path)
    ran, recorded = [], []
    monkeypatch.setitem(sys.modules, "eli.kernel.engine",
                        SimpleNamespace(get_engine=lambda: SimpleNamespace(process=lambda cmd, source="": ran.append(cmd))))
    s.memory = SimpleNamespace(record_habit_run=lambda *a, **k: recorded.append(a))
    monkeypatch.setattr("eli.core.full_control.is_full_control", lambda: False)
    s._execute_rule({"name": "tidy", "command": "rm -rf ~/projects", "id": 7})
    assert ran == [] and recorded == [], "a refused habit ran"
    assert "Refused" in capsys.readouterr().out
    s._execute_rule({"name": "editor", "command": "open the editor", "id": 8})
    assert ran == ["open the editor"], "an ordinary habit was stopped by the gate"


def test_a_habit_is_a_sentence_for_eli_and_only_a_shell_line_meets_the_gate(monkeypatch, tmp_path, capsys):
    """A habit's command is a sentence the engine reads; the gate's only-reads rule refused every
    ordinary one. A line written for the shell is still checked, and one it lets through goes on."""
    s = _scheduler(monkeypatch, tmp_path)
    ran = []
    monkeypatch.setitem(sys.modules, "eli.kernel.engine",
                        SimpleNamespace(get_engine=lambda: SimpleNamespace(
                            process=lambda cmd, source="": ran.append((cmd, source)) or {"ok": True})))
    monkeypatch.setattr("eli.core.full_control.is_full_control", lambda: False)
    monkeypatch.setenv("ELI_ALLOWED_CMDS", "")
    for i, cmd in enumerate(["watch the news", "start the music", "firefox", "ls ~/notes | wc -l"]):
        s._execute_rule({"name": f"h{i}", "command": cmd, "id": i})
    assert ran == [("watch the news", "habit"), ("start the music", "habit"), ("firefox", "habit"),
                   ("ls ~/notes | wc -l", "habit")]
    s._execute_rule({"name": "push", "command": "git push --force", "id": 9})
    assert len(ran) == 4 and "Refused" in capsys.readouterr().out


def test_a_habits_shell_line_waits_for_the_setting_that_allows_it(monkeypatch, tmp_path, capsys):
    """habit_shell_enabled promised a switch for shell lines from an imported habit store, and nothing
    read it. Off, a shell line is reported and never run; a sentence for ELI still runs."""
    s = _scheduler(monkeypatch, tmp_path)
    ran = []
    monkeypatch.setitem(sys.modules, "eli.kernel.engine",
                        SimpleNamespace(get_engine=lambda: SimpleNamespace(
                            process=lambda cmd, source="": ran.append(cmd) or {"ok": True})))
    monkeypatch.setattr("eli.core.full_control.is_full_control", lambda: False)
    monkeypatch.setenv("ELI_ALLOWED_CMDS", "")
    monkeypatch.setattr("eli.core.runtime_settings.load_settings", lambda *a, **k: {"habit_shell_enabled": False})
    s._execute_rule({"name": "count", "command": "ls ~/notes | wc -l", "id": 1})
    s._execute_rule({"name": "news", "command": "watch the news", "id": 2})
    assert ran == ["watch the news"]
    assert "habit_shell_enabled is off" in capsys.readouterr().out


def test_the_proactive_daemon_no_longer_fires_habits_itself():
    """Every habit ran twice: at its minute through the engine, and again by the daemon's tick as a
    raw shell line within five minutes of it."""
    import inspect
    from eli.planning import proactive_daemon
    assert not hasattr(proactive_daemon.ProactiveDaemon, "execute_habit")
    assert "shell=True" not in inspect.getsource(proactive_daemon)

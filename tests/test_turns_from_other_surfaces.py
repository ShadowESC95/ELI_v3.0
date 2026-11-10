"""The window shows turns said to ELI elsewhere (ELI VR, the web page) with where they were said, read
from the session id by the one map in eli/world/surfaces.py. The window kept its own copy of the map."""
import types

import pytest

pytest.importorskip("PySide6")


def test_the_window_names_where_a_turn_was_said_from_the_one_map(monkeypatch):
    from eli.gui.main_window._mixins.conversation import ConversationMixin
    from eli.world import surfaces
    shown = []

    class _Memory:
        def get_recent_conversation(self, limit=40, since=0.0):
            return [{"timestamp": since + 1, "session_id": "eli-vr-11", "role": "user", "content": "light the fire"},
                    {"timestamp": since + 2, "session_id": "watch-3", "role": "assistant", "content": "It is lit."},
                    {"timestamp": since + 3, "session_id": "kiosk-9", "role": "user", "content": "thanks"}]

    class _Window(ConversationMixin):
        _streaming = False
        _other_turns_seen = 1_790_000_000.0
        chat_response_signal = types.SimpleNamespace(emit=shown.append)

    monkeypatch.setattr("eli.memory.get_memory", lambda *a, **k: _Memory())
    monkeypatch.setattr(surfaces, "SURFACES", surfaces.SURFACES + (("watch-", "on the watch"),))
    _Window()._show_turns_from_other_surfaces()
    assert [line.split(" (")[1].split(",")[0] for line in shown] == ["in VR", "on the watch", "elsewhere"]


def test_a_turn_the_window_could_not_finish_is_logged_where_every_failure_is(monkeypatch, tmp_path):
    """The window kept its own failures table in the memory database, which no reader looked at, named
    by the person's own message: the self-repair loop never saw the window's faults."""
    from eli.gui.main_window._mixins.conversation import ConversationMixin
    from eli.memory.memory import Memory
    agent = Memory(db_path=str(tmp_path / "agent.sqlite3"), db_type="agent")
    monkeypatch.setattr("eli.memory.get_agent_memory", lambda *a, **k: agent)
    try:
        {}["missing"]
    except KeyError as failure:
        ConversationMixin._log_turn_failure(failure)
    conn = agent._get_connection()
    try:
        rows = conn.execute("SELECT command, error, source FROM failures").fetchall()
    finally:
        conn.close()
    assert len(rows) == 1 and rows[0][0] == "GUI chat turn" and rows[0][2] == "gui"
    assert "KeyError" in rows[0][1] and "Traceback" in rows[0][1], "its traceback is kept for self-repair"

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

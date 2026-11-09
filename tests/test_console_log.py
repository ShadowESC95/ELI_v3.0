"""ELI keeps its own console where it can read it (eli/utils/log.py).

Asked about the issues in its terminal, ELI had nothing to read: every log line went to the
terminal only, print() wrote straight to it, and the "Write session log to file" setting was saved
and read by nothing.
"""
from __future__ import annotations

import time

import pytest

from eli.utils import log as L


@pytest.fixture()
def kept(tmp_path):
    """Started in the test itself (pytest's capsys takes over the streams after fixtures run)."""
    path = tmp_path / "logs" / L.CONSOLE_LOG_NAME
    yield path
    L.stop_console_log()


def test_what_eli_logs_and_prints_is_kept_and_read_back(capsys, kept):
    assert L.keep_console_log(kept) == str(kept)
    start = time.time() - 1
    logger = L.get_logger("eli.test_console")
    logger.error("[TEST] the widget failed to load")
    logger.warning("[TEST] the widget is slow")
    print("a line printed by a module")
    print("a line printed by a module")
    for handler in (L._console["handler"],):
        handler.flush()
    got = L.read_own_log(path=kept)
    text = "\n".join(got["lines"])
    assert got["ok"] and "[TEST] the widget failed to load" in text and "a line printed by a module" in text
    assert got["errors"] >= 1 and got["warnings"] >= 1
    assert ("a line printed by a module", 2) in got["repeated"]
    assert "a line printed by a module" in capsys.readouterr().out, "the terminal still has it"
    assert L.read_own_log(path=kept, contains="widget", tail=1)["lines"][0].endswith("the widget is slow")
    assert L.read_own_log(path=kept, since=start)["lines"] and not L.read_own_log(path=kept, since=time.time() + 60)["lines"]


def test_the_session_log_setting_decides(monkeypatch):
    import sys
    from eli.core import runtime_settings

    stdout = sys.stdout
    monkeypatch.setattr(runtime_settings, "load_settings", lambda *a, **k: {"log_to_file": False})
    assert L.keep_console_log() is None and sys.stdout is stdout
    monkeypatch.setattr(runtime_settings, "load_settings", lambda *a, **k: {"log_to_file": True})
    try:
        from eli.core.paths import logs_dir
        assert L.keep_console_log() == str(logs_dir() / L.CONSOLE_LOG_NAME)
    finally:
        L.stop_console_log()
    assert sys.stdout is stdout


def test_a_file_grows_to_a_share_of_the_free_disk(kept):
    import shutil
    L.keep_console_log(kept)
    room = shutil.disk_usage(str(kept.parent)).free * L._LOG_SHARE_OF_FREE_DISK
    assert abs(L._console["handler"].maxBytes - room) <= max(room * 0.01, 1)


def test_the_window_shows_the_session_log_setting_as_it_is_by_default():
    """The log is on by default (runtime_settings.DEFAULTS), and the Settings page's box started off and
    read a missing key as off: a save from that page then wrote the log off."""
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from eli.core.runtime_settings import DEFAULTS
    from eli.gui.main_window._mixins.settings_model import SettingsModelMixin

    class _Page(SettingsModelMixin):
        def toggle_theme(self):
            pass
    page = _Page()
    widget = page._build_settings_app_page()       # kept, or Qt deletes the boxes with it
    assert page.log_to_file_checkbox.isChecked() is bool(DEFAULTS["log_to_file"]) is True
    text = __import__("pathlib").Path(__import__("eli.gui.main_window._mixins.settings_model",
                                                fromlist=["x"]).__file__).read_text(encoding="utf-8")
    assert 's.get("log_to_file", False)' not in text

"""Screen-control worker threads must marshal to the GUI thread via signals,
not QTimer.singleShot(0, ...) called from the worker thread itself (no
guaranteed event loop there). Found while porting v2.4.38: the real v3 GUI
entry point is eli.gui.main_window (via the gui_eli_original compat shim),
not the legacy eli_pro_audio_gui_v2_0.py file -- the fix has to live in
main_window/_mixins/screen_capture.py to actually be reachable at runtime.

Source-text checks only (not a live import) -- eli.gui.main_window's import
chain currently fails independently of this fix (eli.gui.panels._qt is
missing a QCheckBox export), which is a pre-existing gap unrelated to this
change and out of scope here.
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCREEN_CAPTURE = _ROOT / "eli" / "gui" / "main_window" / "_mixins" / "screen_capture.py"
_WINDOW = _ROOT / "eli" / "gui" / "main_window" / "window.py"


def test_screen_capture_worker_threads_use_signals_not_singleshot():
    src = _SCREEN_CAPTURE.read_text(encoding="utf-8")
    assert "QTimer.singleShot(0, lambda: self._sc_ocr_done" not in src
    assert "QTimer.singleShot(0, lambda: self._sc_eli_done" not in src
    assert "self._sc_ocr_done_sig.emit(" in src
    assert "self._sc_eli_done_sig.emit(" in src


def test_window_declares_and_connects_the_new_signals():
    src = _WINDOW.read_text(encoding="utf-8")
    assert "_sc_ocr_done_sig = pyqtSignal(str)" in src
    assert "_sc_eli_done_sig = pyqtSignal(str)" in src
    assert "self._sc_ocr_done_sig.connect(self._sc_ocr_done, Qt.ConnectionType.QueuedConnection)" in src
    assert "self._sc_eli_done_sig.connect(self._sc_eli_done, Qt.ConnectionType.QueuedConnection)" in src


# ── what the Screen tab's capture runs ──────────────────────────────────────────────────────────────
class _Recorder:
    """A button, label or signal stand-in that records what it was told (no window is made)."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


def _tab():
    from eli.gui.main_window._mixins.screen_capture import ScreenCaptureMixin

    class _Tab(ScreenCaptureMixin):
        def __init__(self):
            self._sc_capture_btn = _Recorder()
            self._sc_capture_sig = _Recorder()
            self._sc_screenshot_path = ""

    return _Tab()


def _capture(monkeypatch, taken):
    """Run the Screen tab's capture with os_controller's capture answering `taken`, on this thread."""
    import subprocess
    import tempfile
    from eli.gui.main_window._mixins import screen_capture as sc
    from eli.perception import os_controller

    def _never(*a, **k):
        raise AssertionError("the tab may not capture the screen a second way or leave a temp file")

    monkeypatch.setattr(os_controller, "take_screenshot", lambda region="full", *, keep=True: dict(taken))
    monkeypatch.setattr(subprocess, "run", _never)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _never)
    monkeypatch.setitem(__import__("sys").modules, "mss", None)       # importing it would fail loudly
    monkeypatch.setattr(sc.threading, "Thread", lambda target, daemon=None: type("T", (), {"start": staticmethod(target)})())
    tab = _tab()
    tab._sc_capture()
    return tab, [a[0] for name, a in tab._sc_capture_sig.calls if name == "emit"]


def test_a_capture_that_works_is_shown_from_where_it_was_taken(monkeypatch):
    tab, emitted = _capture(monkeypatch, {"ok": True, "path": "/data/screens/Screenshot_1.png"})
    assert emitted == ["/data/screens/Screenshot_1.png"]
    assert tab._sc_screenshot_path == "/data/screens/Screenshot_1.png"


def test_a_capture_that_fails_says_what_each_way_did_and_tries_no_second_chain(monkeypatch):
    """After os_controller failed, the tab ran its own mss/scrot/gnome-screenshot/import chain into a
    temp file it never removed, and said nothing of why the capture failed."""
    why = "No screenshot was taken: the desktop's screenshot portal timed out (no picture in time)."
    tab, emitted = _capture(monkeypatch, {"ok": False, "error": why, "attempts": [{"way": "portal"}]})
    assert emitted == [f"ERROR:{why}"]
    assert tab._sc_screenshot_path == ""


def test_an_analysis_that_fails_shows_its_own_reason(monkeypatch, tmp_path):
    """A failed vision action showed "I captured the screen but couldn't produce a description.", so
    a missing vision model looked the same as an empty description."""
    import eli.execution.executor_enhanced as ex
    from eli.gui.main_window._mixins import screen_capture as sc
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG")
    monkeypatch.setattr(ex, "execute", lambda action, args=None, **k: {
        "ok": False, "action": action, "error": "no vision model is installed", "content": ""})
    monkeypatch.setattr(sc.threading, "Thread", lambda target, daemon=None: type("T", (), {"start": staticmethod(target)})())
    tab = _tab()
    tab._sc_screenshot_path = str(shot)
    tab._text_backend_ready = lambda notify=False: object()
    tab._sc_ocr_text = type("Text", (), {"toPlainText": lambda self: ""})()
    tab._sc_analyse_btn, tab._sc_response, tab._sc_eli_done_sig = _Recorder(), _Recorder(), _Recorder()
    tab._sc_ask_eli()
    said = [a[0] for name, a in tab._sc_eli_done_sig.calls if name == "emit"]
    assert said and "no vision model is installed" in said[0]

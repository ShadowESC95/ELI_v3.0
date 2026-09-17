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

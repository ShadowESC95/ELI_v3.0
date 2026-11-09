"""Ollama model selector — worker-thread → GUI-thread marshalling.

Split out of test_gui_offscreen.py deliberately: that lane aborts partway through
(an unrelated widget test spawns a real Piper voice-asset download), so anything
sharing the file never gets to run. These need a real PySide6 + event loop, so
they run on the same offscreen discipline but stand alone.
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# Skip under the mocked full suite (PySide6 is a MagicMock — no real widgets).
try:
    from PySide6.QtWidgets import QApplication
    if type(QApplication).__name__ == "MagicMock":
        raise RuntimeError("PySide6 mocked")
except Exception as _e:  # pragma: no cover
    pytest.skip(f"offscreen GUI lane needs real PySide6 ({_e})",
                allow_module_level=True)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app



# --------------------------------------------------------------------------- #
# Ollama selector — worker-thread → GUI-thread marshalling
#
# Regression: _fetch_models runs on a plain threading.Thread and originally handed
# its result back with QTimer.singleShot(0, ...). A QTimer started from a thread
# with no Qt event loop never fires, so _update_ui was silently never called: the
# dropdown stayed empty and the status dot stuck on "Checking Ollama..." while the
# client underneath was returning models perfectly. Constructing the widget did NOT
# catch this — only asserting that the models actually LAND in the combo does.
# --------------------------------------------------------------------------- #
def _drive(qapp, predicate, timeout_ms=8000, step_ms=50):
    """Spin the real Qt event loop until predicate() is true or we time out."""
    from PySide6.QtCore import QDeadlineTimer, QEventLoop
    deadline = QDeadlineTimer(timeout_ms)
    while not deadline.hasExpired():
        qapp.processEvents(QEventLoop.AllEvents, step_ms)
        if predicate():
            return True
    return predicate()


@pytest.fixture
def _stub_ollama(monkeypatch):
    """Deterministic client — the test must not depend on a live Ollama daemon."""
    import eli.integrations.ollama.client as client
    monkeypatch.setattr(client, "is_running", lambda *a, **k: True, raising=False)
    monkeypatch.setattr(client, "list_models",
                        lambda *a, **k: ["alpha:7b", "beta:13b"], raising=False)
    monkeypatch.setattr(client, "get_active_model", lambda *a, **k: "beta:13b",
                        raising=False)
    monkeypatch.setattr(client, "ensure_server_running", lambda *a, **k: True,
                        raising=False)
    return client


def test_ollama_selector_populates_from_worker_thread(qapp, _stub_ollama):
    """Models fetched off-thread must reach the combo on the GUI thread."""
    from eli.gui.widgets.ollama_model_selector import OllamaModelSelector
    w = OllamaModelSelector()
    assert _drive(qapp, lambda: w._combo.count() > 0), \
        "dropdown never populated — worker result did not reach the GUI thread"
    items = [w._combo.itemText(i) for i in range(w._combo.count())]
    assert items == ["alpha:7b", "beta:13b"]
    assert w._combo.isEnabled()
    # The configured active model wins the restored selection.
    assert w.current_model() == "beta:13b"


def test_ollama_selector_reports_stopped_daemon(qapp, monkeypatch):
    """A stopped daemon must say so, not sit silently on an empty dropdown."""
    import eli.integrations.ollama.client as client
    monkeypatch.setattr(client, "is_running", lambda *a, **k: False, raising=False)
    monkeypatch.setattr(client, "list_models", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(client, "get_active_model", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(client, "ensure_server_running", lambda *a, **k: False,
                        raising=False)
    from eli.gui.widgets.ollama_model_selector import OllamaModelSelector
    w = OllamaModelSelector()
    assert _drive(qapp, lambda: w._combo.count() > 0), "no status ever surfaced"
    assert "not running" in w._combo.itemText(0)
    assert w.current_model() is None      # a status string is not a selectable model


def test_ollama_selector_reports_daemon_with_no_models(qapp, monkeypatch):
    """Running but empty is a different message from stopped."""
    import eli.integrations.ollama.client as client
    monkeypatch.setattr(client, "is_running", lambda *a, **k: True, raising=False)
    monkeypatch.setattr(client, "list_models", lambda *a, **k: [], raising=False)
    monkeypatch.setattr(client, "get_active_model", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(client, "ensure_server_running", lambda *a, **k: True,
                        raising=False)
    from eli.gui.widgets.ollama_model_selector import OllamaModelSelector
    w = OllamaModelSelector()
    assert _drive(qapp, lambda: w._combo.count() > 0)
    assert "No models" in w._combo.itemText(0)
    assert w.current_model() is None


def test_ollama_selector_marshals_over_signals_not_timers(qapp):
    """Guard the mechanism itself, so a refactor can't reintroduce the defect."""
    import inspect
    from eli.gui.widgets import ollama_model_selector as mod
    src = inspect.getsource(mod._fetch_models)
    # Strip comments/docstring mentions — we care about a real call, not the word.
    code = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "QTimer.singleShot(" not in code, \
        "_fetch_models runs off-thread; a QTimer started there never fires. Emit a signal."
    assert "models_ready.emit(" in code, "_fetch_models must hand back over the signal"
    assert "self" not in inspect.signature(mod._fetch_models).parameters


def test_a_selector_dropped_mid_fetch_is_not_destroyed_on_the_worker(qapp, _stub_ollama):
    """The worker used to hold the selector; dropping the selector while a fetch ran left the
    worker with its last reference, so the widget and its timer were destroyed on that thread
    ("Timers cannot be stopped from another thread"), and the next test's event loop crashed."""
    import gc
    import threading
    from eli.gui.widgets.ollama_model_selector import OllamaModelSelector
    before = {t.ident for t in threading.enumerate()}
    w = OllamaModelSelector()
    del w
    gc.collect()
    for t in [t for t in threading.enumerate() if t.ident not in before]:
        t.join(timeout=5)
    later = OllamaModelSelector()
    assert _drive(qapp, lambda: later._combo.count() > 0)


# --------------------------------------------------------------------------- #
# Pulling a model from the setup dialog: widgets touched on the GUI thread only,
# and Cancel stops the download. The dialog drove its progress dialog and message
# boxes from the pull thread, and Cancel was connected to nothing.
# --------------------------------------------------------------------------- #
class _PullResponse:
    """/api/pull as Ollama streams it, a line per progress step, closing when the client does."""

    def __init__(self, lines, endless=False):
        import json
        import threading
        self._lines = [json.dumps(line).encode() + b"\n" for line in lines]
        self._endless, self.closed, self.read = endless, threading.Event(), 0

    def readline(self):
        import json
        import time
        self.read += 1
        if self._lines:
            return self._lines.pop(0)
        if self._endless and not self.closed.is_set():
            time.sleep(0.01)
            return json.dumps({"status": "pulling", "total": 100, "completed": 1}).encode() + b"\n"
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed.set()


@pytest.fixture
def _pull_lane(qapp, monkeypatch):
    import threading
    from PySide6.QtWidgets import QMessageBox, QProgressDialog
    import eli.integrations.ollama.client as client
    said = []
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, _k=kind, **kw: said.append(
            (_k, threading.current_thread() is threading.main_thread(), a[2] if len(a) > 2 else "")))
    monkeypatch.setattr(QProgressDialog, "show", lambda self: None)     # nothing reaches a screen
    monkeypatch.setattr(client, "set_active_host", lambda host: None, raising=False)
    monkeypatch.setattr(client, "candidate_hosts", lambda *a, **k: ["http://127.0.0.1:9"], raising=False)
    monkeypatch.setattr(client, "_allow_via_netguard", lambda host: None, raising=False)
    return client, said


def test_a_pull_from_the_setup_dialog_reports_on_the_gui_thread(_pull_lane, monkeypatch, qapp):
    client, said = _pull_lane
    response = _PullResponse([{"status": "pulling", "total": 10, "completed": 5}, {"status": "success"}])
    monkeypatch.setattr(client.urllib.request, "urlopen", lambda req, timeout=None: response)
    from eli.gui.widgets.ollama_model_selector import OllamaSetupDialog
    dlg = OllamaSetupDialog(current_host="http://127.0.0.1:9")
    dlg._run_pull("gamma:2b")
    assert _drive(qapp, lambda: bool(said))
    assert said == [("information", True, "✅ gamma:2b is ready.")]
    assert dlg.selected_model() == "gamma:2b"


def test_cancel_stops_a_pull_and_says_nothing_more(_pull_lane, monkeypatch, qapp):
    client, said = _pull_lane
    response = _PullResponse([], endless=True)
    monkeypatch.setattr(client.urllib.request, "urlopen", lambda req, timeout=None: response)
    from eli.gui.widgets.ollama_model_selector import OllamaSetupDialog
    dlg = OllamaSetupDialog(current_host="http://127.0.0.1:9")
    dlg._run_pull("gamma:2b")
    assert _drive(qapp, lambda: response.read > 3), "the pull is running"
    from PySide6.QtWidgets import QPushButton
    dlg._pull_dialog.findChild(QPushButton).click()  # the progress dialog's own Cancel button
    assert _drive(qapp, lambda: response.closed.is_set() and dlg._pull_dialog is None)
    assert said == [], "a cancelled pull is neither a success nor a failure"
    assert dlg.selected_model() != "gamma:2b"

"""
Ollama model selector widget for ELI GUI.

Usage — add to the main window's toolbar (eli/gui/main_window):

    from eli.gui.widgets.ollama_model_selector import OllamaModelSelector

    self.ollama_selector = OllamaModelSelector(self)
    toolbar.addWidget(self.ollama_selector)

The widget:
  - Shows a dropdown of installed Ollama models
  - Host + popular-model setup dialog (gear button)
  - Refresh / pull / open-models-folder controls
  - Persists selection to ELI config (``ollama_model``, ``ollama_host``)
  - Status indicator (green = running, red = not)
  - All network queries run in background threads (never blocks GUI)
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional

from eli.gui.qt_compat import QObject, Qt, QTimer, pyqtSignal
from eli.gui.qt_compat import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


log = logging.getLogger(__name__)

_BTN_STYLE = (
    "QPushButton { background: #313244; color: #cdd6f4; border: 1px solid #444; "
    "border-radius: 3px; font-size: 13px; padding: 0 4px; } "
    "QPushButton:hover { background: #45475a; }"
)


class _StatusDot(QLabel):
    """Tiny colored circle indicating Ollama running state."""

    def __init__(self, parent=None):
        super().__init__("●", parent)
        self.setFixedWidth(16)
        self.set_state(None)

    def set_state(self, running: Optional[bool]):
        if running is True:
            self.setStyleSheet("color: #44ff88; font-size: 10px;")
            self.setToolTip("Ollama running")
        elif running is False:
            self.setStyleSheet("color: #ff4444; font-size: 10px;")
            self.setToolTip("Ollama not running — click ⚙ to configure or start: ollama serve")
        else:
            self.setStyleSheet("color: #888888; font-size: 10px;")
            self.setToolTip("Checking Ollama...")


class _PullCancelled(Exception):
    """Cancel was pressed in the pull's progress dialog."""


class _PullsModels:
    """One pull flow for the setup dialog and the toolbar selector. The pull runs on the client's
    thread and reports through the process-wide relay (_WorkerRelay), so every widget is touched
    on the GUI thread only; Cancel stops the download. The dialog drove its progress dialog and
    message boxes from the pull thread itself, and neither one's Cancel did anything."""

    def _connect_pull_relay(self) -> None:
        relay = _worker_relay()
        relay.pull_progress.connect(self._relayed_pull_progress)
        relay.pull_done.connect(self._relayed_pull_done)
        self._pull_dialog = None
        self._pull_model_name = ""
        self._pull_cancel = threading.Event()

    def _start_pull(self, model: str) -> None:
        progress = QProgressDialog(f"Pulling {model}…", "Cancel", 0, 100, self)
        progress.setWindowTitle("Pulling Model")
        progress.setWindowModality(Qt.WindowModal)
        progress.setValue(0)
        cancel = threading.Event()
        progress.canceled.connect(cancel.set)
        self._pull_dialog, self._pull_model_name, self._pull_cancel = progress, model, cancel
        progress.show()
        relay, key = _worker_relay(), id(self)

        def _progress(status, pct):
            if cancel.is_set():
                raise _PullCancelled()      # the client closes the download and reports it ended
            relay.pull_progress.emit(key, str(status or ""), int(pct or 0))

        def _done(result):
            relay.pull_done.emit(key, dict(result or {}, cancelled=cancel.is_set()))

        try:
            from eli.integrations.ollama.client import pull_model_async
            pull_model_async(model, progress_cb=_progress, done_cb=_done)
        except Exception as e:
            progress.close()
            self._pull_dialog = None
            QMessageBox.critical(self, "Error", f"Pull failed: {e}")

    def _relayed_pull_progress(self, key: int, status: str, pct: int):
        if key == id(self):
            self._on_pull_progress(status, pct)

    def _relayed_pull_done(self, key: int, result: dict):
        if key == id(self):
            self._on_pull_done(result)

    def _on_pull_progress(self, status: str, pct: int):
        dlg = getattr(self, "_pull_dialog", None)
        if dlg is None or self._pull_cancel.is_set():
            return
        dlg.setValue(int(pct))
        if status:
            dlg.setLabelText(f"{status}\n{pct}%")

    def _on_pull_done(self, result: dict):
        dlg = getattr(self, "_pull_dialog", None)
        model = getattr(self, "_pull_model_name", "") or "model"
        if dlg is not None:
            dlg.close()
        self._pull_dialog = None
        if result.get("cancelled"):
            return                          # the user stopped it; nothing more to say
        if result.get("ok"):
            QMessageBox.information(self, "Pull complete", f"✅ {model} is ready.")
            self._pulled(model)
        else:
            QMessageBox.warning(
                self, "Pull failed",
                f"❌ {model}:\n{result.get('error', 'Unknown error')}",
            )

    def _pulled(self, model: str) -> None:
        """What the widget does with a model that has just been pulled."""


class OllamaSetupDialog(_PullsModels, QDialog):
    """User-friendly Ollama configuration: host, model pick, popular pulls, folder."""

    def __init__(self, parent=None, *, current_host: str = "", models: Optional[List[str]] = None):
        super().__init__(parent)
        self.setWindowTitle("Ollama Setup")
        self.setMinimumWidth(420)
        self._models = list(models or [])
        self._build_ui(current_host or "http://localhost:11434")
        self._connect_pull_relay()

    def _build_ui(self, host: str):
        layout = QVBoxLayout(self)

        hint = QLabel(
            "Connect ELI to your local or remote Ollama server. "
            "Install Ollama from ollama.com, then pull a model below or use ones you already have."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        layout.addWidget(hint)

        form = QFormLayout()
        self._host = QLineEdit(host)
        self._host.setPlaceholderText("http://localhost:11434  or  192.168.1.5:11434")
        self._host.setToolTip(
            "Ollama API base URL. Scheme optional — localhost:11434 works.\n"
            "Matches OLLAMA_HOST / your Ollama install."
        )
        form.addRow("Host", self._host)
        layout.addLayout(form)

        model_row = QHBoxLayout()
        self._model_combo = QComboBox()
        self._model_combo.setMinimumWidth(220)
        if self._models:
            self._model_combo.addItems(self._models)
        else:
            self._model_combo.addItem("(no models — pull one below)")
        model_row.addWidget(self._model_combo, 1)
        layout.addWidget(QLabel("Active model"))
        layout.addLayout(model_row)

        pop_lbl = QLabel("Popular models (one-click pull)")
        pop_lbl.setStyleSheet("color: #aaaaaa; font-size: 11px; margin-top: 6px;")
        layout.addWidget(pop_lbl)
        pop_row = QHBoxLayout()
        self._popular = QComboBox()
        self._popular.setMinimumWidth(180)
        try:
            from eli.integrations.ollama.client import POPULAR_OLLAMA_MODELS
            self._popular.addItem("— choose —", "")
            for tag in POPULAR_OLLAMA_MODELS:
                self._popular.addItem(tag, tag)
        except Exception:
            self._popular.addItem("llama3.2:3b", "llama3.2:3b")
        pop_row.addWidget(self._popular, 1)
        btn_pull_pop = QPushButton("Pull")
        btn_pull_pop.setStyleSheet(_BTN_STYLE)
        btn_pull_pop.clicked.connect(self._pull_popular)
        pop_row.addWidget(btn_pull_pop)
        layout.addLayout(pop_row)

        btn_row = QHBoxLayout()
        btn_folder = QPushButton("Open models folder")
        btn_folder.setStyleSheet(_BTN_STYLE)
        btn_folder.setToolTip("Open ~/.ollama/models (or OLLAMA_MODELS) in your file manager")
        btn_folder.clicked.connect(self._open_folder)
        btn_row.addWidget(btn_folder)
        btn_custom = QPushButton("Pull custom…")
        btn_custom.setStyleSheet(_BTN_STYLE)
        btn_custom.clicked.connect(self._pull_custom)
        btn_row.addWidget(btn_custom)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def selected_host(self) -> str:
        return self._host.text().strip() or "http://localhost:11434"

    def selected_model(self) -> str:
        text = self._model_combo.currentText().strip()
        if not text or text.startswith("("):
            return ""
        return text

    def _open_folder(self):
        try:
            from eli.integrations.ollama.client import open_models_folder, ollama_models_dir
            if not open_models_folder():
                QMessageBox.information(
                    self, "Models folder",
                    f"Folder:\n{ollama_models_dir()}\n\n"
                    "Could not open automatically — copy the path above.",
                )
        except Exception as e:
            QMessageBox.warning(self, "Error", str(e))

    def _pull_popular(self):
        tag = str(self._popular.currentData() or "").strip()
        if not tag:
            QMessageBox.information(self, "Pull model", "Choose a popular model first.")
            return
        self._run_pull(tag)

    def _pull_custom(self):
        tag, ok = QInputDialog.getText(
            self, "Pull Ollama Model",
            "Model tag (e.g. llama3.2, mistral, gemma3:12b):",
        )
        if ok and tag.strip():
            self._run_pull(tag.strip())

    def _run_pull(self, model: str):
        try:
            from eli.integrations.ollama.client import set_active_host
            set_active_host(self.selected_host())
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))
            return
        self._start_pull(model)

    def _pulled(self, model: str) -> None:
        if self._model_combo.findText(model) < 0:
            self._model_combo.addItem(model)
        idx = self._model_combo.findText(model)
        if idx >= 0:
            self._model_combo.setCurrentIndex(idx)


class _WorkerRelay(QObject):
    """Where worker threads report. A worker that held the selector itself could end up with its
    last reference when the selector was dropped, and the widget (and its timer) would then be
    destroyed on that thread, which corrupts Qt. The relay lives for the process, on the GUI
    thread; each selector takes only the results tagged with its own id."""
    # the key is id(selector): object, because a Qt int is 32 bits and an id is not
    models_ready = pyqtSignal(object, bool, list, object)
    pull_progress = pyqtSignal(object, str, int)
    pull_done = pyqtSignal(object, dict)


_relay: Optional[_WorkerRelay] = None


def _worker_relay() -> _WorkerRelay:
    # first called from a selector's constructor, so it is created on the GUI thread
    global _relay
    if _relay is None:
        _relay = _WorkerRelay()
    return _relay


def _start_ollama_for_choice() -> None:
    """The user chose Ollama here: start its local server if installed and stopped, off the GUI
    thread. Listing models never starts it (see eli.integrations.ollama.client.ensure_server_running)."""
    import threading

    def _run():
        try:
            from eli.integrations.ollama.client import ensure_server_running
            ensure_server_running(user_choice=True)
        except Exception:
            log.debug("[OLLAMA] start for the user's choice failed", exc_info=True)
    threading.Thread(target=_run, name="ollama-start", daemon=True).start()


def _fetch_models(key: int) -> None:
    try:
        from eli.integrations.ollama.client import is_running, list_models, get_active_model
        running = is_running()
        models = list_models() if running else []
        active = get_active_model()
    except Exception:
        running = False
        models = []
        active = None
    _worker_relay().models_ready.emit(key, bool(running), list(models or []), active)


class OllamaModelSelector(_PullsModels, QWidget):
    """
    Compact toolbar widget: [● ▼ model ] [↻] [⬇] [⚙]
    Emits model_changed(str) when selection changes.
    """

    model_changed = pyqtSignal(str)


    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()
        relay = _worker_relay()
        relay.models_ready.connect(self._relayed_models)
        self._connect_pull_relay()
        self._last_models: List[str] = []
        self._refresh_async()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh_async)
        self._timer.start(30_000)

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 0, 2, 0)
        layout.setSpacing(2)

        self._dot = _StatusDot(self)
        layout.addWidget(self._dot)

        lbl = QLabel("Ollama:")
        lbl.setStyleSheet("color: #aaaaaa; font-size: 11px;")
        layout.addWidget(lbl)

        self._combo = QComboBox(self)
        self._combo.setMinimumWidth(160)
        self._combo.setToolTip("Select Ollama model — click ⚙ for host and pull options")
        self._combo.setStyleSheet("""
            QComboBox {
                background: #1e1e2e; color: #cdd6f4; border: 1px solid #444;
                border-radius: 3px; padding: 2px 6px; font-size: 11px;
            }
            QComboBox::drop-down { border: none; }
            QComboBox QAbstractItemView {
                background: #1e1e2e; color: #cdd6f4; selection-background-color: #313244;
            }
        """)
        self._combo.currentTextChanged.connect(self._on_selection_changed)
        layout.addWidget(self._combo)

        btn_refresh = QPushButton("↻")
        btn_refresh.setFixedWidth(24)
        btn_refresh.setToolTip("Refresh model list")
        btn_refresh.setStyleSheet(_BTN_STYLE)
        btn_refresh.clicked.connect(self._refresh_async)
        layout.addWidget(btn_refresh)

        btn_pull = QPushButton("⬇")
        btn_pull.setFixedWidth(24)
        btn_pull.setToolTip("Quick pull (or use ⚙ for popular models)")
        btn_pull.setStyleSheet(_BTN_STYLE)
        btn_pull.clicked.connect(self._on_pull_clicked)
        layout.addWidget(btn_pull)

        btn_setup = QPushButton("⚙")
        btn_setup.setFixedWidth(24)
        btn_setup.setToolTip("Ollama setup: host, popular models, models folder")
        btn_setup.setStyleSheet(_BTN_STYLE)
        btn_setup.clicked.connect(self._open_setup)
        layout.addWidget(btn_setup)

        self.setLayout(layout)

    def _refresh_async(self):
        self._combo.setEnabled(False)
        threading.Thread(target=_fetch_models, args=(id(self),), daemon=True).start()

    def _relayed_models(self, key: int, running: bool, models: list, active: object):
        if key == id(self):
            self._update_ui(running, models, active)

    def _update_ui(self, running: bool, models: List[str], active: Optional[str]):
        self._dot.set_state(running)
        self._combo.setEnabled(True)
        self._last_models = list(models or [])
        current = self._combo.currentText()

        self._combo.blockSignals(True)
        self._combo.clear()

        if not running:
            self._combo.addItem("Ollama not running")
            self._combo.blockSignals(False)
            return

        if not models:
            self._combo.addItem("No models installed")
            self._combo.blockSignals(False)
            return

        self._combo.addItems(models)
        restore = active or current
        idx = self._combo.findText(restore)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)
        self._combo.blockSignals(False)
        pulled = getattr(self, "_select_when_listed", "")
        if pulled and pulled in models:
            self._select_when_listed = ""
            self._select_model(pulled)

    def _open_setup(self):
        try:
            from eli.integrations.ollama.client import get_active_host, set_active_host, set_active_model
            host = get_active_host()
            _start_ollama_for_choice()   # opening Ollama setup is the user choosing Ollama
        except Exception:
            host = "http://localhost:11434"

        dlg = OllamaSetupDialog(self, current_host=host, models=self._last_models)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            from eli.integrations.ollama.client import set_active_host, set_active_model
            set_active_host(dlg.selected_host())
            model = dlg.selected_model()
            if model:
                set_active_model(model)
                self.model_changed.emit(model)
        except Exception:
            log.debug("[OLLAMA] setup persist failed", exc_info=True)
        self._refresh_async()

    def _on_selection_changed(self, model: str):
        if not model or "not running" in model or "No models" in model:
            return
        try:
            from eli.integrations.ollama.client import set_active_model
            set_active_model(model)
            _start_ollama_for_choice()   # the user picked an Ollama model
        except Exception:
            log.debug("[OLLAMA] persisting active model failed", exc_info=True)
        self.model_changed.emit(model)

    def _on_pull_clicked(self):
        model, ok = QInputDialog.getText(
            self, "Pull Ollama Model",
            "Enter model name to pull (e.g. llama3.2, mistral, gemma3:12b):",
        )
        if not ok or not model.strip():
            return
        self._start_pull(model.strip())

    def _pulled(self, model: str) -> None:
        # chosen once the refreshed list has it (it was chosen two seconds later, listed or not)
        self._select_when_listed = model
        self._refresh_async()

    def _select_model(self, model: str):
        idx = self._combo.findText(model)
        if idx >= 0:
            self._combo.setCurrentIndex(idx)

    def current_model(self) -> Optional[str]:
        text = self._combo.currentText()
        if not text or "not running" in text or "No models" in text:
            return None
        return text

    def refresh(self):
        self._refresh_async()

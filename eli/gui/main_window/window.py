"""EliMainWindow (main application window) — extracted from gui_eli_original.py (v3 modularisation)."""
from __future__ import annotations
from eli.gui.main_window._common import *  # noqa: F401,F403

from eli.gui.main_window.adapters import CentralMemoryAdapter, ExecutorBridge
from eli.gui.main_window.models import LocalModelManager, OllamaModelManager
from eli.gui.main_window.widgets import _CapabilityList, _GlobalScrollZoom, _MiniTelemetryGraph, _QABoard, _ZoomableImagePreview, _ZoomableSettingsView  # noqa: F401


if CENTRAL_IMPORTS_AVAILABLE and get_memory:
    _central_mem = get_memory()
    memory_system = CentralMemoryAdapter(_central_mem)
else:
    # Fallback local MemorySystem
    class MemorySystem:
        def __init__(self, db_path: Path = MEMORY_DB):
            self.db_path = db_path
            self._init_db()
        def _init_db(self):
            import sqlite3
            conn = sqlite3.connect(str(self.db_path))
            from eli.core.sqlite_util import apply_pragmas
            apply_pragmas(conn, db_path=str(self.db_path), synchronous="NORMAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'note',
                    text TEXT NOT NULL,
                    tags TEXT DEFAULT '',
                    source TEXT DEFAULT 'user',
                    confidence REAL DEFAULT 0.8
                );
                CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp);
                CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(kind);
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}'
                );
                CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
            """)
            conn.commit()
            conn.close()
        def store(self, text: str, tags: List[str] = None, kind: str = "note",
                  source: str = "user", confidence: float = 0.8) -> bool:
            import sqlite3
            try:
                conn = sqlite3.connect(str(self.db_path))
                cursor = conn.cursor()
                tags_str = ",".join(tags) if tags else ""
                timestamp = now_timestamp()
                cursor.execute("""
                    INSERT INTO memories (timestamp, kind, text, tags, source, confidence)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (timestamp, kind, text, tags_str, source, confidence))
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                print(f"❌ Memory store error: {e}")
                return False
        def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
            import sqlite3
            try:
                conn = sqlite3.connect(str(self.db_path))
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM memories
                    WHERE text LIKE ? OR tags LIKE ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (f"%{query}%", f"%{query}%", limit))
                results = [dict(row) for row in cursor.fetchall()]
                conn.close()
                return results
            except Exception as e:
                print(f"❌ Memory search error: {e}")
                return []
        def get_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
            import sqlite3
            try:
                conn = sqlite3.connect(str(self.db_path))
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT * FROM memories
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (limit,))
                results = [dict(row) for row in cursor.fetchall()]
                conn.close()
                return results
            except Exception as e:
                print(f"❌ Memory get recent error: {e}")
                return []
        def get_stats(self) -> Dict[str, Any]:
            import sqlite3
            try:
                conn = sqlite3.connect(str(self.db_path))
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM memories")
                _row = cursor.fetchone(); total = _row[0] if _row else 0
                cursor.execute("SELECT kind, COUNT(*) FROM memories GROUP BY kind")
                by_kind = dict(cursor.fetchall())
                conn.close()
                return {"total": total, "by_kind": by_kind}
            except Exception as e:
                print(f"❌ Memory stats error: {e}")
                return {"total": 0, "by_kind": {}}
        def log_event(self, event_type: str, description: str, metadata: Dict = None):
            import sqlite3, json
            try:
                conn = sqlite3.connect(str(self.db_path))
                cursor = conn.cursor()
                timestamp = now_timestamp()
                meta_str = json.dumps(metadata or {})
                cursor.execute("""
                    INSERT INTO events (timestamp, event_type, description, metadata)
                    VALUES (?, ?, ?, ?)
                """, (timestamp, event_type, description, meta_str))
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"❌ Event log error: {e}")
        # Minimal habit methods for fallback
        def get_habit_rules(self, enabled_only: bool = True) -> List[Dict[str, Any]]:
            return []
        def add_habit_rule(self, name: str, command: str, hour: int, minute: int,
                           days: list = None, enabled: bool = True) -> int:
            return -1
        def update_habit_rule(self, rule_id: int, **fields) -> bool:
            return False
        def delete_habit_rule(self, rule_id: int):
            pass
        def toggle_habit_rule(self, rule_id: int, enabled: bool):
            pass
    memory_system = MemorySystem()


model_manager = LocalModelManager()


executor_bridge = ExecutorBridge(memory_system)


from eli.gui.main_window._mixins.chrome_io import ChromeIoMixin
from eli.gui.main_window._mixins.conversation import ConversationMixin
from eli.gui.main_window._mixins.features import FeaturesMixin
from eli.gui.main_window._mixins.image import ImageMixin
from eli.gui.main_window._mixins.quick_actions import QuickActionsMixin
from eli.gui.main_window._mixins.screen_capture import ScreenCaptureMixin
from eli.gui.main_window._mixins.self_improve import SelfImproveMixin
from eli.gui.main_window._mixins.settings_model import SettingsModelMixin
from eli.gui.main_window._mixins.tabs import TabsMixin


class EliMainWindow(QMainWindow, ChromeIoMixin, ConversationMixin, FeaturesMixin, ImageMixin, QuickActionsMixin, ScreenCaptureMixin, SelfImproveMixin, SettingsModelMixin, TabsMixin):
    stt_transcript = pyqtSignal(str)

    _inference_lock = __import__('threading').Lock()  # llama_cpp is NOT thread-safe

    log_signal = pyqtSignal(str)

    status_signal = pyqtSignal(str)

    chat_response_signal = pyqtSignal(str)

    self_improve_failures_signal = pyqtSignal(str)

    self_improve_improvements_signal = pyqtSignal(str)

    proactive_suggestions_signal = pyqtSignal(str)

    proactive_summary_signal = pyqtSignal(str)

    proactive_insights_signal = pyqtSignal(str)

    image_generation_done_signal = pyqtSignal(object, dict)

    image_generation_failed_signal = pyqtSignal(str)

    chat_image_generation_done_signal = pyqtSignal(object, dict, str)

    chat_image_generation_failed_signal = pyqtSignal(str)

    wizard_say_signal = pyqtSignal(str)

    _mem_refresh_sig = pyqtSignal()

    _sc_capture_sig  = pyqtSignal(str)

    _qa_result_sig   = pyqtSignal(str)

    _image_chat_sig  = pyqtSignal(str)

    _generated_artifact_open_sig = pyqtSignal(object)

    _conf_meta_update_sig = pyqtSignal()

    # Screen control: OCR / "ask ELI" results (worker → main thread) — was
    # QTimer.singleShot(0, ...) called FROM the worker thread, which has no
    # guaranteed event loop and is not the safe cross-thread marshalling
    # pattern already used a few lines above for capture (_sc_capture_sig).
    _sc_ocr_done_sig = pyqtSignal(str)
    _sc_eli_done_sig = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        app = QApplication.instance()
        if app:
            _f = app.font()
            # Auto-fit the base UI scale to the display so it isn't oversized on small /
            # laptop / high-DPI screens (Ctrl+scroll still fine-tunes from here). Shorter
            # screens get a smaller base point size.
            try:
                _sh = QApplication.primaryScreen().availableGeometry().height()
            except Exception:
                _sh = 1080
            _base = 8 if _sh < 800 else (9 if _sh < 1200 else (10 if _sh < 1500 else 11))
            _f.setPointSize(_base)
            app.setFont(_f)
            self._global_zoom_filter = _GlobalScrollZoom(self)
            app.installEventFilter(self._global_zoom_filter)
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        screen = QApplication.primaryScreen().availableGeometry()
        screen = QApplication.primaryScreen().availableGeometry()
        self.setMinimumSize(400, 300)
        self.setGeometry(screen.x() + 20, screen.y() + 20, screen.width() - 40, screen.height() - 40)
        self.is_generating = False
        self._cancel_stream_requested = False
        self._model_loading = False
        self.conversation_history = []
        self.current_theme = "dark"
        self._user_text_color = "#a3be8c"  # user message colour (changeable via picker)
        # Agent wizard state
        self._agent_wizard_state: Optional[dict] = None
        self.ollama_manager = OllamaModelManager()
        self.model_manager = model_manager
        self.memory_system = memory_system
        self.active_backend = model_manager
        self.detected_system_info: Dict[str, Any] = {}
        self._startup_model_prompt_done = False
        self._show_startup_model_picker = True
        self._hardware_tuning_dock = None
        self._runtime_stat_history = {
            "cpu": deque(maxlen=60),
            "ram": deque(maxlen=60),
            "vram": deque(maxlen=60),
        }
        self._runtime_stats_timer = None
        # Phase 7: live-data sync timers — memory counts every 30 s,
        # proactive daemon status every 5 s. Without these the panels
        # only updated via manual Refresh buttons.
        self._memory_stats_timer = None
        self._proactive_status_timer = None
        self._central_memory = None
        if CENTRAL_IMPORTS_AVAILABLE and get_memory:
            self._central_memory = get_memory()

        # Streaming VoiceWorker — interruptible TTS with thread-safe interrupt()
        try:
            from eli.perception.voice_worker_streaming import VoiceWorker as _StreamingVW
            self._voice_worker = _StreamingVW()
        except Exception:
            self._voice_worker = None

        self.log_signal.connect(self._append_log)
        self.status_signal.connect(self._update_status, Qt.ConnectionType.QueuedConnection)
        self.chat_response_signal.connect(self._append_chat_response, Qt.ConnectionType.QueuedConnection)
        self.stt_transcript.connect(self._on_stt_transcript)
        self.self_improve_failures_signal.connect(self._update_failures_display, Qt.ConnectionType.QueuedConnection)
        self.self_improve_improvements_signal.connect(self._update_improvements_display, Qt.ConnectionType.QueuedConnection)

        # Connect new proactive signals
        self.proactive_suggestions_signal.connect(self._update_suggestions_display, Qt.ConnectionType.QueuedConnection)
        self.proactive_summary_signal.connect(self._update_summary_display, Qt.ConnectionType.QueuedConnection)
        self.proactive_insights_signal.connect(self._update_insights_display, Qt.ConnectionType.QueuedConnection)
        self.image_generation_done_signal.connect(self._image_generation_done, Qt.ConnectionType.QueuedConnection)
        self.image_generation_failed_signal.connect(self._image_generation_failed, Qt.ConnectionType.QueuedConnection)
        self.chat_image_generation_done_signal.connect(self._chat_image_generation_done, Qt.ConnectionType.QueuedConnection)
        self.chat_image_generation_failed_signal.connect(self._chat_image_generation_failed, Qt.ConnectionType.QueuedConnection)
        self.wizard_say_signal.connect(self._wizard_display_message, Qt.ConnectionType.QueuedConnection)
        self._mem_refresh_sig.connect(self.refresh_memory_stats, Qt.ConnectionType.QueuedConnection)
        self._conf_meta_update_sig.connect(self._update_confidence_meta_label, Qt.ConnectionType.QueuedConnection)
        self._image_chat_sig.connect(self._on_image_chat_response, Qt.ConnectionType.QueuedConnection)
        self._generated_artifact_open_sig.connect(
            self._open_generated_artifact_from_result,
            Qt.ConnectionType.QueuedConnection,
        )
        self._sc_ocr_done_sig.connect(self._sc_ocr_done, Qt.ConnectionType.QueuedConnection)
        self._sc_eli_done_sig.connect(self._sc_eli_done, Qt.ConnectionType.QueuedConnection)

        ensure_dirs()
        self.init_ui()
        self._ensure_hardware_tuning_dock()
        self._start_runtime_monitoring()
        self._start_live_data_monitoring()
        self.load_settings()
        self.refresh_model_sources()
        self.apply_theme()
        # A model the terminal launcher loaded (eli/gui/app.py, through gguf_inference.load_model, which
        # wrote its runtime report) is taken on as it is: nothing is loaded twice or republished.
        try:
            from eli.cognition import gguf_inference as _ggi_pre
            if getattr(_ggi_pre, "_llm", None) is not None:
                model_manager.adopt(_ggi_pre._llm)
                self.active_backend = model_manager
                self.status_signal.emit(model_manager.status_line())
        except Exception as _pre_err:
            log.debug(f"[GUI] preloaded model not taken on: {_pre_err}")

        QTimer.singleShot(600, self.maybe_run_first_time_setup)

        # ---------- START PROACTIVE DAEMON ----------
        self._proactive_daemon = None
        self._proactive_dock = None
        self._operator_console_dock = None
        self._proactive_event_count = 0
        if start_daemon:
            try:
                from eli.planning.proactive_daemon import start_daemon as _start_pd
                self._proactive_daemon = _start_pd()
                log.debug(f"[GUI] Proactive daemon started — USER DB: {self._proactive_daemon.user_mem.db_path}")
                log.debug(f"[GUI] Proactive daemon started — AGENT DB: {self._proactive_daemon.agent_mem.db_path}")
                # Attach ProactiveDock so proactive output has a dedicated panel
                try:
                    from eli.gui.docks.proactive_dock import ProactiveDock
                    self._proactive_dock = ProactiveDock(self)
                    self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._proactive_dock)
                    self._proactive_dock.hide()  # hidden by default — user opens it explicitly
                    log.debug("[GUI] ProactiveDock attached (hidden until opened)")
                except Exception as _dock_err:
                    log.debug(f"[GUI] ProactiveDock unavailable (non-fatal): {_dock_err}")
                # Attach OperatorConsoleDock — hidden by default, toggled via View menu
                try:
                    from eli.gui.docks.operator_console_dock import OperatorConsoleDock
                    self._operator_console_dock = OperatorConsoleDock(self)
                    self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._operator_console_dock)
                    self._operator_console_dock.hide()
                    log.debug("[GUI] OperatorConsoleDock attached (hidden until opened)")
                except Exception as _ocd_err:
                    log.debug(f"[GUI] OperatorConsoleDock unavailable (non-fatal): {_ocd_err}")
                    self._operator_console_dock = None
                # Consume daemon suggestion_queue → proactive tab (thread-safe)
                threading.Thread(
                    target=self._daemon_queue_consumer, daemon=True,
                    name="eli-daemon-queue").start()
                # Refresh status label now that daemon is set (init_ui ran before daemon started)
                QTimer.singleShot(200, self._update_proactive_status_label)
            except Exception as e:
                log.debug(f"[GUI] Failed to start proactive daemon: {e}")
        else:
            log.debug("[GUI] Proactive daemon not available (import failed)")

        # Ambient vision daemon — self-gates on the `ambient_vision_enabled`
        # toggle (OFF by default), so starting it unconditionally is cheap.
        try:
            from eli.perception.ambient_vision import start_ambient_vision
            start_ambient_vision()
            log.debug("[GUI] Ambient vision daemon started (gated on ambient_vision_enabled)")
        except Exception as _av_err:
            log.debug(f"[GUI] Ambient vision daemon unavailable (non-fatal): {_av_err}")

        # ---------- COGNITIVE ENGINE SINGLETON ----------
        self._cognitive_engine = None
        try:
            from eli.kernel.engine import CognitiveEngine, set_engine
            self._cognitive_engine = CognitiveEngine(
                auto_init_gguf=False,
                enforce_hardware_authority=False,
            )
            # Register it as THE engine. Without this the first background caller of get_engine()
            # built a second one, with auto_init_gguf on: the model again, and a duplicate habit
            # scheduler and self-improvement loop.
            set_engine(self._cognitive_engine)
            log.debug("[GUI] CognitiveEngine singleton ready (reflection/habit/awareness active)")
        except Exception as _ce_init_err:
            log.debug(f"[GUI] CognitiveEngine init skipped (non-fatal): {_ce_init_err}")

    _REASONING_MODE_LABELS = {
        # New public names (Stage 1 rename) → stable internal keys.
        "⚡ Quick": "quick",
        "🧠 Normal": "chain_of_thought",
        "🔬 Advanced": "self_consistency",
        "📚 Research": "tree_of_thoughts",
        "🎓 Expert": "constitutional_ai",
        # legacy labels kept accepted for back-compat (not shown in the combobox)
        "🔗 Chain of Thought": "chain_of_thought",
        "🔄 Self-Consistency": "self_consistency",
        "🌳 Tree of Thoughts": "tree_of_thoughts",
        "⚖️ Constitutional AI": "constitutional_ai",
        "🔗 CoT": "chain_of_thought",
        "🔄 Self-C": "self_consistency",
        "🌳 ToT": "tree_of_thoughts",
        "⚖️ Const AI": "constitutional_ai",
    }

    _AUTO_MODE_PATTERNS = (
        ("constitutional_ai", [
            r"\bconstitutional\b", r"\bcritique[- ]then[- ]revise\b",
            r"\bself[- ]critique\b", r"\bdraft.*then.*revise\b",
            r"\bethic(?:s|al)\b.*\b(check|review)\b",
        ]),
        ("tree_of_thoughts", [
            r"\btree[- ]of[- ]thoughts?\b", r"\b(?:explore|enumerate)\s+(?:multiple\s+)?branches?\b",
            r"\bbranch(?:es)?\s+and\s+(?:prune|evaluate)\b",
            r"\bcompare\s+(?:multiple|several)\s+(?:options|approaches|paths)\b",
        ]),
        ("self_consistency", [
            r"\bself[- ]consistenc(?:y|t)\b", r"\bmajority\s+vote\b",
            r"\b(?:three|3|five|5)\s+independent\s+(?:reasoning\s+paths?|attempts?)\b",
            r"\bcross[- ]check\s+(?:multiple|several)\s+approaches\b",
        ]),
        ("chain_of_thought", [
            r"\bchain[- ]of[- ]thought\b", r"\bstep[- ]by[- ]step\b",
            r"\bshow\s+(?:your|the)\s+(?:work|reasoning)\b",
            r"\bthink\s+(?:through|carefully)\b", r"\bderive\b",
            r"\bprove\b", r"\bproof\b",
            r"\bwalk\s+me\s+through\b",
        ]),
    )

    def create_chat_tab(self):
        chat_widget = QWidget()
        layout = QVBoxLayout(chat_widget)
        header = QLabel(APP_NAME)
        header.setStyleSheet(
            "font-size: 18px; font-weight: 600; letter-spacing: 0.5px; "
            "padding: 10px 6px 6px 6px;"
        )
        layout.addWidget(header)
        info_frame = QFrame()
        info_frame.setFrameStyle(QFrame.Shape.NoFrame)
        info_layout = QHBoxLayout(info_frame)
        info_layout.setContentsMargins(6, 0, 6, 4)
        self.model_info_label = QLabel("● Model: not loaded")
        self.model_info_label.setStyleSheet("color: #888; font-size: 11px;")
        info_layout.addWidget(self.model_info_label)
        info_layout.addStretch()
        layout.addWidget(info_frame)
        class ZoomableTextEdit(QTextEdit):
            def wheelEvent(self, event):
                if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
                    if event.angleDelta().y() > 0:
                        self.zoomIn(1)
                    else:
                        self.zoomOut(1)
                    event.accept()
                else:
                    super().wheelEvent(event)

        _IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif"}
        _TEXT_EXTS  = {".txt", ".md", ".py", ".js", ".ts", ".json", ".yaml", ".yml",
                       ".toml", ".csv", ".log", ".sh", ".html", ".css", ".xml", ".rst"}

        class DropAwareTextEdit(QTextEdit):
            """Chat input box that accepts dropped files and images."""
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                self.setAcceptDrops(True)
                self._dropped_paths: list = []

            def dragEnterEvent(self, event):
                if event.mimeData().hasUrls():
                    event.acceptProposedAction()
                else:
                    super().dragEnterEvent(event)

            def dragMoveEvent(self, event):
                if event.mimeData().hasUrls():
                    event.acceptProposedAction()
                else:
                    super().dragMoveEvent(event)

            def dropEvent(self, event):
                if not event.mimeData().hasUrls():
                    super().dropEvent(event)
                    return
                event.acceptProposedAction()
                for url in event.mimeData().urls():
                    path = url.toLocalFile()
                    if not path:
                        continue
                    from pathlib import Path as _P
                    p = _P(path)
                    if not p.exists():
                        continue
                    if p.is_dir():
                        # Folders can't be inlined as content — drop the BARE path
                        # so ELI's router acts on the actual location (list/analyse
                        # the directory) instead of getting a "[File: …]" wrapper it
                        # then tries (and fails) to read as a file.
                        tag = path
                    else:
                        suffix = p.suffix.lower()
                        if suffix in _IMAGE_EXTS:
                            tag = f"[Image: {path}]"
                            self._dropped_paths.append(("image", path))
                        elif suffix in _TEXT_EXTS:
                            tag = f"[File: {path}]"
                            self._dropped_paths.append(("text", path))
                        elif suffix == ".pdf":
                            tag = f"[PDF: {path}]"
                            self._dropped_paths.append(("pdf", path))
                        else:
                            tag = f"[File: {path}]"
                            self._dropped_paths.append(("file", path))
                    cur = self.toPlainText().rstrip()
                    self.setPlainText((cur + "\n" + tag).strip())

        self.chat_display = ZoomableTextEdit()
        self.chat_display.setReadOnly(True)
        self.chat_display.setPlaceholderText("Conversation will appear here...")
        layout.addWidget(self.chat_display, stretch=7)
        input_group = QGroupBox("Your Message")
        input_layout = QVBoxLayout(input_group)
        self.chat_input = DropAwareTextEdit()
        self.chat_input.setPlaceholderText(
            "Type your message here… or drag & drop files/images  (Enter to send, Shift+Enter for newline)"
        )
        self.chat_input.setMaximumHeight(120)
        self.chat_input.installEventFilter(self)
        input_layout.addWidget(self.chat_input)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(4)
        # Compact bottom-row stylesheet shared by all chat controls.
        _BTN_BASE = (
            "QPushButton { color:white; font-weight:600; border-radius:4px;"
            " padding:3px 8px; min-height:24px; max-height:26px; font-size:11px; }"
        )
        _COMBO_BASE = (
            "QComboBox { background:#1e2535; color:#88c0d0;"
            " border:1px solid #88c0d0; border-radius:4px;"
            " padding:2px 6px; min-height:24px; max-height:26px; font-size:11px; }"
            "QComboBox QAbstractItemView { background:#1e2535; color:#ccc;"
            " selection-background-color:#3e3e3e; }"
        )

        self.send_btn = QPushButton("Send")
        self._send_btn_style = (
            _BTN_BASE +
            "QPushButton { background-color:#4CAF50; }"
            "QPushButton:hover { background-color:#45a049; }"
            "QPushButton:disabled { background-color:#5b6470; color:#cfd6e2; }"
        )
        self._stop_btn_style = (
            _BTN_BASE +
            "QPushButton { background-color:#c62828; }"
            "QPushButton:hover { background-color:#e53935; }"
        )
        self.send_btn.setStyleSheet(self._send_btn_style)
        self.send_btn.setToolTip("Send message (Enter). While generating, becomes Stop.")
        self.send_btn.clicked.connect(self._on_send_or_stop)
        btn_layout.addWidget(self.send_btn)

        clear_btn = QPushButton("Clear")
        clear_btn.setStyleSheet(
            _BTN_BASE + "QPushButton { background-color:#5a6373; }"
            "QPushButton:hover { background-color:#6a7484; }"
        )
        clear_btn.clicked.connect(self.clear_chat)
        btn_layout.addWidget(clear_btn)

        self.wake_word_btn = QPushButton("Wake: ON")
        self.wake_word_btn.setCheckable(True)
        self.wake_word_btn.setChecked(True)
        self.wake_word_btn.setToolTip(
            "Toggle wake word requirement.\n"
            "ON = say 'computer' before commands.\n"
            "OFF = all speech is treated as a command (direct listen mode)."
        )
        self.wake_word_btn.setStyleSheet(
            _BTN_BASE
            + "QPushButton:checked { background-color:#4CAF50; }"
            "QPushButton:!checked { background-color:#F44336; }"
            "QPushButton:checked:hover { background-color:#388E3C; }"
            "QPushButton:!checked:hover { background-color:#C62828; }"
        )
        self.wake_word_btn.toggled.connect(self._on_wake_word_toggled)
        btn_layout.addWidget(self.wake_word_btn)

        self.auto_speak_btn = QPushButton("🔇 Auto")
        self.auto_speak_btn.setCheckable(True)
        self.auto_speak_btn.setToolTip("Auto-speak every ELI response")
        self.auto_speak_btn.setStyleSheet(
            _BTN_BASE + "QPushButton { background-color:#607D8B; }"
            "QPushButton:checked { background-color:#2196F3; }"
            "QPushButton:hover { background-color:#455A64; }"
        )
        self.auto_speak_btn.toggled.connect(self._on_auto_speak_toggled)
        btn_layout.addWidget(self.auto_speak_btn)

        # Deep-thinking toggle (any model whose chat template enables reasoning).
        # ON = the answer call may think (higher quality, slower); OFF = no thinking.
        # Utility calls never think regardless; no effect on non-reasoning models.
        try:
            from eli.core.runtime_settings import load_settings as _ls_think
            _think_on = bool(_ls_think().get("model_thinking", True))
        except Exception:
            _think_on = True
        self.thinking_btn = QPushButton("🧠 Think: ON" if _think_on else "🧠 Think: OFF")
        self.thinking_btn.setCheckable(True)
        self.thinking_btn.setChecked(_think_on)
        self.thinking_btn.setToolTip(
            "Deep thinking on answers (only when the loaded model's chat template "
            "enables a reasoning channel).\n"
            "ON = higher quality, slower.  OFF = faster.\n"
            "Routing / summaries never think. No effect on non-reasoning models.")
        self.thinking_btn.setStyleSheet(
            _BTN_BASE + "QPushButton { background-color:#607D8B; }"
            "QPushButton:checked { background-color:#9C27B0; }"
            "QPushButton:hover { background-color:#455A64; }"
        )
        try:
            import os as _os_think
            _os_think.environ["ELI_MODEL_THINK"] = "1" if _think_on else "0"
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        self.thinking_btn.toggled.connect(self._on_thinking_toggled)
        btn_layout.addWidget(self.thinking_btn)

        _net_on = False
        try:
            from eli.core import config as _cfg
            _net_on = bool(_cfg.get("network_enabled", False))
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        self.net_btn = QPushButton("🌐 Net: ON" if _net_on else "🌐 Net: OFF")
        self.net_btn.setCheckable(True)
        self.net_btn.setChecked(_net_on)
        self.net_btn.setToolTip(
            "Toggle internet access for ELI.\n"
            "ON  — news fetch, RSS feeds, and web tools are active; every outbound\n"
            "      connection is logged to the audit trail (host:port).\n"
            "OFF — fully offline; all AI runs locally with no network calls.\n"
            "The flip itself is recorded either way."
        )
        self.net_btn.setStyleSheet(
            _BTN_BASE
            + "QPushButton:checked { background-color:#4CAF50; }"
            "QPushButton:!checked { background-color:#607D8B; }"
            "QPushButton:checked:hover { background-color:#388E3C; }"
            "QPushButton:!checked:hover { background-color:#455A64; }"
        )
        self.net_btn.toggled.connect(self._on_network_toggled)
        btn_layout.addWidget(self.net_btn)

        # ── ELI Full Control — master override that lifts ALL safety barriers ──
        _fc_on = False
        try:
            from eli.core.full_control import is_full_control as _ifc
            _fc_on = bool(_ifc())
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        self.full_control_btn = QPushButton("🔓 Full Control: ON" if _fc_on else "🔒 Full Control: OFF")
        self.full_control_btn.setCheckable(True)
        self.full_control_btn.setChecked(_fc_on)
        self.full_control_btn.setToolTip(
            "ELI Full Control — MASTER OVERRIDE that lifts EVERY safety barrier.\n"
            "ON  — network always allowed; ELI may self-edit code, auto-approve its own\n"
            "      autonomous actions, and run ANY shell command (even normally-blocked\n"
            "      destructive ones). Enable only if you fully trust ELI on this machine.\n"
            "OFF — normal: offline-by-default, gated self-editing, approval-gated autonomy,\n"
            "      fail-closed command safety."
        )
        self.full_control_btn.setStyleSheet(
            _BTN_BASE
            + "QPushButton:checked { background-color:#D32F2F; }"
            "QPushButton:!checked { background-color:#607D8B; }"
            "QPushButton:checked:hover { background-color:#B71C1C; }"
            "QPushButton:!checked:hover { background-color:#455A64; }"
        )
        self.full_control_btn.toggled.connect(self._on_full_control_toggled)
        btn_layout.addWidget(self.full_control_btn)

        # Ambient screen-watch toggle — periodic local vision glances.
        _watch_on = False
        try:
            from eli.core import config as _cfg
            _watch_on = bool(_cfg.get("ambient_vision_enabled", False))
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        self.ambient_vision_btn = QPushButton("👁 Watch: ON" if _watch_on else "👁 Watch: OFF")
        self.ambient_vision_btn.setCheckable(True)
        self.ambient_vision_btn.setChecked(_watch_on)
        self.ambient_vision_btn.setToolTip(
            "Ambient screen watch — ELI periodically looks at your screen with local\n"
            "vision and keeps a rolling sense of what you're doing.\n"
            "ON  — glances every few minutes (only when not mid-reply).\n"
            "OFF — ELI only looks when you ask.\n"
            "Note: each glance briefly swaps in the vision model (~slow on this GPU)."
        )
        self.ambient_vision_btn.setStyleSheet(
            _BTN_BASE
            + "QPushButton:checked { background-color:#4CAF50; }"
            "QPushButton:!checked { background-color:#607D8B; }"
            "QPushButton:checked:hover { background-color:#388E3C; }"
            "QPushButton:!checked:hover { background-color:#455A64; }"
        )
        self.ambient_vision_btn.toggled.connect(self._on_ambient_vision_toggled)
        btn_layout.addWidget(self.ambient_vision_btn)

        # Gaze-cursor toggle — webcam eye-tracking. When ON, ELI moves the cursor
        # to where you're looking so voice phrases act there ("left click",
        # "right click", "double click"/"open it", "hit enter").
        _gaze_on = False
        try:
            from eli.perception.gaze_engine import is_gaze_running as _igr
            _gaze_on = bool(_igr())
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        self.gaze_btn = QPushButton("🎯 Gaze: ON" if _gaze_on else "🎯 Gaze: OFF")
        self.gaze_btn.setCheckable(True)
        self.gaze_btn.setChecked(_gaze_on)
        self.gaze_btn.setToolTip(
            "Gaze cursor — webcam eye-tracking.\n"
            "ON  — the cursor follows where you look; say \"left click\",\n"
            "      \"right click\", \"double click\" / \"open it\", \"hit enter\".\n"
            "OFF — no eye tracking.\n"
            "Needs a calibrated profile (say \"calibrate gaze\" for steps)."
        )
        self.gaze_btn.setStyleSheet(
            _BTN_BASE
            + "QPushButton:checked { background-color:#4CAF50; }"
            "QPushButton:!checked { background-color:#607D8B; }"
            "QPushButton:checked:hover { background-color:#388E3C; }"
            "QPushButton:!checked:hover { background-color:#455A64; }"
        )
        self.gaze_btn.toggled.connect(self._on_gaze_toggled)
        btn_layout.addWidget(self.gaze_btn)

        # Voice selector — visible in chat row so the user doesn't dig into Settings.
        self.voice_combo = QComboBox()
        self.voice_combo.setMinimumWidth(120)
        self.voice_combo.setMaximumWidth(150)
        self.voice_combo.setToolTip(
            "Piper accent voices + Style presets (ffmpeg effects on bundled Piper). "
            "Not copyrighted character impersonations — use voice cloning for custom timbres."
        )
        self.voice_combo.setStyleSheet(_COMBO_BASE)
        try:
            from eli.perception.tts_router import (
                list_voices as _lv, get_active_voice as _gav, voice_display_name,
            )
            _voices = _lv() or []
            _active = _gav()
            _idx = 0
            for i, v in enumerate(_voices):
                self.voice_combo.addItem(voice_display_name(v), v)
                if v == _active:
                    _idx = i
            if _voices:
                self.voice_combo.setCurrentIndex(_idx)
        except Exception as _ve:
            self.voice_combo.addItem("(no voices)")
            self.voice_combo.setEnabled(False)
            log.debug(f"[GUI] voice list unavailable: {_ve}")
        self.voice_combo.currentTextChanged.connect(self._on_voice_changed)
        btn_layout.addWidget(self.voice_combo)

        self.stt_btn = QPushButton("🎤 Mic")
        self.stt_btn.setCheckable(True)
        self.stt_btn.setToolTip("Toggle voice input on/off")
        self.stt_btn.setStyleSheet(
            _BTN_BASE + "QPushButton { background-color:#607D8B; }"
            "QPushButton:checked { background-color:#FF5722; }"
            "QPushButton:hover { background-color:#455A64; }"
        )
        self.stt_btn.toggled.connect(self._stt_toggle)
        btn_layout.addWidget(self.stt_btn)

        self.reasoning_mode_combo = QComboBox()
        self.reasoning_mode_combo.addItems(['⚡ Quick','🧠 Normal','🔬 Advanced','📚 Research','🎓 Expert'])
        self.reasoning_mode_combo.setMinimumWidth(110)
        self.reasoning_mode_combo.setMaximumWidth(140)
        self.reasoning_mode_combo.setToolTip('Reasoning mode')
        self.reasoning_mode_combo.setStyleSheet(_COMBO_BASE)
        btn_layout.addWidget(self.reasoning_mode_combo)
        self.reasoning_mode_combo.currentTextChanged.connect(self.change_reasoning_mode)

        # Auto reasoning-mode toggle — when on, prompt keywords switch the
        # combobox automatically before the message is dispatched.
        self.auto_mode_btn = QPushButton("🤖 Auto-Mode")
        self.auto_mode_btn.setCheckable(True)
        self.auto_mode_btn.setChecked(True)
        self.auto_mode_btn.setToolTip(
            "When enabled, the reasoning mode is auto-selected from prompt keywords "
            "(e.g. 'prove', 'step by step', 'explore branches', 'critique then revise')."
        )
        self.auto_mode_btn.setStyleSheet(
            _BTN_BASE + "QPushButton { background-color:#3a4f7a; }"
            "QPushButton:checked { background-color:#5a8de0; color:#0f1424; }"
            "QPushButton:hover { background-color:#4a6394; }"
        )
        self.auto_mode_btn.toggled.connect(self._on_auto_mode_toggled)
        self._auto_reasoning_mode = True
        btn_layout.addWidget(self.auto_mode_btn)

        user_color_btn = QPushButton("🎨")
        user_color_btn.setMaximumWidth(34)
        user_color_btn.setToolTip("Pick colour for your messages")
        user_color_btn.setStyleSheet(
            _BTN_BASE + "QPushButton { background-color:#4a5160; padding:2px 4px; }"
            "QPushButton:hover { background-color:#5a6373; }"
        )
        user_color_btn.clicked.connect(self._pick_user_color)
        btn_layout.addWidget(user_color_btn)
        btn_layout.addStretch()
        input_layout.addLayout(btn_layout)
        layout.addWidget(input_group, stretch=2)
        self.tabs.addTab(chat_widget, "💬 Chat")

    _QA_SAVE_FILE = APP_DIR / "quick_actions.json"

    _QA_ACTION_ARGS = {
        "OPEN_APP":       ("App name (e.g. firefox)", "app"),
        "OPEN_URL":       ("URL to open", "url"),
        "OPEN_IN_IDE":    ("File/folder path", "path"),
        "OPEN_FILE_SYSTEM": ("Directory path (leave blank for home)", "path"),
        "SHELL_EXEC":     ("Shell command to run", "command"),
        "RUN_CMD":        ("Command to run", "command"),
        "SPEAK":          ("Text to speak", "text"),
        "VOLUME":         ("Volume level 0-100", "level"),
        "SET_TIMER":      ("Timer duration (e.g. 5 minutes)", "duration"),
        "SET_ALARM":      ("Alarm time (e.g. 08:30)", "time"),
        "PLAY_MEDIA":     ("Track/artist/playlist name", "query"),
        "GET_WEATHER":    ("City name (leave blank for local)", "city"),
        "MEMORY_RECALL":  ("Search query", "query"),
        "MEMORY_STORE":   ("Text to remember", "text"),
        "WEB_SEARCH":     ("Search query", "query"),
        "READ_FILE":      ("File path to read", "path"),
        "WRITE_NOTE":     ("Note content", "text"),
        "SEARCH_NOTES":   ("Search term", "query"),
        "SUMMARIZE_FILE": ("File path to summarize", "path"),
        "ANALYZE_PDF":    ("PDF file path", "path"),
        "ANALYZE_CSV":    ("CSV file path", "path"),
        "OCR_IMAGE":      ("Image file path", "path"),
        "EXECUTE_GOAL":       ("Goal description", "goal"),
        "SEQUENCE":           ("Commands separated by semicolons", "commands"),
        "SET_CLIPBOARD":      ("Text to copy to clipboard", "text"),
        "KEYBOARD":           ("Key sequence (e.g. ctrl+c)", "keys"),
        "MOUSE_CONTROL":      ("Action: click/move/scroll (e.g. 'click 100 200')", "action"),
        "ADD_EVENT":          ("Event (e.g. 'Meeting 2025-06-01 10:00')", "event"),
        "LIST_DIR":           ("Directory path (leave blank for current)", "path"),
        "CREATE_FOLDER":      ("Folder path to create", "path"),
        "GENERATE_DOCUMENT":  ("Document description or topic", "description"),
        "GENERATE_SCRIPT":    ("Script description", "description"),
        "GENERATE_PROJECT":   ("Project description", "description"),
        "NEW_NOTE":           ("Note title", "title"),
        "PLUGIN_INSTALL":     ("Plugin name or URL", "plugin"),
        "PLUGIN_SEARCH":      ("Search term", "query"),
        "PLUGIN_ENABLE":      ("Plugin name", "plugin"),
        "PLUGIN_DISABLE":     ("Plugin name", "plugin"),
        "PLUGIN_UNINSTALL":   ("Plugin name", "plugin"),
        "CONVERT_DOCUMENT":   ("File path to convert", "path"),
        "FIX_FILE":           ("File path to fix", "path"),
        "SCREEN_READ_ANALYZE": ("Area to capture (leave blank for full screen)", "area"),
        "CLOSE_APP":          ("App name to close", "app"),
        "SHOW_DIFF":          ("Two file paths separated by space", "paths"),
    }

    _QA_CATEGORIES = {
        "Window":  {"OPEN_APP", "CLOSE_APP", "OPEN_URL", "OPEN_BROWSER",
                    "OPEN_IDE", "OPEN_IN_IDE", "OPEN_FILE_SYSTEM",
                    "OPEN_SYSTEM_SETTINGS", "OPEN_AUDIO_SETTINGS",
                    "OPEN_POWER_SETTINGS", "OPEN_COMMUNICATION_HUB",
                    "OPEN_MEDIA_HUB", "OPEN_NETWORK_BROWSER",
                    "TILE_WINDOWS", "MINIMISE_ALL", "RESTORE_WINDOWS",
                    "MAXIMISE_WINDOW", "NEXT_WINDOW", "PREVIOUS_WINDOW",
                    "SWITCH_WORKSPACE", "FOCUS_APP"},
        "Media":   {"PLAY_MEDIA", "PAUSE_MEDIA", "STOP_MEDIA", "NEXT_MEDIA",
                    "PREVIOUS_MEDIA", "SHUFFLE_MEDIA", "REPEAT_MEDIA",
                    "MEDIA_CONTROL", "VOLUME", "SPEAK", "DICTATE", "TRANSCRIBE"},
        "System":  {"TIME", "DATE", "GET_TIME", "GET_DATE", "GET_WEATHER",
                    "CPU_USAGE", "RAM_USAGE", "SYSTEM_STATS", "HARDWARE_PROFILE",
                    "KEYBOARD", "MOUSE_CONTROL", "SCREENSHOT",
                    "SET_CLIPBOARD", "GET_CLIPBOARD", "SHELL_EXEC", "RUN_CMD",
                    "LIST_DIR", "CREATE_FOLDER", "READ_FILE",
                    "SET_TIMER", "SET_ALARM",
                    "POMODORO_START", "POMODORO_STOP", "POMODORO_STATUS",
                    "ADD_EVENT", "LIST_EVENTS", "SMART_HOME",
                    "PROACTIVE_START", "PROACTIVE_STOP", "CHECK_CHRONAL_ALIGNMENT"},
        "Stats":   {"RUNTIME_STATUS", "REASONING_MODE_STATUS", "MEMORY_STATUS", "COGNITION_STATUS",
                    "AWARENESS_STATUS", "MEMORY_STATS", "MEMORY_RECALL",
                    "MEMORY_STORE", "CLEAR_CHAT_HISTORY",
                    "USER_IDENTITY_SUMMARY", "SELF_REPORT",
                    "EXPLAIN_MEMORY_RUNTIME", "EXPLAIN_COGNITION_RUNTIME",
                    "PERSONAL_MEMORY_SUMMARY", "PERSONAL_MEMORY_DEEP_EXPLAIN",
                    "MEMORY_AUDIT",
                    "ROUTING_FAULT_EXPLAIN", "NAME_SOURCE_AUDIT",
                    "EXPLAIN_LAST_RESPONSE", "RUNTIME_AUDIT", "IMPORT_AUDIT",
                    "RESOLVE_RUNTIME_PATHS", "GUI_RUNTIME_AUDIT",
                    "MORNING_REPORT", "PROACTIVE_STATUS", "HABIT_STATUS",
                    "PERSONA_LOCK_STATUS", "PERSONA_LOCK_SET",
                    "PERSONA_LOCK_CLEAR", "LIST_CAPABILITIES", "CODE_CHANGES"},
        "Tools":   {"SCREEN_READ_ANALYZE", "SCREEN_LOCATE", "OCR_IMAGE",
                    "WEB_SEARCH", "NEWS_FETCH",
                    "ANALYZE_PDF", "ANALYZE_CSV", "ANALYZE_PDF_FOLDER",
                    "SUMMARIZE_FILE", "WRITE_NOTE", "NEW_NOTE",
                    "LIST_NOTES", "SEARCH_NOTES", "CONVERT_DOCUMENT",
                    "EXECUTE_GOAL", "SEQUENCE",
                    "SELF_ANALYZE", "SELF_IMPROVE", "SELF_PATCH",
                    "SELF_TEST", "SELF_UPGRADE",
                    "GENERATE_DOCUMENT", "GENERATE_SCRIPT",
                    "GENERATE_PROJECT", "CREATE_DOCUMENT",
                    "FIX_FILE", "SHOW_DIFF", "DATA_FABRICATOR",
                    "PLUGIN_LIST", "PLUGIN_INSTALL", "PLUGIN_UNINSTALL",
                    "PLUGIN_ENABLE", "PLUGIN_DISABLE", "PLUGIN_SEARCH",
                    "REFRESH_USER_INFO", "HELP"},
    }

    _WIZARD_QUESTIONS = [
        (
            "**Question 1/3 — Name & Purpose**\n\n"
            "What should this agent be called, and what is its main job?\n"
            "*(e.g. WeatherAgent — checks current weather and clothing advice)*"
        ),
        (
            "**Question 2/3 — Trigger & Data**\n\n"
            "What keywords or user requests should activate this agent, "
            "and what data sources or APIs will it use?\n"
            "*(e.g. keywords: weather, rain, temperature; data: OpenWeatherMap or local sensor)*"
        ),
        (
            "**Question 3/3 — Persona & Output**\n\n"
            "How should this agent present its findings? "
            "Describe its tone, output format, and any special behaviour.\n"
            "*(e.g. brief bullet-point summary, friendly tone, always include emojis)*"
        ),
    ]

    _SETTINGS_NAV = [
        ("Model",       "🧠"),
        ("Runtime",     "⚡"),
        ("Generation",  "✨"),
        ("Reasoning",   "🧩"),
        ("Identity",    "🎨"),
        ("Audio",       "🎙"),
        ("Application", "🖥"),
        ("Agents",      "🤖"),
        ("Gaze",        "👁"),
        ("Web Server",  "🌐"),
        ("Advanced",    "⚙️"),
    ]

    _REASONING_PAGE_MODES = (
        ("quick",             "Quick — direct single pass"),
        ("chain_of_thought",  "Chain of Thought — structured hidden reasoning"),
        ("self_consistency",  "Self-Consistency — N independent samples, best wins"),
        ("tree_of_thoughts",  "Tree of Thoughts — propose branches, develop best"),
        ("constitutional_ai", "Constitutional — generate, critique, revise"),
    )

    _REASONING_KEY_ORDER = ("max_tokens", "temperature", "top_p", "top_k",
                            "threshold", "passes", "samples", "branches", "stages")

    _REASONING_KEY_HELP = {
        "max_tokens": "Token budget for the visible answer (per sample where the mode runs several)",
        "temperature": "0 = deterministic · higher = more varied",
        "top_p": "Nucleus sampling cutoff",
        "top_k": "Top-k sampling cutoff",
        "threshold": "Confidence the result must reach before it's accepted",
        "passes": "Sequential refinement passes",
        "samples": "Independent samples drawn (self-consistency)",
        "branches": "Parallel branches proposed (tree of thoughts)",
        "stages": "Generate/critique/revise stages (constitutional)",
        "voice": "Algorithm descriptor — appended to the system prompt to shape this mode's behaviour",
    }

    def eventFilter(self, obj, event):
        if obj == self.chat_input and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape and getattr(self, "is_generating", False):
                self.stop_generation()
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                mods = event.modifiers()
                if mods & Qt.KeyboardModifier.ShiftModifier:
                    return False  # Shift+Enter — let Qt insert a newline
                if getattr(self, "is_generating", False):
                    return True  # Enter does nothing while generating — use Stop or Esc
                self.send_message()
                return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        if getattr(self, "_eli_shutdown_started", False):
            try:
                event.accept()
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            return

        self._eli_shutdown_started = True

        # Stop the in-process web server (if the user started one from Settings) so the
        # port is released on exit.
        try:
            _uv = getattr(self, "_srv_uv", None)
            if _uv is not None:
                _uv.should_exit = True
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        try:
            self._cancel_stream_requested = True
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # Abort in-flight generation before CognitiveEngine.shutdown() — otherwise
        # session-summary LLM work deadlocks on the broker lock (15+ min on CPU).
        try:
            from eli.cognition import gguf_inference as _ggi_close
            _ggi_close.signal_shutdown()
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        try:
            if self._runtime_stats_timer is not None:
                self._runtime_stats_timer.stop()
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # Phase 7: stop the live-data timers too so they don't fire
        # mid-shutdown and try to read a closed memory/database handle.
        for _attr in ("_memory_stats_timer", "_proactive_status_timer", "_other_surfaces_timer"):
            try:
                _t = getattr(self, _attr, None)
                if _t is not None:
                    _t.stop()
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        try:
            if self.auto_save_checkbox.isChecked() and self.conversation_history:
                self.save_conversation()
        except Exception as _save_err:
            log.debug(f"[GUI] Conversation autosave failed during close: {_save_err}")

        # The engine's shutdown (session summary, a model call that can take half a minute, memory
        # flush, model unload) runs once the event loop has ended: main() runs the registered
        # exit handlers after app.exec(). Run here, on this thread, it kept the window on screen
        # and unresponsive until it finished ("ELI is not responding").
        try:
            self.hide()
            event.accept()
        except Exception:
            log.debug("suppressed exception", exc_info=True)



def main():
    print(f"\n  {APP_NAME} {APP_VERSION}\n  ───────────────\n")

    # Portable / source: refresh menu + Desktop icons via stable eli-run pointer
    # (never leave Path=/…/ELI_v2-*-linux-portable baked into .desktop files).
    try:
        from eli.runtime.desktop_launchers import ensure_desktop_launchers
        ensure_desktop_launchers()
    except Exception:
        log.debug("desktop launcher refresh skipped", exc_info=True)

    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle('Fusion')
    try:
        from eli.gui.branding import load_app_icon
        _app_icon = load_app_icon()
        if _app_icon is not None:
            app.setWindowIcon(_app_icon)
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    app.setApplicationName(APP_NAME)

    # ── First-boot check: show wizard if no models are installed ─────────────
    try:
        _existing_models = discover_gguf_models()
    except Exception:
        _existing_models = []
    if not _existing_models:
        _wizard = FirstBootWizard()
        _accepted = _wizard.exec()
        # Persist the wizard's selection so a downloaded/selected model becomes
        # the CONFIGURED model — otherwise the wizard's work is thrown away and
        # the app launches "model not loaded" even after a successful download.
        try:
            from eli.core.runtime_settings import update_settings as _rs_update
            _wiz_provider = _wizard.selected_provider()
            if _wiz_provider == "ollama":
                update_kwargs = {
                    "provider": "ollama",
                    "ollama_host": _wizard.selected_ollama_host(),
                    "ollama_model": _wizard.selected_ollama_model(),
                    "first_run_complete": True,
                }
            else:
                _wiz_path = _wizard.selected_model_path()
                update_kwargs = {"first_run_complete": True}
                if _wiz_path:
                    update_kwargs.update({
                        "provider": _wiz_provider if _wiz_provider in ("bundled_gguf", "custom_gguf") else "custom_gguf",
                        "model_path": _wiz_path,
                        "custom_model_path": _wiz_path,
                        "gguf_model_path": _wiz_path,
                    })
            if _accepted:
                # Voice engine chosen in the wizard — persist before model load so a
                # neural voice's VRAM is a deliberate up-front choice.
                try:
                    _we = _wizard.selected_tts_engine()
                    update_kwargs["tts_engine"] = _we
                    _wv = _wizard.selected_tts_voice()
                    if _wv:
                        update_kwargs["tts_voice"] = _wv
                        os.environ["ELI_XTTS_DEVICE"] = "cuda"
                except Exception:
                    log.debug("[ELI] wizard tts engine read failed", exc_info=True)
                _rs_update(**update_kwargs)
        except Exception as _persist_err:
            log.debug(f"[ELI] First-boot selection persist failed: {_persist_err}")
        # Re-check after wizard — user may have placed/downloaded a model
        try:
            _existing_models = discover_gguf_models()
        except Exception:
            _existing_models = []
        if not _existing_models:
            # Wizard dismissed without a model — still launch but warn
            log.debug("[ELI] No models installed. GUI will launch without a model loaded.")

    window = EliMainWindow()
    window._debug_boot_ts = time.time()
    window.show()

    window.status_label.setText('🔴 Model not loaded')
    window.model_info_label.setText('🔴 Model: Not loaded')
    window.send_btn.setEnabled(False)

    _rc = app.exec()
    # The Qt event loop has ended (window closed). All ELI state is flushed by the
    # registered shutdown/atexit handlers; run them now, then HARD-exit. llama.cpp + FAISS
    # segfault during CPython's C++ destructor pass at normal interpreter exit (observed:
    # "Segmentation fault (core dumped)" AFTER the session summary is already written —
    # nothing is lost, but it's an ugly crash). os._exit bypasses that destructor pass.
    try:
        import atexit as _atexit
        _atexit._run_exitfuncs()   # flush memory + session summary + unload model first
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        if sys.stdout.isatty():
            print("\n[ELI] Shutdown complete.")
            sys.stdout.flush()
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    os._exit(_rc if isinstance(_rc, int) else 0)

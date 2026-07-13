"""ELI Labs — LabsTab container (hosts every sub-tab)

Extracted verbatim from the former eli/gui/labs_tab.py monolith (strangler
refactor, v3 core-modularisation). Shared preamble — Qt binding, plot paths,
physics tables — lives in eli.gui.labs._common.
"""
from __future__ import annotations
from eli.gui.labs._common import *  # noqa: F401,F403

from eli.gui.labs.notebook_tab import _NotebookTab
from eli.gui.labs.memory_conversations_tab import _MemoryAndConversationsTab
from eli.gui.labs.jupyter_tab import _JupyterTab
from eli.gui.labs.calculator_tab import _CalculatorTab
from eli.gui.labs.physics_tab import _PhysicsTab
from eli.gui.labs.file_chat_tab import _FileChatTab
from eli.gui.labs.workspaces_tab import _WorkspacesTab
from eli.gui.labs.sim_ide_tab import _SimIDETab
from eli.gui.labs.orchestration_tab import _OrchestrationTab
from eli.gui.labs.test_review_tab import _TestReviewTab  # noqa: F401


class LabsTab(QWidget):
    """
    Master Labs widget — tabs: Notebook | Conversations | ELI Memory |
    Jupyter | Calculator | Physics | Report | File Chat | Workspaces | Sim/IDE
    """

    def __init__(self, parent_window=None):
        super().__init__(parent_window)
        self._parent = parent_window
        self._build_ui()

    # ── ELI callback: dispatches to the main window's inference ───────────
    def _eli_ask(self, prompt: str) -> str:
        pw = self._parent
        if pw is None:
            return "(ELI not connected)"
        for attr in ("_engine_ask", "engine_ask", "_ask_eli", "ask_eli"):
            fn = getattr(pw, attr, None)
            if callable(fn):
                return fn(prompt) or ""
        # Fallback: try the inference queue directly
        try:
            from eli.cognition import gguf_inference
            result = gguf_inference.infer(prompt, max_tokens=512)
            text = result.get("text", "") if isinstance(result, dict) else str(result)
            try:
                from eli.cognition.output_governor import govern_output, normalize_assistant_text
                text = govern_output(normalize_assistant_text(prompt, text), is_grounded=False)
            except Exception:
                pass
            return text
        except Exception as ex:
            return f"[ELI inference error: {ex}]"

    def _get_memory(self):
        pw = self._parent
        if pw is None:
            return None
        return getattr(pw, "_memory", None) or getattr(pw, "memory", None)

    def _get_db_path(self) -> Optional[str]:
        try:
            from eli.core.paths import user_db_path
            return str(user_db_path())
        except Exception:
            return None

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        hdr = QLabel("⚗️  Labs — Scientific Workspace")
        hdr.setStyleSheet(
            "font-size:13px;font-weight:bold;padding:4px 8px;"
            "background:#1a1f2e;color:#e0e6f0;"
        )
        layout.addWidget(hdr)

        self._inner_tabs = QTabWidget()
        self._inner_tabs.setTabPosition(QTabWidget.TabPosition.North)
        layout.addWidget(self._inner_tabs)

        mem = self._get_memory()

        self._notebook_tab = _NotebookTab()
        self._inner_tabs.addTab(self._notebook_tab, "📓 Notebook")

        # Unified Memory & Conversations panel (replaces separate sub-tabs)
        self._mem_convs_tab = _MemoryAndConversationsTab(
            memory_adapter=mem,
            db_path=self._get_db_path(),
        )
        self._inner_tabs.addTab(self._mem_convs_tab, "🧠 Memory & Conversations")

        self._jupyter_tab = _JupyterTab()
        self._inner_tabs.addTab(self._jupyter_tab, "📓 Jupyter")

        self._calc_tab = _CalculatorTab()
        self._inner_tabs.addTab(self._calc_tab, "🧮 Calculator")

        self._physics_tab = _PhysicsTab()
        self._inner_tabs.addTab(self._physics_tab, "⚛️  Physics")

        # Report Builder was promoted to a top-level main tab
        # (create_report_builder_tab in the main window) — no longer a Labs sub-tab.

        self._file_chat_tab = _FileChatTab(eli_callback=self._eli_ask)
        self._inner_tabs.addTab(self._file_chat_tab, "📂 File Chat")

        self._workspaces_tab = _WorkspacesTab()
        self._inner_tabs.addTab(self._workspaces_tab, "🖥️  Workspaces")

        self._sim_ide_tab = _SimIDETab(eli_callback=self._eli_ask)
        self._inner_tabs.addTab(self._sim_ide_tab, "🔬 Sim / IDE")

        # Orchestration + Test & Review are developer/diagnostic tools, so they live
        # under Labs (with the other dev tools) rather than as prominent top-level tabs.
        try:
            self._orchestration_tab = _OrchestrationTab()
            self._inner_tabs.addTab(self._orchestration_tab, "🧭 Orchestration")
        except Exception as _orch_err:
            log.debug(f"[Labs] Orchestration sub-tab unavailable: {_orch_err}")
        try:
            _chat_cb = getattr(self._parent, "send_to_chat", None)
            self._test_review_tab = _TestReviewTab(eli_callback=self._eli_ask, chat_callback=_chat_cb)
            self._inner_tabs.addTab(self._test_review_tab, "🧪 Test & Review")
        except Exception as _tr_err:
            log.debug(f"[Labs] Test & Review sub-tab unavailable: {_tr_err}")

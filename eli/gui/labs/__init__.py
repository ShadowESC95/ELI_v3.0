"""ELI Labs package — the decomposed former labs_tab.py monolith.

Each scientific/dev sub-tab is now its own module. Import the public surface from
here (or from the eli.gui.labs_tab compatibility shim, unchanged for old callers).
"""
from __future__ import annotations

from eli.gui.labs._common import (
    _LABS_PLOT_DIR, _LABS_PLOT_FILES, _labs_plot_literal,
    PHYSICS_CONSTANTS, PHYSICS_VALUES,
)
from eli.gui.labs.highlighter import _PySyntaxHighlighter
from eli.gui.labs.code_runner import _CodeRunnerThread
from eli.gui.labs.bg_worker import _BgWorker
from eli.gui.labs.notebook_tab import _NotebookTab
from eli.gui.labs.memory_conversations_tab import _MemoryAndConversationsTab
from eli.gui.labs.jupyter_tab import _JupyterTab
from eli.gui.labs.calculator_tab import _CalculatorTab
from eli.gui.labs.physics_tab import _PhysicsTab
from eli.gui.labs.report_tab import _ReportTab
from eli.gui.labs.file_chat_tab import _FileChatTab
from eli.gui.labs.workspaces_tab import _WorkspacesTab
from eli.gui.labs.sim_ide_tab import _SimIDETab
from eli.gui.labs.orchestration_tab import _OrchestrationTab
from eli.gui.labs.test_review_tab import _TestReviewTab
from eli.gui.labs.container import LabsTab

__all__ = [
    "LabsTab",
    "_PySyntaxHighlighter", "_CodeRunnerThread", "_BgWorker",
    "_NotebookTab", "_MemoryAndConversationsTab", "_JupyterTab",
    "_CalculatorTab", "_PhysicsTab", "_ReportTab", "_FileChatTab",
    "_WorkspacesTab", "_SimIDETab", "_OrchestrationTab", "_TestReviewTab",
    "_LABS_PLOT_DIR", "_LABS_PLOT_FILES", "_labs_plot_literal",
    "PHYSICS_CONSTANTS", "PHYSICS_VALUES",
]

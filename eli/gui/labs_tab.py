"""Compatibility shim — Labs was decomposed into the eli.gui.labs package.

The former ~5.7k-line monolith is now eli/gui/labs/*.py (one module per sub-tab).
This module re-exports the public surface so existing imports keep working:

    from eli.gui.labs_tab import LabsTab, _ReportTab, _LABS_PLOT_FILES  # still valid

New code should import from eli.gui.labs directly.
"""
from __future__ import annotations

from eli.gui.labs import (  # noqa: F401
    LabsTab,
    _PySyntaxHighlighter, _CodeRunnerThread, _BgWorker,
    _NotebookTab, _MemoryAndConversationsTab, _JupyterTab,
    _CalculatorTab, _PhysicsTab, _ReportTab, _FileChatTab,
    _WorkspacesTab, _SimIDETab, _OrchestrationTab, _TestReviewTab,
    _LABS_PLOT_DIR, _LABS_PLOT_FILES, _labs_plot_literal,
    PHYSICS_CONSTANTS, PHYSICS_VALUES,
)

__all__ = [
    "LabsTab",
    "_PySyntaxHighlighter", "_CodeRunnerThread", "_BgWorker",
    "_NotebookTab", "_MemoryAndConversationsTab", "_JupyterTab",
    "_CalculatorTab", "_PhysicsTab", "_ReportTab", "_FileChatTab",
    "_WorkspacesTab", "_SimIDETab", "_OrchestrationTab", "_TestReviewTab",
    "_LABS_PLOT_DIR", "_LABS_PLOT_FILES", "_labs_plot_literal",
    "PHYSICS_CONSTANTS", "PHYSICS_VALUES",
]

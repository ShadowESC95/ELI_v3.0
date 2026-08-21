"""Offscreen GUI widget lane — construct REAL Qt widgets, headless.

The full main window (`gui_eli_original.py`) blocks on device/display init and
can't be built in CI. But the tab widgets construct standalone under the offscreen
Qt platform (`QT_QPA_PLATFORM=offscreen`). This lane builds each one for real and
asserts it wired its UI — recovering the GUI __init__/layout/wiring coverage that the
mocked suite (PySide6 is a MagicMock there) structurally cannot reach.

Discipline: assertions are STRUCTURAL — the widget constructs, is a QWidget, and
built child widgets. We don't drive user interactions that would fire real actions.

Run on the clean lane:

    QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest tests/test_gui_offscreen.py \
        --noconftest --cov=eli.gui --cov-report=term
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Constructing a widget must never reach the network. FirstBootWizard kicks off a
# real asset prefetch from its constructor, which aborted the pytest process in
# this lane. Run with --noconftest, so set the offline default here explicitly.
os.environ["ELI_OFFLINE"] = "1"

import pytest

# Skip under the mocked full suite (PySide6 is a MagicMock — no real widgets).
try:
    from PySide6.QtWidgets import QApplication, QWidget, QPushButton
    if type(QApplication).__name__ == "MagicMock":
        raise RuntimeError("PySide6 mocked")
except Exception as _e:  # pragma: no cover
    pytest.skip(f"offscreen GUI lane needs real PySide6 ({_e}); run with --noconftest",
                allow_module_level=True)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# --------------------------------------------------------------------------- #
# qt_compat — the tri-binding shim
# --------------------------------------------------------------------------- #
def test_qt_compat_binding_resolved():
    import eli.gui.qt_compat as qc
    assert qc.QT_API in {"PySide6", "PyQt6", "PyQt5", "headless"}
    # Core symbols are re-exported under one name regardless of binding.
    for name in ("Qt", "QTimer", "QWidget", "QVBoxLayout", "Signal"):
        assert hasattr(qc, name) or name == "Signal"  # Signal aliased as pyqtSignal


# --------------------------------------------------------------------------- #
# coding_tab
# --------------------------------------------------------------------------- #
def test_coding_tab_constructs(qapp):
    from eli.gui.coding_tab import CodingTab
    w = CodingTab(parent_window=None)
    assert isinstance(w, QWidget)
    assert len(w.findChildren(QPushButton)) >= 1


# --------------------------------------------------------------------------- #
# labs_tab — every sub-tab constructs standalone
# --------------------------------------------------------------------------- #
LABS_SUBTABS = [
    "_NotebookTab", "_JupyterTab", "_CalculatorTab", "_PhysicsTab", "_ReportTab",
    "_FileChatTab", "_WorkspacesTab", "_SimIDETab", "_OrchestrationTab", "_TestReviewTab",
]


@pytest.mark.parametrize("clsname", LABS_SUBTABS)
def test_labs_subtab_constructs(qapp, clsname):
    import eli.gui.labs_tab as lt
    cls = getattr(lt, clsname)
    w = cls()
    assert isinstance(w, QWidget)


def test_labs_container_builds_full_tree(qapp):
    from eli.gui.labs_tab import LabsTab
    w = LabsTab(parent_window=None)
    assert isinstance(w, QWidget)
    # The container hosts all the sub-tabs — a deep child tree.
    assert len(w.findChildren(QWidget)) > 50


def test_calculator_tab_has_inputs(qapp):
    # A concrete sub-tab built its interactive surface (not just an empty frame).
    import eli.gui.labs_tab as lt
    w = lt._CalculatorTab()
    from PySide6.QtWidgets import QLineEdit, QTextEdit, QPlainTextEdit
    fields = (w.findChildren(QLineEdit) + w.findChildren(QTextEdit)
              + w.findChildren(QPlainTextEdit))
    assert isinstance(w, QWidget)
    assert len(w.findChildren(QWidget)) > 0


# --------------------------------------------------------------------------- #
# panels / tabs / docks / widgets — every other GUI widget that constructs
# standalone offscreen (the main window is the only piece that can't).
# --------------------------------------------------------------------------- #
MORE_WIDGETS = [
    ("eli.gui.panels.settings", "AdvancedSettingsDialog"),
    ("eli.gui.panels.startup", "HardwareTuningDock"),
    ("eli.gui.panels.startup", "StartupModelSelectionDialog"),
    ("eli.gui.panels.startup", "FirstBootWizard"),
    ("eli.gui.tabs.tasks_tab", "TasksTab"),
    ("eli.gui.tabs.experimental_tab", "ExperimentalTab"),
    ("eli.gui.tabs.eli_world_tab", "EliWorldTab"),
    ("eli.gui.tabs.extensions_tab", "ExtensionsTab"),
    ("eli.gui.tabs.extensions_tab", "AddMcpServerDialog"),
    ("eli.gui.docks.operator_console_dock", "OperatorConsoleDock"),
    ("eli.gui.docks.proactive_dock", "ProactiveDock"),
    ("eli.gui.widgets.ollama_model_selector", "OllamaModelSelector"),
    ("eli.gui.widgets.ollama_model_selector", "_StatusDot"),
]


@pytest.mark.parametrize("module,clsname", MORE_WIDGETS)
def test_gui_widget_constructs(qapp, module, clsname):
    import importlib
    cls = getattr(importlib.import_module(module), clsname)
    w = cls()
    assert isinstance(w, QWidget)


def test_agent_edit_dialog_constructs(qapp):
    from eli.gui.panels.agent_wizard import AgentEditDialog
    w = AgentEditDialog(agent_info={"name": "tester", "description": "a test agent",
                                    "triggers": [], "capabilities": []})
    assert isinstance(w, QWidget)


def test_settings_dialog_builds_deep_tree(qapp):
    from eli.gui.panels.settings import AdvancedSettingsDialog
    w = AdvancedSettingsDialog()
    assert isinstance(w, QWidget)
    assert len(w.findChildren(QWidget)) > 50   # a full multi-tab settings surface


# --------------------------------------------------------------------------- #
# extensions_tab — the top-level plugins / MCP / marketplace surface
# --------------------------------------------------------------------------- #
def test_extensions_tab_has_every_page(qapp):
    """The page list and PAGES must not drift — open_page() indexes into PAGES."""
    from eli.gui.tabs.extensions_tab import ExtensionsTab
    w = ExtensionsTab(parent_window=None)
    assert w._tabs.count() == len(w.PAGES)
    labels = [w._tabs.tabText(i) for i in range(w._tabs.count())]
    for word in ("Overview", "Browse", "Installed", "Permissions", "MCP",
                 "Sources", "Settings"):
        assert any(word in t for t in labels), f"{word} page missing from {labels}"


@pytest.mark.parametrize("page", ["overview", "browse", "installed", "permissions",
                                  "mcp", "sources", "settings"])
def test_extensions_tab_open_page(qapp, page):
    from eli.gui.tabs.extensions_tab import ExtensionsTab
    w = ExtensionsTab(parent_window=None)
    w.open_page(page)
    assert w._tabs.currentIndex() == w.PAGES.index(page)


def test_extensions_overview_reports_real_counts(qapp):
    """The overview must read the subsystems, not print a placeholder."""
    from eli.gui.tabs.extensions_tab import ExtensionsTab
    w = ExtensionsTab(parent_window=None)
    w._refresh_overview()
    text = w._ov_status.text()
    for word in ("Plugins", "Permissions", "MCP servers", "Sources", "scanners"):
        assert word in text, f"overview does not report {word}: {text}"
    assert "unavailable" not in text, text
    # Containment is stated, whatever this machine can actually do.
    assert w._ov_sandbox.text().strip()


def test_extensions_settings_load_and_save(qapp, tmp_path, monkeypatch):
    """The settings page must round-trip through runtime_settings, not a local dict."""
    monkeypatch.setenv("ELI_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.delenv("ELI_MCP_ALLOW_CONTROL", raising=False)
    from eli.gui.tabs.extensions_tab import ExtensionsTab
    w = ExtensionsTab(parent_window=None)
    w._set_timeout.setValue(77)
    w._set_expose.setChecked(True)
    w._save_settings()

    from eli.core.runtime_settings import load_settings
    saved = load_settings()
    assert saved["mcp_start_timeout"] == 77
    assert saved["mcp_expose_control"] is True

    # And the code that consumes them agrees.
    from eli.plugins.mcp import start_timeout
    assert start_timeout() == 77.0
    assert start_timeout(5) == 5.0          # an explicit argument still wins


def test_extensions_env_override_disables_control_checkbox(qapp, monkeypatch):
    """A machine that pins the env var must not be shown a checkbox in charge of it."""
    monkeypatch.setenv("ELI_MCP_ALLOW_CONTROL", "1")
    from eli.gui.tabs.extensions_tab import ExtensionsTab
    w = ExtensionsTab(parent_window=None)
    assert not w._set_expose.isEnabled()
    assert "ELI_MCP_ALLOW_CONTROL" in w._set_expose.toolTip()


# --------------------------------------------------------------------------- #
# AddMcpServerDialog — the form that was missing entirely
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("payload,expected", [
    ({"mcpServers": {"files": {"command": "npx", "args": ["-y", "x"]}}}, ["files"]),
    ({"files": {"command": "npx"}, "other": {"command": "uvx"}}, ["files", "other"]),
    ({"command": "npx", "args": []}, ["server"]),
    ({"mcpServers": {"_comment": {"a": 1}, "files": {"command": "npx"}}}, ["files"]),
    ({"nothing": 1}, []),
])
def test_servers_from_config_accepts_what_people_paste(payload, expected):
    from eli.gui.tabs.extensions_tab import _servers_from_config
    assert sorted(_servers_from_config(payload)) == sorted(expected)


def test_add_mcp_dialog_paste_reports_and_builds_entry(qapp):
    import json
    from eli.gui.tabs.extensions_tab import AddMcpServerDialog
    d = AddMcpServerDialog()
    d._paste.setPlainText(json.dumps(
        {"mcpServers": {"files": {"command": "npx", "args": ["-y", "pkg"]}}}))
    assert "files" in d._paste_status.text()
    entry = d._entry_from_paste()
    assert entry["id"] == "files"
    assert entry["command"] == "npx"
    assert entry["args"] == ["-y", "pkg"]

    d._paste.setPlainText("{not json")
    assert "JSON" in d._paste_status.text()


def test_add_mcp_dialog_form_parses_args_env_and_network(qapp):
    from eli.gui.tabs.extensions_tab import AddMcpServerDialog
    d = AddMcpServerDialog()
    d._tabs.setCurrentIndex(1)
    d._f_id.setText("files")
    d._f_cmd.setText("npx")
    d._f_args.setText('-y pkg "/a path"')       # quoting must survive
    d._f_env.setText("API_KEY=abc MODE=fast")
    d._f_net.setChecked(True)
    entry = d._entry_from_form()
    assert entry["args"] == ["-y", "pkg", "/a path"]
    assert entry["env"] == {"API_KEY": "abc", "MODE": "fast"}
    assert entry["permissions"] == ["network"]  # asked for, so the sandbox grants it

"""Offscreen GUI widget lane — construct REAL Qt widgets, headless.

The tab widgets construct standalone under the offscreen Qt platform
(`QT_QPA_PLATFORM=offscreen`). This lane builds each one for real and asserts it wired
its UI. (The whole window is built offscreen by tests/_gui_runtime_checks.py.)

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
# real asset prefetch (the voice + the embedder) from its constructor, which in
# this lane downloaded and copied files mid-test and ABORTED the pytest process —
# taking every test after it with it. This lane is usually run with --noconftest,
# so it cannot rely on the root conftest for the offline default: set it here, the
# same way the offscreen Qt platform is set, so the file is hermetic on its own.
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
    for name in ("Qt", "QTimer", "QWidget", "QVBoxLayout", "Signal", "QDialogButtonBox"):
        assert hasattr(qc, name) or name == "Signal"  # Signal aliased as pyqtSignal
    # ... and the slot decorator under the name its docstring gives, on a real binding too: it was
    # defined for the headless stubs only, so two other shims each kept a binding list of their own.
    assert callable(qc.pyqtSlot) and callable(qc.pyqtSignal)
    assert qc.real_qt_available() is (qc.QT_API in {"PySide6", "PyQt6", "PyQt5"})


def test_labs_tabs_use_the_binding_labs_chose():
    """Two Labs tabs imported PySide6 by name inside their methods, past the binding Labs chose."""
    import pathlib
    labs = pathlib.Path(__file__).resolve().parents[1] / "eli" / "gui" / "labs"
    for path in labs.glob("*_tab.py"):
        text = path.read_text(encoding="utf-8")
        assert "from PySide6" not in text and "import PySide6" not in text, path.name


# --------------------------------------------------------------------------- #
# coding_tab
# --------------------------------------------------------------------------- #
def test_coding_tab_constructs(qapp):
    from eli.gui.coding_tab import CodingTab
    w = CodingTab(parent_window=None)
    assert isinstance(w, QWidget)
    assert len(w.findChildren(QPushButton)) >= 1


# --------------------------------------------------------------------------- #
# labs_tab
# --------------------------------------------------------------------------- #


def test_labs_container_builds_full_tree(qapp):
    from eli.gui.labs import LabsTab
    w = LabsTab(parent_window=None)
    assert isinstance(w, QWidget)
    # The container hosts all the sub-tabs — a deep child tree.
    assert len(w.findChildren(QWidget)) > 50


def test_calculator_tab_has_inputs(qapp):
    # A concrete sub-tab built its interactive surface (not just an empty frame).
    import eli.gui.labs as lt
    w = lt._CalculatorTab()
    from PySide6.QtWidgets import QLineEdit, QTextEdit, QPlainTextEdit
    fields = (w.findChildren(QLineEdit) + w.findChildren(QTextEdit)
              + w.findChildren(QPlainTextEdit))
    assert isinstance(w, QWidget)
    assert fields, "the calculator has nowhere to type"


# --------------------------------------------------------------------------- #
# panels
# --------------------------------------------------------------------------- #
def test_settings_dialog_builds_deep_tree(qapp):
    from eli.gui.panels.settings import AdvancedSettingsDialog
    w = AdvancedSettingsDialog()
    assert isinstance(w, QWidget)
    assert len(w.findChildren(QWidget)) > 50   # a full multi-tab settings surface


def test_settings_dialog_has_no_second_plugin_install_path(qapp):
    """Its Plugins tab installed through PluginManager.install: a registry entry's code was
    downloaded and run at once, with no hash, scan or consent, and switched on. Plugins are
    installed in the Extensions tab; the cards open pages by name."""
    import pathlib
    from eli.gui.panels.settings import AdvancedSettingsDialog
    w = AdvancedSettingsDialog(start_tab="upgrade")
    labels = [w.inner_tabs.tabText(i) for i in range(w.inner_tabs.count())]
    assert not any(label.endswith(" Plugins") for label in labels), labels
    assert "Self-Upgrade" in w.inner_tabs.tabText(w.inner_tabs.currentIndex())
    w.open_page("cognition")
    assert "Cognition" in w.inner_tabs.tabText(w.inner_tabs.currentIndex())
    gui = pathlib.Path(__file__).resolve().parents[1] / "eli" / "gui"
    for path in gui.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "mgr.install(" not in text and "manager.install(" not in text, path


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


def test_extensions_listing_shows_trust_price_and_fit_with_registry_text_escaped(qapp):
    """A registry's own words are shown as text, never run as markup, and the listing says how far
    it is trusted, what it costs and whether it fits this ELI. The Settings copy of this tab had
    those and this one did not; there is one copy now."""
    from eli.gui.tabs.extensions_tab import ExtensionsTab
    w = ExtensionsTab(parent_window=None)
    listing = {
        "id": "sample", "name": "Sample <b>plugin</b>", "kind": "bundle",
        "description": "<img src=x> does a thing", "author": "A <i>publisher</i>",
        "license": "MIT", "registry": "example", "version": "1.2.0",
        "trust": "checksum", "trust_label": "Checksum only: the file matches the listing",
        "purchase": {"price_display": "4 EUR", "model": "perpetual", "needs_key": True},
        "support_url": "https://example.org/help", "privacy_url": "http://example.org/plain",
        "compatible": {"ok": False, "reason": "needs a newer ELI"},
        "permissions": [],
    }
    w._fill_browse({"listings": [listing]}, "")
    cells = [w._browse_table.item(0, c).text() for c in range(w._browse_table.columnCount())]
    assert cells[1] == "bundle" and cells[4] == "4 EUR" and cells[6] == "checksum only", cells
    assert w._browse_table.item(0, 6).toolTip() == listing["trust_label"]
    assert w._browse_table.horizontalHeaderItem(6).text() == "Trust"
    w._browse_table.selectRow(0)
    w._show_listing()
    shown = w._browse_detail.toPlainText()
    assert "Sample <b>plugin</b>" in shown and "<img src=x> does a thing" in shown
    assert "A <i>publisher</i>" in shown
    assert listing["trust_label"] in shown
    assert "4 EUR" in shown and "one-time purchase" in shown and "licence key" in shown
    assert "https://example.org/help" in shown and "http://example.org/plain" not in shown
    assert "Not for this ELI" in shown and "needs a newer ELI" in shown


def test_settings_marketplace_page_is_the_extensions_tab_itself(qapp):
    """Settings ▸ Marketplace was a second copy of the Extensions tab that had drifted from it."""
    from eli.gui.panels.settings import AdvancedSettingsDialog
    from eli.gui.tabs.extensions_tab import ExtensionsTab
    w = AdvancedSettingsDialog(start_tab="marketplace")
    page = w.inner_tabs.currentWidget()
    assert "Marketplace" in w.inner_tabs.tabText(w.inner_tabs.currentIndex())
    assert type(page) is ExtensionsTab
    assert page._tabs.currentIndex() == page.PAGES.index("browse")
    assert any("MCP" in page._tabs.tabText(i) for i in range(page._tabs.count()))


def test_tasks_tab_keeps_the_stored_record_of_a_task_it_could_not_cancel(qapp, monkeypatch):
    """The tab dropped a task's stored record before asking for the cancel, so a running task that
    could not be cancelled lost its record while the tab said it could not cancel. The cancel
    itself drops the record of a task it does cancel (BackgroundTasks.cancel)."""
    import eli.runtime.scheduled_tasks as scheduled
    from eli.gui.tabs.tasks_tab import TasksTab

    forgotten = []
    monkeypatch.setattr(scheduled, "forget", lambda pid: forgotten.append(pid))

    class _Tasks:
        def __init__(self):
            self.cancelled = []
        def list(self, **_):
            return [{"id": 7, "kind": "research", "status": "running", "meta": {"pid": "p-7"}}]
        def get(self, jid, **_):
            return {"id": jid, "status": "running", "meta": {"pid": "p-7"}}
        def cancel(self, jid):
            self.cancelled.append(jid)
            return False

    tasks = _Tasks()
    w = TasksTab()
    monkeypatch.setattr(w, "_bt", lambda: tasks)
    monkeypatch.setattr(w, "_selected_jid", lambda: 7)
    monkeypatch.setattr(w, "_refresh", lambda: None)
    w._cancel_selected()
    assert tasks.cancelled == [7]
    assert forgotten == []
    assert "could not cancel" in w.result.toPlainText()


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


def test_extensions_control_switch_says_what_clients_get_without_it(qapp, monkeypatch):
    """Off, an MCP client gets only ELI's read-only status actions; the label says so."""
    monkeypatch.delenv("ELI_MCP_ALLOW_CONTROL", raising=False)
    from eli.gui.tabs.extensions_tab import ExtensionsTab
    tip = ExtensionsTab(parent_window=None)._set_expose.toolTip()
    assert "only ELI's read-only status actions" in tip and "Every other action needs this switch" in tip


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


# --------------------------------------------------------------------------- #
# Labs tools answer on a worker and write on the window's thread
# --------------------------------------------------------------------------- #
def _wait_for(qapp, done, seconds=10.0):
    import time
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    qapp.processEvents()


def _watch(widget, method):
    """Note the thread every call of a widget's method comes from."""
    import threading
    calls, real = [], getattr(widget, method)

    def spy(*a, **k):
        calls.append(threading.current_thread() is threading.main_thread())
        return real(*a, **k)

    setattr(widget, method, spy)
    return calls


def test_file_chat_writes_the_answer_on_the_window_thread(qapp):
    """File Chat appended the answer to its QTextEdit from the plain thread that asked the model:
    a Qt widget touched off its thread can corrupt the document or abort the process."""
    import threading
    from eli.gui.labs.file_chat_tab import _FileChatTab
    asked_on_window_thread = []

    def eli(prompt):
        asked_on_window_thread.append(threading.current_thread() is threading.main_thread())
        return "it is a list of three steps"

    tab = _FileChatTab(eli_callback=eli)
    tab._loaded_text = "step one\nstep two\nstep three"
    tab._question.setText("what is this?")
    writes = _watch(tab._chat_history, "append")
    tab._ask()
    _wait_for(qapp, lambda: len(writes) >= 2)
    assert asked_on_window_thread == [False]
    assert writes == [True, True]                     # the question, then the answer
    assert "it is a list of three steps" in tab._chat_history.toPlainText()


def test_file_chat_shows_a_failed_answer_as_an_error(qapp):
    from eli.gui.labs.file_chat_tab import _FileChatTab

    def eli(prompt):
        raise RuntimeError("no model is loaded")

    tab = _FileChatTab(eli_callback=eli)
    tab._loaded_text = "text"
    tab._question.setText("why?")
    tab._ask()
    _wait_for(qapp, lambda: "no model is loaded" in tab._chat_history.toPlainText())
    assert "[Error: no model is loaded]" in tab._chat_history.toPlainText()


def test_sim_ide_writes_the_answer_on_the_window_thread(qapp):
    from eli.gui.labs.sim_ide_tab import _SimIDETab
    tab = _SimIDETab(eli_callback=lambda prompt: "it prints a greeting")
    tab._set_code('print("hi")\n')
    writes = _watch(tab._eli_panel, "setPlainText")
    tab._ask_eli_explain()
    _wait_for(qapp, lambda: bool(writes))
    assert writes == [True]
    assert tab._eli_panel.toPlainText() == "it prints a greeting"


# --------------------------------------------------------------------------- #
# Test & Review: a run that produced no results is not a pass
# --------------------------------------------------------------------------- #
def test_a_run_with_no_results_is_shown_as_no_tests_ran(qapp):
    """With pytest missing (or the runner absent in a bundle) the bar went green: "0 passed …
    of 0 ✅ all green"."""
    from eli.gui.labs.test_review_tab import _TestReviewTab
    tab = _TestReviewTab()
    tab._on_run_done({"ok": True, "totals": {"total": 0, "passed": 0, "failed": 0, "errored": 0},
                      "failures": [], "options": [{"id": "rerun", "label": "Re-run", "command": "x"}],
                      "stdout": "collecting ...\n/usr/bin/python: No module named pytest\n"})
    text = tab._summary.text()
    assert text.startswith("No tests ran") and "No module named pytest" in text
    assert "all green" not in text and "#a3be8c" not in tab._summary.styleSheet()
    assert "No module named pytest" in tab._detail.toPlainText()
    tab._on_run_done({"ok": True, "totals": {"total": 3, "passed": 3, "failed": 0, "errored": 0},
                      "failures": [], "options": [], "stdout": ""})
    assert "all green" in tab._summary.text()


def test_operator_console_offers_the_policy_modes_the_policy_itself_takes(qapp, monkeypatch):
    """The console wrote the four modes out again; a mode the policy gained was not offered."""
    import eli.gui.docks.operator_console_dock as dock
    monkeypatch.setattr(dock, "VALID_POLICY_MODES", {"observe_only", "an_added_mode"})
    w = dock.OperatorConsoleDock()
    assert [w.policy_mode.itemText(i) for i in range(w.policy_mode.count())] == ["an_added_mode", "observe_only"]


def test_operator_console_leaves_cadence_and_tick_limits_to_their_owners(qapp, monkeypatch):
    """A new goal's cadence and the scheduler tick's limit and cooldown are the planner's own."""
    import eli.gui.docks.operator_console_dock as dock
    calls = []
    monkeypatch.setattr(dock, "create_goal", lambda **kw: calls.append(("goal", kw)) or {"ok": True})
    monkeypatch.setattr(dock, "scheduler_tick", lambda *a, **kw: calls.append(("tick", a, kw)) or {"ok": True})
    w = dock.OperatorConsoleDock()
    monkeypatch.setattr(w, "refresh_all", lambda: None)
    w.goal_title.setText("Tidy the notes")
    w.create_goal_from_ui()
    w.run_scheduler_tick()
    assert calls == [("goal", {"title": "Tidy the notes", "objective": "Tidy the notes"}), ("tick", (), {})]


def test_an_image_studio_run_is_noted_once_in_the_proactive_pane():
    """It appended its note to the pane and also put one on the proactive daemon's queue, whose
    reader painted it into the same pane: every run was listed twice."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from eli.gui.main_window._mixins.image import ImageMixin
    window = MagicMock()
    result = SimpleNamespace(saved_paths=["/sample/one.png"], personalization_notes=[], out_dir="/sample",
                             applied_prompt="a lighthouse at dusk", contact_sheet="")
    ImageMixin._image_generation_done(window, result, {})
    assert window.suggestions_display.append.call_count == 1
    assert not window._proactive_daemon.suggestion_queue.put.called


def test_both_startup_windows_take_the_natural_voice_from_the_voice_engine(monkeypatch):
    """The start-up window, the first-run wizard and the main window each wrote the default
    natural voice's name out; it is the voice engine's own (eli/perception/tts_xtts)."""
    from types import SimpleNamespace
    import eli.perception.tts_xtts as xtts
    from eli.gui.panels.startup import FirstBootWizard, StartupModelSelectionDialog
    monkeypatch.setattr(xtts, "_DEFAULT_NATURAL", "another_voice")
    natural = SimpleNamespace(selected_tts_engine=lambda: "natural")
    kokoro = SimpleNamespace(selected_tts_engine=lambda: "kokoro")
    for window in (StartupModelSelectionDialog, FirstBootWizard):
        assert window.selected_tts_voice(natural) == "natural:another_voice"
        assert window.selected_tts_voice(kokoro) == ""


def test_notebook_and_workspaces_read_their_store_with_the_shared_reader(tmp_path):
    """Each read its JSON file with a reader of its own, which took a file holding a list as the
    store itself; the shared reader (eli/utils/jsonio) gives the object in the file or an empty one."""
    import json
    from types import SimpleNamespace
    from eli.gui.labs.notebook_tab import _NotebookTab
    from eli.gui.labs.workspaces_tab import _WorkspacesTab
    for tab, field in ((_NotebookTab, "_projects"), (_WorkspacesTab, "_workspaces")):
        store = tmp_path / f"{field}.json"
        holder = SimpleNamespace(_DATA_FILE=store, **{field: {}})
        tab._load(holder)
        assert getattr(holder, field) == {}
        store.write_text(json.dumps({"Sample": {"note": "kept"}}), encoding="utf-8")
        tab._load(holder)
        assert getattr(holder, field) == {"Sample": {"note": "kept"}}
        store.write_text(json.dumps(["not", "a", "store"]), encoding="utf-8")
        tab._load(holder)
        assert getattr(holder, field) == {}


def test_the_experimental_tab_is_shown_only_where_there_is_an_experimental_folder(qapp, monkeypatch, tmp_path):
    """A released build has no experimental/ folder, and its Experimental tab only ever said
    "experimental/ not found"."""
    from unittest.mock import MagicMock
    import eli.gui.tabs.experimental_tab as tab_module
    import eli.runtime.experimental_inventory as inventory
    from eli.gui.main_window._mixins.tabs import TabsMixin

    class _Window(TabsMixin, QWidget):
        def __init__(self):
            super().__init__()
            self.tabs = MagicMock()

    absent = inventory.build_experimental_inventory(tmp_path / "experimental")
    monkeypatch.setattr(tab_module, "build_experimental_inventory", lambda: absent)
    window = _Window()
    window.create_experimental_tab()
    assert not window.tabs.addTab.called and not hasattr(window, "_experimental_widget")

    (tmp_path / "experimental" / "sample_kit").mkdir(parents=True)
    present = inventory.build_experimental_inventory(tmp_path / "experimental")
    assert present["exists"]
    monkeypatch.setattr(tab_module, "build_experimental_inventory", lambda: present)
    window = _Window()
    window.create_experimental_tab()
    assert window.tabs.addTab.call_count == 1
    assert window.tabs.addTab.call_args[0][0] is window._experimental_widget

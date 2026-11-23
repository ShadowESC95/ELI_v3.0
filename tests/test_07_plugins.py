"""
test_07_plugins.py
==================
Tests for eli.plugins — manager, base, all plugin packages.
"""
import importlib
from pathlib import Path
import pytest


PLUGIN_NAMES = [
    "calendar", "document_reader", "media", "notes",
    "pomodoro", "system_stats", "tts",
    "weather", "web", "web_automation",
]


def test_plugins_manager_has_class():
    mod = importlib.import_module("eli.plugins.manager")
    syms = dir(mod)
    matches = [s for s in syms if "Manager" in s or "Plugin" in s]
    assert matches, f"No PluginManager class in manager.py: {syms}"


def test_plugins_base_has_base_class():
    mod = importlib.import_module("eli.plugins.base.base")
    syms = dir(mod)
    matches = [s for s in syms if "Base" in s or "Plugin" in s]
    assert matches, f"No base plugin class: {syms}"


@pytest.mark.parametrize("plugin", PLUGIN_NAMES, ids=PLUGIN_NAMES)
def test_plugin_package_importable(plugin):
    pkg = importlib.import_module(f"eli.plugins.{plugin}")
    assert pkg is not None


@pytest.mark.parametrize("plugin", PLUGIN_NAMES, ids=PLUGIN_NAMES)
def test_plugin_module_importable(plugin):
    mod = importlib.import_module(f"eli.plugins.{plugin}.plugin")
    assert mod is not None


@pytest.mark.parametrize("plugin", PLUGIN_NAMES, ids=PLUGIN_NAMES)
def test_plugin_has_plugin_class(plugin):
    mod = importlib.import_module(f"eli.plugins.{plugin}.plugin")
    syms = dir(mod)
    # Accept: a class with Plugin/plugin-name in its name, OR the standard
    # function-based plugin API (execute, PLUGIN_ID, ACTIONS)
    matches = [
        s for s in syms
        if "Plugin" in s
        or plugin.replace("_", "").capitalize() in s
        or s in ("execute", "PLUGIN_ID", "ACTIONS", "run", "handle")
    ]
    assert matches, f"No Plugin class or execute() in {plugin}/plugin.py: {syms}"


def test_plugins_registry_index_exists():
    import os
    idx = os.path.join(
        importlib.import_module("eli.plugins").__file__,
        "..", "registry", "index.json"
    )
    idx = os.path.normpath(idx)
    assert os.path.isfile(idx), f"Plugin registry index.json missing at {idx}"


def test_the_notes_plugin_writes_to_the_one_notes_folder(tmp_path, monkeypatch):
    """The notes plugin kept its own store under artifacts/notes, apart from the notes action's."""
    import eli.execution.effectors.notes as notes_effector
    from eli.plugins.notes.plugin import QuickNotesPlugin
    folder = tmp_path / "the_notes"
    monkeypatch.setattr(notes_effector, "notes_folder", lambda: folder, raising=False)
    res = QuickNotesPlugin().new_note({"title": "shopping", "content": "an invented note"})
    assert res["ok"] and Path(res["path"]).parent == folder
    assert QuickNotesPlugin().search_notes({"query": "invented"})["ok"]


def test_the_notes_plugin_falls_back_to_the_user_notes_directory(tmp_path, monkeypatch):
    import eli.core.paths as paths
    import eli.execution.effectors.notes as notes_effector
    from eli.plugins.notes.plugin import QuickNotesPlugin
    monkeypatch.delattr(notes_effector, "notes_folder", raising=False)
    monkeypatch.setattr(paths, "notes_dir", lambda: tmp_path / "user_notes")
    res = QuickNotesPlugin().new_note({"content": "another invented note"})
    assert res["ok"] and Path(res["path"]).parent == tmp_path / "user_notes"


def test_a_new_users_plugin_state_is_not_another_programs(tmp_path, monkeypatch):
    """The plugin state of every ~/.config/eli-* and ~/.eli_* folder (earlier ELI programs, ELI VR, the
    licence keys) was copied in when v3 had none, so a new user started with the plugins another
    program had enabled."""
    import json
    import eli.plugins.manager as manager
    home = tmp_path / "home"
    other = home / ".config" / "eli-other-program"
    other.mkdir(parents=True)
    (other / "plugins_state.json").write_text(json.dumps({"enabled": ["someone_elses"], "disabled": [],
                                                          "installed": {}}), encoding="utf-8")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("ELI_ARTIFACTS_DIR", str(tmp_path / "v3_state"))
    state = json.loads(manager._state_file().read_text(encoding="utf-8"))
    assert state["enabled"] == [] and state["installed"] == {}

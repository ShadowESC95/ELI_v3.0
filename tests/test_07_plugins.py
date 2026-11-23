"""
test_07_plugins.py
==================
Tests for eli.plugins — manager, base, all plugin packages.
"""
import importlib
from pathlib import Path


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

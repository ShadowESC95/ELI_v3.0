"""Notes live in one folder (core.paths.notes_dir(), or ELI_NOTES_DIR when set), never the folder
ELI happens to run from, and WRITE_NOTE writes where LIST_NOTES and SEARCH_NOTES look."""
from __future__ import annotations

from pathlib import Path


def _run(action, args):
    from eli.execution.effectors.registry import get
    import eli.execution.effectors.notes  # noqa: F401  (registers the note actions)
    import eli.execution.effectors.files_notes  # noqa: F401
    return get(action)(action, args)


def test_an_unset_notes_variable_is_not_the_current_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("ELI_NOTES_DIR", raising=False)
    here = tmp_path / "somewhere"
    here.mkdir()
    (here / "README.md").write_text("# not a note\n", encoding="utf-8")
    monkeypatch.chdir(here)
    from eli.core.paths import notes_dir
    from eli.execution.effectors.notes import notes_folder

    assert notes_folder() == Path(notes_dir())
    listed = _run("LIST_NOTES", {})
    assert "README.md" not in listed.get("notes", [])
    made = _run("NEW_NOTE", {"title": "garden plan", "content": "plant the tulips"})
    assert Path(made["path"]).parent == Path(notes_dir())
    assert not (here / "garden_plan.md").exists()


def test_a_written_note_is_listed_and_found(tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_NOTES_DIR", str(tmp_path / "notes"))
    monkeypatch.chdir(tmp_path)
    wrote = _run("WRITE_NOTE", {"text": "the boiler service is on the ninth"})
    assert wrote["ok"] is True
    assert Path(wrote["path"]).parent == tmp_path / "notes"
    assert not (tmp_path / "eli_notebook").exists()
    assert Path(wrote["path"]).name in _run("LIST_NOTES", {})["notes"]
    assert _run("SEARCH_NOTES", {"query": "boiler service"})["hits"] == [Path(wrote["path"]).name]


def test_a_second_note_of_the_same_name_keeps_the_first(tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_NOTES_DIR", str(tmp_path))
    _run("NEW_NOTE", {"title": "shopping", "content": "oat milk"})
    again = _run("NEW_NOTE", {"title": "shopping", "content": "lemons"})
    text = Path(again["path"]).read_text(encoding="utf-8")
    assert "oat milk" in text and "lemons" in text

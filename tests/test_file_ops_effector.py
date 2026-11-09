"""The file effector (eli/execution/effectors/file_ops.py): CREATE_FILE never writes over a file
that is there, and SUMMARIZE_FILE on a folder reads the folder's files instead of guessing from
their names."""
from __future__ import annotations

from pathlib import Path


def test_create_file_leaves_an_existing_file_as_it_is(tmp_path):
    from eli.execution.effectors.file_ops import _h_create_file

    target = tmp_path / "plan.txt"
    first = _h_create_file("CREATE_FILE", {"path": str(target), "content": "first draft"})
    assert first["ok"] is True and first["created"] is True
    second = _h_create_file("CREATE_FILE", {"path": str(target), "content": "something else"})
    assert second["ok"] is False
    assert second["error"] == "exists" and second["created"] is False
    assert target.read_text(encoding="utf-8") == "first draft"
    assert "Created" not in second["response"]


def test_create_file_replaces_only_when_asked(tmp_path):
    from eli.execution.effectors.file_ops import _h_create_file

    target = tmp_path / "plan.txt"
    target.write_text("old", encoding="utf-8")
    out = _h_create_file("CREATE_FILE", {"path": str(target), "content": "new", "overwrite": True})
    assert out["ok"] is True
    assert Path(out["path"]).read_text(encoding="utf-8") == "new"


def test_summarize_on_a_folder_reads_it_and_does_not_guess_from_names(tmp_path, monkeypatch):
    """SUMMARIZE_FILE on a folder with no PDFs sent `ls -R` to the model to infer what it was for."""
    from eli.execution.effectors import file_ops

    (tmp_path / "README.md").write_text("# Kiln log\nRecords firing temperatures.\n", encoding="utf-8")
    ran = []
    monkeypatch.setattr(file_ops, "_execute_impl", lambda action, args: ran.append((action, args)) or {"ok": True})
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no ls -R")))
    file_ops._h_summarize_file("SUMMARIZE_FILE", {"path": str(tmp_path), "instruction": "what is this folder"})
    assert ran == [("ANALYZE_PDF_FOLDER", {"folder": str(tmp_path), "recursive": True,
                                           "instruction": "what is this folder"})]

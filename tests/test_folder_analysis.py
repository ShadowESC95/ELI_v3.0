"""Reading a folder: each PDF into the document index (not personal memory), one summary over all of them,
the whole result saved as a document, and progress the job can report."""
from pathlib import Path

import pytest

from eli.runtime import folder_analysis as fa


def test_a_reply_cut_at_its_limit_ends_at_its_last_finished_line():
    assert fa.finished("### Methods\n1. **Action:** variational.\n4.  **") == "### Methods\n1. **Action:** variational."
    assert fa.finished("All done.") == "All done."
    assert fa.finished("The documents outline an attempt to") == "The documents outline an attempt to…"
    # cut right after a heading's closing ** it looked finished, and the PDF showed the heading alone
    cut = "Intro.\n\n6. **Roadmaps**:\n   - Numerical methods.\n\n7.  **Theoretical Challenges**"
    assert fa.finished(cut) == "Intro.\n\n6. **Roadmaps**:\n   - Numerical methods."
    assert fa.finished("Intro.\n\nKey themes include:\n1.  **") == "Intro."


def test_the_model_is_told_how_many_words_its_reply_holds(monkeypatch):
    monkeypatch.setattr("eli.cognition.context_budget.chars_per_token", lambda: 4.0)
    assert fa._words(700, "aaaa bbbb") == int(700 * 4.0 * 2 / 9)
    assert fa._words(700, "a b c d") > fa._words(700, "abcdefghij klmnopqrst")     # longer words, fewer
    assert all("{words}" in ask for ask in (fa._PART_ASK, fa._JOIN_ASK, fa._FILE_ASK))


def test_every_document_reaches_the_summary_however_many_there_are():
    asked = []

    def call(text, tokens):
        asked.append(text)
        return f"summary {len(asked)}."

    titles = [f"### Doc{i}.pdf\n" + "x" * 300 for i in range(12)]
    out = fa._summary(titles, "physics", call, room=len(fa._PART_ASK) + 1000, report=lambda *a: None)
    parts = [a for a in asked if a.startswith("Below are the opening pages")]
    assert len(parts) > 1
    assert all(any(f"Doc{i}.pdf" in p for p in parts) for i in range(12))
    assert asked[-1].startswith("Below are 4 summaries, each of a share of the 12 documents") and "Group" not in asked[-1]
    assert all(" words, so it ends complete." in a for a in asked)
    assert out == f"summary {len(asked)}."


@pytest.fixture
def index(tmp_path, monkeypatch):
    from eli.memory.document_index import DocumentIndex
    idx = DocumentIndex(tmp_path / "documents.sqlite3", embed=lambda text: None)
    monkeypatch.setattr("eli.memory.document_index.get_document_index", lambda: idx)
    monkeypatch.setenv("ELI_DATA_DIR", str(tmp_path / "data"))
    return idx


def test_a_folder_goes_to_the_document_index_and_a_saved_document(tmp_path, index, monkeypatch):
    folder = tmp_path / "physics"
    folder.mkdir()
    for i in range(3):
        (folder / f"chapter{i}.pdf").write_text(f"Chapter {i} the scalar field and its potential")
    for sub in ("a", "b"):          # the same name in two subfolders is shown by where it is
        (folder / sub).mkdir()
        (folder / sub / "notes.pdf").write_text(f"Notes on {sub}")
    (folder / "broken.pdf").write_bytes(b"")
    # the reader stands in for pypdf, which the test setup's mocked Pillow keeps from importing
    monkeypatch.setattr("eli.plugins.document_reader.plugin.document_sections",
                        lambda p: [("p. 1", p.read_text())] if p.stat().st_size else [])
    calls, seen = [], []
    monkeypatch.setattr(fa, "_model", lambda: ((lambda text, n: calls.append(text) or "It covers $\\phi$."), 100_000))
    stored = []
    monkeypatch.setattr("eli.memory.memory.Memory.store_memory", lambda *a, **k: stored.append(a))
    out = fa.analyse(folder, progress=lambda done, total, stage: seen.append((stage, done, total)))
    assert out["count"] == 5 and out["unreadable"] == ["broken.pdf"] and not stored
    assert {d["title"] for d in index.documents()} == {"chapter0.pdf", "chapter1.pdf", "chapter2.pdf", "notes.pdf"}
    assert "| a/notes.pdf |" in out["content"] and "| b/notes.pdf |" in out["content"]
    assert all(f"chapter{i}.pdf" in calls[0] for i in range(3))
    assert ("reading", 6, 6) in seen and seen[-1][0] == "summarising"
    assert "## Documents" in out["content"] and "broken.pdf" in out["content"]
    saved = out["document_path"]
    assert saved and (saved.endswith(".pdf") or saved.endswith(".docx"))


def test_installed_software_in_the_folder_is_not_read_as_documents(tmp_path):
    """A physics folder held three virtualenvs: matplotlib's toolbar icons came back as documents."""
    from eli.memory.document_index import document_files
    (tmp_path / "papers").mkdir()
    (tmp_path / "papers" / "thesis.pdf").write_bytes(b"x")
    env = tmp_path / "sims" / "venv-sims"
    (env / "lib" / "site-packages" / "matplotlib").mkdir(parents=True)
    (env / "pyvenv.cfg").write_text("home = /usr/bin")
    (env / "lib" / "site-packages" / "matplotlib" / "back.pdf").write_bytes(b"x")
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache" / "old.pdf").write_bytes(b"x")
    (tmp_path / "notes.txt").write_text("x")
    assert [p.name for p in document_files(tmp_path)] == ["thesis.pdf"]


def _two_folders(tmp_path, monkeypatch, reply):
    from eli.runtime import folder_analysis as fa
    made = []
    for name in ("one", "two"):
        d = tmp_path / name
        d.mkdir()
        made.append(str(d))

    class _Index:
        def add(self, path, **kw):
            return {"ok": True, "doc_id": str(path)}

        def outline(self, doc_id, share):
            return {"title": Path(doc_id).name, "path": doc_id, "pages": 1, "chars": 3000, "opening": ("w " * 1500)[:share]}

        def note(self, *a, **k):
            pass

    seen = []

    def call(text, tokens):
        seen.append(text)
        return reply

    monkeypatch.setattr(fa, "_model", lambda: (call, 100000))
    monkeypatch.setattr("eli.memory.document_index.get_document_index", lambda: _Index())
    monkeypatch.setattr("eli.memory.document_index.document_files",
                        lambda root, *a: [Path(root) / "paper.pdf"])
    monkeypatch.setattr("eli.runtime.rich_document.write_document", lambda md, path, title="": path)
    return fa, made, seen


def test_several_folders_are_one_piece_of_work(tmp_path, monkeypatch):
    fa, made, _ = _two_folders(tmp_path, monkeypatch, "What they cover.")
    out = fa.analyse(made)
    assert out["ok"] and out["count"] == 2 and "one/paper.pdf" in out["content"] and "two/paper.pdf" in out["content"]


def test_reading_into_the_index_is_not_analysing(tmp_path, monkeypatch):
    fa, made, _ = _two_folders(tmp_path, monkeypatch, "")
    out = fa.analyse(made)
    assert out["ok"] is False and out["summarised"] is False and out["indexed"] == 2
    assert "## Summary" not in out["content"] and "nothing has been analysed" in out["content"]


def test_asked_to_analyse_the_whole_text_is_read(tmp_path, monkeypatch):
    fa, made, seen = _two_folders(tmp_path, monkeypatch, "Claim and method.")
    out = fa.analyse(made, whole=True)
    assert out["ok"] and "read in full" in out["content"] and "## Analysis" in out["content"]
    assert sum(t.count("w ") for t in seen) >= 2 * 1400     # all of both, not their openings


# ── a folder with no PDFs is read from its own files ──────────────────────────────────────────────
def _project(tmp_path):
    folder = tmp_path / "garden_tool"
    (folder / "garden").mkdir(parents=True)
    (folder / "README.md").write_text("# Garden tool\nPlans watering from soil readings.\n")
    (folder / "garden" / "water.py").write_text("from garden import soil\n\ndef plan():\n    return soil.read()\n")
    (folder / "garden" / "soil.py").write_text("def read():\n    return 0.4\n")
    (folder / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\n\0\0\0binary")
    (folder / ".secret").write_text("hidden")
    return folder


def test_a_folder_with_no_pdfs_is_read_from_its_files(tmp_path, index, monkeypatch):
    folder = _project(tmp_path)
    asked = []
    monkeypatch.setattr(fa, "_model", lambda: ((lambda text, n: asked.append(text) or "A watering planner."), 100_000))
    out = fa.analyse(folder, progress=lambda *a: None)
    assert out["ok"] is True and out["count"] == 3
    read = "\n".join(asked)
    assert "Plans watering from soil readings" in read and "def plan()" in read and "return 0.4" in read
    assert "binary" not in read and "hidden" not in read
    assert read.startswith("Below are the openings of 3 files")
    assert "README.md" in out["markdown"] and "garden/water.py" in out["markdown"]


def test_the_files_that_define_a_folder_come_first(tmp_path):
    folder = _project(tmp_path)
    order = [f.relative_to(folder).as_posix() for f in fa.folder_files(folder)]
    assert order[0] == "README.md"
    assert order.index("garden/soil.py") < order.index("garden/water.py")   # named by water.py
    assert "logo.png" not in order and ".secret" not in order


def test_a_checkout_leaves_out_what_its_gitignore_ignores(tmp_path):
    import subprocess
    folder = _project(tmp_path)
    (folder / "build").mkdir()
    (folder / "build" / "out.txt").write_text("generated")
    (folder / ".gitignore").write_text("build/\n")
    if subprocess.run(["git", "-C", str(folder), "init", "-q"], capture_output=True).returncode != 0:
        pytest.skip("git is not installed")
    order = [f.relative_to(folder).as_posix() for f in fa.folder_files(folder)]
    assert "build/out.txt" not in order and "README.md" in order

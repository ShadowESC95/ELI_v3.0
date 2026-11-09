from pathlib import Path


# Report Builder was extracted from the labs_tab.py monolith into its own module
# during the v3 core-modularisation strangler refactor. These tests assert on the
# report-builder *source*, so they follow it to its new home.
LABS_SOURCE = Path("eli/gui/labs/report_tab.py")


def _source() -> str:
    return LABS_SOURCE.read_text(encoding="utf-8")


def test_report_builder_exposes_quality_citation_depth_controls():
    source = _source()

    assert "_TEMPLATES" not in source
    assert "Generate Template" not in source
    assert "Template:" not in source
    assert "Report Builder: document generation profile" in source
    assert "_QUALITY_PROFILES" in source
    assert "_CITATION_POLICIES" in source
    assert "_DEPTH_PROFILES" in source
    assert "Document type:" in source
    assert "Generation Plan" in source
    assert "Plan Summary" in source
    assert "Raw Debug Prompt" in source
    assert "Quality bar:" in source
    assert "Citation policy:" in source
    assert "Run internal review pass" in source
    assert "Auto-save finished draft" in source
    assert "📄 Report Builder" not in source  # no longer a Labs sub-tab


def test_report_builder_keeps_document_type_generation_profiles():
    source = _source()

    for doc_type in (
        '"Document"',
        '"Article"',
        '"Research Article"',
        '"Review Article"',
        '"Master\'s Thesis"',
        '"PhD Dissertation"',
        '"Peer-Review Paper"',
        '"Literature Review"',
        '"Research Proposal"',
        '"Lab Report"',
        '"Technical Report"',
        '"Simulation Report"',
    ):
        assert doc_type in source

    assert "document types define quality" in source
    assert "fill-in-the-blank bodies" in source


def test_report_prompts_enforce_evidence_and_acceptance_contracts():
    source = _source()

    assert "QUALITY BAR:" in source
    assert "EVIDENCE DISCIPLINE:" in source
    assert "FINAL ACCEPTANCE TEST BEFORE OUTPUT:" in source
    assert "[source needed]" in source
    assert "no fake file paths" in source
    assert "no fake citations" in source
    assert "Evidence Ledger / Source Coverage Matrix" in source


def test_report_pipeline_has_review_revision_and_autosave():
    source = _source()

    assert "def _build_revision_prompt" in source
    assert "critiquing draft against quality contract" in source
    assert "applying review feedback" in source
    assert "def _autosave_report" in source
    assert "artifacts" in source and "documents" in source


# ── behaviour, on the real widget (offscreen Qt) ─────────────────────────────

import subprocess
import threading
import time
import types

import pytest


@pytest.fixture
def report_tab(monkeypatch):
    from PySide6.QtWidgets import QApplication, QMessageBox
    app = QApplication.instance() or QApplication([])
    import eli.gui.labs.report_tab as rt
    shown = []
    for kind in ("information", "warning", "critical"):
        monkeypatch.setattr(QMessageBox, kind, lambda *a, _k=kind, **kw: shown.append((_k, a[1:])))
    tab = rt._ReportTab(eli_callback=lambda prompt: "")
    tab._shown = shown
    return app, rt, tab


def _converter(monkeypatch, rt, tab, exe):
    """A stand-in for pandoc / lualatex: it writes what the real one writes, where it is told."""
    seen = []

    def run(cmd, cwd=None, **kw):
        seen.append(list(cmd))
        if "-o" in cmd:                                        # pandoc <in> -o <out>
            pathlib_out = cmd[cmd.index("-o") + 1]
            open(pathlib_out, "wb").write(b"converted")
        else:                                                  # lualatex -output-directory <d> <tex>
            out_dir = cmd[cmd.index("-output-directory") + 1]
            stem = cmd[-1].rsplit("/", 1)[-1].rsplit(".", 1)[0]
            for ext in (".pdf", ".aux", ".log", ".toc"):
                open(f"{out_dir}/{stem}{ext}", "wb").write(b"converted")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(rt, "subprocess", types.SimpleNamespace(run=run, TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(tab, "_find_executable", lambda names: exe if exe in names else None)
    return seen


@pytest.mark.parametrize("kind,exe,mine", [
    ("docx", "pandoc", ["report.md"]),
    ("pdf", "lualatex", ["report.tex", "report.aux", "report.log", "report.toc"]),
    ("pdf", "pandoc", ["report.md"]),
])
def test_an_export_leaves_the_users_own_files_beside_it_alone(report_tab, monkeypatch, tmp_path, kind, exe, mine):
    """Exporting report.docx wrote its pandoc input over the user's report.md and then deleted it;
    a PDF overwrote report.tex and deleted report.aux/.log/.toc."""
    app, rt, tab = report_tab
    for name in mine:
        (tmp_path / name).write_text(f"the user's own {name}")
    target = tmp_path / f"report.{kind}"
    monkeypatch.setattr(rt.QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    seen = _converter(monkeypatch, rt, tab, exe)
    getattr(tab, f"_export_{kind}")("# Title\n\nBody text.", "Title", "Author")
    assert seen, "the converter ran"
    assert target.read_bytes() == b"converted"
    for name in mine:
        assert (tmp_path / name).read_text() == f"the user's own {name}"
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(mine + [target.name])
    assert all(str(tmp_path) not in arg for cmd in seen for arg in cmd if arg != str(target)), seen


def test_a_converter_that_writes_nothing_is_reported_and_leaves_no_file(report_tab, monkeypatch, tmp_path):
    app, rt, tab = report_tab
    target = tmp_path / "report.docx"
    monkeypatch.setattr(rt.QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    monkeypatch.setattr(rt, "subprocess", types.SimpleNamespace(
        run=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "pandoc: unknown option"),
        TimeoutExpired=subprocess.TimeoutExpired))
    monkeypatch.setattr(tab, "_find_executable", lambda names: "pandoc" if "pandoc" in names else None)
    tab._export_docx("# T", "T", "A")
    assert not target.exists()
    assert tab._shown and tab._shown[-1][0] == "critical" and "unknown option" in tab._shown[-1][1][1]


def _wait(app, done, seconds=10.0):
    deadline = time.monotonic() + seconds
    while not done() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def test_expand_and_critique_write_off_the_window_thread(report_tab, monkeypatch):
    """Both ran a whole generation on the Qt thread: the window stopped repainting until the
    reply came back (a minute or more on the larger models)."""
    app, rt, tab = report_tab
    on_window_thread = []

    def infer(prompt, *, stage, temperature=0.34, top_p=0.92, min_chars=1):
        on_window_thread.append(threading.current_thread() is threading.main_thread())
        return f"[{stage} text]"

    monkeypatch.setattr(tab, "_rb_infer", infer)
    tab._editor.setPlainText("An opening paragraph.")
    cursor = tab._editor.textCursor()
    cursor.select(cursor.SelectionType.Document)
    tab._editor.setTextCursor(cursor)
    tab._ask_eli_expand_selection()
    assert not tab._expand_btn.isEnabled() and not tab._critique_btn.isEnabled()
    _wait(app, lambda: tab._manual_pending is None)
    assert tab._editor.toPlainText() == "[manual_expand text]"
    tab._ask_eli_critique()
    _wait(app, lambda: tab._manual_pending is None)
    assert tab._editor.toPlainText().endswith("## Peer-Review Critique\n\n[manual_critique text]\n")
    assert on_window_thread == [False, False]
    assert tab._expand_btn.isEnabled() and tab._critique_btn.isEnabled()


def test_a_failed_expand_is_said_and_the_buttons_come_back(report_tab, monkeypatch):
    app, rt, tab = report_tab

    def infer(prompt, **kw):
        raise RuntimeError("REPORT_BUILDER[manual_expand] returned 0 chars")

    monkeypatch.setattr(tab, "_rb_infer", infer)
    tab._editor.setPlainText("A paragraph.")
    cursor = tab._editor.textCursor()
    cursor.select(cursor.SelectionType.Document)
    tab._editor.setTextCursor(cursor)
    tab._ask_eli_expand_selection()
    _wait(app, lambda: tab._manual_pending is None)
    assert tab._editor.toPlainText() == "A paragraph."
    assert tab._shown[-1][0] == "warning" and "returned 0 chars" in tab._shown[-1][1][1]
    assert tab._expand_btn.isEnabled()

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
    # Report Builder was promoted from a Labs sub-tab to a top-level MAIN tab:
    # the _ReportTab class still lives in labs_tab.py, but the tab is now added by
    # the main window, not the Labs inner tabs.
    # EliMainWindow was split into mixins under eli/gui/main_window — read the package.
    main_gui = "\n".join(p.read_text(encoding="utf-8")
                         for p in sorted(Path("eli/gui/main_window").rglob("*.py")))
    assert "create_report_builder_tab" in main_gui
    assert "📄 Report Builder" in main_gui
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

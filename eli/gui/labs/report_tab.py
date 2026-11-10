"""ELI Labs — Report Builder tab (promoted to a main tab)

Extracted verbatim from the former eli/gui/labs_tab.py monolith (strangler
refactor, v3 core-modularisation). Shared preamble — Qt binding, plot paths,
physics tables — lives in eli.gui.labs._common.
"""
from __future__ import annotations
from eli.gui.labs._common import *  # noqa: F401,F403

from eli.gui.labs.bg_worker import _start_bg

# NOTE: still ~3.3k lines — next strangler target: split into
#   report/{ui,sources,prompts,outline,pdf_export}.py


class _ReportTab(QWidget):
    """Thesis-grade report workspace.

    The report builder is generation-first: document types define quality
    contracts, not fill-in-the-blank bodies. Static report skeletons are
    intentionally absent because they produce convincing-looking emptiness.
    Sources are passed to ELI as evidence so the persona-bound LLM grounds
    the draft in the user's actual data instead of inventing content.
    """

    _DOC_TYPES = (
        "Document",
        "Article",
        "Research Article",
        "Review Article",
        "Master's Thesis",
        "PhD Dissertation",
        "Peer-Review Paper",
        "Literature Review",
        "Research Proposal",
        "Lab Report",
        "Technical Report",
        "Simulation Report",
    )

    # Per-doc-type spec — drives the LLM prompts, NOT a static skeleton.
    # Each entry encodes audience / voice / structural conventions /
    # citation style / depth / failure-modes / success criterion. The
    # multi-stage pipeline reads from here for outline, per-section, and
    # polish prompts. A Master's Thesis prompt diverges sharply from a
    # Peer-Review Paper prompt at every stage because the spec diverges.
    _DOC_SPECS: Dict[str, Dict[str, str]] = {
        "Document": {
            "audience": "the intended reader named in the brief; default to a technically literate reader who needs a complete, usable document.",
            "voice": "clear, direct, structured prose; professional but not sterile; concrete explanations over ornamental wording.",
            "section_guidance": "Opening states purpose, scope, audience, and assumptions. Body develops the subject through coherent sections chosen for the actual topic, not a fixed skeleton. Include examples, definitions, caveats, and an evidence/assumption ledger where useful. Close with conclusions, next actions, or decision points.",
            "citations": "Use supplied source references when available; otherwise mark factual claims that need support with [source needed].",
            "depth": "adaptive: enough detail to be genuinely useful, never a placeholder or outline masquerading as a document.",
            "avoid": "blank sections, generic headings, filler introductions, fake references, and 'more research is needed' endings without specifics.",
            "success": "the reader can use the document immediately without asking for the missing body.",
        },
        "Article": {
            "audience": "educated public or domain-adjacent readers; assumes curiosity but not specialist mastery.",
            "voice": "engaging, precise, and readable; stronger narrative flow than a report, but still evidence-disciplined.",
            "section_guidance": "Lead with the central claim or tension. Explain context, develop the argument in a logical sequence, include concrete examples, address obvious counterpoints, and finish with a memorable but accurate conclusion.",
            "citations": "Inline links or source notes when supplied; use [source needed] for unsupported factual claims.",
            "depth": "medium to long-form; each section should advance the argument rather than decorate it.",
            "avoid": "clickbait, promotional tone, empty throat-clearing, generic summary paragraphs, and invented anecdotes.",
            "success": "reads like a finished article with a clear thesis, not notes for an article.",
        },
        "Research Article": {
            "audience": "research readers in or near the field; expects technical precision and methodological clarity.",
            "voice": "formal, compact, and evidence-first; careful with claims and explicit about limitations.",
            "section_guidance": "Abstract frames objective, method, result, and contribution. Introduction identifies gap and contribution. Methods are reproducible. Results distinguish observations from interpretation. Discussion compares to prior work, addresses limitations, and states implications. Conclusion is concise.",
            "citations": "Author-year or numbered citations only from supplied/user-named sources; do not fabricate bibliography metadata.",
            "depth": "journal-style depth; enough method and analysis to be reviewable.",
            "avoid": "marketing language, ungrounded novelty claims, missing methods, and conclusions stronger than the evidence.",
            "success": "a reviewer can identify the question, method, evidence, contribution, and limitations.",
        },
        "Review Article": {
            "audience": "field readers who want synthesis across sources rather than a single experiment.",
            "voice": "critical, comparative, and structured; not a list of summaries.",
            "section_guidance": "Define scope and inclusion criteria. Organize by themes, methods, debates, chronology, or mechanisms as appropriate. Compare sources directly, identify consensus and disagreement, expose gaps, and conclude with a research agenda.",
            "citations": "Every source-specific claim must map to supplied/user-named sources; missing sources are marked [source needed].",
            "depth": "broad and synthetic; enough comparison to be more than annotated bibliography.",
            "avoid": "one-paragraph-per-source dumps, fake literature, uncited consensus claims, and generic 'future work' lists.",
            "success": "the reader understands the state of the field, disagreements, and where work should go next.",
        },
        "Master's Thesis": {
            "audience": "thesis examiners and supervisor; expert in the field but not the candidate's specific contribution.",
            "voice": "formal academic register, impersonal or first-person plural, complete paragraphs, no contractions.",
            "section_guidance": "Introduction frames research questions distinctly from background. Lit Review identifies the research gap explicitly. Theoretical Framework defines every term used later. Methodology must be replicable from the text alone. Results: descriptive stats before inferential. Discussion interprets and limits honestly. Conclusion is short — no new analysis.",
            "citations": "Author–year (Harvard) or numbered references; full bibliography.",
            "depth": "long-form; each subsection a complete argument, not a placeholder.",
            "avoid": "executive summaries, bullet dumps, marketing voice, hedging like 'arguably'.",
            "success": "passes a viva: every claim defensible, every limitation acknowledged.",
        },
        "PhD Dissertation": {
            "audience": "doctoral committee and broader research community; deep disciplinary expertise.",
            "voice": "impersonal academic; long, carefully argued paragraphs; precise terminology; no contractions.",
            "section_guidance": "Introduction states the original contribution explicitly. Lit Review is critical, not summative. Theoretical Framework develops the candidate's own conceptual position. Methodology justifies choices against alternatives. Empirical chapters each stand as publishable units. Discussion synthesises across chapters. Conclusion frames contribution to knowledge.",
            "citations": "Author–year throughout; minimum 100+ unique sources; primary not secondary.",
            "depth": "very long; each chapter substantial and self-contained.",
            "avoid": "tutorial-style explanations of well-known concepts, padding, evasive language about negative results.",
            "success": "demonstrably original contribution; defensible per chapter independently.",
        },
        "Peer-Review Paper": {
            "audience": "anonymous peer reviewers and venue readers in the same subfield; high prior expertise.",
            "voice": "compact, declarative, third-person; minimum hedging; tight argument; double-blind safe.",
            "section_guidance": "Intro is dense: motivation, gap, contribution claim, roadmap in ~1 page. Related Work is critical and selective. Methodology reproducible at the level of equations / hyperparameters / datasets / code. Experiments include baselines, ablations, statistical significance. Results: every claim backed by a number in a table or figure that is referenced in text. Discussion short, Conclusion shorter. Reproducibility statement explicit.",
            "citations": "Numbered (IEEE/ACM/NeurIPS/ICML) or author–year per venue; ablation citations required.",
            "depth": "tight (~8–14 pages camera-ready equivalent); every paragraph earns its space.",
            "avoid": "hedging that suggests weak results, marketing tone, anecdotes, undefined symbols, undefended design choices.",
            "success": "could plausibly accept at the named venue.",
        },
        "Literature Review": {
            "audience": "academic supervisor, examiner, or research team looking for defensible synthesis of prior work.",
            "voice": "critical academic synthesis; selective, comparative, and explicit about relevance.",
            "section_guidance": "Start with scope, search/inclusion logic, and organizing lens. Group studies by theme or method. For each theme, compare findings, assumptions, methods, and limitations. End by identifying the research gap and how it motivates the user's project.",
            "citations": "Author-year preferred; never invent sources, titles, venues, years, page spans, or DOIs.",
            "depth": "thesis-grade; sources are compared and weighed, not merely summarized.",
            "avoid": "annotated bibliography structure unless explicitly requested, source padding, and unsupported 'the literature shows' claims.",
            "success": "makes the research gap unavoidable and defensible.",
        },
        "Research Proposal": {
            "audience": "supervisor, funder, ethics panel, or review committee deciding whether the work is worth doing.",
            "voice": "precise, persuasive, and methodologically sober; ambitious without overclaiming.",
            "section_guidance": "State problem, background, gap, objectives, research questions, proposed method, data/sources, risks, timeline, expected contribution, ethics/reproducibility, and evaluation criteria. Include assumptions and feasibility constraints.",
            "citations": "Use supplied/user-named sources; mark missing support with [source needed].",
            "depth": "complete enough for approval or serious critique.",
            "avoid": "vision-only proposals, vague deliverables, missing risks, and fake preliminary evidence.",
            "success": "a reviewer can judge novelty, feasibility, method, risk, and expected contribution.",
        },
        "Lab Report": {
            "audience": "course instructor or lab supervisor; assumes background in the discipline and the apparatus/protocol.",
            "voice": "first-person plural or passive; concrete and procedural; clean separation of action vs interpretation.",
            "section_guidance": "Abstract: hypothesis, method, key result, conclusion in 5–8 sentences. Introduction states hypothesis with rationale. Theory derives the predicted relationship from first principles; states expected error sources. Methodology: equipment with relevant precision; step-by-step procedure; deviations noted. Results: tables and labelled figures with units AND propagated uncertainties. Discussion interprets, compares to literature/expected, analyses error sources QUANTITATIVELY. Conclusion: hypothesis supported or not + dominant uncertainty source.",
            "citations": "Source/textbook style; cite the textbook for any constant or theoretical equation.",
            "depth": "moderate (~6–15 pages); every numerical claim has a unit and an uncertainty.",
            "avoid": "blaming 'human error' without quantification, dropping units, mixing results with interpretation.",
            "success": "the experiment can be repeated by another student from the report alone.",
        },
        "Technical Report": {
            "audience": "engineering management, project sponsors, technical peers; mixed depth — exec summary for managers, body for engineers.",
            "voice": "direct, declarative, action-oriented; bullet points where they help; explicit recommendations.",
            "section_guidance": "Executive Summary: problem, top 3 findings, top recommendations — readable in 90 seconds. Background: just enough domain context for a non-specialist. System Description with diagrams. Analysis structured by sub-question, each ending in an explicit finding. Findings numbered with evidence and impact rating. Recommendations numbered with effort/impact and ownership. Appendix has raw data.",
            "citations": "Internal artefacts (commit hashes, ticket IDs, dashboards), external standards (ISO, RFC, IEEE). Academic citations sparing.",
            "depth": "pragmatic (~10–25 pages); exec summary always ≤1 page.",
            "avoid": "literature review, theoretical motivation beyond what affects the recommendation, passive avoidance of action.",
            "success": "manager decides from exec summary; engineer acts from recommendations.",
        },
        "Simulation Report": {
            "audience": "computational scientists or engineers who will rerun, extend, or rely on the simulation results.",
            "voice": "precise and reproducible; technical; figures and convergence plots are first-class evidence.",
            "section_guidance": "Overview: what was simulated, why, headline quantitative result. Domain: geometry/space, dimensionality, boundary types/locations. Parameters: every dimensional parameter with units; non-dimensional groups (Re, Pe, etc) where applicable. Solver: name + version, discretisation, time-step, mesh resolution, convergence criteria. Convergence: temporal and spatial evidence (residuals, refinement studies). Field Distributions with colour-bar units. Validation against analytical limits / experimental data / independent code, quantified.",
            "citations": "Solver/library DOIs; physical model papers; benchmark data sources; mesh generator references.",
            "depth": "moderate to long (~12–30 pages); every numerical result has a convergence claim.",
            "avoid": "single-resolution claims, missing units, decorative colour plots, validation by visual similarity alone.",
            "success": "a peer can reproduce the simulation from the text and trust the validation envelope.",
        },
    }

    # Per-grade hint — depth/audience modifier on top of doc-type spec.
    _GRADE_HINTS: Dict[str, str] = {
        "Master's grade (rigorous, full citations)":
            "Master's-level rigour: complete citations, defensible methodology, explicit limitations.",
        "PhD-grade (extended, peer-review ready)":
            "PhD-level extended depth: original contribution explicit, comprehensive lit engagement, publishable subsections.",
        "Conference paper (concise, double-blind)":
            "Conference concision: page-budget discipline, double-blind safe, tight argument.",
        "Journal article (long-form, citation-heavy)":
            "Journal long-form: deeper related work, full method reproducibility, comprehensive validation.",
        "Lab report (structured, reproducible)":
            "Lab-report structure: hypothesis explicit, replicable procedure, results separated from interpretation, errors quantified.",
    }

    # User-selectable quality contracts. These are deliberately phrased as
    # reviewable acceptance criteria, not vague style nudges.
    _QUALITY_PROFILES: Dict[str, str] = {
        "Evidence-grounded professional":
            "Professional standard: every factual claim is tied to supplied evidence or marked [source needed]; the document is coherent, complete, and export-clean.",
        "Publication-grade":
            "Publication standard: novelty/gap/contribution are explicit; methods are reproducible; results are interpreted with limitations; unsupported claims are not allowed.",
        "Thesis / viva defense":
            "Defense standard: definitions, assumptions, limitations, and examiner objections are anticipated; argument flow is complete enough for oral defense.",
        "Technical audit grade":
            "Audit standard: findings are traceable to evidence, risks are ranked, recommendations include impact/effort/owner, and uncertainty is explicit.",
    }

    _CITATION_POLICIES: Dict[str, str] = {
        "Strict: mark missing sources":
            "Do not invent citations, references, datasets, page numbers, equations, or statistics. If a claim needs a source and none is present, write [source needed].",
        "Author-year placeholders":
            "Use author-year style where evidence names an author/date. If metadata is incomplete, use [source needed] rather than fabricating bibliographic details.",
        "Numbered references":
            "Use numbered references only for supplied or user-named sources. Never create fake titles, venues, DOIs, URLs, or page spans.",
        "Internal evidence ledger":
            "Prefer source-file references and an Evidence Ledger table showing which attached source supports each major claim.",
    }

    _DEPTH_PROFILES: Dict[str, str] = {
        "Balanced":
            "Balanced depth: complete major sections, concise subsections, no padding.",
        "Extended":
            "Extended depth: develop each section as a full argument with definitions, method detail, and caveats.",
        "Maximal / examiner-ready":
            "Maximal depth: include assumptions, alternatives, limitations, validation checks, and expected objections.",
        "Executive concise":
            "Executive concise: preserve rigor but compress background; prioritize findings, decisions, and next actions.",
    }

    # Per-target-format spec — tells the LLM how to write its output so
    # the export pipeline (pandoc / lualatex / direct .md) gets clean
    # input. Appended to every stage prompt so even per-section text is
    # format-correct, not stitched-together generic markdown.
    _FORMAT_SPECS: Dict[str, Dict[str, str]] = {
        "Markdown (general)": {
            "syntax": "Plain GitHub-flavoured markdown. ATX headings. Fenced code blocks with language tags. Pipe tables. Math with $...$ inline / $$...$$ display. No raw HTML.",
            "citations": "Plain references in `## References` at the end. Do not invent BibTeX keys.",
            "figures": "`![caption](relative/path.png)`.",
            "escapes": "No special escaping. Avoid raw HTML.",
        },
        "LuaLaTeX (.tex)": {
            "syntax": "Emit LATEX SOURCE, not markdown. \\chapter{} for thesis-level, \\section{}/\\subsection{} otherwise. Math \\(...\\) inline, \\[...\\] display. Code in lstlisting. Tables in tabular within table environments. Do NOT include \\documentclass or \\begin{document} — the export wrapper adds those.",
            "citations": "Use \\cite{authoryear} keys. End with \\bibliography{refs}. Do NOT fabricate .bib entries — emit only cite keys, the .bib is supplied separately.",
            "figures": "\\begin{figure}[ht]\\centering\\includegraphics[width=0.8\\textwidth]{path}\\caption{...}\\label{fig:...}\\end{figure}",
            "escapes": "Escape LaTeX specials in prose: \\, %, $, &, #, _, {, }, ^, ~. Use \\textbackslash{}, \\$, \\&, \\#, \\_, \\{, \\}, \\^{}, \\~{}.",
        },
        "Pandoc-PDF (markdown → pandoc → lualatex)": {
            "syntax": "Markdown that pandoc compiles to LaTeX. ATX headings with {#sec:label} attributes for cross-reference. Pipe tables with explicit column alignment. Math $...$ / $$...$$.",
            "citations": "Pandoc syntax [@key]. Either provide top YAML `references:` or assume external .bib at compile time.",
            "figures": "`![caption](path){#fig:label width=80%}` so pandoc emits proper figure refs.",
            "escapes": "Escape pipe inside table cells with \\|; escape $ with \\$ if literal.",
        },
        "DOCX (markdown → pandoc)": {
            "syntax": "Markdown that pandoc compiles to DOCX. ATX headings (Word maps to Heading 1/2/...). Native pipe tables. Inline math is preserved as MathML.",
            "citations": "Pandoc syntax [@key]; pandoc resolves at compile time.",
            "figures": "`![caption](path)` — Word reads the embedded image.",
            "escapes": "Avoid raw HTML; pandoc DOCX writer ignores most of it.",
        },
        "HTML (markdown → html)": {
            "syntax": "GitHub-flavoured markdown to clean HTML. Code blocks with language tags. Pipe tables. Math via $$...$$ if a math renderer is in scope.",
            "citations": "Plain hyperlinks; numbered references at end.",
            "figures": "`![caption](path)`; relative paths only.",
            "escapes": "Escape <, >, & if literal in prose.",
        },
    }

    _SUPPORTED_EXTS = {
        ".md", ".txt", ".rst", ".tex", ".bib",
        ".csv", ".tsv",
        ".json", ".jsonl", ".yaml", ".yml", ".toml",
        ".pdf",
        ".py", ".ipynb",
        ".html",
    }

    _MAX_PREVIEW_BYTES = 200_000  # cap per file fed to ELI as evidence
    _MAX_TOTAL_EVIDENCE = 600_000
    _status_sig = pyqtSignal(str)
    _editor_sig = pyqtSignal(str)

    def __init__(self, eli_callback=None, parent=None):
        super().__init__(parent)
        self._eli = eli_callback
        self._sources: List[Dict[str, Any]] = []  # {path, kind, bytes, preview}
        self._draft_running = False
        self._manual_workers: List[Any] = []
        self._manual_pending: Optional[Dict[str, Any]] = None   # the Expand/Critique in progress
        self._build_ui()
        self._status_sig.connect(self._status.setText)
        self._editor_sig.connect(self._editor.setPlainText)

    # ── UI ────────────────────────────────────────────────────────────────
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)

        profile_group = QGroupBox("Report Builder: document generation profile")
        profile_row = QHBoxLayout(profile_group)
        profile_row.addWidget(QLabel("Document type:"))
        self._template_combo = QComboBox()
        self._template_combo.addItems(list(self._DOC_TYPES))
        self._template_combo.setMinimumWidth(260)
        self._template_combo.setToolTip(
            "Selects the generation contract. Thesis, article, document, "
            "proposal, and report profiles use different structure, voice, "
            "depth, and citation rules."
        )
        profile_row.addWidget(self._template_combo)
        profile_hint = QLabel(
            "Profile changes the drafting prompt, section plan, formatting, "
            "depth, and evidence rules."
        )
        profile_hint.setStyleSheet("color:#8eaac8;")
        profile_row.addWidget(profile_hint, stretch=1)
        outer.addWidget(profile_group)

        splitter_main = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter_main, stretch=1)

        # ── Left: configuration + sources ────────────────────────────────
        left = QWidget()
        lv = QVBoxLayout(left)

        brief_group = QGroupBox("1. Report brief")
        brief_layout = QVBoxLayout(brief_group)
        form = QFormLayout()
        self._title = QLineEdit()
        self._title.setPlaceholderText("Report title")
        form.addRow("Title:", self._title)
        self._author = QLineEdit()
        self._author.setPlaceholderText("Author / candidate name")
        form.addRow("Author:", self._author)
        self._grade_combo = QComboBox()
        self._grade_combo.addItems([
            "Master's grade (rigorous, full citations)",
            "PhD-grade (extended, peer-review ready)",
            "Conference paper (concise, double-blind)",
            "Journal article (long-form, citation-heavy)",
            "Lab report (structured, reproducible)",
        ])
        form.addRow("Grade:", self._grade_combo)
        # Target format: tells the draft pipeline what output style to
        # produce so the export step (pandoc/lualatex/etc) gets clean
        # input. "LuaLaTeX" → emit LaTeX-safe markdown with chapter
        # syntax, \cite{} keys, escaped specials. "Pandoc-PDF" → mostly
        # markdown but with semantic YAML metadata. etc.
        self._target_format_combo = QComboBox()
        self._target_format_combo.addItems([
            "Markdown (general)",
            "LuaLaTeX (.tex)",
            "Pandoc-PDF (markdown → pandoc → lualatex)",
            "DOCX (markdown → pandoc)",
            "HTML (markdown → html)",
        ])
        self._target_format_combo.setToolTip(
            "Tells the draft pipeline what format the export will use, "
            "so the LLM produces output that compiles cleanly."
        )
        form.addRow("Target format:", self._target_format_combo)
        self._quality_combo = QComboBox()
        self._quality_combo.addItems(list(self._QUALITY_PROFILES))
        self._quality_combo.setToolTip(
            "Controls the document acceptance bar applied to every prompt stage."
        )
        form.addRow("Quality bar:", self._quality_combo)
        self._citation_policy_combo = QComboBox()
        self._citation_policy_combo.addItems(list(self._CITATION_POLICIES))
        self._citation_policy_combo.setToolTip(
            "Controls how missing citations and unsupported claims are handled."
        )
        form.addRow("Citation policy:", self._citation_policy_combo)
        self._depth_combo = QComboBox()
        self._depth_combo.addItems(list(self._DEPTH_PROFILES))
        self._depth_combo.setCurrentText("Extended")
        self._depth_combo.setToolTip("Controls how much argument/detail ELI should produce.")
        form.addRow("Depth:", self._depth_combo)
        self._discipline = QLineEdit()
        self._discipline.setPlaceholderText("e.g. Physics, ML, Bioinformatics")
        form.addRow("Discipline:", self._discipline)
        brief_layout.addLayout(form)

        brief_layout.addWidget(QLabel("Abstract / brief:"))
        self._abstract = QTextEdit()
        self._abstract.setMaximumHeight(90)
        brief_layout.addWidget(self._abstract)
        lv.addWidget(brief_group)

        # Sources card
        srcs_group = QGroupBox("2. Evidence and source materials")
        sg = QVBoxLayout(srcs_group)

        btns = QHBoxLayout()
        add_files_btn = QPushButton("Add files…")
        add_files_btn.clicked.connect(self._add_files)
        btns.addWidget(add_files_btn)
        add_dir_btn = QPushButton("Add folder…")
        add_dir_btn.clicked.connect(self._add_directory)
        btns.addWidget(add_dir_btn)
        add_proj_btn = QPushButton("Add project tree")
        add_proj_btn.setToolTip("Recursively pull supported files from a project")
        add_proj_btn.clicked.connect(self._add_project)
        btns.addWidget(add_proj_btn)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._clear_sources)
        btns.addWidget(clear_btn)
        sg.addLayout(btns)

        self._sources_list = QListWidget()
        self._sources_list.itemSelectionChanged.connect(self._on_source_selected)
        sg.addWidget(self._sources_list, stretch=1)

        self._source_preview = QPlainTextEdit()
        self._source_preview.setReadOnly(True)
        self._source_preview.setPlaceholderText("Select a source to preview its analysed content")
        self._source_preview.setMaximumHeight(180)
        sg.addWidget(self._source_preview)

        lv.addWidget(srcs_group, stretch=1)

        # Action buttons
        action_group = QGroupBox("3. Draft, review, export")
        ag = QVBoxLayout(action_group)

        option_row = QHBoxLayout()
        self._auto_review_check = QCheckBox("Run internal review pass")
        self._auto_review_check.setChecked(True)
        self._auto_review_check.setToolTip(
            "After polishing, ask ELI for a critique and a final revision pass."
        )
        option_row.addWidget(self._auto_review_check)
        self._autosave_check = QCheckBox("Auto-save finished draft")
        self._autosave_check.setChecked(True)
        self._autosave_check.setToolTip(
            "Save the final generated document into artifacts/documents automatically."
        )
        option_row.addWidget(self._autosave_check)
        option_row.addStretch(1)
        ag.addLayout(option_row)

        action_row1 = QHBoxLayout()
        if self._eli:
            full_btn = QPushButton("Draft Full Report")
            full_btn.setStyleSheet("background:#5a3aa8;color:white;font-weight:bold;padding:8px;")
            full_btn.setToolTip(
                "Run outline, section drafting, polish, optional review, and autosave."
            )
            full_btn.clicked.connect(self._draft_full_with_eli)
            action_row1.addWidget(full_btn)
        else:
            no_eli = QLabel("ELI generation callback unavailable.")
            no_eli.setStyleSheet("color:#a88; padding:6px;")
            action_row1.addWidget(no_eli)
        ag.addLayout(action_row1)

        action_row2 = QHBoxLayout()
        if self._eli:
            self._expand_btn = QPushButton("Expand Selected Section")
            self._expand_btn.clicked.connect(self._ask_eli_expand_selection)
            action_row2.addWidget(self._expand_btn)

            self._critique_btn = QPushButton("Peer-Review Critique")
            self._critique_btn.setToolTip("Ask ELI to critique the current draft as a peer reviewer")
            self._critique_btn.clicked.connect(self._ask_eli_critique)
            action_row2.addWidget(self._critique_btn)
        ag.addLayout(action_row2)

        action_row3 = QHBoxLayout()
        for label, kind in (
            ("Markdown", "md"),
            ("Quarkdown", "qmd"),
            ("HTML", "html"),
            ("LuaLaTeX", "tex"),
            ("PDF (article)", "pdf"),
            ("DOCX", "docx"),
        ):
            b = QPushButton(label)
            b.clicked.connect(lambda _checked=False, k=kind: self._export(k))
            action_row3.addWidget(b)
        ag.addLayout(action_row3)
        lv.addWidget(action_group)

        splitter_main.addWidget(left)

        # ── Right: editor + live prompt preview + status ─────────────────
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)

        right_tabs = QTabWidget()
        right_tabs.setDocumentMode(True)

        # Tab 1: Document editor
        self._editor = QTextEdit()
        self._editor.setFont(QFont("Courier New", 10))
        self._editor.setPlaceholderText(
            "Generated report will appear here.\n\n"
            "Workflow:\n"
            "  1. Set the brief, quality bar, citation policy, depth, and target format.\n"
            "  2. Add evidence sources (CSV, PDF, markdown, code, notebooks, projects).\n"
            "  3. Click 'Draft Full Report' for outline → sections → polish → review.\n"
            "  4. Use 'Expand Selected Section' or 'Peer-Review Critique' for refinement.\n"
            "  5. Export or use the auto-saved artifact in artifacts/documents.\n"
        )
        right_tabs.addTab(self._editor, "📄 Document")

        # Tab 2: Generation plan. Shows a readable plan by default, with
        # raw prompt inspection available only through the debug view.
        prompt_widget = QWidget()
        pv = QVBoxLayout(prompt_widget)
        pv.setContentsMargins(4, 4, 4, 4)

        preview_row = QHBoxLayout()
        preview_row.addWidget(QLabel("Action:"))
        self._preview_mode = QComboBox()
        self._preview_mode.addItems([
            "Draft Full Report",
            "Expand Selection",
            "Peer-Review Critique",
        ])
        self._preview_mode.currentIndexChanged.connect(
            lambda *_: self._refresh_prompt_preview()
        )
        preview_row.addWidget(self._preview_mode)

        preview_row.addWidget(QLabel("View:"))
        self._preview_detail_combo = QComboBox()
        self._preview_detail_combo.addItems([
            "Plan Summary",
            "Raw Debug Prompt",
        ])
        self._preview_detail_combo.setToolTip(
            "Plan Summary is the normal Report Builder view. Raw Debug Prompt "
            "shows the exact internal prompt for debugging generation behavior."
        )
        self._preview_detail_combo.currentIndexChanged.connect(
            lambda *_: self._refresh_prompt_preview()
        )
        preview_row.addWidget(self._preview_detail_combo)

        copy_btn = QPushButton("Copy")
        copy_btn.setToolTip("Copy the current preview text")
        copy_btn.clicked.connect(self._copy_preview_prompt)
        preview_row.addWidget(copy_btn)

        refresh_btn = QPushButton("↻ Refresh")
        refresh_btn.clicked.connect(self._refresh_prompt_preview)
        preview_row.addWidget(refresh_btn)
        preview_row.addStretch(1)
        pv.addLayout(preview_row)

        self._prompt_preview = QPlainTextEdit()
        self._prompt_preview.setReadOnly(True)
        self._prompt_preview.setFont(QFont("Courier New", 9))
        self._prompt_preview.setPlaceholderText(
            "Readable generation plan. Switch View to Raw Debug Prompt only "
            "when inspecting the exact internal prompt."
        )
        pv.addWidget(self._prompt_preview, stretch=1)
        right_tabs.addTab(prompt_widget, "Generation Plan")

        rv.addWidget(right_tabs, stretch=1)

        self._status = QLabel("Ready.")
        self._status.setStyleSheet("color:#8eaac8; padding:4px 6px;")
        rv.addWidget(self._status)

        splitter_main.addWidget(right)
        splitter_main.setSizes([360, 760])

        # Wire setting changes to refresh the prompt preview.
        for w in (self._title, self._author, self._discipline):
            try:
                w.textChanged.connect(self._refresh_prompt_preview)
            except Exception:
                pass
        for combo in (
            self._template_combo,
            self._grade_combo,
            self._target_format_combo,
            self._quality_combo,
            self._citation_policy_combo,
            self._depth_combo,
        ):
            try:
                combo.currentIndexChanged.connect(self._refresh_prompt_preview)
            except Exception:
                pass
        for check in (self._auto_review_check, self._autosave_check):
            try:
                check.stateChanged.connect(self._refresh_prompt_preview)
            except Exception:
                pass
        try:
            self._abstract.textChanged.connect(self._refresh_prompt_preview)
        except Exception:
            pass
        try:
            self._editor.cursorPositionChanged.connect(self._refresh_prompt_preview)
        except Exception:
            pass

        # First paint.
        self._refresh_prompt_preview()

    # ── Source management ────────────────────────────────────────────────
    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Add source files", "",
            "Supported (*.md *.txt *.rst *.tex *.bib *.csv *.tsv *.json *.jsonl *.yaml *.yml *.toml *.pdf *.py *.ipynb *.html);;All Files (*)"
        )
        for p in paths or []:
            self._ingest_path(Path(p))
        self._refresh_sources_list()

    def _add_directory(self):
        d = QFileDialog.getExistingDirectory(self, "Add source folder")
        if d:
            self._ingest_directory(Path(d), recursive=False)
            self._refresh_sources_list()

    def _add_project(self):
        d = QFileDialog.getExistingDirectory(self, "Add project tree")
        if d:
            self._ingest_directory(Path(d), recursive=True)
            self._refresh_sources_list()

    def _clear_sources(self):
        self._sources.clear()
        self._sources_list.clear()
        self._source_preview.clear()
        self._status.setText("Sources cleared.")

    def _ingest_directory(self, d: Path, recursive: bool):
        if not d.exists():
            return
        glob = "**/*" if recursive else "*"
        count = 0
        for p in d.glob(glob):
            if not p.is_file():
                continue
            if p.suffix.lower() not in self._SUPPORTED_EXTS:
                continue
            # skip noisy / large directories
            parts = {x.lower() for x in p.parts}
            if parts & {".git", ".venv", "node_modules", "__pycache__",
                        "dist", "build", ".pytest_cache", ".mypy_cache"}:
                continue
            self._ingest_path(p)
            count += 1
        self._status.setText(f"Ingested {count} files from {d.name}.")

    def _ingest_path(self, p: Path):
        if not p.exists() or not p.is_file():
            return
        suffix = p.suffix.lower()
        try:
            if suffix == ".pdf":
                preview = self._read_pdf(p)
                kind = "pdf"
            elif suffix in {".csv", ".tsv"}:
                preview = self._read_table(p)
                kind = "table"
            elif suffix == ".ipynb":
                preview = self._read_notebook(p)
                kind = "notebook"
            else:
                preview = p.read_text(encoding="utf-8", errors="replace")
                kind = "text"
            if len(preview) > self._MAX_PREVIEW_BYTES:
                preview = preview[: self._MAX_PREVIEW_BYTES] + "\n…(truncated)…"
            self._sources.append({
                "path": str(p),
                "name": p.name,
                "kind": kind,
                "bytes": p.stat().st_size,
                "preview": preview,
            })
        except Exception as ex:
            self._sources.append({
                "path": str(p), "name": p.name, "kind": "error",
                "bytes": 0, "preview": f"[Could not read: {ex}]",
            })

    def _read_pdf(self, p: Path) -> str:
        try:
            import pdfplumber
            with pdfplumber.open(str(p)) as pdf:
                pages = []
                for i, page in enumerate(pdf.pages[:40]):
                    try:
                        txt = page.extract_text() or ""
                    except Exception:
                        txt = ""
                    pages.append(f"--- Page {i+1} ---\n{txt}")
                return "\n\n".join(pages)
        except Exception:
            try:
                from pypdf import PdfReader
                r = PdfReader(str(p))
                return "\n\n".join(
                    f"--- Page {i+1} ---\n{(pg.extract_text() or '')}"
                    for i, pg in enumerate(r.pages[:40])
                )
            except Exception as ex:
                return f"[PDF extraction failed: {ex}]"

    def _read_table(self, p: Path) -> str:
        try:
            import csv
            sep = "\t" if p.suffix.lower() == ".tsv" else ","
            with p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
                reader = csv.reader(fh, delimiter=sep)
                rows = list(reader)
            if not rows:
                return "[empty table]"
            header = rows[0]
            sample = rows[1:21]
            stats_lines = [
                f"Columns ({len(header)}): {', '.join(header)}",
                f"Total rows: {len(rows) - 1}",
                "",
                "First 20 rows:",
            ]
            for r in sample:
                stats_lines.append(" | ".join(str(c) for c in r))
            # Numeric column summary if pandas is present
            try:
                import pandas as pd
                df = pd.read_csv(p, sep=sep)
                num = df.select_dtypes(include="number")
                if not num.empty:
                    stats_lines.append("")
                    stats_lines.append("Numeric summary:")
                    stats_lines.append(num.describe().round(4).to_string())
            except Exception:
                pass
            return "\n".join(stats_lines)
        except Exception as ex:
            return f"[Table parse failed: {ex}]"

    def _read_notebook(self, p: Path) -> str:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            cells = data.get("cells", [])
            out: List[str] = []
            for i, c in enumerate(cells[:80]):
                ct = c.get("cell_type", "?")
                src = "".join(c.get("source", []))
                out.append(f"--- Cell {i+1} [{ct}] ---\n{src}")
            return "\n\n".join(out)
        except Exception as ex:
            return f"[Notebook parse failed: {ex}]"

    def _refresh_sources_list(self):
        self._sources_list.clear()
        total_bytes = 0
        for s in self._sources:
            kb = s.get("bytes", 0) / 1024.0
            total_bytes += s.get("bytes", 0)
            item = QListWidgetItem(f"[{s['kind']}] {s['name']}  ({kb:.1f} KB)")
            item.setToolTip(s["path"])
            self._sources_list.addItem(item)
        self._status.setText(
            f"{len(self._sources)} source(s) loaded — {total_bytes/1024:.1f} KB total."
        )
        try:
            self._refresh_prompt_preview()
        except Exception:
            pass

    def _on_source_selected(self):
        idx = self._sources_list.currentRow()
        if 0 <= idx < len(self._sources):
            self._source_preview.setPlainText(self._sources[idx].get("preview", ""))

    # ── Generation actions ───────────────────────────────────────────────
    def _sources_inventory_md(self) -> str:
        if not self._sources:
            return "_No source data attached._"
        lines = ["| File | Kind | Size (KB) | Path |", "| --- | --- | --- | --- |"]
        for s in self._sources:
            kb = s.get("bytes", 0) / 1024.0
            lines.append(f"| {s['name']} | {s['kind']} | {kb:.1f} | `{s['path']}` |")
        return "\n".join(lines)

    def _reports_output_dir(self) -> Path:
        artifacts = os.environ.get("ELI_ARTIFACTS_DIR")
        if artifacts:
            base = Path(artifacts)
        else:
            base = Path(__file__).resolve().parents[2] / "artifacts"
        return base / "documents"

    @staticmethod
    def _slugify_filename(text: str, default: str = "eli_report") -> str:
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", (text or "").strip()).strip("._-")
        return (slug or default)[:96]

    def _autosave_report(self, text: str, *, title: Optional[str] = None,
                         target_format: Optional[str] = None) -> Optional[Path]:
        if not text.strip():
            return None
        out_dir = self._reports_output_dir()
        out_dir.mkdir(parents=True, exist_ok=True)
        target_format = target_format or self._target_format_combo.currentText()
        ext = ".tex" if "LuaLaTeX" in target_format else ".html" if "HTML" in target_format else ".md"
        title = self._slugify_filename(title or self._title.text() or "eli_report")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = out_dir / f"{title}_{stamp}{ext}"
        path.write_text(text, encoding="utf-8")
        return path

    def _build_evidence_block(self) -> str:
        if not self._sources:
            return ""
        chunks: List[str] = ["=== SOURCE EVIDENCE (use as primary grounding) ==="]
        budget = self._MAX_TOTAL_EVIDENCE
        for s in self._sources:
            header = f"\n--- {s['name']} ({s['kind']}, {s.get('bytes',0)/1024:.1f} KB) ---\n"
            preview = s.get("preview", "")
            piece = header + preview
            if len(piece) > budget:
                piece = piece[: max(0, budget - 64)] + "\n…(truncated)…"
                chunks.append(piece)
                break
            chunks.append(piece)
            budget -= len(piece)
        return "\n".join(chunks)

    # ── Prompt builders ────────────────────────────────────────────────
    #
    # The "Draft Full Report" pipeline is multi-stage:
    #   1. Outline   → produces a sectioned table of contents
    #   2. Sections  → iterates the outline, generates each section
    #                  with its full ctx budget, target format spec,
    #                  and doc-type voice/structure rules
    #   3. Polish    → format-clean pass over the assembled draft
    #
    # Single-shot drafting is gone. A Master's Thesis was being
    # truncated to ~512 tokens before; per-section generation gives each
    # section the full output budget the model can produce.

    def _doc_spec(self, doc_type: str) -> Dict[str, str]:
        return self._DOC_SPECS.get(doc_type, self._DOC_SPECS["Peer-Review Paper"])

    def _format_spec(self, target_format: str) -> Dict[str, str]:
        return self._FORMAT_SPECS.get(target_format, self._FORMAT_SPECS["Markdown (general)"])

    def _doc_spec_block(self, spec: Dict[str, str]) -> str:
        return (
            f"AUDIENCE: {spec['audience']}\n"
            f"VOICE / REGISTER: {spec['voice']}\n"
            f"STRUCTURAL CONVENTIONS: {spec['section_guidance']}\n"
            f"CITATION STYLE: {spec['citations']}\n"
            f"DEPTH TARGET: {spec['depth']}\n"
            f"AVOID: {spec['avoid']}\n"
            f"SUCCESS CRITERION: {spec['success']}"
        )

    def _format_spec_block(self, fmt: Dict[str, str], target_format: str) -> str:
        return (
            f"OUTPUT FORMAT: {target_format}\n"
            f"  SYNTAX: {fmt['syntax']}\n"
            f"  CITATIONS: {fmt['citations']}\n"
            f"  FIGURES: {fmt['figures']}\n"
            f"  ESCAPES: {fmt['escapes']}"
        )

    def _quality_spec_block(self) -> str:
        quality_name = self._quality_combo.currentText() if hasattr(self, "_quality_combo") else "Evidence-grounded professional"
        citation_name = self._citation_policy_combo.currentText() if hasattr(self, "_citation_policy_combo") else "Strict: mark missing sources"
        depth_name = self._depth_combo.currentText() if hasattr(self, "_depth_combo") else "Extended"
        quality = self._QUALITY_PROFILES.get(quality_name, self._QUALITY_PROFILES["Evidence-grounded professional"])
        citation = self._CITATION_POLICIES.get(citation_name, self._CITATION_POLICIES["Strict: mark missing sources"])
        depth = self._DEPTH_PROFILES.get(depth_name, self._DEPTH_PROFILES["Extended"])
        return (
            "QUALITY BAR:\n"
            f"  Selected standard: {quality_name}\n"
            f"  {quality}\n"
            f"  Depth profile: {depth_name} — {depth}\n"
            "EVIDENCE DISCIPLINE:\n"
            f"  Citation policy: {citation_name}\n"
            f"  {citation}\n"
            "  Treat SOURCE EVIDENCE as the primary grounding record. Never imply that missing evidence was inspected.\n"
            "  Use [source needed] for unsupported factual claims and [assumption] for necessary modelling assumptions.\n"
            "  Include uncertainty, limitations, and verification steps where the document type warrants them."
        )

    def _acceptance_test_block(self) -> str:
        return (
            "FINAL ACCEPTANCE TEST BEFORE OUTPUT:\n"
            "  1. The document has a clear thesis/problem statement, method, evidence-backed findings, limitations, and conclusion.\n"
            "  2. Each non-obvious factual claim is supported by supplied evidence, explicitly marked [source needed], or labelled [assumption].\n"
            "  3. Tables, figures, equations, and references are introduced in prose before use and have units/labels where relevant.\n"
            "  4. The structure matches the selected document type and the output syntax matches the target export format.\n"
            "  5. The final text contains no meta-commentary about being generated, no fake file paths, no fake citations, and no placeholder promises."
        )

    def _build_outline_prompt(self) -> str:
        """Stage 1: Ask the LLM for a structured outline matching the
        doc-type's structural conventions and the user's brief."""
        title = self._title.text().strip() or "Untitled"
        doc_type = self._template_combo.currentText()
        grade = self._grade_combo.currentText()
        grade_hint = self._GRADE_HINTS.get(grade, "")
        target_format = self._target_format_combo.currentText()
        discipline = self._discipline.text().strip() or "general academic"
        abstract = self._abstract.toPlainText().strip() or "(no brief provided)"
        spec = self._doc_spec(doc_type)
        fmt = self._format_spec(target_format)
        evidence = self._build_evidence_block()
        return "\n".join(p for p in [
            f"TASK: Produce a STRUCTURED OUTLINE for a {doc_type} in {discipline}.",
            f"GRADE MODIFIER: {grade_hint}" if grade_hint else "",
            "",
            "DOC-TYPE SPECIFICATION (these conventions govern the section list):",
            self._doc_spec_block(spec),
            "",
            self._format_spec_block(fmt, target_format),
            "",
            self._quality_spec_block(),
            "",
            f"TITLE: {title}",
            f"AUTHOR BRIEF:\n{abstract}",
            "",
            "INSTRUCTIONS:",
            "1. Output ONLY the outline. Do NOT write the body yet.",
            "2. List each top-level section, then its subsections (one level deep).",
            f"3. The structure must match the conventions of a {doc_type} exactly. "
            f"Do not invent sections that do not belong in this doc type.",
            "4. For each section, add ONE sentence: what that section will contain "
            "given the supplied evidence (or '[source needed]' if nothing in evidence supports it).",
            "5. Include an Evidence Ledger / Source Coverage Matrix section when sources are attached.",
            "6. Do not produce body paragraphs. The outline is the deliverable.",
            "",
            self._acceptance_test_block(),
            "",
            evidence,
        ] if p)

    def _build_section_prompt(self, doc_type: str, section_title: str,
                              section_intent: str, accumulated_so_far: str) -> str:
        """Stage 2: Generate a single section in full, given the prior
        sections (for coherence) and this section's outline intent."""
        grade = self._grade_combo.currentText()
        grade_hint = self._GRADE_HINTS.get(grade, "")
        target_format = self._target_format_combo.currentText()
        spec = self._doc_spec(doc_type)
        fmt = self._format_spec(target_format)
        evidence = self._build_evidence_block()
        title = self._title.text().strip() or "Untitled"
        # Prior-sections snippet — last 6 000 chars so the LLM keeps continuity
        # without re-spending the whole ctx on what came before.
        prior_tail = (accumulated_so_far or "")[-6000:]
        return "\n".join(p for p in [
            f"TASK: Write the section titled '{section_title}' of a {doc_type}.",
            f"GRADE MODIFIER: {grade_hint}" if grade_hint else "",
            "",
            "DOC-TYPE SPECIFICATION (governs voice, structure, depth — adhere strictly):",
            self._doc_spec_block(spec),
            "",
            self._format_spec_block(fmt, target_format),
            "",
            self._quality_spec_block(),
            "",
            f"DOCUMENT TITLE: {title}",
            f"SECTION INTENT (from the outline): {section_intent}",
            "",
            "INSTRUCTIONS:",
            f"1. Write this section in the voice and depth of a {doc_type}.",
            "2. Do NOT restart the document — assume prior sections exist (provided below as PRIOR_TAIL).",
            "3. Do NOT include sections other than this one. Output starts at this section's heading.",
            "4. Cite supplied sources where you rely on them, in the exact citation style of OUTPUT FORMAT.",
            "5. Mark anything you cannot ground in the evidence as [source needed].",
            f"6. Respect the AVOID list — those are failure modes specific to {doc_type}.",
            "7. When numbers/equations/units are relevant, show calculation assumptions and units explicitly.",
            "",
            self._acceptance_test_block(),
            "",
            "PRIOR_TAIL (for coherence, do not repeat):",
            prior_tail or "(no prior content yet — this is the opening section)",
            "",
            evidence,
        ] if p)

    def _build_polish_prompt(self, full_draft: str) -> str:
        """Stage 3: Single format-targeted polish pass over the
        assembled draft. Output target is the FINAL DOCUMENT — clean
        for the chosen export pipeline."""
        doc_type = self._template_combo.currentText()
        target_format = self._target_format_combo.currentText()
        spec = self._doc_spec(doc_type)
        fmt = self._format_spec(target_format)
        return "\n".join([
            f"TASK: Polish the following assembled draft so it is publication-ready in {target_format}.",
            "",
            "DOC-TYPE SPECIFICATION:",
            self._doc_spec_block(spec),
            "",
            self._format_spec_block(fmt, target_format),
            "",
            self._quality_spec_block(),
            "",
            "INSTRUCTIONS:",
            f"1. Output a single complete document in {target_format}, ready to compile/export with no further edits.",
            "2. Fix transitions between sections. Remove duplicated content where the section generator overlapped.",
            "3. Apply the OUTPUT FORMAT syntax / citation / figure / escape rules consistently throughout.",
            "4. Add missing section glue only when it is implied by the draft or the supplied evidence.",
            "5. Replace weak unsupported certainty with [source needed] or [assumption] rather than inventing evidence.",
            "6. Output ONLY the polished document — no commentary about what you changed.",
            "",
            self._acceptance_test_block(),
            "",
            "DRAFT TO POLISH:",
            full_draft,
        ])

    # Back-compat: a single-shot draft prompt is still used by the live
    # prompt-preview pane (the Draft Full Report ACTION uses the staged
    # pipeline instead).
    def _build_draft_prompt(self) -> str:
        return self._build_outline_prompt()

    def _build_expand_prompt(self, passage: str) -> str:
        doc_type = self._template_combo.currentText()
        target_format = self._target_format_combo.currentText()
        grade_hint = self._GRADE_HINTS.get(self._grade_combo.currentText(), "")
        spec = self._doc_spec(doc_type)
        fmt = self._format_spec(target_format)
        evidence = self._build_evidence_block()
        return "\n".join([
            f"TASK: Expand and refine this passage so it fits inside a {doc_type}.",
            f"GRADE MODIFIER: {grade_hint}" if grade_hint else "",
            "",
            "DOC-TYPE SPECIFICATION (do not drift into a different doc type's conventions):",
            self._doc_spec_block(spec),
            "",
            self._format_spec_block(fmt, target_format),
            "",
            self._quality_spec_block(),
            "",
            "INSTRUCTIONS:",
            f"1. Match the voice, register, and structural expectations of a {doc_type} exactly.",
            "2. Keep every claim grounded in the supplied evidence; mark anything you cannot ground with [source needed].",
            "3. Apply the OUTPUT FORMAT rules.",
            f"4. Respect the AVOID list for {doc_type}.",
            "5. Preserve the user's original intent while raising rigor, specificity, and traceability.",
            "",
            f"PASSAGE TO EXPAND:\n{passage}",
            "",
            evidence,
        ])

    def _build_critique_prompt(self, draft: str) -> str:
        doc_type = self._template_combo.currentText()
        spec = self._doc_spec(doc_type)
        evidence = self._build_evidence_block()
        return "\n".join([
            f"TASK: Peer-review the following draft AS IF IT WERE A {doc_type.upper()}.",
            "",
            "DOC-TYPE SPECIFICATION the draft should satisfy:",
            self._doc_spec_block(spec),
            "",
            self._quality_spec_block(),
            "",
            "INSTRUCTIONS:",
            f"1. Critique against the doc-type spec FIRST — does this draft actually behave like a {doc_type} on every dimension (audience, voice, structure, citations, depth, avoidance list)?",
            "2. Then critique the substantive content: argument coherence, methodology, statistical validity, citation quality, ethical/reproducibility concerns.",
            "3. Identify unsupported claims, invented citations/paths, vague sections, missing methods, missing units, and export-format risks.",
            "4. List specific revisions the author should make, referencing exact sections and (where applicable) the supplied evidence.",
            "5. Conclude with a verdict on whether the revised draft would meet the SUCCESS CRITERION above.",
            "",
            self._acceptance_test_block(),
            "",
            f"DRAFT:\n{draft}",
            "",
            evidence,
        ])

    def _build_revision_prompt(self, draft: str, critique: str) -> str:
        doc_type = self._template_combo.currentText()
        target_format = self._target_format_combo.currentText()
        spec = self._doc_spec(doc_type)
        fmt = self._format_spec(target_format)
        evidence = self._build_evidence_block()
        return "\n".join([
            f"TASK: Revise the draft into the final {doc_type} after peer-review critique.",
            "",
            "DOC-TYPE SPECIFICATION:",
            self._doc_spec_block(spec),
            "",
            self._format_spec_block(fmt, target_format),
            "",
            self._quality_spec_block(),
            "",
            "REVISION RULES:",
            "1. Apply the critique where valid; do not merely append the critique.",
            "2. Strengthen weak claims only when supported by SOURCE EVIDENCE; otherwise mark [source needed].",
            "3. Remove fabricated citations, fabricated file paths, fake links, and promises about files that were not created.",
            "4. Preserve the target format exactly and output the complete final document only.",
            "",
            self._acceptance_test_block(),
            "",
            "PEER-REVIEW CRITIQUE:",
            critique.strip() or "(empty critique)",
            "",
            "DRAFT TO REVISE:",
            draft,
            "",
            evidence,
        ])

    def _current_preview_prompt(self) -> tuple[str, str]:
        """Return the selected preview action and the raw prompt it would use."""
        try:
            mode = self._preview_mode.currentText() if hasattr(self, "_preview_mode") else "Draft Full Report"
        except Exception:
            mode = "Draft Full Report"

        if mode == "Draft Full Report":
            text = self._build_draft_prompt()
        elif mode == "Expand Selection":
            sel = self._editor.textCursor().selectedText() or "(highlight a passage in the editor)"
            text = self._build_expand_prompt(sel)
        elif mode == "Peer-Review Critique":
            draft = self._editor.toPlainText().strip() or "(paste or generate a draft first)"
            text = self._build_critique_prompt(draft)
        else:
            text = ""
        return mode, text

    def _build_prompt_plan_summary(self, mode: str, raw_prompt: str) -> str:
        """Human-readable Report Builder preview.

        The raw prompt is still available for debugging, but the normal
        UI should show what the selected profile means, not paste the
        internal control contract into the user's face.
        """
        doc_type = self._template_combo.currentText() if hasattr(self, "_template_combo") else "n/a"
        target_format = self._target_format_combo.currentText() if hasattr(self, "_target_format_combo") else "n/a"
        grade = self._grade_combo.currentText() if hasattr(self, "_grade_combo") else "n/a"
        discipline = self._discipline.text().strip() if hasattr(self, "_discipline") else ""
        quality = self._quality_combo.currentText() if hasattr(self, "_quality_combo") else "n/a"
        citation = self._citation_policy_combo.currentText() if hasattr(self, "_citation_policy_combo") else "n/a"
        depth = self._depth_combo.currentText() if hasattr(self, "_depth_combo") else "n/a"
        review = "on" if getattr(self, "_auto_review_check", None) and self._auto_review_check.isChecked() else "off"
        autosave = "on" if getattr(self, "_autosave_check", None) and self._autosave_check.isChecked() else "off"
        title = self._title.text().strip() if hasattr(self, "_title") else ""
        brief = self._abstract.toPlainText().strip() if hasattr(self, "_abstract") else ""
        spec = self._doc_spec(doc_type)
        evidence_n = len(self._sources)
        evidence_bytes = sum(int(s.get("bytes", 0) or 0) for s in self._sources)
        approx_tokens = max(1, len(raw_prompt) // 4)

        if mode == "Draft Full Report":
            action_plan = [
                "1. Build a document-specific outline.",
                "2. Draft each section separately so long outputs do not collapse into a stub.",
                "3. Polish the assembled document for the selected export format.",
                "4. Run critique/revision pass if internal review is on.",
                "5. Auto-save the finished draft if auto-save is on.",
            ]
        elif mode == "Expand Selection":
            action_plan = [
                "1. Read the selected passage in the editor.",
                "2. Expand it using the selected document type and evidence rules.",
                "3. Insert the improved passage at the cursor.",
            ]
        else:
            action_plan = [
                "1. Review the current draft against the selected document type.",
                "2. Flag unsupported claims, fake citations, weak structure, and missing evidence.",
                "3. Return concrete revision instructions.",
            ]

        lines = [
            "Report Builder generation plan",
            "",
            f"Action: {mode}",
            f"Document type: {doc_type}",
            f"Target format: {target_format}",
            f"Grade: {grade}",
            f"Discipline: {discipline or 'not set'}",
            f"Title: {title or 'not set'}",
            f"Brief: {'provided' if brief else 'not provided'}",
            f"Sources: {evidence_n} file(s), {evidence_bytes/1024:.1f} KB total",
            "",
            "Generation controls:",
            f"- Quality bar: {quality}",
            f"- Citation policy: {citation}",
            f"- Depth: {depth}",
            f"- Internal review: {review}",
            f"- Auto-save: {autosave}",
            "",
            "Selected profile behavior:",
            f"- Audience: {spec['audience']}",
            f"- Voice: {spec['voice']}",
            f"- Structure: {spec['section_guidance']}",
            f"- Citation rule: {spec['citations']}",
            f"- Depth target: {spec['depth']}",
            f"- Avoid: {spec['avoid']}",
            f"- Success criterion: {spec['success']}",
            "",
            "What ELI will do:",
            *[f"- {step}" for step in action_plan],
            "",
            "Debug:",
            f"- Raw prompt size: {len(raw_prompt):,} chars, about {approx_tokens:,} tokens.",
            "- Switch View to Raw Debug Prompt only when you need to inspect the exact control text.",
        ]
        return "\n".join(lines)

    def _copy_preview_prompt(self):
        """Copy the current preview text to clipboard."""
        try:
            body = self._prompt_preview.toPlainText()
            from eli.gui.qt_compat import QApplication
            cb = QApplication.clipboard()
            cb.setText(body)
            self._status.setText(f"Copied preview to clipboard ({len(body):,} chars).")
        except Exception as ex:
            QMessageBox.warning(self, "Copy failed", str(ex))

    def _refresh_prompt_preview(self):
        """Update the generation-plan pane when settings change."""
        mode, text = self._current_preview_prompt()
        detail = "Plan Summary"
        try:
            if hasattr(self, "_preview_detail_combo"):
                detail = self._preview_detail_combo.currentText()
        except Exception:
            pass

        if detail != "Raw Debug Prompt":
            self._prompt_preview.setPlainText(self._build_prompt_plan_summary(mode, text))
            return

        # Stats line at top so the user can see scale at a glance.
        char_count = len(text)
        approx_tokens = max(1, char_count // 4)
        evidence_n = len(self._sources)
        evidence_bytes = sum(int(s.get("bytes", 0) or 0) for s in self._sources)
        quality = self._quality_combo.currentText() if hasattr(self, "_quality_combo") else "n/a"
        citation = self._citation_policy_combo.currentText() if hasattr(self, "_citation_policy_combo") else "n/a"
        depth = self._depth_combo.currentText() if hasattr(self, "_depth_combo") else "n/a"
        review = "on" if getattr(self, "_auto_review_check", None) and self._auto_review_check.isChecked() else "off"
        autosave = "on" if getattr(self, "_autosave_check", None) and self._autosave_check.isChecked() else "off"
        header = (
            f"# Raw debug prompt - mode: {mode}\n"
            f"# {char_count:,} chars | ~{approx_tokens:,} tokens | "
            f"{evidence_n} source{'s' if evidence_n != 1 else ''} "
            f"({evidence_bytes/1024:.1f} KB total)\n"
            f"# Quality={quality} | citation={citation} | depth={depth} | review={review} | autosave={autosave}\n"
            f"# This is the exact internal prompt. Normal users should stay on Plan Summary.\n"
            "─" * 72 + "\n"
        )
        self._prompt_preview.setPlainText(header + text)

    # ── Outline parsing ────────────────────────────────────────────────

    # ── Phase 21: frontier direct-broker Report Builder helpers ────────────
    #
    # Architectural rule:
    #   Internal Report Builder stage prompts must never be handed back to the
    #   ordinary ELI chat callback. That callback routes the prompt as user
    #   dialogue, which can turn a document-generation stage into an unrelated
    #   diagnostic/audit action. Report Builder generation is a dedicated local
    #   production engine and therefore calls the inference broker directly.

    _RB_SECTION_COMPLETE = "[[SECTION_COMPLETE]]"
    _RB_CONTINUE_SECTION = "[[CONTINUE_SECTION]]"
    _RB_EVIDENCE_MARKER = "=== SOURCE INVENTORY ==="
    _RB_CONTINUATION_TAIL_MARKER = "EXISTING SECTION TAIL — continue directly after this:"

    _RB_CONTROL_POISON = (
        "NAME_SOURCE_AUDIT",
        "EXPLAIN_COGNITION_RUNTIME",
        "Router parsed:",
        "Cognition runtime:",
        "Memory and retrieval runtime:",
        "route_action",
        "result_action",
        "agents_used",
        "aggregated_confidence",
        "matched_by",
        '"action": "EXPLAIN_',
        "'action': 'EXPLAIN_",
        "[COGNITIVE]",
        "[AGENT:",
        "[GGUF][",
    )

    @staticmethod
    def _rb_word_count(text: str) -> int:
        return len(re.findall(r"\b[\w’'-]+\b", text or ""))

    @staticmethod
    def _rb_int_env(name: str, default: int) -> int:
        raw = str(os.environ.get(name, "") or "").strip()
        if not raw:
            return int(default)
        try:
            return int(raw)
        except Exception:
            return int(default)

    @staticmethod
    def _rb_float_env(name: str, default: float) -> float:
        raw = str(os.environ.get(name, "") or "").strip()
        if not raw:
            return float(default)
        try:
            return float(raw)
        except Exception:
            return float(default)

    def _rb_contract(self, doc_type: str) -> Dict[str, int]:
        # Finished-document and per-section targets. These are deliberately
        # large. The generator reaches them through continuation loops, not by
        # pretending a 16k-context model can emit a 100k-word dissertation in
        # one forward pass.
        defaults: Dict[str, Dict[str, int]] = {
            "Document": {
                "target_total_words": 6000,
                "target_section_words": 850,
                "chunk_goal_words": 1600,
                "section_cap": 14,
                "max_chunks_per_section": 6,
                "review_section_char_limit": 52000,
            },
            "Article": {
                "target_total_words": 8000,
                "target_section_words": 950,
                "chunk_goal_words": 1800,
                "section_cap": 14,
                "max_chunks_per_section": 7,
                "review_section_char_limit": 54000,
            },
            "Research Article": {
                "target_total_words": 14000,
                "target_section_words": 1300,
                "chunk_goal_words": 2400,
                "section_cap": 18,
                "max_chunks_per_section": 9,
                "review_section_char_limit": 60000,
            },
            "Review Article": {
                "target_total_words": 24000,
                "target_section_words": 1800,
                "chunk_goal_words": 3200,
                "section_cap": 24,
                "max_chunks_per_section": 12,
                "review_section_char_limit": 68000,
            },
            "Master's Thesis": {
                "target_total_words": 45000,
                "target_section_words": 2600,
                "chunk_goal_words": 4200,
                "section_cap": 30,
                "max_chunks_per_section": 16,
                "review_section_char_limit": 76000,
            },
            "PhD Dissertation": {
                "target_total_words": 100000,
                "target_section_words": 3800,
                "chunk_goal_words": 5600,
                "section_cap": 48,
                "max_chunks_per_section": 22,
                "review_section_char_limit": 84000,
            },
            "Peer-Review Paper": {
                "target_total_words": 12000,
                "target_section_words": 1200,
                "chunk_goal_words": 2200,
                "section_cap": 16,
                "max_chunks_per_section": 8,
                "review_section_char_limit": 58000,
            },
            "Literature Review": {
                "target_total_words": 30000,
                "target_section_words": 2100,
                "chunk_goal_words": 3600,
                "section_cap": 28,
                "max_chunks_per_section": 14,
                "review_section_char_limit": 72000,
            },
            "Research Proposal": {
                "target_total_words": 12000,
                "target_section_words": 1100,
                "chunk_goal_words": 2200,
                "section_cap": 20,
                "max_chunks_per_section": 8,
                "review_section_char_limit": 58000,
            },
            "Lab Report": {
                "target_total_words": 10000,
                "target_section_words": 1000,
                "chunk_goal_words": 2000,
                "section_cap": 16,
                "max_chunks_per_section": 8,
                "review_section_char_limit": 56000,
            },
            "Technical Report": {
                "target_total_words": 18000,
                "target_section_words": 1400,
                "chunk_goal_words": 2600,
                "section_cap": 22,
                "max_chunks_per_section": 10,
                "review_section_char_limit": 64000,
            },
            "Simulation Report": {
                "target_total_words": 22000,
                "target_section_words": 1600,
                "chunk_goal_words": 3000,
                "section_cap": 24,
                "max_chunks_per_section": 12,
                "review_section_char_limit": 68000,
            },
        }

        contract = dict(defaults.get(doc_type, defaults["Research Article"]))
        depth_name = self._depth_combo.currentText() if hasattr(self, "_depth_combo") else "Extended"
        depth_scale = {
            "Executive concise": 0.45,
            "Balanced": 0.75,
            "Extended": 1.0,
            "Maximal / examiner-ready": 1.35,
        }.get(depth_name, 1.0)
        external_scale = max(0.20, self._rb_float_env("ELI_REPORT_BUILDER_SCALE", 1.0))
        scale = depth_scale * external_scale

        for key in ("target_total_words", "target_section_words", "chunk_goal_words"):
            contract[key] = max(300, int(round(contract[key] * scale)))

        override_map = {
            "ELI_REPORT_BUILDER_TARGET_WORDS": "target_total_words",
            "ELI_REPORT_BUILDER_SECTION_WORDS": "target_section_words",
            "ELI_REPORT_BUILDER_CHUNK_WORDS": "chunk_goal_words",
            "ELI_REPORT_BUILDER_SECTION_CAP": "section_cap",
            "ELI_REPORT_BUILDER_MAX_CHUNKS_PER_SECTION": "max_chunks_per_section",
            "ELI_REPORT_BUILDER_REVIEW_SECTION_CHAR_LIMIT": "review_section_char_limit",
        }
        for env_name, key in override_map.items():
            value = self._rb_int_env(env_name, 0)
            if value > 0:
                contract[key] = value
        return contract

    def _rb_max_tokens(self, stage: str) -> int:
        # Default -1 is intentional: gguf_inference interprets <=0 as use the
        # dynamically available output window after counting the stage prompt.
        safe_stage = re.sub(r"[^A-Z0-9]+", "_", (stage or "DEFAULT").upper()).strip("_")
        stage_raw = os.environ.get(f"ELI_REPORT_BUILDER_MAX_TOKENS_{safe_stage}")
        generic_raw = os.environ.get("ELI_REPORT_BUILDER_MAX_TOKENS")
        raw = stage_raw if stage_raw not in (None, "") else generic_raw
        if raw in (None, ""):
            return -1
        try:
            parsed = int(str(raw).strip())
        except Exception:
            return -1
        return parsed if parsed != 0 else -1

    def _rb_guard_generated_text(self, stage: str, text: str, *, min_chars: int = 1) -> str:
        candidate = str(text or "").strip()
        if len(candidate) < max(1, int(min_chars)):
            raise RuntimeError(
                f"REPORT_BUILDER[{stage}] returned {len(candidate)} chars; minimum required is {min_chars}."
            )
        for poison in self._RB_CONTROL_POISON:
            if poison and poison in candidate:
                raise RuntimeError(
                    f"REPORT_BUILDER[{stage}] rejected control/runtime packet leakage: {poison!r}"
                )
        return candidate

    @staticmethod
    def _rb_estimate_tokens(text: str) -> int:
        return max(1, len(str(text or "")) // 4)

    @staticmethod
    def _rb_is_context_overflow_error(error: Exception) -> bool:
        msg = str(error or "").lower()
        needles = (
            "context window",
            "requested tokens",
            "exceeds context",
            "prompt is too long",
            "llama_context",
        )
        return any(needle in msg for needle in needles)

    def _rb_runtime_n_ctx(self) -> int:
        # what loaded, read each time: a reload can change it
        try:
            from eli.cognition import gguf_inference as gi

            gi.load_model()
            n_ctx = int(gi.context_window() or 0)
        except Exception:
            n_ctx = 0
        return max(1024, n_ctx)

    def _rb_available_tokens(self, prompt: str, *, system: str) -> int:
        try:
            from eli.cognition import gguf_inference as gi

            fn = getattr(gi, "available_generation_tokens", None)
            if callable(fn):
                value = int(fn(prompt, system=system))
                if value > 0:
                    return value
        except Exception:
            pass
        return -1

    @staticmethod
    def _rb_keep_head_tail(text: str, max_chars: int) -> str:
        raw = str(text or "")
        max_chars = max(240, int(max_chars))
        if len(raw) <= max_chars:
            return raw
        bridge = "\n\n[... trimmed for context fit ...]\n\n"
        budget = max_chars - len(bridge)
        if budget <= 160:
            return raw[:max_chars]
        head = max(80, int(budget * 0.58))
        tail = max(80, budget - head)
        if head + tail > len(raw):
            return raw
        return raw[:head].rstrip() + bridge + raw[-tail:].lstrip()

    def _rb_trim_block_by_marker(
        self,
        prompt: str,
        *,
        start_marker: str,
        end_marker: Optional[str],
        max_chars: int,
        mode: str = "head",
    ) -> Tuple[str, bool]:
        raw = str(prompt or "")
        if not start_marker:
            return raw, False
        start = raw.find(start_marker)
        if start < 0:
            return raw, False
        block_start = start + len(start_marker)
        if end_marker:
            end = raw.find(end_marker, block_start)
            if end < 0:
                end = len(raw)
        else:
            end = len(raw)

        block = raw[block_start:end]
        limit = max(200, int(max_chars))
        if len(block) <= limit:
            return raw, False

        if mode == "tail":
            trimmed = "\n[... trimmed for context fit ...]\n" + block[-limit:].lstrip()
        elif mode == "head_tail":
            trimmed = self._rb_keep_head_tail(block, limit)
        else:
            trimmed = block[:limit].rstrip() + "\n[... trimmed for context fit ...]\n"

        return raw[:block_start] + trimmed + raw[end:], True

    def _rb_compact_prompt_for_context(
        self,
        prompt: str,
        *,
        stage: str,
        scale: float,
    ) -> Tuple[str, List[str]]:
        working = str(prompt or "")
        stage_name = str(stage or "").lower()
        scale = max(0.08, min(1.0, float(scale)))
        changes: List[str] = []

        evidence_base = max(12000, self._rb_int_env("ELI_REPORT_BUILDER_SECTION_EVIDENCE_CHARS", 36000))
        evidence_budget = max(1200, int(evidence_base * scale))
        working, changed = self._rb_trim_block_by_marker(
            working,
            start_marker=self._RB_EVIDENCE_MARKER,
            end_marker=None,
            max_chars=evidence_budget,
            mode="head",
        )
        if changed:
            changes.append(f"evidence->{evidence_budget}")

        tail_base = max(3200, self._rb_int_env("ELI_REPORT_BUILDER_CONTINUATION_TAIL_CHARS", 9000))
        tail_budget = max(1200, int(tail_base * scale))
        working, changed = self._rb_trim_block_by_marker(
            working,
            start_marker=self._RB_CONTINUATION_TAIL_MARKER,
            end_marker=self._RB_EVIDENCE_MARKER,
            max_chars=tail_budget,
            mode="tail",
        )
        if not changed and self._RB_CONTINUATION_TAIL_MARKER in working:
            working, changed = self._rb_trim_block_by_marker(
                working,
                start_marker=self._RB_CONTINUATION_TAIL_MARKER,
                end_marker=None,
                max_chars=tail_budget,
                mode="tail",
            )
        if changed:
            changes.append(f"continuation_tail->{tail_budget}")

        review_base = max(8000, self._rb_int_env("ELI_REPORT_BUILDER_REVIEW_SECTION_CHAR_LIMIT", 52000))
        section_budget = max(1800, int(review_base * scale))
        section_markers = (
            "SECTION DRAFT TO REVISE:",
            "SECTION DRAFT:",
            "DRAFT TO INTEGRATE:",
        )
        for marker in section_markers:
            if marker not in working:
                continue
            if marker == "DRAFT TO INTEGRATE:":
                end_marker = None
            else:
                end_marker = self._RB_EVIDENCE_MARKER
            working, changed = self._rb_trim_block_by_marker(
                working,
                start_marker=marker,
                end_marker=end_marker,
                max_chars=section_budget,
                mode="head_tail",
            )
            if changed:
                changes.append(f"{marker.split(':', 1)[0].lower()}->{section_budget}")

        critique_budget = max(1000, int(12000 * scale))
        working, changed = self._rb_trim_block_by_marker(
            working,
            start_marker="CRITIQUE TO APPLY:",
            end_marker="REVISION RULES:",
            max_chars=critique_budget,
            mode="head_tail",
        )
        if changed:
            changes.append(f"critique->{critique_budget}")

        if not changes and ("global_polish" in stage_name or "manual_critique" in stage_name):
            generic_budget = max(2200, int(28000 * scale))
            working = self._rb_keep_head_tail(working, generic_budget)
            if len(working) < len(prompt):
                changes.append(f"prompt->{generic_budget}")

        return working, changes

    def _rb_fit_prompt_to_context(
        self,
        prompt: str,
        *,
        stage: str,
        system: str,
        min_output_tokens: int,
        force_compaction: bool = False,
    ) -> Tuple[str, Dict[str, Any]]:
        raw = str(prompt or "")
        min_output = max(64, int(min_output_tokens))
        n_ctx = self._rb_runtime_n_ctx()

        measured_available = self._rb_available_tokens(raw, system=system)
        if measured_available > 0:
            approx_available = measured_available
            approx_prompt = max(1, n_ctx - measured_available - 128)
        else:
            approx_prompt = self._rb_estimate_tokens(raw)
            approx_available = max(0, n_ctx - approx_prompt - 128)

        if (not force_compaction) and approx_available >= min_output:
            return raw, {
                "n_ctx": n_ctx,
                "available_tokens": approx_available,
                "prompt_tokens_est": approx_prompt,
                "compacted": False,
                "changes": [],
            }

        scales = [1.0, 0.78, 0.62, 0.50, 0.40, 0.30, 0.22, 0.16, 0.12]
        best_prompt = raw
        best_changes: List[str] = []
        best_available = approx_available
        best_prompt_tokens = approx_prompt

        for scale in scales:
            if scale >= 0.999:
                candidate = raw
                changes: List[str] = []
            else:
                candidate, changes = self._rb_compact_prompt_for_context(raw, stage=stage, scale=scale)

            available = self._rb_available_tokens(candidate, system=system)
            if available <= 0:
                prompt_tokens = self._rb_estimate_tokens(candidate)
                available = max(0, n_ctx - prompt_tokens - 128)
            else:
                prompt_tokens = max(1, n_ctx - available - 128)

            if available > best_available:
                best_prompt = candidate
                best_changes = changes
                best_available = available
                best_prompt_tokens = prompt_tokens

            if available >= min_output:
                return candidate, {
                    "n_ctx": n_ctx,
                    "available_tokens": available,
                    "prompt_tokens_est": prompt_tokens,
                    "compacted": bool(changes),
                    "changes": changes,
                }

        raise RuntimeError(
            f"REPORT_BUILDER[{stage}] prompt overflow after context fitting: "
            f"prompt≈{best_prompt_tokens:,} tokens, ctx={n_ctx:,}, "
            f"available={best_available:,}, required_output={min_output:,}. "
            f"Consider fewer sources or a lower depth profile."
        )

    def _rb_infer(
        self,
        prompt: str,
        *,
        stage: str,
        temperature: float = 0.34,
        top_p: float = 0.92,
        min_chars: int = 1,
    ) -> str:
        from eli.cognition.inference_broker import get_broker

        broker = get_broker()
        if broker is None:
            raise RuntimeError("REPORT_BUILDER cannot acquire inference broker.")

        system = (
            "You are ELI's dedicated frontier document-generation engine. "
            "Follow the stage packet exactly. Do not answer as a conversational assistant. "
            "Do not emit routing metadata, runtime audits, control packets, agent packets, "
            "or meta-commentary. Generate only the requested planning, drafting, critique, "
            "revision, or integration artifact."
        )
        min_output_tokens = max(96, min(4096, int(math.ceil(max(1, min_chars) / 3.0))))
        fitted_prompt, fit_meta = self._rb_fit_prompt_to_context(
            prompt,
            stage=stage,
            system=system,
            min_output_tokens=min_output_tokens,
        )
        available_tokens = max(0, int(fit_meta.get("available_tokens") or 0))
        if available_tokens <= 0:
            raise RuntimeError(
                f"REPORT_BUILDER[{stage}] prompt does not fit current context window "
                f"(ctx={fit_meta.get('n_ctx')}, available_tokens={available_tokens})."
            )

        requested_max = int(self._rb_max_tokens(stage))
        # Keep a small safety margin to avoid tokenizer drift overrun.
        cap_max = max(32, int(available_tokens) - 32)
        if requested_max <= 0:
            effective_max = cap_max
        else:
            effective_max = max(32, min(int(requested_max), cap_max))

        if fit_meta.get("compacted"):
            log.debug(
                "[REPORT_BUILDER][CTX] "
                f"stage={stage} n_ctx={fit_meta.get('n_ctx')} "
                f"available_tokens={fit_meta.get('available_tokens')} "
                f"requested_max={requested_max} effective_max={effective_max} "
                f"changes={','.join(fit_meta.get('changes') or [])}"
            )

        try:
            response = broker.infer(
                fitted_prompt,
                system=system,
                max_tokens=effective_max,
                temperature=float(temperature),
                top_p=float(top_p),
                retry=True,
            )
        except Exception as exc:
            if not self._rb_is_context_overflow_error(exc):
                raise
            retry_prompt, retry_meta = self._rb_fit_prompt_to_context(
                prompt,
                stage=f"{stage}_retry",
                system=system,
                min_output_tokens=max(64, min_output_tokens // 2),
                force_compaction=True,
            )
            retry_available = max(0, int(retry_meta.get("available_tokens") or 0))
            if retry_available <= 0:
                raise RuntimeError(
                    f"REPORT_BUILDER[{stage}] prompt still exceeds context after forced compaction "
                    f"(ctx={retry_meta.get('n_ctx')}, available_tokens={retry_available})."
                ) from exc
            retry_cap = max(32, int(retry_available) - 32)
            retry_effective_max = (
                retry_cap if requested_max <= 0 else max(32, min(int(requested_max), retry_cap))
            )
            log.debug(
                "[REPORT_BUILDER][CTX][retry] "
                f"stage={stage} n_ctx={retry_meta.get('n_ctx')} "
                f"available_tokens={retry_meta.get('available_tokens')} "
                f"requested_max={requested_max} effective_max={retry_effective_max} "
                f"changes={','.join(retry_meta.get('changes') or [])}"
            )
            response = broker.infer(
                retry_prompt,
                system=system,
                max_tokens=retry_effective_max,
                temperature=float(temperature),
                top_p=float(top_p),
                retry=True,
            )
        return self._rb_guard_generated_text(stage, response, min_chars=min_chars)

    @staticmethod
    def _rb_terms(*parts: str) -> List[str]:
        stop = {
            "about", "after", "again", "against", "along", "also", "among",
            "because", "before", "being", "between", "could", "document",
            "evidence", "from", "given", "into", "itself", "material",
            "section", "should", "source", "sources", "that", "their",
            "there", "these", "this", "those", "through", "under", "using",
            "which", "with", "within", "would", "write", "title", "intent",
        }
        seen = set()
        terms: List[str] = []
        blob = " ".join(str(part or "") for part in parts)
        for token in re.findall(r"[A-Za-zΑ-Ωα-ωµμ][A-Za-z0-9Α-Ωα-ωµμ_-]{2,}", blob):
            low = token.lower()
            if low in stop or low in seen:
                continue
            seen.add(low)
            terms.append(low)
        return terms[:64]

    @staticmethod
    def _rb_windows(text: str, *, width: int = 2800, overlap: int = 300) -> List[str]:
        raw = str(text or "")
        if not raw:
            return []
        width = max(700, int(width))
        overlap = max(0, min(int(overlap), width // 2))
        step = max(1, width - overlap)
        windows: List[str] = []
        for start in range(0, len(raw), step):
            window = raw[start:start + width].strip()
            if window:
                windows.append(window)
            if start + width >= len(raw):
                break
        return windows

    def _rb_evidence_packet(
        self,
        focus_title: str,
        focus_intent: str = "",
        *,
        sources: Optional[List[Dict[str, Any]]] = None,
        max_chars: Optional[int] = None,
    ) -> str:
        source_rows = list(sources if sources is not None else self._sources)
        if not source_rows:
            return ""

        budget = int(
            max_chars
            if max_chars is not None
            else self._rb_int_env("ELI_REPORT_BUILDER_SECTION_EVIDENCE_CHARS", 36000)
        )
        budget = max(8000, budget)
        terms = self._rb_terms(focus_title, focus_intent)

        lines = [
            "=== SOURCE INVENTORY ===",
            "| File | Kind | Size KB |",
            "| --- | --- | ---: |",
        ]
        for source in source_rows:
            lines.append(
                f"| {source.get('name', 'unknown')} | {source.get('kind', 'unknown')} | "
                f"{float(source.get('bytes', 0) or 0) / 1024.0:.1f} |"
            )

        lines.extend([
            "",
            "=== RELEVANCE-SELECTED SOURCE EVIDENCE ===",
            f"FOCUS: {focus_title}",
            f"INTENT: {focus_intent or '(none)'}",
            f"SEARCH TERMS: {', '.join(terms) if terms else '(none extracted)'}",
        ])

        candidates: List[Dict[str, Any]] = []
        for source_index, source in enumerate(source_rows):
            name = str(source.get("name", "unknown"))
            kind = str(source.get("kind", "unknown"))
            preview = str(source.get("preview", "") or "")
            windows = self._rb_windows(preview)
            if not windows and preview:
                windows = [preview[:2800]]
            for window_index, window in enumerate(windows):
                low = window.lower()
                score = sum(low.count(term) for term in terms)
                if score <= 0 and window_index == 0:
                    score = 1
                if score <= 0:
                    continue
                candidates.append({
                    "score": int(score),
                    "source_index": int(source_index),
                    "window_index": int(window_index),
                    "name": name,
                    "kind": kind,
                    "text": window,
                })

        candidates.sort(key=lambda item: (item["score"], -item["window_index"]), reverse=True)
        used = len("\n".join(lines))
        per_source: Dict[str, int] = {}
        selected = 0

        for candidate in candidates:
            name = str(candidate["name"])
            if per_source.get(name, 0) >= 4:
                continue
            block = (
                f"\n--- {name} ({candidate['kind']}; relevance={candidate['score']}) ---\n"
                f"{str(candidate['text']).strip()}\n"
            )
            if used + len(block) > budget:
                continue
            lines.append(block.rstrip())
            used += len(block)
            per_source[name] = per_source.get(name, 0) + 1
            selected += 1

        if selected == 0:
            for source in source_rows[:6]:
                excerpt = str(source.get("preview", "") or "")[:2400].strip()
                if not excerpt:
                    continue
                block = (
                    f"\n--- {source.get('name', 'unknown')} ({source.get('kind', 'unknown')}; fallback) ---\n"
                    f"{excerpt}\n"
                )
                if used + len(block) > budget:
                    break
                lines.append(block.rstrip())
                used += len(block)

        return "\n".join(lines).strip()

    def _rb_blueprint_prompt(
        self,
        *,
        title: str,
        doc_type: str,
        discipline: str,
        brief: str,
        grade_hint: str,
        doc_spec_block: str,
        format_spec_block: str,
        quality_spec_block: str,
        acceptance_test_block: str,
        contract: Dict[str, int],
        evidence_packet: str,
    ) -> str:
        return "\n".join(part for part in [
            f"TASK: Design the full long-form generation blueprint for a {doc_type} in {discipline}.",
            f"GRADE MODIFIER: {grade_hint}" if grade_hint else "",
            "",
            "DOC-TYPE SPECIFICATION:",
            doc_spec_block,
            "",
            format_spec_block,
            "",
            quality_spec_block,
            "",
            f"TITLE: {title}",
            f"AUTHOR BRIEF:\n{brief}",
            "",
            "SCALE CONTRACT:",
            f"- Finished document target: approximately {contract['target_total_words']:,}+ words unless the evidence genuinely constrains a shorter result.",
            f"- Maximum top-level section count: {contract['section_cap']}.",
            f"- Normal substantive section floor: approximately {contract['target_section_words']:,}+ words.",
            "- Compact front/back-matter sections may be shorter when structurally correct.",
            "",
            "OUTPUT SCHEMA — obey exactly:",
            "SECTION | <ordinal> | <top-level heading text> | <section purpose and evidence obligations> | <suggested target words>",
            "SUBSECTION | <ordinal.parent> | <subheading text> | <subsection purpose>",
            "",
            "RULES:",
            "1. Output only SECTION and SUBSECTION schema lines. No prose introduction. No markdown table.",
            "2. Build a structure that genuinely matches the selected document mode, not a generic article template.",
            "3. Include enough sections to support the scale contract without padding.",
            "4. Section intents must identify evidence obligations or [source needed] where evidence is missing.",
            "5. Long academic modes should include the structure needed for defensible examiner-level work.",
            "",
            acceptance_test_block,
            "",
            evidence_packet,
        ] if part)

    @staticmethod
    def _rb_parse_blueprint(blueprint_text: str) -> List[Dict[str, Any]]:
        sections: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None

        for raw in str(blueprint_text or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            if "|" in line:
                parts = [piece.strip() for piece in line.split("|")]
                tag = parts[0].upper() if parts else ""
                if tag == "SECTION" and len(parts) >= 4:
                    if current is not None:
                        sections.append(current)
                    target_words = 0
                    if len(parts) >= 5:
                        match = re.search(r"\d[\d,]*", parts[4])
                        if match:
                            try:
                                target_words = int(match.group(0).replace(",", ""))
                            except Exception:
                                target_words = 0
                    current = {
                        "ordinal": parts[1] if len(parts) > 1 else str(len(sections) + 1),
                        "title": parts[2] if len(parts) > 2 else "Untitled Section",
                        "intent": parts[3] if len(parts) > 3 else "",
                        "target_words": target_words,
                        "subsections": [],
                    }
                    continue
                if tag == "SUBSECTION" and current is not None and len(parts) >= 4:
                    current.setdefault("subsections", []).append({
                        "ordinal": parts[1],
                        "title": parts[2],
                        "intent": parts[3],
                    })
                    continue

            # Fallback for a model that ignored the strict schema but did emit
            # headings. Better to recover than to throw away a viable blueprint.
            heading = re.match(
                r"^\s*(?:#{1,4}\s+|(?:\d+\.|[IVX]+\.)\s+|Chapter\s+\d+[:\.]?\s+)(.+)$",
                line,
                re.IGNORECASE,
            )
            if heading:
                if current is not None:
                    sections.append(current)
                current = {
                    "ordinal": str(len(sections) + 1),
                    "title": heading.group(1).strip(),
                    "intent": "",
                    "target_words": 0,
                    "subsections": [],
                }
                continue
            if current is not None:
                current["intent"] = (str(current.get("intent", "")) + " " + line).strip()

        if current is not None:
            sections.append(current)
        return sections[:64]

    def _rb_section_target_words(
        self,
        section: Dict[str, Any],
        *,
        section_count: int,
        contract: Dict[str, int],
    ) -> int:
        title_low = str(section.get("title", "") or "").lower()
        target = max(
            int(contract["target_section_words"]),
            int(math.ceil(float(contract["target_total_words"]) / max(1, int(section_count)))),
            int(section.get("target_words", 0) or 0),
        )
        compact_terms = (
            "abstract", "acknowledgement", "acknowledgment", "references",
            "bibliography", "evidence ledger", "source coverage",
        )
        if any(term in title_low for term in compact_terms):
            return max(450, min(1400, target // 2))
        if "conclusion" in title_low or "limitations" in title_low:
            return max(900, int(round(target * 0.72)))
        return target

    @staticmethod
    def _rb_section_map(sections: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        for section in sections:
            lines.append(
                f"- {section.get('ordinal', '?')}. {section.get('title', 'Untitled')}: "
                f"{section.get('intent', '')}"
            )
            for subsection in section.get("subsections", []) or []:
                lines.append(
                    f"  - {subsection.get('ordinal', '?')} {subsection.get('title', 'Untitled')}: "
                    f"{subsection.get('intent', '')}"
                )
        return "\n".join(lines)

    @staticmethod
    def _rb_section_brief(section: Dict[str, Any]) -> str:
        lines = [
            f"SECTION ORDINAL: {section.get('ordinal', '?')}",
            f"SECTION TITLE: {section.get('title', 'Untitled Section')}",
            f"SECTION INTENT: {section.get('intent', '')}",
        ]
        subsections = section.get("subsections", []) or []
        if subsections:
            lines.append("SUBSECTIONS TO COVER:")
            for subsection in subsections:
                lines.append(
                    f"- {subsection.get('ordinal', '?')} {subsection.get('title', 'Untitled')}: "
                    f"{subsection.get('intent', '')}"
                )
        return "\n".join(lines)

    def _rb_section_prompt(
        self,
        *,
        section: Dict[str, Any],
        all_sections: List[Dict[str, Any]],
        title: str,
        doc_type: str,
        grade_hint: str,
        doc_spec_block: str,
        format_spec_block: str,
        quality_spec_block: str,
        acceptance_test_block: str,
        target_words: int,
        chunk_goal_words: int,
        evidence_packet: str,
    ) -> str:
        return "\n".join(part for part in [
            f"TASK: Write a substantive top-level section for a {doc_type}.",
            f"GRADE MODIFIER: {grade_hint}" if grade_hint else "",
            "",
            f"DOCUMENT TITLE: {title}",
            "",
            "FULL DOCUMENT BLUEPRINT:",
            self._rb_section_map(all_sections),
            "",
            "SECTION TO WRITE NOW:",
            self._rb_section_brief(section),
            "",
            "DOC-TYPE SPECIFICATION:",
            doc_spec_block,
            "",
            format_spec_block,
            "",
            quality_spec_block,
            "",
            "GENERATION CONTRACT:",
            f"- Finished target for this section: at least ~{target_words:,} words unless structurally compact front/back matter.",
            f"- This call should attempt a substantial chunk of up to ~{chunk_goal_words:,} words while remaining coherent.",
            "- If the section is complete and has met target, end with [[SECTION_COMPLETE]].",
            "- If it needs continuation, end with [[CONTINUE_SECTION]].",
            "- The marker must be the final line. Do not explain it.",
            "",
            "WRITING RULES:",
            "1. Output starts with this section's heading in the target format.",
            "2. Do not write any other top-level section.",
            "3. Do not emit an outline, synopsis, placeholder, or compressed executive summary.",
            "4. Develop the argument, derivation, method, discussion, or evidence mapping expected of this section.",
            "5. Use supplied evidence where relevant. Use [source needed] or [assumption] exactly where required.",
            "6. Preserve mathematical and technical specificity where the evidence supports it.",
            "",
            acceptance_test_block,
            "",
            evidence_packet,
        ] if part)

    def _rb_continuation_prompt(
        self,
        *,
        section: Dict[str, Any],
        existing_section: str,
        title: str,
        doc_type: str,
        format_spec_block: str,
        quality_spec_block: str,
        target_words: int,
        chunk_goal_words: int,
        continuation_index: int,
        evidence_packet: str,
    ) -> str:
        current_words = self._rb_word_count(existing_section)
        tail_chars = max(
            3200,
            self._rb_int_env("ELI_REPORT_BUILDER_CONTINUATION_TAIL_CHARS", 9000),
        )
        tail = str(existing_section or "")[-tail_chars:]
        return "\n".join(part for part in [
            f"TASK: Continue the same {doc_type} section without restarting it.",
            "",
            f"DOCUMENT TITLE: {title}",
            "",
            "SECTION BEING CONTINUED:",
            self._rb_section_brief(section),
            "",
            format_spec_block,
            "",
            quality_spec_block,
            "",
            "CONTINUATION CONTRACT:",
            f"- Current assembled section length: {current_words:,} words.",
            f"- Target floor for completion: {target_words:,} words.",
            f"- This is continuation chunk {continuation_index}.",
            f"- Continue for up to ~{chunk_goal_words:,} additional words if needed.",
            "- Continue exactly after the last completed idea in the supplied tail; do not restart the section.",
            "- Do not repeat the section heading, earlier subsections, definitions, or paragraphs already visible in the tail.",
            "- Begin directly with new prose or the next unfinished subsection. No preamble.",
            "- Do NOT recap or restart the section.",
            "- Do NOT repeat the heading unless grammatically required by a truncated prior chunk.",
            "- If complete and at/above target, end with [[SECTION_COMPLETE]].",
            "- Otherwise end with [[CONTINUE_SECTION]].",
            "",
            "EXISTING SECTION TAIL — continue directly after this:",
            tail or "(empty section body)",
            "",
            evidence_packet,
        ] if part)

    @staticmethod
    def _rb_strip_marker(text: str) -> Tuple[str, str]:
        raw = str(text or "").strip()
        if raw.endswith(_ReportTab._RB_SECTION_COMPLETE):
            return raw[:-len(_ReportTab._RB_SECTION_COMPLETE)].rstrip(), "complete"
        if raw.endswith(_ReportTab._RB_CONTINUE_SECTION):
            return raw[:-len(_ReportTab._RB_CONTINUE_SECTION)].rstrip(), "continue"
        return raw, ""


    @staticmethod
    def _rb_compact_repeat_key(text: str) -> str:
        """Return a stable, low-noise comparison key for repetition detection."""
        raw = str(text or "").lower()
        raw = re.sub(r"[\t\r]+", " ", raw)
        raw = re.sub(r"[^\w\s\[\]{}()\\#+\-.:;,/]", "", raw)
        raw = re.sub(r"\s+", " ", raw).strip()
        return raw

    @staticmethod
    def _rb_heading_key(line: str) -> str:
        """Normalise Markdown/LaTeX heading lines for restart detection."""
        raw = str(line or "").strip()
        md = re.match(r"^#{1,6}\s+(.+?)\s*$", raw)
        tex = re.match(r"^\\(?:chapter|section|subsection)\{(.+?)\}\s*$", raw)
        if md:
            value = md.group(1)
        elif tex:
            value = tex.group(1)
        else:
            return ""
        value = re.sub(r"^\d+(?:\.\d+)*[.)]?\s*", "", value)
        value = re.sub(r"\s+", " ", value).strip().lower()
        return value

    def _rb_sanitize_continuation(self, existing_section: str, continuation: str) -> Tuple[str, str]:
        """
        Reject continuation chunks that restart a section, re-emit an existing
        heading, or append substantive paragraphs already present.
        """
        candidate = str(continuation or "").strip()
        if not candidate:
            return "", "empty_continuation"

        existing_text = str(existing_section or "")
        existing_heading_keys = {
            self._rb_heading_key(line)
            for line in existing_text.splitlines()
            if self._rb_heading_key(line)
        }

        lines = candidate.splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)

        if lines:
            first_heading = self._rb_heading_key(lines[0])
            if first_heading and first_heading in existing_heading_keys:
                lines.pop(0)
                while lines and not lines[0].strip():
                    lines.pop(0)
                candidate = "\n".join(lines).strip()

        if not candidate:
            return "", "repeated_heading_only"

        existing_para_keys = set()
        for para in re.split(r"\n\s*\n", existing_text):
            key = self._rb_compact_repeat_key(para)
            if len(key) >= 120:
                existing_para_keys.add(key)

        accepted: list[str] = []
        rejected = 0
        for para in re.split(r"\n\s*\n", candidate):
            para = para.strip()
            if not para:
                continue
            key = self._rb_compact_repeat_key(para)
            if len(key) >= 120 and key in existing_para_keys:
                rejected += 1
                continue
            accepted.append(para)

        candidate = "\n\n".join(accepted).strip()
        if not candidate:
            return "", "all_continuation_paragraphs_repeated"

        existing_tail_key = self._rb_compact_repeat_key(existing_text[-20000:])
        candidate_key = self._rb_compact_repeat_key(candidate)
        if len(candidate_key) >= 260 and candidate_key[:260] in existing_tail_key:
            return "", "continuation_prefix_restarts_existing_tail"

        if len(candidate) < 120:
            return "", "continuation_too_short_after_dedup"

        if rejected:
            return candidate, f"accepted_after_pruning_{rejected}_repeated_paragraphs"

        return candidate, "accepted"

    def _rb_generate_section(
        self,
        *,
        section: Dict[str, Any],
        all_sections: List[Dict[str, Any]],
        title: str,
        doc_type: str,
        grade_hint: str,
        doc_spec_block: str,
        format_spec_block: str,
        quality_spec_block: str,
        acceptance_test_block: str,
        target_words: int,
        contract: Dict[str, int],
        evidence_packet: str,
        stage_prefix: str,
    ) -> str:
        first_prompt = self._rb_section_prompt(
            section=section,
            all_sections=all_sections,
            title=title,
            doc_type=doc_type,
            grade_hint=grade_hint,
            doc_spec_block=doc_spec_block,
            format_spec_block=format_spec_block,
            quality_spec_block=quality_spec_block,
            acceptance_test_block=acceptance_test_block,
            target_words=target_words,
            chunk_goal_words=int(contract["chunk_goal_words"]),
            evidence_packet=evidence_packet,
        )
        first = self._rb_infer(
            first_prompt,
            stage=f"{stage_prefix}_draft",
            temperature=0.34,
            min_chars=500,
        )
        body, marker = self._rb_strip_marker(first)
        assembled = body.strip()
        chunks_used = 1
        max_chunks = max(1, int(contract["max_chunks_per_section"]))

        while chunks_used < max_chunks:
            words = self._rb_word_count(assembled)
            # Once the section has reached its resolved target, stop regardless
            # of whether the model emitted [[CONTINUE_SECTION]]. The previous
            # logic allowed a stale "continue" marker to force needless chunks.
            if words >= target_words:
                break
            chunks_used += 1
            continuation_prompt = self._rb_continuation_prompt(
                section=section,
                existing_section=assembled,
                title=title,
                doc_type=doc_type,
                format_spec_block=format_spec_block,
                quality_spec_block=quality_spec_block,
                target_words=target_words,
                chunk_goal_words=int(contract["chunk_goal_words"]),
                continuation_index=chunks_used,
                evidence_packet=evidence_packet,
            )
            more = self._rb_infer(
                continuation_prompt,
                stage=f"{stage_prefix}_continue_{chunks_used}",
                temperature=0.32,
                min_chars=260,
            )
            continuation, marker = self._rb_strip_marker(more)
            continuation, continuation_guard = self._rb_sanitize_continuation(assembled, continuation)
            if continuation:
                assembled = (assembled.rstrip() + "\n\n" + continuation.lstrip()).strip()
            else:
                # A continuation that collapses entirely into repeated or
                # restarted prose is a deterministic stop signal, not a reason
                # to keep consuming budget.
                marker = "complete"
                break
        return assembled.strip()

    def _rb_section_review_prompt(
        self,
        *,
        section: Dict[str, Any],
        section_text: str,
        doc_type: str,
        doc_spec_block: str,
        quality_spec_block: str,
        acceptance_test_block: str,
        evidence_packet: str,
    ) -> str:
        return "\n".join(part for part in [
            f"TASK: Perform a rigorous peer-review critique of one {doc_type} section.",
            "",
            "SECTION:",
            self._rb_section_brief(section),
            "",
            "DOC-TYPE SPECIFICATION:",
            doc_spec_block,
            "",
            quality_spec_block,
            "",
            "REVIEW RULES:",
            "1. Identify unsupported claims, shallow reasoning, repetition, structure drift, missing equations or methods where warranted, and export-format hazards.",
            "2. Distinguish evidence-backed claims from [source needed] claims.",
            "3. Return a specific revision agenda; do not rewrite the section yet.",
            "",
            acceptance_test_block,
            "",
            "SECTION DRAFT:",
            section_text,
            "",
            evidence_packet,
        ] if part)

    def _rb_section_revision_prompt(
        self,
        *,
        section: Dict[str, Any],
        section_text: str,
        critique: str,
        doc_type: str,
        format_spec_block: str,
        doc_spec_block: str,
        quality_spec_block: str,
        target_words: int,
        evidence_packet: str,
    ) -> str:
        return "\n".join(part for part in [
            f"TASK: Revise this {doc_type} section using the critique, returning the complete revised section only.",
            "",
            "SECTION:",
            self._rb_section_brief(section),
            f"TARGET FLOOR: retain substantive scale; aim for at least ~{target_words:,} words unless structurally compact front/back matter.",
            "",
            "DOC-TYPE SPECIFICATION:",
            doc_spec_block,
            "",
            format_spec_block,
            "",
            quality_spec_block,
            "",
            "CRITIQUE TO APPLY:",
            critique,
            "",
            "REVISION RULES:",
            "1. Preserve valid technical content and strengthen weak passages; do not compress the section into a summary.",
            "2. Remove hallucinated claims, fake citations, fake paths, and generic filler.",
            "3. Add [source needed] or [assumption] rather than inventing support.",
            "4. Output the revised section only, in the selected format.",
            "",
            "SECTION DRAFT TO REVISE:",
            section_text,
            "",
            evidence_packet,
        ] if part)

    def _rb_review_and_revise_section(
        self,
        *,
        section: Dict[str, Any],
        section_text: str,
        doc_type: str,
        format_spec_block: str,
        doc_spec_block: str,
        quality_spec_block: str,
        acceptance_test_block: str,
        target_words: int,
        contract: Dict[str, int],
        evidence_packet: str,
        stage_prefix: str,
    ) -> str:
        if len(section_text) > int(contract["review_section_char_limit"]):
            return section_text
        try:
            critique = self._rb_infer(
                self._rb_section_review_prompt(
                    section=section,
                    section_text=section_text,
                    doc_type=doc_type,
                    doc_spec_block=doc_spec_block,
                    quality_spec_block=quality_spec_block,
                    acceptance_test_block=acceptance_test_block,
                    evidence_packet=evidence_packet,
                ),
                stage=f"{stage_prefix}_review",
                temperature=0.20,
                min_chars=180,
            )
        except Exception as exc:
            log.debug(f"[REPORT_BUILDER][review] skipped stage={stage_prefix}: {exc}")
            return section_text
        try:
            revised = self._rb_infer(
                self._rb_section_revision_prompt(
                    section=section,
                    section_text=section_text,
                    critique=critique,
                    doc_type=doc_type,
                    format_spec_block=format_spec_block,
                    doc_spec_block=doc_spec_block,
                    quality_spec_block=quality_spec_block,
                    target_words=target_words,
                    evidence_packet=evidence_packet,
                ),
                stage=f"{stage_prefix}_revise",
                temperature=0.28,
                min_chars=500,
            )
        except Exception as exc:
            log.debug(f"[REPORT_BUILDER][revise] skipped stage={stage_prefix}: {exc}")
            return section_text
        original_words = max(1, self._rb_word_count(section_text))
        revised_words = self._rb_word_count(revised)
        if revised_words < int(original_words * 0.60):
            return section_text
        return revised

    def _rb_global_polish_prompt(
        self,
        *,
        full_draft: str,
        doc_type: str,
        format_spec_block: str,
        doc_spec_block: str,
        quality_spec_block: str,
    ) -> str:
        return "\n".join([
            f"TASK: Perform a final whole-document integration polish for this {doc_type}.",
            "",
            "DOC-TYPE SPECIFICATION:",
            doc_spec_block,
            "",
            format_spec_block,
            "",
            quality_spec_block,
            "",
            "INTEGRATION RULES:",
            "1. Preserve scale. Do not compress this into a summary.",
            "2. Improve transitions, heading consistency, terminology continuity, and repeated-definition handling.",
            "3. Preserve technically substantive passages unless duplicated or contradicted.",
            "4. Do not add unsupported claims; use [source needed] where support is absent.",
            "5. Output the complete integrated document only.",
            "",
            "DRAFT TO INTEGRATE:",
            full_draft,
        ])

    def _rb_maybe_global_polish(
        self,
        *,
        full_draft: str,
        doc_type: str,
        format_spec_block: str,
        doc_spec_block: str,
        quality_spec_block: str,
    ) -> str:
        max_chars = max(12000, self._rb_int_env("ELI_REPORT_BUILDER_GLOBAL_POLISH_MAX_CHARS", 36000))
        if len(full_draft) > max_chars:
            return full_draft
        try:
            polished = self._rb_infer(
                self._rb_global_polish_prompt(
                    full_draft=full_draft,
                    doc_type=doc_type,
                    format_spec_block=format_spec_block,
                    doc_spec_block=doc_spec_block,
                    quality_spec_block=quality_spec_block,
                ),
                stage="global_polish",
                temperature=0.24,
                min_chars=800,
            )
        except Exception as exc:
            log.debug(f"[REPORT_BUILDER][global_polish] skipped: {exc}")
            return full_draft
        original_words = max(1, self._rb_word_count(full_draft))
        polished_words = self._rb_word_count(polished)
        if polished_words < int(original_words * 0.70):
            return full_draft
        return polished

    def _validate_generated_report(
        self,
        *,
        final_text: str,
        doc_type: str,
        section_count: int,
        contract: Dict[str, int],
    ) -> Tuple[bool, str]:
        body = str(final_text or "").strip()
        if not body:
            return False, "final document is empty"
        for poison in self._RB_CONTROL_POISON:
            if poison in body:
                return False, f"control/runtime leakage remains in final document: {poison!r}"
        low = body.lower()
        if "the documents you've provided outline" in low and self._rb_word_count(body) < 2200:
            return False, "final output collapsed into a source-summary stub"
        words = self._rb_word_count(body)
        target = max(1, int(contract["target_total_words"]))
        floor = max(1500, int(round(target * 0.55)))
        if words < floor:
            return False, (
                f"final document has {words:,} words; fail-closed floor for {doc_type} is "
                f"{floor:,} words from configured target {target:,}"
            )
        if section_count >= 3:
            heading_count = len(re.findall(r"(?m)^\s*(?:#{1,4}\s+|\\(?:chapter|section|subsection)\{)", body))
            if heading_count < max(2, section_count // 3):
                return False, "final document lost expected section-heading structure"

        repeat_ok, repeat_detail = self._rb_validate_repeat_density(body)
        if not repeat_ok:
            return False, repeat_detail

        return True, f"{words:,} words; configured target {target:,}; {repeat_detail}"


    def _rb_validate_repeat_density(self, text: str) -> Tuple[bool, str]:
        """
        Fail closed on loop-generated substantive paragraph repetition.
        Headings, short labels, and small bullet fragments are ignored.
        """
        paragraphs: list[str] = []
        for para in re.split(r"\n\s*\n", str(text or "")):
            key = self._rb_compact_repeat_key(para)
            if len(key) >= 180:
                paragraphs.append(key)

        if len(paragraphs) < 8:
            return True, "repeat-density validator skipped for compact document"

        counts: dict[str, int] = {}
        for key in paragraphs:
            counts[key] = counts.get(key, 0) + 1

        duplicate_instances = sum(count - 1 for count in counts.values() if count > 1)
        allowed = max(1, len(paragraphs) // 12)

        if duplicate_instances > allowed:
            return (
                False,
                f"final document retains {duplicate_instances} repeated substantive paragraph instance(s); "
                f"allowed threshold for {len(paragraphs)} substantive paragraphs is {allowed}",
            )

        return True, "repeat-density validator passed"


    def _rb_record_failure_event(
        self,
        stage: str,
        detail: str,
        *,
        title: str = "",
        doc_type: str = "",
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Push Report Builder fail-closed events into ELI's existing failure /
        self-improvement substrate without allowing telemetry failure to break UX.
        """
        payload: Dict[str, Any] = {
            "source": "eli.gui.labs_tab.report_builder",
            "stage": str(stage or "unknown"),
            "title": str(title or ""),
            "doc_type": str(doc_type or ""),
            "detail": str(detail or ""),
        }
        if context:
            payload["context"] = dict(context)

        failure_input = (
            f"REPORT_BUILDER::{payload['stage']}::"
            f"{payload['doc_type'] or 'document'}::{payload['title'] or 'untitled'}"
        )
        failure_error = payload["detail"] or "report builder fail-closed event"

        try:
            from eli.runtime.self_improvement import get_self_improvement

            engine = get_self_improvement()
            if hasattr(engine, "log_failure"):
                engine.log_failure(
                    failure_input,
                    error=failure_error,
                    confidence=1.0,
                    context=payload,
                )
                return

            memory = getattr(engine, "memory", None)
            if memory is not None and hasattr(memory, "log_failure"):
                memory.log_failure(
                    failure_input,
                    error=failure_error,
                    confidence=1.0,
                    context=payload,
                    source="report_builder",
                )
        except Exception:
            return

    def _rb_manifest_path(self, title: str) -> Path:
        from eli.core.paths import documents_dir
        root = Path(documents_dir())
        root.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", title.strip() or "eli_report").strip("_") or "eli_report"
        return root / f"{safe}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.generation_manifest.json"

    def _rb_write_manifest(
        self,
        *,
        title: str,
        doc_type: str,
        target_format: str,
        contract: Dict[str, int],
        sections: List[Dict[str, Any]],
        final_text: str,
        validation_ok: bool,
        validation_detail: str,
        saved_path: Optional[Path],
        elapsed_seconds: float,
    ) -> Optional[Path]:
        try:
            path = self._rb_manifest_path(title)
            payload = {
                "kind": "eli_report_builder_frontier_manifest_v1",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "title": title,
                "document_type": doc_type,
                "target_format": target_format,
                "contract": contract,
                "sections": sections,
                "final_chars": len(final_text or ""),
                "final_words": self._rb_word_count(final_text or ""),
                "validation_ok": bool(validation_ok),
                "validation_detail": validation_detail,
                "saved_document": str(saved_path) if saved_path else None,
                "elapsed_seconds": round(float(elapsed_seconds), 3),
                "max_tokens_policy": "stage override env > ELI_REPORT_BUILDER_MAX_TOKENS > -1 auto/context-aware",
            }
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            return path
        except Exception:
            return None

    @staticmethod
    def _parse_outline(outline_text: str) -> List[Dict[str, str]]:
        """Parse the LLM's outline output into a list of
        {title, intent} dicts. Heuristic — scans for markdown headings
        or numbered lines and picks the first ~14 entries."""
        sections: List[Dict[str, str]] = []
        if not outline_text:
            return sections
        lines = outline_text.splitlines()
        current: Optional[Dict[str, str]] = None
        for raw in lines:
            line = raw.rstrip()
            if not line.strip():
                continue
            # Top-level markers: '# Heading', '## Heading',
            # 'N. Heading', 'Section: Heading', 'Chapter N: ...'.
            m = re.match(r"^\s*(?:#{1,3}\s+|(?:\d+\.|[IVX]+\.)\s+|Chapter\s+\d+[:\.]?\s+)(.+)$",
                         line, re.IGNORECASE)
            if m:
                if current is not None:
                    sections.append(current)
                current = {"title": m.group(1).strip(), "intent": ""}
                continue
            if current is not None:
                # Treat continuation text as the section's intent line.
                current["intent"] = (current["intent"] + " " + line.strip()).strip()
        if current is not None:
            sections.append(current)
        # Cap to keep total LLM call count bounded.
        return sections[:14]


    def _draft_full_with_eli(self):
        # Frontier direct-broker, continuation-based, fail-closed pipeline.
        if self._draft_running:
            self._status.setText("Draft pipeline already running.")
            return

        title_snapshot = self._title.text().strip() or "eli_report"
        doc_type = self._template_combo.currentText()
        target_format = self._target_format_combo.currentText()
        discipline = self._discipline.text().strip() or "general academic"
        brief = self._abstract.toPlainText().strip() or "(no brief provided)"
        grade = self._grade_combo.currentText()
        grade_hint = self._GRADE_HINTS.get(grade, "")
        spec = self._doc_spec(doc_type)
        fmt = self._format_spec(target_format)
        doc_spec_block = self._doc_spec_block(spec)
        format_spec_block = self._format_spec_block(fmt, target_format)
        quality_spec_block = self._quality_spec_block()
        acceptance_test_block = self._acceptance_test_block()
        contract = self._rb_contract(doc_type)
        sources_snapshot = [dict(source) for source in self._sources]
        run_review = bool(self._auto_review_check.isChecked()) if hasattr(self, "_auto_review_check") else True
        autosave = bool(self._autosave_check.isChecked()) if hasattr(self, "_autosave_check") else True

        def _set_status(message: str) -> None:
            try:
                self._status_sig.emit(message)
            except Exception:
                pass

        def _set_editor(text: str) -> None:
            try:
                self._editor_sig.emit(text)
            except Exception:
                pass

        self._draft_running = True
        _set_status(
            f"ELI Report Builder [frontier direct-broker]: blueprinting {doc_type}; "
            f"target≈{contract['target_total_words']:,}+ words; output budget=auto/context-aware unless overridden."
        )

        def _run_pipeline() -> None:
            started = time.perf_counter()
            saved_path: Optional[Path] = None
            sections: List[Dict[str, Any]] = []
            final = ""
            validation_ok = False
            validation_detail = "pipeline did not reach validation"

            try:
                outline_evidence = self._rb_evidence_packet(
                    title_snapshot,
                    brief,
                    sources=sources_snapshot,
                    max_chars=self._rb_int_env("ELI_REPORT_BUILDER_OUTLINE_EVIDENCE_CHARS", 48000),
                )
                blueprint_prompt = self._rb_blueprint_prompt(
                    title=title_snapshot,
                    doc_type=doc_type,
                    discipline=discipline,
                    brief=brief,
                    grade_hint=grade_hint,
                    doc_spec_block=doc_spec_block,
                    format_spec_block=format_spec_block,
                    quality_spec_block=quality_spec_block,
                    acceptance_test_block=acceptance_test_block,
                    contract=contract,
                    evidence_packet=outline_evidence,
                )
                blueprint_text = self._rb_infer(
                    blueprint_prompt,
                    stage="blueprint",
                    temperature=0.22,
                    min_chars=180,
                )
                sections = self._rb_parse_blueprint(blueprint_text)[: int(contract["section_cap"])]
                if not sections:
                    _set_editor(blueprint_text)
                    validation_detail = "blueprint was not parseable into SECTION/SUBSECTION rows"
                    self._rb_record_failure_event(
                        "blueprint_parse",
                        validation_detail,
                        title=title_snapshot,
                        doc_type=doc_type,
                        context={"raw_blueprint_chars": len(blueprint_text or "")},
                    )
                    _set_status(f"REPORT_BUILDER FAIL-CLOSED: {validation_detail}. Raw blueprint shown.")
                    return

                assembled_sections: List[str] = []
                for index, section in enumerate(sections, 1):
                    target_words = self._rb_section_target_words(
                        section,
                        section_count=len(sections),
                        contract=contract,
                    )
                    section["resolved_target_words"] = target_words
                    focus = (
                        f"{section.get('title', '')}\n{section.get('intent', '')}\n"
                        + "\n".join(
                            f"{sub.get('title', '')} {sub.get('intent', '')}"
                            for sub in section.get("subsections", []) or []
                        )
                    )
                    evidence_packet = self._rb_evidence_packet(
                        str(section.get("title", "")),
                        focus,
                        sources=sources_snapshot,
                    )
                    _set_status(
                        f"ELI Report Builder: drafting section {index}/{len(sections)} — "
                        f"{section.get('title', 'Untitled')} | target≈{target_words:,} words"
                    )
                    section_text = self._rb_generate_section(
                        section=section,
                        all_sections=sections,
                        title=title_snapshot,
                        doc_type=doc_type,
                        grade_hint=grade_hint,
                        doc_spec_block=doc_spec_block,
                        format_spec_block=format_spec_block,
                        quality_spec_block=quality_spec_block,
                        acceptance_test_block=acceptance_test_block,
                        target_words=target_words,
                        contract=contract,
                        evidence_packet=evidence_packet,
                        stage_prefix=f"section_{index}",
                    )
                    if run_review:
                        _set_status(
                            f"ELI Report Builder: critiquing draft against quality contract "
                            f"and applying review feedback for section {index}/{len(sections)} - "
                            f"{section.get('title', 'Untitled')}"
                        )
                        section_text = self._rb_review_and_revise_section(
                            section=section,
                            section_text=section_text,
                            doc_type=doc_type,
                            format_spec_block=format_spec_block,
                            doc_spec_block=doc_spec_block,
                            quality_spec_block=quality_spec_block,
                            acceptance_test_block=acceptance_test_block,
                            target_words=target_words,
                            contract=contract,
                            evidence_packet=evidence_packet,
                            stage_prefix=f"section_{index}",
                        )
                    assembled_sections.append(section_text.strip())
                    partial = "\n\n".join(block for block in assembled_sections if block).strip()
                    _set_editor(partial)

                final = "\n\n".join(block for block in assembled_sections if block).strip()
                _set_status("ELI Report Builder: attempting final integration polish when context-safe…")
                final = self._rb_maybe_global_polish(
                    full_draft=final,
                    doc_type=doc_type,
                    format_spec_block=format_spec_block,
                    doc_spec_block=doc_spec_block,
                    quality_spec_block=quality_spec_block,
                )
                validation_ok, validation_detail = self._validate_generated_report(
                    final_text=final,
                    doc_type=doc_type,
                    section_count=len(sections),
                    contract=contract,
                )
                _set_editor(final)

                if not validation_ok:
                    self._rb_record_failure_event(
                        "final_validation",
                        validation_detail,
                        title=title_snapshot,
                        doc_type=doc_type,
                        context={
                            "sections": len(sections),
                            "final_words": self._rb_word_count(final),
                        },
                    )
                    _set_status(
                        f"REPORT_BUILDER FAIL-CLOSED: {validation_detail}. "
                        "Draft retained in editor; not autosaved as a completed document."
                    )
                    return

                if autosave:
                    saved_path = self._autosave_report(
                        final,
                        title=title_snapshot,
                        target_format=target_format,
                    )
                elapsed = time.perf_counter() - started
                manifest_path = self._rb_write_manifest(
                    title=title_snapshot,
                    doc_type=doc_type,
                    target_format=target_format,
                    contract=contract,
                    sections=sections,
                    final_text=final,
                    validation_ok=validation_ok,
                    validation_detail=validation_detail,
                    saved_path=saved_path,
                    elapsed_seconds=elapsed,
                )
                saved_note = f" Saved: {saved_path}" if saved_path else ""
                manifest_note = f" Manifest: {manifest_path}" if manifest_path else ""
                _set_status(
                    f"ELI Report Builder delivered {self._rb_word_count(final):,} words "
                    f"across {len(sections)} sections for {doc_type}. "
                    f"{validation_detail}.{saved_note}{manifest_note}"
                )
            except Exception as exc:
                elapsed = time.perf_counter() - started
                if final:
                    _set_editor(final)
                self._rb_record_failure_event(
                    "pipeline_exception",
                    f"{type(exc).__name__}: {exc}",
                    title=title_snapshot,
                    doc_type=doc_type,
                    context={
                        "elapsed_seconds": round(elapsed, 3),
                        "assembled_final_words": self._rb_word_count(final),
                    },
                )
                _set_status(f"REPORT_BUILDER FAIL-CLOSED after {elapsed:.1f}s: {exc}")
            finally:
                self._draft_running = False

        threading.Thread(
            target=_run_pipeline,
            name="labs-frontier-report-builder",
            daemon=True,
        ).start()


    def _ask_eli_expand_selection(self):
        cursor = self._editor.textCursor()
        selected = cursor.selectedText()
        if not selected:
            QMessageBox.information(
                self, "Select text",
                "Highlight a section heading or paragraph to expand."
            )
            return
        prompt = self._build_expand_prompt(selected)
        # A copy of the cursor follows the text while ELI writes, so the expansion lands on the
        # passage that was selected even if the draft is edited meanwhile.
        self._run_manual_stage(
            prompt, stage="manual_expand", temperature=0.34, min_chars=120,
            where=type(cursor)(cursor),
            status=(f"ELI expanding selection through direct report broker… "
                    f"({len(prompt):,} chars / ~{len(prompt)//4:,} prompt tokens)"))

    def _ask_eli_critique(self):
        draft = self._editor.toPlainText().strip()
        if not draft:
            QMessageBox.information(self, "No draft", "Generate or paste a draft first.")
            return
        prompt = self._build_critique_prompt(draft)
        self._run_manual_stage(
            prompt, stage="manual_critique", temperature=0.22, min_chars=180, where=None,
            status=(f"ELI preparing peer-review critique through direct report broker… "
                    f"({len(prompt):,} chars / ~{len(prompt)//4:,} prompt tokens)"))

    def _set_manual_busy(self, busy: bool) -> None:
        for name in ("_expand_btn", "_critique_btn"):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(not busy)

    def _run_manual_stage(self, prompt: str, *, stage: str, temperature: float, min_chars: int,
                          where, status: str) -> None:
        """Expand / Critique: the model writes off the window's thread (a whole generation, on the
        35B model a minute or more, froze the window when it ran on it); the text is put into the
        editor on the window's thread when it is done."""
        if self._manual_pending is not None:
            self._status.setText("ELI is still writing the last Expand / Critique.")
            return
        self._manual_pending = {"stage": stage, "where": where}
        self._set_manual_busy(True)
        self._status.setText(status)
        _start_bg(self._manual_workers,
                  lambda: self._rb_infer(prompt, stage=stage, temperature=temperature,
                                         min_chars=min_chars),
                  self._on_manual_stage_done)

    def _on_manual_stage_done(self, response) -> None:
        pending, self._manual_pending = self._manual_pending or {}, None
        self._set_manual_busy(False)
        if isinstance(response, dict) and "__error__" in response:
            self._status.setText(f"ELI Report Builder error: {response['__error__']}")
            QMessageBox.warning(self, "ELI Report Builder error", str(response["__error__"]))
            return
        response = str(response or "")
        if pending.get("stage") == "manual_expand":
            pending["where"].insertText(response)
            self._status.setText(f"Inserted {len(response):,} chars at cursor.")
            return
        existing = self._editor.toPlainText().rstrip()
        critique_block = "\n\n---\n\n## Peer-Review Critique\n\n" + response.strip() + "\n"
        self._editor.setPlainText(existing + critique_block)
        self._status.setText(f"Peer-review critique appended ({len(response):,} chars).")

    def _export(self, kind: str):
        text = self._editor.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "Empty", "Nothing to export yet.")
            return
        if getattr(self, "_export_running", False):
            self._status.setText("An export is still running; it says here when it is done.")
            return

        title = self._title.text().strip() or "Untitled"
        author = self._author.text().strip() or "Author"

        # PDF / DOCX go through pandoc / lualatex; the rest are pure text.
        if kind == "pdf":
            self._export_pdf(text, title, author)
            return
        if kind == "docx":
            self._export_docx(text, title, author)
            return

        # Each payload is made only for the kind asked for: the LaTeX one runs pandoc, and it ran for
        # every export, Markdown and HTML included.
        ext_map = {
            "md":   ("Markdown (*.md)",   ".md",   lambda: text),
            "qmd":  ("Quarkdown (*.qmd)", ".qmd",  lambda: self._md_to_quarkdown(text, title, author)),
            "html": ("HTML (*.html)",     ".html", lambda: self._md_to_html(text)),
            "tex":  ("LuaLaTeX (*.tex)",  ".tex",  lambda: self._md_to_latex(text, title, author)),
        }
        if kind not in ext_map:
            return
        flt, suffix, payload = ext_map[kind]
        path, _ = QFileDialog.getSaveFileName(self, "Export Report", f"report{suffix}", flt)
        if not path:
            return

        def write() -> Dict[str, Any]:
            Path(path).write_text(payload(), encoding="utf-8")
            return {"ok": True, "path": path, "what": "Report"}

        self._run_export(f"Writing {Path(path).name}…", write)

    # ── PDF / DOCX export. The LaTeX or Markdown a converter reads, and everything it writes
    #    beside it, live in a folder of the export's own that is removed afterwards; only the
    #    finished file is moved to the name the user chose. They were written next to that file
    #    under its name, so exporting report.docx overwrote and then deleted the user's report.md
    #    (and a PDF replaced report.tex and deleted report.aux/.log/.toc).
    #    The converters run on a worker thread (_run_export): they ran in the button's handler, so
    #    the window froze until lualatex or pandoc finished.
    @staticmethod
    def _export_workdir():
        return tempfile.TemporaryDirectory(prefix=_temp_path("report-export-").name)

    @staticmethod
    def _deliver(produced: Path, out: Path) -> bool:
        import shutil
        if not produced.is_file():
            return False
        shutil.move(str(produced), str(out))
        return True

    def _run_export(self, doing: str, convert) -> None:
        """`convert()` on a worker thread; what it returns ({ok, path, what} or {ok: False, error})
        is said on the window's thread by _export_done."""
        self._export_running = True
        self._status.setText(doing)
        if getattr(self, "_export_workers", None) is None:
            self._export_workers = []
        _start_bg(self._export_workers, convert, self._export_done)

    def _export_done(self, result) -> None:
        self._export_running = False
        result = result if isinstance(result, dict) else {"ok": False, "error": str(result)}
        if result.get("ok"):
            what, where = result.get("what") or "Report", result.get("path") or ""
            self._status.setText(f"{what} saved: {where}")
            QMessageBox.information(self, "Exported", f"{what} saved to:\n{where}")
            return
        why = str(result.get("error") or result.get("__error__") or "the export failed")
        self._status.setText(f"Export failed: {why.splitlines()[0] if why else ''}")
        QMessageBox.critical(self, "Export Error", why)

    @staticmethod
    def _converter_said(proc) -> str:
        return ((getattr(proc, "stderr", "") or getattr(proc, "stdout", "") or "")[-1500:]) if proc else ""

    # lualatex (preferred), else pandoc with --pdf-engine=lualatex.
    def _export_pdf(self, md_text: str, title: str, author: str):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export PDF (article)", "report.pdf", "PDF (*.pdf)"
        )
        if not path:
            return
        out_pdf = Path(path)
        engine = self._find_executable(["lualatex", "xelatex", "pdflatex"])
        pandoc = None if engine else self._find_executable(["pandoc"])
        if not engine and not pandoc:
            QMessageBox.critical(
                self, "PDF export",
                "Neither lualatex/xelatex/pdflatex nor pandoc are installed.\n"
                "Install TeX Live (e.g. apt: texlive-luatex) or pandoc to export PDFs."
            )
            return
        latex = self._md_to_latex

        def convert() -> Dict[str, Any]:
            proc = None
            if engine:
                try:
                    with self._export_workdir() as work:
                        tex = Path(work) / "report.tex"
                        tex.write_text(latex(md_text, title, author), encoding="utf-8")
                        cmd = [engine, "-interaction=nonstopmode", "-output-directory", work, str(tex)]
                        proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
                        if self._deliver(tex.with_suffix(".pdf"), out_pdf):
                            return {"ok": True, "path": str(out_pdf), "what": "PDF"}
                except Exception as ex:
                    return {"ok": False, "error": f"{engine} failed: {ex}"}
                return {"ok": False, "error": f"{engine} did not produce a PDF.\n\n{self._converter_said(proc)}"}
            try:
                with self._export_workdir() as work:
                    md = Path(work) / "report.md"
                    md.write_text(md_text, encoding="utf-8")
                    produced = Path(work) / "report.pdf"
                    cmd = [pandoc, str(md), "-o", str(produced),
                           "--pdf-engine=lualatex", "-V", f"title={title}", "-V", f"author={author}"]
                    proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
                    if self._deliver(produced, out_pdf):
                        return {"ok": True, "path": str(out_pdf), "what": "PDF (pandoc)"}
            except Exception as ex:
                return {"ok": False, "error": f"pandoc invocation failed: {ex}"}
            return {"ok": False, "error": f"pandoc failed:\n{self._converter_said(proc)}"}

        self._run_export(f"Running {Path(engine or pandoc).name} → {out_pdf.name}…", convert)

    def _export_docx(self, md_text: str, title: str, author: str):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export DOCX", "report.docx", "Word Document (*.docx)"
        )
        if not path:
            return
        pandoc = self._find_executable(["pandoc"])
        if not pandoc:
            QMessageBox.critical(
                self, "DOCX export",
                "pandoc not found — install pandoc to export DOCX."
            )
            return
        out_docx = Path(path)

        def convert() -> Dict[str, Any]:
            proc = None
            try:
                with self._export_workdir() as work:
                    md = Path(work) / "report.md"
                    md.write_text(md_text, encoding="utf-8")
                    produced = Path(work) / "report.docx"
                    cmd = [pandoc, str(md), "-o", str(produced),
                           "-V", f"title={title}", "-V", f"author={author}"]
                    proc = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
                    if self._deliver(produced, out_docx):
                        return {"ok": True, "path": str(out_docx), "what": "DOCX"}
            except Exception as ex:
                return {"ok": False, "error": f"pandoc failed: {ex}"}
            return {"ok": False, "error": f"pandoc failed:\n{self._converter_said(proc)}"}

        self._run_export(f"Running pandoc → {out_docx.name}…", convert)

    @staticmethod
    def _find_executable(names: List[str]) -> Optional[str]:
        import shutil
        for n in names:
            p = shutil.which(n)
            if p:
                return p
        return None

    @staticmethod
    def _md_to_html(md: str) -> str:
        try:
            import markdown  # type: ignore
            body = markdown.markdown(md, extensions=["tables", "fenced_code", "toc"])
        except Exception:
            body = "<pre>" + md.replace("&", "&amp;").replace("<", "&lt;") + "</pre>"
        return (
            "<!doctype html><html><head><meta charset=\"utf-8\">"
            "<title>Report</title>"
            "<style>body{font-family:Georgia,serif;max-width:780px;margin:40px auto;line-height:1.55;}"
            "h1,h2,h3{font-family:Helvetica,Arial,sans-serif;}"
            "code{background:#f4f4f4;padding:2px 4px;}"
            "pre{background:#f4f4f4;padding:12px;overflow:auto;}"
            "table{border-collapse:collapse;}"
            "td,th{border:1px solid #ccc;padding:4px 8px;}"
            "</style></head><body>" + body + "</body></html>"
        )

    @staticmethod
    def _md_to_quarkdown(md: str, title: str, author: str) -> str:
        """Emit a Quarkdown (.qmd) document with front-matter."""
        front = (
            "---\n"
            f"title: \"{title}\"\n"
            f"author: \"{author}\"\n"
            f"date: \"{datetime.now().strftime('%Y-%m-%d')}\"\n"
            "format:\n"
            "  html:\n    toc: true\n    number-sections: true\n    theme: cosmo\n"
            "  pdf:\n    pdf-engine: lualatex\n    toc: true\n    number-sections: true\n"
            "  docx:\n    toc: true\n    number-sections: true\n"
            "---\n\n"
        )
        return front + md

    @staticmethod
    def _md_to_latex(md: str, title: str = "", author: str = "") -> str:
        """Convert Markdown → LuaLaTeX (article class). Pandoc-quality if pandoc
        is on PATH; otherwise lightweight regex conversion good enough for
        Overleaf import."""
        import shutil, subprocess as _sp
        if shutil.which("pandoc"):
            try:
                proc = _sp.run(
                    ["pandoc", "-f", "markdown", "-t", "latex",
                     "--standalone", "--pdf-engine=lualatex",
                     "-V", f"title={title}", "-V", f"author={author}",
                     "-V", "documentclass=article",
                     "-V", "fontfamily=lmodern",
                     "-V", "geometry=a4paper,margin=2.5cm"],
                    input=md, capture_output=True, text=True,
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    return proc.stdout
            except Exception:
                pass

        out = md
        out = re.sub(r"^### (.*)$", r"\\subsubsection{\1}", out, flags=re.M)
        out = re.sub(r"^## (.*)$", r"\\subsection{\1}", out, flags=re.M)
        out = re.sub(r"^# (.*)$", r"\\section{\1}", out, flags=re.M)
        out = re.sub(r"\*\*(.+?)\*\*", r"\\textbf{\1}", out)
        out = re.sub(r"\*(.+?)\*", r"\\emph{\1}", out)
        out = re.sub(r"`([^`]+)`", r"\\texttt{\1}", out)
        preamble = (
            "\\documentclass[11pt,a4paper]{article}\n"
            "\\usepackage[a4paper,margin=2.5cm]{geometry}\n"
            "\\usepackage{fontspec}\n"
            "\\usepackage{hyperref}\n"
            "\\usepackage{graphicx}\n"
            "\\usepackage{booktabs}\n"
            "\\usepackage{longtable}\n"
            "\\usepackage{enumitem}\n"
            f"\\title{{{title or 'Report'}}}\n"
            f"\\author{{{author or 'Author'}}}\n"
            f"\\date{{{datetime.now().strftime('%Y-%m-%d')}}}\n"
            "\\begin{document}\n\\maketitle\n\n"
        )
        return preamble + out + "\n\\end{document}\n"


# ═══════════════════════════════════════════════════════════════════════════
# File/Folder Chat sub-tab
# ═══════════════════════════════════════════════════════════════════════════

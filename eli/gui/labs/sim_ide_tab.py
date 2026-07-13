"""ELI Labs — Simulation / IDE tab

Extracted verbatim from the former eli/gui/labs_tab.py monolith (strangler
refactor, v3 core-modularisation). Shared preamble — Qt binding, plot paths,
physics tables — lives in eli.gui.labs._common.
"""
from __future__ import annotations
from eli.gui.labs._common import *  # noqa: F401,F403

from eli.gui.labs.code_runner import _CodeRunnerThread  # noqa: F401


class _SimIDETab(QWidget):
    _STARTERS = {
        "Blank": "",
        "Hello World": 'print("Hello, World!")\n',
        "Matplotlib plot": (
            "import matplotlib.pyplot as plt\n"
            "import numpy as np\n\n"
            "x = np.linspace(0, 2 * np.pi, 400)\n"
            "y = np.sin(x)\n\n"
            "fig, ax = plt.subplots()\n"
            "ax.plot(x, y, label='sin(x)')\n"
            "ax.set_title('Sine wave')\n"
            "ax.set_xlabel('x')\n"
            "ax.set_ylabel('y')\n"
            "ax.legend()\n"
            "plt.tight_layout()\n"
            f"plt.savefig({_labs_plot_literal('plot')}, dpi=120)\n"
            "plt.show()\n"
            f"print('Plot saved to {_LABS_PLOT_FILES['plot']}')\n"
        ),
        "Physics: Projectile": (
            "import numpy as np\n"
            "import matplotlib.pyplot as plt\n\n"
            "g = 9.80665  # m/s²\n"
            "v0 = 50      # m/s initial speed\n"
            "angles = [15, 30, 45, 60, 75]  # degrees\n\n"
            "fig, ax = plt.subplots(figsize=(9, 5))\n"
            "for theta in angles:\n"
            "    rad = np.radians(theta)\n"
            "    t_flight = 2 * v0 * np.sin(rad) / g\n"
            "    t = np.linspace(0, t_flight, 300)\n"
            "    x = v0 * np.cos(rad) * t\n"
            "    y = v0 * np.sin(rad) * t - 0.5 * g * t**2\n"
            "    ax.plot(x, y, label=f'{theta}°')\n"
            "ax.set_title('Projectile trajectories (v₀ = 50 m/s)')\n"
            "ax.set_xlabel('Range (m)')\n"
            "ax.set_ylabel('Height (m)')\n"
            "ax.legend()\n"
            "ax.grid(True, alpha=0.3)\n"
            "plt.tight_layout()\n"
            f"plt.savefig({_labs_plot_literal('projectile')}, dpi=120)\n"
            "plt.show()\n"
            'print("Done.")\n'
        ),
        "Physics: Wave superposition": (
            "import numpy as np\n"
            "import matplotlib.pyplot as plt\n\n"
            "x = np.linspace(0, 4 * np.pi, 1000)\n"
            "y1 = np.sin(x)\n"
            "y2 = 0.5 * np.sin(2 * x + np.pi / 3)\n"
            "y3 = 0.3 * np.sin(3 * x)\n"
            "total = y1 + y2 + y3\n\n"
            "fig, axes = plt.subplots(4, 1, figsize=(10, 8), sharex=True)\n"
            "for ax, y, lbl in zip(axes, [y1, y2, y3, total],\n"
            "                      ['y₁=sin(x)', 'y₂=0.5sin(2x+π/3)', 'y₃=0.3sin(3x)', 'Superposition']):\n"
            "    ax.plot(x, y, linewidth=1.5)\n"
            "    ax.set_ylabel(lbl, fontsize=8)\n"
            "    ax.grid(True, alpha=0.3)\n"
            "plt.suptitle('Wave Superposition')\n"
            "plt.tight_layout()\n"
            f"plt.savefig({_labs_plot_literal('waves')}, dpi=120)\n"
            "plt.show()\n"
        ),
    }

    def __init__(self, eli_callback=None, parent=None):
        super().__init__(parent)
        self._eli = eli_callback
        self._current_file: Optional[Path] = None
        self._runner: Optional[_CodeRunnerThread] = None
        self._build_ui()
        # Phase 4 hook: expose the editor buffer so a project's Save/Resume can
        # capture and restore the Sim-IDE's working code.
        try:
            from eli.runtime.state_providers import register as _reg_state
            _reg_state("sim_ide_code", self._get_code, self._set_code)
        except Exception:
            pass

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # ── Toolbar ───────────────────────────────────────────────────────
        tb = QHBoxLayout()
        self._file_label = QLabel("Unsaved")
        self._file_label.setStyleSheet("color:#7d8ba2;")
        tb.addWidget(self._file_label)
        tb.addStretch()

        starter_combo = QComboBox()
        starter_combo.addItems(list(self._STARTERS))
        starter_combo.currentTextChanged.connect(self._load_starter)
        tb.addWidget(QLabel("Starter:"))
        tb.addWidget(starter_combo)

        for lbl, slot in [("New", self._new), ("Open", self._open), ("Save", self._save),
                           ("Save As", self._save_as)]:
            btn = QPushButton(lbl)
            btn.clicked.connect(slot)
            tb.addWidget(btn)

        self._run_btn = QPushButton("▶ Run")
        self._run_btn.setStyleSheet("background:#2d7d46;color:white;font-weight:bold;padding:4px 12px;")
        self._run_btn.setToolTip("Runs code with full process privileges — no sandbox. Only run code you trust.")
        self._run_btn.clicked.connect(self._run_code)
        tb.addWidget(self._run_btn)

        if self._eli:
            fix_btn = QPushButton("ELI: Fix errors")
            fix_btn.clicked.connect(self._ask_eli_fix)
            tb.addWidget(fix_btn)
            explain_btn = QPushButton("ELI: Explain")
            explain_btn.clicked.connect(self._ask_eli_explain)
            tb.addWidget(explain_btn)

        layout.addLayout(tb)

        # ── Splitter: editor | output ─────────────────────────────────────
        v_splitter = QSplitter(Qt.Orientation.Vertical)
        layout.addWidget(v_splitter)

        h_splitter = QSplitter(Qt.Orientation.Horizontal)

        # Editor
        editor_widget = QWidget()
        ev = QVBoxLayout(editor_widget)
        ev.setContentsMargins(0, 0, 0, 0)

        if _QSCI:
            self._editor = QsciScintilla()
            lexer = QsciLexerPython(self._editor)
            lexer.setDefaultFont(QFont("Courier New", 10))
            self._editor.setLexer(lexer)
            self._editor.setMarginType(0, QsciScintilla.MarginType.NumberMargin)
            self._editor.setMarginWidth(0, "00000")
            self._editor.setTabWidth(4)
            self._editor.setIndentationsUseTabs(False)
            self._editor.setAutoIndent(True)
            self._editor.setBraceMatching(QsciScintilla.BraceMatch.SloppyBraceMatch)
            self._editor.setCaretLineVisible(True)
            self._editor.setCaretLineBackgroundColor(QColor("#1e2a3a"))
            self._editor.setAutoCompletionSource(QsciScintilla.AutoCompletionSource.AcsAll)
            self._editor.setAutoCompletionThreshold(2)
            self._editor.setFolding(QsciScintilla.FoldStyle.BoxedTreeFoldStyle)
        else:
            # Native full editor — QScintilla has no PySide6 build.
            from eli.gui.code_editor import PyCodeEditor
            self._editor = PyCodeEditor()

        ev.addWidget(self._editor)
        h_splitter.addWidget(editor_widget)

        # Plot panel (right)
        plot_panel = QWidget()
        pv = QVBoxLayout(plot_panel)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.addWidget(QLabel("Plot Output"))

        if _MPL:
            self._fig = Figure(figsize=(5, 4), tight_layout=True)
            self._canvas = FigureCanvas(self._fig)
            self._canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
            pv.addWidget(self._canvas)
            refresh_plot_btn = QPushButton(f"Refresh plot from {_LABS_PLOT_FILES['plot']}")
            refresh_plot_btn.clicked.connect(self._refresh_plot)
            pv.addWidget(refresh_plot_btn)
        else:
            pv.addWidget(QLabel("(matplotlib not available — install to see inline plots)"))
            self._canvas = None

        h_splitter.addWidget(plot_panel)
        h_splitter.setSizes([600, 400])
        v_splitter.addWidget(h_splitter)

        # Console
        console_group = QGroupBox("Console")
        cv = QVBoxLayout(console_group)
        self._console = QTextEdit()
        self._console.setReadOnly(True)
        self._console.setFont(QFont("Courier New", 9))
        self._console.setMaximumHeight(180)
        cv.addWidget(self._console)
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self._console.clear)
        cv.addWidget(clear_btn)
        v_splitter.addWidget(console_group)
        v_splitter.setSizes([500, 200])

        # ELI response panel
        if self._eli:
            self._eli_panel = QTextEdit()
            self._eli_panel.setReadOnly(True)
            self._eli_panel.setMaximumHeight(160)
            self._eli_panel.setPlaceholderText("ELI responses will appear here…")
            layout.addWidget(QLabel("ELI:"))
            layout.addWidget(self._eli_panel)

    # ── Editor helpers ────────────────────────────────────────────────────

    def _get_code(self) -> str:
        if _QSCI:
            return self._editor.text()
        return self._editor.toPlainText()

    def _set_code(self, text: str):
        if _QSCI:
            self._editor.setText(text)
        else:
            self._editor.setPlainText(text)
        # Clear stale plot so previous run's image doesn't persist
        if _MPL and getattr(self, "_canvas", None) and getattr(self, "_fig", None):
            try:
                self._fig.clear()
                self._canvas.draw()
            except Exception:
                pass
        # Delete cached plot files so "Refresh plot" doesn't show the old image
        try:
            from pathlib import Path as _P
            for _pf in _LABS_PLOT_FILES.values():
                _fp = _P(_pf)
                if _fp.exists():
                    _fp.unlink(missing_ok=True)
        except Exception:
            pass

    def _load_starter(self, name: str):
        code = self._STARTERS.get(name, "")
        if code and not self._get_code().strip():
            self._set_code(code)

    def _new(self):
        self._set_code("")
        self._current_file = None
        self._file_label.setText("Unsaved")

    def _open(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open File", "",
                                              "Python (*.py);;All files (*)")
        if path:
            try:
                self._set_code(Path(path).read_text(encoding="utf-8", errors="replace"))
                self._current_file = Path(path)
                self._file_label.setText(path)
            except Exception as ex:
                QMessageBox.critical(self, "Open Error", str(ex))

    def _save(self):
        if not self._current_file:
            self._save_as()
            return
        try:
            self._current_file.write_text(self._get_code(), encoding="utf-8")
            self._file_label.setText(str(self._current_file))
        except Exception as ex:
            QMessageBox.critical(self, "Save Error", str(ex))

    def _save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save File", "",
                                              "Python (*.py);;All files (*)")
        if path:
            self._current_file = Path(path)
            self._save()

    def _run_code(self):
        code = self._get_code()
        if not code.strip():
            return
        self._console.clear()
        self._console.append("Running…\n")
        self._run_btn.setEnabled(False)
        cwd = str(self._current_file.parent) if self._current_file else str(Path.home())
        self._runner = _CodeRunnerThread(code, cwd)
        self._runner.finished.connect(self._on_run_done)
        self._runner.start()

    def _on_run_done(self, stdout: str, stderr: str):
        self._run_btn.setEnabled(True)
        if stdout:
            self._console.append(stdout)
        if stderr:
            self._console.append(f"\n[stderr]\n{stderr}")
        self._refresh_plot()

    def _refresh_plot(self):
        if not _MPL or not self._canvas:
            return
        for path_candidate in _LABS_PLOT_FILES.values():
            p = Path(path_candidate)
            if p.exists():
                try:
                    img = matplotlib.image.imread(str(p))
                    self._fig.clear()
                    ax = self._fig.add_subplot(111)
                    ax.imshow(img)
                    ax.axis("off")
                    self._canvas.draw()
                    return
                except Exception:
                    pass

    def _ask_eli_fix(self):
        code = self._get_code().strip()
        stderr = self._console.toPlainText().strip()
        if not self._eli:
            return
        if not code and not stderr:
            self._set_eli_panel("Write some code first, then click Fix.")
            return
        prompt = (f"Fix the following Python code. Errors:\n```\n{stderr[-500:]}\n```\n\n"
                  f"Code:\n```python\n{code[:3000]}\n```\n\nReturn only the corrected code.")
        self._run_eli(prompt)

    def _ask_eli_explain(self):
        code = self._get_code().strip()
        if not self._eli:
            return
        if not code:
            self._set_eli_panel("Write some code first, then click Explain.")
            return
        prompt = f"Explain what this Python code does, step by step:\n```python\n{code[:3000]}\n```"
        self._run_eli(prompt)

    def _run_eli(self, prompt: str):
        def _thread():
            try:
                ans = self._eli(prompt)
                self._set_eli_panel(ans)
            except Exception as ex:
                self._set_eli_panel(f"[ELI error: {ex}]")
        threading.Thread(target=_thread, daemon=True).start()

    def _set_eli_panel(self, text: str):
        try:
            if hasattr(self, "_eli_panel"):
                self._eli_panel.setPlainText(text)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# Orchestration sub-tab — read-only view of the agent DAG + last RunReport
# ═══════════════════════════════════════════════════════════════════════════

"""ELI Labs — Test-review tab

Extracted verbatim from the former eli/gui/labs_tab.py monolith (strangler
refactor, v3 core-modularisation). Shared preamble — Qt binding, plot paths,
physics tables — lives in eli.gui.labs._common.
"""
from __future__ import annotations
from eli.gui.labs._common import *  # noqa: F401,F403

from eli.gui.labs.bg_worker import _BgWorker  # noqa: F401


class _TestReviewTab(QWidget):
    """Run the full test/project suite, then: ELI summarises the results, the prior
    report is backed up + an errors file is written, and result-driven options are
    offered as buttons that hand off to ELI in chat."""

    def __init__(self, eli_callback=None, chat_callback=None, parent=None):
        super().__init__(parent)
        self._eli = eli_callback          # synchronous ask → returns text (in-panel)
        self._chat = chat_callback        # submit to the MAIN chat panel (streams there)
        self._workers = []        # keep QThread refs alive
        self._last = None
        self._fail_modules = []   # row index → {node, module, message, findings}
        from PySide6.QtWidgets import (QVBoxLayout, QHBoxLayout, QPushButton, QTextEdit,
                                       QLabel, QSplitter, QTableWidget, QHeaderView,
                                       QWidget as _QW)
        from PySide6.QtCore import Qt
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Run the suite → pass/fail summary, a table of failing tests, and "
                             "click-to-fix. Prior report backed up + errors file written."))
        row = QHBoxLayout()
        self._run_btn = QPushButton("▶ Run full suite & review")
        self._run_btn.clicked.connect(lambda: self._start("tests/"))
        row.addWidget(self._run_btn)
        self._quick_btn = QPushButton("⚡ Quick review")
        self._quick_btn.clicked.connect(lambda: self._start("tests/claims/test_structural_claims.py"))
        row.addWidget(self._quick_btn); row.addStretch(1)
        lay.addLayout(row)

        # Coloured pass/fail summary bar.
        self._summary = QLabel("No run yet.")
        self._summary.setStyleSheet("padding:5px;font-weight:bold;border-radius:4px;"
                                    "background:#2e3440;color:#d8dee9;")
        lay.addWidget(self._summary)

        # Split: failing-test table | detail + Fix button.
        split = QSplitter(Qt.Orientation.Horizontal if hasattr(Qt, "Orientation") else Qt.Horizontal)
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Failing test", "Finding"])
        try:
            self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        except Exception:
            pass
        self._table.itemSelectionChanged.connect(self._on_row_selected)
        split.addWidget(self._table)

        right = _QW(); rl = QVBoxLayout(right)
        self._detail = QTextEdit(); self._detail.setReadOnly(True)
        self._detail.setStyleSheet("font-family:monospace;")
        self._detail.setPlaceholderText("Select a failing test to see its message + examiner finding.")
        rl.addWidget(self._detail)
        self._fix_btn = QPushButton("🔧 Fix this with the coding agent")
        self._fix_btn.setEnabled(False)
        self._fix_btn.clicked.connect(self._fix_selected)
        rl.addWidget(self._fix_btn)
        split.addWidget(right); split.setSizes([430, 450])
        lay.addWidget(split, 1)

        # ELI natural-language summary + result-driven option buttons.
        self._eli_summary = QLabel(""); self._eli_summary.setWordWrap(True)
        self._eli_summary.setStyleSheet("padding:4px;color:#cdd6f4;")
        lay.addWidget(self._eli_summary)
        self._opts_host = _QW(); self._opts_layout = QHBoxLayout(self._opts_host)
        lay.addWidget(self._opts_host)

    def _set_busy(self, busy: bool):
        self._run_btn.setEnabled(not busy)
        self._quick_btn.setEnabled(not busy)

    def _clear_options(self):
        while self._opts_layout.count():
            it = self._opts_layout.takeAt(0)
            w = it.widget()
            if w:
                w.deleteLater()

    def _start(self, target: str):
        self._set_busy(True)
        self._clear_options()
        self._summary.setStyleSheet("padding:5px;font-weight:bold;border-radius:4px;"
                                    "background:#2e3440;color:#d8dee9;")
        self._summary.setText(f"Running {target} … (full suite can take a few minutes)")
        self._table.setRowCount(0); self._detail.clear(); self._fix_btn.setEnabled(False)
        self._eli_summary.setText("")
        from eli.runtime.test_review import run_and_review
        w = _BgWorker(lambda: run_and_review(target))
        w.done.connect(self._on_run_done)
        self._workers.append(w)
        w.start()

    @staticmethod
    def _module_of(node: str) -> str:
        # tests/foo/test_bar.py::test_x  →  tests/foo/test_bar.py
        return str(node or "").split("::", 1)[0]

    def _on_run_done(self, res):
        self._set_busy(False)
        if not isinstance(res, dict) or res.get("__error__") or res.get("error"):
            msg = (res.get("__error__") or res.get("error")) if isinstance(res, dict) else res
            self._summary.setStyleSheet("padding:5px;font-weight:bold;border-radius:4px;"
                                        "background:#bf616a;color:#1a1f2e;")
            self._summary.setText(f"Test run failed: {msg}")
            return
        self._last = res
        t = res.get("totals", {})
        failed = int(t.get("failed", 0)) + int(t.get("errored", 0))
        bg = "#a3be8c" if failed == 0 else "#bf616a"
        self._summary.setStyleSheet(f"padding:5px;font-weight:bold;border-radius:4px;"
                                    f"background:{bg};color:#1a1f2e;")
        self._summary.setText(
            f"{t.get('passed',0)} passed · {t.get('failed',0)} failed · "
            f"{t.get('errored',0)} errored · {t.get('xfailed',0)} known-gap of {t.get('total',0)}"
            + ("   ✅ all green" if failed == 0 else "   ❌ needs attention"))

        # Map failing module → code-examiner findings.
        fa = res.get("fix_analysis") or {}
        find_by_mod = {p.get("module"): (p.get("findings") or []) for p in fa.get("proposals", [])}

        from PySide6.QtWidgets import QTableWidgetItem
        fails = res.get("failures", [])
        self._table.setRowCount(len(fails))
        self._fail_modules = []
        for r, f in enumerate(fails):
            node = f.get("node", "?"); mod = self._module_of(node)
            finds = find_by_mod.get(mod) or []
            self._fail_modules.append({"node": node, "module": mod,
                                       "message": f.get("message") or "", "findings": finds})
            self._table.setItem(r, 0, QTableWidgetItem(node))
            tag = (f"{finds[0].get('kind')}: {finds[0].get('message')}" if finds else "—")
            self._table.setItem(r, 1, QTableWidgetItem(str(tag)[:80]))
        self._detail.clear(); self._fix_btn.setEnabled(False)
        if not fails:
            self._detail.setPlainText("No failing tests. 🎉")

        # Result-driven option buttons (unchanged behaviour) + backups note.
        self._clear_options()
        from PySide6.QtWidgets import QPushButton
        for opt in res.get("options", []):
            b = QPushButton(opt.get("label", opt.get("id", "option")))
            b.clicked.connect(lambda _=False, cmd=opt.get("command", ""): self._send(cmd))
            self._opts_layout.addWidget(b)
        self._opts_layout.addStretch(1)
        if res.get("backup_path") or res.get("error_file"):
            self._eli_summary.setText(f"Prior report → {res.get('backup_path','?')}   |   "
                                      f"Errors → {res.get('error_file','?')}")
        self._summarise(res)

    def _on_row_selected(self):
        idx = self._table.currentRow()
        if idx is None or idx < 0 or idx >= len(self._fail_modules):
            return
        d = self._fail_modules[idx]
        lines = [f"Test:   {d['node']}", f"Module: {d['module']}", ""]
        if d["message"]:
            lines += ["── failure message ──", d["message"][:2000], ""]
        if d["findings"]:
            lines.append("── code-examiner findings ──")
            for fn in d["findings"][:8]:
                loc = f":{fn.get('line')}" if fn.get("line") else ""
                lines.append(f"  {d['module']}{loc} [{fn.get('kind')}] {fn.get('message')}")
        else:
            lines.append("(no static examiner findings for this module)")
        self._detail.setPlainText("\n".join(lines))
        self._fix_btn.setEnabled(True)

    def _fix_selected(self):
        idx = self._table.currentRow()
        if idx is None or idx < 0 or idx >= len(self._fail_modules):
            return
        d = self._fail_modules[idx]
        # Hand the failing module to ELI's coding agent (examine → propose → fix).
        self._send(f"examine {d['module']} for errors and fix the bug behind the failing "
                   f"test {d['node']}")

    def _summarise(self, res):
        if not callable(self._eli):
            return
        t = res.get("totals", {})
        fails = "\n".join(f"- {f['node']}: {(f.get('message') or '')[:120]}"
                          for f in res.get("failures", [])[:15]) or "(none)"
        prompt = ("Summarise these test results in a few sentences and say what's most worth "
                  f"doing next.\nTotals: {t}\nFailing:\n{fails}")
        w = _BgWorker(lambda: (self._eli(prompt) or ""))
        w.done.connect(self._on_summary)
        self._workers.append(w)
        w.start()

    def _on_summary(self, text):
        if isinstance(text, dict):
            return
        self._eli_summary.setText("ELI: " + str(text))

    def _send(self, cmd: str):
        """Hand a command off to ELI — prefer the MAIN chat panel (streams into the
        conversation + switches to Chat); fall back to an in-panel reply."""
        if not cmd:
            return
        if callable(self._chat):
            try:
                self._chat(cmd)
                return
            except Exception:
                pass
        if callable(self._eli):
            w = _BgWorker(lambda: (self._eli(cmd) or ""))
            w.done.connect(self._on_summary)
            self._workers.append(w)
            w.start()


# ═══════════════════════════════════════════════════════════════════════════
# Top-level Labs tab
# ═══════════════════════════════════════════════════════════════════════════

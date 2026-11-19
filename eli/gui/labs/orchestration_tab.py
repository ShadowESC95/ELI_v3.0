"""ELI Labs — Orchestration tab

Extracted verbatim from the former eli/gui/labs_tab.py monolith (strangler
refactor, v3 core-modularisation). Shared preamble — Qt binding, plot paths,
physics tables — lives in eli.gui.labs._common.
"""
from __future__ import annotations
from eli.gui.labs._common import *  # noqa: F401,F403


class _OrchestrationTab(QWidget):
    """Visual view of the agent DAG: agents laid out by execution layer and colour-coded
    by the last dispatch's result, a plain-English summary of the last turn, the
    dependencies, and the per-node trace. Auto-refreshes so the last-turn view stays
    current; 'Refresh' re-reads immediately. This is an explainability view — it shows
    WHICH agents ran and how the last answer was grounded, not just static wiring."""

    _OK = "#a3be8c"; _FAIL = "#bf616a"; _SKIP = "#5c6370"; _IDLE = "#4c566a"
    _STATUS = {"ok": _OK, "success": _OK, "done": _OK, "completed": _OK, "passed": _OK,
               "failed": _FAIL, "error": _FAIL, "errored": _FAIL,
               "skipped": _SKIP, "skip": _SKIP}

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        hdr = QLabel("🧭 Agent DAG — live wiring + last-turn trace")
        hdr.setStyleSheet("font-weight:bold;padding:2px;")
        lay.addWidget(hdr)
        self._summary = QLabel("(no dispatch recorded yet — talk to ELI first)")
        self._summary.setWordWrap(True)
        self._summary.setStyleSheet("padding:4px 2px;color:#d8dee9;")
        lay.addWidget(self._summary)
        self._view = QTextEdit()
        self._view.setReadOnly(True)
        lay.addWidget(self._view)
        row = QHBoxLayout()
        btn = QPushButton("↻ Refresh")
        btn.clicked.connect(self.refresh)
        row.addWidget(btn); row.addStretch(1)
        lay.addLayout(row)
        try:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.refresh)
            self._timer.start(3000)
        except Exception:
            pass
        self.refresh()

    def _chip(self, label, color):
        return (f'<span style="background:{color};color:#1a1f2e;padding:2px 9px;'
                f'border-radius:9px;font-weight:bold;">{label}</span>&nbsp;')

    def refresh(self):
        try:
            from eli.cognition.agent_bus import orchestration_snapshot
            snap = orchestration_snapshot()
        except Exception as e:
            self._view.setHtml(f"<p style='color:#bf616a'>orchestration unavailable: {e}</p>")
            return
        last = snap.get("last_run") or {}
        nodes = last.get("nodes") or {}

        if last:
            self._summary.setText(
                f"Last turn: {snap.get('count')} agents across {snap.get('critical_path')} "
                f"layer(s) — {'completed OK' if last.get('ok') else 'completed with issues'} "
                f"in {last.get('seconds')}s "
                f"({last.get('failed', 0)} failed, {last.get('skipped', 0)} skipped).")
        else:
            self._summary.setText("(no dispatch recorded yet — talk to ELI, then Refresh)")

        h = ["<div style='font-family:sans-serif;font-size:12px;'>",
             f"<b>Engine:</b> {snap.get('engine')} &nbsp;|&nbsp; "
             f"<b>Agents:</b> {snap.get('count')} &nbsp;|&nbsp; "
             f"<b>Critical path:</b> {snap.get('critical_path')} layer(s)<br><br>",
             "<b>Execution layers</b> (agents in a layer run in parallel; colour = last run):<br>"]
        for i, layer in enumerate(snap.get("execution_layers") or []):
            h.append(f"<div style='margin:5px 0;'><span style='color:#81a1c1;'>layer {i} →</span>&nbsp; ")
            for agent in layer:
                st = str((nodes.get(agent) or {}).get("status", "")).lower()
                h.append(self._chip(agent, self._STATUS.get(st, self._IDLE)))
            h.append("</div>")
        h.append("<br><span style='font-size:11px;color:#9aa5b1;'>"
                 + self._chip("ok", self._OK) + "ran ok &nbsp;&nbsp;"
                 + self._chip("failed", self._FAIL) + "failed &nbsp;&nbsp;"
                 + self._chip("skipped", self._SKIP) + "skipped &nbsp;&nbsp;"
                 + self._chip("idle", self._IDLE) + "not run last turn</span>")
        deps = snap.get("dependencies") or {}
        if deps:
            h.append("<br><br><b>Dependencies:</b><br>")
            for k, v in deps.items():
                h.append(f"&nbsp;&nbsp;{k} &nbsp;⟵&nbsp; {', '.join(v)}<br>")
        if nodes:
            h.append("<br><b>Last dispatch — per agent:</b>"
                     "<table cellspacing='0' cellpadding='3' style='font-size:11px;'>"
                     "<tr style='color:#81a1c1;'><td>agent</td><td>status</td>"
                     "<td>time</td><td>attempts</td></tr>")
            for nid in sorted(nodes):
                nd = nodes[nid]; st = str(nd.get("status", "")).lower()
                h.append(f"<tr><td>{nid}</td>"
                         f"<td style='color:{self._STATUS.get(st, '#d8dee9')};'>{nd.get('status')}</td>"
                         f"<td>{nd.get('seconds')}s</td><td>{nd.get('attempts')}</td></tr>")
            h.append("</table>")
        h.append("</div>")
        self._view.setHtml("".join(h))


# ═══════════════════════════════════════════════════════════════════════════
# Test & Review sub-tab — full suite run → LLM summary → backups → option chat
# ═══════════════════════════════════════════════════════════════════════════

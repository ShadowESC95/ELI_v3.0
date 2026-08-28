"""ELI Labs — Memory & Conversations tab

Extracted verbatim from the former eli/gui/labs_tab.py monolith (strangler
refactor, v3 core-modularisation). Shared preamble — Qt binding, plot paths,
physics tables — lives in eli.gui.labs._common.
"""
from __future__ import annotations
from eli.gui.labs._common import *  # noqa: F401,F403


class _MemoryAndConversationsTab(QWidget):
    """Unified Memory & Conversations panel.

    Replaces the old separate "Conversations" and "ELI Memory" sub-tabs.
    The left side filters between conversation turns (SQLite
    `conversation_turns` table) and ELI long-term memories (memory adapter).
    The right side shows the selected entry and lets the user store a new
    memory directly. Both data sources are live-queried, so this tab always
    reflects the canonical artifacts/db/user.sqlite3.
    """

    SOURCE_CONVERSATIONS = "Conversations"
    SOURCE_MEMORIES = "ELI Memory"

    def __init__(self, memory_adapter=None, db_path: Optional[str] = None, parent=None):
        super().__init__(parent)
        self._mem = memory_adapter
        self._db_path = db_path
        self._rows_data: List[Dict[str, Any]] = []
        self._build_ui()
        self._load()

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)

        # Top control bar
        top = QHBoxLayout()
        top.addWidget(QLabel("Source:"))
        self._source = QComboBox()
        self._source.addItems([self.SOURCE_CONVERSATIONS, self.SOURCE_MEMORIES])
        self._source.currentTextChanged.connect(self._load)
        top.addWidget(self._source)

        top.addWidget(QLabel("Search:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Keyword filter (Enter to apply)…")
        self._search.returnPressed.connect(self._load)
        top.addWidget(self._search, stretch=1)

        self._kind_filter = QComboBox()
        self._kind_filter.addItems(["all kinds", "memory", "project", "note", "preference",
                                    "user", "assistant", "system"])
        self._kind_filter.currentTextChanged.connect(self._load)
        top.addWidget(QLabel("Kind/Role:"))
        top.addWidget(self._kind_filter)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._load)
        top.addWidget(refresh_btn)

        export_btn = QPushButton("Export…")
        export_btn.clicked.connect(self._export_visible)
        top.addWidget(export_btn)

        root.addLayout(top)

        # Splitter: list / detail
        splitter = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(splitter, stretch=1)

        # Left: results table
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Timestamp", "Source", "Kind/Role", "Snippet"])
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.cellClicked.connect(self._on_row_clicked)
        splitter.addWidget(self._table)

        # Right: detail + store
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)

        self._detail = QTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText("Select a row to see the full entry.")
        rv.addWidget(self._detail, stretch=1)

        audit_btn = QPushButton("Why did ELI believe this?")
        audit_btn.setToolTip("Show stored memories and evidence events related to the selected entry.")
        audit_btn.clicked.connect(self._audit_selection)
        rv.addWidget(audit_btn)

        store_group = QGroupBox("Store new memory")
        sv = QVBoxLayout(store_group)
        self._store_text = QTextEdit()
        self._store_text.setMaximumHeight(80)
        self._store_text.setPlaceholderText("Type a fact for ELI to remember…")
        sv.addWidget(self._store_text)
        sh = QHBoxLayout()
        self._store_kind = QComboBox()
        self._store_kind.addItems(["memory", "project", "note", "preference"])
        sh.addWidget(QLabel("Kind:"))
        sh.addWidget(self._store_kind)
        sh.addStretch()
        store_btn = QPushButton("Store")
        store_btn.clicked.connect(self._store)
        sh.addWidget(store_btn)
        sv.addLayout(sh)
        rv.addWidget(store_group)

        splitter.addWidget(right)
        splitter.setSizes([520, 380])

    # ── Data loading ──────────────────────────────────────────────────────
    def _load(self, *_):
        keyword = self._search.text().strip().lower()
        kind = self._kind_filter.currentText()
        kind = "" if kind == "all kinds" else kind

        if self._source.currentText() == self.SOURCE_CONVERSATIONS:
            rows = self._fetch_conversations(keyword, kind)
        else:
            rows = self._fetch_memories(keyword, kind)

        self._rows_data = rows
        self._table.setRowCount(0)
        for r in rows:
            i = self._table.rowCount()
            self._table.insertRow(i)
            self._table.setItem(i, 0, QTableWidgetItem(str(r.get("ts", ""))[:19]))
            self._table.setItem(i, 1, QTableWidgetItem(str(r.get("source", ""))))
            self._table.setItem(i, 2, QTableWidgetItem(str(r.get("kind", ""))))
            snippet = (r.get("text", "") or "").replace("\n", " ")[:160]
            self._table.setItem(i, 3, QTableWidgetItem(snippet))
        self._table.resizeColumnToContents(0)
        self._table.resizeColumnToContents(1)
        self._table.resizeColumnToContents(2)

    def _fetch_conversations(self, keyword: str, role: str) -> List[Dict[str, Any]]:
        try:
            import sqlite3
            from eli.core.paths import user_db_path
            db = self._db_path or str(user_db_path())
            conn = sqlite3.connect(db)
            try:
                cols = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(conversation_turns)").fetchall()
                    if len(row) > 1
                }
                role_expr = "role" if "role" in cols else "''"
                content_expr = "content" if "content" in cols else (
                    "text" if "text" in cols else "message" if "message" in cols else "''"
                )
                params: List[Any] = []
                where: List[str] = []
                if keyword and content_expr != "''":
                    where.append(f"LOWER({content_expr}) LIKE ?")
                    params.append(f"%{keyword}%")
                if role and role_expr != "''":
                    where.append(f"LOWER({role_expr}) = LOWER(?)")
                    params.append(role)
                clause = ("WHERE " + " AND ".join(where)) if where else ""
                ts_col = next(
                    (c for c in ("created_at", "timestamp", "ts", "time", "updated_at") if c in cols),
                    None,
                )
                ts_expr = ts_col if ts_col else ("id" if "id" in cols else "rowid")
                order_expr = "id" if "id" in cols else ts_expr
                rows = conn.execute(
                    f"SELECT {ts_expr}, {role_expr}, {content_expr} FROM conversation_turns "
                    f"{clause} ORDER BY {order_expr} DESC LIMIT 500",
                    params,
                ).fetchall()
            finally:
                conn.close()
            return [
                {"ts": str(ts or ""), "source": "convo", "kind": str(role or ""),
                 "text": str(content or ""), "tags": ""}
                for ts, role, content in rows
            ]
        except Exception as ex:
            return [{"ts": "", "source": "convo", "kind": "error",
                     "text": f"Conversations query failed: {ex}", "tags": ""}]

    def _fetch_memories(self, keyword: str, kind: str) -> List[Dict[str, Any]]:
        if not self._mem:
            return [{"ts": "", "source": "memory", "kind": "error",
                     "text": "Memory adapter not available.", "tags": ""}]
        try:
            if keyword:
                results = self._mem.search(keyword, limit=200) or []
            elif hasattr(self._mem, "get_recent"):
                results = self._mem.get_recent(limit=200) or []
            else:
                results = self._mem.search("", limit=200) or []
            out: List[Dict[str, Any]] = []
            for r in results:
                if not isinstance(r, dict):
                    continue
                if kind and (str(r.get("kind", "")).lower() != kind.lower()):
                    continue
                out.append({
                    "ts": str(r.get("timestamp", r.get("ts", "")))[:19],
                    "source": "memory",
                    "kind": str(r.get("kind", "")),
                    "text": str(r.get("text", "")),
                    "tags": str(r.get("tags", "")),
                })
            return out
        except Exception as ex:
            return [{"ts": "", "source": "memory", "kind": "error",
                     "text": f"Memory query failed: {ex}", "tags": ""}]

    def _on_row_clicked(self, row: int, _col: int):
        if 0 <= row < len(self._rows_data):
            r = self._rows_data[row]
            lines = [
                f"Source: {r.get('source','')}",
                f"Timestamp: {r.get('ts','')}",
                f"Kind/Role: {r.get('kind','')}",
            ]
            if r.get("tags"):
                lines.append(f"Tags: {r.get('tags','')}")
            lines.append("")
            lines.append(r.get("text", ""))
            self._detail.setPlainText("\n".join(lines))

    def _audit_selection(self):
        row = self._table.currentRow()
        if row < 0 or row >= len(self._rows_data):
            QMessageBox.information(self, "Memory audit", "Select a row first.")
            return
        query = str(self._rows_data[row].get("text") or "").strip()
        if not query:
            QMessageBox.information(self, "Memory audit", "Selected entry has no text to audit.")
            return
        try:
            from eli.runtime.personal_memory_render import render_belief_audit
            report = render_belief_audit(query[:240])
        except Exception as ex:
            QMessageBox.warning(self, "Memory audit", str(ex))
            return
        self._detail.setPlainText(report)

    def _store(self):
        text = self._store_text.toPlainText().strip()
        if not text or not self._mem:
            QMessageBox.warning(self, "Memory", "Memory adapter unavailable or text empty.")
            return
        try:
            self._mem.store(text=text, kind=self._store_kind.currentText())
            self._store_text.clear()
            if self._source.currentText() == self.SOURCE_MEMORIES:
                self._load()
        except Exception as ex:
            QMessageBox.warning(self, "Memory store error", str(ex))

    def _export_visible(self):
        if not self._rows_data:
            QMessageBox.information(self, "Export", "Nothing to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Export visible rows", "memory_export.jsonl",
            "JSON Lines (*.jsonl);;CSV (*.csv);;Markdown (*.md)"
        )
        if not path:
            return
        try:
            p = Path(path)
            if p.suffix.lower() == ".csv":
                import csv
                with p.open("w", encoding="utf-8", newline="") as fh:
                    w = csv.writer(fh)
                    w.writerow(["timestamp", "source", "kind", "tags", "text"])
                    for r in self._rows_data:
                        w.writerow([r.get("ts", ""), r.get("source", ""),
                                    r.get("kind", ""), r.get("tags", ""),
                                    r.get("text", "")])
            elif p.suffix.lower() == ".md":
                lines = ["# Memory & Conversations export", ""]
                for r in self._rows_data:
                    lines.append(f"## [{r.get('source','')}] {r.get('ts','')} — {r.get('kind','')}")
                    if r.get("tags"):
                        lines.append(f"_tags: {r.get('tags','')}_")
                    lines.append("")
                    lines.append(r.get("text", ""))
                    lines.append("")
                p.write_text("\n".join(lines), encoding="utf-8")
            else:
                with p.open("w", encoding="utf-8") as fh:
                    for r in self._rows_data:
                        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            QMessageBox.information(self, "Export", f"Saved {len(self._rows_data)} rows to:\n{p}")
        except Exception as ex:
            QMessageBox.critical(self, "Export error", str(ex))


# ═══════════════════════════════════════════════════════════════════════════
# Jupyter sub-tab
# ═══════════════════════════════════════════════════════════════════════════

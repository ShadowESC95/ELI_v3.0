"""ELI Labs — Physics constants tab

Extracted verbatim from the former eli/gui/labs_tab.py monolith (strangler
refactor, v3 core-modularisation). Shared preamble — Qt binding, plot paths,
physics tables — lives in eli.gui.labs._common.
"""
from __future__ import annotations
from eli.gui.labs._common import *  # noqa: F401,F403


class _PhysicsTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        hdr = QLabel("Physics Symbols & Constants")
        hdr.setStyleSheet("font-size:12px;font-weight:bold;padding:4px;")
        layout.addWidget(hdr)

        top = QHBoxLayout()
        self._filter = QLineEdit()
        self._filter.setPlaceholderText("Filter by symbol, name, or category…")
        self._filter.textChanged.connect(self._filter_table)
        top.addWidget(self._filter)
        self._cat_combo = QComboBox()
        cats = ["All"] + sorted({row[4] for row in PHYSICS_CONSTANTS})
        self._cat_combo.addItems(cats)
        self._cat_combo.currentTextChanged.connect(self._filter_table)
        top.addWidget(self._cat_combo)
        layout.addLayout(top)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["Symbol", "Name", "Value", "Unit", "Category"])
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

        self._populate(PHYSICS_CONSTANTS)

    def _populate(self, rows):
        self._table.setRowCount(0)
        for sym, name, val, unit, cat in rows:
            r = self._table.rowCount()
            self._table.insertRow(r)
            for c, txt in enumerate([sym, name, val, unit, cat]):
                self._table.setItem(r, c, QTableWidgetItem(txt))
        self._table.resizeColumnToContents(0)
        self._table.resizeColumnToContents(4)

    def _filter_table(self):
        text = self._filter.text().lower()
        cat = self._cat_combo.currentText()
        filtered = [
            row for row in PHYSICS_CONSTANTS
            if (text in row[0].lower() or text in row[1].lower() or text in row[3].lower())
            and (cat == "All" or row[4] == cat)
        ]
        self._populate(filtered)


# ═══════════════════════════════════════════════════════════════════════════
# Scientific report generator sub-tab
# ═══════════════════════════════════════════════════════════════════════════

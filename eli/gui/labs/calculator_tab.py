"""ELI Labs — Calculator tab

Extracted verbatim from the former eli/gui/labs_tab.py monolith (strangler
refactor, v3 core-modularisation). Shared preamble — Qt binding, plot paths,
physics tables — lives in eli.gui.labs._common.
"""
from __future__ import annotations
from eli.gui.labs._common import *  # noqa: F401,F403


class _CalculatorTab(QWidget):
    _BUTTONS = [
        ["7", "8", "9", "/", "sqrt", "x²"],
        ["4", "5", "6", "*", "sin", "cos"],
        ["1", "2", "3", "-", "tan", "log"],
        ["0", ".", "e", "+", "exp", "ln"],
        ["π", "ħ", "kB", "NA", "c", "G"],
        ["eV", "me", "mp", "a0", "eps0", "mu0"],
        ["(", ")", "^", "!", "CE", "="],
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: List[str] = []
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)

        left = QWidget()
        lv = QVBoxLayout(left)

        self._display = QLineEdit()
        self._display.setFont(QFont("Courier New", 18))
        self._display.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._display.setMinimumHeight(48)
        self._display.returnPressed.connect(self._evaluate)
        lv.addWidget(self._display)

        self._result = QLabel("= ")
        self._result.setFont(QFont("Courier New", 14))
        self._result.setStyleSheet("color:#4ec9b0;padding:4px;")
        lv.addWidget(self._result)

        btn_grid = QWidget()
        grid = QGridLayout(btn_grid)
        grid.setSpacing(4)
        for r, row in enumerate(self._BUTTONS):
            for c, lbl in enumerate(row):
                btn = QPushButton(lbl)
                btn.setMinimumSize(54, 36)
                btn.clicked.connect(lambda _, t=lbl: self._btn_pressed(t))
                if lbl == "=":
                    btn.setStyleSheet("background:#2d7d46;color:white;font-weight:bold;")
                elif lbl == "CE":
                    btn.setStyleSheet("background:#7d2d2d;color:white;")
                grid.addWidget(btn, r, c)
        lv.addWidget(btn_grid)

        self._hist_list = QListWidget()
        self._hist_list.setMaximumHeight(120)
        self._hist_list.itemClicked.connect(lambda item: self._display.setText(item.text().split("=")[0].strip()))
        lv.addWidget(QLabel("History:"))
        lv.addWidget(self._hist_list)

        layout.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        rv.addWidget(QLabel("Constants reference:"))
        self._const_filter = QLineEdit()
        self._const_filter.setPlaceholderText("Filter constants…")
        self._const_filter.textChanged.connect(self._filter_constants)
        rv.addWidget(self._const_filter)
        self._const_table = QTableWidget(0, 3)
        self._const_table.setHorizontalHeaderLabels(["Symbol", "Value", "Unit"])
        self._const_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._const_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._const_table.cellDoubleClicked.connect(self._insert_constant)
        rv.addWidget(self._const_table)
        self._populate_constants(PHYSICS_CONSTANTS)
        layout.addWidget(right)

    def _populate_constants(self, rows):
        self._const_table.setRowCount(0)
        for sym, name, val, unit, *_ in rows:
            r = self._const_table.rowCount()
            self._const_table.insertRow(r)
            self._const_table.setItem(r, 0, QTableWidgetItem(sym))
            self._const_table.setItem(r, 1, QTableWidgetItem(val))
            self._const_table.setItem(r, 2, QTableWidgetItem(unit))
            self._const_table.item(r, 0).setToolTip(name)
        self._const_table.resizeColumnToContents(0)
        self._const_table.resizeColumnToContents(2)

    def _filter_constants(self, text: str):
        text = text.lower()
        filtered = [row for row in PHYSICS_CONSTANTS
                    if text in row[0].lower() or text in row[1].lower() or text in row[3].lower()]
        self._populate_constants(filtered)

    def _insert_constant(self, row: int, col: int):
        item = self._const_table.item(row, 0)
        if item:
            sym = item.text()
            # Map display symbol to eval key
            key_map = {"ħ": "hbar", "ε₀": "eps0", "μ₀": "mu0", "kB": "kB",
                       "NA": "NA", "me": "me", "mp": "mp", "a₀": "a0"}
            key = key_map.get(sym, sym)
            self._display.setText(self._display.text() + key)

    def _btn_pressed(self, lbl: str):
        cur = self._display.text()
        if lbl == "CE":
            self._display.clear()
            self._result.setText("= ")
        elif lbl == "=":
            self._evaluate()
        elif lbl == "sqrt":
            self._display.setText(cur + "sqrt(")
        elif lbl == "x²":
            self._display.setText(cur + "**2")
        elif lbl == "sin":
            self._display.setText(cur + "sin(")
        elif lbl == "cos":
            self._display.setText(cur + "cos(")
        elif lbl == "tan":
            self._display.setText(cur + "tan(")
        elif lbl == "log":
            self._display.setText(cur + "log10(")
        elif lbl == "ln":
            self._display.setText(cur + "log(")
        elif lbl == "exp":
            self._display.setText(cur + "exp(")
        elif lbl == "^":
            self._display.setText(cur + "**")
        elif lbl == "π":
            self._display.setText(cur + "pi")
        elif lbl == "e":
            self._display.setText(cur + "e")
        elif lbl == "!":
            self._display.setText(cur + "factorial(")
        else:
            self._display.setText(cur + lbl)

    def _evaluate(self):
        expr = self._display.text().strip()
        if not expr:
            return
        env = {**PHYSICS_VALUES,
               "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos,
               "tan": math.tan, "log": math.log, "log10": math.log10,
               "exp": math.exp, "factorial": math.factorial,
               "abs": abs, "round": round, "pow": pow,
               "e": math.e}
        if _NP:
            env.update({"np": np, "array": np.array, "linspace": np.linspace})
        try:
            safe_expr = expr.replace("^", "**")
            result = eval(safe_expr, {"__builtins__": {}}, env)  # noqa: S307
            self._result.setText(f"= {result}")
            entry = f"{expr} = {result}"
            self._history.insert(0, entry)
            self._hist_list.insertItem(0, entry)
        except Exception as ex:
            self._result.setText(f"Error: {ex}")


# ═══════════════════════════════════════════════════════════════════════════
# Physics constants reference sub-tab
# ═══════════════════════════════════════════════════════════════════════════

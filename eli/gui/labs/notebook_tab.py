"""ELI Labs — Notebook tab

Extracted verbatim from the former eli/gui/labs_tab.py monolith (strangler
refactor, v3 core-modularisation). Shared preamble — Qt binding, plot paths,
physics tables — lives in eli.gui.labs._common.
"""
from __future__ import annotations
from eli.gui.labs._common import *  # noqa: F401,F403


class _NotebookTab(QWidget):
    @property
    def _DATA_FILE(self) -> Path:
        from eli.core.paths import adopt_legacy_file, data_dir
        return adopt_legacy_file(data_dir() / "labs" / "labs_notebook.json", "~/.eli/labs_notebook.json")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._projects: Dict[str, str] = {}
        self._current: Optional[str] = None
        self._load()
        self._build_ui()

    def _build_ui(self):
        layout = QHBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        layout.addWidget(splitter)

        left = QWidget()
        lv = QVBoxLayout(left)
        lv.addWidget(QLabel("Projects"))
        self._project_list = QListWidget()
        self._project_list.currentTextChanged.connect(self._on_project_selected)
        lv.addWidget(self._project_list)
        btn_row = QHBoxLayout()
        new_btn = QPushButton("+ New")
        new_btn.clicked.connect(self._new_project)
        btn_row.addWidget(new_btn)
        del_btn = QPushButton("Delete")
        del_btn.clicked.connect(self._delete_project)
        btn_row.addWidget(del_btn)
        lv.addLayout(btn_row)
        splitter.addWidget(left)

        right = QWidget()
        rv = QVBoxLayout(right)
        self._note_title = QLabel("Select or create a project")
        self._note_title.setStyleSheet("font-weight:bold;font-size:14px;padding:4px;")
        rv.addWidget(self._note_title)
        self._note_editor = QTextEdit()
        self._note_editor.setPlaceholderText("Write your project notes here…")
        self._note_editor.textChanged.connect(self._autosave)
        rv.addWidget(self._note_editor)
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save)
        rv.addWidget(save_btn)
        splitter.addWidget(right)
        splitter.setSizes([200, 600])

        self._refresh_list()

    def _refresh_list(self):
        self._project_list.clear()
        for name in sorted(self._projects):
            self._project_list.addItem(name)

    def _on_project_selected(self, name: str):
        if self._current and self._projects.get(self._current) is not None:
            self._projects[self._current] = self._note_editor.toPlainText()
        self._current = name
        if name in self._projects:
            self._note_editor.setPlainText(self._projects[name])
            self._note_title.setText(name)

    def _new_project(self):
        name, ok = QInputDialog.getText(self, "New Project", "Project name:")
        if ok and name.strip():
            name = name.strip()
            if name not in self._projects:
                self._projects[name] = ""
            self._refresh_list()
            items = self._project_list.findItems(name, Qt.MatchFlag.MatchExactly)
            if items:
                self._project_list.setCurrentItem(items[0])
            self._save()

    def _delete_project(self):
        item = self._project_list.currentItem()
        if not item:
            return
        name = item.text()
        reply = QMessageBox.question(self, "Delete", f"Delete project '{name}'?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self._projects.pop(name, None)
            self._current = None
            self._note_editor.clear()
            self._note_title.setText("Select or create a project")
            self._refresh_list()
            self._save()

    def _autosave(self):
        if self._current:
            self._projects[self._current] = self._note_editor.toPlainText()

    def _save(self):
        self._autosave()
        try:
            self._DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._DATA_FILE.write_text(json.dumps(self._projects, indent=2), encoding="utf-8")
        except Exception as ex:
            log.debug(f"[Notebook] save error: {ex}")

    def _load(self):
        try:
            if self._DATA_FILE.exists():
                self._projects = json.loads(self._DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            self._projects = {}


# ═══════════════════════════════════════════════════════════════════════════
# Conversations sub-tab
# ═══════════════════════════════════════════════════════════════════════════

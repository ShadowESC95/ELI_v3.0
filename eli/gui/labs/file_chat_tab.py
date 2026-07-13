"""ELI Labs — File-chat tab

Extracted verbatim from the former eli/gui/labs_tab.py monolith (strangler
refactor, v3 core-modularisation). Shared preamble — Qt binding, plot paths,
physics tables — lives in eli.gui.labs._common.
"""
from __future__ import annotations
from eli.gui.labs._common import *  # noqa: F401,F403


class _FileChatTab(QWidget):
    def __init__(self, eli_callback=None, parent=None):
        super().__init__(parent)
        self._eli = eli_callback
        self._loaded_text = ""
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        hdr = QLabel("File / Folder Chat — load files and ask ELI about them")
        hdr.setStyleSheet("font-size:12px;font-weight:bold;padding:4px;")
        layout.addWidget(hdr)

        top = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("File or folder path…")
        top.addWidget(self._path_edit)
        file_btn = QPushButton("Open File…")
        file_btn.clicked.connect(self._open_file)
        top.addWidget(file_btn)
        folder_btn = QPushButton("Open Folder…")
        folder_btn.clicked.connect(self._open_folder)
        top.addWidget(folder_btn)
        layout.addLayout(top)

        self._file_info = QLabel("No file loaded")
        self._file_info.setStyleSheet("color:#7d8ba2;padding:2px;")
        layout.addWidget(self._file_info)

        self._preview = QTextEdit()
        self._preview.setReadOnly(True)
        self._preview.setMaximumHeight(180)
        self._preview.setFont(QFont("Courier New", 9))
        self._preview.setPlaceholderText("File contents preview…")
        layout.addWidget(self._preview)

        layout.addWidget(QLabel("Ask ELI about the loaded content:"))
        self._chat_history = QTextEdit()
        self._chat_history.setReadOnly(True)
        layout.addWidget(self._chat_history)

        bottom = QHBoxLayout()
        self._question = QLineEdit()
        self._question.setPlaceholderText("Ask something about the file…")
        self._question.returnPressed.connect(self._ask)
        bottom.addWidget(self._question)
        ask_btn = QPushButton("Ask ELI")
        ask_btn.clicked.connect(self._ask)
        bottom.addWidget(ask_btn)
        layout.addLayout(bottom)

    def _open_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open File", "", "All files (*)")
        if path:
            self._load_path(path)

    def _open_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Open Folder", "")
        if folder:
            self._load_folder(folder)

    def _load_path(self, path: str):
        p = Path(path)
        self._path_edit.setText(path)
        try:
            ext = p.suffix.lower()
            if ext in (".py", ".txt", ".md", ".json", ".csv", ".yaml", ".toml", ".sh",
                       ".js", ".ts", ".html", ".css", ".xml", ".rst", ".log", ".ini",
                       ".cfg", ".h", ".c", ".cpp", ".java"):
                text = p.read_text(encoding="utf-8", errors="replace")
            else:
                text = f"[Binary file: {p.name} — {p.stat().st_size} bytes]"
            self._loaded_text = text
            self._file_info.setText(f"Loaded: {p.name}  ({len(text):,} chars)")
            self._preview.setPlainText(text[:3000] + ("\n…(truncated)" if len(text) > 3000 else ""))
        except Exception as ex:
            self._file_info.setText(f"Error: {ex}")

    def _load_folder(self, folder: str):
        p = Path(folder)
        self._path_edit.setText(folder)
        parts = []
        for f in sorted(p.rglob("*"))[:60]:
            if f.is_file():
                parts.append(str(f.relative_to(p)))
        listing = f"Folder: {p.name}\nFiles ({len(parts)}):\n" + "\n".join(parts)
        self._loaded_text = listing
        self._file_info.setText(f"Loaded folder: {p.name}  ({len(parts)} files)")
        self._preview.setPlainText(listing[:3000])

    def _ask(self):
        question = self._question.text().strip()
        if not question:
            return
        if not self._loaded_text:
            QMessageBox.information(self, "No file", "Load a file or folder first.")
            return
        context = self._loaded_text[:4000]
        prompt = (f"The user has loaded the following file/folder content:\n\n"
                  f"```\n{context}\n```\n\n"
                  f"User question: {question}")
        self._chat_history.append(f"\n**You:** {question}\n")
        self._question.clear()
        if self._eli:
            def _run():
                try:
                    answer = self._eli(prompt)
                    self._append_answer(answer)
                except Exception as ex:
                    self._append_answer(f"[Error: {ex}]")
            threading.Thread(target=_run, daemon=True).start()
        else:
            self._chat_history.append("*[ELI not connected — load a model first]*\n")

    def _append_answer(self, text: str):
        try:
            self._chat_history.append(f"**ELI:** {text}\n")
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# Named Workspaces sub-tab
# ═══════════════════════════════════════════════════════════════════════════

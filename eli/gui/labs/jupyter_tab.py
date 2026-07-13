"""ELI Labs — Jupyter tab

Extracted verbatim from the former eli/gui/labs_tab.py monolith (strangler
refactor, v3 core-modularisation). Shared preamble — Qt binding, plot paths,
physics tables — lives in eli.gui.labs._common.
"""
from __future__ import annotations
from eli.gui.labs._common import *  # noqa: F401,F403


class _JupyterTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc: Optional[subprocess.Popen] = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        hdr = QLabel("Jupyter Lab Integration")
        hdr.setStyleSheet("font-size:12px;font-weight:bold;padding:5px;")
        layout.addWidget(hdr)

        info = QLabel(
            "Launch JupyterLab in your default browser. "
            "ELI will start the server and open the interface automatically."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QFormLayout()
        self._dir_edit = QLineEdit(str(Path.home()))
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_dir)
        dir_row = QHBoxLayout()
        dir_row.addWidget(self._dir_edit)
        dir_row.addWidget(browse)
        form.addRow("Working directory:", dir_row)
        self._port_spin = QSpinBox()
        self._port_spin.setRange(1024, 65535)
        self._port_spin.setValue(8888)
        form.addRow("Port:", self._port_spin)
        layout.addLayout(form)

        btn_row = QHBoxLayout()
        self._launch_btn = QPushButton("Launch JupyterLab")
        self._launch_btn.setStyleSheet("background:#2d7d46;color:white;font-weight:bold;padding:8px 16px;")
        self._launch_btn.clicked.connect(self._launch)
        btn_row.addWidget(self._launch_btn)
        self._stop_btn = QPushButton("Stop Server")
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(self._stop_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._status = QLabel("Status: not running")
        layout.addWidget(self._status)

        nb_group = QGroupBox("New Notebook")
        nv = QVBoxLayout(nb_group)
        nb_info = QLabel("Create a blank notebook in the working directory:")
        nv.addWidget(nb_info)
        nh = QHBoxLayout()
        self._nb_name = QLineEdit("untitled")
        nh.addWidget(self._nb_name)
        create_btn = QPushButton("Create .ipynb")
        create_btn.clicked.connect(self._create_notebook)
        nh.addWidget(create_btn)
        nv.addLayout(nh)
        layout.addWidget(nb_group)

        layout.addStretch()

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Working Directory", self._dir_edit.text())
        if d:
            self._dir_edit.setText(d)

    def _launch(self):
        cwd = self._dir_edit.text() or str(Path.home())
        port = self._port_spin.value()
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-m", "jupyter", "lab", f"--port={port}", "--no-browser=False"],
                cwd=cwd,
            )
            self._status.setText(f"Status: running on port {port} (PID {self._proc.pid})")
            self._launch_btn.setEnabled(False)
            self._stop_btn.setEnabled(True)
        except FileNotFoundError:
            QMessageBox.warning(self, "Jupyter not found",
                                "JupyterLab is not installed.\nRun: pip install jupyterlab")
        except Exception as ex:
            QMessageBox.critical(self, "Launch Error", str(ex))

    def _stop(self):
        if self._proc:
            self._proc.terminate()
            self._proc = None
        self._status.setText("Status: stopped")
        self._launch_btn.setEnabled(True)
        self._stop_btn.setEnabled(False)

    def _create_notebook(self):
        name = self._nb_name.text().strip() or "untitled"
        if not name.endswith(".ipynb"):
            name += ".ipynb"
        cwd = Path(self._dir_edit.text() or str(Path.home()))
        nb = {
            "nbformat": 4,
            "nbformat_minor": 5,
            "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                          "language_info": {"name": "python"}},
            "cells": [{"cell_type": "code", "execution_count": None, "metadata": {},
                       "outputs": [], "source": "# New notebook\n"}],
        }
        target = cwd / name
        try:
            target.write_text(json.dumps(nb, indent=2), encoding="utf-8")
            QMessageBox.information(self, "Created", f"Notebook created:\n{target}")
        except Exception as ex:
            QMessageBox.critical(self, "Error", str(ex))


# ═══════════════════════════════════════════════════════════════════════════
# Scientific calculator sub-tab
# ═══════════════════════════════════════════════════════════════════════════

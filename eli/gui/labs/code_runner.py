"""ELI Labs — background code-runner thread

Extracted verbatim from the former eli/gui/labs_tab.py monolith (strangler
refactor, v3 core-modularisation). Shared preamble — Qt binding, plot paths,
physics tables — lives in eli.gui.labs._common.
"""
from __future__ import annotations
from eli.gui.labs._common import *  # noqa: F401,F403


class _CodeRunnerThread(QThread):
    finished = pyqtSignal(str, str)

    def __init__(self, code: str, cwd: str):
        super().__init__()
        self._code = code
        self._cwd = cwd

    def run(self):
        try:
            result = subprocess.run(
                [sys.executable, "-c", self._code],
                capture_output=True, text=True, timeout=60,
                cwd=self._cwd,
            )
            self.finished.emit(result.stdout, result.stderr)
        except subprocess.TimeoutExpired:
            self.finished.emit("", "Timeout: execution exceeded 60 s")
        except Exception as ex:
            self.finished.emit("", str(ex))


# ═══════════════════════════════════════════════════════════════════════════
# Notebook sub-tab
# ═══════════════════════════════════════════════════════════════════════════

"""ELI Labs — background worker thread (test review)

Extracted verbatim from the former eli/gui/labs_tab.py monolith (strangler
refactor, v3 core-modularisation). Shared preamble — Qt binding, plot paths,
physics tables — lives in eli.gui.labs._common.
"""
from __future__ import annotations
from eli.gui.labs._common import *  # noqa: F401,F403


class _BgWorker(QThread):
    """Run any callable off the UI thread; emit its result."""
    done = pyqtSignal(object)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as e:  # surfaced to the slot
            self.done.emit({"__error__": str(e)})


def _start_bg(keep: list, fn, on_done) -> _BgWorker:
    """Run `fn` on a worker thread; `on_done`, a method of a widget, gets its result (or
    {"__error__": ...}) on the window's thread, the only thread that may touch a widget.

    `keep` holds each worker while it runs (a QThread dropped while running aborts the process);
    finished ones are let go here, on the window's thread, never from the worker itself."""
    keep[:] = [w for w in keep if w.isRunning()]
    worker = _BgWorker(fn)
    worker.done.connect(on_done)
    keep.append(worker)
    worker.start()
    return worker

"""
eli.utils.log — Central ELI logger factory.

Usage in any module:
    from eli.utils.log import get_logger
    log = get_logger(__name__)

Level is controlled at runtime via:
    ELI_LOG_LEVEL=DEBUG   (default: DEBUG — all diagnostic output)
    ELI_LOG_LEVEL=INFO    — startup/state changes only
    ELI_LOG_LEVEL=WARNING — warnings and errors only
    ELI_LOG_LEVEL=ERROR   — errors only

Set ELI_LOG_LEVEL=WARNING in production to silence diagnostic chatter.
"""
from __future__ import annotations

import logging
import os
import sys
from typing import Optional


_CONFIGURED = False
_FMT = "%(message)s"  # keep existing [TAG] prefix style intact; no extra decoration


def _configure_root() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    _CONFIGURED = True

    level_name = os.environ.get("ELI_LOG_LEVEL", "DEBUG").upper()
    level = getattr(logging, level_name, logging.DEBUG)

    root = logging.getLogger("eli")
    if root.handlers:
        return

    root.setLevel(logging.DEBUG)  # capture everything; handler filters by ELI_LOG_LEVEL

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(_FMT))
    root.addHandler(handler)

    # Suppress noisy third-party loggers
    for noisy in ("urllib3", "httpx", "httpcore", "PIL", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Return an ELI-namespaced logger. Pass __name__ from the calling module."""
    _configure_root()
    if name and not name.startswith("eli"):
        name = f"eli.{name}"
    return logging.getLogger(name or "eli")


# ── ELI's own console, kept where ELI can read it ──────────────────────────────────
# Everything ELI logged went to the terminal and nowhere else, and print() wrote straight to it, so
# asked about its terminal ELI had nothing to read. keep_console_log() keeps both in a file of ELI's
# own (logs/ in its data folder); read_own_log() reads it back. It starts from an entry point once
# ELI's folders are settled: resolving them when the first logger is made would fix them before a
# launcher has set them. What a native library writes straight to the process's descriptors (the
# model runtime's own lines) is not caught here.
CONSOLE_LOG_NAME = "eli-console.log"
_LOG_SHARE_OF_FREE_DISK = 1 / 2000   # each log file may use this share of the disk's free space
_LOG_BACKUPS = 3                     # rotated files kept besides the current one
_FILE_FMT = "%(asctime)s %(levelname)s %(name)s %(message)s"
_console: dict = {}


class _Tee:
    """A text stream that writes to the original one and, a line at a time, to the console log."""

    def __init__(self, original, logger: logging.Logger, level: int):
        self._original, self._logger, self._level, self._pending = original, logger, level, ""

    def write(self, text):
        if self._original is not None:
            self._original.write(text)
        self._pending += str(text)
        while "\n" in self._pending:
            line, self._pending = self._pending.split("\n", 1)
            if line.strip():
                self._logger.log(self._level, line)
        return len(text)

    def flush(self):
        if self._original is not None:
            self._original.flush()

    def __getattr__(self, name):
        return getattr(self._original, name)


def keep_console_log(path=None) -> Optional[str]:
    """Keep ELI's log and its print() output in a rotating file it can read back; the path, or None
    when the "Write session log to file" setting (log_to_file / ELI_LOG_TO_FILE) is off. Each file
    may grow to _LOG_SHARE_OF_FREE_DISK of the free space; idempotent."""
    import shutil
    from logging.handlers import RotatingFileHandler
    from pathlib import Path

    if _console:
        return _console["path"]
    if path is None:
        try:
            from eli.core.runtime_settings import load_settings
            if not load_settings().get("log_to_file"):
                return None
            from eli.core.paths import logs_dir
            path = Path(logs_dir()) / CONSOLE_LOG_NAME
        except Exception:
            logging.getLogger("eli").debug("console log not kept", exc_info=True)
            return None
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    room = int(shutil.disk_usage(str(path.parent)).free * _LOG_SHARE_OF_FREE_DISK)
    handler = RotatingFileHandler(str(path), maxBytes=max(room, 1), backupCount=_LOG_BACKUPS,
                                  encoding="utf-8", delay=True)
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(_FILE_FMT))
    _configure_root()
    logging.getLogger("eli").addHandler(handler)
    console = logging.getLogger("eli.console")      # print() lines: to the file only, the terminal has them
    console.propagate = False
    console.setLevel(logging.DEBUG)
    console.addHandler(handler)
    _console.update(path=str(path), handler=handler, stdout=sys.stdout, stderr=sys.stderr)
    sys.stdout = _Tee(sys.stdout, console, logging.INFO)
    sys.stderr = _Tee(sys.stderr, console, logging.WARNING)
    return str(path)


def stop_console_log() -> None:
    """Stop keeping the console (the streams are given back as they were)."""
    if not _console:
        return
    sys.stdout, sys.stderr = _console["stdout"], _console["stderr"]
    handler = _console["handler"]
    logging.getLogger("eli").removeHandler(handler)
    logging.getLogger("eli.console").removeHandler(handler)
    handler.close()
    _console.clear()


def read_own_log(*, tail: Optional[int] = None, contains: str = "", since: Optional[float] = None,
                 path=None) -> dict:
    """What ELI's console said, from its own log file: the lines (oldest first; the last `tail` of
    them, those containing `contains`, those written at or after `since`, a timestamp), how many
    were errors or warnings, and the lines said most often. Facts only; the words are the caller's."""
    import datetime
    from collections import Counter
    from pathlib import Path

    named = path or _console.get("path")
    if not named:
        try:
            from eli.core.paths import logs_dir
            named = Path(logs_dir()) / CONSOLE_LOG_NAME
        except Exception:
            named = None
    where = Path(named) if named else None
    if where is None or not (where.exists() or any(where.parent.glob(where.name + ".*"))):
        return {"ok": False, "error": "ELI's console is not being kept (the session log setting is off)",
                "lines": []}
    files = sorted(where.parent.glob(where.name + ".*"), key=lambda p: p.stat().st_mtime) + [where]
    lines = []
    for f in files:
        try:
            lines += f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
    if since is not None:
        def _when(line: str) -> Optional[float]:
            try:
                return datetime.datetime.strptime(line[:23], "%Y-%m-%d %H:%M:%S,%f").timestamp()
            except ValueError:
                return None
        lines = [ln for ln in lines if (_when(ln) or 0.0) >= float(since)]
    if contains:
        lines = [ln for ln in lines if contains.lower() in ln.lower()]
    if tail is not None:
        lines = lines[-int(tail):] if tail > 0 else []
    body = [ln[24:] if len(ln) > 24 and ln[4] == "-" else ln for ln in lines]
    return {"ok": True, "path": str(where), "lines": lines,
            "errors": sum(1 for ln in body if ln.startswith(("ERROR", "CRITICAL")) or "Traceback" in ln),
            "warnings": sum(1 for ln in body if ln.startswith("WARNING")),
            "repeated": [(text, n) for text, n in Counter(b.split(" ", 2)[-1] for b in body).most_common()
                         if n > 1]}

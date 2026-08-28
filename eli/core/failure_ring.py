"""Structured failure ring buffer — makes suppressed exceptions observable at runtime.

The silent-swallow ratchet (tests/claims/test_no_silent_swallow.py) prevents *new*
bare `except: pass` handlers from landing in CI. This module complements that
ratchet: every best-effort `log.debug("suppressed exception", exc_info=True)` can
also land here so operators can inspect recent failures without tailing logs.

Cross-platform: pure in-process deque, no OS-specific APIs.
"""
from __future__ import annotations

import sys
import threading
import time
import traceback
from collections import deque
from typing import Any, Deque, Dict, List, Optional

_DEFAULT_CAPACITY = 256
_lock = threading.Lock()
_buffer: Deque[Dict[str, Any]] = deque(maxlen=_DEFAULT_CAPACITY)


def capacity() -> int:
    return _buffer.maxlen or _DEFAULT_CAPACITY


def record(
    module: str,
    context: str = "",
    *,
    exc: BaseException | None = None,
    exc_type: str = "",
    exc_msg: str = "",
    tb: str = "",
) -> None:
    """Append one failure event to the ring buffer (thread-safe)."""
    if exc is not None:
        exc_type = type(exc).__name__
        exc_msg = str(exc)
        tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    elif not exc_type and sys.exc_info()[0] is not None:
        _t, _v, _tb = sys.exc_info()
        exc_type = _t.__name__ if _t else "Exception"
        exc_msg = str(_v or "")
        tb = "".join(traceback.format_exception(_t, _v, _tb))

    entry: Dict[str, Any] = {
        "ts": time.time(),
        "module": str(module or "unknown"),
        "context": str(context or ""),
        "exc_type": exc_type or "Exception",
        "exc_msg": exc_msg,
        "traceback": tb,
    }
    with _lock:
        _buffer.append(entry)


def record_suppressed(module: str, context: str = "", *, exc: BaseException | None = None) -> None:
    """Log and record a suppressed exception — drop-in for best-effort guards."""
    from eli.utils.log import get_logger

    log = get_logger(module)
    if exc is not None:
        log.debug("%s", context or "suppressed exception", exc_info=(type(exc), exc, exc.__traceback__))
    else:
        log.debug(context or "suppressed exception", exc_info=True)
    record(module, context, exc=exc)


def recent(limit: int = 32) -> List[Dict[str, Any]]:
    """Return the most recent failure events (newest last)."""
    n = max(1, min(int(limit), capacity()))
    with _lock:
        items = list(_buffer)
    return items[-n:]


def clear() -> None:
    """Clear the buffer (tests / diagnostics only)."""
    with _lock:
        _buffer.clear()


def stats() -> Dict[str, Any]:
    """Summary for diagnostics surfaces."""
    with _lock:
        count = len(_buffer)
        modules: Dict[str, int] = {}
        for entry in _buffer:
            mod = str(entry.get("module") or "unknown")
            modules[mod] = modules.get(mod, 0) + 1
    return {"count": count, "capacity": capacity(), "by_module": modules}

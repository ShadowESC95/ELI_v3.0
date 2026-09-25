"""Runtime observability — failure ring + operator-facing diagnostics."""
from __future__ import annotations

from typing import Any, Dict

from eli.core.failure_ring import recent as _recent_failures, record_suppressed, stats as _failure_stats


def observe(module: str, context: str = "", *, exc: BaseException | None = None) -> None:
    """Log and record a suppressed exception (drop-in for best-effort guards)."""
    record_suppressed(module, context, exc=exc)


def recent_failure_report(*, limit: int = 16) -> str:
    """Human-readable summary of recent suppressed failures."""
    items = _recent_failures(limit=limit)
    if not items:
        return "No suppressed failures recorded in the failure ring."
    lines = ["Recent suppressed failures (newest last):", ""]
    for i, ev in enumerate(items, 1):
        mod = ev.get("module") or "?"
        ctx = ev.get("context") or ""
        et = ev.get("exc_type") or "Exception"
        em = str(ev.get("exc_msg") or "")[:120]
        lines.append(f"  {i}. [{mod}] {ctx}: {et}: {em}")
    st = _failure_stats()
    lines.append("")
    lines.append(f"Ring: {st.get('count', 0)}/{st.get('capacity', 256)} events")
    return "\n".join(lines)


def observability_snapshot() -> Dict[str, Any]:
    """Structured payload for META_DIAGNOSTIC / operator surfaces."""
    return {
        "failure_ring": _failure_stats(),
        "recent_failures": _recent_failures(limit=8),
    }

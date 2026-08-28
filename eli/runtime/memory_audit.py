"""User-visible memory audit — explain why ELI believed something.

Surfaces evidence from the evidence ledger and memory retrieval so users can
ask "why did ELI think X?" without reading raw logs.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from eli.utils.log import get_logger

log = get_logger(__name__)


def _search_memories(query: str, *, limit: int = 5) -> List[Dict[str, Any]]:
    try:
        from eli.memory.memory import get_memory
        mem = get_memory()
        hits = mem.search_memories(query, limit=limit) or []
        out: List[Dict[str, Any]] = []
        for h in hits:
            if isinstance(h, dict):
                out.append(h)
            else:
                out.append({"text": str(h)})
        return out
    except Exception:
        log.debug("memory search in audit failed", exc_info=True)
        return []


def _ledger_hits(query: str, *, limit: int = 8) -> List[Dict[str, Any]]:
    try:
        from eli.runtime.evidence_ledger import recent_events
        q = " ".join(str(query or "").lower().split())
        terms = [t for t in q.split() if len(t) > 3][:6]
        events = recent_events(limit=80, reusable_only=False)
        hits: List[Dict[str, Any]] = []
        for ev in events:
            blob = " ".join([
                str(ev.get("event_type") or ""),
                str(ev.get("source") or ""),
                str(ev.get("action") or ""),
                str(ev.get("subject") or ""),
                str(ev.get("content") or ""),
                str(ev.get("payload_json") or ""),
            ]).lower()
            if not terms or any(t in blob for t in terms):
                hits.append(ev)
            if len(hits) >= limit:
                break
        return hits
    except Exception:
        log.debug("ledger search in audit failed", exc_info=True)
        return []


def explain_belief(query: str, *, limit: int = 5) -> str:
    """Return a user-facing explanation of why ELI might believe ``query``."""
    q = str(query or "").strip()
    if not q:
        return "No query provided — ask what ELI remembered or why it answered a certain way."

    memories = _search_memories(q, limit=limit)
    events = _ledger_hits(q, limit=limit)

    lines = [f"Memory audit for: {q}", ""]

    if memories:
        lines.append("Stored memories that matched:")
        for i, m in enumerate(memories, 1):
            text = str(m.get("text") or m.get("content") or m)
            imp = m.get("importance")
            src = m.get("source") or m.get("kind") or "memory"
            suffix = f" (importance={imp:.2f})" if isinstance(imp, (int, float)) else ""
            lines.append(f"  {i}. [{src}]{suffix} {text[:240]}")
    else:
        lines.append("No stored memories matched this query.")

    lines.append("")
    if events:
        lines.append("Recent evidence events:")
        for i, ev in enumerate(events, 1):
            et = ev.get("event_type") or "event"
            src = ev.get("source") or ""
            content = str(ev.get("content") or "")[:180]
            conf = ev.get("confidence")
            conf_s = f", confidence={conf:.2f}" if isinstance(conf, (int, float)) else ""
            lines.append(f"  {i}. {et} ({src}{conf_s}): {content or '(no content)'}")
    else:
        lines.append("No recent evidence-ledger events matched.")

    lines.append("")
    lines.append(
        "This audit shows what ELI actually stored and logged — not a reconstructed guess. "
        "If nothing matched, ELI likely synthesized the answer without durable memory evidence."
    )
    return "\n".join(lines)


def explain_belief_json(query: str, *, limit: int = 5) -> Dict[str, Any]:
    """Structured audit payload for GUI/API surfaces."""
    return {
        "query": query,
        "memories": _search_memories(query, limit=limit),
        "events": _ledger_hits(query, limit=limit),
        "text": explain_belief(query, limit=limit),
    }

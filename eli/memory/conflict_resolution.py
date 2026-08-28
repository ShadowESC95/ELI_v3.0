"""Memory conflict detection and resolution.

When a new fact contradicts a stored one (same subject, different value),
demote the older row and store the correction with higher importance.
Cross-platform: SQLite only, no OS-specific APIs.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from eli.utils.log import get_logger

log = get_logger(__name__)

_FACT_RE = re.compile(
    r"(?:my|user'?s?|i am|i'm|call me|name is|favorite|favourite|prefers?|likes?)\s+"
    r"(?P<subject>\w[\w\s]{0,40}?)\s+(?:is|are|was|has|have|as)\s+(?P<value>.+)",
    re.I,
)
_SIMPLE_KV_RE = re.compile(
    r"^(?P<subject>[A-Za-z_][\w\s/-]{1,40}?)\s*[:=]\s*(?P<value>.+)$",
)


@dataclass
class MemoryConflict:
    existing_id: int
    existing_text: str
    new_text: str
    subject: str
    old_value: str
    new_value: str


def _normalize_subject(subject: str) -> str:
    return " ".join(str(subject or "").lower().split())


def _normalize_value(value: str) -> str:
    return " ".join(str(value or "").strip().rstrip(".").lower().split())


def parse_fact(text: str) -> Optional[Tuple[str, str]]:
    """Return (subject, value) if text looks like a storable fact."""
    t = str(text or "").strip()
    if not t:
        return None
    m = _FACT_RE.search(t)
    if m:
        return _normalize_subject(m.group("subject")), _normalize_value(m.group("value"))
    m = _SIMPLE_KV_RE.match(t)
    if m:
        return _normalize_subject(m.group("subject")), _normalize_value(m.group("value"))
    return None


def find_conflicts(
    memory: Any,
    new_text: str,
    *,
    limit: int = 5,
) -> List[MemoryConflict]:
    """Find stored memories that contradict ``new_text``."""
    parsed = parse_fact(new_text)
    if not parsed:
        return []
    subject, new_value = parsed
    conflicts: List[MemoryConflict] = []
    try:
        hits = memory.search_memories(subject, limit=limit) or []
    except Exception:
        try:
            hits = memory.recall(subject, limit=limit) or []
        except Exception:
            return []
    for hit in hits:
        if isinstance(hit, dict):
            hid = int(hit.get("id") or 0)
            text = str(hit.get("text") or hit.get("content") or "")
        else:
            hid = int(getattr(hit, "id", 0) or 0)
            text = str(getattr(hit, "text", "") or hit)
        old_parsed = parse_fact(text)
        if not old_parsed:
            continue
        old_subject, old_value = old_parsed
        if old_subject == subject and old_value != new_value:
            conflicts.append(MemoryConflict(
                existing_id=hid,
                existing_text=text,
                new_text=new_text,
                subject=subject,
                old_value=old_value,
                new_value=new_value,
            ))
    return conflicts


def resolve_conflicts(
    memory: Any,
    conflicts: List[MemoryConflict],
    *,
    source: str = "conflict_resolution",
) -> Dict[str, Any]:
    """Demote superseded facts and record the resolution."""
    if not conflicts:
        return {"resolved": 0}
    resolved = 0
    for c in conflicts:
        try:
            conn = memory._get_connection()
            try:
                conn.execute(
                    "UPDATE memories SET weight = MIN(weight, 0.15), "
                    "importance = MIN(COALESCE(importance, 0.5), 0.2), "
                    "tags = COALESCE(tags, '') || ',superseded' "
                    "WHERE id = ?",
                    (c.existing_id,),
                )
                conn.commit()
                resolved += 1
            finally:
                conn.close()
        except Exception:
            log.debug("conflict demotion failed", exc_info=True)
        try:
            from eli.runtime.evidence_ledger import record_event
            record_event(
                "memory_conflict_resolved",
                source=source,
                subject=c.subject,
                content=f"{c.old_value} -> {c.new_value}",
                payload={
                    "existing_id": c.existing_id,
                    "existing_text": c.existing_text[:500],
                    "new_text": c.new_text[:500],
                },
                outcome="superseded",
                reusable=True,
            )
        except Exception:
            log.debug("conflict ledger record failed", exc_info=True)
    return {"resolved": resolved, "conflicts": len(conflicts)}


def resolve_on_store(memory: Any, text: str) -> Dict[str, Any]:
    """Pre-store hook: resolve conflicts before writing a new fact."""
    conflicts = find_conflicts(memory, text)
    return resolve_conflicts(memory, conflicts)

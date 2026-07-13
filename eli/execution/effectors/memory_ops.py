"""Memory-ops effector — store/recall/search, conversation log, session history.

Core memory operations (via the canonical Memory bridge). Lifted from
executor_enhanced.py (v3 modularisation); MEMORY_PATH/MAX_HISTORY_MESSAGES reproduced
locally as pure config derivations. Memory *reports* stay with system_intel.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from eli.core.paths import get_paths
from eli.utils.log import get_logger
log = get_logger(__name__)

MEMORY_PATH = Path(os.environ.get("ELI_MEMORY_FILE", str(get_paths().artifacts_dir / "eli_memory_legacy.jsonl"))).expanduser().resolve()
MAX_HISTORY_MESSAGES = int(os.environ.get("ELI_MAX_HISTORY_MESSAGES", "32"))


def _get_canonical_memory():
    """Lazy import to avoid circular import."""
    from eli.memory import get_memory
    return get_memory()


def search_memory(query="", k=5, **kwargs):
    """Search via canonical Memory.recall_memory(), with conversation fallback."""
    try:
        mem = _get_canonical_memory()
        results = list(mem.recall_memory(query, limit=k))
        if hasattr(mem, "search_conversations") and len(results) < int(k or 5):
            conv = mem.search_conversations(query, limit=max(1, int(k or 5) - len(results)))
            for row in conv:
                results.append({
                    "timestamp": row.get("timestamp") or 0,
                    "text": row.get("content") or "",
                    "tags": "conversation",
                    "kind": "conversation",
                    "role": row.get("role") or "",
                    "session_id": row.get("session_id") or "",
                })
        results = results[: int(k or 5)]
        return {"ok": True, "results": results, "count": len(results)}
    except Exception as e:
        return {"ok": False, "error": str(e), "results": []}


def search_memory_compat(query: str = "", limit: int = 10, q: str = "", k: int = 0, db_path=None):
    """Back-compat wrapper around Memory.recall_memory()."""
    qq = (q or query or "").strip()
    kk = int(k or limit or 10)
    return search_memory(query=qq, k=kk)


def _repo_root():
    try:
        return get_paths().artifacts_dir
    except Exception:
        return None


def _convlog_path():
    try:
        from datetime import datetime, timezone
        art = _repo_root()
        if art is None:
            return None
        d = Path(art) / "conversations"
        d.mkdir(parents=True, exist_ok=True)
        fn = datetime.now(timezone.utc).strftime("%Y%m%d") + ".jsonl"
        return d / fn
    except Exception:
        return None


def _convlog_append(role: str, text: str, meta: dict | None = None) -> None:
    try:
        import json
        from datetime import datetime, timezone

        path = _convlog_path()
        if path is None:
            return

        rec = {
            "ts_iso": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "ts_unix": float(time.time()),
            "role": str(role),
            "text": str(text),
            "meta": meta or {},
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        return


def _get_memory_path():
    from eli.core.db_paths import get_user_db_path
    return get_user_db_path()


def memory_store(text, tags=None, kind="note", source="chat", confidence=0.7, ts=None):
    try:
        from eli.memory import get_memory
        mem = get_memory(db_path=_get_memory_path())
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        result = mem.store_memory(
            text=str(text),
            tags=tags or [],
            source=str(source),
            kind=str(kind),
            confidence=float(confidence),
        )
        return {"ok": True, "path": str(_get_memory_path())}
    except Exception as e:
        return {"ok": False, "error": repr(e), "path": str(_get_memory_path())}


def memory_recall(query, limit=20):
    try:
        from eli.memory import get_memory
        mem = get_memory(db_path=_get_memory_path())
        results = mem.recall_memory(str(query), limit=int(limit))
        return {"ok": True, "results": results, "path": str(_get_memory_path())}
    except Exception as e:
        return {"ok": False, "error": repr(e), "results": [], "path": str(_get_memory_path())}


def _state_get_history(st: Dict[str, Any]) -> List[Dict[str, str]]:
    h = st.get("chat_history")
    if not isinstance(h, list):
        return []
    out: List[Dict[str, str]] = []
    for m in h:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            out.append({"role": role, "content": content})
    return out


def _state_append_history(st: Dict[str, Any], user_msg: str, assistant_msg: str) -> None:
    h = _state_get_history(st)
    h.append({"role": "user", "content": user_msg})
    h.append({"role": "assistant", "content": assistant_msg})
    if len(h) > MAX_HISTORY_MESSAGES:
        h = h[-MAX_HISTORY_MESSAGES:]
    st["chat_history"] = h
    st["updated_at"] = time.time()


def memory_store_legacy(text: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        msg = "Empty memory text."
        return {"ok": False, "action": "MEMORY_STORE", "error": "empty_text", "content": msg, "response": msg}

    rec = {
        "ts": time.time(),
        "text": text,
        "tags": [t.strip() for t in (tags or []) if isinstance(t, str) and t.strip()],
    }
    try:
        MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        with MEMORY_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        msg = "Memory stored."
        return {"ok": True, "action": "MEMORY_STORE", "record": rec, "content": msg, "response": msg}
    except Exception as e:
        msg = "Failed to store memory."
        return {"ok": False, "action": "MEMORY_STORE", "error": repr(e), "content": msg, "response": msg}


def memory_recall_legacy(query: str, limit: int = 5) -> Dict[str, Any]:
    q = (query or "").strip().lower()
    if not q:
        msg = "Empty recall query."
        return {"ok": False, "action": "MEMORY_RECALL", "error": "empty_query", "content": msg, "response": msg}

    hits: List[Dict[str, Any]] = []
    try:
        if not MEMORY_PATH.exists():
            msg = "No memory archive yet."
            return {"ok": True, "action": "MEMORY_RECALL", "hits": [], "content": msg, "response": msg}

        with MEMORY_PATH.open("r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                text = str(rec.get("text", "")).lower()
                tags = " ".join(rec.get("tags") or []).lower()
                if q in text or q in tags:
                    hits.append(rec)

        hits = hits[-max(1, int(limit)):]  # most recent matches
        # Human readable summary
        summary_lines = []
        for r in reversed(hits):
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get("ts", 0)))
            t = str(r.get("text", "")).strip()
            t = t if len(t) <= 180 else t[:177] + "..."
            summary_lines.append(f"- [{ts}] {t}")
        content = "Memory recall:\n" + ("\n".join(summary_lines) if summary_lines else "(no matches)")
        return {"ok": True, "action": "MEMORY_RECALL", "hits": hits, "content": content, "response": content}
    except Exception as e:
        msg = "Memory recall failed."
        return {"ok": False, "action": "MEMORY_RECALL", "error": repr(e), "content": msg, "response": msg}


def _recall_memory_builtin(args: dict) -> dict:
    """Read recent utterances from ./data/memory/utterances.jsonl (if present)."""
    try:
        import json
        from pathlib import Path

        limit = int(args.get("limit", 20))
        root = Path(__file__).resolve().parent.parent
        memory_log = root / "data" / "memory" / "utterances.jsonl"

        if not memory_log.exists():
            return {"ok": True, "entries": [], "message": "No memory log found"}

        entries = []
        with open(memory_log, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        recent = entries[-limit:] if entries else []
        return {"ok": True, "entries": recent, "count": len(recent), "total": len(entries)}
    except Exception as e:
        return {"ok": False, "error": str(e)}

__all__ = ['_get_canonical_memory', 'search_memory', 'search_memory_compat', '_repo_root', '_convlog_path', '_convlog_append', '_get_memory_path', 'memory_store', 'memory_recall', '_state_get_history', '_state_append_history', 'memory_store_legacy', 'memory_recall_legacy', '_recall_memory_builtin']

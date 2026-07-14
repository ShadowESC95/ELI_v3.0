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


def set_user_name(*a, **k):
    from eli.execution.executor_enhanced import set_user_name as _f
    return _f(*a, **k)


def clear_chat_history(*a, **k):
    from eli.execution.executor_enhanced import clear_chat_history as _f
    return _f(*a, **k)


def _eli_refresh_user_info(*a, **k):
    from eli.execution.executor_enhanced import _eli_refresh_user_info as _f
    return _f(*a, **k)


def _eli_user_info_report(*a, **k):
    from eli.execution.executor_enhanced import _eli_user_info_report as _f
    return _f(*a, **k)


def _h_memory_store(a, args):
    txt = str(args.get("text") or args.get("content") or args.get("message") or "").strip()
    # Remove common prefixes like "memory: " and quotes
    txt = re.sub(r'^memory:\s*', '', txt, flags=re.IGNORECASE)
    txt = re.sub(r'^"(.+)"$', r'\1', txt)
    if not txt:
        return {"ok": False, "action": a, "error": "empty_text", "content": "empty_text", "response": "empty_text"}

    tags = args.get("tags", [])
    if isinstance(tags, str):
        tags = [x.strip() for x in tags.split(",") if x.strip()]
    elif isinstance(tags, (tuple, set)):
        tags = [str(x).strip() for x in tags if str(x).strip()]
    elif not isinstance(tags, list):
        tags = []

    # --- FIX: try local memory_store first, then fallback to other backends ---
    # Local JSONL memory_store() (most reliable, no prefix)
    try:
        result = memory_store(txt, tags=tags)
        if isinstance(result, dict):
            result.setdefault("action", a)
            result.setdefault("ok", True)
            result.setdefault("content", result.get("content") or "memory stored")
            result.setdefault("response", result.get("response") or result["content"])
        return result
    except Exception as e:
        # If local fails, try adapter
        try:
            from eli.memory.memory_adapter import memory_store as adapter_store
            result = adapter_store(txt, tags=tags)
            if isinstance(result, dict):
                result.setdefault("action", a)
                result.setdefault("ok", True)
                result.setdefault("content", result.get("content") or "memory stored")
                result.setdefault("response", result.get("response") or result["content"])
            return result
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # Last resort: canonical Memory class
        try:
            from eli.memory.memory import get_memory
            _mem = get_memory()
            _mem.store_memory(txt, tags=",".join(tags) if isinstance(tags, list) else str(tags))
            msg = "memory stored"
            return {"ok": True, "action": a, "content": msg, "response": msg}
        except Exception as e2:
            return {"ok": False, "action": a, "error": str(e2), "content": str(e2), "response": str(e2)}


def _h_set_user_name(a, args):
    return set_user_name(str(args.get("name", "")))


def _h_clear_chat_history(a, args):
    return clear_chat_history()


def _h_refresh_user_info(a, args):
    return _eli_refresh_user_info(
        force=args.get("force", False),
        reason=args.get("reason", "manual"),
    )


def _h_user_info_report(a, args):
    return _eli_user_info_report(
        force=args.get("force", False),
        reason=args.get("reason", "identity_query"),
    )


def _h_user_identity_summary(a, args):
    _question = str((args or {}).get("question") or (args or {}).get("query") or "who am i")
    try:
        _snapshot = _eli_user_info_report(force=False, reason="user_identity_summary")
        _raw = str(_snapshot.get("content") or _snapshot.get("response") or "")

        def _section(_name):
            _lines = []
            _inside = False
            _header = f"[{_name}]"
            for _ln in _raw.splitlines():
                _s = _ln.strip()
                if _s == _header:
                    _inside = True
                    continue
                if _inside and _s.startswith("[") and _s.endswith("]"):
                    break
                if _inside and _s.startswith("- "):
                    _item = _s[2:].strip()
                    if _item and _item.lower() != "none confirmed.":
                        _lines.append(_item.rstrip("."))
            return _lines

        _identity = _section("Identity")
        _prefs = _section("Communication Preferences")
        _working = _section("Working Style")
        _projects = _section("Active Projects")
        _technical = _section("Technical Environment")

        _lines = ["Current user identity summary:"]

        if _identity:
            _lines.append("")
            _lines.append("Identity:")
            for _x in _identity[:5]:
                _lines.append(f"- {_x}.")
        else:
            _lines.append("")
            _lines.append("- I do not have a confirmed name/identity row in the current user-info snapshot.")

        _facts = []
        for _bucket in (_prefs, _working, _projects, _technical):
            for _x in _bucket:
                if _x not in _facts:
                    _facts.append(_x)

        if _facts:
            _lines.append("")
            _lines.append("What I can say from current memory:")
            for _x in _facts[:10]:
                _lines.append(f"- {_x}.")

        _msg = "\n".join(_lines).strip()
        if not _msg:
            _msg = "I have no clean user identity summary available right now."

        return {
            "ok": True,
            "action": a,
            "content": _msg,
            "response": _msg,
            "report": {
                "source": "user_info_snapshot_summarized",
                "raw_snapshot_suppressed": True,
                "question": _question,
            },
            "evidence_source": "user_info_snapshot_summarized",
            "generation_invoked": False,
        }
    except Exception as e:
        _msg = f"User identity summary failed: {type(e).__name__}: {e}"
        return {"ok": False, "action": a, "content": _msg, "response": _msg, "error": repr(e)}


def _h_message_time_query(a, args):
    import sqlite3 as _sq3
    from datetime import datetime as _dt
    from eli.memory import get_memory as _mt_get_memory
    which = str((args or {}).get("which", "first")).lower()
    scope = str((args or {}).get("scope", "all")).lower()
    try:
        mem = _mt_get_memory()
        dbp = getattr(mem, "db_path", None)
        if not dbp or not Path(dbp).exists():
            msg = "I don't have a conversation-history database to read that from yet."
            return {"ok": False, "action": a, "content": msg, "response": msg}
        where = "role='user'"
        params: List[Any] = []
        if scope == "today":
            midnight = _dt.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            where += " AND timestamp >= ?"
            params.append(midnight)
        agg = "MAX" if which in ("last", "latest") else "MIN"
        conn = _sq3.connect(str(dbp))
        try:
            row = conn.execute(
                f"SELECT {agg}(timestamp) FROM conversation_turns WHERE {where}",
                params,
            ).fetchone()
        finally:
            conn.close()
        ts = (row or [None])[0]
        scope_txt = " today" if scope == "today" else ""
        if not ts:
            msg = f"I have no record of a message from you{scope_txt or ' on file'}."
            return {"ok": True, "action": a, "content": msg, "response": msg,
                    "evidence_source": "conversation_turns"}
        stamp = _dt.fromtimestamp(float(ts)).strftime("%H:%M:%S on %Y-%m-%d")
        label = "most recent" if agg == "MAX" else "first"
        msg = f"Your {label} message{scope_txt} was at {stamp}."
        return {"ok": True, "action": a, "content": msg, "response": msg,
                "evidence_source": "conversation_turns"}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e),
                "content": str(e), "response": str(e)}


def _h_memory_recall(a, args):
    q = str(args.get("query", "")).strip()
    try:
        lim = int(args.get("limit", 5))
    except Exception:
        lim = 5

    if not q:
        return {"ok": False, "action": a, "error": "empty_query", "content": "empty_query", "response": "empty_query"}

    # 1) Canonical Memory class (proven working)
    try:
        from eli.memory.memory import get_memory as _get_mem_recall
        _mem_r = _get_mem_recall()
        hits = _mem_r.search_memory(q, limit=20) or []
        if isinstance(hits, list):
            # Truncate each hit and cap total to prevent context overflow.
            # Large hits (e.g. repeated conversation dumps) crash llama.cpp.
            _PER_HIT_CHARS = 300
            _MAX_TOTAL_CHARS = 3000
            lines, total = [], 0
            for h in hits:
                t = (h.get("text", "") or "").strip()
                if not t:
                    continue
                t = t[:_PER_HIT_CHARS]
                lines.append(t)
                total += len(t)
                if total >= _MAX_TOTAL_CHARS:
                    break
            content = "Memory recall:\n" + "\n---\n".join(lines) if lines else "Memory recall:\n(no matches)"
        else:
            content = "Memory recall:\n(no matches)"
        if not content.strip():
            content = "Memory recall:\n(no matches)"
        return {"ok": True, "action": a, "hits": hits, "content": content, "response": content}
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    # 2) Legacy fallback: brain.memory_adapter.memory_search (may not exist)
    try:
        from eli.memory.memory_adapter import memory_search
        result = memory_search(q, k=lim)
        # Ensure consistent envelope
        if isinstance(result, dict):
            result.setdefault("action", a)
            result.setdefault("ok", True)
            if "content" not in result:
                # memory_adapter may return 'results' (common) or 'hits' (legacy).
                hits = result.get("results")
                if hits is None:
                    hits = result.get("hits")
                if hits is None:
                    hits = result.get("items")
                if hits is None:
                    hits = []
                content = ""
                if isinstance(hits, list) and hits:
                    # Friendly readable render (top-k)
                    lines = []
                    for h in hits:
                        if not isinstance(h, dict):
                            continue
                        t = (h.get("text") or "").strip()
                        if not t:
                            continue
                        score = h.get("score", None)
                        tags = h.get("tags", "")
                        if score is not None:
                            try:
                                lines.append(f"- ({float(score):.3f}) {t} [{tags}]")
                            except Exception:
                                lines.append(f"- {t} [{tags}]")
                        else:
                            lines.append(f"- {t} [{tags}]")
                    content = "\n".join(lines).strip()
                result["content"] = content or "Memory recall:\n(no matches)"
                # Also expose a consistent 'hits' key for any callers expecting it.
                if "hits" not in result:
                    result["hits"] = hits
            result.setdefault("response", result.get("response") or result["content"])
        return result
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    # 2) Fallback: canonical Memory class
    try:
        from eli.memory.memory import get_memory
        _mem = get_memory()
        hits = _mem.search_memory(q, limit=20) or []
        content = "\n".join((h.get("text", "") or "") for h in hits) if isinstance(hits, list) else "Memory recall:\n(no matches)"
        if not content.strip():
            content = "Memory recall:\n(no matches)"
        return {"ok": True, "action": a, "hits": hits, "content": content, "response": content}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

_register("MEMORY_STORE", _h_memory_store)
_register("SET_USER_NAME", _h_set_user_name)
_register("CLEAR_CHAT_HISTORY", _h_clear_chat_history)

_register("REFRESH_USER_INFO", _h_refresh_user_info)
_register("USER_INFO_REPORT", _h_user_info_report)
_register("USER_IDENTITY_SUMMARY", _h_user_identity_summary)

_register("MESSAGE_TIME_QUERY", _h_message_time_query)

_register("MEMORY_RECALL", _h_memory_recall)

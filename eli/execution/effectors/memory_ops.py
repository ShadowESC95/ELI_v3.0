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
from pathlib import Path
from typing import Tuple, Any, Dict, List, Optional
from eli.core.paths import get_paths
from eli.utils.log import get_logger
log = get_logger(__name__)

MEMORY_PATH = Path(os.environ.get("ELI_MEMORY_FILE", str(get_paths().artifacts_dir / "eli_memory_legacy.jsonl"))).expanduser().resolve()
MAX_HISTORY_MESSAGES = int(os.environ.get("ELI_MAX_HISTORY_MESSAGES", "32"))


# "the full logs from that comment", "the transcript", "the exact timestamps": the record is asked for,
# not just when.
_ASKS_FOR_THE_RECORD = re.compile(r"\b(?:logs?|transcripts?|records?|time\s?stamps?)\b", re.I)


def _named_in_last_reply(mem: Any) -> List[str]:
    """Proper names in ELI's last reply, longest first: what a bare "him", "her" or "that" points at
    when ELI itself just named it ("▶ Playing — spotify (Immortal Technique — Point Of No Return)")."""
    try:
        last = next((str(t.get("content") or "") for t in mem.get_recent_conversation(limit=6) or []
                     if str(t.get("role", "")).lower() in ("assistant", "eli")), "")
    except Exception:
        return []
    names = re.findall(r"\b(?:[A-Z][\w'’]+(?:\s+(?:of|the|and|&)\s+)?){2,}|\b[A-Z][\w'’]{3,}\b", last)
    out: List[str] = []
    for name in sorted((n.strip() for n in names), key=len, reverse=True):
        if name and name.lower() not in ("playing", "paused", "spotify", "eli", "jason") and name not in out:
            out.append(name)
    return out[:4]


def _when_said_answer(a: str, topic: str, turn_args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """When the user wrote about `topic`: every one of their messages that mentions it, by day, from
    the conversation log. An empty topic means the exchange just had ("give me the timestamps of that
    interaction"): what the user's recent messages were about. Questions asking when are left out,
    or the answer would list them."""
    import time as _t
    from eli.cognition.evidence_format import age_label, row_time
    from eli.execution.router_enhanced import when_said_topic
    from eli.memory import get_memory
    from eli.runtime.conversation_thread import last_subject
    mem = get_memory()
    asked_for_record = bool(_ASKS_FOR_THE_RECORD.search(str((turn_args or {}).get("question") or "")))
    candidates = [topic] if topic else []
    if not topic:
        recent = []
        for turn in mem.get_recent_conversation(role="user") or []:
            asked = when_said_topic(turn.get("content"))
            if asked != "":
                recent.append({**turn, "content": asked} if asked else turn)
        candidates = [t for t in [last_subject(recent)] if t]
        # what ELI itself last named: "you remember him?" after ELI said who was playing
        candidates += _named_in_last_reply(mem)
    hits: List[Tuple[float, Dict[str, Any]]] = []
    for candidate in candidates:
        found = [(row_time(h), h) for h in mem.search_conversations(candidate, limit=None, match_all=True) or []
                 if when_said_topic(h.get("content")) is None]
        found = sorted((when, h) for when, h in found if when)
        if found:
            topic, hits = candidate, found
            break
    if not candidates:
        msg = "Which conversation do you mean? Tell me what it was about and I'll give you the times."
        return {"ok": True, "action": a, "needs_input": True, "content": msg, "response": msg}
    if not hits:
        msg = f"I can't find a message of yours that mentions “{topic}” in our conversation log."
        return {"ok": True, "action": a, "content": msg, "response": msg, "evidence_source": "conversation_turns"}
    if asked_for_record:
        # the record itself: each message, with its time, in full
        shown = hits[-8:]
        lines = [f"- {_t.strftime('%a %d %b %Y at %H:%M', _t.localtime(when))} ({age_label(when)}): "
                 f"“{' '.join(str(h.get('content') or '').split())[:400]}”" for when, h in shown]
        msg = (f"The record of you on “{topic}”, {len(hits)} message{'s' if len(hits) != 1 else ''}"
               f"{' (the last 8)' if len(hits) > 8 else ''}:\n" + "\n".join(lines))
        return {"ok": True, "action": a, "content": msg, "response": msg, "evidence_source": "conversation_turns"}
    days: Dict[str, List[float]] = {}
    for when, _h in hits:
        days.setdefault(_t.strftime("%Y-%m-%d", _t.localtime(when)), []).append(when)
    lines = [f"- {_t.strftime('%a %d %b %Y', _t.localtime(times[0]))} ({age_label(times[0])}): "
             + ", ".join(_t.strftime("%H:%M", _t.localtime(x)) for x in times) for _day, times in sorted(days.items())]
    first_when, first = hits[0]
    opening = re.split(r"(?<=[.!?])\s", str(first.get("content") or "").strip(), maxsplit=1)[0]
    msg = (f"You wrote about “{topic}” in {len(hits)} message{'s' if len(hits) != 1 else ''}, first on "
           f"{_t.strftime('%a %d %b %Y at %H:%M', _t.localtime(first_when))} (“{opening}”):\n" + "\n".join(lines)
           + "\nThese are when you wrote about it; the day of the event itself is only here if you said it.")
    return {"ok": True, "action": a, "content": msg, "response": msg, "evidence_source": "conversation_turns"}


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


def memory_store(*a, **k):
    # One implementation: this copy dropped the row id, skip reason and index flag.
    from eli.execution.executor_enhanced import memory_store as _f
    return _f(*a, **k)


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
        root = Path(__file__).resolve().parents[2]
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
            if result.get("ok"):
                _idx = result.get("vector_indexed")
                _where = Path(str(result.get("path") or "user.sqlite3")).name
                _msg = (f'Stored in long-term memory ({_where}'
                        + (f", row {result['id']}" if result.get("id") else "")
                        + ("; semantic index: yes" if _idx else "; semantic index: no" if _idx is False else "")
                        + f'): "{txt}"')
            elif result.get("skipped"):
                _msg = f"Not stored: the memory filter rejected it ({result.get('reason') or 'not durable knowledge'})."
            else:
                _msg = f"Not stored: {result.get('error') or result.get('reason') or 'the memory write failed'}."
            result["content"] = _msg
            result["response"] = _msg
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
    # "when did I write about X": every message of the user's that mentions it, by day
    if (args or {}).get("topic") is not None:
        try:
            return _when_said_answer(a, str((args or {}).get("topic") or "").strip(), args or {})
        except Exception as e:
            return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}
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


_GRAPH_LINE = re.compile(
    r"^\s*\[(?P<obj>[^\]]+)\]\s*\((?P<kind>\w+)\)\s*\n?\s*(?P<subj>[\w ]+?)\s*—\[(?P<rel>[^\]]+)\]→\s*"
    r"(?P<obj2>.+?)(?:\s*\(as of (?P<when>[^)]+)\))?\s*$", re.S)
_RELATION_WORDS = {"prefers": "prefer", "likes": "like", "dislikes": "dislike", "loves": "love", "hates": "hate",
                   "is": "are", "has": "have", "wants": "want", "needs": "need", "owns": "own", "uses": "use",
                   "lives_in": "live in", "works_at": "work at", "works_on": "work on"}
_DIGEST_LINE = re.compile(r"session digest \(\d+ turns\)", re.I)


def _plain_hit(text: str) -> str:
    """A memory as a sentence: a graph edge ("User —[prefers]→ X (as of 14 Oct)") as "you prefer X (as
    of 14 Oct)"; a turn ("[Tue 27 Oct 13:01, 7 days ago] ...") as "Tue 27 Oct 13:01 (7 days ago): you
    said “...”"; a session digest as nothing. ELI dumped the raw lines in the user's face."""
    text = str(text or "").strip()
    if _DIGEST_LINE.search(text):
        return ""
    m = _GRAPH_LINE.match(text)
    if m:
        who = "you" if m.group("subj").strip().lower() in ("user", "you", "owner") else m.group("subj").strip()
        rel = m.group("rel").strip().lower()
        verb = _RELATION_WORDS.get(rel, rel.replace("_", " ")) if who == "you" else rel.replace("_", " ")
        when = f" (as of {m.group('when')})" if m.group("when") else ""
        return f"{who} {verb} {m.group('obj2').strip()}{when}"
    m = re.match(r"^\[(?P<stamp>[^\]]+)\]\s*(?P<said>.+)$", text, re.S)
    if m:
        stamp = m.group("stamp").strip()
        said = " ".join(m.group("said").split())
        if ", " in stamp:
            day, ago = stamp.split(", ", 1)
            stamp = f"{day} ({ago})"
        return f"{stamp}: you said “{said}”"
    return " ".join(text.split())


def _recall_in_words(q: str, lines: List[str]) -> str:
    """The recall as an answer: what matches the question first, then the rest, each a sentence."""
    words = [w for w in re.findall(r"[a-z0-9']+", q.lower()) if len(w) > 2]
    told = [s for s in (_plain_hit(l) for l in lines) if s]
    told.sort(key=lambda s: -sum(w in s.lower() for w in words))
    if not told:
        return f"I have nothing on “{q}” in my memory."
    head = f"What I have on “{q}”:" if q else "What I have:"
    return head + "\n" + "\n".join(f"- {s}" for s in told[:8])


def _h_memory_recall(a, args):
    q = str(args.get("query", "")).strip()
    try:
        lim = int(args.get("limit", 5))
    except Exception:
        lim = 5

    if not q and str(args.get("referent") or "") == "last_reply":
        # "you remember him?": the one ELI itself last named
        from eli.memory.memory import get_memory as _get_mem_named
        names = _named_in_last_reply(_get_mem_named())
        if not names:
            msg = "Who do you mean? Give me the name and I'll tell you what I have."
            return {"ok": True, "action": a, "needs_input": True, "content": msg, "response": msg}
        q = names[0]
    if not q:
        return {"ok": False, "action": a, "error": "empty_query", "content": "empty_query", "response": "empty_query"}

    def _dated(h: Dict[str, Any], text: str) -> str:
        # Each memory says when it is from: asked "when was that?", ELI made a time up.
        from eli.cognition.evidence_format import row_time, turn_stamp
        when = row_time(h)
        stamp = turn_stamp(when) if when else ""
        return f"[{stamp}] {text}" if stamp else text

    # 1) Canonical Memory class (proven working)
    try:
        from eli.memory.memory import get_memory as _get_mem_recall
        _mem_r = _get_mem_recall()
        hits = _mem_r.search_memory(q, limit=20) or []
        from eli.kernel.request_context import turn_facts_var
        _said = str((turn_facts_var.get() or {}).get("user_input") or "").strip()
        hits = [h for h in hits if str(h.get("text", "") or "").strip() != _said]
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
                t = _dated(h, t[:_PER_HIT_CHARS])
                lines.append(t)
                total += len(t)
                if total >= _MAX_TOTAL_CHARS:
                    break
            content = _recall_in_words(q, lines)
        else:
            content = _recall_in_words(q, [])
        if not content.strip():
            content = _recall_in_words(q, [])
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
                        t = _dated(h, t)
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
        content = "\n".join(_dated(h, h.get("text", "") or "") for h in hits) if isinstance(hits, list) else "Memory recall:\n(no matches)"
        if not content.strip():
            content = "Memory recall:\n(no matches)"
        return {"ok": True, "action": a, "hits": hits, "content": content, "response": content}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_memory_forget(a, args):
    from eli.memory import get_memory as _gm_forget
    _mem_f = _gm_forget(db_path=_get_memory_path())
    if args.get("confirm") and args.get("ids"):
        _rep = _mem_f.forget(args.get("ids"))
        try:
            from eli.runtime.pending_proposal import clear_pending_proposal
            clear_pending_proposal()
        except Exception:
            log.debug("pending forget not cleared", exc_info=True)
        _parts = [f"{v} {k}" for k, v in _rep.items() if v]
        msg = ("Forgotten: " + ", ".join(_parts) + ".") if _rep.get("memories") else "Nothing matched those ids, so nothing was deleted."
        return {"ok": bool(_rep.get("memories")), "action": a, "report": _rep, "content": msg, "response": msg}
    query = str(args.get("query") or "").strip()
    # Documents ELI has read are stored whole in the document index: forgetting one takes it
    # and every passage and vector made from it out. The file on disk is left alone.
    _dindex, _duid, _docs = None, "", []
    try:
        from eli.kernel import request_context as _rc_forget
        from eli.memory.document_index import get_document_index
        _dindex, _duid = get_document_index(), str(_rc_forget.user_id_var.get() or "")
    except Exception:
        log.debug("document index unavailable for forget", exc_info=True)
    if args.get("confirm") and args.get("document_ids"):
        _gone = _dindex.remove(args.get("document_ids"), user_id=_duid) if _dindex else []
        try:
            from eli.runtime.pending_proposal import clear_pending_proposal
            clear_pending_proposal()
        except Exception:
            log.debug("pending forget not cleared", exc_info=True)
        msg = ("Removed from the document index: " + ", ".join(f"{d['title']} ({d['passages']} passages)" for d in _gone)
               + ". The files themselves are untouched.") if _gone else "No indexed document has those ids, so nothing was removed."
        return {"ok": bool(_gone), "action": a, "documents": _gone, "content": msg, "response": msg}
    if _dindex and query:
        try:
            _docs = _dindex.matching(query, _duid)
        except Exception:
            log.debug("document match failed", exc_info=True)
    _doc_note = ""
    if _docs:
        _doc_note = ("\nIndexed documents that match:\n" + "\n".join(
            f"- document #{d['id']}: {d['title']} ({d['passages']} passages)" for d in _docs)
            + "\nSay confirm forget documents " + " ".join(str(d["id"]) for d in _docs)
            + " to remove them from the index (the files stay on disk).")
    found = _mem_f.forget_candidates(query) if query else []
    if not found and _docs:
        try:
            from eli.runtime.pending_proposal import set_pending_proposal
            set_pending_proposal("confirm forget documents " + " ".join(str(d["id"]) for d in _docs),
                                 summary="forget documents")
        except Exception:
            log.debug("pending forget not set", exc_info=True)
        msg = f"No stored memory matches '{query}'." + _doc_note
        return {"ok": True, "action": a, "matches": [], "documents": _docs, "content": msg, "response": msg}
    if not found:
        msg = f"I found nothing stored that matches '{query}', so there is nothing to forget."
        return {"ok": True, "action": a, "matches": [], "content": msg, "response": msg}
    ids = [f["id"] for f in found]
    try:
        from eli.runtime.pending_proposal import set_pending_proposal
        set_pending_proposal("confirm forget memories " + " ".join(str(i) for i in ids), summary="forget memories")
    except Exception:
        log.debug("pending forget not set", exc_info=True)
    lines = "\n".join(f"- #{f['id']}: {f['text']}" for f in found)
    msg = (f"These stored memories match '{query}':\n{lines}\nSay yes to delete them and everything derived from them "
           f"(summaries, profile items, index entries, claims), or say confirm forget memories with the ids you want gone."
           + _doc_note)
    return {"ok": True, "action": a, "matches": found, "documents": _docs, "content": msg, "response": msg}


from eli.execution.effectors.registry import register as _register

_register("MEMORY_FORGET", _h_memory_forget)
_register("MEMORY_STORE", _h_memory_store)
_register("SET_USER_NAME", _h_set_user_name)
_register("CLEAR_CHAT_HISTORY", _h_clear_chat_history)

_register("REFRESH_USER_INFO", _h_refresh_user_info)
_register("USER_INFO_REPORT", _h_user_info_report)
_register("USER_IDENTITY_SUMMARY", _h_user_identity_summary)

_register("MESSAGE_TIME_QUERY", _h_message_time_query)

_register("MEMORY_RECALL", _h_memory_recall)

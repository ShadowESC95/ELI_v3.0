"""
eli/cognition/thoughts.py
─────────────────────────
One shape for a thought of ELI's, wherever it is had and wherever it is shown.

A memory that came to mind, a claim a reply rested on, a sentence read as a command, something ELI
made of its own accord, an action that failed and what ELI did about it, a fix ELI proposed for
itself, a memory it let go: each was decided somewhere in ELI with reasons and a chance, and each
was decided silently. This is the record every one of them writes, so that "why did you think of
that", "how sure were you", "why did you drop it" and "what became of it" have the same answer
from the chat, the world's rooms (ELI VR shows them on the screens of the room they belong to) and
any package that reads ELI's world.

    record(kind, what, why, chance, decision, reason, ...)   write one; a `ref` names it for settling
    settle(ref, outcome, how)              what became of it: right, wrong, done, undone, dropped
    settle_prefix(prefix, outcome, how)    every open thought under a ref prefix (a turn's recalls)
    recent(limit, kinds, room, user_id)    the latest, newest first, as dicts with their words
    by_id(id)                              one thought, by its row id
    words(thought)                         one line in words
    room_of(kind)                          the room of the house a kind belongs in

The kinds and their rooms:
    recall      a memory shown to a reply            memory_archive
    memory      a memory kept, promoted or let go    memory_archive
    claim       a claim a reply rested on            evidence_wall
    surprise    something that did not fit           anomaly_room
    conflict    two things ELI was told disagree     anomaly_room
    action      an action that failed, and what next debug_basement
    route       a sentence read as a command         core_room
    act         something made of ELI's own accord   core_room
    proposal    a fix ELI proposed for itself        upgrade_bay
    diagnosis   a failure ELI analysed               upgrade_bay
    lesson      a change ELI made to one of its parts  upgrade_bay
    note        a note ELI wrote in its rooms        evidence_wall

Whose a thought is: the user of the turn it was had in (the owner's, or one API member's, as the
memory policy keys them), unless it is about ELI itself (SHARED_KINDS: a fix, a diagnosis, a change
to its parts, something it made, a note in its rooms). A reader is shown its own thoughts and ELI's
shared ones, never another user's memories, claims or sentences.

A body hangs the latest of each room up as sheets (eli/world/shown.py) the user can pick up and
ask about; by_id gives one back for that.

A thought is kept DAYS (as the calibration keeps outcomes); nothing here phrases a reply.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from eli.utils.log import get_logger

log = get_logger(__name__)

DAYS = 90.0                       # how long a thought is kept, as epistemics keeps outcomes
ROOM_OF = {
    "recall": "memory_archive", "memory": "memory_archive",
    "claim": "evidence_wall",
    "surprise": "anomaly_room", "conflict": "anomaly_room",
    "action": "debug_basement",
    "route": "core_room", "act": "core_room",
    "proposal": "upgrade_bay", "diagnosis": "upgrade_bay", "lesson": "upgrade_bay",
    "note": "evidence_wall",
}
# The kinds that are about ELI itself and are any reader's to see; every other kind is its user's.
SHARED_KINDS = ("proposal", "diagnosis", "lesson", "act", "note")
DECISIONS = ("shown", "held", "kept", "raised", "dropped", "taken", "declined", "tried", "advised", "told",
             "proposed", "adopted", "pinned")
OUTCOMES = ("right", "wrong", "done", "undone", "dropped", "stood", "rejected")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS thoughts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    what TEXT NOT NULL,
    why_json TEXT,
    chance REAL,
    decision TEXT,
    reason TEXT,
    subject TEXT,
    request_id TEXT,
    ref TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    outcome TEXT,
    how TEXT,
    settled_at REAL,
    user_id TEXT,
    room TEXT
);
CREATE INDEX IF NOT EXISTS idx_thoughts_ts ON thoughts(ts);
CREATE INDEX IF NOT EXISTS idx_thoughts_ref ON thoughts(ref);
"""
# The databases whose schema this process has made sure of: each is made sure of once, not on every
# write, settle and read of a turn.
_ensured: set = set()
_COLUMNS = ("id", "ts", "kind", "what", "why_json", "chance", "decision", "reason", "subject", "request_id", "ref",
            "status", "outcome", "how", "settled_at", "user_id", "room")
_trimmed_at = 0.0


def _default_db_path() -> Path:
    from eli.runtime.evidence_ledger import _default_db_path as ledger_path
    return ledger_path()


def _connect(db_path=None) -> sqlite3.Connection:
    path = Path(db_path).expanduser().resolve() if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _ensured.discard(str(path))       # a store taken away (a wipe) is made again
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        from eli.core.sqlite_util import apply_pragmas
        apply_pragmas(conn, db_path=str(path), synchronous="NORMAL")
    except ImportError as failure:   # the plain connection serves
        log.debug("pragmas not applied to the thoughts store: %s", failure)
    key = str(path)
    if key not in _ensured:
        conn.executescript(_SCHEMA)
        _ensured.add(key)
    return conn


def _owner_key(user_id: Any = None) -> str:
    """Whose a thought is, as the memory policy keys users ("" the owner, else the member's id); with
    none given, the user of the turn running in this context."""
    try:
        from eli.memory.policy import owner_key
        return owner_key(None if user_id in (None, "") else user_id)
    except ImportError as failure:      # no policy to say otherwise: one owner
        log.debug("memory policy unavailable: %s", failure)
        return ""


def _prefix_range(prefix: str) -> tuple:
    """A prefix as a range of the ref index ("ref >= ? AND ref < ?"): LIKE is case-blind and the
    index is not, so a LIKE settle read the whole table."""
    return prefix, prefix + "\U0010ffff"


def room_of(kind: str) -> str:
    return ROOM_OF.get(str(kind or ""), "core_room")


def _why_list(why: Any) -> List[str]:
    if why is None:
        return []
    if isinstance(why, str):
        return [why] if why.strip() else []
    out: List[str] = []
    for item in why:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("what") or "").strip()
            value = item.get("value")
            text = str(item.get("words") or name or "").strip()
            if text and value is not None and not item.get("words"):
                text = f"{text} {value}"
            if text:
                out.append(text)
        else:
            text = str(item or "").strip()
            if text:
                out.append(text)
    return out


def record(kind: str, what: str, *, why: Any = None, chance: Optional[float] = None, decision: str = "",
           reason: str = "", subject: str = "", request_id: str = "", ref: str = "", user_id: str = "",
           room: str = "", now: Optional[float] = None, db_path=None) -> int:
    """Write one thought. With a `ref` that is already there and still open, the thought is brought
    up to date instead of written again (a recall shown twice is one thought). The row id."""
    what = " ".join(str(what or "").split())
    if not kind or not what:
        return 0
    now = float(time.time() if now is None else now)
    row = {
        "ts": now, "kind": str(kind), "what": what[:300], "why_json": json.dumps(_why_list(why), ensure_ascii=False),
        "chance": None if chance is None else max(0.0, min(1.0, float(chance))),
        "decision": str(decision or "")[:40], "reason": " ".join(str(reason or "").split())[:300],
        "subject": " ".join(str(subject or "").split())[:200], "request_id": str(request_id or "")[:120],
        "ref": str(ref or "")[:200], "room": str(room or room_of(kind)),
        # a thought about ELI itself is any reader's; any other is the user's of the turn it was had in
        "user_id": "" if str(kind) in SHARED_KINDS else _owner_key(user_id)[:120],
    }
    try:
        conn = _connect(db_path)
    except (sqlite3.Error, OSError) as failure:
        log.debug("a thought was not kept: %s", failure)
        return 0
    try:
        if row["ref"]:
            had = conn.execute("SELECT id FROM thoughts WHERE ref = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
                               (row["ref"],)).fetchone()
            if had:
                conn.execute("UPDATE thoughts SET ts = ?, what = ?, why_json = ?, chance = ?, decision = ?, reason = ?, "
                             "subject = ?, request_id = ?, user_id = ?, room = ? WHERE id = ?",
                             (row["ts"], row["what"], row["why_json"], row["chance"], row["decision"], row["reason"],
                              row["subject"], row["request_id"], row["user_id"], row["room"], int(had[0])))
                conn.commit()
                return int(had[0])
        cur = conn.execute(
            "INSERT INTO thoughts (ts, kind, what, why_json, chance, decision, reason, subject, request_id, ref, user_id, room) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["ts"], row["kind"], row["what"], row["why_json"], row["chance"], row["decision"], row["reason"],
             row["subject"], row["request_id"], row["ref"], row["user_id"], row["room"]))
        _trim(conn, now)
        conn.commit()
        return int(cur.lastrowid or 0)
    except sqlite3.Error as failure:
        log.debug("a thought was not kept: %s", failure)
        return 0
    finally:
        conn.close()


def _trim(conn: sqlite3.Connection, now: float) -> None:
    """Thoughts older than DAYS leave, checked at most once an hour."""
    global _trimmed_at
    if now - _trimmed_at < 3600.0:
        return
    _trimmed_at = now
    conn.execute("DELETE FROM thoughts WHERE ts < ?", (now - DAYS * 86400.0,))


def settle(ref: str, outcome: str, *, how: str = "", now: Optional[float] = None, db_path=None) -> int:
    """What became of the open thought(s) with this ref. How many were settled."""
    if not ref:
        return 0
    return settle_prefix(ref, outcome, how=how, now=now, db_path=db_path, exact=True)


def settle_prefix(prefix: str, outcome: str, *, how: str = "", now: Optional[float] = None, db_path=None,
                  exact: bool = False, kinds: Optional[Sequence[str]] = None, before: Optional[float] = None) -> int:
    """Every open thought whose ref starts with `prefix` (or equals it, with `exact`) is settled with
    this outcome; with `before`, only thoughts had before that time. How many were."""
    if not prefix:
        return 0
    now = float(time.time() if now is None else now)
    try:
        conn = _connect(db_path)
    except (sqlite3.Error, OSError) as failure:
        log.debug("thoughts not settled: %s", failure)
        return 0
    try:
        sql = "UPDATE thoughts SET status = 'settled', outcome = ?, how = ?, settled_at = ? WHERE status = 'open' AND "
        params: List[Any] = [str(outcome or "")[:40], str(how or "")[:120], now]
        if exact:
            sql += "ref = ?"
            params.append(prefix)
        else:
            sql += "ref >= ? AND ref < ?"
            params.extend(_prefix_range(prefix))
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            params.extend(str(k) for k in kinds)
        if before is not None:
            sql += " AND ts < ?"
            params.append(float(before))
        cur = conn.execute(sql, params)
        conn.commit()
        return int(cur.rowcount or 0)
    except sqlite3.Error as failure:
        log.debug("thoughts not settled: %s", failure)
        return 0
    finally:
        conn.close()


def _as_dict(row) -> Dict[str, Any]:
    out = dict(zip(_COLUMNS, row))
    try:
        out["why"] = json.loads(out.pop("why_json") or "[]")
    except (TypeError, ValueError):
        out["why"] = []
    out["words"] = words(out)
    return out


def recent(limit: int = 40, *, kinds: Optional[Iterable[str]] = None, room: str = "", user_id: Optional[str] = None,
           since: Optional[float] = None, open_only: bool = False, db_path=None) -> List[Dict[str, Any]]:
    """The latest thoughts of one reader, newest first, each with its reasons as a list and its words:
    their own and ELI's shared ones (SHARED_KINDS). `user_id` is the reader (None: the user of the turn
    running in this context, which outside any turn is the owner)."""
    try:
        conn = _connect(db_path)
    except (sqlite3.Error, OSError) as failure:
        log.debug("thoughts unreadable: %s", failure)
        return []
    try:
        sql = f"SELECT {', '.join(_COLUMNS)} FROM thoughts WHERE 1 = 1"
        params: List[Any] = []
        kinds = [str(k) for k in (kinds or []) if str(k)]
        if kinds:
            sql += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            params.extend(kinds)
        if room:
            sql += " AND room = ?"
            params.append(str(room))
        key = _owner_key(user_id)
        from eli.memory.policy import scope_sql
        mine, mine_args = scope_sql("user_id", key, raw_ids=True)
        shared = ",".join("?" * len(SHARED_KINDS))
        sql += f" AND ((1 = 1{mine}) OR (COALESCE(user_id, '') = '' AND kind IN ({shared})))"
        params.extend(mine_args)
        params.extend(SHARED_KINDS)
        if since is not None:
            sql += " AND ts >= ?"
            params.append(float(since))
        if open_only:
            sql += " AND status = 'open'"
        rows = conn.execute(sql + " ORDER BY ts DESC, id DESC LIMIT ?", (*params, int(limit))).fetchall()
    except sqlite3.Error as failure:
        log.debug("thoughts unreadable: %s", failure)
        return []
    finally:
        conn.close()
    return [_as_dict(r) for r in rows]


def by_id(thought_id: Any, *, db_path=None) -> Optional[Dict[str, Any]]:
    """One thought by its row id, with its reasons and words; None when there is none."""
    if not str(thought_id or "").lstrip("-").isdigit():
        return None
    try:
        conn = _connect(db_path)
    except (sqlite3.Error, OSError) as failure:
        log.debug("thoughts unreadable: %s", failure)
        return None
    try:
        row = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM thoughts WHERE id = ?", (int(thought_id),)).fetchone()
    except sqlite3.Error as failure:
        log.debug("thoughts unreadable: %s", failure)
        return None
    finally:
        conn.close()
    return _as_dict(row) if row else None


def words(thought: Dict[str, Any]) -> str:
    """One line in words: what, what was decided and why, how sure, and what became of it."""
    what = str(thought.get("what") or "").strip()
    if not what:
        return ""
    parts = [what.rstrip(".")]
    decision = str(thought.get("decision") or "").strip()
    reason = str(thought.get("reason") or "").strip()
    why = thought.get("why")
    if isinstance(why, str):
        try:
            why = json.loads(why)
        except (TypeError, ValueError):
            why = [why]
    why = [str(w) for w in (why or []) if str(w).strip()]
    if decision:
        parts.append(decision + (f" because {reason}" if reason else ""))
    if why:
        parts.append("came up because " + "; ".join(why[:3]) if decision != "dropped" else "it was " + "; ".join(why[:3]))
    chance = thought.get("chance")
    if chance is not None:
        try:
            parts.append(f"chance {float(chance):.0%}")
        except (TypeError, ValueError) as failure:   # a chance that is not a number is left unsaid
            log.debug("a thought's chance was not a number: %s", failure)
    outcome = str(thought.get("outcome") or "").strip()
    if outcome:
        how = str(thought.get("how") or "").strip()
        parts.append(f"it proved {outcome}" + (f" ({how})" if how else ""))
    return ". ".join(p[0].upper() + p[1:] if p else p for p in parts) + "."


def for_feed(limit: int = 40, *, user_id: Optional[str] = None, db_path=None) -> List[Dict[str, Any]]:
    """The latest thoughts of one reader (recent's `user_id`) as a body draws them: id, kind, room,
    what, why, chance, decision, reason, status, outcome, when (unix time), words, and what the thought
    is about: its subject (an action's name, a memory's words, a relation) and its ref (the record it
    settles with: "claim:<id>:<turn>", "act:<moment>"), so a body can tie a thought to the record it is
    about."""
    out = []
    for t in recent(limit, user_id=user_id, db_path=db_path):
        out.append({"id": int(t["id"]), "kind": str(t["kind"]), "room": str(t["room"] or room_of(t["kind"])),
                    "what": str(t["what"]), "why": list(t.get("why") or []), "chance": t.get("chance"),
                    "decision": str(t.get("decision") or ""), "reason": str(t.get("reason") or ""),
                    "status": str(t.get("status") or ""), "outcome": str(t.get("outcome") or ""),
                    "when": float(t.get("ts") or 0.0), "words": str(t.get("words") or ""),
                    "subject": str(t.get("subject") or ""), "ref": str(t.get("ref") or "")})
    return out


__all__ = ["DAYS", "DECISIONS", "OUTCOMES", "ROOM_OF", "SHARED_KINDS", "by_id", "for_feed", "recent", "record",
           "room_of", "settle", "settle_prefix", "words"]

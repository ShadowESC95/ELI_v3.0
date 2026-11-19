from __future__ import annotations


# eli/memory/memory.py

import json
import os
import sqlite3
import stat
import sys as _sys
import threading
import time
import re
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from eli.core import paths as core_paths
from eli.core.paths import path_get as _path_value
from eli.utils.log import get_logger

log = get_logger(__name__)


# ---------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class DBPaths:
    user_db: Path
    agent_db: Path
    memory_db: Optional[Path] = None


def _resolve_project_artifacts_dir() -> Path:
    """Single source of truth: follow eli.core.paths first."""
    try:
        return core_paths.get_paths().artifacts_dir.resolve()
    except Exception:
        log.debug("[MEMORY] core_paths artifact dir lookup failed", exc_info=True)
    for env_name in ("ELI_DATA_DIR", "ELI_ARTIFACTS_DIR"):
        raw = os.environ.get(env_name, "").strip()
        if raw:
            return Path(raw).expanduser().resolve()

    here = Path(__file__).resolve()
    eli_root = here.parents[1]
    return (eli_root / "artifacts").resolve()


def resolve_db_paths() -> DBPaths:
    """
    Canonical three-DB resolution.

    - memory_db: searchable memories + conversations (consolidated into user.sqlite3)
    - user_db: user profile/state db
    - agent_db: proactive/agent/self-improvement db

    The actual path authority lives in eli.core.paths.
    This compatibility helper preserves older imports that still call resolve_db_paths().
    """
    paths = core_paths.get_paths()

    def _pick(name: str, fallback: Path) -> Path:
        fn = getattr(core_paths, name, None)
        if callable(fn):
            try:
                return Path(fn()).expanduser().resolve()
            except Exception:
                log.debug("[MEMORY] path resolver %s failed", name, exc_info=True)
        if fallback is not None:
            return Path(fallback).expanduser().resolve()
        return _resolve_project_artifacts_dir() / "db" / "user.sqlite3"

    return DBPaths(
        user_db=_pick("get_user_db_path", _path_value(paths, 'user_db')),
        agent_db=_pick("get_agent_db_path", _path_value(paths, 'agent_db')),
        memory_db=Path(_path_value(paths, 'memory_db') or _pick("get_user_db_path", None)).expanduser().resolve(),
    )


# ---------------------------------------------------------------------
# Memory instances (per-db singletons)
# ---------------------------------------------------------------------

_memory_singletons: Dict[str, "Memory"] = {}
_memory_lock = threading.Lock()
# Compat: conftest does `mem_mod._memory_singleton = None` to reset cache.
_memory_singleton = None
_memory = None
_agent_memory_singleton = None
_agent_memory = None

def _clear_memory_singletons() -> None:
    """Clear all cached Memory instances (call between tests)."""
    with _memory_lock:
        _memory_singletons.clear()
    try:
        import eli.runtime.self_improvement as _si

        _si._self_engine = None
    except Exception:
        log.debug("[MEMORY] self_improvement singleton reset failed", exc_info=True)


_this_module = _sys.modules[__name__]


class _MemoryModule(type(_this_module)):
    def __setattr__(self, name, value):
        if name in {"_memory_singleton", "_memory", "_agent_memory_singleton", "_agent_memory"} and value is None:
            _clear_memory_singletons()
        super().__setattr__(name, value)


_this_module.__class__ = _MemoryModule


# Association (Hebbian learning, Hebb 1949, in ACT-R's terms): memories that went together into an
# answer the user endorsed are linked (Memory.strengthen_links); a link's strength is the base-level
# activation of its uses, so it fades without use as a memory does (eli/memory/policy.base_activation);
# and a recall spreads from what it found to what is strongly linked to it, a third channel beside the
# vector and keyword ones. Kept only while it measurably improves recall: its test switches it off
# otherwise (tests/test_memory_learning.py, the replay figures, which measure it with
# ELI_MEMORY_ASSOCIATION=0 against it on).
ASSOCIATION = os.environ.get("ELI_MEMORY_ASSOCIATION", "1").strip() != "0"

# How many candidates each recall channel fetches for each place in the caller's limit: ELI's own
# records, deleted rows and stale index entries are dropped after the fetch, and the one ranking
# (eli/cognition/reranker.rerank_candidates) needs the candidates it chooses between.
_OVERFETCH = 3




def get_memory(db_path: Union[str, Path, None] = None) -> "Memory":
    """
    Get or create a Memory instance for the USER DB by default.

    Important contract:
    - get_memory()         -> user.sqlite3 only
    - get_agent_memory()   -> agent.sqlite3 only
    - get_search_memory()  -> memory_db when configured, else user.sqlite3
    """
    paths = resolve_db_paths()
    if db_path is None:
        db_path = _path_value(paths, 'user_db')
    db_path = Path(db_path).expanduser().resolve()
    key = str(db_path)

    with _memory_lock:
        if key not in _memory_singletons:
            # Canonical rule: default user memory writes only to its own DB.
            # No implicit fan-out into agent.sqlite3 or any secondary store.
            _memory_singletons[key] = Memory(db_path=db_path)
        return _memory_singletons[key]


def get_agent_memory(db_path: Union[str, Path, None] = None) -> "Memory":
    """Get or create Memory for the AGENT DB."""
    paths = resolve_db_paths()
    if db_path is None:
        db_path = _path_value(paths, 'agent_db')
    return get_memory(db_path=db_path)


def get_search_memory(db_path: Union[str, Path, None] = None) -> "Memory":
    """Return the retrieval/search memory authority (memory_db when present, else user_db)."""
    paths = resolve_db_paths()
    if db_path is None:
        db_path = _path_value(paths, 'memory_db') or _path_value(paths, 'user_db')
    return get_memory(db_path=db_path)


def get_memory_authority() -> Dict[str, str]:
    paths = resolve_db_paths()
    return {
        "user_db": str(Path(_path_value(paths, 'user_db')).expanduser().resolve()) if _path_value(paths, 'user_db') else "",
        "agent_db": str(Path(_path_value(paths, 'agent_db')).expanduser().resolve()) if _path_value(paths, 'agent_db') else "",
        "memory_db": str(Path(_path_value(paths, 'memory_db')).expanduser().resolve()) if _path_value(paths, 'memory_db') else "",
    }


# ---------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------

import re as _re_sql


# Allowlist of known table names used in schema migrations.
_KNOWN_TABLES: frozenset = frozenset({
    "memories", "conversation_turns", "conversations", "session_summaries",
    "kg_entities", "kg_relations", "recall_log",
    "improvements", "failures", "habit_rules", "habit_events",
    "memories_fts", "self_improvements", "capabilities",
    "agent_dispatches", "proactive_insights",
})

# Written by the self-improvement engine and the proactive daemon on agent.sqlite3. Both databases are created
# from one schema, so user.sqlite3 carries empty copies of these; reports must read them from the agent database.
AGENT_OWNED_TABLES = ("improvements", "failures", "corrections", "capability_proposals")

_IDENTIFIER_RE = _re_sql.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def _validate_identifier(name: str, context: str = "identifier") -> str:
    """Validate a SQL identifier (table or column name) before f-string interpolation.

    Only allows characters safe for SQL identifiers.  Raises ValueError on
    anything that looks like an injection attempt.
    """
    if not isinstance(name, str) or not _IDENTIFIER_RE.match(name):
        raise ValueError(
            f"Invalid SQL {context} '{name}': only [A-Za-z0-9_] allowed"
        )
    return name


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


# ---------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------

class _MemoryMeta(type):
    """Metaclass that intercepts Memory._instance = None to clear singleton cache."""

    def __setattr__(cls, name, value):
        if name == "_instance" and value is None:
            _clear_memory_singletons()
            return
        super().__setattr__(name, value)


# ═══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════

def _eli_count_table(conn, table_name: str) -> int:
    try:
        _validate_identifier(table_name, "table")
    except (ValueError, Exception):
        return 0
    if not _has_table(conn, table_name):
        return 0
    row = conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()
    return int((row[0] if row else 0) or 0)

def _eli_format_tags(value) -> str:
    if isinstance(value, (list, tuple)):
        return ", ".join(str(x) for x in value if str(x).strip())
    return str(value or "")

def _add_column_if_missing(conn, table, name, decl):
    cols = _table_columns(conn, table)
    if name not in cols:
        t = _validate_identifier(table, "table")
        _validate_identifier(name, "column")
        conn.execute(f"ALTER TABLE {t} ADD COLUMN {name} {decl}")


def _migrate_memory_provenance(conn) -> None:
    """One-time alignment: auto_extracted rows are hypothesis tier, not grounding.

    Runs on every connection, so it reads first: the UPDATE took the write lock each time and failed
    with "database is locked" whenever another writer held it."""
    match = """WHERE LOWER(COALESCE(tags, '')) LIKE '%auto_extracted%'
              AND COALESCE(verification_status, 'verified') = 'verified'
              AND COALESCE(provenance_kind, 'user_verbatim') = 'user_verbatim'"""
    try:
        cols = _table_columns(conn, "memories")
        if "verification_status" not in cols or not conn.execute(
                f"SELECT EXISTS(SELECT 1 FROM memories {match})").fetchone()[0]:
            return
        conn.execute(f"UPDATE memories SET verification_status = 'hypothesis', provenance_kind = 'auto_extract' {match}")
    except sqlite3.OperationalError as e:
        log.debug("provenance alignment waits for the next connection: %s", e)
    except Exception:
        log.debug("suppressed exception", exc_info=True)


# Storage-policy columns (eli/memory/policy.py): event_ts is when it was said (ts is when written),
# seen_count/last_seen count repeats, last_recalled/recall_count count answers a memory helped with,
# exposure_count counts retrievals alone, corrected_count counts answers it was part of that were corrected.
# text_key dedupes.
_POLICY_COLUMNS = [
    ("event_ts", "REAL"),
    ("seen_count", "INTEGER DEFAULT 1"),
    ("last_seen", "REAL"),
    ("last_recalled", "REAL"),
    ("recall_count", "INTEGER DEFAULT 0"),
    ("exposure_count", "INTEGER DEFAULT 0"),
    ("corrected_count", "INTEGER DEFAULT 0"),
    ("origin", "TEXT"),
    ("text_key", "TEXT"),
    # whose the row is (eli/memory/policy.owner_key): '' the owner, an API member's id otherwise
    ("user_id", "TEXT DEFAULT ''"),
]


def _memory_scope(cols, alias: str = "", user_id=None) -> tuple:
    """(" AND ...", params) keeping the memories of the turn's user (eli/memory/policy.scope_sql):
    the owner's rows for the owner and background work, a member's own rows for that member."""
    if "user_id" not in cols:
        return "", ()
    from eli.memory import policy as _policy
    return _policy.scope_sql(f"{alias}user_id", _policy.owner_key(user_id))


def _semantic_scope(key: str) -> tuple:
    """The semantic facts of `key` (owner_key): store_semantic files the owner's under "default"."""
    from eli.memory import policy as _policy
    return _policy.scope_sql("user_id", key, raw_ids=True)


def _policy_min_weight() -> float:
    from eli.memory import policy as _policy
    return _policy.MIN_WEIGHT


def _ensure_policy_schema(conn) -> None:
    """Columns, archive table and meta table for the storage policy. Idempotent."""
    for name, decl in _POLICY_COLUMNS:
        try:
            _add_column_if_missing(conn, "memories", name, decl)
        except Exception:
            log.debug("[MEMORY] policy column %s not added", name, exc_info=True)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(memories)").fetchall()]
        decls = ", ".join(f'"{c}"' for c in cols)
        conn.execute(
            f'CREATE TABLE IF NOT EXISTS memories_archive ({decls}, '
            'archived_at REAL, archive_reason TEXT)'
        )
        arch = {r[1] for r in conn.execute("PRAGMA table_info(memories_archive)").fetchall()}
        for c in cols:
            if c not in arch:
                conn.execute(f'ALTER TABLE memories_archive ADD COLUMN "{c}"')
        conn.execute(
            "CREATE TABLE IF NOT EXISTS memory_meta (key TEXT PRIMARY KEY, value TEXT, updated_at REAL)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_text_key ON memories(text_key)")
        conn.execute("CREATE TABLE IF NOT EXISTS memory_lineage (child_id INTEGER NOT NULL, parent_id INTEGER NOT NULL, "
                     "kind TEXT, created_at REAL, PRIMARY KEY (child_id, parent_id))")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_lineage_parent ON memory_lineage(parent_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_origin ON memories(origin)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id)")
        # Memories that went together into an answer the user endorsed (Memory.strengthen_links), one row
        # per pair (a < b): how often and when, so the link's strength fades as a memory's does.
        conn.execute("CREATE TABLE IF NOT EXISTS memory_links (a INTEGER NOT NULL, b INTEGER NOT NULL, "
                     "user_id TEXT NOT NULL DEFAULT '', uses INTEGER NOT NULL DEFAULT 1, first_used REAL, "
                     "last_used REAL, PRIMARY KEY (a, b, user_id))")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_links_b ON memory_links(b)")
    except Exception:
        log.debug("[MEMORY] policy tables not created", exc_info=True)


# Tables whose rows are one user's (eli/memory/policy.owner_key): '' is the owner, so every row from
# before the column existed is the owner's. memories gets its column with the policy columns
# (_POLICY_COLUMNS), so its archive table carries it too.
_OWNED_TABLES = ("user_patterns", "observations", "habits", "habit_events")
# Conversation rows that are not turns: fragment-guard NOOP entries stored before the NOOP-suppression
# fix (internal routing events; fed back, the model mimics their JSON). Every reader of turns leaves them out.
_NOT_A_TURN_LIKE = ('%"event": "input_fragment_guard"%', '%fragmentary_input%')
# Memories that are ELI's own looking back (a reflection, its own insight), not something it was told.
_OWN_REFLECTION_KINDS = ("reflection", "assistant_insight")


def _ensure_owner_columns(conn) -> None:
    for table in _OWNED_TABLES:
        try:
            if _has_table(conn, table):
                _add_column_if_missing(conn, table, "user_id", "TEXT DEFAULT ''")
        except Exception:
            log.debug("[MEMORY] owner column not added to %s", table, exc_info=True)


def _owned_scope(conn, table: str, user_id=None) -> tuple:
    """(" AND ...", params) keeping the turn's user's rows of one of _OWNED_TABLES; nothing when the
    table has no owner column yet."""
    if "user_id" not in _table_columns(conn, table):
        return "", ()
    from eli.memory import policy as _policy
    return _policy.scope_sql("user_id", _policy.owner_key(user_id))


def _ensure_memory_indexes(conn) -> None:
    """Create the recall indexes. Called LAST, after every table exists.

    This used to sit in the middle of schema creation, four statements sharing one
    `try` that ended in `except Exception: pass`. Two faults compounded:

      * `CREATE INDEX ... ON conversation_turns(...)` ran roughly twenty lines
        BEFORE `CREATE TABLE ... conversation_turns`, so on a fresh database it
        raised "no such table";
      * because all four shared one try, that exception abandoned the rest, and the
        bare `pass` meant nothing was ever reported.

    Net effect, verified on a live 2.3.10 database: `memories` (441 rows),
    `conversation_turns` (617) and `observations` (114) carried NO indexes at all,
    while kg_entities, runtime_events and emotion_events — indexed elsewhere — had
    theirs. Every recall was `SCAN` + `USE TEMP B-TREE FOR ORDER BY`, several times
    per turn. Invisible at a few hundred rows and linear from there.

    Each statement now gets its own try so one failure cannot take the others, and
    failures are logged rather than swallowed.
    """
    indexes = [
        # ORDER BY COALESCE(timestamp, ts) in every recall query
        ("idx_memories_ts", "CREATE INDEX IF NOT EXISTS idx_memories_ts ON memories(ts DESC)"),
        ("idx_memories_timestamp",
         "CREATE INDEX IF NOT EXISTS idx_memories_timestamp ON memories(timestamp DESC)"),
        ("idx_memories_importance",
         "CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance DESC)"),
        # WHERE clause in search_conversations
        ("idx_conversations_user_session",
         "CREATE INDEX IF NOT EXISTS idx_conversations_user_session "
         "ON conversations(user_id, session_id)"),
        # get_recent_conversation: filter by session, order by time
        ("idx_conversation_turns_session",
         "CREATE INDEX IF NOT EXISTS idx_conversation_turns_session "
         "ON conversation_turns(session_id, timestamp DESC)"),
        ("idx_conversation_turns_ts",
         "CREATE INDEX IF NOT EXISTS idx_conversation_turns_ts "
         "ON conversation_turns(timestamp DESC)"),
        # get_recent_observations — was scanning the whole table
        ("idx_observations_timestamp",
         "CREATE INDEX IF NOT EXISTS idx_observations_timestamp ON observations(timestamp DESC)"),
        # Every recency read orders by COALESCE(timestamp, ts, 0), and SQLite uses an index for an
        # ORDER BY only when it is on that same expression: the plain-column indexes above served none
        # of them (SCAN + TEMP B-TREE on every read, about ten a turn).
        ("idx_conversation_turns_time",
         "CREATE INDEX IF NOT EXISTS idx_conversation_turns_time ON conversation_turns(COALESCE(timestamp, ts, 0))"),
        ("idx_conversation_turns_session_time",
         "CREATE INDEX IF NOT EXISTS idx_conversation_turns_session_time "
         "ON conversation_turns(session_id, COALESCE(timestamp, ts, 0))"),
        ("idx_memories_time",
         "CREATE INDEX IF NOT EXISTS idx_memories_time ON memories(COALESCE(timestamp, ts, 0))"),
    ]
    created = 0
    for name, ddl in indexes:
        try:
            conn.execute(ddl)
            created += 1
        except Exception as exc:
            # A missing table or column is worth knowing about; it is why these
            # indexes were absent for so long.
            log.debug("[MEMORY] index %s not created: %s", name, exc)
    log.debug("[MEMORY] recall indexes ensured (%d/%d)", created, len(indexes))


def _table_columns(conn, table):
    try:
        t = _validate_identifier(table, "table")
        return [r[1] for r in conn.execute(f"PRAGMA table_info({t})").fetchall()]
    except (ValueError, Exception):
        return []


def _ensure_contract_schema(conn):
    # improvements
    conn.execute("""
        CREATE TABLE IF NOT EXISTS improvements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            timestamp REAL,
            category TEXT,
            area TEXT,
            description TEXT,
            priority INTEGER DEFAULT 1,
            status TEXT DEFAULT 'pending',
            created_at REAL,
            title TEXT,
            name TEXT,
            improvement TEXT,
            text TEXT,
            content TEXT,
            details TEXT,
            source TEXT,
            count INTEGER DEFAULT 1,
            suggestion TEXT,
            applied INTEGER DEFAULT 0
        )
    """)
    for name, decl in [
        ("ts", "REAL"), ("timestamp", "REAL"), ("category", "TEXT"), ("area", "TEXT"),
        ("description", "TEXT"), ("priority", "INTEGER DEFAULT 1"), ("status", "TEXT DEFAULT 'pending'"),
        ("created_at", "REAL"), ("title", "TEXT"), ("name", "TEXT"), ("improvement", "TEXT"),
        ("text", "TEXT"), ("content", "TEXT"), ("details", "TEXT"), ("source", "TEXT"),
        ("count", "INTEGER DEFAULT 1"), ("suggestion", "TEXT"), ("applied", "INTEGER DEFAULT 0"),
    ]:
        _add_column_if_missing(conn, "improvements", name, decl)

    # failures
    conn.execute("""
        CREATE TABLE IF NOT EXISTS failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            timestamp REAL,
            user_input TEXT,
            command TEXT,
            error TEXT,
            confidence REAL DEFAULT 0.0,
            low_confidence INTEGER DEFAULT 0,
            context TEXT,
            context_json TEXT,
            occurrence_count INTEGER DEFAULT 1,
            first_seen REAL,
            last_seen REAL,
            failure TEXT,
            name TEXT,
            text TEXT,
            content TEXT,
            details TEXT,
            description TEXT,
            source TEXT,
            signature TEXT,
            status TEXT,
            count INTEGER DEFAULT 1,
            traceback TEXT
        )
    """)
    for name, decl in [
        ("ts", "REAL"), ("timestamp", "REAL"), ("user_input", "TEXT"), ("command", "TEXT"),
        ("error", "TEXT"), ("confidence", "REAL DEFAULT 0.0"), ("low_confidence", "INTEGER DEFAULT 0"),
        ("context", "TEXT"), ("context_json", "TEXT"), ("occurrence_count", "INTEGER DEFAULT 1"),
        ("first_seen", "REAL"), ("last_seen", "REAL"), ("failure", "TEXT"), ("name", "TEXT"),
        ("text", "TEXT"), ("content", "TEXT"), ("details", "TEXT"), ("description", "TEXT"),
        ("source", "TEXT"), ("signature", "TEXT"), ("status", "TEXT"), ("count", "INTEGER DEFAULT 1"),
        ("traceback", "TEXT"),
    ]:
        _add_column_if_missing(conn, "failures", name, decl)

def _jsonify_value(v):
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    try:
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(v)


# The same thing was written three times under three names (contract, profile, plain).
_jsonify_contract_value = _jsonify_profile_value = _jsonify_value


# Observation retention. The daemon appends one observation per tick forever and they're
# filtered from every reasoning path, so the only cost is growth. Prune per category so a flood
# of ticks can't evict a real observation, keeping the newest. Throttled: the first insert per
# category prunes, then every _OBS_PRUNE_EVERY inserts.
_OBS_PRUNE_EVERY = 50
_obs_insert_counts: Dict[str, int] = {}


def _prune_observations(conn, category: str) -> int:
    """Trim `category` down to its retention limit. Returns rows deleted."""
    try:
        from eli.core.self_provenance import observation_retention_limit
    except Exception:
        return 0
    key = str(category or "").strip().lower()
    n = _obs_insert_counts.get(key, 0)
    _obs_insert_counts[key] = n + 1
    # n == 0 is the first insert this process: always check.
    if n != 0 and (n % _OBS_PRUNE_EVERY) != 0:
        return 0
    limit = int(observation_retention_limit(key))
    try:
        cols = _table_columns(conn, "observations")
        cat_col = "category" if "category" in cols else ("source" if "source" in cols else None)
        if not cat_col:
            return 0
        order = "COALESCE(timestamp, ts, id)" if {"timestamp", "ts"} & set(cols) else "id"
        total = conn.execute(
            f"SELECT COUNT(*) FROM observations WHERE LOWER(COALESCE({cat_col}, '')) = ?",
            (key,),
        ).fetchone()[0]
        if total <= limit:
            return 0
        cur = conn.execute(
            f"""DELETE FROM observations WHERE id IN (
                    SELECT id FROM observations
                    WHERE LOWER(COALESCE({cat_col}, '')) = ?
                    ORDER BY {order} DESC
                    LIMIT -1 OFFSET ?
                )""",
            (key, limit),
        )
        conn.commit()
        deleted = int(cur.rowcount or 0)
        if deleted:
            log.debug("[MEMORY] pruned %d '%s' observations (kept %d)",
                      deleted, key, limit)
        return deleted
    except Exception:
        log.debug("observation prune failed", exc_info=True)
        return 0



def _now_ts():
    return float(time.time())

def _ensure_full_memory_schema(conn):
    # Include `importance` in fresh DBs so all storage paths share one schema.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            value TEXT,
            content TEXT,
            tags TEXT,
            kind TEXT,
            ts REAL,
            timestamp REAL,
            source TEXT,
            status TEXT,
            weight REAL DEFAULT 1.0,
            confidence REAL DEFAULT 1.0,
            importance REAL DEFAULT 0.5
        )
    """)
    # Existing DBs get missing columns on connection open. Idempotent:
    # _add_column_if_missing is a no-op if the column already exists.
    for n, d in [
        ("content", "TEXT"), ("timestamp", "REAL"), ("source", "TEXT"),
        ("status", "TEXT"), ("weight", "REAL DEFAULT 1.0"), ("confidence", "REAL DEFAULT 1.0"),
        ("kind", "TEXT"), ("tags", "TEXT"), ("text", "TEXT"), ("value", "TEXT"), ("ts", "REAL"),
        ("importance", "REAL DEFAULT 0.5"),
        ("verification_status", "TEXT DEFAULT 'verified'"),
        ("provenance_kind", "TEXT DEFAULT 'user_verbatim'"),
    ]:
        _add_column_if_missing(conn, "memories", n, d)

    _migrate_memory_provenance(conn)
    _ensure_policy_schema(conn)

    try:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(text, tags, content='memories', content_rowid='id')
        """)
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            user_id TEXT,
            role TEXT,
            content TEXT,
            timestamp REAL,
            ts REAL,
            created_at REAL,
            updated_at REAL,
            title TEXT
        )
    """)
    for n, d in [
        ("session_id", "TEXT"), ("user_id", "TEXT"), ("role", "TEXT"), ("content", "TEXT"),
        ("timestamp", "REAL"), ("ts", "REAL"), ("created_at", "REAL"), ("updated_at", "REAL"),
        ("title", "TEXT")
    ]:
        _add_column_if_missing(conn, "conversations", n, d)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS conversation_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            user_id TEXT,
            role TEXT,
            content TEXT,
            ts REAL,
            timestamp REAL
        )
    """)
    for n, d in [
        ("session_id", "TEXT"), ("user_id", "TEXT"), ("role", "TEXT"),
        ("content", "TEXT"), ("ts", "REAL"), ("timestamp", "REAL")
    ]:
        _add_column_if_missing(conn, "conversation_turns", n, d)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS recall_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            timestamp REAL,
            query TEXT,
            results_count INTEGER,
            result_count INTEGER,
            memory_id INTEGER
        )
    """)
    for n, d in [
        ("ts", "REAL"), ("timestamp", "REAL"), ("query", "TEXT"),
        ("results_count", "INTEGER"), ("result_count", "INTEGER"), ("memory_id", "INTEGER")
    ]:
        _add_column_if_missing(conn, "recall_log", n, d)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS habit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            event TEXT,
            details TEXT,
            data TEXT,
            timestamp REAL,
            ts REAL,
            name TEXT,
            cmd TEXT,
            method TEXT,
            command TEXT
        )
    """)
    for n, d in [
        ("event_type", "TEXT"), ("event", "TEXT"), ("details", "TEXT"), ("data", "TEXT"),
        ("timestamp", "REAL"), ("ts", "REAL"), ("name", "TEXT"), ("cmd", "TEXT"),
        ("method", "TEXT"), ("command", "TEXT")
    ]:
        _add_column_if_missing(conn, "habit_events", n, d)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS habit_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            command TEXT,
            hour INTEGER,
            minute INTEGER,
            days TEXT,
            enabled INTEGER DEFAULT 1,
            timestamp REAL,
            ts REAL,
            pattern TEXT,
            action TEXT,
            trigger_phrase TEXT,
            action_type TEXT
        )
    """)
    for n, d in [
        ("name", "TEXT"), ("command", "TEXT"), ("hour", "INTEGER"), ("minute", "INTEGER"),
        ("days", "TEXT"), ("enabled", "INTEGER DEFAULT 1"), ("timestamp", "REAL"),
        ("ts", "REAL"), ("pattern", "TEXT"), ("action", "TEXT"),
        ("trigger_phrase", "TEXT"), ("action_type", "TEXT")
    ]:
        _add_column_if_missing(conn, "habit_rules", n, d)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS habits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            cmd TEXT,
            method TEXT,
            count INTEGER DEFAULT 0,
            hour INTEGER,
            minute INTEGER,
            timestamp REAL,
            command TEXT,
            enabled INTEGER DEFAULT 1,
            ts REAL
        )
    """)
    for n, d in [
        ("name", "TEXT"), ("cmd", "TEXT"), ("method", "TEXT"), ("count", "INTEGER DEFAULT 0"),
        ("hour", "INTEGER"), ("minute", "INTEGER"), ("timestamp", "REAL"),
        ("command", "TEXT"), ("enabled", "INTEGER DEFAULT 1"), ("ts", "REAL")
    ]:
        _add_column_if_missing(conn, "habits", n, d)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS corrections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original TEXT,
            corrected TEXT,
            timestamp REAL,
            ts REAL
        )
    """)
    for n, d in [("original", "TEXT"), ("corrected", "TEXT"), ("timestamp", "REAL"), ("ts", "REAL")]:
        _add_column_if_missing(conn, "corrections", n, d)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            category TEXT,
            observation TEXT,
            content TEXT,
            text TEXT,
            details TEXT,
            timestamp REAL,
            ts REAL
        )
    """)
    for n, d in [
        ("source", "TEXT"), ("category", "TEXT"), ("observation", "TEXT"),
        ("content", "TEXT"), ("text", "TEXT"), ("details", "TEXT"),
        ("timestamp", "REAL"), ("ts", "REAL")
    ]:
        _add_column_if_missing(conn, "observations", n, d)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS session_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            user_id TEXT,
            summary TEXT,
            content TEXT,
            turns_count INTEGER,
            started_at REAL,
            ended_at REAL,
            source TEXT,
            timestamp REAL,
            ts REAL
        )
    """)
    for n, d in [
        ("session_id", "TEXT"), ("user_id", "TEXT"), ("summary", "TEXT"),
        ("content", "TEXT"), ("turns_count", "INTEGER"), ("started_at", "REAL"),
        ("ended_at", "REAL"), ("source", "TEXT"), ("timestamp", "REAL"), ("ts", "REAL")
    ]:
        _add_column_if_missing(conn, "session_summaries", n, d)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS improvements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            timestamp REAL,
            category TEXT,
            area TEXT,
            description TEXT,
            priority INTEGER DEFAULT 1,
            status TEXT DEFAULT 'pending',
            created_at REAL,
            title TEXT,
            name TEXT,
            improvement TEXT,
            text TEXT,
            content TEXT,
            details TEXT,
            source TEXT,
            count INTEGER DEFAULT 1,
            suggestion TEXT,
            applied INTEGER DEFAULT 0
        )
    """)
    for n, d in [
        ("ts", "REAL"), ("timestamp", "REAL"), ("category", "TEXT"), ("area", "TEXT"),
        ("description", "TEXT"), ("priority", "INTEGER DEFAULT 1"), ("status", "TEXT"),
        ("created_at", "REAL"), ("title", "TEXT"), ("name", "TEXT"),
        ("improvement", "TEXT"), ("text", "TEXT"), ("content", "TEXT"),
        ("details", "TEXT"), ("source", "TEXT"), ("count", "INTEGER DEFAULT 1"),
        ("suggestion", "TEXT"), ("applied", "INTEGER DEFAULT 0")
    ]:
        _add_column_if_missing(conn, "improvements", n, d)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            timestamp REAL,
            user_input TEXT,
            command TEXT,
            error TEXT,
            traceback TEXT,
            confidence REAL,
            low_confidence INTEGER DEFAULT 0,
            context TEXT,
            context_json TEXT,
            occurrence_count INTEGER DEFAULT 1,
            first_seen REAL,
            last_seen REAL,
            failure TEXT,
            name TEXT,
            text TEXT,
            content TEXT,
            details TEXT,
            description TEXT,
            source TEXT,
            signature TEXT,
            status TEXT,
            count INTEGER DEFAULT 1
        )
    """)
    for n, d in [
        ("ts", "REAL"), ("timestamp", "REAL"), ("user_input", "TEXT"), ("command", "TEXT"),
        ("error", "TEXT"), ("traceback", "TEXT"), ("confidence", "REAL"),
        ("low_confidence", "INTEGER DEFAULT 0"), ("context", "TEXT"), ("context_json", "TEXT"),
        ("occurrence_count", "INTEGER DEFAULT 1"), ("first_seen", "REAL"), ("last_seen", "REAL"),
        ("failure", "TEXT"), ("name", "TEXT"), ("text", "TEXT"), ("content", "TEXT"),
        ("details", "TEXT"), ("description", "TEXT"), ("source", "TEXT"),
        ("signature", "TEXT"), ("status", "TEXT"), ("count", "INTEGER DEFAULT 1")
    ]:
        _add_column_if_missing(conn, "failures", n, d)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS capability_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            timestamp REAL,
            proposed_name TEXT,
            capability TEXT,
            description TEXT,
            examples TEXT,
            plugin_code TEXT,
            reasoning TEXT,
            rationale TEXT,
            status TEXT DEFAULT 'pending',
            priority TEXT,
            notes TEXT,
            created_at REAL,
            updated_at REAL,
            source TEXT,
            category TEXT
        )
    """)
    for n, d in [
        ("ts", "REAL"), ("timestamp", "REAL"), ("proposed_name", "TEXT"), ("capability", "TEXT"),
        ("description", "TEXT"), ("examples", "TEXT"), ("plugin_code", "TEXT"),
        ("reasoning", "TEXT"), ("rationale", "TEXT"), ("status", "TEXT"),
        ("priority", "TEXT"), ("notes", "TEXT"), ("created_at", "REAL"),
        ("updated_at", "REAL"), ("source", "TEXT"), ("category", "TEXT")
    ]:
        _add_column_if_missing(conn, "capability_proposals", n, d)

    conn.execute("DROP TABLE IF EXISTS error_tracking")     # the second failure store (see above)
    _ensure_owner_columns(conn)
    _ensure_conversation_search(conn)
    # The recall indexes were created only by the old init_db schema: a live database
    # carried none of them and every recency read scanned and sorted the whole table.
    _ensure_memory_indexes(conn)


def _ensure_conversation_search(conn) -> None:
    """A full-text index over what was said (conversation_turns), kept by triggers whoever writes the
    turn: search_conversations ORed LIKE '%word%' over the whole table on every turn."""
    try:
        have = _has_table(conn, "conversation_turns_fts")
        conn.executescript("""
            CREATE VIRTUAL TABLE IF NOT EXISTS conversation_turns_fts
                USING fts5(content, content='conversation_turns', content_rowid='id');
            CREATE TRIGGER IF NOT EXISTS conversation_turns_fts_ai AFTER INSERT ON conversation_turns BEGIN
                INSERT INTO conversation_turns_fts(rowid, content) VALUES (new.id, COALESCE(new.content, ''));
            END;
            CREATE TRIGGER IF NOT EXISTS conversation_turns_fts_ad AFTER DELETE ON conversation_turns BEGIN
                INSERT INTO conversation_turns_fts(conversation_turns_fts, rowid, content)
                VALUES ('delete', old.id, COALESCE(old.content, ''));
            END;
            CREATE TRIGGER IF NOT EXISTS conversation_turns_fts_au AFTER UPDATE OF content ON conversation_turns BEGIN
                INSERT INTO conversation_turns_fts(conversation_turns_fts, rowid, content)
                VALUES ('delete', old.id, COALESCE(old.content, ''));
                INSERT INTO conversation_turns_fts(rowid, content) VALUES (new.id, COALESCE(new.content, ''));
            END;
        """)
        if not have:
            conn.execute("INSERT INTO conversation_turns_fts(conversation_turns_fts) VALUES('rebuild')")
    except sqlite3.Error as exc:
        log.debug("[MEMORY] conversation search index not created: %s", exc)

def _plugin_may(capability: str, what: str) -> None:
    """A plugin behind this call must have declared `capability` (eli/plugins/sandbox.enforce, which
    lets ELI's own calls pass): the memory was open to any plugin running in ELI's process."""
    from eli.plugins.sandbox import enforce
    enforce(capability, what)


def _examples_json(v):
    if v is None:
        return "[]"
    if isinstance(v, str):
        try:
            json.loads(v)
            return v
        except Exception:
            return json.dumps([v], ensure_ascii=False)
    try:
        return json.dumps(list(v), ensure_ascii=False)
    except Exception:
        return json.dumps([str(v)], ensure_ascii=False)

def _is_readonly_error(exc: Exception) -> bool:
    """True for the SQLITE_READONLY family ('attempt to write a readonly database')."""
    return isinstance(exc, sqlite3.OperationalError) and "readonly" in str(exc).lower()


def _repair_readonly_db(p: Path) -> str | None:
    """Try to make *p* writable again. Returns a description of what was fixed.

    A store can become unwritable without the user doing anything wrong:

      * the install (or an earlier run) happened under ``sudo``, leaving
        root-owned ``-wal`` / ``-shm`` sidecars that a later normal-user launch
        cannot write — SQLite then reports SQLITE_READONLY on the FIRST write
        (``PRAGMA journal_mode=WAL``) even though the directory and the database
        file itself are perfectly writable;
      * an archive or copy preserved a read-only mode bit on the database file.

    Both are recoverable in place. Sidecars are only removed when they are
    unwritable AND the main database is intact — a WAL we can still write is
    never touched, so committed-but-uncheckpointed data is never discarded.
    """
    fixed: list[str] = []
    try:
        if p.is_file() and not os.access(p, os.W_OK) and _owned_by_us(p):
            p.chmod(p.stat().st_mode | stat.S_IWUSR)
            fixed.append("cleared the read-only bit on the database file")
    except Exception:
        log.debug("[MEMORY] could not clear read-only bit on %s", p, exc_info=True)
    for suffix in ("-wal", "-shm"):
        side = Path(str(p) + suffix)
        try:
            if side.exists() and not os.access(side, os.W_OK):
                side.unlink()
                fixed.append(f"removed an unwritable {suffix} sidecar (left by a root/sudo run)")
        except Exception:
            log.debug("[MEMORY] could not remove unwritable sidecar %s", side, exc_info=True)
    return "; ".join(fixed) or None


# Public name: other stores (profile, knowledge graph, news, evidence ledger) open user.sqlite3 with
# their own connections and would each hit the same unwritable-sidecar condition. init_data repairs
# every store up front so recovery doesn't depend on which one opens the file first.
repair_unwritable_db = _repair_readonly_db


def _owned_by_us(p: Path) -> bool:
    try:
        return p.stat().st_uid == os.getuid()
    except Exception:
        return False  # Windows has no uid; chmod there is a no-op anyway


def _open_memory_db(p: Path):
    """Open *p* with WAL where the filesystem supports it, DELETE where it doesn't.

    Two distinct portability hazards are handled here:

    * **WAL-hostile filesystem** (NTFS/exFAT/FAT, network mounts) — ``PRAGMA
      journal_mode=WAL`` reports SQLITE_READONLY even though the file is writable.
      ``apply_pragmas`` falls back to a rollback journal, which works everywhere.
      This is the common cause of "attempt to write a readonly database" for a
      portable build extracted onto a non-ext4 partition.
    * **Recoverable read-only file/sidecars** (a root/sudo run left them
      unwritable) — ``_repair_readonly_db`` clears the mode bit / removes the
      stale sidecars before the open, then a single retry.

    ``_repair_readonly_db`` is a couple of ``stat`` calls when everything is
    healthy, which is the overwhelmingly common case.
    """
    from eli.core.sqlite_util import apply_pragmas
    _repair_readonly_db(p)
    last: Exception | None = None
    for attempt in (0, 1):
        conn = sqlite3.connect(str(p))
        try:
            apply_pragmas(conn, db_path=str(p), synchronous="NORMAL", cache_size=-64000)
            return conn
        except Exception as exc:
            try:
                conn.close()
            except Exception:
                log.debug("[MEMORY] failed to close connection during recovery", exc_info=True)
            if attempt or not _is_readonly_error(exc):
                raise
            last = exc
            # Best-effort repair, then retry unconditionally: opening the
            # database can itself reset stale sidecars, so a retry is worth a
            # shot even when there was nothing explicit to fix.
            what = _repair_readonly_db(p) or "no explicit repair needed"
            log.warning("[MEMORY] %s was not writable — %s; retrying", p, what)
    raise last  # unreachable; keeps the contract explicit


# The schema each database was last brought up to in this process, by its PRAGMA schema_version: a
# connection whose database still has that version needs no migration. Every connection used to run
# the whole migration again (about 190 PRAGMA table_info calls and twenty CREATE ... IF NOT EXISTS),
# dozens of times a turn. A database replaced underneath (a memory wipe, a restore) or changed by
# another writer reads another version, and is brought up again.
_SCHEMA_SEEN: Dict[str, int] = {}
_SCHEMA_LOCK = threading.Lock()


def _schema_version(conn) -> Optional[int]:
    try:
        return int(conn.execute("PRAGMA schema_version").fetchone()[0])
    except (sqlite3.Error, TypeError, ValueError):
        return None


def _ensure_schema_once(conn, p) -> None:
    key = str(p)
    if _SCHEMA_SEEN.get(key) is not None and _SCHEMA_SEEN.get(key) == _schema_version(conn):
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_SEEN.get(key) is not None and _SCHEMA_SEEN.get(key) == _schema_version(conn):
            return
        _ensure_full_memory_schema(conn)
        _ensure_profile_schema(conn)
        conn.commit()
        version = _schema_version(conn)
        if version is not None:
            _SCHEMA_SEEN[key] = version


def _memory_connection(self):
    p = _path_from_args(db_path=getattr(self, "db_path", None), db_type=getattr(self, "db_type", None))
    self.db_path = p
    conn = _open_memory_db(p)  # sets journal mode (WAL or DELETE) + synchronous + cache
    _ensure_schema_once(conn, p)
    return conn

def _habit_key(event_type, details) -> Tuple[str, str]:
    """(name, command) a habit event is tallied under: the app, the named thing or the action."""
    key_name = _norm_text(event_type)
    key_cmd = key_name
    if isinstance(details, dict):
        key_name = _norm_text(details.get("app") or details.get("name") or details.get("action") or event_type)
        key_cmd = _norm_text(details.get("cmd") or details.get("command") or details.get("action")
                             or details.get("app") or key_name)
    return key_name, key_cmd


def _get_habit_events_window(self, event_type=None, days=14, user_key=None):
    """Habit events of the last `days`; with user_key (eli/memory/policy.owner_key) one user's only."""
    window = _now_ts() - float(days) * 86400.0
    conn = self._get_connection()
    try:
        sql = "SELECT id, COALESCE(event_type, event, name, ''), COALESCE(details, data, ''), COALESCE(timestamp, ts, 0) FROM habit_events WHERE COALESCE(timestamp, ts, 0) >= ?"
        params = [window]
        if user_key is not None:
            frag, frag_params = _owned_scope(conn, "habit_events", user_key)
            sql += frag
            params.extend(frag_params)
        if event_type:
            sql += " AND COALESCE(event_type, event, name, '') = ?"
            params.append(event_type)
        sql += " ORDER BY COALESCE(timestamp, ts, 0) ASC"
        rows = conn.execute(sql, params).fetchall()
        out = []
        for rid, et, det, ts in rows:
            parsed = det
            if isinstance(det, str):
                try:
                    parsed = json.loads(det)
                except Exception:
                    parsed = det
            out.append({
                "id": rid,
                "event_type": et,
                "details": parsed,
                "data": parsed,
                "timestamp": ts,
            })
        return out
    finally:
        conn.close()

def _has_table(conn, table):
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None
    except Exception:
        return False

def _insert_payload(conn, table, payload):
    cols = set(_table_columns(conn, table))
    ordered = [k for k in payload.keys() if k in cols]
    vals = [payload[k] for k in ordered]
    sql = f"INSERT INTO {table} ({', '.join(ordered)}) VALUES ({', '.join(['?'] * len(ordered))})"
    cur = conn.execute(sql, vals)
    return int(cur.lastrowid)

def _norm_text(v):
    if v is None:
        return ""
    return str(v)

def _path_from_args(db_path=None, db_type=None):
    if db_path is None:
        paths = resolve_db_paths()
        if db_type == "agent":
            db_path = _path_value(paths, 'agent_db')
        elif db_type == "memory":
            db_path = getattr(paths, "memory_db", None) or _path_value(paths, 'user_db')
        else:
            db_path = _path_value(paths, 'user_db')

    p = Path(str(db_path)).expanduser()
    suffix = p.suffix.lower()

    if suffix not in {".db", ".sqlite", ".sqlite3"}:
        name = (db_type or "").strip().lower()
        if name == "agent":
            p = p / "agent.sqlite3"
        elif name == "memory":
            # memory_db is consolidated into the canonical user DB (single store).
            p = p / "user.sqlite3"
        else:
            p = p / "user.sqlite3"

    p.parent.mkdir(parents=True, exist_ok=True)
    return p.resolve()

def _ensure_profile_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_type TEXT,
            pattern_data TEXT,
            timestamp REAL,
            ts REAL
        )
    """)
    for n, d in [
        ("pattern_type", "TEXT"),
        ("pattern_data", "TEXT"),
        ("timestamp", "REAL"),
        ("ts", "REAL"),
    ]:
        _add_column_if_missing(conn, "user_patterns", n, d)
    _ensure_owner_columns(conn)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS learning_replay (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            input_text TEXT,
            output_text TEXT,
            action TEXT,
            outcome TEXT,
            reward REAL,
            metadata TEXT,
            timestamp REAL,
            ts REAL
        )
    """)
    for n, d in [
        ("event_type", "TEXT"),
        ("input_text", "TEXT"),
        ("output_text", "TEXT"),
        ("action", "TEXT"),
        ("outcome", "TEXT"),
        ("reward", "REAL"),
        ("metadata", "TEXT"),
        ("timestamp", "REAL"),
        ("ts", "REAL"),
    ]:
        _add_column_if_missing(conn, "learning_replay", n, d)

    if _has_table(conn, "session_summaries"):
        for n, d in [
            ("user_id", "TEXT"),
            ("started_at", "REAL"),
            ("ended_at", "REAL"),
            ("source", "TEXT"),
            ("turns_count", "INTEGER"),
            ("timestamp", "REAL"),
            ("ts", "REAL"),
        ]:
            _add_column_if_missing(conn, "session_summaries", n, d)

    if _has_table(conn, "observations"):
        for n, d in [
            ("category", "TEXT"),
            ("observation", "TEXT"),
            ("content", "TEXT"),
            ("text", "TEXT"),
            ("details", "TEXT"),
            ("source", "TEXT"),
            ("timestamp", "REAL"),
            ("ts", "REAL"),
        ]:
            _add_column_if_missing(conn, "observations", n, d)


def _fts_delete(conn, rowid: int, text: str, tags: str) -> None:
    """Remove one row from memories_fts.

    External-content FTS5 cannot look the old terms up itself — the 'delete'
    command has to be given the exact text and tags the row was indexed with, and
    a mismatch corrupts the index rather than erroring. Callers therefore pass the
    values read from the row BEFORE any update.
    """
    try:
        conn.execute(
            "INSERT INTO memories_fts(memories_fts, rowid, text, tags) "
            "VALUES('delete', ?, ?, ?)",
            (int(rowid), text or "", tags or ""),
        )
    except Exception:
        log.debug("[MEMORY] fts delete failed for rowid %s", rowid, exc_info=True)


def _fts_rebuild(conn) -> bool:
    """Regenerate memories_fts from the `memories` table.

    Called after a consolidation pass removes rows, to clear index entries that
    point at rowids which no longer exist. A stale entry makes keyword recall —
    now half of hybrid retrieval — return a memory that cannot be read back, so
    the join drops it and the result slot is wasted.

    This rebuilds unconditionally rather than checking for orphans first, because
    on an external-content FTS5 table there is no way to ask. Any query without a
    MATCH reads *through* to the content table, so `SELECT rowid FROM memories_fts`
    and `COUNT(*)` both report the live rows and can never reveal a stale one — a
    live database with three known orphans (rowids 2, 34 and 36, drift from some
    removal that predates any delete path existing) reports zero by that route.

    Only reachable from the consolidation tick, which is sampled at 0.1% of
    responses, so the O(rows) cost is paid rarely.
    """
    try:
        conn.execute("INSERT INTO memories_fts(memories_fts) VALUES('rebuild')")
        return True
    except Exception:
        log.debug("[MEMORY] fts rebuild failed", exc_info=True)
        return False


def _fts_reindex(conn, rowid: int, text: str, tags: str) -> None:
    """Re-insert a row into memories_fts after its indexed columns changed."""
    try:
        conn.execute(
            "INSERT INTO memories_fts(rowid, text, tags) VALUES (?, ?, ?)",
            (int(rowid), text or "", tags or ""),
        )
    except Exception:
        log.debug("[MEMORY] fts reindex failed for rowid %s", rowid, exc_info=True)


class Memory(metaclass=_MemoryMeta):
    """
    SQLite-backed memory store.
    """

    def __init__(self, db_path=None, db_type=None, *args, **kwargs):
        dtype = db_type or kwargs.get("db_type")
        self.db_path = _path_from_args(db_path=db_path, db_type=dtype)
        if dtype:
            self.db_type = str(dtype)
        else:
            self.db_type = "agent" if self.db_path.name.lower() == "agent.sqlite3" else "user"
        # Whether this instance is the canonical default store. The process-global knowledge-graph and
        # vector singletons are bound to the canonical DB and ignore a custom db_path, so they may only
        # enrich recall for the canonical instance. Otherwise an isolated one (tests, per-project
        # workspaces) leaks global content into its recall.
        try:
            self._is_canonical = (self.db_path == _path_from_args(db_path=None, db_type=dtype))
        except Exception:
            self._is_canonical = True
        conn = self._get_connection()
        conn.close()

    def init_db(self) -> None:
        self._ensure_tables()

    def init_self_improvement_tables(self) -> None:
        self._ensure_tables()

    # -----------------------------
    # connection
    # -----------------------------

    def _get_connection(self):
        return _memory_connection(self)

    def _ensure_tables(self):
        """The schema, as every connection brings it up (_ensure_full_memory_schema): there was a second
        builder for init_db with its own copies of the tables and FTS triggers, so a database made by it
        indexed every memory twice."""
        self._get_connection().close()

    @property
    def vector_store(self):
        try:
            from eli.memory.vector_store import get_vector_store
            return get_vector_store()
        except Exception:
            return None

    def _store_to_db(self, conn: sqlite3.Connection, text: str, tags: Optional[List[str]] = None,
                     source: str = "user", kind: str = "memory", confidence: float = 1.0,
                     ts: float = None, importance: float = 0.5,
                     verification_status: str = "verified",
                     provenance_kind: str = "user_verbatim",
                     event_ts: Optional[float] = None, origin: str = "",
                     text_key: str = "", user_key: str = "") -> int:
        """Internal method to write a memory to a single DB connection."""
        t = (text or "").strip()
        if not t:
            raise ValueError("empty_text")
        if isinstance(tags, str):
            tags = [x.strip() for x in tags.split(",") if x.strip()]
        tag_blob = ",".join([x.strip() for x in (tags or []) if x.strip()])
        if ts is None:
            ts = time.time()
        cols = _table_columns(conn, "memories")
        row = {
            "ts": ts, "timestamp": ts, "kind": kind, "text": t, "value": t, "tags": tag_blob,
            "source": source, "confidence": float(confidence), "weight": 1.0,
            "importance": float(importance),
        }
        if "verification_status" in cols and "provenance_kind" in cols:
            row["verification_status"] = verification_status
            row["provenance_kind"] = provenance_kind
        if "origin" in cols:
            row.update({
                "event_ts": float(event_ts if event_ts else ts), "seen_count": 1,
                "last_seen": ts, "recall_count": 0, "origin": origin or "",
                "text_key": text_key or "",
            })
        if "user_id" in cols:
            row["user_id"] = user_key or ""
        names = list(row)
        cur = conn.execute(
            f"INSERT INTO memories ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
            [row[n] for n in names],
        )
        rowid = cur.lastrowid
        try:
            conn.execute(
                "INSERT INTO memories_fts(rowid, text, tags) VALUES (?, ?, ?)",
                (rowid, t, tag_blob),
            )
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        return rowid

    def importance_of(self, text: str, *, source: str = "user", kind: str = "memory", tags=None,
                      user_id: Optional[str] = None) -> Dict[str, Any]:
        """A new memory's importance (eli/memory/salience.importance_of), its novelty read against the
        claims this user's store holds and the relations their graph holds. Read before the memory's
        own claims and relations are written, so a statement is never compared with itself."""
        from eli.memory import claims as _claims
        from eli.memory import policy as _policy
        from eli.memory.salience import importance_of as _importance_of
        held: List[Dict[str, Any]] = []
        try:
            held = self._claims_query(_claims.current, self._claim_subject(user_id))
        except (sqlite3.Error, OSError, ValueError) as failure:
            log.debug("held claims not read for novelty: %s", failure)
        graph_holds = None
        if getattr(self, "_is_canonical", True):
            try:
                from eli.memory.knowledge_graph import get_knowledge_graph
                _kg, _who = get_knowledge_graph(), _policy.owner_key(user_id)
                graph_holds = lambda predicate, obj: _kg.holds(predicate, obj, user_id=_who)  # noqa: E731
            except (sqlite3.Error, OSError, ImportError) as failure:
                log.debug("graph not read for novelty: %s", failure)
        return _importance_of(text, source=source, kind=kind, tags=tags, held_claims=held, graph_holds=graph_holds)

    @staticmethod
    def _touch_duplicate(conn, text_key: str, origin: str, now: float, importance: float,
                         user_key: str = "") -> Optional[int]:
        """If this user already has this statement stored, count the new sighting and return its id."""
        if not text_key:
            return None
        cols = _table_columns(conn, "memories")
        if "text_key" not in cols or "seen_count" not in cols:
            return None
        scope, scope_params = _memory_scope(cols, "", user_key or "")
        row = conn.execute(
            "SELECT id FROM memories WHERE text_key = ? AND COALESCE(origin, '') = ?" + scope + " "
            "ORDER BY id ASC LIMIT 1", (text_key, origin or "", *scope_params),
        ).fetchone()
        if not row:
            return None
        # The sighting is counted and dated in last_seen; the row keeps the date it was first said
        # (timestamp, event_ts): "I adopted a cat" said again three weeks later was dated three weeks later.
        conn.execute(
            "UPDATE memories SET seen_count = COALESCE(seen_count, 1) + 1, last_seen = ?, "
            "importance = MAX(COALESCE(importance, 0.5), ?), weight = 1.0 WHERE id = ?",
            (float(now), float(importance if importance is not None else 0.5), int(row[0])),
        )
        return int(row[0])

    def store_memory(
        self,
        text: str,
        tags: Optional[List[str]] = None,
        source: str = "user",
        kind: str = "memory",
        confidence: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
        importance: Optional[float] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Store memory in primary DB and all secondary DBs.

        Parameters
        ----------
        importance : override the auto-computed salience score (0.0–1.0).
            Pass None to have it read from the evidence for it (importance_of).
        user_id : whose memory it is; None reads the turn's own (eli/memory/policy.owner_key).
        """
        _plugin_may("memory_write", "store a memory")
        t = (text or "").strip()
        if not t:
            return {"ok": False, "error": "empty_text"}

        # Persistence gate is part of the normal write path, in a test run too: it was skipped in test
        # mode, so no test could reach the gate the product runs (a test that stores fragments turns
        # the gate aside itself: tests/conftest.py force_persistence_gate).
        if callable(_eli_should_store_memory_text):
            if not _eli_should_store_memory_text(t, role=str(source or "user"), tags=tags):
                # nothing was stored: every caller reading only ok counted it as kept
                return {"ok": False, "skipped": True, "reason": "persistence_gate"}

        meta = metadata or {}
        if isinstance(meta, dict):
            source = str(meta.get("source", source) or source)
            kind = str(meta.get("kind", kind) or kind)
            try:
                confidence = float(meta.get("confidence", confidence))
            except Exception:
                confidence = float(confidence)
            if importance is None and "importance" in meta:
                try:
                    importance = float(meta["importance"])
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]

        # Project namespacing: scope durable facts to the active project (if set) so a project
        # accumulates its own memories. Only plain "memory" facts are tagged, never reflections,
        # session summaries or continuity (those are global). No active project, no change.
        if kind == "memory":
            try:
                from eli.runtime.active_project import active_memory_tag
                _ptag = active_memory_tag()
                if _ptag:
                    tags = list(tags or [])
                    if _ptag not in tags:
                        tags.append(_ptag)
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        # Importance from the evidence for it (eli/memory/salience.importance_of): the user asking for it
        # to be kept, and what it states about them that ELI did not already hold.
        if importance is None:
            importance = self.importance_of(t, source=source, kind=kind, tags=tags, user_id=user_id)["importance"]

        try:
            from eli.runtime.memory_provenance import resolve_write_provenance
            verification_status, provenance_kind = resolve_write_provenance(
                source=source, kind=kind, tags=tags, metadata=meta, text=t,
            )
        except Exception:
            verification_status, provenance_kind = "verified", "user_verbatim"

        from eli.memory import policy as _policy
        origin = _policy.classify_origin(source, kind, tags, t)
        _key = _policy.text_key(t)
        _who = _policy.owner_key(user_id)
        ts = time.time()
        try:
            event_ts = float(meta.get("event_ts")) if isinstance(meta, dict) and meta.get("event_ts") else None
        except (TypeError, ValueError):
            event_ts = None

        # a repeat bumps the existing row's seen_count instead of adding a row
        conn_primary = self._get_connection()
        try:
            _dup = self._touch_duplicate(conn_primary, _key, origin, ts, importance, _who)
            if _dup is not None:
                conn_primary.commit()
                return {"ok": True, "id": _dup, "deduplicated": True, "importance": importance,
                        "origin": origin, "vector_indexed": False}
            rowid = self._store_to_db(
                conn_primary, t, tags, source, kind, confidence, ts,
                importance=importance,
                verification_status=verification_status,
                provenance_kind=provenance_kind,
                event_ts=event_ts, origin=origin, text_key=_key, user_key=_who,
            )
            conn_primary.commit()
        finally:
            conn_primary.close()
        # Also index in the FAISS vector store. Best effort (the SQL row is the durable record), but
        # a failed write mustn't be invisible: recall_memory() tries FAISS first, so a silently
        # unindexed row stays unrecallable by semantic search though store_memory() reports ok.
        vector_indexed = False
        try:
            from eli.memory.vector_store import get_vector_store
            # tallies stay out of the vector index; keyword search still finds them
            vs = get_vector_store() if _policy.wants_vector_index(origin) else None
            if vs is not None:
                vector_indexed = bool(vs.add(
                    t,
                    metadata={
                        "memory_id": rowid,
                        "kind": kind,
                        "source": source,
                        "tags": tags if isinstance(tags, str) else ",".join(tags or []),
                        "importance": importance,
                        "verification_status": verification_status,
                        "provenance_kind": provenance_kind,
                        "user_id": _who,
                    },
                ))
                # No save here: each save rewrites the whole index (18 MB at 2,900 memories), and a
                # 349-PDF read stored 2,399 rows, 24 GB of writes in 50 minutes that stalled the
                # machine. add() saves in batches; shutdown saves the rest.
                if not vector_indexed:
                    # Ran without raising but declined the write (the embedder returned no vector):
                    # a real failed write, not "no vector store configured". Feeds the World tab's
                    # memory_uncertainty bar.
                    try:
                        from eli.world.world_event_bus import fire_memory_uncertainty_event
                        fire_memory_uncertainty_event("embedder returned no vector", rowid)
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
        except Exception:
            log.warning(
                "[MEMORY] FAISS index write failed for memory id=%s — row is "
                "stored (recoverable via FTS5/LIKE fallback) but will not "
                "surface in semantic recall until re-indexed",
                rowid, exc_info=True,
            )
            try:
                from eli.world.world_event_bus import fire_memory_uncertainty_event
                fire_memory_uncertainty_event("vector index write raised", rowid)
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        try:
            if origin == _policy.ORIGIN_USER:
                self.record_claims_from(t, rowid, event_ts or ts, user_id=_who)
        except Exception:
            log.debug("claims not recorded", exc_info=True)

        # Extract entity-relation triples into knowledge graph (fire-and-forget)
        try:
            from eli.memory.knowledge_graph import feeds_graph, get_knowledge_graph
            if feeds_graph(origin):
                kg = get_knowledge_graph()
                kg.extract_from_memory(t, source=source, user_id=_who)
        except Exception:
            log.debug("suppressed exception", exc_info=True)


        return {"ok": True, "id": rowid, "importance": importance, "vector_indexed": vector_indexed}

    def add_memory(self, text: str, tags: Union[str, List[str], None] = None) -> Optional[Dict[str, Any]]:
        if isinstance(tags, str):
            tags_list = [tags]
        else:
            tags_list = tags
        return self.store_memory(text, tags=tags_list or [])

    def get_memories_by_tag(self, tag: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Project-scoped recall (Phase 2): durable facts carrying a given tag,
        most-recent first. Returns [{id, text, tags, ts}]."""
        tag = (tag or "").strip()
        if not tag:
            return []
        conn = self._get_connection()
        try:
            if not _has_table(conn, "memories"):
                return []
            mine, mine_params = _memory_scope(_table_columns(conn, "memories"))
            rows = conn.execute("""
                SELECT id, COALESCE(text,''), COALESCE(tags,''), COALESCE(timestamp, ts, id)
                FROM memories
                WHERE tags LIKE ?""" + mine + """
                ORDER BY COALESCE(timestamp, ts, id) DESC
                LIMIT ?
            """, (f"%{tag}%", *mine_params, max(1, int(limit)))).fetchall()
            out = []
            for r in rows:
                tags_s = str(r[2] or "")
                # guard against substring false-positives (e.g. project.a vs project.alpha)
                if tag not in [x.strip() for x in tags_s.replace(",", " ").split()]:
                    if tag not in tags_s:
                        continue
                txt = str(r[1] or "").strip()
                if txt:
                    out.append({"id": r[0], "text": txt, "tags": tags_s, "ts": r[3]})
            return out
        except Exception:
            return []
        finally:
            try:
                conn.close()
            except Exception:
                log.debug("suppressed exception", exc_info=True)


    def get_recent_semantic_memories(self, limit=20):
        conn = self._get_connection()
        try:
            if not _has_table(conn, "memories"):
                return []
            mine, mine_params = _memory_scope(_table_columns(conn, "memories"))
            rows = conn.execute("""
                SELECT id, COALESCE(timestamp, ts, id), COALESCE(kind,''), COALESCE(text,''), COALESCE(tags,''), COALESCE(source,'')
                FROM memories WHERE 1=1""" + mine + """
                ORDER BY COALESCE(timestamp, ts, id) DESC
                LIMIT ?
            """, (*mine_params, max(int(limit) * 8, 160))).fetchall()
            out = []
            for r in rows:
                kind = str(r[2] or '').lower()
                tags = str(r[4] or '').lower()
                source = str(r[5] or '').lower()
                text = str(r[3] or '').strip()
                if not text:
                    continue
                if kind in {*_OWN_REFLECTION_KINDS, 'conversation', 'session_summary'}:
                    continue
                if 'reflection' in tags or 'session_summary' in tags or 'continuity' in tags:
                    continue
                if source == 'session_end':
                    continue
                out.append({'id': r[0], 'timestamp': r[1], 'kind': r[2], 'text': text, 'tags': r[4], 'source': r[5]})
                if len(out) >= int(limit):
                    break
            return out
        finally:
            conn.close()

    def recall_memory(self, query, limit=10, keyword_only: bool = False,
                      verified_only: bool = False, tags=None, user_id: Optional[str] = None):
        """What the turn's user has stored that matches `query` (user_id None: the turn's own,
        eli/memory/policy.owner_key). Another user's memories, claims, graph facts and turns never
        come back: every channel below is scoped to them."""
        _plugin_may("memory_read", "recall memories")
        q = _norm_text(query).strip()
        if not q:
            return []
        from eli.memory import policy as _scope_policy
        _scope_key = _scope_policy.owner_key(user_id)

        limit = int(limit or 10)
        conn = self._get_connection()
        try:
            cols = _table_columns(conn, "memories")
            text_expr = "LOWER(COALESCE(text, content, ''))" if "content" in cols else "LOWER(COALESCE(text, ''))"
            time_expr = "COALESCE(timestamp, ts, 0)"
            time_expr_m = "COALESCE(m.timestamp, m.ts, 0)"
            # when it was first said: a restated memory keeps the date it was first said (last_seen
            # holds the latest sighting), and every hit carries it (eli/cognition/evidence_format.row_time)
            event_expr = "COALESCE(event_ts, timestamp, ts, 0)" if "event_ts" in cols else time_expr
            event_expr_m = "COALESCE(m.event_ts, m.timestamp, m.ts, 0)" if "event_ts" in cols else time_expr_m
            imp_expr = "COALESCE(importance, 0.5)" if "importance" in cols else "0.5"
            imp_expr_m = "COALESCE(m.importance, 0.5)" if "importance" in cols else "0.5"

            # What counts as ELI's own record-keeping is decided in one place, eli/core/self_provenance.py.
            # It used to be decided in four and each new reader inherited the bug. This fragment was also
            # written twice here (aliased and not) and had to be kept in step by hand.
            from eli.core.self_provenance import memory_exclusion_sql
            from eli.runtime.memory_provenance import (
                filter_grounding_hits,
                verification_filter_sql,
            )

            # Both forms bind the same parameters — same kinds, same sources —
            # so the aliased list is the one every query below uses.
            _kind_filter, _kind_params = memory_exclusion_sql(cols, alias="m.")
            _kind_filter_t, _ = memory_exclusion_sql(cols, alias="")
            # the user's own rows only: rides with the exclusion fragment every query below uses
            _scope, _scope_params = _memory_scope(cols, "m.", _scope_key)
            _scope_t, _ = _memory_scope(cols, "", _scope_key)
            _kind_filter, _kind_filter_t = _kind_filter + _scope, _kind_filter_t + _scope_t
            _kind_params = (*_kind_params, *_scope_params)
            _verify_filter, _verify_params = verification_filter_sql(
                cols, alias="m.", verified_only=verified_only,
            )
            _verify_filter_t, _ = verification_filter_sql(
                cols, alias="", verified_only=verified_only,
            )

            # Every channel gathers candidates; one ranking (eli/cognition/reranker.rerank_candidates)
            # orders them all at the end: relevance first, what is known of each before the question
            # as its prior, the priors' weight learned from the recall outcomes. Each channel fetches
            # _OVERFETCH times the limit, as ELI's own records and deleted rows are dropped below.
            #
            # Vector channel. With keyword_only=True (the orchestrator's keyword_search) FAISS is
            # skipped: the orchestrator runs its own semantic_search() and running it here would
            # duplicate vector hits under an "fts5" label.
            vector_results = []
            _vector_ran = False
            if not keyword_only:
                try:
                    from eli.memory.vector_store import get_vector_store
                    _vs = get_vector_store()
                    _idx = getattr(_vs, '_index', None) if _vs is not None else None
                    _ntotal = int(getattr(_idx, 'ntotal', 0) or 0)
                    if _vs is not None and _ntotal > 0:
                        _vector_ran = True
                        _hits = _vs.search(q, top_k=limit * _OVERFETCH) or []
                        for h in _hits:
                            vector_results.append({
                                'id': h.get('memory_id', h.get('id', f"vec:{h.get('pos', 0)}")),
                                'text': h.get('text', ''),
                                'tags': h.get('tags', ''),
                                '_similarity': float(h.get('score', 0.0) or 0.0),
                                '_source': 'vector',
                            })
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
                # The keyword channels filter out ELI's own records and unverified rows in SQL; vector
                # hits skipped that, and the index still holds 372 telemetry rows embedded before the
                # storage policy ("Top topics: days, past, memory" was the top recall). Same filters,
                # one query; rows deleted since they were indexed drop out too. The row is the record: an
                # index entry whose text is not the row's (the id now belongs to another memory, as after
                # a reset or a restore) is stale, and its own copy of the text was shown as a memory. The
                # row also gives the hit its own importance and dates: the similarity is no importance.
                _vec_ids = [int(v['id']) for v in vector_results if str(v.get('id')).isdigit()]
                if _vec_ids:
                    try:
                        _row_text = "COALESCE(m.text, m.content, '')" if "content" in cols else "COALESCE(m.text, '')"
                        _ok_rows = conn.execute(
                            f"SELECT m.id, {time_expr_m}, {event_expr_m}, {_row_text}, {imp_expr_m}, "
                            f"COALESCE(m.weight, 1.0), COALESCE(m.tags, '') FROM memories m "
                            f"WHERE m.id IN ({','.join('?' * len(_vec_ids))}) {_kind_filter}{_verify_filter}",
                            (*_vec_ids, *_kind_params),
                        ).fetchall()
                        _ok = {int(r[0]): r for r in _ok_rows}
                        _same = lambda a, b: " ".join(str(a).split()) == " ".join(str(b).split())
                        vector_results = [
                            dict(v, ts=_ok[int(v['id'])][1], timestamp=_ok[int(v['id'])][1],
                                 event_ts=_ok[int(v['id'])][2], text=_ok[int(v['id'])][3],
                                 importance=_ok[int(v['id'])][4], weight=_ok[int(v['id'])][5],
                                 tags=v.get('tags') or _ok[int(v['id'])][6])
                            for v in vector_results if str(v.get('id')).isdigit() and int(v['id']) in _ok
                            and _same(v.get('text', ''), _ok[int(v['id'])][3])
                        ]
                    except Exception:
                        log.debug("vector hit filter skipped", exc_info=True)
                        vector_results = []
                else:
                    vector_results = []

            # Keyword channel: FTS5 alongside vectors, not as a fallback ("what did I say about fallout"
            # missed the turns with the word when it only ran on an empty vector result). Content terms
            # only, matched in the text column (tags are split at "_", so "memory" matched every row
            # tagged memory_recall). Candidates by how well they match (BM25, FTS5's own rank), never by
            # importance: the one-word match on an important row came before the row holding every term.
            try:
                from eli.runtime.reflection import TOPIC_STOPWORDS as _STOP
            except Exception:
                _STOP = frozenset()
            _all_terms = [t for t in re.split(r"[^a-zA-Z0-9_]+", q) if len(t) > 1]
            # Every token was noise (e.g. "what about that?"): search the raw tokens rather than nothing.
            _terms = [t for t in _all_terms if t.lower() not in _STOP] or _all_terms
            fts_rows = []
            if _terms and _has_table(conn, "memories_fts"):
                try:
                    fts_q = " OR ".join('text:"{}"'.format(t.replace('"', '')) for t in _terms)
                    fts_rows = conn.execute(
                        f"""
                        SELECT m.id, {time_expr_m}, COALESCE(m.text, m.content, ''),
                               COALESCE(m.tags, ''), COALESCE(m.weight, 1.0), {imp_expr_m}, {event_expr_m}
                        FROM memories_fts f
                        JOIN memories m ON m.id = f.rowid
                        WHERE memories_fts MATCH ?
                        {_kind_filter}{_verify_filter}
                        ORDER BY bm25(memories_fts)
                        LIMIT ?
                        """,
                        (fts_q, *_kind_params, limit * _OVERFETCH),
                    ).fetchall()
                except Exception:
                    log.debug("fts recall failed", exc_info=True)
                    fts_rows = []

            # LIKE fallback when FTS5 found nothing: the phrase, else each term, ordered by how many
            # of the terms a row holds (the same measure of match), then by when it was said.
            like_rows = []
            if not fts_rows:
                _like_terms = [q.lower()] + [t.lower() for t in _terms if t.lower() != q.lower()]
                try:
                    _match = " + ".join(f"(CASE WHEN {text_expr} LIKE ? THEN 1 ELSE 0 END)" for _ in _like_terms)
                    _where = " OR ".join(f"{text_expr} LIKE ?" for _ in _like_terms)
                    _likes = [f"%{t}%" for t in _like_terms]
                    like_rows = conn.execute(
                        f"""
                        SELECT id, {time_expr}, COALESCE(text, content, ''), COALESCE(tags, ''),
                               COALESCE(weight, 1.0), {imp_expr}, {event_expr}
                        FROM memories
                        WHERE ({_where})
                        {_kind_filter_t}{_verify_filter_t}
                        ORDER BY ({_match}) DESC, {time_expr} DESC
                        LIMIT ?
                        """,
                        (*_likes, *_kind_params, *_likes, limit * _OVERFETCH),
                    ).fetchall()
                except Exception:
                    log.debug("like recall failed", exc_info=True)
                    like_rows = []

            # Convert DB rows to dicts — row format: (id, ts, text, tags, weight, importance, event_ts)
            keyword_results = [
                {"id": r[0], "ts": r[1], "timestamp": r[1], "text": r[2], "tags": r[3],
                 "weight": r[4], "importance": r[5], "event_ts": r[6],
                 "_source": "fts" if fts_rows else "like"}
                for r in (fts_rows or like_rows)
            ]

            # Reciprocal Rank Fusion (eli/cognition/reranker.fuse_ranked_lists): it sums 1/(k+rank) over
            # the channels that found a hit, so agreement beats one strong showing, by rank because the
            # scores (L2 vs BM25) aren't comparable. Only the channels that ran are passed: a hit's
            # agreement is the share of those that found it.
            _channels = {"keyword": keyword_results}
            if _vector_ran:
                _channels["vector"] = vector_results
            from eli.cognition.reranker import fuse_ranked_lists
            out: List[Dict] = fuse_ranked_lists(_channels)
            sort_now = time.time()
            # Spreading activation: from the memories this recall found to the ones linked to them
            # (ASSOCIATION, strengthen_links). Each linked memory carries its links (_links: source ->
            # strength); the ranking gives it the relevance of what it is linked to, times the link's
            # strength (eli/cognition/reranker.rerank_candidates), so a memory that went with this one
            # into endorsed answers comes with it though it shares no word with the question.
            if ASSOCIATION and not keyword_only:
                try:
                    _spread = self._linked(conn, [r.get("id") for r in out[:limit * _OVERFETCH]], sort_now, _scope_key)
                    for _row in out:
                        if str(_row.get("id", "")).isdigit() and int(_row["id"]) in _spread:
                            _row["_links"] = _spread[int(_row["id"])]
                    _have = {int(r["id"]) for r in out if str(r.get("id", "")).isdigit()}
                    _new = sorted((i for i in _spread if i not in _have), key=lambda i: max(_spread[i].values()),
                                  reverse=True)[:limit * _OVERFETCH]
                    if _new:
                        for r in conn.execute(
                                f"SELECT m.id, {time_expr_m}, COALESCE(m.text, m.content, ''), COALESCE(m.tags, ''), "
                                f"COALESCE(m.weight, 1.0), {imp_expr_m}, {event_expr_m} FROM memories m "
                                f"WHERE m.id IN ({','.join('?' * len(_new))}) {_kind_filter}{_verify_filter}",
                                (*_new, *_kind_params)).fetchall():
                            out.append({"id": r[0], "ts": r[1], "timestamp": r[1], "text": r[2], "tags": r[3],
                                        "weight": r[4], "importance": r[5], "event_ts": r[6], "_source": "association",
                                        "_channels": [], "_channels_ran": sorted(_channels), "_links": _spread[int(r[0])]})
                except sqlite3.Error:
                    log.debug("association not spread", exc_info=True)
            # How readily each comes to mind (eli/memory/policy.py): its faded uses, the curve's own
            # factors, and its share against the look-alikes in this set. Known for stored rows only.
            _how_readily = self._accessibility(conn, out, cols, sort_now)
            for _row in out:
                if _row.get("id") in _how_readily:
                    _row["accessibility"] = _how_readily[_row["id"]]
            # How likely it still holds: a memory whose every fact ELI now holds superseded ("I live in
            # Fenwick Bay" after "I live in Ashby Green now") still holds only as far as what replaced
            # it might be wrong (one less the sureness of the claim standing in its place), the
            # confidence the one ranking takes into its prior (eli/cognition/reranker.prior).
            for _mid, _chance in self._superseded_chance(conn, [r.get("id") for r in out]).items():
                for _row in out:
                    if str(_row.get("id")) == str(_mid):
                        _row["chance"] = _chance

            def _postmerge_noise(hit):
                _tags = str(hit.get('tags', '') or '').lower()
                _kind = str(hit.get('kind', '') or '').lower()
                _source = str(hit.get('source', '') or hit.get('_source', '') or '').lower()
                _text = str(hit.get('text', '') or '').lower()

                if 'reflection' in _tags or _kind == 'reflection' or _source == 'reflection' or _text.startswith('reflection ('):
                    return True
                if 'session_summary' in _tags or 'continuity' in _tags or _text.startswith('session context:'):
                    return True
                if 'capability_change' in _tags or 'self_awareness' in _tags or _text.startswith('capability inventory updated:'):
                    return True
                if _tags.strip() == 'test' or _kind == 'test' or _source == 'test':
                    return True
                if 'e2e_test_' in _tags or _text.startswith('e2e test:') or 'test_enterprise_v2:' in _text:
                    return True
                return False

            _filtered = []
            _seen_filtered = set()
            for _r in out:
                if _postmerge_noise(_r):
                    continue
                _txt = str(_r.get('text', '') or '').strip()
                _key = _txt[:160].lower() if _txt else f"__id__:{_r.get('id')}"
                if _key in _seen_filtered:
                    continue
                _seen_filtered.add(_key)
                _filtered.append(_r)
            out = _filtered

            # The user's semantic facts (eli/runtime/profile_extractor) join the candidates when the
            # question is about the user themselves, the ones that share a word with it (or, for "who am
            # I"/"what do you know about me", all of them), each with its confidence as its chance of
            # being right; the ranking decides where they stand. Any question with "name" or "user" in
            # it used to put every semantic row first, lowest confidence first, over what it asked for.
            _q_low = q.lower()
            _about_user = bool(re.search(r"\b(?:who am i|about me|know me|remember me|my name|my identity)\b", _q_low))
            if _about_user and _has_table(conn, "semantic"):
                try:
                    from eli.cognition.scoring import tokenize as _sem_tok
                    _q_words = set(_sem_tok(q)) - {"name", "know", "remember", "identity"}
                    _sem_scope, _sem_params = _semantic_scope(_scope_key)
                    for sr in conn.execute(
                            "SELECT id, fact, tags, confidence, created_at FROM semantic WHERE 1=1 " + _sem_scope
                            + " ORDER BY confidence DESC", _sem_params).fetchall():
                        _fact = str(sr[1] or "")
                        if _q_words and not (_q_words & set(_sem_tok(_fact))) and "name" not in _fact.lower():
                            continue
                        _key = _fact.strip()[:160].lower()
                        if not _key or _key in _seen_filtered:
                            continue
                        _seen_filtered.add(_key)
                        out.append({
                            "id": f"sem:{sr[0]}", "ts": sr[4] or 0, "timestamp": sr[4] or 0, "text": _fact,
                            "tags": sr[2] or "semantic,user_fact", "kind": "semantic", "source": "semantic",
                            "chance": float(sr[3] if sr[3] is not None else 0.5),
                        })
                except Exception:
                    log.debug("semantic facts not read", exc_info=True)
            # Knowledge-graph enrichment. Skipped when keyword_only=True: the orchestrator runs its own
            # kg_search() and manages KG insertion into hybrid_merge() with its own priority. Injecting here
            # would put a KG hit in both the keyword and kg buckets, and the orchestrator's dedup
            # (text[:240]) would silently drop one copy.
            if not keyword_only and getattr(self, "_is_canonical", True):
                try:
                    from eli.memory.knowledge_graph import get_knowledge_graph
                    _kg = get_knowledge_graph()
                    _kg_ctx = _kg.context_for_prompt(q, max_chars=600, user_id=_scope_key)
                    if _kg_ctx:
                        out.append({
                            "id": "kg:context",
                            "ts": 0,
                            "timestamp": 0,
                            "text": _kg_ctx,
                            "tags": "knowledge_graph,entities,relations",
                            "kind": "knowledge_graph",
                            "source": "knowledge_graph",
                            "_source": "kg",
                        })
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
            # --- Deep history fallback: search conversation_turns when memories sparse ---
            # This surfaces things the user said that were never extracted as facts.
            if len(out) < 2 and _has_table(conn, "conversation_turns"):
                try:
                    _q_low2 = q.lower()
                    # Key on distinctive content nouns, not the whole question. "what is my dog's name" must
                    # surface a turn saying "...my dog Biscuit", and a verbatim '%what is my dog's name%' LIKE never
                    # matches. Drop interrogatives, possessives and generic-memory words so it keys on "dog", and
                    # de-pluralise so "dogs" finds "dog".
                    _fb_stop = {
                        "what", "whats", "which", "who", "whom", "when", "where",
                        "why", "how", "is", "are", "was", "were", "the", "an",
                        "my", "your", "our", "you", "we", "do", "does", "did",
                        "tell", "know", "knew", "remember", "recall", "again",
                        "please", "name", "named", "call", "called", "about",
                        "and", "for", "its", "that", "this", "eli", "have",
                    }
                    _kw: list = []
                    for _tok in re.split(r"[^a-z0-9]+", _q_low2):
                        if len(_tok) < 3 or _tok in _fb_stop:
                            continue
                        _kw.append(_tok)
                        if _tok.endswith("s") and len(_tok) > 3:
                            _kw.append(_tok[:-1])  # de-pluralise: dogs -> dog
                    _kw = list(dict.fromkeys(_kw))
                    # Full phrase first, then a distinctive-keyword OR sweep.
                    _likes = [f"%{_q_low2}%"] + [f"%{_k}%" for _k in _kw]
                    _where = " OR ".join("LOWER(content) LIKE ?" for _ in _likes)
                    _turn_scope, _turn_params = _scope_policy.scope_sql("user_id", _scope_key, raw_ids=True)
                    _conv_rows = conn.execute(
                        f"""SELECT COALESCE(timestamp, ts, 0), role, content
                           FROM conversation_turns
                           WHERE ({_where})
                           AND role = 'user'{_turn_scope}
                           ORDER BY COALESCE(timestamp, ts, 0) DESC
                           LIMIT ?""",
                        (*_likes, *_turn_params, max(6, limit - len(out)) * 4),
                    ).fetchall()
                    for _cr in _conv_rows:
                        _txt = (_cr[2] or "").strip()
                        _key = _txt[:160].lower()
                        if _key and _key not in _seen_filtered and len(_txt) >= 10:
                            _seen_filtered.add(_key)
                            out.append({
                                "id": f"conv_turn:{_cr[0]}",
                                "ts": float(_cr[0] or 0),
                                "timestamp": float(_cr[0] or 0),
                                "text": _txt,
                                "tags": "conversation,user",
                                "_source": "conversation_turns",
                            })
                except Exception:
                    log.debug("suppressed exception", exc_info=True)

            if verified_only:
                out = filter_grounding_hits(out)
            # Optional tag filter — callers (e.g. turn_dossier morning_report) may
            # request memories tagged with specific labels without a separate API.
            if tags:
                _want = {str(t).strip().lower() for t in tags if str(t).strip()}
                if _want:
                    def _hit_has_tag(hit: dict) -> bool:
                        raw = hit.get("tags", "")
                        if isinstance(raw, (list, tuple, set)):
                            blob = " ".join(str(x) for x in raw)
                        else:
                            blob = str(raw or "")
                        parts = {
                            p.strip().lower()
                            for p in re.split(r"[,;\s]+", blob)
                            if p.strip()
                        }
                        return bool(_want & parts) or any(w in blob.lower() for w in _want)

                    out = [h for h in out if _hit_has_tag(h)]

            # The one ranking, then complementary, not five ways of saying one thing: within the
            # caller's limit the later places go by rank less likeness to what is already chosen
            # (scoring.mmr_select).
            from eli.cognition.reranker import hit_score as _hit_score, rerank_candidates as _rank
            from eli.cognition.scoring import mmr_select as _mmr
            out = _mmr(_rank(q, out, limit=len(out)), limit, score=_hit_score)

            # The recall log: one row per memory this recall returned, when and how many came with it,
            # written at once (a queue flushed every fiftieth write dropped what it held at shutdown) and
            # without the question (every question ever asked was kept, and forgetting a memory left
            # them). Kept for RECALL_LOG_DAYS (the upkeep, prune_recall_log). Retrieval alone never
            # strengthens a memory: that is record_recall_outcome's, when the answer was endorsed.
            try:
                _rnow = _now_ts()
                _logged = [int(_h["id"]) for _h in out if str(_h.get("id", "")).isdigit()]
                if _logged and _has_table(conn, "recall_log"):
                    conn.executemany(
                        "INSERT INTO recall_log (ts, timestamp, query, results_count, result_count, memory_id) "
                        "VALUES (?, ?, '', ?, ?, ?)", [(_rnow, _rnow, len(out), len(out), _mid) for _mid in _logged])
                    conn.commit()
            except sqlite3.Error:
                log.debug("recall not logged", exc_info=True)
            return out
        finally:
            conn.close()

    @staticmethod
    def _superseded_chance(conn, ids) -> Dict[int, float]:
        """{memory id: the chance it still holds} for the memories among `ids` whose facts (the claims
        read from them) are all superseded: one less the sureness of the claim that now stands for
        that relation. A memory with a current claim, or none, is not listed."""
        ids = sorted({int(i) for i in ids if str(i).isdigit()})
        if not ids or not _has_table(conn, "memory_claims"):
            return {}
        marks = ",".join("?" * len(ids))
        try:
            rows = conn.execute(
                f"SELECT c.source_memory_id, c.status, c.relation, c.subject, "
                f"(SELECT MAX(n.confidence) FROM memory_claims n WHERE n.subject = c.subject AND n.relation = c.relation "
                f"AND n.status = 'current') FROM memory_claims c WHERE c.source_memory_id IN ({marks})", ids).fetchall()
        except sqlite3.Error:
            log.debug("claims not read for the recall", exc_info=True)
            return {}
        held: Dict[int, List[Optional[float]]] = {}
        for mid, status, _rel, _subj, standing in rows:
            if status == "current":
                held.setdefault(int(mid), []).append(None)
            elif status == "superseded" and standing is not None:
                held.setdefault(int(mid), []).append(1.0 - float(standing))
        return {mid: max(v) for mid, v in held.items() if v and None not in v}

    def _accessibility(self, conn, rows, cols, now: float) -> Dict[int, float]:
        """How readily each stored row among `rows` comes to mind, 0..1 (eli/memory/policy.py): its
        activation from its uses and the curve's own factors, less the competition of the look-alikes
        in this set, read off the set itself. {} when the rows carry no policy columns yet."""
        import sqlite3 as _sqlite3
        from eli.cognition.scoring import jaccard as _jaccard, tokenize as _tokenize
        from eli.memory import policy as _policy
        ids = sorted({int(r["id"]) for r in rows if str(r.get("id", "")).isdigit()})
        if not ids or "seen_count" not in cols:
            return {}
        marks = ",".join("?" * len(ids))
        try:
            fetched = conn.execute(
                f"SELECT id, COALESCE(timestamp, ts, 0), COALESCE(event_ts, 0), COALESCE(last_seen, 0), "
                f"COALESCE(last_recalled, 0), COALESCE(seen_count, 1), COALESCE(recall_count, 0), "
                f"COALESCE(importance, 0.5), COALESCE(origin, ''), COALESCE(text, content, '') "
                f"FROM memories WHERE id IN ({marks})", ids).fetchall()
        except _sqlite3.Error:
            log.debug("[MEMORY] accessibility not read", exc_info=True)
            return {}
        def _seconds(value) -> float:
            # a timestamp that is not a number (an old row's text date) is unknown, as the recall's
            # own recency reads it
            try:
                return float(value or 0.0)
            except (TypeError, ValueError):
                return 0.0

        order, acts, words = [], [], []
        for mid, ts, event_ts, last_seen, last_recalled, seen, recalls, importance, origin, text in fetched:
            order.append(int(mid))
            ts, event_ts, last_seen, last_recalled = (_seconds(v) for v in (ts, event_ts, last_seen, last_recalled))
            acts.append(_policy.activation(now, event_ts or ts, last_seen or ts, last_recalled, seen, recalls,
                                           importance, origin))
            words.append(set(_tokenize(text)))
        likeness = [[_jaccard(words[i], words[j]) if i != j else 0.0 for j in range(len(order))]
                    for i in range(len(order))]
        chances = _policy.retrievable(_policy.compete(acts, likeness))
        return {mid: round(float(c), 4) for mid, c in zip(order, chances)}

    def search_memory(self, query, limit=10, include_conversations=True, user_id=None, session_id=None):
        _plugin_may("memory_read", "search memories and conversations")
        _q_lower = query.lower() if query else ""
        _q_lower = str(query or "").lower()
        out = list(self.recall_memory(query, limit=limit))
        if include_conversations and len(out) < int(limit):
            try:
                conv_hits = self.search_conversations(
                    query, user_id=user_id, limit=max(1, int(limit) - len(out)),
                    session_id=session_id)
            except Exception:
                conv_hits = []
            for row in conv_hits:
                out.append({
                    "id": f"conv:{row.get('session_id','')}:{row.get('timestamp','')}",
                    "ts": row.get("timestamp") or 0,
                    "timestamp": row.get("timestamp") or 0,
                    "text": row.get("content") or "",
                    "tags": "conversation",
                    "weight": 0.5,
                    "kind": "conversation",
                    "role": row.get("role") or "",
                    "source": "conversations",
                    "session_id": row.get("session_id") or "",
                    "user_id": row.get("user_id") or "",
                })
        # Lexical filter and dedup. Drops vector hits with no token overlap to the query unless it
        # looks like an identity question, then dedups by (id, text[:500]). Also filters all sources
        # when the query is a pure greeting or generic phrase, so low-relevance hits aren't injected and
        # don't cause hallucinated memory claims.
        _q_lower = str(query or "").lower()
        _q_terms = set(re.findall(r"[a-z0-9]+", _q_lower))

        # Stopwords that carry no content signal for memory matching.
        _STOPWORDS = {
            "i", "me", "my", "you", "your", "we", "it", "the", "a", "an",
            "is", "are", "was", "were", "be", "been", "am",
            "and", "or", "but", "not", "in", "on", "at", "to", "for",
            "of", "with", "by", "as", "do", "does", "did", "have", "has",
            "had", "will", "would", "could", "should", "can", "may", "might",
            "that", "this", "there", "here", "what", "when", "where", "which",
            "who", "how", "why", "so", "if", "then", "than", "very", "just",
            "up", "down", "out", "about", "now", "today", "pal", "hey",
        }
        _content_terms = _q_terms - _STOPWORDS
        # A query is "social/generic" when it has no content terms at all
        # (pure greetings, chitchat, filler phrases like "how are you today").
        _is_social_query = len(_content_terms) == 0

        def _hardening_text_of(hit):
            if not isinstance(hit, dict):
                return ""
            return str(hit.get("text") or hit.get("fact") or hit.get("value") or "").strip()

        def _hardening_source_of(hit):
            if not isinstance(hit, dict):
                return ""
            return str(hit.get("_source") or hit.get("source") or "").strip().lower()

        def _hardening_looks_identity_query(s):
            return any(p in s for p in (
                "who am i",
                "about me",
                "what do you know about me",
                "user info",
                "profile",
                "memory about me",
            ))

        try:
            from eli.runtime.persistence_gate import is_recall_narration as _is_narr
        except Exception:
            _is_narr = lambda _t: False

        _filtered = []
        for _hit in out:
            _txt = _hardening_text_of(_hit)
            _src = _hardening_source_of(_hit)
            if not _txt:
                continue
            # B2: never surface ELI's own recall-narration from any source.
            if _is_narr(_txt):
                continue
            if _src == "vector":
                _hit_terms = set(re.findall(r"[a-z0-9]+", _txt.lower()))
                _overlap = len(_q_terms & _hit_terms)
                if not _q_terms:
                    continue
                if _overlap == 0 and not _hardening_looks_identity_query(_q_lower):
                    continue
                if _overlap == 0 and len(_q_terms) >= 2:
                    continue
            elif _is_social_query and not _hardening_looks_identity_query(_q_lower):
                # For social/generic queries, drop ALL non-identity hits from
                # every source (fts, like, conversation_turns, kg) — they have
                # no meaningful content overlap and risk hallucinated claims.
                continue
            _filtered.append(_hit)

        _dedup = []
        _seen_keys = set()
        for _hit in _filtered:
            _k_txt = _hardening_text_of(_hit).lower()
            _k_id = str(_hit.get("id", "")) if isinstance(_hit, dict) else ""
            _key = (_k_id, _k_txt[:500])
            if _key in _seen_keys:
                continue
            _seen_keys.add(_key)
            _dedup.append(_hit)

        return _dedup[: int(limit)]

    def search_memories(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        return self.search_memory(query, limit=limit)

    # ------------------------------------------------------------------
    # Decay and consolidation
    # ------------------------------------------------------------------

    # The forgetting curve's numbers live in eli/memory/policy.py.
    # Smallest weight change worth a write. `now` advances between calls, so every row's ideal
    # weight drifts a hair each run; without a floor the UPDATE rewrites the whole table every time
    # and "rows updated" always equals "rows eligible", hiding whether decay does anything.
    DECAY_MIN_DELTA = 0.005

    def _active_day_gaps(self, conn, days: int = 180) -> List[float]:
        """Days between consecutive days of use."""
        try:
            since = time.time() - days * 86400
            rows = conn.execute(
                "SELECT DISTINCT date(COALESCE(timestamp, ts), 'unixepoch') FROM conversation_turns "
                "WHERE COALESCE(timestamp, ts, 0) > ? AND role = 'user' ORDER BY 1", (since,),
            ).fetchall()
            ds = [time.mktime(time.strptime(r[0], "%Y-%m-%d")) for r in rows if r and r[0]]
            return [(b - a) / 86400.0 for a, b in zip(ds, ds[1:])]
        except Exception:
            return []

    def adaptive_half_life(self, conn=None) -> float:
        """Base half-life in days, from usage unless mem.half_life_days is set."""
        from eli.memory import policy as _policy
        try:
            from eli.core.cognition_tunables import get_tunable as _gt
            _override = float(_gt("mem.half_life_days"))
            if _override > 0:
                return _override
        except Exception:
            pass
        own = conn is None
        conn = conn or self._get_connection()
        try:
            return _policy.adaptive_half_life_days(self._active_day_gaps(conn))
        finally:
            if own:
                conn.close()

    def apply_weight_decay(
        self,
        min_weight: float = 0.05,
        older_than_days: int = 7,
        half_life_days: Optional[float] = None,
    ) -> int:
        """Set every row's weight from the forgetting curve in eli/memory/policy.py. Idempotent; returns rows changed."""
        from eli.memory import policy as _policy
        now = time.time()
        conn = self._get_connection()
        try:
            cols = _table_columns(conn, "memories")
            base = float(half_life_days) if half_life_days else self.adaptive_half_life(conn)
            floor = max(0.0, min(1.0, float(min_weight)))
            cutoff_ts = now - (max(0, int(older_than_days)) * 86400)

            def _weight(last_touch, importance, origin, seen, recalls, source, kind, tags, text) -> float:
                o = origin or _policy.classify_origin(source, kind, tags, text)
                return max(floor, _policy.strength(
                    now, last_touch, importance if importance is not None else 0.5, o,
                    int(seen or 1), int(recalls or 0), base))

            conn.create_function("eli_strength", 9, _weight, deterministic=True)
            touch = ("MAX(COALESCE(timestamp, ts, 0), COALESCE(event_ts, 0), COALESCE(last_seen, 0), "
                     "COALESCE(last_recalled, 0))") if "last_seen" in cols else "COALESCE(timestamp, ts, 0)"
            args = (f"{touch}, COALESCE(importance, 0.5), "
                    + ("origin, " if "origin" in cols else "NULL, ")
                    + ("seen_count, recall_count, " if "seen_count" in cols else "1, 0, ")
                    + "source, kind, tags, COALESCE(text, content, value, '')")
            conn.execute(
                f"UPDATE memories SET weight = eli_strength({args}) "
                f"WHERE {touch} < ? "
                f"AND ABS(COALESCE(weight, 1.0) - eli_strength({args})) > ?",
                (cutoff_ts, self.DECAY_MIN_DELTA),
            )
            updated = conn.execute("SELECT changes()").fetchone()[0]
            conn.commit()
            return int(updated or 0)
        except Exception as e:
            log.debug(f"[MEMORY] weight_decay failed: {e}")
            return 0
        finally:
            conn.close()

    def _remove_rows(self, conn, rows) -> None:
        """Delete memory rows with their FTS entries and vector tombstones. `rows` are dicts."""
        ids = []
        for r in rows:
            _fts_delete(conn, r["id"], r.get("text") or "", r.get("tags") or "")
            ids.append(r["id"])
        conn.executemany("DELETE FROM memories WHERE id = ?", [(i,) for i in ids])
        if ids and _has_table(conn, "memory_links"):
            conn.executemany("DELETE FROM memory_links WHERE a = ? OR b = ?", [(i, i) for i in ids])
        try:
            from eli.memory.vector_store import get_vector_store
            _vs = get_vector_store()
            if _vs is not None and ids:
                _vs.mark_memories_deleted(ids)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    @staticmethod
    def _repoint(conn, doomed_ids, keep_id: int) -> None:
        """What pointed at a merged-away duplicate points at the row it was merged into: the claims read
        from it (so forgetting the row still takes them back), its lineage, its recall log and its
        links. They were left pointing at a deleted id."""
        doomed = [int(i) for i in doomed_ids if int(i) != int(keep_id)]
        if not doomed:
            return
        marks = ",".join("?" * len(doomed))
        if _has_table(conn, "memory_claims"):
            from eli.memory.claims import repoint_sources
            repoint_sources(conn, doomed, keep_id)
        if _has_table(conn, "memory_lineage"):
            for column in ("child_id", "parent_id"):
                conn.execute(f"UPDATE OR IGNORE memory_lineage SET {column} = ? WHERE {column} IN ({marks})",
                             [keep_id] + doomed)
            conn.execute("DELETE FROM memory_lineage WHERE child_id = parent_id")
        if _has_table(conn, "recall_log"):
            conn.execute(f"UPDATE recall_log SET memory_id = ? WHERE memory_id IN ({marks})", [keep_id] + doomed)
        if _has_table(conn, "memory_links"):
            for column in ("a", "b"):
                conn.execute(f"UPDATE OR IGNORE memory_links SET {column} = ? WHERE {column} IN ({marks})",
                             [keep_id] + doomed)
            conn.execute("DELETE FROM memory_links WHERE a = b")

    def consolidate_memories(self, dry_run: bool = False) -> Dict[str, Any]:
        """Merge duplicates: earliest date kept, seen/recall counts summed, latest sighting as timestamp."""
        from eli.memory import policy as _policy
        summary: Dict[str, Any] = {"groups": 0, "removed": 0, "scanned": 0, "dry_run": bool(dry_run)}
        conn = self._get_connection()
        try:
            cols = _table_columns(conn, "memories")
            have = "seen_count" in cols
            sel = ("id, COALESCE(text, content, value, ''), COALESCE(tags, ''), source, kind, "
                   "COALESCE(importance, 0.5), COALESCE(weight, 1.0), COALESCE(timestamp, ts, 0)")
            if have:
                sel += (", COALESCE(event_ts, 0), COALESCE(seen_count, 1), COALESCE(last_seen, 0), "
                        "COALESCE(recall_count, 0), COALESCE(last_recalled, 0), COALESCE(origin, ''), "
                        + ("COALESCE(corrected_count, 0)" if "corrected_count" in cols else "0"))
            rows = conn.execute(f"SELECT {sel} FROM memories ORDER BY id ASC").fetchall()
            summary["scanned"] = len(rows)
            groups: Dict[tuple, List[dict]] = {}
            for r in rows:
                d = {"id": r[0], "text": r[1], "tags": r[2], "source": r[3], "kind": r[4],
                     "importance": r[5], "weight": r[6], "ts": r[7]}
                if have:
                    d.update({"event_ts": r[8], "seen_count": r[9], "last_seen": r[10],
                              "recall_count": r[11], "last_recalled": r[12], "origin": r[13],
                              "corrected_count": r[14]})
                key = _policy.text_key(d["text"])
                if not key:
                    continue
                origin = d.get("origin") or _policy.classify_origin(d["source"], d["kind"], d["tags"], d["text"])
                groups.setdefault((key, origin), []).append(d)
            for (_key, origin), members in groups.items():
                if len(members) < 2:
                    continue
                members.sort(key=lambda m: (float(m.get("event_ts") or m["ts"] or 0) or float("inf"), m["id"]))
                keep, doomed = members[0], members[1:]
                merged = _policy.merge_group(members)
                tags: List[str] = []
                for m in members:
                    for tag in str(m["tags"] or "").split(","):
                        tag = tag.strip()
                        if tag and tag not in tags:
                            tags.append(tag)
                summary["groups"] += 1
                summary["removed"] += len(doomed)
                if dry_run:
                    continue
                _fts_delete(conn, keep["id"], keep["text"], keep["tags"])
                sets = "importance = ?, weight = ?, tags = ?, timestamp = ?"
                vals: list = [merged["importance"], max(m["weight"] for m in members), ",".join(tags),
                              merged["last_seen"] or keep["ts"]]
                if have:
                    sets += (", seen_count = ?, event_ts = ?, last_seen = ?, recall_count = ?, "
                             "last_recalled = ?, origin = ?, text_key = ?")
                    vals += [merged["seen_count"], merged["event_ts"] or keep["ts"], merged["last_seen"],
                             merged["recall_count"], merged["last_recalled"], origin, _key]
                    if "corrected_count" in cols:
                        sets += ", corrected_count = ?"
                        vals.append(merged["corrected_count"])
                conn.execute(f"UPDATE memories SET {sets} WHERE id = ?", vals + [keep["id"]])
                _fts_reindex(conn, keep["id"], keep["text"], ",".join(tags))
                self._repoint(conn, [m["id"] for m in doomed], keep["id"])
                self._remove_rows(conn, doomed)
            if not dry_run:
                if summary["removed"]:
                    summary["fts_rebuilt"] = _fts_rebuild(conn)
                conn.commit()
            return summary
        except Exception as e:
            log.debug(f"[MEMORY] consolidate_memories failed: {e}")
            summary["error"] = str(e)
            return summary
        finally:
            conn.close()

    def raise_importance(self, ids, gain: float, *, reason: str = "") -> int:
        """A surprise about these memories (eli/memory/salience.py): each one's importance moves `gain` of
        the way from where it is to one, never past it. How many rows moved."""
        ids = sorted({int(i) for i in (ids or []) if str(i).lstrip("-").isdigit() and int(i) > 0})
        gain = max(0.0, min(1.0, float(gain or 0.0)))
        if not ids or gain <= 0.0:
            return 0
        conn = self._get_connection()
        try:
            marks = ",".join("?" * len(ids))
            cur = conn.execute(
                f"UPDATE memories SET importance = MIN(1.0, COALESCE(importance, 0.5) + (1.0 - COALESCE(importance, 0.5)) * ?) "
                f"WHERE id IN ({marks})", [gain] + ids)
            conn.commit()
            log.debug("[MEMORY] importance raised on %d rows (%s)", cur.rowcount or 0, reason)
            return int(cur.rowcount or 0)
        except Exception:
            log.debug("importance not raised", exc_info=True)
            return 0
        finally:
            conn.close()

    def promote_used(self, dry_run: bool = False) -> Dict[str, Any]:
        """Upkeep: what the outcomes said of a memory settles into its importance. A memory that went
        into answers nobody corrected (record_recall_outcome) stands at least as high as its share of
        helpful uses, the mean of a Beta(1, 1) over helped against corrected; one corrected more often
        than it helped stands no higher than that share. Pure arithmetic on the counts, so a missed
        day costs nothing. {"promoted", "demoted"}; a thought of the pass is kept when anything moved."""
        out: Dict[str, Any] = {"promoted": 0, "demoted": 0, "dry_run": bool(dry_run)}
        conn = self._get_connection()
        try:
            cols = _table_columns(conn, "memories")
            if "recall_count" not in cols or "corrected_count" not in cols:
                return out
            rows = conn.execute(
                "SELECT id, COALESCE(importance, 0.5), COALESCE(recall_count, 0), COALESCE(corrected_count, 0) "
                "FROM memories WHERE COALESCE(recall_count, 0) > 0 OR COALESCE(corrected_count, 0) > 0").fetchall()
            up, down = [], []
            for mid, importance, helped, corrected in rows:
                share = (float(helped) + 1.0) / (float(helped) + float(corrected) + 2.0)
                if helped > corrected and share > float(importance) + 1e-9:
                    up.append((share, int(mid)))
                elif corrected > helped and share < float(importance) - 1e-9:
                    down.append((share, int(mid)))
            out["promoted"], out["demoted"] = len(up), len(down)
            if dry_run or not (up or down):
                return out
            conn.executemany("UPDATE memories SET importance = ? WHERE id = ?", up + down)
            conn.commit()
        except Exception as e:
            log.debug(f"[MEMORY] promote_used failed: {e}")
            out["error"] = str(e)
            return out
        finally:
            conn.close()
        try:
            from eli.cognition.thoughts import record as _thought
            _thought("memory", f"{out['promoted']} memories that helped answers matter more, {out['demoted']} that were "
                     f"corrected matter less", why=["the day's upkeep read what became of the answers they went into"],
                     decision="raised" if out["promoted"] else "kept", reason="their share of helpful uses")
        except (sqlite3.Error, OSError, ValueError, TypeError):
            log.debug("upkeep thought not kept", exc_info=True)
        return out

    def archive_faded(self, dry_run: bool = False) -> Dict[str, Any]:
        """Move faded, unused, derived rows to memories_archive. Nothing the user said moves."""
        from eli.memory import policy as _policy
        out: Dict[str, Any] = {"archived": 0, "dry_run": bool(dry_run)}
        conn = self._get_connection()
        try:
            cols = _table_columns(conn, "memories")
            if "origin" not in cols:
                return out
            rows = conn.execute(
                "SELECT id, COALESCE(text, content, value, ''), COALESCE(tags, ''), source, kind, "
                "COALESCE(weight, 1.0), COALESCE(recall_count, 0), COALESCE(importance, 0.5), "
                "COALESCE(origin, '') FROM memories WHERE COALESCE(weight, 1.0) <= ?",
                (_policy.ARCHIVE_WEIGHT,),
            ).fetchall()
            doomed = []
            for r in rows:
                origin = r[8] or _policy.classify_origin(r[3], r[4], r[2], r[1])
                if _policy.should_archive(origin, r[5], r[6], r[7]):
                    doomed.append({"id": r[0], "text": r[1], "tags": r[2]})
            out["archived"] = len(doomed)
            if dry_run or not doomed:
                return out
            names = ", ".join(f'"{c}"' for c in cols)
            now = time.time()
            for d in doomed:
                conn.execute(
                    f"INSERT INTO memories_archive ({names}, archived_at, archive_reason) "
                    f"SELECT {names}, ?, ? FROM memories WHERE id = ?",
                    (now, "faded_unused_derived", d["id"]),
                )
            self._remove_rows(conn, doomed)
            _fts_rebuild(conn)
            conn.commit()
            # each one let go is a decision with a reason, kept where "why did you drop that" is answered
            try:
                from eli.cognition.thoughts import record as _thought
                for d, r in zip(doomed, [r for r in rows if _policy.should_archive(
                        r[8] or _policy.classify_origin(r[3], r[4], r[2], r[1]), r[5], r[6], r[7])]):
                    _thought("memory", f"let go: {d['text'][:120]}", why=[f"its strength had faded to {float(r[5]):.2f}",
                             "it never went into an answer", "it was not something you said"],
                             chance=float(r[5]), decision="dropped", reason="faded, unused and derived; it is in the "
                             "archive, searched when live evidence is thin", ref=f"memory:{d['id']}", now=now)
            except (sqlite3.Error, OSError, ValueError, TypeError, ImportError):
                log.debug("archive thoughts not kept", exc_info=True)
            return out
        except Exception as e:
            log.debug(f"[MEMORY] archive_faded failed: {e}")
            out["error"] = str(e)
            return out
        finally:
            conn.close()

    def search_archive(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Keyword search over archived rows (they are out of normal recall, not gone)."""
        conn = self._get_connection()
        try:
            from eli.cognition.scoring import STOPWORDS
            words = [w for w in re.findall(r"\w{3,}", str(query or "").lower()) if w not in STOPWORDS][:8]
            if not words:
                return []
            hits = " + ".join("(lower(COALESCE(text, '')) LIKE ?)" for _ in words)
            need = max(1, int(round(len(words) * 0.6)))
            mine, mine_params = _memory_scope(_table_columns(conn, "memories_archive"))
            rows = conn.execute(
                f"SELECT id, COALESCE(text, ''), origin, archived_at, archive_reason, ({hits}) AS h FROM memories_archive "
                f"WHERE ({hits}) >= ?{mine} ORDER BY h DESC, archived_at DESC LIMIT ?",
                [f"%{w}%" for w in words] * 2 + [need, *mine_params, int(limit)],
            ).fetchall()
            return [{"id": r[0], "text": r[1], "origin": r[2], "archived_at": r[3], "reason": r[4]} for r in rows]
        except Exception:
            return []
        finally:
            conn.close()

    def restore_from_archive(self, ids) -> int:
        """Put archived rows back into live memory at full strength."""
        ids = [int(i) for i in (ids or [])]
        if not ids:
            return 0
        conn = self._get_connection()
        back: List[int] = []
        try:
            cols = _table_columns(conn, "memories")
            names = ", ".join(f'"{c}"' for c in cols)
            n = 0
            for i in ids:
                cur = conn.execute(
                    f"INSERT OR IGNORE INTO memories ({names}) SELECT {names} FROM memories_archive WHERE id = ?", (i,))
                if cur.rowcount:
                    row = conn.execute("SELECT COALESCE(text, ''), COALESCE(tags, '') FROM memories WHERE id = ?", (i,)).fetchone()
                    conn.execute("UPDATE memories SET weight = 1.0, last_recalled = ? WHERE id = ?", (time.time(), i))
                    if row:
                        _fts_reindex(conn, i, row[0], row[1])
                    conn.execute("DELETE FROM memories_archive WHERE id = ?", (i,))
                    n += 1
                    back.append(i)
            conn.commit()
        except Exception:
            return 0
        finally:
            conn.close()
        # letting it go was undone: its thought ("let go: ...", archive_faded) says so
        try:
            from eli.cognition.thoughts import settle as _settle
            for i in back:
                _settle(f"memory:{i}", "undone", how="restored from the archive")
        except (sqlite3.Error, OSError, ValueError, ImportError):
            log.debug("let-go thoughts not settled", exc_info=True)
        return n

    def link_derivation(self, child_id: int, parent_ids, kind: str = "derived") -> int:
        """Record that a memory was made from others (a summary, a reflection), so it never counts as independent evidence."""
        parents = [int(p) for p in (parent_ids or []) if str(p).isdigit() and int(p) != int(child_id)]
        if not parents:
            return 0
        conn = self._get_connection()
        try:
            conn.executemany("INSERT OR IGNORE INTO memory_lineage (child_id, parent_id, kind, created_at) VALUES (?,?,?,?)",
                             [(int(child_id), p, kind, time.time()) for p in parents])
            conn.commit()
            return len(parents)
        except Exception:
            log.debug("lineage not recorded", exc_info=True)
            return 0
        finally:
            conn.close()

    def lineage_root(self, memory_id: int) -> int:
        """The original memory this one ultimately comes from (itself when it is original)."""
        conn = self._get_connection()
        try:
            cur, seen = int(memory_id), set()
            while cur not in seen:
                seen.add(cur)
                row = conn.execute("SELECT parent_id FROM memory_lineage WHERE child_id = ? ORDER BY parent_id LIMIT 1", (cur,)).fetchone()
                if not row:
                    break
                cur = int(row[0])
            return cur
        except Exception:
            return int(memory_id)
        finally:
            conn.close()

    def independent_sources(self, memory_ids) -> int:
        """How many separate originals a set of memories rests on: a fact and its summaries are one."""
        return len({self.lineage_root(i) for i in (memory_ids or [])})

    def forget_candidates(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        """Live memories that match a request to forget something, for the user to confirm."""
        hits = self.recall_memory(str(query or ""), limit=int(limit), verified_only=False) or []
        return [{"id": h["id"], "text": str(h.get("text") or h.get("content") or "")[:160]}
                for h in hits if str(h.get("id", "")).isdigit()]

    # Document text the old folder reader filed as memories: kind "pdf:<id>", from a tool.
    _FILED_DOCUMENTS = "kind LIKE 'pdf:%' AND COALESCE(source, '') = 'tool'"

    def refile_documents(self, dry_run: bool = False) -> Dict[str, Any]:
        """Documents filed as memories go to the document index, where documents are searched; the
        rows leave memory, and so do the graph relations made from tool text, which the extractor read
        as the user's own words. One 349-PDF folder had made 2,399 of 2,929 memories and 969 relations
        such as "User prefers screening". Not forget(): it matches words against the whole profile,
        and book text shares words with everything."""
        conn = self._get_connection()
        try:
            rows = [{"id": r[0], "text": r[1], "tags": r[2]} for r in conn.execute(
                f"SELECT id, COALESCE(text, content, ''), COALESCE(tags, '') FROM memories WHERE {self._FILED_DOCUMENTS}")]
            paths = sorted({m.group(1).strip() for r in rows for m in [re.match(r"PDF (.+)\nPages read:", r["text"])] if m})
            report: Dict[str, Any] = {"memories": len(rows), "documents": len(paths), "relations": 0, "entities": 0}
            has_graph = _has_table(conn, "kg_relations") and _has_table(conn, "kg_entities")
            if has_graph:
                report["relations"] = conn.execute("SELECT COUNT(*) FROM kg_relations WHERE source = 'tool'").fetchone()[0]
            if dry_run or not (rows or report["relations"]):
                return report
            self._remove_rows(conn, rows)
            if has_graph:
                ends = {e for pair in conn.execute("SELECT subject_id, object_id FROM kg_relations WHERE source = 'tool'")
                        for e in pair}
                conn.execute("DELETE FROM kg_relations WHERE source = 'tool'")
                for eid in ends:
                    if not conn.execute("SELECT EXISTS(SELECT 1 FROM kg_relations WHERE subject_id = ? OR object_id = ?)",
                                        (eid, eid)).fetchone()[0] and conn.execute(
                            "SELECT LOWER(name) != 'user' FROM kg_entities WHERE id = ?", (eid,)).fetchone() == (1,):
                        conn.execute("DELETE FROM kg_entities WHERE id = ?", (eid,))
                        report["entities"] += 1
            conn.commit()
        finally:
            conn.close()
        self._drop_turn_cache()
        try:
            from eli.memory.document_index import note_document
            for path in paths:
                if os.path.isfile(path):
                    note_document(path, source="folder analysis")
        except Exception:
            log.debug("filed documents not queued for the index", exc_info=True)
        log.info("[MEMORY] moved %d document passages out of memory (%d files to the document index), "
                 "%d graph relations and %d entities made from them", report["memories"], report["documents"],
                 report["relations"], report["entities"])
        return report

    # "Most of a memory's content words": more than half of them.
    _FORGET_MOST = 0.5
    # The graph reads a memory in the same call that stores it, or from the turn that said it moments
    # before: a relation first learned within this many seconds of the memory was learned from it.
    _LEARNED_WITH_SECONDS = 60.0

    def forget(self, ids, *, include_turns: bool = True) -> Dict[str, int]:
        """Delete memories and everything made from them: index entries, claims, summaries, profile
        items, stances, patterns, graph relations, turns, the learning replay, the latest-turn copy,
        ELI's thoughts that quoted them. What could not be removed is counted under a *_kept key
        (the audit chain cannot be edited without breaking it)."""
        report: Dict[str, int] = {k: 0 for k in (
            "memories", "derived", "claims", "recall_log", "semantic", "summaries", "profile", "stances", "graph",
            "turns", "patterns", "replay", "conversations", "thoughts", "vectors", "audit_log", "audit_log_kept")}
        wanted = sorted({int(i) for i in (ids or []) if str(i).isdigit()})
        if not wanted:
            return report
        conn = self._get_connection()
        try:
            _ensure_policy_schema(conn)
            frontier, everyone = list(wanted), set(wanted)
            while frontier:
                marks = ",".join("?" * len(frontier))
                kids = [r[0] for r in conn.execute(f"SELECT child_id FROM memory_lineage WHERE parent_id IN ({marks})", frontier)]
                frontier = [k for k in kids if k not in everyone]
                everyone.update(frontier)
            report["derived"] = len(everyone) - len(wanted)
            marks = ",".join("?" * len(everyone))
            whose = "COALESCE(user_id, '')" if "user_id" in _table_columns(conn, "memories") else "''"
            rows = [{"id": r[0], "text": r[1] or "", "tags": r[2] or "", "ts": r[3] or 0, "source": r[4] or "user",
                     "user_id": r[5] or ""}
                    for r in conn.execute(
                        f"SELECT id, COALESCE(text, ''), COALESCE(tags, ''), COALESCE(timestamp, ts, 0), "
                        f"COALESCE(source, 'user'), {whose} FROM memories WHERE id IN ({marks})", sorted(everyone))]
            report["memories"] = len(rows)
            if not rows:
                return report
            self._remove_rows(conn, rows)
            from eli.memory import claims as _claims
            for i in everyone:
                report["claims"] += _claims.retract_from_memory(conn, i, erase=True)
            report["recall_log"] = conn.execute(f"DELETE FROM recall_log WHERE memory_id IN ({marks})", sorted(everyone)).rowcount or 0
            conn.execute(f"DELETE FROM memory_lineage WHERE child_id IN ({marks}) OR parent_id IN ({marks})", sorted(everyone) * 2)
            for r in rows:
                self._forget_derivatives(conn, r["text"], r["ts"], report, include_turns, source=r["source"],
                                         user_key=r["user_id"])
            conn.commit()
        finally:
            conn.close()
        try:
            from eli.memory.vector_store import get_vector_store
            _vs = get_vector_store()
            if _vs is not None:
                report["vectors"] = int(_vs.remove_memories(everyone) or 0)
        except Exception:
            log.debug("forgotten memories not taken out of the vector index", exc_info=True)
        self._drop_turn_cache()
        return report

    @staticmethod
    def _only_in(conn, text: str, raw: str) -> set:
        """The memory's words that nothing else the user said contains: no live memory and no turn
        but the ones being forgotten with it. Such a word (a code, a name said once) can only have
        come from this memory, so whatever holds it was made from it."""
        from eli.cognition.scoring import tokenize
        has_turns = _has_table(conn, "conversation_turns")
        out = set()
        for w in set(tokenize(text)):
            if len(w) < 4 and not any(ch.isdigit() for ch in w):
                continue
            if conn.execute("SELECT 1 FROM memories WHERE instr(lower(COALESCE(text, '')), ?) > 0 LIMIT 1", (w,)).fetchone():
                continue
            if has_turns and conn.execute(
                    "SELECT 1 FROM conversation_turns WHERE instr(lower(COALESCE(content, '')), ?) > 0 "
                    "AND instr(lower(COALESCE(content, '')), ?) = 0 LIMIT 1", (w, raw[:120])).fetchone():
                continue
            out.add(w)
        return out

    @staticmethod
    def _forget_derivatives(conn, text: str, ts: float, report: Dict[str, int], include_turns: bool,
                            source: str = "user", user_key: str = "") -> None:
        """Remove what was made from one forgotten memory. Something was made from it when it quotes
        the memory whole, is a piece of it (a quote, a thought's excerpt), holds most of its content
        words, or holds a word nothing else the user said contains (_only_in). One shared capitalised
        word ("Tomorrow", a friend's name) deleted every fact, stance and pattern that mentioned it."""
        from eli.cognition.scoring import tokenize
        from eli.memory import policy as _policy
        raw = " ".join(str(text or "").lower().split())
        needle = _policy.normalise_text(text)
        if len(needle) < 6:
            return
        words = set(tokenize(text))
        only_here = Memory._only_in(conn, text, raw)

        def derived(value) -> bool:
            v = _policy.normalise_text(value)
            if not v:
                return False
            if needle in v:
                return True
            held = set(tokenize(str(value or "")))
            if len(held) >= 2 and v in needle:
                return True
            if words and len(words & held) / len(words) > Memory._FORGET_MOST:
                return True
            return bool(only_here & held)

        def has(name: str) -> bool:
            return _has_table(conn, name)

        def columns(name: str) -> set:
            return {r[1] for r in conn.execute(f"PRAGMA table_info({name})")}

        if has("semantic"):
            for rid, fact, evidence in conn.execute("SELECT id, COALESCE(fact, ''), COALESCE(evidence, '[]') FROM semantic").fetchall():
                try:
                    quotes = json.loads(evidence or "[]")
                except Exception:
                    quotes = []
                kept = [q for q in quotes if not derived(q)]
                if len(kept) != len(quotes) or (not quotes and derived(fact)):
                    report["semantic"] += 1
                    if kept:
                        conn.execute("UPDATE semantic SET evidence = ?, evidence_count = ? WHERE id = ?", (json.dumps(kept), len(kept), rid))
                    else:
                        conn.execute("DELETE FROM semantic WHERE id = ?", (rid,))
        if has("session_summaries"):
            for rid, summ, content in conn.execute("SELECT id, COALESCE(summary, ''), COALESCE(content, '') FROM session_summaries").fetchall():
                if derived(summ) or derived(content):
                    conn.execute("DELETE FROM session_summaries WHERE id = ?", (rid,))
                    report["summaries"] += 1
        if has("user_model"):
            for row in conn.execute("SELECT user_id, identity, comms_style, current_focus, interests, habits, goals, relationship, dossier, brief FROM user_model").fetchall():
                uid, changed, sets = row[0], False, {}
                for name, val in zip(("identity", "comms_style", "current_focus", "interests", "habits", "goals", "relationship", "dossier", "brief"), row[1:]):
                    if not val:
                        continue
                    try:
                        items = json.loads(val)
                    except Exception:
                        items = None
                    if isinstance(items, list):
                        kept_items = [i for i in items if not derived(i)]
                        if len(kept_items) != len(items):
                            sets[name], changed = json.dumps(kept_items), True
                    elif derived(val):
                        sets[name], changed = "", True
                if changed:
                    conn.execute("UPDATE user_model SET " + ", ".join(f"{k} = ?" for k in sets) + " WHERE user_id = ?", [*sets.values(), uid])
                    report["profile"] += 1
        for table, cols in (("eli_stances", ("topic", "position")), ("belief_revisions", ("topic", "old_value", "new_value"))):
            if has(table):
                for rid, *vals in conn.execute(f"SELECT id, {', '.join(cols)} FROM {table}").fetchall():
                    if derived(" ".join(str(v or "") for v in vals)):
                        conn.execute(f"DELETE FROM {table} WHERE id = ?", (rid,))
                        report["stances"] += 1
        if has("user_patterns") and "pattern_data" in columns("user_patterns"):
            for rid, val in conn.execute("SELECT id, pattern_data FROM user_patterns").fetchall():
                if derived(val):
                    conn.execute("DELETE FROM user_patterns WHERE id = ?", (rid,))
                    report["patterns"] += 1
        # claims read from the turn itself carry no memory id: the words they were read from say it
        if has("memory_claims"):
            from eli.memory import claims as _claims
            hit_ids = [rid for rid, said in conn.execute(
                "SELECT id, COALESCE(source_text, '') FROM memory_claims WHERE COALESCE(source_text, '') != ''").fetchall()
                if derived(said)]
            for rid in hit_ids:
                report["claims"] += _claims.erase_claims(conn, "id = ?", (rid,))
        if has("learning_replay"):
            for rid, said, replied in conn.execute(
                    "SELECT id, COALESCE(input_text, ''), COALESCE(output_text, '') FROM learning_replay").fetchall():
                if derived(said) or derived(replied):
                    conn.execute("DELETE FROM learning_replay WHERE id = ?", (rid,))
                    report["replay"] += 1
        if has("conversations"):
            for rid, content in conn.execute("SELECT id, COALESCE(content, '') FROM conversations").fetchall():
                if content and derived(content):
                    conn.execute("UPDATE conversations SET content = '' WHERE id = ?", (rid,))
                    report["conversations"] += 1
        if has("thoughts"):
            for rid, what, subject, reason, why in conn.execute(
                    "SELECT id, COALESCE(what, ''), COALESCE(subject, ''), COALESCE(reason, ''), COALESCE(why_json, '') "
                    "FROM thoughts").fetchall():
                if derived(what) or derived(subject) or derived(reason) or derived(why):
                    conn.execute("DELETE FROM thoughts WHERE id = ?", (rid,))
                    report["thoughts"] += 1
        if has("runtime_events"):
            # The audit chain commits to each row's words, so they are taken out by the ledger's own
            # redaction, which the chain records and verify_chain accepts; a row it could not redact
            # stays, and is reported, not hidden.
            from eli.runtime import evidence_ledger as _ledger
            _ledger.ensure_schema(conn)
            # its words or what it was about; a payload (a whole result) only when it holds the memory
            quoting = [rid for rid, content, subject, payload in conn.execute(
                "SELECT id, COALESCE(content, ''), COALESCE(subject, ''), COALESCE(payload_json, '') "
                "FROM runtime_events WHERE redacted_at IS NULL AND COALESCE(event_type, '') != ?",
                (_ledger.REDACTION_EVENT,))
                if derived(content) or derived(subject) or needle in _policy.normalise_text(payload)]
            if quoting:
                try:
                    taken = _ledger.redact(conn, quoting)
                except Exception:
                    log.warning("audit rows quoting a forgotten memory could not be redacted", exc_info=True)
                    taken = 0
                report["audit_log"] += taken
                report["audit_log_kept"] += len(quoting) - taken
        if has("kg_relations") and has("kg_entities") and ts:
            from eli.memory.knowledge_graph import read_triples
            ends = set()
            mine, mine_params = ((" AND COALESCE(r.user_id, '') = ?", (user_key or "",))
                                 if "user_id" in columns("kg_relations") else ("", ()))
            for subj, predicate, obj in set(read_triples(text, source)):
                for rid, sid, oid in conn.execute(
                        "SELECT r.id, r.subject_id, r.object_id FROM kg_relations r "
                        "JOIN kg_entities s ON s.id = r.subject_id JOIN kg_entities o ON o.id = r.object_id "
                        "WHERE LOWER(s.name) = LOWER(?) AND LOWER(r.predicate) = LOWER(?) AND LOWER(o.name) = LOWER(?) "
                        "AND ABS(COALESCE(r.ts, 0) - ?) <= ?" + mine,
                        (subj, predicate, obj, float(ts), Memory._LEARNED_WITH_SECONDS, *mine_params)).fetchall():
                    conn.execute("DELETE FROM kg_relations WHERE id = ?", (rid,))
                    report["graph"] += 1
                    ends.update((sid, oid))
            for eid in ends:
                if conn.execute("SELECT 1 FROM kg_relations WHERE subject_id = ? OR object_id = ? LIMIT 1", (eid, eid)).fetchone():
                    continue
                conn.execute("DELETE FROM kg_entities WHERE id = ? AND LOWER(name) != 'user'", (eid,))
        if include_turns and has("conversation_turns"):
            report["turns"] += conn.execute("DELETE FROM conversation_turns WHERE lower(COALESCE(content, '')) = ? OR lower(COALESCE(content, '')) LIKE ?",
                                            (raw[:120], f"%{raw[:120]}%")).rowcount or 0

    def _drop_turn_cache(self) -> None:
        """Retrieval caches each turn's evidence; after memories or claims change the next question must not get the old one."""
        try:
            from eli.memory.retrieval import invalidate_turn_cache
            invalidate_turn_cache()
        except Exception:
            log.debug("turn cache not cleared", exc_info=True)

    def _claims_conn(self):
        from eli.memory import claims as _claims
        conn = self._get_connection()
        _claims.ensure_schema(conn)
        return conn

    @staticmethod
    def _claim_subject(user_id=None) -> str:
        """Who the turn's claims are about: the owner ("user") or one API member ("user:<id>")."""
        from eli.memory import claims as _claims
        from eli.memory import policy as _policy
        return _claims.user_subject(_policy.owner_key(user_id))

    def record_observed(self, relation: str, value: str, when: Optional[float] = None, source: str = "") -> Optional[int]:
        """Something ELI saw rather than was told, such as the show playing in the user's player. Activities only."""
        from eli.memory import claims as _claims
        if relation not in _claims.CHANGES_OFTEN or not str(value or "").strip():
            return None
        conn = self._claims_conn()
        try:
            cid = _claims.record(conn, relation, value, valid_from=when, source_text=source, origin="observed",
                                 subject=self._claim_subject())
            conn.commit()
        finally:
            conn.close()
        self._drop_turn_cache()
        return cid

    def backfill_claims(self, dry_run: bool = False) -> Dict[str, Any]:
        """Read the whole conversation for claims, oldest first, once per extraction version: what the user said
        in every message, and the episodes ELI's own player replies named ("(Game of Thrones — S2 E5 ...)")."""
        from eli.memory import claims as _claims
        out: Dict[str, Any] = {"turns": 0, "claims": 0}
        conn = self._claims_conn()
        try:
            if self._meta_get(conn, "claims_extract_version") == _claims.EXTRACT_VERSION:
                out["skipped"] = "current"
                return out
            if "conversation_turns" not in _table_names(conn):
                return out
            rows = conn.execute("SELECT role, content, COALESCE(ts, timestamp, 0), COALESCE(user_id, '') "
                                "FROM conversation_turns ORDER BY COALESCE(ts, timestamp, 0), id").fetchall()
            for role, content, ts, who in rows:
                text, when = str(content or ""), float(ts or 0.0) or None
                subject = self._claim_subject(who)
                out["turns"] += 1
                if str(role or "").lower() == "user":
                    found = _claims.extract(text)
                    out["claims"] += len(found)
                    if found and not dry_run:
                        _claims.record_from_text(conn, text, when=when, subject=subject)
                elif str(role or "").lower() == "assistant":
                    for inner in re.findall(r"\(([^()]{3,160})\)", text):
                        show = _claims.observed_show(inner)
                        if show:
                            out["claims"] += 1
                            if not dry_run:
                                _claims.record(conn, "watching", show, valid_from=when, source_text=inner,
                                               origin="observed", subject=subject)
            if not dry_run:
                self._meta_set(conn, "claims_extract_version", _claims.EXTRACT_VERSION)
                conn.commit()
            return out
        except Exception as e:
            out["error"] = str(e)
            return out
        finally:
            conn.close()

    def record_claims_from(self, text: str, memory_id: Optional[int] = None, when: Optional[float] = None,
                           user_id: Optional[str] = None) -> List[int]:
        """Turn first-person statements into dated claims about whoever said them (user_id None: the
        turn's user). A changed fact supersedes that user's old one and keeps it as history."""
        from eli.memory import claims as _claims
        if not _claims.extract(text):
            return []
        conn = self._claims_conn()
        try:
            ids = _claims.record_from_text(conn, text, source_memory_id=memory_id, when=when,
                                           root_id=self.lineage_root(memory_id) if memory_id else None,
                                           subject=self._claim_subject(user_id))
            conn.commit()
        finally:
            conn.close()
        if ids:
            self._drop_turn_cache()
        return ids

    def contradict_claim(self, claim_id: Any, *, by: str = "user_said") -> bool:
        """A claim the user corrected stands contested and less sure (claims.contradict)."""
        from eli.memory import claims as _claims
        conn = self._claims_conn()
        try:
            done = _claims.contradict(conn, claim_id, by=by)
            conn.commit()
        finally:
            conn.close()
        if done:
            self._drop_turn_cache()
        return done

    def _claims_query(self, fn, *args):
        conn = self._claims_conn()
        try:
            return fn(conn, *args)
        finally:
            conn.close()

    # Every reader below is about the turn's user (_claim_subject): never another member's claims,
    # never ELI's own stances (claims.SUBJECT_ELI).
    def claims_current(self):
        from eli.memory import claims as _c
        return self._claims_query(_c.current, self._claim_subject())

    def claims_disputed(self):
        from eli.memory import claims as _c
        return self._claims_query(lambda conn: _c._rows(
            conn, f"SELECT {_c._COLS} FROM memory_claims WHERE subject = ? AND status = 'disputed' ORDER BY id",
            (self._claim_subject(),)))

    def claim_conflicts(self):
        from eli.memory import claims as _c
        return self._claims_query(_c.open_conflicts, self._claim_subject())

    def resolve_claim_conflict(self, relation: str, value: str) -> int:
        from eli.memory import claims as _c
        conn = self._claims_conn()
        try:
            n = _c.resolve_conflict(conn, relation, value, subject=self._claim_subject())
            conn.commit()
            return n
        finally:
            conn.close()

    def claims_during(self, start: float, end: float):
        from eli.memory import claims as _c
        return self._claims_query(_c.valid_during, start, end, self._claim_subject())

    def claims_known_at(self, when: float):
        from eli.memory import claims as _c
        return self._claims_query(_c.known_at, when, self._claim_subject())

    def claim_history(self, relation: str):
        from eli.memory import claims as _c
        return self._claims_query(_c.history, relation, self._claim_subject())

    def memory_facts(self, memory_id: int) -> Dict[str, Any]:
        """What is on record for one memory, for ELI to answer from when the user holds it in its
        rooms (eli/world/embodiment.held_record): its text, when it was kept and from where, how much
        it matters, how often it helped an answer and how often it was corrected. {} when there is no
        such memory."""
        if not str(memory_id).lstrip("-").isdigit() or int(memory_id) <= 0:
            return {}
        conn = self._get_connection()
        try:
            cols = _table_columns(conn, "memories")
            wanted = ["COALESCE(text, content, '')", "COALESCE(timestamp, ts, 0)", "COALESCE(source, '')",
                      "COALESCE(importance, 0.5)",
                      "COALESCE(recall_count, 0)" if "recall_count" in cols else "0",
                      "COALESCE(corrected_count, 0)" if "corrected_count" in cols else "0",
                      "COALESCE(origin, '')" if "origin" in cols else "''",
                      "COALESCE(kind, '')" if "kind" in cols else "''"]
            row = conn.execute(f"SELECT {', '.join(wanted)} FROM memories WHERE id = ?", (int(memory_id),)).fetchone()
        except Exception:
            log.debug("memory facts not read", exc_info=True)
            return {}
        finally:
            conn.close()
        if not row:
            return {}
        text, when, source, importance, helped, corrected, origin, kind = row
        try:
            when = float(when or 0.0)
        except (TypeError, ValueError):
            when = 0.0
        return {"id": int(memory_id), "text": " ".join(str(text or "").split()), "when": when, "source": str(source or ""),
                "importance": float(importance or 0.0), "helped": int(helped or 0), "corrected": int(corrected or 0),
                "origin": str(origin or ""), "kind": str(kind or "")}

    def record_recall_outcome(self, ids, helped: bool) -> int:
        """What the next turn said of the answer these memories went into: endorsed (helped) reinforces
        them and counts a use; corrected weakens them. The engine settles them only on an endorsement
        or a correction (eli/kernel/engine._settle_recall_outcome): a change of subject is no verdict.

        Retrieval alone never strengthens a memory, so a wrong memory that keeps coming back cannot
        make itself more prominent.
        """
        ids = sorted({int(i) for i in (ids or []) if str(i).lstrip("-").isdigit() and int(i) > 0})
        if not ids:
            return 0
        conn = self._get_connection()
        try:
            cols = _table_columns(conn, "memories")
            if "recall_count" not in cols or "corrected_count" not in cols:
                return 0
            now = time.time()
            marks = ",".join("?" * len(ids))
            if helped:
                cur = conn.execute(
                    f"UPDATE memories SET recall_count = COALESCE(recall_count, 0) + 1, last_recalled = ?, "
                    f"weight = 1.0 WHERE id IN ({marks})", [now] + ids)
            else:
                cur = conn.execute(
                    f"UPDATE memories SET corrected_count = COALESCE(corrected_count, 0) + 1, "
                    f"weight = MAX(?, COALESCE(weight, 1.0) * 0.5) WHERE id IN ({marks})",
                    [_policy_min_weight()] + ids)
            conn.commit()
            moved = int(cur.rowcount or 0)
        except Exception:
            log.debug("recall outcome not recorded", exc_info=True)
            return 0
        finally:
            conn.close()
        if helped and ASSOCIATION:
            # what went together into an endorsed answer is linked (strengthen_links)
            self.strengthen_links(ids, now=now)
        return moved

    def strengthen_links(self, ids, *, now: Optional[float] = None, user_id: Optional[str] = None) -> int:
        """Memories that went together into an answer the user endorsed are linked, each pair once more
        (Hebb: what fires together wires together). How many pairs."""
        from eli.memory import policy as _policy
        ids = sorted({int(i) for i in (ids or []) if str(i).isdigit() and int(i) > 0})
        if len(ids) < 2:
            return 0
        now = float(now if now is not None else time.time())
        who = _policy.owner_key(user_id)
        pairs = [(a, b) for i, a in enumerate(ids) for b in ids[i + 1:]]
        conn = self._get_connection()
        try:
            conn.executemany(
                "INSERT INTO memory_links (a, b, user_id, uses, first_used, last_used) VALUES (?, ?, ?, 1, ?, ?) "
                "ON CONFLICT(a, b, user_id) DO UPDATE SET uses = uses + 1, last_used = excluded.last_used",
                [(a, b, who, now, now) for a, b in pairs])
            conn.commit()
            return len(pairs)
        except sqlite3.Error:
            log.debug("links not strengthened", exc_info=True)
            return 0
        finally:
            conn.close()

    @staticmethod
    def _linked(conn, ids, now: float, who: str = "") -> Dict[int, Dict[int, float]]:
        """The memories linked to any of `ids`: {memory: {source among ids: how strongly activation
        spreads along the link}}, the link's chance of coming to mind (eli/memory/policy.retrievable of
        its base-level activation, on ACT-R's fixed scale), so a link used often and lately carries most
        and one unused for long next to nothing."""
        from eli.memory import policy as _policy
        ids = sorted({int(i) for i in ids if str(i).isdigit()})
        if not ids or not _has_table(conn, "memory_links"):
            return {}
        marks = ",".join("?" * len(ids))
        try:
            rows = conn.execute(
                f"SELECT a, b, uses, COALESCE(first_used, last_used, 0), COALESCE(last_used, 0) FROM memory_links "
                f"WHERE (a IN ({marks}) OR b IN ({marks})) AND user_id = ?", (*ids, *ids, who)).fetchall()
        except sqlite3.Error:
            log.debug("links not read", exc_info=True)
            return {}
        source = set(ids)
        out: Dict[int, Dict[int, float]] = {}
        for a, b, uses, first, last in rows:
            for src, other in ((a, b), (b, a)):
                if src not in source:
                    continue
                act = _policy.base_activation(int(uses or 1), max(0.0, (now - float(first)) / _policy.DAY),
                                              [max(0.0, (now - float(last)) / _policy.DAY)])
                out.setdefault(int(other), {})[int(src)] = round(_policy.retrievable([act])[0], 4)
        return out

    def rekey_text_keys(self, dry_run: bool = False) -> Dict[str, Any]:
        """Recompute every row's dedupe key when the key definition changes. Never merges rows."""
        from eli.memory import policy as _policy
        out: Dict[str, Any] = {"rekeyed": 0}
        conn = self._get_connection()
        try:
            if "text_key" not in _table_columns(conn, "memories"):
                return out
            if self._meta_get(conn, "text_key_version") == _policy.KEY_VERSION:
                out["skipped"] = "current"
                return out
            rows = conn.execute("SELECT id, COALESCE(text, content, value, ''), COALESCE(text_key, '') FROM memories").fetchall()
            changes = [(_policy.text_key(t), i) for i, t, k in rows if _policy.text_key(t) != k]
            out["rekeyed"] = len(changes)
            if dry_run:
                return out
            conn.executemany("UPDATE memories SET text_key = ? WHERE id = ?", changes)
            self._meta_set(conn, "text_key_version", _policy.KEY_VERSION)
            conn.commit()
            return out
        except Exception as e:
            out["error"] = str(e)
            return out
        finally:
            conn.close()

    def backfill_policy_fields(self, dry_run: bool = False) -> Dict[str, Any]:
        """Label pre-policy rows (origin, key, true event time). Metadata only; backs up the db first."""
        from eli.memory import policy as _policy
        from eli.runtime.memory_provenance import resolve_write_provenance
        out = {"filled": 0, "relabelled": 0, "redated": 0}
        conn = self._get_connection()
        try:
            cols = _table_columns(conn, "memories")
            if "origin" not in cols:
                return out
            out.update(self._relabel_origins(conn, dry_run))
            todo = conn.execute(
                "SELECT id, COALESCE(text, content, value, ''), COALESCE(tags, ''), source, kind, "
                "COALESCE(timestamp, ts, 0), COALESCE(provenance_kind, ''), COALESCE(verification_status, '') "
                "FROM memories WHERE origin IS NULL OR origin = '' OR text_key IS NULL OR text_key = ''"
            ).fetchall()
            if not todo:
                return out
            if dry_run:
                out["would_fill"] = len(todo)
                return out
            try:
                bak = Path(str(self.db_path) + ".pre_policy.bak")
                if not bak.exists():
                    import shutil as _sh
                    conn.commit()
                    _sh.copy2(str(self.db_path), str(bak))
            except Exception:
                log.debug("policy backup not taken", exc_info=True)
            turns = None
            for (rid, text, tags, source, kind, ts, prov, ver) in todo:
                origin = _policy.classify_origin(source, kind, tags, text)
                event_ts = ts
                if "session_pin" in str(tags) or "memory_recall" in str(tags):
                    if turns is None:
                        turns = {}
                        for c, tt in conn.execute(
                                "SELECT content, COALESCE(timestamp, ts, 0) FROM conversation_turns WHERE role = 'user' ORDER BY 2 DESC"):
                            turns[_policy.text_key(c)] = tt
                    orig = turns.get(_policy.text_key(text))
                    if orig and orig < ts:
                        event_ts = orig
                        out["redated"] += 1
                sets, vals = "origin = ?, text_key = ?, event_ts = COALESCE(NULLIF(event_ts, 0), ?), " \
                             "seen_count = COALESCE(seen_count, 1), last_seen = COALESCE(last_seen, ?)", \
                             [origin, _policy.text_key(text), event_ts, ts]
                if prov == "user_verbatim" and origin != _policy.ORIGIN_USER:
                    v, p = resolve_write_provenance(source=source, kind=kind, tags=tags, text=text)
                    sets += ", verification_status = ?, provenance_kind = ?"
                    vals += [v, p]
                    out["relabelled"] += 1
                if event_ts != ts:
                    sets = sets.replace("event_ts = COALESCE(NULLIF(event_ts, 0), ?)", "event_ts = ?")
                conn.execute(f"UPDATE memories SET {sets} WHERE id = ?", vals + [rid])
                out["filled"] += 1
            conn.commit()
            return out
        except Exception as e:
            log.debug(f"[MEMORY] backfill_policy_fields failed: {e}")
            out["error"] = str(e)
            return out
        finally:
            conn.close()

    def _relabel_origins(self, conn, dry_run: bool) -> Dict[str, int]:
        """Rows labelled by an older classify_origin get today's label, once per policy.ORIGIN_VERSION.
        A row that is no longer the user's own words loses the claims read from it as if it were,
        and its verified-as-said tier with them."""
        from eli.memory import claims as _claims
        from eli.memory import policy as _policy
        from eli.runtime.memory_provenance import resolve_write_provenance
        if self._meta_get(conn, "origin_version") == _policy.ORIGIN_VERSION:
            return {}
        changed = []
        for rid, text, tags, source, kind, origin, prov in conn.execute(
                "SELECT id, COALESCE(text, content, ''), COALESCE(tags, ''), COALESCE(source, 'user'), "
                "COALESCE(kind, 'memory'), origin, COALESCE(provenance_kind, '') FROM memories "
                "WHERE origin IS NOT NULL AND origin != ''").fetchall():
            now_origin = _policy.classify_origin(source, kind, tags, text)
            if now_origin != origin:
                changed.append((rid, text, tags, source, kind, origin, prov, now_origin))
        if dry_run:
            return {"would_relabel_origin": len(changed)}
        withdrawn = 0
        for rid, text, tags, source, kind, origin, prov, now_origin in changed:
            conn.execute("UPDATE memories SET origin = ? WHERE id = ?", (now_origin, rid))
            if origin == _policy.ORIGIN_USER:
                withdrawn += _claims.retract_from_memory(conn, rid)
                if prov == "user_verbatim":
                    v, p = resolve_write_provenance(source=source, kind=kind, tags=tags, text=text)
                    conn.execute("UPDATE memories SET verification_status = ?, provenance_kind = ? WHERE id = ?",
                                 (v, p, rid))
        self._meta_set(conn, "origin_version", _policy.ORIGIN_VERSION)
        conn.commit()
        return {"origin_relabelled": len(changed), "claims_withdrawn": withdrawn}

    def _meta_get(self, conn, key: str) -> Optional[str]:
        try:
            r = conn.execute("SELECT value FROM memory_meta WHERE key = ?", (key,)).fetchone()
            return r[0] if r else None
        except Exception:
            return None

    def _meta_set(self, conn, key: str, value: str) -> None:
        conn.execute("INSERT INTO memory_meta (key, value, updated_at) VALUES (?, ?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                     (key, value, time.time()))

    # The bookkeeping tables nothing learns from past the window ELI's learning reads its outcomes over
    # (eli/cognition/epistemics.DAYS): the recall log, the learning replay buffer (a copy of every
    # turn and command result) and the corrections log. They grew without end, copies of the user's
    # words that informed nothing.
    _LOGS = (("recall_log", "COALESCE(ts, timestamp, 0)"), ("learning_replay", "COALESCE(ts, timestamp, 0)"),
             ("corrections", "COALESCE(ts, timestamp, 0)"))

    def _prune_logs(self, conn, *, dry_run: bool = False, now: Optional[float] = None) -> Dict[str, int]:
        from eli.cognition.epistemics import DAYS
        cutoff = float(now if now is not None else time.time()) - float(DAYS) * 86400.0
        out: Dict[str, int] = {}
        # an improvement proposal nothing found again in that window is closed as obsolete
        if _has_table(conn, "improvements"):
            stale = conn.execute("SELECT COUNT(*) FROM improvements WHERE COALESCE(status, 'pending') IN ('pending', 'open') "
                                 "AND COALESCE(timestamp, ts, created_at, 0) < ?", (cutoff,)).fetchone()[0]
            out["improvements_closed"] = int(stale or 0)
            if stale and not dry_run:
                conn.execute("UPDATE improvements SET status = 'obsolete' WHERE COALESCE(status, 'pending') IN "
                             "('pending', 'open') AND COALESCE(timestamp, ts, created_at, 0) < ?", (cutoff,))
        for table, when in self._LOGS:
            if not _has_table(conn, table):
                continue
            cols = set(_table_columns(conn, table))
            expr = when if {"ts", "timestamp"} <= cols else ("ts" if "ts" in cols else "timestamp" if "timestamp" in cols else "")
            if not expr:
                continue
            n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {expr} < ?", (cutoff,)).fetchone()[0]
            out[f"{table}_pruned"] = int(n or 0)
            if n and not dry_run:
                conn.execute(f"DELETE FROM {table} WHERE {expr} < ?", (cutoff,))
        return out

    def tidy_learning_tables(self, dry_run: bool = False) -> Dict[str, Any]:
        """Drop repeated belief revisions; cap corroboration at the days the user has used ELI. The ledger
        copies of replay rows an older version wrote stay where they are: runtime_events is the hash-chained
        evidence ledger (eli/runtime/evidence_ledger), and deleting from it broke the chain for good, so its
        check could no longer tell tampering from ELI's own upkeep."""
        out: Dict[str, Any] = {"revisions_removed": 0, "corroboration_capped": 0}
        conn = self._get_connection()
        try:
            out.update(self._prune_logs(conn, dry_run=dry_run))
            from eli.cognition.stance_store import compact_revisions
            if _has_table(conn, "eli_stances") and _has_table(conn, "memory_claims"):
                from eli.memory import claims as _claims
                if dry_run:
                    out["stance_claims_moved"] = conn.execute(
                        "SELECT COUNT(DISTINCT c.id) FROM memory_claims c JOIN eli_stances s ON s.claim_id = c.id "
                        "WHERE c.subject = ? AND lower(s.topic) = lower(c.relation) AND s.position = c.value",
                        (_claims.SUBJECT_USER,)).fetchone()[0]
                else:
                    out["stance_claims_moved"] = _claims.move_stance_claims(conn)
            if _has_table(conn, "belief_revisions"):
                dup = conn.execute("SELECT COUNT(*) - COUNT(DISTINCT kind || '|' || topic || '|' || old_value || '|' || new_value) "
                                   "FROM belief_revisions").fetchone()[0]
                out["revisions_removed"] = int(dup or 0)
                if not dry_run and dup:
                    compact_revisions(conn.cursor())
            if _has_table(conn, "user_patterns") and "corroboration" in _table_columns(conn, "user_patterns"):
                days = conn.execute("SELECT COUNT(DISTINCT date(COALESCE(timestamp, ts), 'unixepoch')) "
                                    "FROM conversation_turns WHERE role = 'user'").fetchone()[0] or 1
                n = conn.execute("SELECT COUNT(*) FROM user_patterns WHERE COALESCE(corroboration, 1) > ?", (days,)).fetchone()[0]
                out["corroboration_capped"] = int(n or 0)
                if not dry_run and n:
                    conn.execute("UPDATE user_patterns SET corroboration = ? WHERE COALESCE(corroboration, 1) > ?", (days, days))
            conn.commit()
        except Exception as e:
            out["error"] = str(e)
        finally:
            conn.close()
        return out

    def consolidate_semantic(self, dry_run: bool = False) -> Dict[str, Any]:
        """One row per durable fact: repeats fold into evidence_count, last_seen and up to five quotes."""
        from eli.runtime.profile_extractor import add_evidence, ensure_profile_tables, split_evidence
        out = {"facts_before": 0, "facts_after": 0}
        ensure_profile_tables(self.db_path)
        conn = self._get_connection()
        try:
            if not _has_table(conn, "semantic"):
                return out
            rows = conn.execute("SELECT id, fact, COALESCE(created_at, 0), COALESCE(evidence_count, 1), evidence "
                                "FROM semantic ORDER BY COALESCE(created_at, 0), id").fetchall()
            groups: Dict[str, list] = {}
            for r in rows:
                groups.setdefault(split_evidence(r[1])[0].lower(), []).append(r)
            out.update(facts_before=len(rows), facts_after=len(groups))
            if dry_run or len(groups) == len(rows):
                return out
            for label_l, members in groups.items():
                keep = members[0]
                label = split_evidence(keep[1])[0]
                quotes = keep[4]
                for m in members:
                    quotes = add_evidence(quotes, split_evidence(m[1])[1])
                conn.execute("UPDATE semantic SET fact = ?, evidence_count = ?, last_seen = ?, evidence = ? WHERE id = ?",
                             (label, sum(m[3] for m in members), max(m[2] for m in members), quotes, keep[0]))
                conn.executemany("DELETE FROM semantic WHERE id = ?", [(m[0],) for m in members[1:]])
            conn.commit()
            return out
        except Exception as e:
            out["error"] = str(e)
            return out
        finally:
            conn.close()

    def integrity_report(self) -> Dict[str, Any]:
        """Measured agreement between SQLite, FTS5, the vector index and summaries. No inference."""
        from eli.memory import policy as _policy
        conn = self._get_connection()
        rep: Dict[str, Any] = {}
        try:
            q = lambda sql, *a: conn.execute(sql, a).fetchone()[0]
            ids = {r[0] for r in conn.execute("SELECT id FROM memories")}
            rep["memories"] = len(ids)
            rep["archived"] = q("SELECT COUNT(*) FROM memories_archive") if _has_table(conn, "memories_archive") else 0
            if _has_table(conn, "memories_fts_docsize"):
                fts = {r[0] for r in conn.execute("SELECT id FROM memories_fts_docsize")}
                rep["fts_indexed"], rep["fts_missing"], rep["fts_orphans"] = len(fts), len(ids - fts), len(fts - ids)
            cols = _table_columns(conn, "memories")
            if "text_key" in cols:
                rep["duplicate_groups"] = q("SELECT COUNT(*) FROM (SELECT 1 FROM memories WHERE text_key <> '' "
                                            "GROUP BY text_key, origin HAVING COUNT(*) > 1)")
                rep["unlabelled"] = q("SELECT COUNT(*) FROM memories WHERE origin IS NULL OR origin = ''")
                embeddable = {r[0] for r in conn.execute("SELECT id, origin FROM memories") if _policy.wants_vector_index(r[1] or "")}
            else:
                embeddable = ids
            try:
                from eli.memory.vector_store import get_vector_store
                vs = get_vector_store()
                if vs is not None:
                    tomb = set(getattr(vs, "_tombstone_ids", ()) or ())
                    vec_ids = {int(m["memory_id"]) for m in vs._meta if m.get("memory_id") is not None} - tomb
                    rep.update(vectors=vs.ntotal, vector_meta=vs.meta_count, vector_orphans=len(vec_ids - ids),
                               missing_vectors=len(embeddable - vec_ids))
            except Exception:
                rep["vectors"] = None
            if _has_table(conn, "session_summaries") and _has_table(conn, "conversation_turns"):
                sess = {r[0] for r in conn.execute("SELECT DISTINCT session_id FROM conversation_turns")}
                summ = {r[0] for r in conn.execute("SELECT DISTINCT session_id FROM session_summaries")}
                rep["sessions"], rep["sessions_without_summary"], rep["summaries_without_session"] = len(sess), len(sess - summ), len(summ - sess)
            rep["ok"] = not any(rep.get(k) for k in ("fts_missing", "fts_orphans", "vector_orphans", "missing_vectors",
                                                     "duplicate_groups", "unlabelled"))
            rep["status"] = "healthy" if rep["ok"] else "degraded"
            if rep.get("vectors") is None:
                rep["vector_index"] = "unavailable"
        except Exception as e:
            rep["error"] = str(e)
        finally:
            conn.close()
        return rep

    _UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
    _JSON_LISTS = ("identity", "comms_style", "current_focus", "interests", "habits", "goals", "relationship", "sources")

    def owner_id(self, candidate: str) -> str:
        """The install's stable user id, kept in the database so a reinstall can't change it."""
        conn = self._get_connection()
        try:
            cur = self._meta_get(conn, "owner_id")
            if not cur:
                self._meta_set(conn, "owner_id", candidate)
                conn.commit()
            return cur or candidate
        finally:
            conn.close()

    def merge_user_ids(self) -> Dict[str, Any]:
        """Fold this install's earlier auto-generated user ids into the owner id. Named ids are untouched."""
        out: Dict[str, Any] = {"merged_ids": 0, "rows": 0}
        conn = self._get_connection()
        try:
            owner = self._meta_get(conn, "owner_id")
            if not owner:
                return out
            old = set()
            for table in ("conversation_turns", "conversations", "session_summaries", "emotion_events", "user_model"):
                if _has_table(conn, table):
                    old |= {r[0] for r in conn.execute(f"SELECT DISTINCT user_id FROM {table}") if r[0]}
            old = {u for u in old if u != owner and self._UUID.match(u)}
            if not old:
                return out
            for table in ("conversation_turns", "conversations", "session_summaries", "emotion_events"):
                if _has_table(conn, table):
                    for u in old:
                        out["rows"] += conn.execute(f"UPDATE {table} SET user_id = ? WHERE user_id = ?", (owner, u)).rowcount
            self._merge_user_models(conn, owner, old)
            out["merged_ids"] = len(old)
            conn.commit()
            return out
        except Exception as e:
            out["error"] = str(e)
            return out
        finally:
            conn.close()

    def _merge_user_models(self, conn, owner: str, old: set) -> None:
        if not _has_table(conn, "user_model"):
            return
        import json as _json
        from eli.memory import policy as _policy
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM user_model WHERE user_id IN (%s)" % ",".join("?" * (len(old) + 1)),
                            [owner, *old]).fetchall()
        conn.row_factory = None
        if not rows:
            return
        rows = sorted(rows, key=lambda r: (r["user_id"] != owner, -(r["updated_at"] or 0)))
        newest = max(rows, key=lambda r: r["updated_at"] or 0)
        merged = {"user_id": owner, "confidence": max((r["confidence"] or 0) for r in rows),
                  "updated_at": newest["updated_at"], "ts": newest["ts"],
                  "dossier": newest["dossier"], "brief": newest["brief"]}
        for col in self._JSON_LISTS:
            seen, items = set(), []
            for r in rows:
                try:
                    vals = _json.loads(r[col] or "[]")
                except (TypeError, ValueError):
                    vals = []
                for v in vals if isinstance(vals, list) else []:
                    k = _policy.text_key(str(v))
                    if k and k not in seen:
                        seen.add(k)
                        items.append(v)
            merged[col] = _json.dumps(items, ensure_ascii=False)
        conn.execute("DELETE FROM user_model WHERE user_id IN (%s)" % ",".join("?" * (len(old) + 1)), [owner, *old])
        conn.execute(f"INSERT INTO user_model ({', '.join(merged)}) VALUES ({', '.join('?' * len(merged))})",
                     list(merged.values()))

    _upkeep_lock = threading.Lock()

    def upkeep_async(self) -> bool:
        """Start today's upkeep in the background if it is due and not already running."""
        from eli.core.paths import test_mode
        if test_mode() or not self.upkeep_due() \
                or not Memory._upkeep_lock.acquire(blocking=False):
            return False

        def _run():
            try:
                self.run_upkeep()
            finally:
                Memory._upkeep_lock.release()

        threading.Thread(target=_run, daemon=True, name="eli-memory-upkeep").start()
        return True

    def upkeep_due(self) -> bool:
        """True when upkeep has not completed today. A partly failed run is retried after an hour."""
        conn = self._get_connection()
        try:
            # A history read pending after an update runs on this start, not tomorrow.
            from eli.memory import claims as _claims
            pending = self._meta_get(conn, "claims_extract_version") != _claims.EXTRACT_VERSION or bool(
                conn.execute(f"SELECT EXISTS(SELECT 1 FROM memories WHERE {self._FILED_DOCUMENTS})").fetchone()[0])
            if self._meta_get(conn, "last_upkeep_day") == time.strftime("%Y-%m-%d") and not pending:
                return False
            try:
                attempt = float(self._meta_get(conn, "last_upkeep_attempt") or 0.0)
            except (TypeError, ValueError):
                attempt = 0.0
            return (time.time() - attempt) >= 3600.0 or self._meta_get(conn, "last_upkeep_status") != "partial"
        finally:
            conn.close()

    def run_upkeep(self, force: bool = False, dry_run: bool = False) -> Dict[str, Any]:
        """Once per day: label, merge, decay, archive. Idempotent."""
        report: Dict[str, Any] = {"ran": False}
        if not force and not dry_run and not self.upkeep_due():
            return report
        report["ran"] = True
        steps = (
            ("backfill", lambda: self.backfill_policy_fields(dry_run=dry_run)),
            ("rekey", lambda: self.rekey_text_keys(dry_run=dry_run)),
            ("claims", lambda: self.backfill_claims(dry_run=dry_run)),
            ("consolidate", lambda: self.consolidate_memories(dry_run=dry_run)),
            ("promote", lambda: self.promote_used(dry_run=dry_run)),
            ("decay", lambda: {"updated": self.apply_weight_decay()} if not dry_run else {}),
            ("archive", lambda: self.archive_faded(dry_run=dry_run)),
            ("identity", lambda: self.merge_user_ids() if not dry_run else {}),
            ("learning", lambda: self.tidy_learning_tables(dry_run=dry_run)),
            ("semantic", lambda: self.consolidate_semantic(dry_run=dry_run)),
            ("documents", lambda: self.refile_documents(dry_run=dry_run)),
        )
        for name, fn in steps:
            try:
                report[name] = fn()
            except Exception as e:
                report[name] = {"error": str(e)}
        failed = sorted(k for k, v in report.items() if isinstance(v, dict) and v.get("error"))
        report["status"] = "partial" if failed else "complete"
        if failed:
            report["failed_steps"] = failed
        if not dry_run:
            conn = self._get_connection()
            try:
                import json as _json
                if not failed:
                    self._meta_set(conn, "last_upkeep_day", time.strftime("%Y-%m-%d"))
                self._meta_set(conn, "last_upkeep_status", report["status"])
                self._meta_set(conn, "last_upkeep_attempt", str(time.time()))
                self._meta_set(conn, "last_upkeep_report", _json.dumps(report, default=str)[:4000])
                conn.commit()
            finally:
                conn.close()
        return report

    def add_conversation_turn(
        self,
        role: str,
        content: str,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> int:
        """Add a conversation turn to the configured DB and any explicit secondaries."""
        # Persistence gate is part of the normal write path.
        if callable(_eli_should_store_conversation_turn):
            if not _eli_should_store_conversation_turn(str(role or ""), content):
                return None

        session_id = session_id or "default-session"
        user_id = user_id or "default-user"
        now = time.time()

        # Dedup flag: set True by _write_to_conn when the turn was already stored within the last 10
        # seconds. Gates all downstream writes (log_learning_event, record_event, profile_extractor)
        # so a second concurrent call for the same message produces zero extra rows.
        _was_deduped = [False]

        # Helper to write to a single connection
        def _write_to_conn(conn):
            ccols = _table_columns(conn, "conversations")
            row = conn.execute(
                "SELECT id FROM conversations WHERE session_id = ? AND user_id = ? ORDER BY id DESC LIMIT 1",
                (session_id, user_id),
            ).fetchone()
            if row:
                updates = []
                params = []
                for name, value in [
                    ("updated_at", now), ("timestamp", now), ("ts", now),
                    ("role", role), ("content", content)
                ]:
                    if name in ccols:
                        updates.append(f"{name} = ?")
                        params.append(value)
                if updates:
                    params.append(int(row[0]))
                    conn.execute(f"UPDATE conversations SET {', '.join(updates)} WHERE id = ?", params)
            else:
                payload = {
                    "session_id": session_id,
                    "user_id": user_id,
                    "role": role,
                    "content": content,
                    "timestamp": now,
                    "ts": now,
                    "created_at": now,
                    "updated_at": now,
                    "title": session_id,
                }
                _insert_payload(conn, "conversations", payload)

            # Dedup guard: skip insert if identical (session, role, content) was
            # stored within the last 10 seconds. Prevents double-writes from
            # re-entrant paths (proactive daemon, secondary mirror, etc.).
            existing = conn.execute(
                "SELECT id FROM conversation_turns "
                "WHERE session_id = ? AND role = ? AND content = ? "
                "AND COALESCE(ts, timestamp, 0) >= ? "
                "LIMIT 1",
                (session_id, role, content, now - 10.0),
            ).fetchone()
            if existing:
                _was_deduped[0] = True
                return existing[0]

            payload = {
                "session_id": session_id,
                "user_id": user_id,
                "role": role,
                "content": content,
                "timestamp": now,
                "ts": now,
            }
            rid = _insert_payload(conn, "conversation_turns", payload)
            return rid

        # Primary
        conn_primary = self._get_connection()
        try:
            rid = _write_to_conn(conn_primary)
            conn_primary.commit()
        finally:
            conn_primary.close()

        # If the primary write was deduped, skip all downstream writes.
        # A second concurrent call for the same turn has already been handled.
        if _was_deduped[0]:
            return rid


        # What the user states about themselves becomes a durable memory, recallable without "remember
        # this", and its facts become dated claims and graph relations read from that memory (their
        # source). Only statements: a question, a request, a wish or a hedge is not a fact
        # (eli/memory/salience.states_about_user). User-authored turns only (identity-grade trust).
        if str(role or "").lower() == "user":
            # facts come from the user's own words, never from text they pasted (split_pasted)
            from eli.cognition.correction_patterns import split_pasted
            from eli.memory.salience import states_about_user
            _content_s = split_pasted(str(content or ""))[0]
            # all of it is the turn's user's: a member's statements never become the owner's facts
            from eli.memory import policy as _turn_policy
            _turn_who = _turn_policy.owner_key(user_id)
            try:
                # first, so its importance is read against what ELI held before this message, and its
                # claims carry the memory they came from; said again, the stored row counts the sighting
                if states_about_user(_content_s):
                    self.store_memory(_content_s, tags="conversation,auto_fact", source="conversation",
                                      kind="fact", user_id=_turn_who)
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            try:
                from eli.memory.knowledge_graph import get_knowledge_graph
                get_knowledge_graph().extract_from_memory(_content_s, source="user", user_id=_turn_who)
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            # A dated claim from every message, not only those kept as memories: "GOT is on in the
            # background" said in passing was never stored, and ELI answered with a show from a month before.
            try:
                self.record_claims_from(_content_s, None, now, user_id=_turn_who)
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        try:
            self.log_learning_event(
                "conversation_turn",
                input_text=content if str(role or "").lower() == "user" else "",
                output_text=content if str(role or "").lower() == "assistant" else "",
                action="CHAT",
                outcome=str(role or ""),
                metadata={
                    "session_id": session_id,
                    "user_id": user_id,
                    "role": role,
                    "row_id": rid,
                },
                timestamp=now,
            )
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        try:
            from eli.runtime.diagnostic_patterns import (
                is_user_challenge as _eli_is_user_challenge,
                should_exclude_turn_from_prompt as _eli_exclude_turn,
            )
            from eli.runtime.evidence_ledger import record_event as _eli_record_event

            role_l = str(role or "").lower()
            content_s = str(content or "")
            event_type = "user_challenge" if role_l == "user" and _eli_is_user_challenge(content_s) else "conversation_turn"
            _eli_record_event(
                event_type,
                source="memory.add_conversation_turn",
                action="CHAT",
                subject=role_l,
                content=content_s,
                payload={
                    "session_id": session_id,
                    "user_id": user_id,
                    "role": role,
                    "row_id": rid,
                    "excluded_from_prompt": _eli_exclude_turn(role_l, content_s),
                },
                outcome=role_l,
                reusable=not _eli_exclude_turn(role_l, content_s),
                session_id=str(session_id or ""),
                user_id=str(user_id or ""),
                db_path=self.db_path,
                timestamp=now,
            )
            if event_type == "user_challenge":
                self.log_habit_event(
                    "user_challenge",
                    {
                        "action": "CHAT",
                        "content": content_s[:500],
                        "session_id": session_id,
                        "user_id": user_id,
                        "source": "memory.add_conversation_turn",
                    },
                )
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        if str(role or "").lower() == "user":
            # The learned profile (patterns, semantic facts, the user model) holds one person, the
            # owner: a member's turn feeding it made the member's life the owner's.
            from eli.memory import policy as _profile_policy
            if not _profile_policy.is_member(user_id):
                try:
                    from eli.runtime.profile_extractor import write_patterns_from_turn
                    write_patterns_from_turn(content, db_path=self.db_path, ts_value=now)
                except Exception as e:
                    log.debug(f"[MEMORY] profile extraction failed: {e}")
        else:
            # Notice when ELI commits to a position so it outlives the conversation. Without this a stance
            # exists only in the scrollback: ELI could argue a line for an hour and take the opposite
            # tomorrow without knowing. Detection is narrow and regex-only because it runs on every reply.
            self._capture_stance(content, session_id, user_id)

        return rid

    def _capture_stance(self, reply, session_id=None, user_id=None) -> None:
        """Record a position ELI just took, keyed on what the user asked.

        The topic comes from the preceding USER turn because the user frames the
        subject, and that framing is more stable across sessions than whatever
        wording ELI happened to reach for.
        """
        try:
            from eli.cognition.stance_capture import capture
        except Exception:
            return
        conn = None
        try:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT content FROM conversation_turns "
                " WHERE lower(COALESCE(role,'')) = 'user' "
                + ("  AND session_id = ? " if session_id else "") +
                # Order by id, not timestamp: turns written in the same second tie on timestamp and
                # the winner is arbitrary, so a reply could be keyed to the wrong question. id is
                # autoincrement and is insertion order.
                " ORDER BY id DESC LIMIT 1",
                ((session_id,) if session_id else ()),
            ).fetchone()
            if not row or not row[0]:
                return
            # A complaint, insult or correction aimed at ELI is not a topic ELI holds a position on:
            # "it is 2026 you fucking idiot" was recorded as a stance on "2026 idiot".
            try:
                from eli.cognition.self_claims import asks_about_own_behaviour
                if asks_about_own_behaviour(str(row[0])):
                    return
            except Exception:
                log.debug("stance: own-behaviour check failed", exc_info=True)
            topic = capture(conn.cursor(), str(row[0]), str(reply or ""))
            if topic:
                conn.commit()
                log.debug("[MEMORY] stance recorded on %r", topic)
        except Exception:
            log.debug("[MEMORY] stance capture failed", exc_info=True)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    log.debug("suppressed exception", exc_info=True)

    def store_conversation(self, session_id, role, content, user_id=None):
        return self.add_conversation_turn(
            role=role,
            content=content,
            session_id=session_id,
            user_id=user_id,
        )

    def get_conversation_history(self, session_id, limit=100):
        conn = self._get_connection()
        try:
            rows = conn.execute(
                """
                SELECT COALESCE(timestamp, ts, 0), session_id, user_id, role, content
                FROM conversation_turns
                WHERE session_id = ?
                ORDER BY COALESCE(timestamp, ts, 0) ASC
                LIMIT ?
                """,
                (session_id, int(limit)),
            ).fetchall()

            if not rows:
                ccols = set(_table_columns(conn, "conversations"))
                if {"session_id", "content"} <= ccols:
                    role_expr = "role" if "role" in ccols else "'assistant'"
                    user_expr = "user_id" if "user_id" in ccols else "''"
                    time_expr = "COALESCE(timestamp, updated_at, created_at, ts, id)"
                    rows = conn.execute(
                        f"""
                        SELECT {time_expr}, session_id, {user_expr}, {role_expr}, content
                        FROM conversations
                        WHERE session_id = ?
                        ORDER BY {time_expr} ASC
                        LIMIT ?
                        """,
                        (session_id, int(limit)),
                    ).fetchall()

            return [
                {
                    "timestamp": r[0],
                    "session_id": r[1],
                    "user_id": r[2],
                    "role": r[3],
                    "content": r[4],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def adjust_weight(self, memory_id: int, delta: float) -> None:
        conn = self._get_connection()
        try:
            conn.execute(
                "UPDATE memories SET weight = COALESCE(weight, 1.0) + ? WHERE id = ?",
                (float(delta), int(memory_id)),
            )
            conn.commit()
        finally:
            conn.close()

    def get_recent_turns_since(
        self,
        seconds: int = 900,
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        cutoff = time.time() - max(1, int(seconds))
        conn = self._get_connection()
        try:
            q = "SELECT role, content, timestamp, user_id, session_id FROM conversations WHERE COALESCE(timestamp, ts, 0) >= ?"
            params: List[Any] = [cutoff]
            if user_id:
                q += " AND user_id = ?"
                params.append(user_id)
            if session_id:
                q += " AND session_id = ?"
                params.append(session_id)
            q += " ORDER BY COALESCE(timestamp, ts, 0) DESC LIMIT ?"
            params.append(int(limit))
            rows = conn.execute(q, params).fetchall()
            return [
                {"role": r[0], "content": r[1], "timestamp": r[2], "user_id": r[3], "session_id": r[4]}
                for r in rows
            ]
        finally:
            conn.close()

    def get_session_summaries(self, user_id=None, limit=20):
        conn = self._get_connection()
        try:
            cols = _table_columns(conn, "session_summaries")
            has_user = "user_id" in cols
            order_col = "timestamp" if "timestamp" in cols else ("ended_at" if "ended_at" in cols else "id")
            sql = "SELECT session_id"
            sql += ", user_id" if has_user else ", '' AS user_id"
            sql += ", summary, turns_count, started_at, ended_at, source"
            sql += f" FROM session_summaries"
            params = []
            if user_id and has_user:
                sql += " WHERE user_id = ?"
                params.append(user_id)
            sql += f" ORDER BY COALESCE({order_col}, id) DESC LIMIT ?"
            params.append(int(limit))
            rows = conn.execute(sql, params).fetchall()
            return [
                {
                    "session_id": r[0],
                    "user_id": r[1],
                    "summary": r[2],
                    "turns_count": r[3],
                    "started_at": r[4],
                    "ended_at": r[5],
                    "source": r[6],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def conversation_sessions(self, user_id=None) -> List[Dict[str, Any]]:
        """Each conversation of the turn's user (policy.scope_sql), newest first: its session id, when
        it began and ended, how many turns it had and how many characters they ran to, read as one
        aggregate. For the books ELI's rooms keep of its conversations (eli/world/books.py); the turns
        themselves are get_recent_conversation's."""
        conn = self._get_connection()
        try:
            if not _has_table(conn, "conversation_turns"):
                return []
            from eli.memory import policy as _policy
            mine, mine_params = _policy.scope_sql("user_id", _policy.owner_key(user_id), raw_ids=True)
            junk = " AND content NOT LIKE ?" * len(_NOT_A_TURN_LIKE)
            rows = conn.execute(
                "SELECT session_id, MIN(COALESCE(timestamp, ts, 0)), MAX(COALESCE(timestamp, ts, 0)), COUNT(*), "
                "SUM(LENGTH(COALESCE(content, ''))) FROM conversation_turns "
                "WHERE COALESCE(session_id, '') <> ''" + junk + mine +
                " GROUP BY session_id ORDER BY MAX(COALESCE(timestamp, ts, 0)) DESC",
                (*_NOT_A_TURN_LIKE, *mine_params)).fetchall()
        finally:
            conn.close()
        return [{"session_id": str(r[0]), "started": float(r[1] or 0.0), "ended": float(r[2] or 0.0),
                 "turns": int(r[3] or 0), "chars": int(r[4] or 0)} for r in rows]

    def get_recent_reflections(self, limit=20) -> List[Dict[str, Any]]:
        """ELI's own looking back, newest first: the memories that are a reflection or an insight of its
        own (_OWN_REFLECTION_KINDS, or tagged reflection), of the turn's user's memories. limit -1: all."""
        conn = self._get_connection()
        try:
            if not _has_table(conn, "memories"):
                return []
            mine, mine_params = _memory_scope(_table_columns(conn, "memories"))
            kinds = ",".join("?" * len(_OWN_REFLECTION_KINDS))
            rows = conn.execute(
                "SELECT id, COALESCE(timestamp, ts, 0), COALESCE(kind, ''), COALESCE(text, ''), COALESCE(tags, ''), "
                "COALESCE(source, '') FROM memories WHERE (lower(COALESCE(kind, '')) IN (" + kinds + ") "
                "OR lower(COALESCE(tags, '')) LIKE '%reflection%')" + mine +
                " ORDER BY COALESCE(timestamp, ts, 0) DESC, id DESC LIMIT ?",
                (*_OWN_REFLECTION_KINDS, *mine_params, int(limit))).fetchall()
        finally:
            conn.close()
        return [{"id": r[0], "timestamp": float(r[1] or 0.0), "kind": r[2], "text": " ".join(str(r[3]).split()),
                 "tags": r[4], "source": r[5]} for r in rows if str(r[3]).strip()]

    def stances(self) -> List[Dict[str, Any]]:
        """Every position ELI has held, newest first (eli/cognition/stance_store.listed), each with the
        revisions of its topic."""
        from eli.cognition import stance_store
        conn = self._get_connection()
        try:
            cur = conn.cursor()
            rows = stance_store.listed(cur)
            for row in rows:
                row["revisions"] = stance_store.revisions_for(cur, row["topic"], limit=-1)
            conn.commit()
            return rows
        finally:
            conn.close()

    def set_stance_valence(self, stance_id: int, valence: int) -> bool:
        """Whether a held position is a liking (1), a disliking (-1) or neither (0): stance_store.set_valence."""
        from eli.cognition import stance_store
        conn = self._get_connection()
        try:
            done = stance_store.set_valence(conn.cursor(), int(stance_id), int(valence))
            conn.commit()
            return done
        finally:
            conn.close()

    def search_conversations(self, query, user_id=None, limit=10, session_id=None,
                             include_assistant=False, match_all=False):
        """Turns whose text holds the query's keywords: any one of them, or every one with
        match_all ("drive to the coast" must not match every message that says "drive"). limit=None
        returns every match, oldest first."""
        q = str(query or "").strip()
        if not q:
            return []
        # Extract meaningful keywords from the query instead of using it verbatim
        # as a LIKE pattern (which almost never matches anything useful).
        import re as _re
        _stopwords = {
            "a","an","the","is","are","was","were","be","been","being","have","has",
            "had","do","does","did","will","would","could","should","may","might",
            "i","me","my","we","our","you","your","he","she","it","they","what",
            "how","when","where","who","which","that","this","these","those",
            "and","or","but","if","in","on","at","to","for","of","with","by",
            "say","said","tell","told","ask","asked","know","think","about",
            "last","did","just","not","up","so","no","yes","ok","okay",
        }
        words = _re.findall(r"[a-zA-Z0-9α-ωΐ-Ͽ₀-₉]+", q.lower())
        keywords = [w for w in words if len(w) > 2 and w not in _stopwords]
        if not keywords:
            # Fallback: use all non-trivial words
            keywords = [w for w in words if len(w) > 2]
        if not keywords:
            return []

        conn = self._get_connection()
        try:
            # A turn is relevant if ANY keyword is in it (every one with match_all): looked up in the
            # turns' full-text index (each keyword as a word or the start of one: "drive" finds
            # "driving"), the whole table's LIKE scan only where the index is missing.
            joiner = " AND " if match_all else " OR "
            params: list = []
            if _has_table(conn, "conversation_turns_fts"):
                conditions = ("id IN (SELECT rowid FROM conversation_turns_fts WHERE conversation_turns_fts MATCH ?)")
                params.append(joiner.join('"{}"*'.format(kw.replace('"', '')) for kw in keywords))
            else:
                conditions = joiner.join("LOWER(COALESCE(content, '')) LIKE ?" for _ in keywords)
                params.extend(f"%{kw}%" for kw in keywords)
            # Keyword recall doesn't return ELI's own past statements by default: one fabrication
            # otherwise becomes permanent truth. Labelling it in the prompt didn't hold, an instruction
            # isn't a filter. The sibling search already does `AND role = 'user'`. Assistant turns are
            # reachable via include_assistant=True and get_recent_conversation.
            sql = (
                f"SELECT timestamp, session_id, user_id, role, content, ts "
                f"FROM conversation_turns "
                f"WHERE ({conditions})"
            )
            if not include_assistant:
                sql += " AND LOWER(COALESCE(role,'')) = 'user'"
            if user_id:
                sql += " AND user_id = ?"
                params.append(user_id)
            else:
                # the turn's own user's turns (eli/memory/policy.owner_key): never an API member's
                # in the owner's search, nor the owner's in a member's
                from eli.memory import policy as _scope_policy
                _scope, _scope_params = _scope_policy.scope_sql("user_id", _scope_policy.owner_key(None), raw_ids=True)
                sql += _scope
                params.extend(_scope_params)
            # Fetch DESC then reverse so caller gets chronological order,
            # consistent with get_recent_conversation().
            sql += " ORDER BY COALESCE(timestamp, ts, 0) DESC"
            if limit is not None:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = conn.execute(sql, params).fetchall()
            rows = list(reversed(rows))  # chronological order
            result = [
                {
                    "timestamp": r[0] or r[5] or 0,
                    "session_id": r[1],
                    "user_id": r[2],
                    "role": r[3],
                    "content": r[4],
                }
                for r in rows
            ]
            # B2: drop ELI's own cross-session recall-narration ("from a previous
            # session, we were…") — even rows stored before the B1 store-gate
            # landed — so the self-amplifying echo can't surface in recall.
            try:
                from eli.runtime.persistence_gate import is_recall_narration as _is_narr
                result = [h for h in result if not _is_narr(h.get("content"))]
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            # B2: bias to the ACTIVE session — current-session turns first, so the
            # live conversation outranks days-old matches (stable; recency order
            # preserved within each group).
            if session_id:
                _sid = str(session_id)
                _cur = [h for h in result if str(h.get("session_id")) == _sid]
                _oth = [h for h in result if str(h.get("session_id")) != _sid]
                result = _cur + _oth
            return result
        finally:
            conn.close()

    def get_turns_for_day(self, day: str, user_id: Optional[str] = None, limit: int = 40) -> List[Dict[str, Any]]:
        qday = (day or "").strip()
        if not qday:
            return []
        try:
            start_struct = time.strptime(qday + " 00:00:00", "%Y-%m-%d %H:%M:%S")
            end_struct = time.strptime(qday + " 23:59:59", "%Y-%m-%d %H:%M:%S")
            start_ts = time.mktime(start_struct)
            end_ts = time.mktime(end_struct)
        except Exception:
            return []
        conn = self._get_connection()
        try:
            sql = "SELECT role, content, timestamp, user_id, session_id FROM conversations WHERE COALESCE(timestamp, ts, 0) BETWEEN ? AND ?"
            params: List[Any] = [start_ts, end_ts]
            if user_id:
                sql += " AND user_id = ?"
                params.append(user_id)
            sql += " ORDER BY COALESCE(timestamp, ts, 0) ASC LIMIT ?"
            params.append(int(limit))
            rows = conn.execute(sql, params).fetchall()
            return [{"role": r[0], "content": r[1], "timestamp": r[2], "user_id": r[3], "session_id": r[4]} for r in rows]
        finally:
            conn.close()

    # -----------------------------------------------------------------
    # Recent memories and statistics (added for GUI)
    # -----------------------------------------------------------------

    def memories_between(self, since: float, until: float, limit: int = 40,
                         verified_only: bool = True, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Memories whose event time falls in [since, until), strongest first: the time filter comes before any ranking."""
        from eli.core.self_provenance import memory_exclusion_sql
        from eli.runtime.memory_provenance import verification_filter_sql
        conn = self._get_connection()
        try:
            cols = _table_columns(conn, "memories")
            when = "COALESCE(event_ts, timestamp, ts, 0)" if "event_ts" in cols else "COALESCE(timestamp, ts, 0)"
            imp = "COALESCE(importance, 0.5)" if "importance" in cols else "0.5"
            skip, skip_params = memory_exclusion_sql(cols, alias="")
            ver, ver_params = verification_filter_sql(cols, alias="", verified_only=verified_only)
            mine, mine_params = _memory_scope(cols, "", user_id)
            rows = conn.execute(
                f"SELECT id, {when}, COALESCE(text, content, ''), COALESCE(tags, ''), COALESCE(weight, 1.0), {imp} "
                f"FROM memories WHERE {when} >= ? AND {when} < ?{skip}{ver}{mine} "
                f"ORDER BY {imp} * COALESCE(weight, 1.0) DESC, {when} DESC LIMIT ?",
                (float(since), float(until), *skip_params, *ver_params, *mine_params, int(limit))).fetchall()
            return [{"id": r[0], "ts": r[1], "timestamp": r[1], "event_ts": r[1], "text": r[2], "tags": r[3],
                     "weight": r[4], "importance": r[5], "_source": "window"} for r in rows]
        finally:
            conn.close()

    # The world layer's presence records: a body coming into or leaving ELI's rooms (eli/world/embodiment).
    _PRESENCE_SOURCE = "embodiment"

    def presence_between(self, since: float, until: float, limit: int = 400,
                         user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Every presence record in [since, until), oldest first, all of them: ranked among the
        period's other memories (memories_between keeps the strongest few) they could be cut."""
        conn = self._get_connection()
        try:
            cols = _table_columns(conn, "memories")
            when = "COALESCE(event_ts, timestamp, ts, 0)" if "event_ts" in cols else "COALESCE(timestamp, ts, 0)"
            mine, mine_params = _memory_scope(cols, "", user_id)
            rows = conn.execute(
                f"SELECT id, {when}, COALESCE(text, ''), COALESCE(tags, '') FROM memories "
                f"WHERE COALESCE(source, '') = ? AND {when} >= ? AND {when} < ?{mine} ORDER BY {when} LIMIT ?",
                (self._PRESENCE_SOURCE, float(since), float(until), *mine_params, int(limit))).fetchall()
            return [{"id": r[0], "event_ts": r[1], "timestamp": r[1], "text": r[2], "tags": r[3],
                     "source": self._PRESENCE_SOURCE} for r in rows]
        finally:
            conn.close()

    def get_recent_memories(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Return the most recent memory entries, ordered by timestamp descending."""
        conn = self._get_connection()
        try:
            mine, mine_params = _memory_scope(_table_columns(conn, "memories"))
            cursor = conn.execute(
                "SELECT id, ts, timestamp, kind, text, tags, source, confidence, weight "
                "FROM memories WHERE 1=1" + mine + " ORDER BY COALESCE(timestamp, ts, id) DESC LIMIT ?",
                (*mine_params, limit)
            )
            rows = cursor.fetchall()
            out = []
            for row in rows:
                out.append({
                    "id": row[0],
                    "ts": row[1] or row[2] or 0,
                    "timestamp": row[2] or row[1] or 0,
                    "kind": row[3] or "note",
                    "text": row[4] or "",
                    "tags": row[5] or "",
                    "source": row[6] or "",
                    "confidence": row[7] or 1.0,
                    "weight": row[8] or 1.0,
                })
            return out
        finally:
            conn.close()

    def get_stats(self) -> Dict[str, Any]:
        """Return memory statistics: total count and counts by kind."""
        conn = self._get_connection()
        try:
            total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] or 0
            by_kind_rows = conn.execute(
                "SELECT kind, COUNT(*) FROM memories GROUP BY kind"
            ).fetchall()
            by_kind = {row[0] or "note": row[1] for row in by_kind_rows}
            return {"total": total, "by_kind": by_kind}
        finally:
            conn.close()

    # -----------------------------------------------------------------
    # Habits
    # -----------------------------------------------------------------

    def log_habit_event(self, event_type, details):
        """Record one user-requested action. Writes an audit row to `habit_events` and
        upserts a per-action running tally in `habits` (count + last-used).

        The `habits` table is an *action-usage log* — "what the user has asked ELI to do,
        and how often" — NOT a list of habits to schedule. Scheduling lives in the separate
        `habit_rules` table (add_habit_rule); behavioural-habit *detection* reads the recent
        events that worked (get_detected_habits), not this all-time tally. Surface the raw usage log via
        get_requested_action_log(). Keep these three distinct so a frequently-invoked
        action is never silently promoted into an automated schedule."""
        now = _now_ts()
        details_blob = _jsonify_value(details)
        conn = self._get_connection()
        try:
            from eli.memory import policy as _policy
            rid = _insert_payload(conn, "habit_events", {
                "event_type": _norm_text(event_type),
                "event": _norm_text(event_type),
                "details": details_blob,
                "data": details_blob,
                "timestamp": now,
                "ts": now,
                "name": _norm_text(event_type),
                "command": _norm_text(event_type),
                "user_id": _policy.owner_key(),
            })

            key_name, key_cmd = _habit_key(event_type, details)
            mine, mine_params = _owned_scope(conn, "habits")

            row = conn.execute(
                "SELECT id, COALESCE(count, 0) FROM habits WHERE COALESCE(name,'') = ?" + mine,
                (key_name, *mine_params),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE habits SET count = ?, timestamp = ?, ts = ?, command = COALESCE(command, ?), enabled = 1 WHERE id = ?",
                    (int(row[1] or 0) + 1, now, now, key_cmd, int(row[0])),
                )
            else:
                _insert_payload(conn, "habits", {
                    "name": key_name,
                    "cmd": key_cmd,
                    "method": "auto",
                    "count": 1,
                    "timestamp": now,
                    "ts": now,
                    "command": key_cmd,
                    "enabled": 1,
                    "user_id": _policy.owner_key(),
                })

            # Deliberately no `habit_rules` row here. Rules are time-scheduled and only come from the
            # habit-learning path (eli/planning/habits.py), which clusters real events by time. Promoting
            # every event made rows with no hour/minute and replayed the vocabulary at midnight.

            conn.commit()
            return rid
        finally:
            conn.close()

    def get_habit_events(self, event_type=None, days=14):
        out = _get_habit_events_window(self, event_type=event_type, days=days)
        if out:
            return out

        conn = self._get_connection()
        try:
            if _has_table(conn, "user_patterns"):
                window = _now_ts() - float(days) * 86400.0
                mine, mine_params = _owned_scope(conn, "user_patterns")
                rows = conn.execute(
                    """
                    SELECT id, pattern_data, COALESCE(timestamp, ts, 0)
                    FROM user_patterns
                    WHERE pattern_type = 'app_cmd' AND COALESCE(timestamp, ts, 0) >= ?""" + mine + """
                    ORDER BY COALESCE(timestamp, ts, 0) ASC
                    """,
                    (window, *mine_params),
                ).fetchall()
                derived = []
                for rid, pdata, ts in rows:
                    parsed = pdata
                    if isinstance(pdata, str):
                        try:
                            parsed = json.loads(pdata)
                        except Exception:
                            parsed = pdata
                    derived.append({
                        "id": rid,
                        "event_type": "app_cmd",
                        "details": parsed,
                        "data": parsed,
                        "timestamp": ts,
                    })
                return derived
            return []
        finally:
            conn.close()

    def add_habit_rule(self, name: str, command: str, hour: int, minute: int,
                       days: list = None, enabled: bool = True) -> int:
        # enabled defaults True for manual adds; auto-detection passes enabled=False so a suggested
        # habit never activates until the user enables it in the Habits tab. A habit needs a concrete
        # scheduled time: refuse None/blank so the un-schedulable rows legacy paths made (bogus
        # "active habits at 00:00") never get written.
        if hour is None or minute is None or str(hour) == "" or str(minute) == "":
            raise ValueError("habit rule needs a scheduled hour and minute")
        now = time.time()
        days_json = json.dumps(days) if days else None
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "INSERT INTO habit_rules (name, command, hour, minute, days, enabled, timestamp, ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, command, int(hour), int(minute), days_json,
                 1 if enabled else 0, now, now),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def disable_invalid_habit_rules(self) -> int:
        """Self-heal: disable ENABLED habit rules that can never validly fire —
        a NULL hour/minute (un-schedulable) or command == name (a bare app/action
        token, not a learned "Open X at HH:MM" routine). These are legacy
        corruption that otherwise shows as "active habits at 00:00". Disabled
        (not deleted) so the user can fix or remove them in the Habits tab.
        Returns the number of rules disabled."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "UPDATE habit_rules SET enabled=0 WHERE enabled=1 AND ("
                "hour IS NULL OR minute IS NULL OR "
                "TRIM(LOWER(command)) = TRIM(LOWER(name)))"
            )
            conn.commit()
            return int(cur.rowcount or 0)
        except Exception:
            return 0
        finally:
            conn.close()

    def purge_invalid_habit_rules(self) -> int:
        """Delete un-schedulable legacy habit rows — a NULL hour/minute AND a
        command equal to the bare name (e.g. name='firefox', command='firefox',
        no time). These were written by an old path, can never validly fire, and
        otherwise clutter the Habits tab and surface as a bogus 00:00 offer. A
        user-authored or detected rule always carries a concrete time, so it is
        never matched here. Returns the number of rows deleted."""
        conn = self._get_connection()
        try:
            cur = conn.execute(
                "DELETE FROM habit_rules WHERE "
                "(hour IS NULL OR minute IS NULL) AND "
                "TRIM(LOWER(command)) = TRIM(LOWER(name))"
            )
            conn.commit()
            return int(cur.rowcount or 0)
        except Exception:
            return 0
        finally:
            conn.close()

    def update_habit_rule(self, rule_id: int, *, name: str = None, command: str = None,
                          hour: int = None, minute: int = None, days: list = None) -> bool:
        """Update fields of an existing habit rule (manual edit from the GUI).
        Only provided fields are changed. Returns True if a row was updated."""
        sets, params = [], []
        if name is not None:
            sets.append("name = ?"); params.append(str(name))
        if command is not None:
            sets.append("command = ?"); params.append(str(command))
        if hour is not None:
            sets.append("hour = ?"); params.append(int(hour))
        if minute is not None:
            sets.append("minute = ?"); params.append(int(minute))
        if days is not None:
            sets.append("days = ?"); params.append(json.dumps(days) if days else None)
        if not sets:
            return False
        params.append(int(rule_id))
        conn = self._get_connection()
        try:
            cur = conn.execute(
                f"UPDATE habit_rules SET {', '.join(sets)} WHERE id = ?", params)
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def set_habit_rule_enabled(self, rule_id: int, enabled: bool) -> bool:
        """Enable/disable a habit rule. Enabling a detected suggestion = the user
        approving it (ELI's proactive 'add this habit?' offer)."""
        conn = self._get_connection()
        try:
            cur = conn.execute("UPDATE habit_rules SET enabled = ? WHERE id = ?",
                               (1 if enabled else 0, int(rule_id)))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def delete_habit_rule(self, rule_id: int) -> bool:
        conn = self._get_connection()
        try:
            cur = conn.execute("DELETE FROM habit_rules WHERE id = ?", (int(rule_id),))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    # Meta/introspection/system actions that land in the `habits` table because the
    # user repeatedly *tested* ELI — they are NOT user behavioural habits. Excluded
    # from get_detected_habits() so the surfaced habits are real (media, apps, etc.).
    _HABIT_NOISE_EXACT = {
        "MODEL_LOAD", "NOOP", "CHAT", "MEMORY_STORE", "MEMORY_RECALL",
        "SET_USER_NAME", "TIME", "DATE", "HABIT_STATUS",
    }
    _HABIT_NOISE_RE = re.compile(
        r"(_STATUS$|_AUDIT$|^EXPLAIN_|^SELF_|^RUNTIME_|^COGNITION_|^FRONTIER_|"
        r"^PERSONAL_MEMORY|^NAME_SOURCE|^USER_IDENTITY|^GUI_|^ORCHESTRATOR|"
        r"^AGENTBUS|^OUTPUT_GOVERNOR|^DIAGNOSE_|^EXAMINE_CODE$|^MEMORY_|^REASONING_)"
    )

    def get_detected_habits(self, min_count: int = 3, limit: int = 10):
        """Meaningful detected *behavioural* habits (media, app launches, real user actions),
        excluding ELI's own meta/introspection/system noise. Distinct from get_habit_rules()
        (scheduled time automations).

        A habit is what the user did and what worked, lately: the uses of each action that did not
        fail, by this user (the turn's: eli/memory/policy.owner_key), within one of their own memory
        half-lives (adaptive_half_life: how often they use ELI). It was the all-time tally of every request, failed or not, so a run of failed plays
        grew "play media x31" to x57 and ELI offered to streamline it. Each habit also says how often
        it failed in that window ("failed"), so a caller can tell a routine from something broken."""
        out = []
        try:
            days = self.adaptive_half_life()
            worked: Dict[str, int] = {}
            failed: Dict[str, int] = {}
            command: Dict[str, str] = {}
            order: List[str] = []
            from eli.memory import policy as _policy
            for ev in _get_habit_events_window(self, days=days, user_key=_policy.owner_key()):
                details = ev.get("details")
                name, cmd = _habit_key(ev.get("event_type"), details)
                name = str(name or "").strip()
                if not name:
                    continue
                outcome = details.get("ok", details.get("success")) if isinstance(details, dict) else None
                if name not in command:
                    order.append(name)
                    command[name] = str(cmd or "")
                if outcome is False:
                    failed[name] = failed.get(name, 0) + 1
                else:
                    worked[name] = worked.get(name, 0) + 1
            for name in sorted(order, key=lambda n: worked.get(n, 0), reverse=True):
                up = name.upper()
                if worked.get(name, 0) < int(min_count):
                    continue
                if up in self._HABIT_NOISE_EXACT or self._HABIT_NOISE_RE.search(up):
                    continue
                out.append({"name": name, "count": worked[name], "failed": failed.get(name, 0),
                            "command": command[name], "window_days": round(days, 1)})
                if len(out) >= int(limit):
                    break
        except Exception as exc:
            log.debug("get_detected_habits failed: %s", exc)
        return out

    def get_requested_action_log(self, min_count: int = 1, limit: int = 20):
        """The raw `habits` tally framed for what it actually is: a usage log of the
        actions the USER has asked ELI to perform, most-used first.

        Distinct from the other two habit reads:
          • get_habit_rules()      → *scheduled* time-automations (the habit_rules table)
          • get_detected_habits()  → *behavioural* habits (filters out ELI's own meta/
                                      introspection/system actions as noise)
        This one keeps EVERYTHING (including introspection/system actions like SELF_REPORT
        or GUI_RUNTIME_AUDIT) — those are still actions the user genuinely invoked, so they
        belong in a usage log even though they are not behavioural habits. Returns
        [{action, count, last_used}], newest activity carried for display/sorting."""
        out = []
        try:
            conn = self._get_connection()
            try:
                mine, mine_params = _owned_scope(conn, "habits")
                rows = conn.execute(
                    "SELECT COALESCE(name,''), COALESCE(count,0), "
                    "COALESCE(timestamp, ts, 0) FROM habits "
                    "WHERE COALESCE(enabled,1)=1 AND COALESCE(count,0) >= ?" + mine + " "
                    "ORDER BY COALESCE(count,0) DESC, COALESCE(timestamp, ts, 0) DESC",
                    (int(min_count), *mine_params),
                ).fetchall()
            finally:
                conn.close()
            for r in rows:
                name = str(r[0] or "").strip()
                if not name:
                    continue
                out.append({"action": name, "count": int(r[1] or 0),
                            "last_used": float(r[2] or 0.0)})
                if len(out) >= int(limit):
                    break
        except Exception as exc:
            log.debug("get_requested_action_log failed: %s", exc)
        return out

    def get_habit_rules(self, enabled_only=True):
        conn = self._get_connection()
        try:
            cols = set(_table_columns(conn, "habit_rules"))
            time_expr = "COALESCE(timestamp, ts, id)"
            sql = "SELECT id"
            sql += ", COALESCE(name, '')"
            sql += ", COALESCE(command, action, '')"
            sql += ", COALESCE(hour, 0)"
            sql += ", COALESCE(minute, 0)"
            sql += ", COALESCE(days, '')" if "days" in cols else ", ''"
            sql += ", COALESCE(enabled, 1)"
            sql += f", {time_expr}"
            sql += " FROM habit_rules"
            # A rule without a time is no schedule: read as 00:00 it ran its command at midnight.
            where = ["hour IS NOT NULL", "minute IS NOT NULL"] if {"hour", "minute"} <= cols else ["0"]
            if enabled_only and "enabled" in cols:
                where.append("COALESCE(enabled, 1) = 1")
            sql += " WHERE " + " AND ".join(where)
            sql += f" ORDER BY {time_expr} ASC"
            rows = conn.execute(sql).fetchall()

            out = []
            for r in rows:
                days = None
                if r[5]:
                    try:
                        days = json.loads(r[5])
                    except Exception:
                        days = r[5]
                out.append({
                    "id": r[0],
                    "name": r[1],
                    "command": r[2],
                    "hour": r[3],
                    "minute": r[4],
                    "days": days,
                    "enabled": bool(r[6]),
                    "timestamp": r[7],
                })
            return out
        finally:
            conn.close()

    def record_habit_run(self, rule_id: int) -> None:
        self.log_habit_event("habit_run", {"rule_id": int(rule_id)})

    def log_learning_event(
        self,
        event_type: str,
        input_text: str = "",
        output_text: str = "",
        action: str = "",
        outcome: str = "",
        reward: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        timestamp: Optional[float] = None,
    ) -> int:
        """Append one row to the local replay buffer for continual-learning audits."""
        now = float(timestamp or _now_ts())
        payload = {
            "event_type": _norm_text(event_type),
            "input_text": _norm_text(input_text)[:4000],
            "output_text": _norm_text(output_text)[:4000],
            "action": _norm_text(action),
            "outcome": _norm_text(outcome),
            "reward": reward,
            "metadata": _jsonify_value(metadata or {}),
            "timestamp": now,
            "ts": now,
        }
        conn = self._get_connection()
        try:
            rid = _insert_payload(conn, "learning_replay", payload)
            conn.commit()
            return int(rid or 0)
        finally:
            conn.close()


    def store_app_cmd(self, name, cmd, method=None):
        now = _now_ts()
        name = _norm_text(name).strip()
        if isinstance(cmd, (list, tuple)):
            cmd_str = " ".join(_norm_text(x).strip() for x in cmd if _norm_text(x).strip())
        else:
            cmd_str = _norm_text(cmd).strip()

        payload_json = _jsonify_profile_value({
            "name": name,
            "cmd": cmd_str,
            "method": method,
        })

        conn = self._get_connection()
        try:
            _ensure_profile_schema(conn)

            cur = conn.execute(
                """
                INSERT INTO user_patterns (pattern_type, pattern_data, timestamp, ts)
                VALUES (?, ?, ?, ?)
                """,
                ("app_cmd", payload_json, now, now),
            )

            row = conn.execute(
                "SELECT id, COALESCE(count, 0) FROM habits WHERE COALESCE(name, '') = ?",
                (name,),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE habits
                    SET count = ?, timestamp = ?, ts = ?, command = ?, cmd = ?, method = ?, enabled = 1
                    WHERE id = ?
                    """,
                    (int(row[1] or 0) + 1, now, now, cmd_str, cmd_str, _norm_text(method), int(row[0])),
                )
            else:
                _insert_payload(conn, "habits", {
                    "name": name,
                    "cmd": cmd_str,
                    "method": _norm_text(method),
                    "count": 1,
                    "timestamp": now,
                    "ts": now,
                    "command": cmd_str,
                    "enabled": 1,
                })

            # No habit rule from a use: a rule is a time something runs (eli/planning/habits.detect_habits
            # through add_habit_rule). This made an enabled rule with no time for every app opened,
            # and the scheduler, reading the missing time as 00:00, opened it at midnight.

            _insert_payload(conn, "habit_events", {
                "event_type": "app_cmd",
                "event": "app_cmd",
                "details": payload_json,
                "data": payload_json,
                "timestamp": now,
                "ts": now,
                "name": name,
                "command": cmd_str,
                "cmd": cmd_str,
                "method": _norm_text(method),
            })

            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()


    # Transient, user-input and environment outcomes aren't actionable code bugs. The failures
    # table feeds self-improvement and the "recurring errors" surface, which exist to find code to
    # fix. Recording "a job/file/app that doesn't exist", "network is off" or STT garble made noise
    # like "No background job #999999 (x21)" dominate it.
    _NON_ACTIONABLE_FAILURE_PATTERNS = (
        "no background job #", "no media player", "no player could handle",
        "no players found", "network access is off", "can't search the web",
        "cannot search the web", "path not found", "file not found",
        "no location match", "missing_location", "missing path", "missing folder",
        "is not installed", "could not open", "could not close",
        # Empty/malformed user commands — validation outcomes, not code bugs.
        "missing topic", "missing app name", "specify app", "unsupported executor action",
        "couldn't tell which voice", "give the voice a name", "missing required",
        "validation failed", "empty args", "no topic", "no app name",
    )

    # How many of the wordings that hit one fault a failure row keeps beside its count, to show it by.
    FAILURE_EXAMPLES = 5

    @staticmethod
    def failure_signature(error: str, action: str = "", said: str = "") -> str:
        """The fault a failure is, whatever the request that hit it: the action with the error's own
        template, its paths, addresses, quoted values and numbers taken out ("cannot open '/tmp/a.txt'"
        and "cannot open '/home/x/b.pdf'" are one fault). With no error text, the request is all there is."""
        t = str(error or "").lower() or str(said or "").lower()
        t = re.sub(r"(['\"`]).*?\1", "<value>", t)
        t = re.sub(r"(?:[a-z]:)?(?:[\\/][\w.\-~]+)+", "<path>", t)
        t = re.sub(r"0x[0-9a-f]+", "<addr>", t)
        t = re.sub(r"\d+(?:\.\d+)?", "<n>", t)
        t = " ".join(t.split())
        return hashlib.sha1(f"{str(action or '').upper()}|{t}".encode("utf-8", "ignore")).hexdigest()

    def log_failure(self, user_input, error="", confidence=0.0, context=None, details=None, source="manual", **kwargs):
        """One row per fault (failure_signature: the action and the error's template), counted each time
        it recurs, with the last few requests that hit it as examples. It was keyed by the exact pair
        (the user's sentence, the error text), so one code fault hit by differently worded requests
        stayed a pile of single rows no detector ever saw recur."""
        user_input = str(user_input or "").strip()
        error_text = str(error or kwargs.get("error") or "").strip()
        # Skip non-actionable transient/user errors so they don't pollute the
        # self-improve / proactive failure surface.
        _low_err = error_text.lower()
        if any(p in _low_err for p in self._NON_ACTIONABLE_FAILURE_PATTERNS):
            return None
        ctx = context if context is not None else kwargs.get("context")
        ctx_text = _jsonify_contract_value(ctx)
        details_text = _jsonify_contract_value(details if details not in (None, "") else ctx)
        source = str(source or kwargs.get("source") or "manual").strip() or "manual"
        now = _now_ts()
        action = str(kwargs.get("action") or (ctx.get("action") if isinstance(ctx, dict) else "") or "").strip()
        signature = self.failure_signature(error_text, action, user_input)
        # the failing action is the command; the request that hit it is an example
        command = action.upper() or user_input

        conn = self._get_connection()
        try:
            _ensure_contract_schema(conn)
            _add_column_if_missing(conn, "failures", "examples", "TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_failures_signature ON failures(signature)")

            row = conn.execute(
                "SELECT id, COALESCE(occurrence_count, 0), COALESCE(examples, '[]') FROM failures "
                "WHERE signature = ? ORDER BY id LIMIT 1",
                (signature,),
            ).fetchone()

            if row:
                row_id = int(row[0])
                occ = int(row[1] or 0) + 1
                try:
                    examples = [e for e in json.loads(row[2] or "[]") if isinstance(e, str)]
                except ValueError:
                    examples = []
                examples = ([e for e in examples if e != user_input] + [user_input])[-self.FAILURE_EXAMPLES:]
                conn.execute(
                    """
                    UPDATE failures
                    SET timestamp = ?, ts = ?, last_seen = ?, occurrence_count = ?, count = ?,
                        confidence = ?, low_confidence = ?, context = ?, context_json = ?,
                        details = ?, description = ?, source = ?, user_input = ?, examples = ?,
                        status = COALESCE(status, 'open')
                    WHERE id = ?
                    """,
                    (
                        now, now, now, occ, occ,
                        float(confidence or 0.0), 1 if float(confidence or 0.0) < 0.5 else 0,
                        ctx_text, ctx_text,
                        details_text, details_text or error_text or user_input,
                        source, user_input, json.dumps(examples),
                        row_id,
                    ),
                )
                conn.commit()
                return row_id

            cur = conn.execute(
                """
                INSERT INTO failures (
                    ts, timestamp, user_input, command, error, confidence, low_confidence,
                    context, context_json, occurrence_count, first_seen, last_seen,
                    failure, name, text, content, details, description, source,
                    signature, status, count, examples
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now, now, user_input, command, error_text, float(confidence or 0.0),
                    1 if float(confidence or 0.0) < 0.5 else 0,
                    ctx_text, ctx_text, 1, now, now,
                    error_text or user_input, command, details_text or error_text or user_input,
                    details_text or error_text or user_input,
                    details_text, details_text or error_text or user_input,
                    source, signature, "open", 1, json.dumps([user_input] if user_input else []),
                ),
            )
            conn.commit()
            try:
                from eli.runtime.evidence_ledger import record_event as _eli_record_event
                _eli_record_event(
                    "failure",
                    source=source,
                    action="",
                    subject=user_input,
                    content=error_text,
                    payload={"context": ctx, "details": details, "signature": signature},
                    severity="error",
                    outcome="failed",
                    confidence=float(confidence or 0.0),
                    reusable=True,
                    db_path=self.db_path,
                    timestamp=now,
                )
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            return int(cur.lastrowid)
        finally:
            conn.close()

    def log_correction(self, original: str, corrected: str):
        now = time.time()
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO corrections (original, corrected, timestamp, ts) VALUES (?, ?, ?, ?)",
                (original, corrected, now, now),
            )
            conn.commit()
        finally:
            conn.close()


    def add_observation(self, *args, **kwargs):
        if not args and not kwargs:
            raise TypeError("add_observation requires at least one positional argument")

        # Support both positional (category, observation) and kwargs-only
        # (category=, observation=, source=) calling conventions.
        # awareness_boot uses kwargs-only; older call sites use positional.
        first = _norm_text(args[0]).strip() if args else ""
        second = _norm_text(args[1]).strip() if len(args) > 1 else _norm_text(kwargs.pop("observation", "")).strip()

        named_content = kwargs.pop("content", None)
        named_source = kwargs.pop("source", None)
        named_category = kwargs.pop("category", None)

        source = _norm_text(named_source).strip() or first or "runtime"
        category = _norm_text(named_category).strip() or first or "general"
        observation = second
        content_value = _norm_text(named_content).strip() if named_content is not None else (second or "")

        now = _now_ts()
        from eli.memory import policy as _policy
        conn = self._get_connection()
        try:
            payload = {
                "source": source,
                "category": category,
                "observation": observation,
                "content": content_value,
                "text": content_value or observation,
                "details": content_value or observation,
                "timestamp": now,
                "ts": now,
                "user_id": _policy.owner_key(),
            }
            rid = _insert_payload(conn, "observations", payload)
            conn.commit()
            _prune_observations(conn, category)
            return rid
        finally:
            conn.close()


    def get_dashboard_counts(self):
        conn = self._get_connection()
        try:
            by_kind = {}
            raw_memories = _eli_count_table(conn, "memories")
            processed_memories = 0
            if _has_table(conn, "memories"):
                rows = conn.execute(
                    """
                    SELECT COALESCE(kind, 'note') AS kind, COUNT(*)
                    FROM memories
                    GROUP BY COALESCE(kind, 'note')
                    ORDER BY COUNT(*) DESC, kind ASC
                    """
                ).fetchall()
                by_kind = {str(r[0] or "note"): int(r[1]) for r in rows}
                row = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM memories
                    WHERE TRIM(COALESCE(text, '')) <> ''
                      AND LOWER(COALESCE(kind, 'note')) <> 'chat'
                    """
                ).fetchone()
                processed_memories = int((row[0] if row else 0) or 0)
            semantic_count = 0
            if _has_table(conn, "semantic"):
                try: semantic_count = conn.execute("SELECT COUNT(*) FROM semantic").fetchone()[0] or 0
                except Exception: pass

            return {
                "processed_memories": processed_memories + semantic_count,
                "semantic_facts": semantic_count,
                "raw_memories": raw_memories,
                "total_memories": raw_memories,
                "conversation_turns": _eli_count_table(conn, "conversation_turns"),
                "conversations": _eli_count_table(conn, "conversations"),
                "habit_rules": _eli_count_table(conn, "habit_rules"),
                "habit_events": _eli_count_table(conn, "habit_events"),
                "observations": _eli_count_table(conn, "observations"),
                "failures": _eli_count_table(conn, "failures"),
                "improvements": _eli_count_table(conn, "improvements"),
                "session_summaries": _eli_count_table(conn, "session_summaries"),
                "by_kind": by_kind,
                "by_type": by_kind,
                "generated_at": time.time(),
            }
        finally:
            conn.close()

    def get_pending_proposals(self, limit=25, **kwargs):
        limit = int(limit or kwargs.get("n") or 25)
        conn = self._get_connection()
        try:
            rows = conn.execute(
                "SELECT id, proposed_name, capability, description, examples, plugin_code, reasoning, status, COALESCE(timestamp, created_at, ts, id) "
                "FROM capability_proposals "
                "WHERE COALESCE(status, 'pending') = 'pending' "
                "ORDER BY COALESCE(timestamp, created_at, ts, id) DESC LIMIT ?",
                (limit,),
            ).fetchall()
            out = []
            for r in rows:
                ex = []
                if r[4]:
                    try:
                        ex = json.loads(r[4])
                    except Exception:
                        ex = [r[4]]
                out.append({
                    "id": r[0],
                    "proposed_name": r[1],
                    "capability": r[2],
                    "description": r[3],
                    "examples": ex,
                    "plugin_code": r[5],
                    "reasoning": r[6],
                    "status": r[7],
                    "timestamp": r[8],
                })
            return out
        finally:
            conn.close()

    def get_recent_conversation(self, limit=20, session_id=None, user_id=None,
                                since=None, until=None, role=None):
        """The last `limit` turns, oldest first (limit None: every turn in the range). The turn's own
        user's when no user is named; one conversation's when session_id is given."""
        unbounded = limit is None
        limit = int(limit) if limit else 20
        conn = self._get_connection()
        try:
            # Fetch the most-recent `limit` rows (DESC), then reverse so the
            # caller receives them in chronological (oldest-first) order.
            sql = "SELECT COALESCE(timestamp, ts, 0), session_id, user_id, role, content FROM conversation_turns"
            where = []
            params = []
            if session_id:
                where.append("session_id = ?")
                params.append(session_id)
            if user_id:
                where.append("user_id = ?")
                params.append(user_id)
            else:
                # With no user named, the turn's own user's turns (eli/memory/policy.owner_key): an API
                # member's turns never reach the owner's readers (the anti-repeat guard, the output
                # governor, follow-ups), nor the owner's a member's.
                from eli.memory import policy as _scope_policy
                _scope, _scope_params = _scope_policy.scope_sql("user_id", _scope_policy.owner_key(None), raw_ids=True)
                if _scope:
                    where.append(_scope.replace(" AND ", "", 1))
                    params.extend(_scope_params)
            if role:
                where.append("role = ?")
                params.append(role)
            if since:
                where.append("COALESCE(timestamp, ts, 0) >= ?")
                params.append(float(since))
            if until:
                where.append("COALESCE(timestamp, ts, 0) < ?")
                params.append(float(until))
            # Exclude fragment-guard NOOP entries — they are internal routing
            # events that were stored before the NOOP-suppression fix.
            # Including them in LLM context causes the model to mimic JSON format.
            for junk in _NOT_A_TURN_LIKE:
                where.append("content NOT LIKE ?")
                params.append(junk)
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY COALESCE(timestamp, ts, 0) DESC"
            if not unbounded:
                sql += " LIMIT ?"
                params.append(int(limit))
            rows = conn.execute(sql, params).fetchall()
            # Reverse to chronological order for the synthesiser
            rows = list(reversed(rows))
            out = []
            for r in rows:
                role, content = r[3], r[4]
                # Defence in depth: never feed internal error/sentinel assistant turns ("GGUF unavailable...",
                # "[ELI] Model not ready...") into recent-conversation context or the model parrots them.
                # Mirrors the storage gate so stale pre-gate rows are excluded too.
                if role == "assistant" and (
                    (callable(_eli_should_store_conversation_turn)
                     and not _eli_should_store_conversation_turn("assistant", content or ""))
                    or (callable(_eli_is_internal_report_dump)
                        and _eli_is_internal_report_dump(content or ""))
                ):
                    continue
                out.append({
                    "timestamp": r[0],
                    "session_id": r[1],
                    "user_id": r[2],
                    "role": role,
                    "content": content,
                })
            return out
        finally:
            conn.close()

    def get_recent_failures(self, limit=20):
        conn = self._get_connection()
        try:
            if not _has_table(conn, "failures"):
                return []
            rows = conn.execute(
                """
                SELECT
                    COALESCE(user_input, '') AS user_input,
                    COALESCE(error, '') AS error,
                    COALESCE(command, '') AS command,
                    COALESCE(occurrence_count, 1) AS occurrence_count,
                    COALESCE(timestamp, ts, id) AS sort_ts
                FROM failures
                WHERE COALESCE(status, 'open') NOT IN ('resolved', 'closed')
                ORDER BY sort_ts DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [{"user_input": r[0], "error": r[1], "command": r[2], "occurrence_count": r[3], "timestamp": r[4]} for r in rows]
        finally:
            conn.close()

    def mark_failure_resolved(self, *, error_like: str = None, id: int = None) -> int:
        """Mark logged failures resolved (preserved for history, hidden from the
        live Self-Improve panel). Match by exact id or by an error LIKE pattern.
        Returns the number of rows updated."""
        conn = self._get_connection()
        try:
            if not _has_table(conn, "failures"):
                return 0
            if id is not None:
                cur = conn.execute("UPDATE failures SET status='resolved' WHERE id=?", (int(id),))
            elif error_like:
                cur = conn.execute(
                    "UPDATE failures SET status='resolved' "
                    "WHERE COALESCE(status,'open') NOT IN ('resolved','closed') "
                    "AND (error LIKE ? OR user_input LIKE ? OR command LIKE ?)",
                    (error_like, error_like, error_like),
                )
            else:
                return 0
            conn.commit()
            return int(cur.rowcount or 0)
        finally:
            conn.close()

    def get_recent_improvements(self, limit=20, open_only: bool = False):
        """The newest improvement proposals; with open_only, only those still open (not applied, rejected
        or closed as no longer found)."""
        conn = self._get_connection()
        try:
            if not _has_table(conn, "improvements"):
                return []
            rows = conn.execute(
                f"""
                SELECT
                    COALESCE(category, area, 'runtime') AS category,
                    COALESCE(area, category, 'runtime') AS area,
                    COALESCE(description, suggestion, '') AS description,
                    COALESCE(status, 'pending') AS status,
                    COALESCE(timestamp, created_at, ts, id) AS sort_ts
                FROM improvements
                {"WHERE COALESCE(status, 'pending') IN ('pending', 'open')" if open_only else ""}
                ORDER BY sort_ts DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
            return [{"category": r[0], "area": r[1], "description": r[2], "status": r[3], "timestamp": r[4]} for r in rows]
        finally:
            conn.close()

    def get_recent_observations(self, limit=20, category=None):
        """The turn's user's newest observations, or only those of `category` (one name or several):
        a trend is compared like with like, not against the newest rows of any kind."""
        conn = self._get_connection()
        try:
            if not _has_table(conn, "observations"):
                return []
            cols = _table_columns(conn, "observations")
            category_expr = "category" if "category" in cols else ("source" if "source" in cols else "'system'")
            obs_expr = "observation" if "observation" in cols else ("content" if "content" in cols else ("text" if "text" in cols else "''"))
            content_expr = "content" if "content" in cols else ("observation" if "observation" in cols else ("text" if "text" in cols else "''"))
            source_expr = "source" if "source" in cols else "'runtime'"
            time_expr = "COALESCE(timestamp, ts, id)"
            kinds = [category] if isinstance(category, str) else [str(c) for c in (category or ())]
            only = f" AND {category_expr} IN ({','.join('?' * len(kinds))})" if kinds else ""
            mine, mine_params = _owned_scope(conn, "observations")
            rows = conn.execute(
                f"""
                SELECT {category_expr}, {obs_expr}, {content_expr}, {source_expr}, {time_expr}
                FROM observations
                WHERE 1=1{only}{mine}
                ORDER BY {time_expr} DESC
                LIMIT ?
                """,
                (*kinds, *mine_params, int(limit)),
            ).fetchall()
            return [
                {
                    "category": r[0],
                    "observation": r[1],
                    "content": r[2],
                    "source": r[3],
                    "timestamp": r[4],
                }
                for r in rows
            ]
        finally:
            conn.close()

    def get_recent_processed_memories(self, limit=20):
        conn = self._get_connection()
        try:
            if not _has_table(conn, "memories"):
                return []
            rows = conn.execute(
                """
                SELECT
                    id,
                    COALESCE(timestamp, ts, id) AS sort_ts,
                    COALESCE(kind, 'note') AS kind,
                    COALESCE(text, '') AS text,
                    COALESCE(tags, '') AS tags,
                    COALESCE(source, '') AS source
                FROM memories
                ORDER BY sort_ts DESC
                LIMIT ?
                """,
                (max(int(limit) * 6, 120),),
            ).fetchall()
            out = []
            for r in rows:
                text = str(r[3] or "").strip()
                kind = str(r[2] or "note")
                tags = _eli_format_tags(r[4])
                low = text.lower()
                if not text:
                    continue
                if kind.lower() == "chat":
                    continue
                if "conversation" in tags.lower():
                    continue
                if low.startswith("q:") and "\na:" in low:
                    continue
                out.append({
                    "id": r[0],
                    "timestamp": r[1],
                    "kind": kind,
                    "text": text,
                    "tags": tags,
                    "source": str(r[5] or ""),
                })
                if len(out) >= int(limit):
                    break
            return out
        finally:
            conn.close()

    def log_improvement(self, category, description="", area="runtime", priority=1, status="pending", code_before="", code_after="", details=None, **kwargs):
        category = str(category or "").strip()
        description = str(description or "").strip()
        area = str(area or "runtime").strip() or "runtime"
        details_text = _jsonify_contract_value(details) if details not in (None, "") else ""
        body = description or details_text or category or "improvement"
        now = _now_ts()

        conn = self._get_connection()
        try:
            _ensure_contract_schema(conn)

            row = conn.execute(
                "SELECT id FROM improvements WHERE COALESCE(category,'') = ? AND COALESCE(area,'') = ? AND COALESCE(description,'') = ?",
                (category, area, body),
            ).fetchone()

            if row:
                # found again: seen once more, and open again if it had been closed as no longer found
                row_id = int(row[0])
                conn.execute(
                    "UPDATE improvements SET timestamp = ?, ts = ?, priority = ?, status = ?, details = ?, "
                    "text = ?, content = ?, improvement = ?, suggestion = ?, source = COALESCE(source, ?), "
                    "count = COALESCE(count, 1) + 1 WHERE id = ?",
                    (
                        now, now, int(priority or 1), str(status or "pending"), details_text,
                        body, body, body, body, str(kwargs.get("source") or "manual"),
                        row_id,
                    ),
                )
                conn.commit()
                return row_id

            cur = conn.execute(
                """
                INSERT INTO improvements (
                    ts, timestamp, category, area, description, priority, status, created_at,
                    title, name, improvement, text, content, details, source, count, suggestion, applied
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now, now, category, area, body, int(priority or 1), str(status or "pending"), now,
                    category, category, body, body, body, details_text,
                    str(kwargs.get("source") or "manual"), 1, body, 0,
                ),
            )
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def close_improvements(self, category: str, still_found, *, area: Optional[str] = None,
                           status: str = "obsolete") -> int:
        """A rescan of `category` (and `area`) found only `still_found` (descriptions): every open item of
        it that was not found again is closed with `status`. Proposals were only ever written 'pending',
        so the store held every suggestion ever made, fixed or not, and the prompt counted them all.
        How many were closed."""
        keep = {str(d or "").strip() for d in (still_found or ())}
        conn = self._get_connection()
        try:
            _ensure_contract_schema(conn)
            where = "COALESCE(category, '') = ? AND COALESCE(status, 'pending') IN ('pending', 'open')"
            args: list = [str(category or "")]
            if area is not None:
                where += " AND COALESCE(area, '') = ?"
                args.append(str(area))
            rows = conn.execute(f"SELECT id, COALESCE(description, '') FROM improvements WHERE {where}", args).fetchall()
            gone = [int(r[0]) for r in rows if str(r[1]).strip() not in keep]
            if gone:
                conn.executemany("UPDATE improvements SET status = ? WHERE id = ?", [(status, i) for i in gone])
                conn.commit()
            return len(gone)
        finally:
            conn.close()

    def close_improvement(self, description: str, *, status: str = "applied") -> int:
        """The open improvement of this description is done with (a fix made for it adopted): closed
        with `status`. close_improvements closes what a rescan did not find again; this closes the one
        named. How many were closed."""
        conn = self._get_connection()
        try:
            _ensure_contract_schema(conn)
            cur = conn.execute("UPDATE improvements SET status = ? WHERE COALESCE(description, '') = ? "
                               "AND COALESCE(status, 'pending') IN ('pending', 'open')",
                               (str(status), str(description or "").strip()))
            conn.commit()
            return int(cur.rowcount or 0)
        finally:
            conn.close()

    def save_session_summary(self, session_id, user_id=None, summary="", turns_count=0, started_at=None, ended_at=None, source="gui.autosave"):
        now = _now_ts()
        started_at = now if started_at is None else float(started_at)
        ended_at = now if ended_at is None else float(ended_at)
        conn = self._get_connection()
        try:
            payload = {
                "session_id": session_id,
                "user_id": user_id,
                "summary": summary,
                "turns_count": int(turns_count or 0),
                "started_at": started_at,
                "ended_at": ended_at,
                "source": source,
                "timestamp": now,
                "ts": now,
            }
            rid = _insert_payload(conn, "session_summaries", payload)
            conn.commit()
            return rid
        finally:
            conn.close()


# Persistence-gate + world-model-sync helpers.
# Lazily import to keep the module loadable when these are missing.
try:
    from eli.runtime.persistence_gate import (
        should_store_conversation_turn as _eli_should_store_conversation_turn,
        should_store_memory_text as _eli_should_store_memory_text,
        is_internal_report_dump as _eli_is_internal_report_dump,
    )
except Exception:
    _eli_should_store_conversation_turn = None
    _eli_should_store_memory_text = None
    _eli_is_internal_report_dump = None

# Explicit FAISS persistence helper. vs.flush() should persist the index but has been unreliable, so
# this writes faiss.write_index() to the canonical artifacts/vectors/ paths so post-rebuild state
# survives a restart.
def rebuild_vector_index_from_search_db() -> Dict[str, Any]:
    """Re-embed every memory the storage policy indexes, through the vector store's own rebuild
    (VectorStore.rebuild_from_db)."""
    from eli.memory.vector_store import get_vector_store
    vs = get_vector_store()
    if vs is None:
        return {"ok": False, "indexed": 0, "source_count": 0, "error": "vector_store_unavailable"}
    result = vs.rebuild_from_db()
    result["skipped"] = max(0, int(result.get("source_count", 0)) - int(result.get("indexed", 0)))
    return result

# Persistence behavior is implemented directly in Memory.store_memory,
# Memory.add_conversation_turn, and VectorStore.rebuild_from_db.

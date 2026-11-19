"""Incremental per-capability state — what is actually known about each
action right now, built up one fact at a time instead of wiped and rebuilt
on every capability_manifest.json regen.

update_capability_manifest() (capability_updater.py) rebuilds its 9-field
list from a fresh AST scan every call — anything not re-derivable from that
scan + evidence_ledger was lost on every regen, which is why `active` has
always been a hardcoded True with no real detection: there was nowhere to
persist a real answer between regens. This module is that place. One row
per action, upserted whenever something new is actually known — never
wiped wholesale, unlike the manifest it feeds.

Deliberately NOT hash-chained like evidence_ledger/orchestrator_audit_ledger
— those are append-only event histories; this is current, mutable state
about each action, closer to user_model's one-row-per-subject shape.
"""
from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)


def _default_db_path() -> Path:
    from eli.core.paths import capability_state_db_path
    return capability_state_db_path()


def _connect(db_path: Optional[str | Path] = None) -> sqlite3.Connection:
    path = Path(db_path).expanduser().resolve() if db_path else _default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0)
    from eli.core.sqlite_util import apply_pragmas
    apply_pragmas(conn, db_path=str(path), synchronous="NORMAL")
    ensure_schema(conn)
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS capability_state (
            action TEXT PRIMARY KEY,
            discovered_at REAL,
            dependency_ready INTEGER,
            permission_state TEXT,
            reachable INTEGER,
            device_available INTEGER,
            last_probe_at REAL,
            last_verified_success REAL,
            reliability REAL,
            calibrated_success REAL,
            evidence_state TEXT,
            updated_at REAL
        )
        """
    )


_FIELDS = (
    "discovered_at", "dependency_ready", "permission_state", "reachable",
    "device_available", "last_probe_at", "last_verified_success",
    "reliability", "calibrated_success", "evidence_state", "updated_at",
)
_BOOL_FIELDS = ("dependency_ready", "reachable", "device_available")
_COLUMNS = ("action",) + _FIELDS


def _row(row) -> Dict[str, Any]:
    out = dict(zip(_COLUMNS, row))
    for b in _BOOL_FIELDS:
        if out[b] is not None:
            out[b] = bool(out[b])
    return out


def get_state(action: str, *, db_path: Optional[str | Path] = None) -> Optional[Dict[str, Any]]:
    """The current known state for one action, or None if nothing has ever
    been recorded about it. Missing is not false — callers must not treat
    None the same as a confirmed-unavailable capability."""
    action = str(action or "").strip().upper()
    conn = _connect(db_path)
    try:
        row = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM capability_state WHERE action = ?",
            (action,),
        ).fetchone()
    finally:
        conn.close()
    return _row(row) if row else None


def all_states(*, db_path: Optional[str | Path] = None) -> Dict[str, Dict[str, Any]]:
    """Every action's known state, in one read."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM capability_state").fetchall()
    finally:
        conn.close()
    return {r[0]: _row(r) for r in rows}


def touch_discovered(action: str, *, now: Optional[float] = None,
                     db_path: Optional[str | Path] = None) -> None:
    """Record the first time this action was seen, without touching anything
    else about it. A no-op if it has already been recorded once — this is
    meant to be called from capability_updater.py's own discovery loop, on
    every action, every run."""
    action = str(action or "").strip().upper()
    if not action:
        return
    now = float(now if now is not None else time.time())
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO capability_state (action, discovered_at, updated_at) "
            "VALUES (?, ?, ?) ON CONFLICT(action) DO NOTHING",
            (action, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_state(action: str, *, db_path: Optional[str | Path] = None,
                 now: Optional[float] = None, **fields: Any) -> None:
    """Merge the given fields into this action's row — fields the caller
    didn't pass are left exactly as they were, which is the whole point:
    this never wipes what it doesn't have fresh evidence for. Unknown keys
    raise rather than being silently accepted into nowhere."""
    bad = set(fields) - set(_FIELDS)
    if bad:
        raise ValueError(f"unknown capability_state field(s): {sorted(bad)}")
    action = str(action or "").strip().upper()
    if not action:
        return
    now = float(now if now is not None else time.time())
    conn = _connect(db_path)
    try:
        conn.execute(
            "INSERT INTO capability_state (action, updated_at) VALUES (?, ?) "
            "ON CONFLICT(action) DO NOTHING",
            (action, now),
        )
        for key, value in fields.items():
            if isinstance(value, bool):
                value = int(value)
            conn.execute(
                f"UPDATE capability_state SET {key} = ?, updated_at = ? WHERE action = ?",
                (value, now, action),
            )
        conn.commit()
    finally:
        conn.close()


# A failure that names something missing from this machine, not a mistake in the request: a module,
# a program, an extra. A missing file is left out: that is usually the user's file, not ELI's part.
_MISSING_PART_RE = re.compile(r"no module named|modulenotfounderror|\b(?:isn't|is not|not) installed\b|command not found",
                              re.I)


def observe_run(action: str, outcome: str, detail: str = "", *, db_path: Optional[str | Path] = None) -> None:
    """What a real run says about whether an action can run here. A success: it can. A failure naming a
    missing module or program: it can't, until what it needs is installed and it works again."""
    if outcome == "ok":
        upsert_state(action, db_path=db_path, dependency_ready=True)
    elif outcome == "failed" and _MISSING_PART_RE.search(str(detail or "")):
        upsert_state(action, db_path=db_path, dependency_ready=False)


__all__ = [
    "ensure_schema", "get_state", "all_states", "touch_discovered", "upsert_state", "observe_run",
]

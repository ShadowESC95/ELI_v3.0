"""Persistent repo index for the coding agent.

Replaces full-tree rglob scans with an incremental SQLite index keyed by
file mtime. Cross-platform: uses pathlib + mtime only — no OS-specific APIs.

Index location: ``<db_dir>/coding_repo_index.sqlite3`` (writable on all installs).
Scope defaults to ``project_root()/eli``; override with ``ELI_REPO_INDEX_ROOT``.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)

_DEF_TMPL = r"^\s*(?:async\s+)?def\s+{name}\b|^\s*class\s+{name}\b"
_IMPORT_RE = re.compile(r"\s*(?:import\s|from\s[\w.]+\simport\s)")

_lock = threading.Lock()
_instance: Optional["RepoIndex"] = None


def _index_db_path() -> Path:
    try:
        from eli.core.paths import db_dir
        return Path(db_dir()) / "coding_repo_index.sqlite3"
    except Exception:
        return Path(__file__).resolve().parents[2] / "artifacts" / "coding_repo_index.sqlite3"


def _index_root() -> Path:
    override = os.environ.get("ELI_REPO_INDEX_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    try:
        from eli.core.paths import project_root
        return Path(project_root()) / "eli"
    except Exception:
        return Path(__file__).resolve().parents[1]


def _project_root() -> Path:
    try:
        from eli.core.paths import project_root
        return Path(project_root())
    except Exception:
        return Path(__file__).resolve().parents[2]


class RepoIndex:
    def __init__(self, db_path: Path, root: Path):
        self.db_path = db_path
        self.root = root.resolve()
        self.project_root = _project_root().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self.db_path), timeout=10)
        con.row_factory = sqlite3.Row
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS files (
                    rel_path TEXT PRIMARY KEY,
                    mtime REAL NOT NULL,
                    size INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    imports TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS symbols (
                    name TEXT NOT NULL,
                    rel_path TEXT NOT NULL,
                    line INTEGER NOT NULL,
                    snippet TEXT NOT NULL,
                    PRIMARY KEY (name, rel_path, line)
                );
                CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
            """)

    def _rel(self, path: Path) -> str:
        return str(path.resolve().relative_to(self.project_root)).replace("\\", "/")

    def _file_hash(self, text: str) -> str:
        return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]

    def _imports_of(self, src: str, limit: int = 40) -> str:
        lines = [l for l in src.splitlines() if _IMPORT_RE.match(l)]
        return "\n".join(lines[:limit])

    def _index_file(self, py: Path) -> None:
        try:
            stat = py.stat()
            text = py.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return
        rel = self._rel(py)
        content_hash = self._file_hash(text)
        with self._connect() as con:
            row = con.execute(
                "SELECT mtime, content_hash FROM files WHERE rel_path = ?", (rel,)
            ).fetchone()
            if row and float(row["mtime"]) == stat.st_mtime and row["content_hash"] == content_hash:
                return
            imports = self._imports_of(text)
            con.execute(
                "INSERT OR REPLACE INTO files(rel_path, mtime, size, content_hash, imports) "
                "VALUES (?, ?, ?, ?, ?)",
                (rel, stat.st_mtime, stat.st_size, content_hash, imports),
            )
            con.execute("DELETE FROM symbols WHERE rel_path = ?", (rel,))
            lines = text.splitlines()
            for i, line in enumerate(lines):
                m = re.match(r"^\s*(?:async\s+)?def\s+(\w+)\b", line)
                if not m:
                    m = re.match(r"^\s*class\s+(\w+)\b", line)
                if m:
                    snippet = "\n".join(lines[i:i + 30])
                    con.execute(
                        "INSERT OR REPLACE INTO symbols(name, rel_path, line, snippet) "
                        "VALUES (?, ?, ?, ?)",
                        (m.group(1), rel, i + 1, snippet),
                    )

    def refresh(self, *, force: bool = False) -> Dict[str, int]:
        """Incremental refresh: index new/changed .py files under root."""
        stats = {"scanned": 0, "updated": 0}
        if not self.root.exists():
            return stats
        with _lock:
            before = time.time()
            for py in self.root.rglob("*.py"):
                if "__pycache__" in py.parts:
                    continue
                stats["scanned"] += 1
                try:
                    rel = self._rel(py)
                    with self._connect() as con:
                        row = con.execute(
                            "SELECT mtime FROM files WHERE rel_path = ?", (rel,)
                        ).fetchone()
                    if not force and row and float(row["mtime"]) >= py.stat().st_mtime:
                        continue
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
                self._index_file(py)
                stats["updated"] += 1
            with self._connect() as con:
                con.execute(
                    "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                    ("last_refresh", str(before)),
                )
        return stats

    def query_symbols(self, names: Iterable[str], *, def_lines: int = 30) -> Dict[str, str]:
        self.refresh()
        found: Dict[str, str] = {}
        names = [n for n in names if n]
        if not names:
            return found
        placeholders = ",".join("?" * len(names))
        with self._connect() as con:
            rows = con.execute(
                f"SELECT name, rel_path, line, snippet FROM symbols "
                f"WHERE name IN ({placeholders}) ORDER BY line",
                tuple(names),
            ).fetchall()
        for row in rows:
            name = row["name"]
            if name in found:
                continue
            snippet = row["snippet"]
            if def_lines and snippet:
                snippet = "\n".join(snippet.splitlines()[:def_lines])
            found[name] = f"# {row['rel_path']}\n{snippet}"
        return found

    def query_imports(self, rel_path: str) -> str:
        self.refresh()
        with self._connect() as con:
            row = con.execute(
                "SELECT imports FROM files WHERE rel_path = ?", (rel_path,)
            ).fetchone()
        return str(row["imports"] if row else "")

    def stats(self) -> Dict[str, int]:
        with self._connect() as con:
            files = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            symbols = con.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        return {"files": files, "symbols": symbols}


def get_repo_index() -> RepoIndex:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = RepoIndex(_index_db_path(), _index_root())
    return _instance


def refresh_index(*, force: bool = False) -> Dict[str, int]:
    return get_repo_index().refresh(force=force)

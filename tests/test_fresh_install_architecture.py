"""Fresh-install architecture: full stores, zero personal rows."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "config" / "templates" / "db"

EXPECTED_DBS = (
    "user.sqlite3",
    "agent.sqlite3",
    "system_index.sqlite3",
    "coding_memory.sqlite3",
)

# Content tables that must stay empty on a blank slate (schema-only install).
PERSONAL_TABLES = (
    ("user.sqlite3", "memories"),
    ("user.sqlite3", "conversation_turns"),
    ("user.sqlite3", "conversations"),
    ("user.sqlite3", "user_patterns"),
    ("user.sqlite3", "observations"),
    ("user.sqlite3", "session_summaries"),
    ("user.sqlite3", "learning_replay"),
    ("agent.sqlite3", "agent_dispatches"),
)

# Architecture tables that must exist after init (subset — full list is larger).
REQUIRED_TABLES = (
    ("user.sqlite3", "memories"),
    ("user.sqlite3", "memories_fts"),
    ("user.sqlite3", "kg_entities"),
    ("user.sqlite3", "kg_relations"),
    ("user.sqlite3", "news_articles"),
    ("user.sqlite3", "runtime_events"),
    ("system_index.sqlite3", "executables"),
    ("coding_memory.sqlite3", "coding_bug_fixes"),
    ("agent.sqlite3", "agent_dispatches"),
)


def _table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {str(r[0]) for r in rows}


def _count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)
    except sqlite3.Error:
        return -1


@pytest.fixture()
def fresh_install_env(tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    db_dir = artifacts / "db"
    db_dir.mkdir(parents=True)
    install_root = tmp_path / "ELI_v3"
    install_root.mkdir()
    # Mirror frozen layout: templates live beside eli/ under project root.
    template_dest = install_root / "config" / "templates" / "db"
    template_dest.mkdir(parents=True)
    for src in TEMPLATES.glob("*.sqlite3"):
        import shutil

        shutil.copy2(src, template_dest / src.name)

    monkeypatch.setenv("ELI_PROJECT_ROOT", str(install_root))
    monkeypatch.setenv("ELI_DATA_DIR", str(artifacts))
    monkeypatch.setenv("ELI_DB_DIR", str(db_dir))
    monkeypatch.setenv("ELI_USER_DB", str(db_dir / "user.sqlite3"))
    monkeypatch.setenv("ELI_AGENT_DB", str(db_dir / "agent.sqlite3"))

    from eli.core import paths as cp

    for name in ("data_dir", "config_dir", "user_db_path", "agent_db_path", "project_root"):
        fn = getattr(cp, name, None)
        if fn is not None and hasattr(fn, "cache_clear"):
            fn.cache_clear()

    yield db_dir


def test_init_all_data_builds_full_architecture_blank_slate(fresh_install_env):
    from eli.core.init_data import init_all_data

    results = init_all_data()
    failed = [name for name, ok, _ in results if not ok]
    assert not failed, f"init steps failed: {failed}"

    db_dir: Path = fresh_install_env
    for name in EXPECTED_DBS:
        assert (db_dir / name).exists(), f"missing store: {name}"

    for db_name, table in REQUIRED_TABLES:
        db_path = db_dir / db_name
        assert table in _table_names(db_path), f"{db_name} missing table {table}"

    for db_name, table in PERSONAL_TABLES:
        db_path = db_dir / db_name
        with sqlite3.connect(db_path) as conn:
            assert _count(conn, table) == 0, f"{db_name}.{table} must be empty on fresh install"


def test_git_template_dbs_exist_and_are_schema_only():
    assert TEMPLATES.is_dir(), "config/templates/db must ship in git"
    names = {p.name for p in TEMPLATES.glob("*.sqlite3")}
    assert set(EXPECTED_DBS) <= names

    for db_name, table in PERSONAL_TABLES:
        db_path = TEMPLATES / db_name
        with sqlite3.connect(db_path) as conn:
            if table not in _table_names(db_path):
                pytest.skip(f"template {db_name} predates table {table}")
            assert _count(conn, table) == 0, (
                f"template {db_name}.{table} must not ship personal rows"
            )


def test_frozen_data_and_config_dirs_use_eli_v3(tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("ELI_DATA_DIR", raising=False)
    monkeypatch.delenv("ELI_CONFIG_DIR", raising=False)
    monkeypatch.delenv("ELI_PROJECT_ROOT", raising=False)

    from eli.core import paths

    paths.data_dir.cache_clear()
    paths.config_dir.cache_clear()

    root = paths.project_root()
    assert root.name == "ELI_v3"
    assert paths.data_dir() == root / "artifacts"
    assert paths.config_dir() == root / "config"
    assert paths.persona_auto_path() == root / "config" / "persona.auto.txt"


def test_frozen_project_root_rejects_read_only_explicit_env(tmp_path, monkeypatch):
    import os
    import sys

    read_only = tmp_path / "readonly_mount"
    read_only.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("ELI_PROJECT_ROOT", str(read_only))
    # Importing paths in this (unfrozen) test process pinned ELI_DATA_DIR to the checkout; a frozen
    # launch gets it from the runtime hook instead.
    monkeypatch.delenv("ELI_DATA_DIR", raising=False)
    os.chmod(read_only, 0o555)

    from eli.core import paths

    paths.data_dir.cache_clear()

    resolved = paths.project_root()
    assert resolved.name == "ELI_v3"
    assert paths.data_dir().is_relative_to(resolved)


def test_capability_sync_state_dir_is_writable_when_frozen(tmp_path, monkeypatch):
    import sys

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("ELI_DATA_DIR", str(tmp_path / "artifacts"))

    from eli.core import paths
    from eli.runtime.capability_sync import CapabilitySync

    paths.data_dir.cache_clear()
    sync = CapabilitySync(repo_root=tmp_path / "bundle")
    state = sync._state_dir()
    assert state == tmp_path / "artifacts" / "runtime"
    state.mkdir(parents=True, exist_ok=True)
    probe = state / ".write_probe"
    probe.write_text("ok", encoding="utf-8")
    assert probe.read_text(encoding="utf-8") == "ok"


def test_memory_schema_adds_value_column_on_legacy_db(tmp_path):
    import sqlite3

    db = tmp_path / "user.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            text TEXT,
            content TEXT
        )
        """
    )
    conn.commit()

    from eli.memory.memory import _ensure_memory_schema

    _ensure_memory_schema(conn)
    cols = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
    assert "value" in cols

    conn.execute(
        "INSERT INTO memories (ts, text, value) VALUES (?, ?, ?)",
        (1.0, "hello", "world"),
    )
    conn.commit()
    row = conn.execute("SELECT value FROM memories WHERE id=1").fetchone()
    assert row[0] == "world"
    conn.close()


def test_rthook_safe_copy_reads_from_readonly_source(tmp_path):
    import importlib.util

    src_root = tmp_path / "bundle"
    dst_root = tmp_path / "user"
    src_file = src_root / "tts_piper" / "piper" / "voice.onnx"
    src_file.parent.mkdir(parents=True)
    src_file.write_bytes(b"onnx-bytes")

    hook_path = Path(__file__).resolve().parents[1] / "packaging" / "pyinstaller" / "rthook_eli_frozen_paths.py"
    spec = importlib.util.spec_from_file_location("rthook_eli_frozen_paths", hook_path)
    hook = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(hook)

    hook._safe_copy_file(src_file, dst_root / "tts_piper" / "piper" / "voice.onnx")
    copied = dst_root / "tts_piper" / "piper" / "voice.onnx"
    assert copied.read_bytes() == b"onnx-bytes"


def test_frozen_build_keeps_the_runtime_hooks_env(tmp_path, monkeypatch):
    """The bundle ships pyproject.toml, so ensure_install_env() found the read-only bundle as
    "the checkout" and re-pointed ELI_PROJECT_ROOT/ELI_DATA_DIR into it, moving user data out of
    the per-user root that rthook_eli_frozen_paths.py had set."""
    import os
    import sys

    user_root = tmp_path / "ELI_v3"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("ELI_PROJECT_ROOT", str(user_root))
    monkeypatch.setenv("ELI_DATA_DIR", str(user_root / "artifacts"))

    from eli.core import paths

    paths.ensure_install_env()
    assert os.environ["ELI_PROJECT_ROOT"] == str(user_root)
    assert os.environ["ELI_DATA_DIR"] == str(user_root / "artifacts")

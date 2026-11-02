"""Regression locks for AppImage/portable install failures (v2.3.94).

Failures observed on fresh Linux portable/AppImage installs (low-RAM laptops):
  - read-only writes under AppImage mount
  - DB at ~/.local/share/eli/ instead of ELI_v3
  - memories.value column missing on upgraded DBs
  - capability snapshot written into the bundle
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def test_frozen_data_and_config_dirs_use_eli_v3(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("ELI_DATA_DIR", raising=False)
    monkeypatch.delenv("ELI_CONFIG_DIR", raising=False)
    monkeypatch.delenv("ELI_PROJECT_ROOT", raising=False)
    # the per-path overrides the test run sets would hide the frozen layout
    monkeypatch.delenv("ELI_PERSONA_AUTO_PATH", raising=False)
    monkeypatch.delenv("ELI_NOTEBOOK_DIR", raising=False)

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

    read_only = tmp_path / "readonly_mount"
    read_only.mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("ELI_PROJECT_ROOT", str(read_only))
    monkeypatch.delenv("ELI_DATA_DIR", raising=False)   # ensure_install_env() pins it at import
    os.chmod(read_only, 0o555)

    from eli.core import paths

    paths.data_dir.cache_clear()

    resolved = paths.project_root()
    assert resolved.name == "ELI_v3"
    assert paths.data_dir().is_relative_to(resolved)


def test_capability_sync_state_dir_is_writable_when_frozen(tmp_path, monkeypatch):
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


# A frozen build copies its eli/ source into the user's data folder and runs it from there. The copy
# was keyed on the version alone, so a second build under the same number (a dry run, a release
# rebuilt under its tag) found the first one's copy and ran the old code.
ROOT = Path(__file__).resolve().parents[1]


def test_the_build_records_its_source():
    spec = (ROOT / "ELI.spec").read_text(encoding="utf-8")
    assert "build_stamp.txt" in spec and '"rev-parse", "HEAD"' in spec


# ---- v3's folders are v3's alone ----------------------------------------------------------------

def test_installed_v3_uses_its_own_folders_not_generic_eli(tmp_path, monkeypatch):
    """An installed (not packaged, not a git checkout) v3 keeps everything under the same ELI_v3 root
    as the packaged app. It used to use ~/.local/share/eli, ~/.config/eli and ~/.cache/eli, which
    other ELI versions use too."""
    from eli.core import paths
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    for k in ("ELI_DATA_DIR", "ELI_CONFIG_DIR", "ELI_CACHE_DIR", "ELI_PROJECT_ROOT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setattr(paths, "_is_dev_mode", lambda: False)
    for f in (paths.data_dir, paths.config_dir, paths.cache_dir):
        f.cache_clear()
    try:
        root = tmp_path / "share" / "ELI_v3"
        assert paths.data_dir() == root / "artifacts"
        assert paths.config_dir() == root / "config"
        assert paths.cache_dir() == root / "cache"
    finally:
        for f in (paths.data_dir, paths.config_dir, paths.cache_dir):
            f.cache_clear()


def test_adopt_legacy_file_copies_once_and_leaves_the_old_one(tmp_path, monkeypatch):
    from eli.core.paths import adopt_legacy_file
    monkeypatch.setenv("HOME", str(tmp_path))
    old = tmp_path / ".eli" / "labs_notebook.json"
    old.parent.mkdir()
    old.write_text('{"a": 1}', encoding="utf-8")
    own = tmp_path / "ELI_v3" / "artifacts" / "labs" / "labs_notebook.json"
    assert adopt_legacy_file(own, "~/.eli/labs_notebook.json") == own
    assert own.read_text(encoding="utf-8") == '{"a": 1}' and old.exists()
    own.write_text('{"a": 2}', encoding="utf-8")
    adopt_legacy_file(own, "~/.eli/labs_notebook.json")  # never copied over again
    assert own.read_text(encoding="utf-8") == '{"a": 2}'


def test_no_code_writes_to_generic_or_relative_eli_paths():
    """Every file v3 keeps is under its own folders (eli.core.paths). Generic home locations
    (~/.eli, ~/.eli_*, ~/.config/eli, ~/.cache/eli, ~/.local/share/eli) belong to no single version,
    a relative artifacts/... path lands in whatever folder ELI was started from, and an eli_... name
    in the temp folder is shared with every other ELI version running beside it."""
    import re
    root = Path(__file__).resolve().parents[1]
    bad = re.compile(
        r"Path\.home\(\)\s*/\s*[\"']\.eli\b(?!-v3-)|[\"']\.eli_[a-z_]+[\"']|"
        r"Path\.home\(\)\s*/\s*[\"']\.(?:config|cache)[\"']\s*/\s*[\"']eli[\"']|"
        r"[\"']\.(?:config|cache)/eli\b|share/eli[\"'/]|share[\"']\s*/\s*[\"']eli[\"']|"
        r"user_(?:data|config|cache)_dir\(\s*[\"']eli[\"']|"
        r"Path\(\s*[\"']artifacts(?:[\"'/])|"
        r"gettempdir\(\).*[\"']eli[_-]")  # a temp file is named with eli.core.paths.temp_path
    import ast
    found = []
    for base in ("eli", "api"):
        for f in (root / base).rglob("*.py"):
            if "tests" in f.parts:
                continue
            text = f.read_text(encoding="utf-8", errors="ignore")
            doc_lines = set()  # docstrings may describe an old path; only code counts
            try:
                for node in ast.walk(ast.parse(text)):
                    body = getattr(node, "body", None)
                    if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                            and isinstance(getattr(body[0], "value", None), ast.Constant) \
                            and isinstance(body[0].value.value, str):
                        doc_lines.update(range(body[0].lineno, body[0].end_lineno + 1))
            except SyntaxError:
                pass
            for i, line in enumerate(text.splitlines(), 1):
                code = line.split("#", 1)[0]
                if i not in doc_lines and bad.search(code) and "adopt_legacy_file(" not in code:
                    found.append(f"{f.relative_to(root)}:{i}: {line.strip()[:100]}")
    assert not found, "generic or relative paths:\n" + "\n".join(found)

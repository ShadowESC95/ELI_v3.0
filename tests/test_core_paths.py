"""Tests for eli.core.paths — ~120 tests."""
from __future__ import annotations

from pathlib import Path
import pytest

from eli.core.paths import (
    project_root,
    models_dir,
    voices_dir,
    config_dir,
    data_dir,
    cache_dir,
    db_dir,
    logs_dir,
    plugins_dir,
    artifacts_dir,
    conversations_dir,
    proactive_dir,
    documents_dir,
    scripts_dir,
    notes_dir,
    user_db_path,
    agent_db_path,
    memory_db_path,
    knowledge_graph_db_path,
    gguf_models_dir,
    embedding_models_dir,
    models_root,
    training_root,
    get_paths,
    get_gguf_model_path,
    path_info,
    EliPaths,
    PATHS,
    PROJECT_ROOT,
    is_frozen,
    _is_dev_mode,
    _find_project_root,
    resolve_runtime_path,
)


# ── Basic imports and constants ───────────────────────────────────────────


def test_project_root_exists():
    assert project_root().exists()

def test_project_root_has_eli_dir():
    assert (project_root() / "eli").is_dir()

def test_project_root_constant_matches_function():
    assert PROJECT_ROOT == project_root()

# ── where data is kept is asked when it is used, not fixed at import ──────────────────────────────

# A store path fixed when its module is imported keeps the folder that was current then, so a folder
# set afterwards (a test lane setting its stores aside) is not followed: the executor's state file did
# so and a test read this checkout's own file. Every store is asked for when it is used.
_STORE_RESOLVERS = frozenset((
    "data_dir", "artifacts_dir", "config_dir", "cache_dir", "logs_dir", "log_dir", "db_dir", "learning_dir",
    "user_db_path", "memory_db_path", "agent_db_path", "conversations_dir", "proactive_dir", "documents_dir",
    "scripts_dir", "notes_dir"))
_STORE_ATTRS = _STORE_RESOLVERS | {"memory_db", "user_db", "agent_db"}


def _resolves_a_store(node, own=frozenset()):
    import ast
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name.lstrip("_") in _STORE_RESOLVERS or (isinstance(f, ast.Name) and name in own):
                return True
        elif isinstance(n, ast.Attribute) and n.attr in _STORE_ATTRS and isinstance(n.ctx, ast.Load):
            return True
    return False


def _run_at_import(tree):
    """What a module runs when imported: its body and the bodies of module-level if/try/with (not
    `if __name__ == "__main__"`), and the default values of its functions and methods."""
    import ast
    todo = list(tree.body)
    while todo:
        s = todo.pop(0)
        if isinstance(s, ast.If) and "__main__" in ast.dump(s.test):
            continue
        if isinstance(s, (ast.If, ast.Try, ast.With)):
            todo.extend(list(getattr(s, "body", [])) + list(getattr(s, "orelse", []))
                        + list(getattr(s, "finalbody", [])) + [b for h in getattr(s, "handlers", []) for b in h.body])
        elif isinstance(s, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)):
            yield s
        elif isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for d in ([s] if not isinstance(s, ast.ClassDef) else
                      [b for b in s.body if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef))]):
                yield from (v for v in d.args.defaults + d.args.kw_defaults if v is not None)
            if isinstance(s, ast.ClassDef):
                yield from (b for b in s.body if isinstance(b, (ast.Assign, ast.AnnAssign)))


def test_no_store_path_is_fixed_when_its_module_is_imported():
    import ast
    root = project_root()
    found = set()
    for top in ("eli", "api"):
        for py in sorted((root / top).rglob("*.py")):
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            own = frozenset(d.name for d in tree.body
                            if isinstance(d, (ast.FunctionDef, ast.AsyncFunctionDef)) and _resolves_a_store(d))
            if any(_resolves_a_store(s, own) for s in _run_at_import(tree)):
                found.add(py.relative_to(root).as_posix())
    assert not found, f"a store path fixed at import: {sorted(found)}"


# ── Dev mode detection ────────────────────────────────────────────────────

def test_is_not_frozen():
    assert not is_frozen()

def test_find_project_root_returns_path_or_none():
    result = _find_project_root()
    assert result is None or isinstance(result, Path)


def test_installer_like_tree_without_git_is_not_dev_mode(monkeypatch, tmp_path):
    import eli.core.paths as paths

    root = tmp_path / "ELI_v3.0"
    (root / "eli" / "cognition").mkdir(parents=True)
    (root / "eli" / "gui").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='eli-v3'\n")

    monkeypatch.delenv("ELI_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("ELI_DEV_MODE", raising=False)
    monkeypatch.delenv("ELI_FORCE_DEV_MODE", raising=False)
    monkeypatch.setattr(paths, "_find_project_root", lambda: root)

    paths.data_dir.cache_clear()
    paths.config_dir.cache_clear()
    paths.cache_dir.cache_clear()
    try:
        assert paths._is_dev_mode() is False
        assert paths.data_dir() != root / "artifacts"
    finally:
        paths.data_dir.cache_clear()
        paths.config_dir.cache_clear()
        paths.cache_dir.cache_clear()

def test_explicit_project_root_keeps_dev_mode(monkeypatch, tmp_path):
    import eli.core.paths as paths

    root = tmp_path / "ELI_v3.0"
    (root / "eli" / "cognition").mkdir(parents=True)
    (root / "eli" / "gui").mkdir(parents=True)
    monkeypatch.setenv("ELI_PROJECT_ROOT", str(root))
    monkeypatch.delenv("ELI_DEV_MODE", raising=False)
    monkeypatch.delenv("ELI_FORCE_DEV_MODE", raising=False)

    assert paths._is_dev_mode() is True


def test_relative_runtime_paths_anchor_to_active_project_root(monkeypatch, tmp_path):
    import eli.core.paths as paths

    root = tmp_path / "ELI_v3-3.0.9-linux-portable"
    cwd = tmp_path / "somewhere_else"
    (root / "eli" / "cognition").mkdir(parents=True)
    (root / "eli" / "gui").mkdir(parents=True)
    cwd.mkdir()

    monkeypatch.setenv("ELI_PROJECT_ROOT", str(root))
    monkeypatch.chdir(cwd)

    assert paths.resolve_runtime_path("models") == root / "models"
    assert paths.resolve_runtime_path("artifacts/db/user.sqlite3") == root / "artifacts" / "db" / "user.sqlite3"


def test_models_dir_relative_override_is_not_cwd_based(monkeypatch, tmp_path):
    import eli.core.paths as paths

    root = tmp_path / "ELI_v3-3.0.9-linux-portable"
    cwd = tmp_path / "caller_cwd"
    (root / "eli" / "cognition").mkdir(parents=True)
    (root / "eli" / "gui").mkdir(parents=True)
    cwd.mkdir()

    monkeypatch.setenv("ELI_PROJECT_ROOT", str(root))
    monkeypatch.setenv("ELI_MODELS_DIR", "models")
    monkeypatch.chdir(cwd)

    assert paths.models_dir() == root / "models"
    assert paths.models_dir() != cwd / "models"


# ── DB path functions ────────────────────────────────────────────────────


def test_knowledge_graph_db_same_as_memory():
    assert knowledge_graph_db_path() == memory_db_path()

def test_user_db_path_ends_with_sqlite3():
    assert user_db_path().suffix == ".sqlite3"

def test_agent_db_path_ends_with_sqlite3():
    assert agent_db_path().suffix == ".sqlite3"


# ── Env overrides ────────────────────────────────────────────────────────

def test_user_db_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ELI_USER_DB", str(tmp_path / "custom.sqlite3"))
    # Re-import to pick up override (functions are not cached for these env paths)
    from eli.core.paths import user_db_path as _udb
    assert str(tmp_path) in str(_udb())

def test_agent_db_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("ELI_AGENT_DB", str(tmp_path / "agent.sqlite3"))
    from eli.core.paths import agent_db_path as _adb
    assert str(tmp_path) in str(_adb())


# ── Models directories ────────────────────────────────────────────────────

def test_gguf_models_dir_inside_models_root():
    assert str(models_root()) in str(gguf_models_dir())

def test_embedding_models_dir_inside_models_root():
    assert str(models_root()) in str(embedding_models_dir())

def test_models_root_inside_project_root():
    assert str(project_root()) in str(models_root())

def test_models_root_matches_models_dir():
    assert models_root() == models_dir()


# ── get_paths / PATHS singleton ───────────────────────────────────────────


def test_paths_project_root_attr():
    p = get_paths()
    assert p.project_root is not None

def test_paths_models_dir_attr():
    p = get_paths()
    assert p.models_dir is not None

def test_paths_config_dir_attr():
    p = get_paths()
    assert p.config_dir is not None

def test_paths_user_db_attr():
    p = get_paths()
    assert p.user_db is not None

def test_paths_agent_db_attr():
    p = get_paths()
    assert p.agent_db is not None

def test_paths_repr():
    p = get_paths()
    r = repr(p)
    assert "EliPaths" in r


# ── GGUF model path ───────────────────────────────────────────────────────

def test_get_gguf_model_path_returns_str_or_none():
    result = get_gguf_model_path()
    assert result is None or isinstance(result, str)

def test_gguf_env_override(monkeypatch, tmp_path):
    model = tmp_path / "env_model.gguf"
    model.write_bytes(b"fake")
    monkeypatch.setenv("ELI_GGUF_MODEL_PATH", str(model))
    assert get_gguf_model_path() == str(model)


def test_gguf_relative_env_path_anchors_to_project_root(monkeypatch, tmp_path):
    import eli.core.paths as paths

    root = tmp_path / "ELI_v3-3.0.9-linux-portable"
    cwd = tmp_path / "caller_cwd"
    model = root / "models" / "env_model.gguf"
    (root / "eli" / "cognition").mkdir(parents=True)
    (root / "eli" / "gui").mkdir(parents=True)
    model.parent.mkdir(parents=True)
    model.write_bytes(b"fake")
    cwd.mkdir()

    monkeypatch.setenv("ELI_PROJECT_ROOT", str(root))
    monkeypatch.setenv("ELI_GGUF_MODEL_PATH", "models/env_model.gguf")
    monkeypatch.chdir(cwd)

    assert paths.get_gguf_model_path() == str(model)


# ── path_info ────────────────────────────────────────────────────────────


def test_path_info_has_project_root():
    info = path_info()
    assert "project_root" in info

def test_path_info_has_is_dev_mode():
    info = path_info()
    assert "is_dev_mode" in info

def test_path_info_has_is_frozen():
    info = path_info()
    assert "is_frozen" in info

@pytest.mark.parametrize("key", [
    "project_root", "data_dir", "config_dir", "cache_dir",
    "db_dir", "logs_dir", "models_dir", "voices_dir",
    "plugins_dir", "user_db", "agent_db",
])
def test_path_info_has_key(key):
    info = path_info()
    assert key in info


# ── EliPaths class ────────────────────────────────────────────────────────

def test_eli_paths_all_attrs():
    p = EliPaths()
    attrs = [
        "project_root", "root", "artifacts_dir", "data_dir", "config_dir",
        "cache_dir", "db_dir", "logs_dir", "log_dir", "models_dir",
        "voices_dir", "plugins_dir", "user_db", "agent_db", "memory_db",
        "knowledge_graph_db", "db", "conversations_dir", "proactive_dir",
        "documents_dir", "scripts_dir", "notes_dir", "notebook_dir",
    ]
    for attr in attrs:
        val = getattr(p, attr)
        assert val is not None, f"EliPaths.{attr} is None"

def test_eli_paths_root_alias():
    p = EliPaths()
    assert p.root == p.project_root

def test_eli_paths_db_alias():
    p = EliPaths()
    assert p.db is not None
    assert isinstance(p.db, Path)

def test_eli_paths_log_dir_alias():
    p = EliPaths()
    assert p.log_dir == p.logs_dir


def test_a_real_folder_named_eli_in_the_home_is_the_users_not_the_project(tmp_path, monkeypatch):
    """'~/eli/...' was always read as the project, so a folder of the user's named ~/eli could not be
    read or fixed. It stands for the project only where no such folder exists."""
    from pathlib import Path
    import eli.core.paths as P
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    project = P.canonical_project_root()
    assert P.resolve_user_repo_path("~/eli/notes/todo.md") == project / "notes" / "todo.md"
    (home / "eli" / "notes").mkdir(parents=True)
    assert P.resolve_user_repo_path("~/eli/notes/todo.md") == (home / "eli" / "notes" / "todo.md").resolve()
    assert P.resolve_user_repo_path(str(project / "eli" / "core")) == project / "eli" / "core"


def test_a_path_under_the_old_name_that_only_the_package_holds_is_the_packages(tmp_path, monkeypatch):
    """In the old layout the package sat at the top of ~/eli: "~/eli/core" is the package's core
    (LIST_DIR kept that in a list of its own; it now reads paths here)."""
    from pathlib import Path
    import eli.core.paths as P
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("HOME", str(home))
    project = P.canonical_project_root()
    assert P.resolve_user_repo_path("~/eli/core") == project / "eli" / "core"
    assert P.resolve_user_repo_path("~/eli/tests") == project / "tests"
    assert P.resolve_user_repo_path("~/eli/nothing/here.md") == project / "nothing" / "here.md"


def test_a_training_path_is_where_eli_keeps_its_training(tmp_path, monkeypatch):
    import eli.core.paths as P
    monkeypatch.setenv("ELI_LEARNING_DIR", str(tmp_path / "learn"))
    assert P.training_path("training/datasets/x.jsonl") == (tmp_path / "learn" / "datasets" / "x.jsonl").resolve()
    assert P.training_path("models/lora/a") == (P.project_root() / "models" / "lora" / "a").resolve()
    assert P.training_path(tmp_path / "abs.jsonl") == tmp_path / "abs.jsonl"


def test_test_mode_is_one_switch_read_in_one_place(monkeypatch):
    """PYTEST_CURRENT_TEST and "pytest is imported" were read as test mode as well, in three places, so
    a process a test started (the eval's replay of the real product) ran in test mode: no session-end
    flush, replies cut short, no model. ELI_TEST_MODE is the switch, and core.paths reads it."""
    import pathlib
    import re
    import eli.core.paths as P
    from eli.kernel.stages._common import _eli_test_mode
    monkeypatch.delenv("ELI_TEST_MODE", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_x.py::test_y (call)")
    assert P.test_mode() is False and _eli_test_mode() is False
    monkeypatch.setenv("ELI_TEST_MODE", "1")
    assert P.test_mode() is True and _eli_test_mode() is True
    package = pathlib.Path(P.__file__).resolve().parents[1]
    readers = sorted(str(f.relative_to(package)) for f in package.rglob("*.py")
                     if re.search(r"getenv\(\s*[\"']PYTEST_CURRENT_TEST|environ[^\n]{0,20}PYTEST_CURRENT_TEST"
                                  r"|[\"']pytest[\"'] in sys\.modules", f.read_text(encoding="utf-8")))
    assert not readers, readers
    # and ELI_TEST_MODE itself is read through test_mode(), not by each module on its own terms
    own = sorted(str(f.relative_to(package)) for f in package.rglob("*.py") if f.resolve() != pathlib.Path(P.__file__).resolve()
                 and re.search(r"(?:getenv|environ\.get)\(\s*[\"']ELI_TEST_MODE|environ\[\s*[\"']ELI_TEST_MODE[\"']\s*\](?!\s*=)",
                               f.read_text(encoding="utf-8")))
    assert not own, own


def test_the_suite_runs_as_its_own_owner_never_this_machines():
    """With no id in the sandbox's config folder, ELI brought the generic ~/.eli_user_id over, so every
    test ran as this machine's real owner (and a run outside the suite left a copy in a checkout)."""
    from tests._state_env import TEST_OWNER_ID
    from eli.kernel.engine import CognitiveEngine
    assert CognitiveEngine.__new__(CognitiveEngine)._get_user_id() == TEST_OWNER_ID


@pytest.mark.parametrize("platform,variable,default", [
    ("linux", "XDG_DATA_HOME", (".local", "share")),
    ("win32", "LOCALAPPDATA", ("AppData", "Local")),
])
@pytest.mark.parametrize("value", ["", "   ", "relative/share"])
def test_a_data_home_left_empty_or_relative_is_not_the_folder_eli_starts_in(tmp_path, monkeypatch, platform,
                                                                          variable, default, value):
    """An XDG_DATA_HOME set to nothing is no folder by the XDG rules, but it was read as Path(""), the
    current folder: a new user's whole ELI_v3 root (databases, settings, models) went into whatever
    folder ELI was started from, the packaged app's runtime hook and the launcher pointer with it."""
    import importlib.util
    import sys
    import eli.core.paths as P
    import eli.runtime.desktop_launchers as dl
    home, elsewhere = tmp_path / "home", tmp_path / "started_here"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setenv(variable, value)
    monkeypatch.setattr(dl, "product_line", lambda: "v3")
    own = (home.joinpath(*default) / "ELI_v3").resolve()
    hook_file = Path(P.__file__).resolve().parents[2] / "packaging" / "pyinstaller" / "rthook_eli_frozen_paths.py"
    spec = importlib.util.spec_from_file_location("_rthook_under_test", hook_file)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)          # not frozen here: it only defines its functions
    assert P.user_root() == own
    assert hook._user_root().resolve() == own
    assert dl.stable_user_root() == own


def test_temp_names_are_each_users_own(monkeypatch):
    """/tmp is one folder for every account on the machine, and a Windows named pipe one namespace. With
    the version alone in the name, a second account's ELI took the first one's speaking lock as its own
    (its microphone muted while the other ELI spoke) and could not write its own lock or player socket."""
    import os
    import eli.core.paths as P
    if not hasattr(os, "getuid"):
        pytest.skip("POSIX accounts")
    monkeypatch.setattr(os, "getuid", lambda: 1000)
    first = (P.temp_path("tts_speaking.lock"), P.pipe_path("youtube_mpv"))
    monkeypatch.setattr(os, "getuid", lambda: 1001)
    second = (P.temp_path("tts_speaking.lock"), P.pipe_path("youtube_mpv"))
    assert first[0] != second[0] and first[1] != second[1]
    assert first[0].parent == second[0].parent and first[0].name.startswith("ELI_v3-")

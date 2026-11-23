"""The FAISS index must honour the artifacts overrides like every other store.

`_get_index_paths()` hardcoded `_project_root()/artifacts/vectors`, which meant:

* an **installed** build kept writing inside the installation even though
  `_is_dev_mode()` is False when frozen and every other store resolves to the
  platform data dir — on an AppImage that directory is a read-only mount;
* **redirection was ignored**, so a run that had pointed every database at a
  throwaway tree still wrote vectors into the user's real semantic memory. Caught
  live: a test session appended five auto-reflections about itself to the real index
  (292 -> 297). The suite only escaped the same fate because faiss is absent here,
  which is luck, not isolation.

Dev-tree behaviour must stay byte-identical: artifacts_dir() IS
<project_root>/artifacts in dev mode.
"""
import importlib
import os
from pathlib import Path

import pytest

from eli.core.paths import project_root


def _paths(monkeypatch, create=True, **env):
    """Where the index is with only `env` set. A test that asks with neither variable set asks where
    the checkout's own index is, and asks it without making the folder (create=False): asking with
    create made an empty artifacts/vectors in the checkout on every full run."""
    for k in ("ELI_ARTIFACTS_DIR", "ELI_DATA_DIR"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    from eli.core import paths
    paths.data_dir.cache_clear()          # resolved again for these variables, not the run's
    import eli.memory.vector_store as vs
    importlib.reload(vs)
    return Path(vs._get_index_paths(create=create)[0])


def test_artifacts_dir_override_is_honoured(tmp_path, monkeypatch):
    p = _paths(monkeypatch, ELI_ARTIFACTS_DIR=str(tmp_path))
    assert tmp_path in p.parents, f"index escaped the override: {p}"


def test_data_dir_override_is_honoured(tmp_path, monkeypatch):
    p = _paths(monkeypatch, ELI_DATA_DIR=str(tmp_path))
    assert tmp_path in p.parents


def test_artifacts_dir_wins_over_data_dir(tmp_path, monkeypatch):
    a = tmp_path / "a"; d = tmp_path / "d"
    p = _paths(monkeypatch, ELI_ARTIFACTS_DIR=str(a), ELI_DATA_DIR=str(d))
    assert a in p.parents and d not in p.parents


def test_dev_tree_default_is_unchanged(monkeypatch):
    """The whole point of using artifacts_dir(): a source checkout must land in
    exactly the directory it always did, so nobody's index moves. (Alone, this read the run's
    cached data folder and failed; in the file's order it made the checkout's folder.)"""
    p = _paths(monkeypatch, create=False)
    assert p == (project_root() / "artifacts" / "vectors" / "index.faiss").resolve()


def test_the_hardcoded_project_path_is_gone():
    import inspect
    import eli.memory.vector_store as vs
    src = inspect.getsource(vs._get_index_paths)
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith(("*", "#")))
    assert "ELI_ARTIFACTS_DIR" in body and "artifacts_dir" in body


def test_migration_does_not_clobber_an_existing_index(tmp_path, monkeypatch):
    """Copying a legacy index into a location that already has one would destroy
    live data — the guard is 'only when the target has none'."""
    vdir = tmp_path / "vectors"; vdir.mkdir(parents=True)
    sentinel = vdir / "index.faiss"
    sentinel.write_bytes(b"DO-NOT-CLOBBER")
    _paths(monkeypatch, ELI_ARTIFACTS_DIR=str(tmp_path))
    assert sentinel.read_bytes() == b"DO-NOT-CLOBBER"


def test_paths_never_raise_when_the_target_is_unwritable(tmp_path, monkeypatch):
    """A read-only target must degrade to the user data dir, not take VectorStore
    construction down with it."""
    blocked = tmp_path / "ro"
    blocked.mkdir()
    blocked.chmod(0o500)
    try:
        # the user data dir it falls back to is this test's own, not the checkout's artifacts
        p = _paths(monkeypatch, ELI_ARTIFACTS_DIR=str(blocked), ELI_DATA_DIR=str(tmp_path / "data"))
        assert p.name == "index.faiss" and p.parent == (tmp_path / "data" / "vectors").resolve()
    finally:
        blocked.chmod(0o700)


def _checkout_with_an_index(tmp_path):
    root = tmp_path / "checkout"
    legacy = root / "artifacts" / "vectors"
    legacy.mkdir(parents=True)
    (legacy / "index.faiss").write_bytes(b"THE-DEVELOPERS-OWN-INDEX")
    (legacy / "meta.json").write_text('[{"text": "a memory of the developer"}]', encoding="utf-8")
    return root


def test_a_redirected_store_starts_empty_while_the_checkout_has_an_index(tmp_path, monkeypatch):
    """The legacy-location copy filled every redirected vectors folder (the test sandbox, a tmp_path)
    with the checkout's real index and its memories' text, so tests searched the developer's memories."""
    import eli.memory.vector_store as vs
    root = _checkout_with_an_index(tmp_path)
    target = tmp_path / "sandbox"
    monkeypatch.setenv("ELI_ARTIFACTS_DIR", str(target))
    monkeypatch.setattr(vs, "_project_root", lambda: root)
    index_path, meta_path = vs._get_index_paths()
    assert Path(index_path).parent == (target / "vectors").resolve()
    assert not Path(index_path).exists() and not Path(meta_path).exists(), "the checkout's index was copied in"
    for env in ("ELI_ARTIFACTS_DIR", "ELI_DATA_DIR"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("ELI_DATA_DIR", str(tmp_path / "other"))
    index_path, meta_path = vs._get_index_paths()
    assert not Path(index_path).exists() and not Path(meta_path).exists()


def test_asking_where_the_index_is_makes_nothing_and_copies_nothing(tmp_path, monkeypatch):
    """A status report, and the suite's check of where every store resolves, asked the index's path,
    and asking made the folder (the --noconftest lane left an empty artifacts/vectors in the checkout)
    and could carry the old index over."""
    import eli.memory.vector_store as vs
    root = _checkout_with_an_index(tmp_path)
    own = tmp_path / "installed_data"
    for env in ("ELI_ARTIFACTS_DIR", "ELI_DATA_DIR", "ELI_TEST_MODE"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(vs, "_project_root", lambda: root)
    monkeypatch.setattr("eli.core.paths.artifacts_dir", lambda: own)
    index_path, meta_path = vs._get_index_paths(create=False)
    assert Path(index_path) == (own / "vectors" / "index.faiss").resolve() and not own.exists()
    from eli.memory import memory_truth
    assert memory_truth._store_paths()["index"] == Path(index_path) and not own.exists()
    assert Path(vs._get_index_paths()[0]).read_bytes() == b"THE-DEVELOPERS-OWN-INDEX", "the store itself still does"


def test_the_installation_s_own_folder_still_takes_the_old_index(tmp_path, monkeypatch):
    """What the copy is for: an installed build whose index used to live inside the installation."""
    import eli.memory.vector_store as vs
    root = _checkout_with_an_index(tmp_path)
    own = tmp_path / "installed_data"
    for env in ("ELI_ARTIFACTS_DIR", "ELI_DATA_DIR", "ELI_TEST_MODE"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(vs, "_project_root", lambda: root)
    monkeypatch.setattr("eli.core.paths.artifacts_dir", lambda: own)
    index_path, _ = vs._get_index_paths()
    assert Path(index_path).read_bytes() == b"THE-DEVELOPERS-OWN-INDEX"


def test_tests_never_take_the_checkout_s_index(tmp_path, monkeypatch):
    import eli.memory.vector_store as vs
    root = _checkout_with_an_index(tmp_path)
    for env in ("ELI_ARTIFACTS_DIR", "ELI_DATA_DIR"):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setenv("ELI_TEST_MODE", "1")
    monkeypatch.setattr(vs, "_project_root", lambda: root)
    monkeypatch.setattr("eli.core.paths.artifacts_dir", lambda: tmp_path / "data")
    index_path, _ = vs._get_index_paths()
    assert not Path(index_path).exists()


def test_the_test_sandbox_starts_without_the_last_runs_state():
    """A vector index copied into the sandbox once was searched by every later run, and a VR session
    one run recorded showed in the next run's period log. Each run starts with an empty sandbox: no
    file in it was made before this run's own start (tests/conftest.py's _SANDBOX_STARTED). Change
    times, which a copy cannot carry over as it can a modification time."""
    import sys
    import eli.memory.vector_store as vs
    conftest = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    started = getattr(conftest, "_SANDBOX_STARTED", None)
    assert started is not None, "the run does not record when its sandbox was made"
    sandbox = (Path(__file__).resolve().parents[1] / "artifacts" / "_pytest").resolve()
    stale = [p for p in sandbox.rglob("*") if p.is_file() and p.stat().st_ctime < started - 1]
    assert stale == [], f"left from an earlier run: {stale[:5]}"
    for path in vs._get_index_paths():
        assert sandbox in Path(path).resolve().parents, path


def test_a_run_going_on_in_the_checkout_keeps_its_sandbox(tmp_path, monkeypatch):
    """Starting a second run while one is going must not pull the sandbox from under the first: the
    run going on holds a shared lock (outside the checkout), and the new run then leaves it as it is."""
    import sys
    fcntl = pytest.importorskip("fcntl")
    conftest = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    assert conftest is not None and hasattr(conftest, "_fresh_sandbox")
    sandbox = tmp_path / "artifacts" / "_pytest"
    (sandbox / "db").mkdir(parents=True)
    marker = sandbox / "db" / "open.sqlite3"
    marker.write_text("in use")
    monkeypatch.setattr(conftest, "ROOT", tmp_path)
    monkeypatch.setattr(conftest, "_SANDBOX_ROOT", sandbox)
    monkeypatch.setattr(conftest, "_SANDBOX_HOLD", conftest._SANDBOX_HOLD)    # this session's own, put back
    other_run = open(conftest._sandbox_lock(), "a+")
    assert tmp_path not in conftest._sandbox_lock().parents, "the lock is a file left in the checkout"
    try:
        fcntl.flock(other_run, fcntl.LOCK_SH)
        conftest._fresh_sandbox()
        assert marker.exists(), "a run going on lost its sandbox"
    finally:
        other_run.close()
    conftest._SANDBOX_HOLD.close()
    conftest._fresh_sandbox()
    assert not sandbox.exists(), "with no run going on, the last run's sandbox goes"
    conftest._SANDBOX_HOLD.close()


def test_whatever_a_test_writes_anywhere_in_the_checkout_is_named_with_the_test(tmp_path, monkeypatch):
    """A full run left artifacts/runtime and artifacts/vectors in the checkout, both empty, and the
    run's guard, which listed only files, said nothing; it watched only ELI's state folders, so the
    capability manifest written at the checkout's root and a rewritten document passed unseen too.
    Every folder made, file written and file deleted anywhere in the checkout is named now, with the
    test that did it, the eval's throwaway folder among them (a run with its data folder outside the
    project left an empty artifacts/_eval_sandbox). Not the run's own sandbox, bytecode or a Python
    environment kept in the checkout, nor what the run was asked to write."""
    import sys
    conftest = sys.modules.get("tests.conftest") or sys.modules.get("conftest")
    (tmp_path / "artifacts" / "db").mkdir(parents=True)
    (tmp_path / "blueprints").mkdir()
    (tmp_path / "blueprints" / "a.md").write_text("as shipped")
    (tmp_path / "docs.md").write_text("as shipped")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "pyvenv.cfg").write_text("home = /usr\n")
    monkeypatch.setattr(conftest, "ROOT", tmp_path)
    monkeypatch.setattr(conftest, "SANDBOX", tmp_path / "artifacts" / "_pytest")
    monkeypatch.setattr(conftest, "_WATCHED", str(tmp_path.resolve()))
    monkeypatch.setattr(conftest, "_WRITTEN_BY", {})
    before = conftest._own_state()
    (tmp_path / "artifacts" / "runtime").mkdir()
    (tmp_path / "artifacts" / "_eval_sandbox").mkdir()
    (tmp_path / "artifacts" / "_pytest").mkdir()
    (tmp_path / "artifacts" / "_pytest" / "user.sqlite3").write_text("the run's own")
    (tmp_path / "capability_manifest.json").write_text("{}")
    (tmp_path / "blueprints" / "a.md").write_text("rewritten")
    (tmp_path / "docs.md").unlink()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / ".venv" / "lib").mkdir()
    (tmp_path / "report.xml").write_text("asked for")
    # a folder removed elsewhere is not this checkout's, though rmtree names its files relative to it
    import shutil
    import tempfile
    monkeypatch.chdir(tmp_path)
    elsewhere = Path(tempfile.mkdtemp())
    (elsewhere / "notes.txt").write_text("not the checkout's")
    shutil.rmtree(elsewhere)
    assert str((tmp_path / "notes.txt").resolve()) not in conftest._WRITTEN_BY
    this_test = os.environ["PYTEST_CURRENT_TEST"].split(" ")[0]
    assert conftest._written_since(before, [tmp_path / "report.xml"]) == [
        f"artifacts/_eval_sandbox/ (made by {this_test})",
        f"artifacts/runtime/ (made by {this_test})",
        f"blueprints/a.md (written by {this_test})",
        f"capability_manifest.json (written by {this_test})",
        f"docs.md (deleted by {this_test})",
    ]


def test_a_module_run_without_the_conftest_notes_a_folder_made_in_the_machines_own_state(tmp_path):
    """The --noconftest lane's guard noted files and databases opened under this machine's state, not
    folders made there, so asking where something is could make its folder unseen."""
    from tests import _state_env
    machine = (tmp_path / "machine").resolve()
    machine.mkdir()
    with _state_env.own_state(tmp_path / "mine") as held:
        held.roots = (str(machine),)
        (machine / "runtime").mkdir()
        (tmp_path / "mine" / "runtime").mkdir(parents=True)
    assert [path for _test, path in held.opened_elsewhere] == [str(machine / "runtime")]

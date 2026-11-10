"""Every place ELI keeps state, for a test run: one folder holds all of it.

The suite conftest puts it in its throwaway sandbox (artifacts/_pytest). A module that runs without
the conftest (the --noconftest lane, tests/test_world_spatial_feed.py) puts it in a folder of its own
the same way, and both check it with `stores()`. The list was the conftest's alone, so a module run
without it read and wrote this machine's records: the owner's thoughts, agenda, world and models
on a developer's machine.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, Iterator, List, Tuple


def state_env(root: Path) -> Dict[str, str]:
    """The environment that puts every store under `root`."""
    root = Path(root)
    db = root / "db"
    return {
        # data_dir() holds the conversations, incidents, runtime state, per-user files and databases;
        # config_dir() the small settings files (pomodoro, voice clones); learning_dir() training runs.
        "ELI_ARTIFACTS_DIR": str(root),
        "ELI_DATA_DIR": str(root),
        "ELI_LEARNING_DIR": str(root / "training"),
        "ELI_CONFIG_DIR": str(root / "config"),
        "ELI_GPU_PACK_LOG": str(root / "gpu-pack.log"),          # beside the checkout's runtime/ otherwise
        "ELI_VOICE_REFS_DIR": str(root / "voice_profiles"),      # and a cloned voice beside its models/
        "ELI_KEYS_DIR": str(root / "eli-keys"),                  # licence keys every ELI product shares
        # starting an engine regenerates the capabilities table (data_dir() otherwise; never the blueprint)
        "ELI_CAPABILITIES_DOC": str(root / "capabilities_and_actions.md"),
        # the user DB is resolved by user_db_path(), which honours ELI_USER_DB/ELI_MEMORY_DB before
        # anything else; the agent DB by ELI_AGENT_DB first
        "ELI_USER_DB": str(db / "user.sqlite3"),
        "ELI_MEMORY_DB": str(db / "user.sqlite3"),
        "ELI_DB_DIR": str(db),
        "ELI_AGENT_DB": str(db / "agent.sqlite3"),
        # persona_auto_path() and notebook_dir() have their own overrides
        "ELI_PERSONA_AUTO_PATH": str(root / "persona.auto.txt"),
        "ELI_NOTEBOOK_DIR": str(root / "eli_notebook"),
        # storage.WORLD_DIR is frozen when that module is first imported
        "ELI_WORLD_DIR": str(root / "world"),
        "ELI_LAST_TRACE_PATH": str(root / "last_trace.json"),
        # the audit chain, the capability state and the HMAC key the chain is signed with
        "ELI_ORCHESTRATOR_AUDIT_DB": str(db / "orchestrator_audit.sqlite3"),
        "ELI_CAPABILITY_STATE_DB": str(db / "capability_state.sqlite3"),
        "ELI_AUDIT_HMAC_KEY": "eli-pytest-audit-key-not-a-real-secret",
        "ELI_DOCUMENT_INDEX_DB": str(db / "documents.sqlite3"),
        # the agenda (calendar and reminders): a DB, an .ics file, and no desktop notifications
        "ELI_AGENDA_DB": str(db / "agenda.sqlite3"),
        "ELI_CALENDAR_FILE": str(root / "calendar.ics"),
        "ELI_AGENDA_NOTIFY": "0",
        # nor this machine's settings (Net toggle, Full Control, model paths)
        "ELI_SETTINGS_FILE": str(root / "settings.json"),
    }


# The run's own owner. With none in its config folder, ELI brings the generic ~/.eli_user_id over
# (paths.adopt_legacy_file), so every test ran as this machine's real owner.
TEST_OWNER_ID = "a-test-owner"


def seed_identity(config_dir: Path) -> None:
    folder = Path(config_dir)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "user_id").write_text(TEST_OWNER_ID, encoding="utf-8")


def stores() -> Dict[str, Path]:
    """Each store as ELI resolves it now, module constants frozen at import included."""
    from eli.cognition import persona
    from eli.core import paths
    from eli.core.runtime_settings import _settings_file
    from eli.memory.vector_store import _get_index_paths
    from eli.planning.goal_store import _default_goal_store
    from eli.runtime.evidence_ledger import _default_db_path
    from eli.world.persistence import storage

    found = {
        "data folder": paths.data_dir(), "config folder": paths.config_dir(), "training folder": paths.learning_dir(),
        "user DB": paths.user_db_path(), "memory DB": paths.memory_db_path(), "agent DB": paths.agent_db_path(),
        "orchestrator audit DB": paths.orchestrator_audit_db_path(),
        "capability state DB": paths.capability_state_db_path(), "document index DB": paths.document_index_db_path(),
        "agenda DB": paths.agenda_db_path(), "calendar": paths.calendar_ics_path(),
        "persona overlay": paths.persona_auto_path(), "persona overlay (persona module)": persona.auto_path(),
        "notebook": paths.notebook_dir(), "settings": _settings_file(), "goal store": _default_goal_store(),
        "evidence ledger": _default_db_path(), "world (storage.WORLD_DIR)": storage.WORLD_DIR,
        "world state (storage.STATE_PATH)": storage.STATE_PATH, "world folder": storage.world_dir(),
    }
    found.update({f"vector index {Path(p).name}": p for p in _get_index_paths()})
    return {name: Path(p).expanduser().resolve() for name, p in found.items()}


def outside(root: Path) -> Dict[str, Path]:
    """The stores that resolve anywhere but under `root`."""
    safe = Path(root).resolve()
    return {name: p for name, p in stores().items() if p != safe and safe not in p.parents}


# ── a module of its own, without the conftest ────────────────────────────────────────────────────

class OwnState:
    """What `own_state` holds: the folder, and every file or database opened meanwhile under this
    machine's own state folders (test, path)."""

    def __init__(self, root: Path, roots: Tuple[str, ...], callers_sandbox: str = "") -> None:
        self.root = Path(root)
        self.roots = roots
        # a run that was a test before the module (the suite's conftest) has its own throwaway
        # sandbox: a world object built there earlier in the run is the run's, not the machine's
        self.callers_sandbox = callers_sandbox
        self.opened_elsewhere: List[Tuple[str, str]] = []


_HELD: List[OwnState] = []
_HOOKED = [False]


def _note_opened(event, args) -> None:
    """Audit hook: a database connected or a file opened under the machine's own state, while held."""
    if not _HELD or event not in ("open", "sqlite3.connect") or not args:
        return
    try:
        path = os.path.realpath(os.fsdecode(args[0]))
    except (TypeError, ValueError):
        return                                         # a file descriptor and the like
    held = _HELD[-1]
    for own in (str(held.root.resolve()), held.callers_sandbox):
        if own and (path == own or path.startswith(own + os.sep)):
            return
    if any(path == r or path.startswith(r + os.sep) for r in held.roots):
        held.opened_elsewhere.append((os.environ.get("PYTEST_CURRENT_TEST", "").split(" ")[0], path))


def machines_own_state() -> Tuple[str, ...]:
    """This machine's state folders as ELI resolves them now (before a module sets its own): the data
    and config folders, the per-user root of an installed ELI, the checkout's artifacts and config,
    the generic id file, every store as it resolves, and every store the caller's environment names."""
    from eli.core import paths
    roots = {paths.data_dir(), paths.config_dir(), paths._frozen_user_root(), paths.project_root() / "artifacts",
             paths.project_root() / "config", Path.home() / ".eli_user_id", *stores().values()}
    names = state_env(Path(os.sep))
    roots |= {Path(v) for k, v in os.environ.items() if k in names and os.sep in v}
    return tuple(sorted({str(Path(r).expanduser().resolve()) for r in roots}))


def _forget_resolved_paths() -> None:
    from eli.core import paths
    for cached in (paths.data_dir, paths.config_dir, paths.cache_dir):
        cached.cache_clear()


@contextmanager
def own_state(root: Path) -> Iterator[OwnState]:
    """Every store under `root`, and test mode on (ELI_TEST_MODE), for as long as it is held, for a
    module that runs without the suite conftest (the --noconftest lane); what it opens under the
    machine's own state meanwhile is noted (`opened_elsewhere`). Put back as it was afterwards."""
    import pytest
    from eli.core.paths import data_dir, test_mode
    root = Path(root)
    held = OwnState(root, machines_own_state(), str(data_dir().resolve()) if test_mode() else "")
    seed_identity(root / "config")
    with pytest.MonkeyPatch.context() as mp:
        for name, value in state_env(root).items():
            mp.setenv(name, value)
        mp.setenv("ELI_TEST_MODE", "1")
        _forget_resolved_paths()
        # the world's storage freezes its paths when it is first imported (on the same lane, by an
        # earlier module): they are pointed here as well
        from eli.world.persistence import snapshots, storage
        world = Path(os.environ["ELI_WORLD_DIR"])
        for name, value in (("WORLD_DIR", world), ("STATE_PATH", world / "eli_world_state.json"),
                            ("EVENTS_PATH", world / "events.jsonl"), ("ACTIONS_PATH", world / "actions.jsonl")):
            mp.setattr(storage, name, value)
        mp.setattr(snapshots, "STATE_PATH", world / "eli_world_state.json", raising=False)
        # EliWorldStorage(state_path=STATE_PATH) took the constant as its default when it was defined
        mp.setattr(storage.EliWorldStorage.__init__, "__defaults__", (world / "eli_world_state.json",))
        if not _HOOKED[0]:
            sys.addaudithook(_note_opened)
            _HOOKED[0] = True
        _HELD.append(held)
        try:
            yield held
        finally:
            _HELD.remove(held)
    _forget_resolved_paths()

"""Every place ELI keeps state, for a test run: one folder holds all of it.

The suite conftest puts it in its throwaway sandbox (artifacts/_pytest). A module that runs without
the conftest (the --noconftest lane, tests/test_world_spatial_feed.py) puts it in a folder of its own
the same way, and both check it with `stores()`. The list was the conftest's alone, so a module run
without it read and wrote this machine's records: the owner's thoughts, agenda, world and models
on a developer's machine.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict


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
        # starting an engine regenerates the capabilities table and the manifest at the checkout's root
        "ELI_CAPABILITIES_DOC": str(root / "capabilities_and_actions.md"),
        "ELI_CAPABILITY_MANIFEST": str(root / "capability_manifest.json"),
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

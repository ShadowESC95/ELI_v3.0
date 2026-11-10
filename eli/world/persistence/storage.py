from __future__ import annotations
import dataclasses
import json
import os
import tempfile
import threading
from pathlib import Path
from time import time
from typing import Any, Dict, Optional
from eli.world.agency.world_constitution import get_world_constitution, get_world_identity
from eli.world.core.ontology import get_default_rooms
from eli.world.core.schemas import AwarenessState, AvatarState, EliWorldState, WorldAction, WorldEvent, WorldObject

from eli.utils.log import get_logger

log = get_logger(__name__)


def _world_dir() -> Path:
    """Return the absolute path to the world state directory.

    Uses get_paths() so the directory resolves correctly regardless of
    working directory — critical because this module is imported from
    multiple entry points (GUI, daemon, CLI) that may set cwd differently.
    Relative Path("artifacts/world") would fail silently when cwd is not
    the project root, causing load() to create a default state with
    room="core_room" every time, overriding the persisted room placement.

    Override: ELI_WORLD_DIR — without it a test run writes real autonomy-engine
    events into the actual eli_world_state.json/events.jsonl/actions.jsonl.
    """
    override = os.environ.get("ELI_WORLD_DIR")
    if override:
        return Path(override).expanduser().resolve()
    try:
        from eli.core.paths import get_paths as _gp
        return Path(_gp().artifacts_dir) / "world"
    except Exception:
        # Fallback: resolve relative to this file → project root / artifacts / world
        return Path(__file__).resolve().parents[4] / "artifacts" / "world"


def world_dir() -> Path:
    """Public accessor for the world state directory.

    Exported so journal/provenance/snapshots resolve the same way instead of
    each hardcoding a relative ``Path("artifacts/world/...")`` — three of them
    did, and the docstring on _world_dir above already records why that fails.
    Resolved at every call, never kept: the world's paths used to be module
    constants fixed when this module was first imported, so a data folder set
    afterwards (a test's sandbox, ELI_WORLD_DIR) was never where the world went.
    """
    return _world_dir()


def state_file() -> Path:
    """The world state file, in world_dir() as it resolves now."""
    return world_dir() / "eli_world_state.json"


_CORRUPT_BACKUP_KEEP = 5
# Serialises the read-modify-write swap across the GUI turn, the proactive
# daemon and the world panel timer, which all touch this file concurrently.
_SAVE_LOCK = threading.RLock()

# The world fires an action on every autonomy tick and these files are appended forever.
# Nothing rotated them: actions.jsonl reached 41MB / 80,576 lines and events.jsonl 6.2MB on a
# normal desktop, and nothing reads them whole (the panel and journal want the recent tail).
# Corrupt state backups were already pruned here, the logs weren't.
_JSONL_MAX_LINES = int(os.environ.get("ELI_WORLD_LOG_MAX_LINES", "20000"))
_JSONL_CHECK_EVERY = 250          # stat() cost, not a rewrite, on most appends
_jsonl_since_check: Dict[str, int] = {}


def _trim_jsonl(path: Path, max_lines: int = 0) -> None:
    """Keep the newest `max_lines` of an append-only log. Never raises.

    Counted rather than size-capped so a trim never splits a JSON line in half,
    and checked every N appends so the common path stays a dict increment.

    The counter lives in this process only, so "every N appends" alone leaves a
    hole: a run that appends fewer than N entries never checks at all, and the
    file grows across restarts untouched. events.jsonl was found at 23,179 lines
    against a 20,000 cap for exactly that reason while the busier actions.jsonl
    had been trimmed correctly. So the FIRST append to a path in a given process
    always checks — one stat at startup — and the amortised counter takes over
    from there.
    """
    limit = max_lines or _JSONL_MAX_LINES
    if limit <= 0:
        return
    key = str(path)
    if key in _jsonl_since_check:
        n = _jsonl_since_check[key] + 1
        if n < _JSONL_CHECK_EVERY:
            _jsonl_since_check[key] = n
            return
    _jsonl_since_check[key] = 0
    try:
        if not path.exists():
            return
        with path.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        if len(lines) <= limit:
            return
        keep_lines = lines[-limit:]
        tmp = path.with_suffix(path.suffix + ".trim")
        with tmp.open("w", encoding="utf-8") as f:
            f.writelines(keep_lines)
        os.replace(tmp, path)      # atomic; a crash mid-trim leaves the original
        log.debug("world log %s trimmed %d → %d lines", path.name, len(lines), len(keep_lines))
    except Exception:
        log.debug("world log trim failed for %s", path, exc_info=True)


def _prune_corrupt_backups(state_path: Path, keep: int = _CORRUPT_BACKUP_KEEP) -> None:
    """Keep only the newest corrupt-state backups to avoid unbounded disk use."""
    try:
        backups = sorted(
            state_path.parent.glob(f"{state_path.stem}.corrupt_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in backups[keep:]:
            try:
                old.unlink()
            except Exception:
                log.debug("suppressed exception", exc_info=True)
    except Exception:
        log.debug("suppressed exception", exc_info=True)

def _fit(cls, data: Any):
    """Build `cls` from a saved dict, ignoring fields it no longer declares.

    ``AwarenessState(**data)`` raises TypeError the moment the state on disk
    carries a key the dataclass has since dropped or renamed. load() catches
    every exception, files the state as ".corrupt_*" and hands back a fresh
    default world — so a single renamed field silently wipes the user's rooms,
    objects, goals and habits on upgrade. Two of the five corrupt backups on
    this machine parse as perfectly valid JSON; they were condemned for exactly
    this and nothing else.

    Forward-compatibility matters more than strictness here: a world that comes
    back missing one new attribute beats a world that comes back empty.
    """
    if not isinstance(data, dict):
        return cls()
    known = {f.name for f in dataclasses.fields(cls)}
    extra = set(data) - known
    if extra:
        log.debug("world state: ignoring unknown %s field(s) %s", cls.__name__, sorted(extra))
    try:
        return cls(**{k: v for k, v in data.items() if k in known})
    except TypeError:
        # A *required* field is missing (schema went the other way). Still
        # better to lose one record than the whole world.
        log.debug("world state: could not rebuild %s from %s", cls.__name__, sorted(data), exc_info=True)
        return None


def _fit_all(cls, items) -> list:
    built = [_fit(cls, i) for i in (items or [])]
    return [b for b in built if b is not None]


def _state_from_dict(data: Dict[str, Any]) -> EliWorldState:
    state = EliWorldState()
    state.world_name = data.get("world_name", state.world_name)
    state.identity = data.get("identity") or get_world_identity()
    state.constitution = data.get("constitution") or get_world_constitution()
    state.awareness = _fit(AwarenessState, data.get("awareness", {})) or AwarenessState()
    state.avatar = _fit(AvatarState, data.get("avatar", {})) or AvatarState()
    state.rooms = data.get("rooms") or get_default_rooms()
    spaces = data.get("spaces")
    state.spaces = {str(k): dict(v) for k, v in spaces.items() if isinstance(v, dict)} if isinstance(spaces, dict) else {}
    objects = {k: _fit(WorldObject, v) for k, v in (data.get("objects") or {}).items()}
    state.objects = {k: v for k, v in objects.items() if v is not None}
    state.events = _fit_all(WorldEvent, data.get("events", [])[-300:])
    state.actions = _fit_all(WorldAction, data.get("actions", [])[-300:])
    state.goals = data.get("goals", [])
    state.habits = data.get("habits", [])
    state.timestamp = data.get("timestamp", time())
    return state

class EliWorldStorage:
    """The world kept in one state file, its event and action logs beside it. With no `state_path`,
    the file is state_file() as it resolves at each use, so a long-lived storage (the autonomy
    engine's) follows the data folder instead of the one there was when it was made."""

    def __init__(self, state_path: Optional[Path] = None):
        self._state_path = Path(state_path) if state_path is not None else None
        self._ensure()

    @property
    def state_path(self) -> Path:
        return self._state_path if self._state_path is not None else state_file()

    @state_path.setter
    def state_path(self, value: Optional[Path]) -> None:
        self._state_path = Path(value) if value is not None else None

    def _ensure(self) -> Path:
        folder = self.state_path.parent
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def load(self) -> EliWorldState:
        if not self.state_path.exists():
            state = EliWorldState(identity=get_world_identity(), constitution=get_world_constitution(), rooms=get_default_rooms())
            self.save(state)
            return state
        try:
            raw = self.state_path.read_text(encoding="utf-8")
        except OSError as exc:
            # A transient read failure is not corruption. Condemning the file
            # here would destroy a healthy world over a momentary EIO/EBUSY.
            log.warning("world state unreadable; keeping the file", exc_info=True)
            return self._stand_in(f"it could not be read ({type(exc).__name__}: {exc})")
        try:
            data = json.loads(raw)
        except ValueError:
            # Genuinely unparseable. This is the only case that earns a backup.
            log.warning("world state is not valid JSON; filing it as corrupt")
            data = None
        if data is not None:
            try:
                return _state_from_dict(data)
            except Exception as exc:
                # The JSON parsed, so the user's data is intact on disk even if
                # this build cannot map it. Keep the file and start fresh in
                # memory rather than renaming their world away.
                log.warning("world state parsed but could not be rebuilt; leaving "
                            "the file in place", exc_info=True)
                return self._stand_in(f"this build could not rebuild it ({type(exc).__name__}: {exc})")
        corrupt = self.state_path.with_suffix(f".corrupt_{int(time())}.json")
        try:
            self.state_path.rename(corrupt)
        except Exception:
            log.debug("could not file the corrupt world state aside", exc_info=True)
        _prune_corrupt_backups(self.state_path)
        state = EliWorldState(identity=get_world_identity(), constitution=get_world_constitution(), rooms=get_default_rooms())
        self.save(state)
        return state

    def _stand_in(self, why: str) -> EliWorldState:
        """A default world to work with in memory while the one on disk cannot be read. It carries
        where it stands in for, and save() never writes it over that file: the next save after a
        failed read used to replace the user's rooms, objects, goals and habits with the default."""
        state = EliWorldState(identity=get_world_identity(), constitution=get_world_constitution(),
                              rooms=get_default_rooms())
        object.__setattr__(state, "_stands_in_for", (str(self.state_path), why))
        return state

    def save(self, state: EliWorldState) -> bool:
        """Atomically persist the world state. False, with nothing written, for a stand-in world
        (see _stand_in) while the file it stands in for is still there.

        The scratch file is unique per write and the swap is serialised. A
        single fixed ".tmp" name raced: the GUI turn, the proactive daemon and
        the world panel all write this file, and every read is a write (see
        EliWorldAutonomyEngine.load), so two writers would create the same tmp,
        the first replace() would consume it, and the second raised
        FileNotFoundError mid-turn — which in turn knocked out the persona
        handoff's self-status injection.
        """
        stands_in = getattr(state, "_stands_in_for", None)
        if stands_in and Path(stands_in[0]).exists():
            log.warning("the world on disk at %s was not read (%s); it is left as it is and this change "
                        "is not saved over it", stands_in[0], stands_in[1])
            return False
        self._ensure()
        state.timestamp = time()
        payload = json.dumps(state.to_dict(), indent=2, ensure_ascii=False)
        with _SAVE_LOCK:
            fd, tmp_name = tempfile.mkstemp(
                dir=str(self.state_path.parent),
                prefix=f"{self.state_path.stem}.",
                suffix=".tmp",
            )
            tmp = Path(tmp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                    fh.flush()
                    os.fsync(fh.fileno())
                tmp.replace(self.state_path)
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    log.debug("world-state scratch cleanup failed", exc_info=True)
                raise
        return True

    def append_event(self, event: WorldEvent) -> None:
        path = self._ensure() / "events.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.__dict__, ensure_ascii=False) + "\n")
        _trim_jsonl(path)

    def append_action(self, action: WorldAction) -> None:
        path = self._ensure() / "actions.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(action.__dict__, ensure_ascii=False) + "\n")
        _trim_jsonl(path)

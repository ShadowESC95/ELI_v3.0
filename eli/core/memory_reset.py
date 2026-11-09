"""ELI memory / identity factory-reset — reusable core.

Clears every place ELI keeps learned state AND the user's identity, while preserving all DB schema
(tables/columns/FTS) and install config. Backs up first, and wipes nothing unless the backup is whole.

The reset is inverted: everything under data_dir() goes, except what is the installation or the
machine and holds none of the user's words (KEEP_TOP / KEEP_RUNTIME: models, voices, plugins, GPU
packs, hardware measurements, the licence trial, paired devices, the gaze calibration). A list of what to wipe always fell behind the
stores ELI grew: the executor's incidents, auto goals quoting the user's commands, the attention
queue, notes, documents, the proactive queue, the world journal, logs and backup copies of the user
database all survived a "factory reset". Also cleared outside data_dir():

  • config/settings.json identity fields              name, device labels, …
  • the persona overlay (persona.auto.txt)            ELI's learned overlay

Databases are emptied in place (rows wiped, schema kept, FTS reset, VACUUM). Their backup is taken
with SQLite's own backup API inside one read snapshot and checked (integrity_check, every table's
row count) before anything is wiped: copying the .sqlite3 file alone missed every commit still in
its -wal file while ELI held the database open. Other files are moved into the backup folder, so
backing them up costs no extra disk.

After wipe, rebuilds the full DB architecture via init_all_data().

Used by both tools/clear_memory.py (CLI) and the GUI Advanced settings page.
VACUUM is best-effort so a reset can run even while ELI holds DB connections
(the rows still commit; only space-reclaim is skipped under a lock).
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)

_NAME_KEYS = {"name", "preferred_name", "user_name", "nickname", "alias", "first_name"}
_IDENTITY_SETTINGS = {
    "user_name": "",
    "bluetooth_display_name": "",
    "device_custom_names": {},
    "audio_output_aliases": {},
}
# What a reset leaves under data_dir(): the installation and the machine, none of the user's words.
KEEP_TOP = frozenset({
    "models", "voices", "plugins", "bundles",          # installed models, voices, plugins, GPU packs
    "plugins_state.json",                               # which installed plugins are switched on
    "llama_runtime_smoke.json", "runtime_hardware_profile.json", "runtime_dynamic_budget.json",
    ".eli_trial_state.json",                            # the licence trial
    "devices",                                          # paired devices, their automations, the home mesh
    "eli_ar_avatar",                                    # the gaze tracker's calibration state
    "README.md",                                        # shipped with the data folder
    "_pytest",                                          # a checkout's test sandbox, not ELI's state
})
# Under runtime/: what was measured on this machine.
KEEP_RUNTIME = frozenset({
    "gpu_headroom.json", "gpu_xr.json", "whisper_gpu.json", "model_sizes.json", "load_probe.json",
    "capability_inventory.generated.json", "versions_seen.json",
})
_BACKUP_PREFIX = "_memory_backup_"
_BLANK_PERSONA_TEMPLATE = Path(__file__).resolve().parents[2] / "config" / "templates" / "persona.auto.template.txt"


def bases():
    """(data_dir, config_dir) resolved via eli.core.paths (dev + platformdirs)."""
    try:
        from eli.core.paths import get_paths, data_dir
        return Path(data_dir()), Path(get_paths().config_dir)
    except Exception:
        repo = Path(__file__).resolve().parents[2]
        return repo / "artifacts", repo / "config"


def _skip(p: Path) -> bool:
    return _BACKUP_PREFIX in str(p)


def _persona_overlay_paths(config: Path) -> List[Path]:
    paths: List[Path] = []
    try:
        from eli.core.paths import persona_auto_path
        p = persona_auto_path()
        if p.exists():
            paths.append(p)
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    alt = config / "persona.auto.txt"
    if alt.exists() and alt not in paths:
        paths.append(alt)
    return paths


def discover(base: Path, config: Path,
             keep_conversations: bool = False, keep_profile: bool = False) -> Dict[str, List[Path]]:
    """Existing targets by category. Everything under `base` is a target unless it is in KEEP_TOP
    or KEEP_RUNTIME (or kept by the options): the databases are emptied in place, every other file
    goes. keep_profile keeps the learned profile and settings identity, and scrubs the names from
    the world model and state caches instead of removing them; keep_conversations keeps the
    transcripts."""
    t: Dict[str, List[Path]] = {k: [] for k in (
        "dbs", "faiss", "name_caches", "runtime_wipe", "settings", "profile", "conversations", "files",
        "persona_overlay")}
    t["dbs"] = sorted(p for p in base.glob("db/*.sqlite3") if not _skip(p))
    db_files = set(t["dbs"]) | {Path(str(p) + sfx) for p in t["dbs"] for sfx in ("-wal", "-shm", "-journal")}
    name_caches = {base / "runtime" / "world_model.json", base / "state.json", base / "runtime" / "state.json"}
    if base.is_dir():
        for p in sorted(base.rglob("*")):
            if not p.is_file() or _skip(p) or p in db_files:
                continue
            rel = p.relative_to(base).parts
            if rel[0] in KEEP_TOP or (rel[0] == "runtime" and len(rel) == 2 and rel[1] in KEEP_RUNTIME):
                continue
            if rel[0] == "vectors":
                t["faiss"].append(p)
            elif rel[0] == "conversations":
                if not keep_conversations:
                    t["conversations"].append(p)
            elif rel[:2] == ("runtime", "users"):
                if not keep_profile:
                    t["profile"].append(p)
            elif p in name_caches:
                t["name_caches" if keep_profile else "runtime_wipe"].append(p)
            elif rel[0] == "runtime" or p.name.startswith(("runtime_", "user_info")):
                t["runtime_wipe"].append(p)
            else:
                t["files"].append(p)
    t["settings"] = [config / "settings.json"] if (config / "settings.json").exists() and not keep_profile else []
    t["persona_overlay"] = _persona_overlay_paths(config)
    return t


def counts(t: Dict[str, List[Path]]) -> Dict[str, int]:
    return {k: len(v) for k, v in t.items()}


# ── operations ──────────────────────────────────────────────────────────────
def clear_db(path: Path, keep_tables: "frozenset[str] | set[str] | None" = None) -> int:
    """Delete all rows (keep schema), reset FTS, best-effort VACUUM. Returns rows cleared."""
    keep = {str(t) for t in (keep_tables or ())}
    c = sqlite3.connect(str(path)); c.isolation_level = None
    try:
        c.execute("PRAGMA foreign_keys=OFF")
        rows = c.execute("SELECT name, sql FROM sqlite_master WHERE type='table'").fetchall()
        fts = [n for n, s in rows if "using fts" in (s or "").lower() and n not in keep]
        shadow = ("_data", "_idx", "_docsize", "_config", "_content")
        base = [n for n, s in rows if "using fts" not in (s or "").lower()
                and not n.endswith(shadow) and n not in keep]
        before = 0
        for n in base + fts:
            try:
                before += c.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0]
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        for n in base:
            if n == "sqlite_sequence":
                continue
            c.execute(f'DELETE FROM "{n}"')
        for n in fts:
            try:
                c.execute(f'INSERT INTO "{n}"("{n}") VALUES(\'delete-all\')')
            except Exception:
                try:
                    c.execute(f'DELETE FROM "{n}"')
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
        try:
            if keep:
                qs = ",".join("?" * len(keep))
                c.execute(f"DELETE FROM sqlite_sequence WHERE name NOT IN ({qs})", tuple(keep))
            else:
                c.execute("DELETE FROM sqlite_sequence")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        try:
            c.execute("VACUUM")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        return before
    finally:
        c.close()


def scrub_json_names(path: Path) -> int:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0

    def _walk(o):
        n = 0
        if isinstance(o, dict):
            for k, v in list(o.items()):
                if k.lower() in _NAME_KEYS and isinstance(v, str) and v.strip():
                    o[k] = ""; n += 1
                else:
                    n += _walk(v)
        elif isinstance(o, list):
            for x in o:
                n += _walk(x)
        return n

    n = _walk(d)
    if n:
        path.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return n


def clear_settings_identity(path: Path) -> bool:
    """Clear user-specific fields in settings.json (name, device labels, …)."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    changed = False
    for key, blank in _IDENTITY_SETTINGS.items():
        cur = d.get(key)
        if cur == blank:
            continue
        if isinstance(blank, dict):
            if cur:
                d[key] = dict(blank)
                changed = True
        elif str(cur or "").strip() != str(blank).strip():
            d[key] = blank
            changed = True
    if changed:
        path.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return changed


def clear_settings_name(path: Path) -> bool:
    """Backward-compatible alias — clears full identity block in settings."""
    return clear_settings_identity(path)


def reset_persona_overlay(template: Optional[Path] = None) -> bool:
    """Replace persona.auto.txt with the blank shipped template."""
    tpl = template or _BLANK_PERSONA_TEMPLATE
    text = ""
    if tpl.exists():
        text = tpl.read_text(encoding="utf-8")
    else:
        text = (
            "# Auto-updated persona overlay\n"
            "# Generated from habits, reflection, self-improvement, memory, and runtime signals.\n"
        )
    try:
        from eli.cognition.persona import write_auto_persona
        write_auto_persona(text)
        return True
    except Exception:
        try:
            from eli.core.paths import persona_auto_path
            dest = persona_auto_path()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text.rstrip() + "\n", encoding="utf-8")
            return True
        except Exception:
            return False


def rebuild_after_reset(verbose: bool = False) -> Dict[str, Any]:
    """Drop in-memory caches and rebuild the full blank DB architecture."""
    out: Dict[str, Any] = {"init_ok": True, "init_steps": 0, "init_failed": []}
    try:
        from eli.memory import _clear_memory_singletons
        _clear_memory_singletons()
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    try:
        from eli.core.init_data import init_all_data
        results = init_all_data(verbose=verbose)
        out["init_steps"] = len(results)
        out["init_failed"] = [n for n, ok, _ in results if not ok]
        out["init_ok"] = not out["init_failed"]
    except Exception as e:
        out["init_ok"] = False
        out["init_error"] = str(e)
    return out


def _table_counts(conn: sqlite3.Connection) -> Dict[str, int]:
    names = [n for (n,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {n: conn.execute(f'SELECT COUNT(*) FROM "{n}"').fetchone()[0] for n in names}


def backup_db(src_path: Path, dest: Path) -> Dict[str, int]:
    """Copy a live database with SQLite's backup API inside one read snapshot, so the commits still
    in its -wal file come too, then check the copy: integrity_check must say ok and every table must
    hold the rows the snapshot held. Returns those counts; raises when the copy is not whole."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(str(src_path), timeout=30.0)
    src.isolation_level = None
    try:
        src.execute("BEGIN")                       # one snapshot for the counts and the copy
        try:
            want = _table_counts(src)
            dst = sqlite3.connect(str(dest))
            try:
                src.backup(dst)
                ok = dst.execute("PRAGMA integrity_check").fetchone()
                got = _table_counts(dst)
            finally:
                dst.close()
        finally:
            src.execute("COMMIT")
    finally:
        src.close()
    if not ok or str(ok[0]).lower() != "ok":
        raise RuntimeError(f"backup of {src_path.name} failed its integrity check: {ok[0] if ok else 'no answer'}")
    if got != want:
        short = sorted(n for n in want if got.get(n) != want[n])
        raise RuntimeError(f"backup of {src_path.name} does not hold every row ({', '.join(short[:5])})")
    return want


def _backup_dest(bk: Path, base: Path, cat: str, p: Path) -> Path:
    return bk / (p.relative_to(base) if base in p.parents else Path(cat) / p.name)


def backup(t: Dict[str, List[Path]], base: Path, bk: Optional[Path] = None) -> Path:
    """Back up every target into one folder: databases through backup_db, the files edited in
    place (settings identity, name caches, persona overlay) copied. Raises on the first failure, so
    the caller can refuse to wipe. The files a reset removes are moved in by run_reset instead."""
    bk = bk or base / f"{_BACKUP_PREFIX}{time.strftime('%Y%m%d_%H%M%S')}"
    for p in t.get("dbs", []):
        backup_db(p, _backup_dest(bk, base, "dbs", p))
    for cat in ("settings", "name_caches", "persona_overlay"):
        for p in t.get(cat, []):
            dest = _backup_dest(bk, base, cat, p)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dest)
    return bk


_REMOVED = ("faiss", "profile", "conversations", "runtime_wipe", "files")


def run_reset(keep_profile: bool = False, keep_conversations: bool = False,
              do_backup: bool = True,
              keep_tables: "frozenset[str] | set[str] | None" = None,
              rebuild: bool = True) -> Dict[str, Any]:
    """Perform the reset. Returns a summary dict (safe to show in a UI). With do_backup, nothing is
    wiped unless every database backed up whole: then ok is False, the reason is in errors and
    summary['wiped'] is False."""
    base, config = bases()
    t = discover(base, config, keep_conversations, keep_profile)
    summary: Dict[str, Any] = {"ok": True, "backup": None, "db_rows": 0, "errors": [], "wiped": False,
                               "not_removed": [], "faiss_reset": 0, "profiles_removed": 0,
                               "conversations_removed": 0, "runtime_wiped": 0, "files_removed": 0,
                               "name_fields_blanked": 0, "settings_identity_cleared": False,
                               "settings_name_cleared": False, "persona_overlay_reset": False}

    bk: Optional[Path] = None
    if do_backup:
        bk = base / f"{_BACKUP_PREFIX}{time.strftime('%Y%m%d_%H%M%S')}"
        try:
            backup(t, base, bk)
            summary["backup"] = str(bk)
        except Exception as e:
            summary["errors"].append(f"backup: {e}")
            summary["ok"] = False
            summary["backup_incomplete"] = str(bk)
            log.warning("[MEMORY_RESET] backup failed, nothing wiped: %s", e)
            return summary

    # A running store would write its old index back over the wiped one.
    try:
        from eli.memory.vector_store import reset_vector_store
        reset_vector_store(discard=True)
        summary["vector_store_reset"] = True
    except Exception as e:
        summary["vector_store_reset"] = False
        summary["errors"].append(f"vector_store_reset: {e}")

    for p in t["dbs"]:
        try:
            summary["db_rows"] += clear_db(p, keep_tables=keep_tables)
        except Exception as e:
            summary["errors"].append(f"{p.name}: {e}")

    for cat in _REMOVED:
        for p in t.get(cat, []):
            try:
                if bk is not None:
                    dest = _backup_dest(bk, base, cat, p)
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), str(dest))
                else:
                    p.unlink()
            except Exception as e:
                summary["not_removed"].append(str(p))
                summary["errors"].append(f"{p.name}: {e}")
    _drop_empty_dirs(base)

    summary["name_fields_blanked"] = sum(scrub_json_names(p) for p in t["name_caches"])
    summary["settings_identity_cleared"] = any(clear_settings_identity(p) for p in t["settings"])
    summary["settings_name_cleared"] = summary["settings_identity_cleared"]
    summary["persona_overlay_reset"] = reset_persona_overlay()
    summary["faiss_reset"] = len(t["faiss"])
    summary["profiles_removed"] = len(t["profile"])
    summary["conversations_removed"] = len(t["conversations"])
    summary["runtime_wiped"] = len(t["runtime_wipe"])
    summary["files_removed"] = len(t["files"])
    summary["wiped"] = True

    if rebuild:
        summary["rebuild"] = rebuild_after_reset()

    summary["ok"] = not summary["errors"]
    return summary


def _drop_empty_dirs(base: Path) -> None:
    """Folders a reset emptied go too (deepest first); kept and backup folders stay."""
    if not base.is_dir():
        return
    for d in sorted((p for p in base.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
        rel = d.relative_to(base).parts
        if rel[0] in KEEP_TOP or _skip(d) or rel[0] == "db":
            continue
        try:
            if not any(d.iterdir()):               # not empty: something kept is in it
                d.rmdir()
        except OSError:
            log.debug("emptied folder %s left in place", d, exc_info=True)

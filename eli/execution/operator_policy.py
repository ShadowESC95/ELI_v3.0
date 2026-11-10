from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict

VALID_POLICY_MODES = {
    "proposal_only",
    "operator_supervised",
    "goal_driven",
    "observe_only",
}

def policy_path() -> Path:
    """Where the autonomy level is kept: ELI's data folder (runtime/), with its other runtime state.
    It was the install's own tree (project root/artifacts), which is the data folder only by default:
    where the data folder is set elsewhere (ELI_DATA_DIR, a test run's throwaway one) the level was
    read and written in the install, and every test run left an empty artifacts/runtime there."""
    from eli.core.paths import data_dir
    return Path(data_dir()) / "runtime" / "operator_policy.json"

def load_policy() -> Dict[str, Any]:
    p = policy_path()
    if not p.exists():
        return {
            "ok": True,
            "mode": "proposal_only",
            "updated_at": None,
            "actor": None,
            "reason": "",
            "path": str(p),
        }
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except Exception:
        raw = {}
    raw.setdefault("ok", True)
    raw.setdefault("mode", "proposal_only")
    raw.setdefault("updated_at", None)
    raw.setdefault("actor", None)
    raw.setdefault("reason", "")
    raw["path"] = str(p)
    return raw

def set_policy_mode(mode: str, actor: str = "operator_console", reason: str = "") -> Dict[str, Any]:
    mode = str(mode or "").strip()
    if mode not in VALID_POLICY_MODES:
        return {"ok": False, "error": f"invalid mode: {mode}", "valid": sorted(VALID_POLICY_MODES)}
    rec = {
        "ok": True,
        "mode": mode,
        "updated_at": time.time(),
        "actor": actor,
        "reason": reason or "",
        "path": str(policy_path()),
    }
    p = policy_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return rec

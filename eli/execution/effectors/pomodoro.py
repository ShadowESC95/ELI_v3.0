"""Pomodoro effector — start/stop/status focus timer (one handler, 3 actions)."""
from __future__ import annotations

from eli.utils.log import get_logger

log = get_logger(__name__)


def _h_pomodoro(a, args):
    try:
        from eli.core.paths import config_dir as _pcd
        import json as _pj, time as _pt
        pom_file = _pcd() / "pomodoro.json"

        def _pom_load():
            if pom_file.exists():
                try:
                    return _pj.loads(pom_file.read_text(encoding="utf-8"))
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
            return {}

        def _pom_save(d):
            pom_file.parent.mkdir(parents=True, exist_ok=True)
            pom_file.write_text(_pj.dumps(d), encoding="utf-8")

        if a == "POMODORO_STATUS":
            state = _pom_load()
            if not state.get("running"):
                msg = "No Pomodoro running."
            else:
                elapsed = int(_pt.time() - state.get("start", _pt.time()))
                duration = state.get("duration", 1500)
                remaining = max(0, duration - elapsed)
                msg = (f"Pomodoro running — {elapsed // 60}m {elapsed % 60}s elapsed, "
                       f"{remaining // 60}m {remaining % 60}s remaining.")
            return {"ok": True, "action": a, "content": msg, "response": msg}

        if a == "POMODORO_START":
            minutes = int(args.get("minutes") or args.get("duration") or 25)
            state = {"running": True, "start": _pt.time(), "duration": minutes * 60}
            _pom_save(state)
            msg = f"Pomodoro started: {minutes} minutes. Focus!"
            return {"ok": True, "action": a, "content": msg, "response": msg}

        if a == "POMODORO_STOP":
            state = _pom_load()
            if state.get("running"):
                elapsed = int(_pt.time() - state.get("start", _pt.time()))
                state["running"] = False
                _pom_save(state)
                msg = f"Pomodoro stopped after {elapsed // 60}m {elapsed % 60}s."
            else:
                msg = "No Pomodoro was running."
            return {"ok": True, "action": a, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


from eli.execution.effectors.registry import register as _register

for _action in ("POMODORO_START", "POMODORO_STOP", "POMODORO_STATUS"):
    _register(_action, _h_pomodoro)

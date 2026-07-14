"""Shell effector — RUN_CMD, SHELL_EXEC (security-gated via shell_gate)."""
from __future__ import annotations

import subprocess

from eli.utils.log import get_logger

log = get_logger(__name__)

def _execute_impl(*a, **k):
    from eli.execution.executor_enhanced import _execute_impl as _f
    return _f(*a, **k)


def _h_run_cmd(a, args):
    try:
        cmd = args.get("cmd") or args.get("command") or ""

        # ── SECURITY GATE ──
        # When cmd is a list, join to a string for pattern matching so that
        # ["bash", "-c", "..."] cannot bypass regex checks via str(list) repr.
        if isinstance(cmd, (list, tuple)):
            _cmd_str = " ".join(str(x) for x in cmd)
        else:
            _cmd_str = str(cmd)
        _cmd_low = _cmd_str.lower().strip()

        # ── SECURITY GATE — centralised, protected denylist ──
        # Destructive-pattern + dangerous-executable denylist lives in
        # eli/execution/shell_gate.py (a protected file apply_code_patch refuses to
        # auto-edit), so a self-improvement patch can't quietly strip it. Behaviour
        # is identical to the former inline gate: returns a block result unless ELI
        # Full Control is on, in which case the hard safety floor is lifted.
        from eli.execution.shell_gate import check_command as _shell_gate_check
        _gate = _shell_gate_check(cmd)
        if _gate is not None:
            _gate["action"] = a
            return _gate

        # Warn on sudo — allow but flag
        _needs_sudo = "sudo " in _cmd_low
        cwd = args.get("cwd") or None
        timeout = int(args.get("timeout", 30))
        shell = bool(args.get("shell", False))

        # Accept both ["ls", "/path"] and "ls /path"
        if isinstance(cmd, (list, tuple)):
            cmd_list = [str(x) for x in cmd if str(x).strip() != ""]
            if not cmd_list:
                return {"ok": False, "action": a, "error": "empty_cmd", "content": "", "response": ""}
            p = subprocess.run(
                cmd_list,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )
        else:
            cmd_str = str(cmd).strip()
            if not cmd_str:
                return {"ok": False, "action": a, "error": "empty_cmd", "content": "", "response": ""}
            import shlex
            argv = shlex.split(cmd_str)
            if not argv:
                return {"ok": False, "action": a, "error": "empty_cmd", "content": "", "response": ""}
            p = subprocess.run(
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
            )

        out = (p.stdout or "") + (p.stderr or "")
        return {"ok": p.returncode == 0, "action": a, "code": p.returncode, "content": out, "response": out}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_shell_exec(a, args):
    cmd = args.get("cmd") or args.get("command") or ""
    return _execute_impl("RUN_CMD", {"cmd": cmd, "shell": True})


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "RUN_CMD": _h_run_cmd,
    "SHELL_EXEC": _h_shell_exec,
}.items():
    _register(_action, _fn)

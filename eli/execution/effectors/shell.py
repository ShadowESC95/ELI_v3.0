"""Shell effector — RUN_CMD, SHELL_EXEC (security-gated via shell_gate)."""
from __future__ import annotations

import subprocess
import shutil

from eli.utils.log import get_logger

log = get_logger(__name__)

# Commands of cmd.exe itself, not programs: RUN_CMD runs them inside cmd on Windows
_WINDOWS_CMD_BUILTINS = frozenset({
    "dir", "echo", "type", "copy", "move", "ren", "rename", "md", "mkdir", "rd", "rmdir", "del", "erase",
    "cls", "set", "ver", "vol", "date", "time", "path", "pushd", "popd", "start", "assoc", "ftype",
    "mklink", "title", "color", "cd", "chdir",
})


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
            from eli.utils import platform_compat as _pc_rc
            if shell:
                # SHELL_EXEC: the OS's own shell, after the gate above has read the whole line
                argv = (["cmd.exe", "/d", "/s", "/c", cmd_str] if _pc_rc.WINDOWS
                        else ["/bin/sh", "-c", cmd_str])
            else:
                # Windows paths keep their backslashes (POSIX splitting ate them), and cmd's
                # built-ins (dir, type, echo...) are no programs, so they run inside cmd.
                argv = [t.strip('"') for t in shlex.split(cmd_str, posix=not _pc_rc.WINDOWS)]
                if (_pc_rc.WINDOWS and argv and not shutil.which(argv[0])
                        and argv[0].lower() in _WINDOWS_CMD_BUILTINS):
                    argv = ["cmd.exe", "/d", "/s", "/c", subprocess.list2cmdline(argv)]
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

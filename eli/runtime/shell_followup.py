"""Resolve deictic shell follow-ups ("run that command") from prior assistant output."""
from __future__ import annotations

import re
from typing import Optional

from eli.utils.log import get_logger

log = get_logger(__name__)

_RUN_PRIOR_SHELL_RE = re.compile(
    r"^\s*(?:please\s+)?(?:run|execute)\s+(?:that|this|the|it)(?:\s+command)?\s*\.?\s*$",
    re.I,
)

_CODE_FENCE_RE = re.compile(r"```(?:bash|sh|shell)?\n([\s\S]*?)```", re.I)


def is_run_prior_shell_request(text: str) -> bool:
    return bool(_RUN_PRIOR_SHELL_RE.match(str(text or "").strip()))


def _runnable(line: str) -> bool:
    """One command, and one that only reads: no second command after ; && || | or inside $( ) or
    backticks, no file written. What "run that command" may run is read the way the shell will
    read it (eli.execution.shell_gate), not by its first word."""
    from eli.execution.shell_gate import only_reads, read_line
    read = read_line(line)
    if read is None:
        return False
    commands, writes = read
    return len(commands) == 1 and not writes and only_reads(commands[0])


def extract_shell_from_assistant(text: str) -> str:
    """The first fenced shell line that is a single command that only reads, or "" when the block
    has none (the user is asked instead: a line that would change something is never picked)."""
    m = _CODE_FENCE_RE.search(str(text or ""))
    if not m:
        return ""
    lines = [
        ln.strip().rstrip(";")
        for ln in m.group(1).splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    runnable = [line for line in lines if _runnable(line)]
    for preferred in ("date", "timedatectl", "stat"):
        for line in runnable:
            if line.split()[0].lower() == preferred:
                return line
    for line in runnable:
        if line.split()[0].lower() != "echo":
            return line
    return ""


def _current_turn() -> tuple:
    """(session id, user id) of the turn being answered: the request's own, else the engine's."""
    try:
        from eli.kernel import request_context as _rc
        session, user = _rc.session_id_var.get(), _rc.user_id_var.get()
        if session is None or user is None:
            import sys
            _eng_mod = sys.modules.get("eli.kernel.engine")
            eng = getattr(_eng_mod, "_engine", None)  # read a running engine; never construct one
            if eng is not None:
                session = session if session is not None else getattr(eng, "session_id", None)
                user = user if user is not None else getattr(eng, "user_id", None)
        return str(session or ""), str(user or "")
    except Exception:
        log.debug("suppressed exception", exc_info=True)
        return "", ""


def _last_assistant_text() -> str:
    """ELI's last reply in this conversation. Another session's or another person's reply is
    never what "that command" points at, so with no session known there is nothing to run."""
    session, user = _current_turn()
    if not session:
        return ""
    try:
        from eli.memory.memory import get_memory
        turns = get_memory().get_recent_conversation(limit=8, session_id=session, user_id=user or None)
        for turn in reversed(turns or []):
            role = str((turn or {}).get("role") or "").lower()
            if role in ("assistant", "eli"):
                content = str((turn or {}).get("content") or "").strip()
                if content:
                    return content
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return ""


def resolve_run_prior_shell(user_text: str = "") -> Optional[str]:
    """Return a shell command to execute when the user says 'run that command'."""
    if not is_run_prior_shell_request(user_text):
        return None
    cmd = extract_shell_from_assistant(_last_assistant_text())
    return cmd or None

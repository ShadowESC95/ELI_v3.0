"""Centralised shell-command safety gate.

Extracted verbatim from the inline RUN_CMD handler in ``executor_enhanced.py`` so the
destructive-pattern + dangerous-executable denylist lives in ONE small, *protected*
module instead of buried in a 14k-line file — where a self-improvement patch could
quietly strip it. ``self_improvement.apply_code_patch`` lists this file in its
protected-path set, so the self-modifier can never auto-edit the shell denylist.

What it checks:
  - ELI Full Control (the GUI toggle, default off) lifts the hard safety floor — when
    on, everything below steps aside.
  - Otherwise the line is read the way the shell reads it (``read_line``): every command
    in it — after ``;`` ``&&`` ``||`` ``|`` ``&`` or a newline, inside ``( )``, and inside
    ``$( )``, backticks, ``<( )`` and ``>( )`` — and every file a ``>`` writes. The line is
    refused when a destructive pattern matches, when any of its commands is a denylisted
    executable, when it writes a file, or when any of its commands is not one that only
    reads (``only_reads``) and the owner has not allowed it by name in ELI_ALLOWED_CMDS
    (``eli.runtime.security``, the same fail-closed allowlist every other effector's
    subprocess goes through). Checking only the first word let ``ls; rm -rf ~/x`` and
    ``find ~ -delete`` through.

100% local, deterministic, never raises. ``check_command`` returns a block-result
dict (the caller adds ``action``) when the command must be refused, else ``None``.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

# ── Destructive / dangerous command patterns — block outright ──
_BLOCKED_PATTERNS = [
    r"\brm\s+(-[rf]+\s+)?/(?!tmp)",            # rm -rf / (except /tmp)
    r"\bmkfs\b",                                # format filesystem
    r"\bdd\s+.*of=/dev/",                       # dd to raw device
    r"\bchmod\s+777\s+/",                       # chmod 777 on root
    r"\b:\(\)\s*\{.*\}",                        # fork bomb
    r"\bshutdown\b|\breboot\b|\bpoweroff\b",    # system power control
    r"\bcurl\b.*\|\s*(?:ba)?sh\b",              # curl | bash (remote exec)
    r"\bwget\b.*\|\s*(?:ba)?sh\b",              # wget | bash
    r"\b/dev/sd[a-z]\b",                        # raw disk device access
    r"\biptables\s+-F\b",                       # flush firewall rules
    r"\bmv\s+/\S",                              # mv files from root
    r"\b(?:bash|sh|zsh|ksh|fish|dash)\s+-c\b",  # shell -c arbitrary exec
    r"\bpython\d*\s+-c\b",                      # python -c arbitrary exec
    r"\bperl\s+-e\b",                           # perl -e arbitrary exec
    r"\bruby\s+-e\b",                           # ruby -e arbitrary exec
    r"\bnc\b.*-e\b",                            # netcat reverse shell
    r"\b(?:ncat|netcat)\b.*-e\b",               # netcat variants
    r">\s*/etc/",                               # redirect to system config
    r">\s*/boot/",                              # redirect to boot partition
    r"\bchpasswd\b|\bpasswd\b\s+\w",           # password change
    r"\bvisudo\b|\bsudoers\b",                  # sudoers modification
    r"\bcrontab\s+-[re]\b",                     # crontab modification
    # Windows: the same harm in its own words (previously the gate knew only POSIX commands)
    r"\b(?:del|erase)\b.*\s/s\b",                # recursive delete
    r"\b(?:rd|rmdir)\b.*\s/s\b",                 # recursive directory removal
    r"\bformat(?:\.com)?\s+[a-z]:",              # format a drive
    r"\bremove-item\b.*-recurse\b",              # PowerShell recursive delete
    r"\breg(?:\.exe)?\s+delete\b",               # registry deletion
    r"\bvssadmin\b.*\bdelete\b",                # shadow copies (backups) deleted
    r"\bwbadmin\b.*\bdelete\b",                 # backups deleted
    r"\bbcdedit\b",                              # boot configuration
    r"\bcipher(?:\.exe)?\s+/w\b",                # wipe free space
    r"\b(?:invoke-expression|iex)\b",            # PowerShell arbitrary exec
    r"\b(?:cmd|powershell|pwsh)(?:\.exe)?\s+(?:/[ck]\b|-c(?:ommand)?\b|-enc)",  # shell -c arbitrary exec
    r">\s*[a-z]:\\windows\\",                     # redirect into the Windows folder
    # macOS
    r"\bdiskutil\s+(?:erase|zerodisk|secureerase|partitiondisk|reformat)",  # erase a disk
    r"\bcsrutil\s+disable\b",                    # system integrity protection off
    r"\bspctl\s+--master-disable\b",             # Gatekeeper off
]

# ── Denylisted executable names as argv[0] ──
_DENIED_EXECUTABLES = {
    "bash", "sh", "zsh", "ksh", "fish", "dash",  # shell interpreters
    "python", "python3", "python2",              # scripting engines
    "perl", "ruby", "node", "nodejs",            # more scripting engines
    "nc", "ncat", "netcat",                      # network tools
    "dd", "mkfs", "fdisk", "parted",             # disk tools
    "rm", "shred", "wipe",                       # destructive file ops
    "iptables", "ip6tables", "nftables",         # firewall manipulation
    # Windows
    "cmd", "powershell", "pwsh",                 # shell interpreters
    "cscript", "wscript", "mshta",               # scripting engines
    "diskpart", "format",                        # disk tools
    "bcdedit", "vssadmin", "wbadmin",            # boot config, backups
    # macOS
    "osascript",                                 # scripting engine
}

# ── Commands that only read, each with the arguments that would make it change something ──
# The one list of what runs without Full Control or the owner's allowlist (shell_followup and
# the router's "run that command" take theirs from here). A command not named here changes
# something, or might.
_READ_ONLY_COMMANDS: Dict[str, frozenset] = {
    name: frozenset() for name in (
        "cat", "head", "tail", "grep", "wc", "ls", "pwd", "stat", "echo", "uname", "uptime",
        "whoami", "id", "df", "du", "free", "diff", "which", "file", "nproc", "lscpu", "lsblk",
        "ps", "basename", "dirname", "realpath", "readlink", "test", "[", "true", "false", "printf",
        # Windows' own read-only commands
        "dir", "type", "where", "findstr", "ver", "systeminfo", "tasklist",
    )
}
_READ_ONLY_COMMANDS.update({
    "date": frozenset({"-s", "--set"}),
    "find": frozenset({"-delete", "-exec", "-execdir", "-ok", "-okdir",
                       "-fprint", "-fprint0", "-fprintf", "-fls"}),
    "hostname": frozenset({"-F", "--file", "-b", "--boot"}),
    "timedatectl": frozenset(),
})
# Commands that read when given no operand (or only these) and set what they are given otherwise:
# ``hostname box`` renames the machine, ``timedatectl set-time`` sets the clock.
_READ_ONLY_OPERANDS: Dict[str, frozenset] = {
    "hostname": frozenset(),
    "timedatectl": frozenset({"status", "show", "list-timezones", "timesync-status", "show-timesync"}),
}

# Words that run the rest of their command as a command of its own (``env rm ...``, ``sudo rm ...``).
_RUNS_ANOTHER = frozenset({
    "env", "sudo", "doas", "su", "runuser", "pkexec", "nohup", "nice", "ionice", "time", "timeout",
    "command", "builtin", "exec", "eval", "xargs", "stdbuf", "setsid", "chrt", "taskset", "watch",
    "strace", "ltrace", "unbuffer", "flock", "chroot", "start", "call",
})

# Reserved words that open or close a compound command; the command is what follows them.
_SHELL_KEYWORDS = frozenset({"!", "{", "}", "if", "then", "else", "elif", "fi", "do", "done",
                             "while", "until", "esac"})
# A loop or case header runs nothing itself (its words are a variable and a list).
_SHELL_HEADERS = frozenset({"for", "select", "case", "function"})

# Where a redirection may send output without writing anything.
_DISCARD_TARGETS = frozenset({"/dev/null", "/dev/stdout", "/dev/stderr", "nul"})

# Stands in for what a substitution or expansion produces: no command is named by it.
_UNKNOWN = "\x00"

_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\+?=")


def read_line(line: str, posix: Optional[bool] = None) -> Optional[Tuple[List[List[str]], List[str]]]:
    """The commands a shell line runs, each as its words, and the files it writes by redirection.

    Commands joined by ``;`` ``&`` ``&&`` ``||`` ``|`` or a newline, inside ``( )`` and inside
    ``$( )``, backticks, ``<( )`` and ``>( )``, each come out as a command of their own; a
    substitution inside a word leaves ``_UNKNOWN`` in its place. Leading ``NAME=value``
    assignments and compound-command keywords are not the command. None when the line cannot
    be read: an open quote or an unclosed bracket.

    ``posix`` is the shell's own quoting (default: this OS's). cmd.exe has no single quotes, its
    backslash is a path separator, and its escape is ``^``.
    """
    posix = (os.name != "nt") if posix is None else bool(posix)
    commands: List[List[str]] = []
    writes: List[str] = []
    s = str(line or "")
    n = len(s)

    def read(i: int, closer: Optional[str]) -> Optional[int]:
        words: List[str] = []
        word: Optional[List[str]] = None
        redirect: List[Optional[str]] = [None]   # "write" | "read" for the word that follows

        def end_word() -> None:
            nonlocal word
            if word is None:
                return
            w = "".join(word)
            word = None
            kind, redirect[0] = redirect[0], None
            if kind == "write":
                writes.append(w)
            elif kind is None:
                words.append(w)

        def end_command() -> None:
            end_word()
            argv = list(words)
            words.clear()
            while argv and argv[0] in _SHELL_KEYWORDS:
                argv.pop(0)
            if argv and argv[0] in _SHELL_HEADERS:
                return
            while argv and _ASSIGNMENT_RE.match(argv[0]):
                argv.pop(0)
            if argv:
                commands.append(argv)

        def add(text: str) -> None:
            nonlocal word
            if word is None:
                word = []
            word.append(text)

        def sub(j: int, close: str) -> Optional[int]:
            # a substitution: its commands are commands of the line; the word gets _UNKNOWN
            k = read(j, close)
            if k is None:
                return None
            add(_UNKNOWN)
            return k

        while i < n:
            c = s[i]
            if closer is not None and c == closer:
                end_command()
                return i + 1
            if c in " \t":
                end_word()
                i += 1
            elif c == "#" and word is None:
                while i < n and s[i] != "\n":
                    i += 1
            elif c == ("\\" if posix else "^"):
                if i + 1 < n and s[i + 1] != "\n":
                    add(s[i + 1])
                i += 2
            elif c == "'" and posix:
                k = s.find("'", i + 1)
                if k < 0:
                    return None
                add(s[i + 1:k])
                i = k + 1
            elif c == '"':
                i += 1
                add("")
                while True:
                    if i >= n:
                        return None
                    d = s[i]
                    if d == '"':
                        i += 1
                        break
                    if d == "\\" and posix and i + 1 < n:
                        add(s[i + 1])
                        i += 2
                    elif d == "$" and i + 1 < n and s[i + 1] == "(":
                        k = sub(i + 2, ")")
                        if k is None:
                            return None
                        i = k
                    elif d == "`":
                        k = sub(i + 1, "`")
                        if k is None:
                            return None
                        i = k
                    else:
                        add(d)
                        i += 1
            elif c == "`":
                k = sub(i + 1, "`")
                if k is None:
                    return None
                i = k
            elif c == "$" and i + 1 < n and s[i + 1] == "(":
                k = sub(i + 2, ")")
                if k is None:
                    return None
                i = k
            elif c == "$":
                add(_UNKNOWN)   # $NAME / ${NAME}: what it expands to is not known here
                add(c)
                i += 1
            elif c in "<>" and i + 1 < n and s[i + 1] == "(":
                k = sub(i + 2, ")")
                if k is None:
                    return None
                i = k
            elif c == "(":
                end_command()
                k = read(i + 1, ")")
                if k is None:
                    return None
                i = k
            elif c == ")":
                end_command()       # a stray one (a case pattern): the command before it ends
                i += 1
            elif c in ";\n|":
                end_command()
                i += 1
            elif c == "&" and not (i + 1 < n and s[i + 1] == ">"):
                end_command()
                i += 1
            elif c in "<>&":
                # an fd number right before the operator (2>) is not a word of the command
                if word is not None and "".join(word).isdigit():
                    word = None
                end_word()
                op = c
                i += 1
                while i < n and s[i] in "<>&|":
                    op += s[i]
                    i += 1
                if op.endswith("&") or (op.startswith(">&") or op.startswith("<&")):
                    # >&2, 2>&1, <&0: a copy of a descriptor, no file; >&file is a write
                    j = i
                    while j < n and s[j] in " \t":
                        j += 1
                    if j < n and (s[j].isdigit() or s[j] == "-"):
                        while j < n and (s[j].isdigit() or s[j] == "-"):
                            j += 1
                        i = j
                        continue
                if op in ("<", "<<", "<<<", "<<-"):
                    redirect[0] = "read"
                else:
                    redirect[0] = "write"
            else:
                add(c)
                i += 1
        if closer is not None:
            return None
        end_command()
        return i

    if read(0, None) is None:
        return None
    return commands, writes


def _command_name(word: str) -> str:
    """The bare name a command word runs: no directory, no Windows suffix, lower case."""
    base = re.split(r"[\\/]", str(word or ""))[-1].lower()
    return re.sub(r"\.(?:exe|com|bat|cmd)$", "", base)


def only_reads(argv: Sequence[str]) -> bool:
    """True when this one command, as written, only reads: it is a read-only command and none of
    its arguments is one that makes it change something."""
    if not argv:
        return False
    name = _command_name(argv[0])
    acting = _READ_ONLY_COMMANDS.get(name)
    if acting is None or _UNKNOWN in argv[0]:
        return False
    rest = [str(w) for w in argv[1:]]
    for w in rest:
        if any(w == flag or w.startswith(flag + "=") for flag in acting):
            return False
    if name in _READ_ONLY_OPERANDS:
        reads = _READ_ONLY_OPERANDS[name]
        if any(not w.startswith("-") and w not in reads for w in rest):
            return False
    return True


def _owner_allows(argv0: str) -> bool:
    """The owner's own allowlist (ELI_ALLOWED_CMDS) through the security manager every
    effector's subprocess already answers to; fail-closed when neither can be read."""
    try:
        from eli.execution.effectors._base import _get_security_manager, _shell_command_allowed_fallback
        sm = _get_security_manager()
        if sm is not None:
            return bool(sm.is_command_allowed(argv0))
        return bool(_shell_command_allowed_fallback(argv0))
    except Exception:
        return False


def _full_control() -> bool:
    try:
        from eli.core.full_control import is_full_control
        return bool(is_full_control())
    except Exception:
        return False


def _blocked_result(msg: str) -> Dict[str, Any]:
    return {"ok": False, "error": "security_blocked",
            "content": msg, "response": msg, "blocked": True}


def check_command(cmd: Union[str, Sequence[Any]],
                  full_control: Optional[bool] = None) -> Optional[Dict[str, Any]]:
    """Evaluate a command line against the safety floor.

    Returns a block-result dict (caller should set ``["action"]``) when the command
    must be refused, otherwise ``None``. ``full_control=None`` queries the live toggle.
    A list is one command's own argv (no shell reads it); a string is read as the shell
    reads it, every command in it checked.
    """
    # When cmd is a list, join to a string for pattern matching so that
    # ["bash", "-c", "..."] cannot bypass regex checks via str(list) repr.
    if isinstance(cmd, (list, tuple)):
        cmd_str = " ".join(str(x) for x in cmd)
    else:
        cmd_str = str(cmd)
    cmd_low = cmd_str.lower().strip()

    fc = _full_control() if full_control is None else bool(full_control)
    if fc:
        return None  # Full Control lifts the hard safety floor

    for pat in _BLOCKED_PATTERNS:
        if re.search(pat, cmd_low):
            return _blocked_result(f"Blocked dangerous command: {cmd_str[:60]}")

    if isinstance(cmd, (list, tuple)):
        argv = [str(x) for x in cmd if str(x).strip() != ""]
        commands, writes = ([argv] if argv else []), []
    else:
        read = read_line(cmd_str)
        if read is None:
            return _blocked_result(
                f"Not run: the line could not be read the way the shell reads it "
                f"(an open quote or bracket): {cmd_str[:60]}")
        commands, writes = read

    for argv in commands:
        name = _command_name(argv[0])
        named = [name] + ([_command_name(w) for w in argv[1:]] if name in _RUNS_ANOTHER else [])
        denied = next((w for w in named if w in _DENIED_EXECUTABLES), "")
        if denied:
            return _blocked_result(
                f"Execution of '{denied}' is not permitted via RUN_CMD for security reasons.")

    for target in writes:
        if target.strip().lower() not in _DISCARD_TARGETS:
            return _blocked_result(
                f"Not run: it writes to {target[:60]} and Full Control is off, so commands "
                f"may not write files.")

    for argv in commands:
        if only_reads(argv) or (_UNKNOWN not in argv[0] and _owner_allows(argv[0])):
            continue
        name = _command_name(argv[0]).replace(_UNKNOWN, "") or argv[0].replace(_UNKNOWN, "$")
        return _blocked_result(
            f"Not run: '{name}' is not a command that only reads, Full Control is off and "
            f"ELI_ALLOWED_CMDS does not list it.")

    return None

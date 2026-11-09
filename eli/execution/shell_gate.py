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

# ── Commands that only read ──
# The one table of what runs without Full Control or the owner's allowlist (shell_followup and the
# router's "run that command" take theirs from here). A command not named here changes something,
# or might. Each entry says how the command is read (``_rule``):
#   acting   options that make it change something (``-x`` / ``--long`` / ``--long=value``)
#   operands "any", or "none": an operand makes it act (``hostname box``, ``git branch new``)
#   listing  options under which operands are only patterns to list (``git branch --list 'f*'``)
#   needs    it reads only with one of these (``git config --get``, ``dpkg -l``)
#   options  options whose value is the next word (so the value is not taken for an operand)
#   sub      subcommands that only read, each a rule of its own; any other subcommand acts
#   bare     with ``sub``: whether the command with no subcommand only reads
#   order    with ``sub``: the program's own list it matches an abbreviation against, first match
#            wins (``ip a`` is ``ip address``, ``nmcli d`` is ``nmcli device``)
#   takes    short options that take their value attached (``-Iseconds``): in a cluster of short
#            options, the letters after one of these are its value, not options
#   long1    the program spells long options with one dash (``-name``, ``-brief``): no clusters
def _rule(acting=(), operands="any", listing=(), needs=(), options=(), sub=None, bare=True,
          order=(), takes="", long1=False) -> Dict[str, Any]:
    return {"acting": frozenset(acting), "operands": operands, "listing": frozenset(listing),
            "needs": frozenset(needs), "options": frozenset(options), "sub": dict(sub or {}),
            "bare": bool(bare), "order": tuple(order), "takes": frozenset(takes), "long1": bool(long1)}


_LEAF = _rule()
_SHOW_VERBS = {"show": _LEAF, "list": _LEAF, "lst": _LEAF, "ls": _LEAF}

_GIT = _rule(
    # -c and friends set config for this run (core.pager, alias.x=!cmd...): any command at all
    acting={"-c", "--config-env", "--exec-path"},
    options={"-C", "--git-dir", "--work-tree", "--namespace", "--super-prefix"},
    sub={
        **{name: _LEAF for name in (
            "status", "rev-parse", "ls-files", "ls-tree", "blame", "annotate", "shortlog",
            "describe", "cat-file", "rev-list", "whatchanged", "ls-remote", "count-objects",
            "show-ref", "for-each-ref", "name-rev", "merge-base", "var", "help", "version",
            "check-ignore", "check-attr", "cherry", "range-diff")},
        "log": _rule(acting={"--output"}),
        "diff": _rule(acting={"--output"}),
        "show": _rule(acting={"--output"}),
        "grep": _rule(acting={"-O", "--open-files-in-pager"}),
        "branch": _rule(
            acting={"-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C", "--copy", "-f",
                    "--force", "-u", "--set-upstream-to", "--unset-upstream", "--edit-description",
                    "-t", "--track", "--no-track", "--create-reflog", "--recurse-submodules"},
            operands="none", listing={"-l", "--list"},
            options={"--contains", "--no-contains", "--merged", "--no-merged", "--points-at",
                     "--sort", "--format"}),
        "tag": _rule(
            acting={"-a", "--annotate", "-s", "--sign", "-u", "--local-user", "-d", "--delete",
                    "-f", "--force", "-m", "--message", "-F", "--file", "-e", "--edit"},
            operands="none", listing={"-l", "--list", "-v", "--verify"},
            options={"--contains", "--no-contains", "--merged", "--no-merged", "--points-at",
                     "--sort", "--format", "-n"}),
        "config": _rule(
            acting={"--add", "--unset", "--unset-all", "--replace-all", "--rename-section",
                    "--remove-section", "-e", "--edit"},
            needs={"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color",
                   "--get-colorbool", "-l", "--list"}),
        "remote": _rule(sub={"show": _LEAF, "get-url": _LEAF}),
        "stash": _rule(sub={"list": _LEAF, "show": _LEAF}, bare=False),
        "reflog": _rule(sub={"show": _LEAF}),
        "worktree": _rule(sub={"list": _LEAF}, bare=False),
        "submodule": _rule(sub={"status": _LEAF, "summary": _LEAF}),
        "notes": _rule(sub={"list": _LEAF, "show": _LEAF}),
    })

_SYSTEMCTL = _rule(
    options={"-H", "--host", "-M", "--machine", "-t", "--type", "--state", "-p", "--property",
             "-n", "--lines", "-o", "--output", "--root"},
    sub={name: _LEAF for name in (
        "status", "show", "cat", "is-active", "is-enabled", "is-failed", "is-system-running",
        "list-units", "list-unit-files", "list-sockets", "list-timers", "list-jobs",
        "list-dependencies", "list-machines", "list-automounts", "list-paths", "show-environment",
        "get-default", "help")})

_LISTS = {"ls": _LEAF, "list": _LEAF, "inspect": _LEAF}
_CONTAINERS = _rule(
    # these pick the programs a container engine runs
    acting={"--runtime", "--conmon", "--network-cmd-path", "--hooks-dir"},
    options={"-c", "--context", "--connection", "-H", "--host", "--url", "--identity", "--config",
             "-l", "--log-level", "--root", "--runroot", "--storage-driver", "--tlscacert",
             "--tlscert", "--tlskey"},
    sub={
        **{name: _LEAF for name in (
            "ps", "images", "inspect", "logs", "version", "info", "stats", "top", "port", "diff",
            "history", "events", "search", "help")},
        "container": _rule(sub={**_LISTS, **{n: _LEAF for n in ("ps", "logs", "top", "port", "diff", "stats")}},
                           bare=False),
        "image": _rule(sub={**_LISTS, "history": _LEAF}, bare=False),
        "network": _rule(sub=_LISTS, bare=False),
        "volume": _rule(sub=_LISTS, bare=False),
        "pod": _rule(sub={**_LISTS, "ps": _LEAF, "top": _LEAF, "stats": _LEAF}, bare=False),
        "system": _rule(sub={"df": _LEAF, "info": _LEAF, "events": _LEAF}, bare=False),
        "compose": _rule(sub={n: _LEAF for n in ("ps", "ls", "logs", "config", "images", "top",
                                                 "version", "events")}, bare=False),
    })

# -o / -c hand apt a configuration of the caller's choosing (hooks among it)
_APT_ACTING = {"-o", "--option", "-c", "--config-file"}
_APT = _rule(acting=_APT_ACTING, sub={n: _LEAF for n in (
    "list", "show", "search", "policy", "depends", "rdepends", "showsrc", "changelog", "help")})
_APT_CACHE = _rule(acting=_APT_ACTING, sub={n: _LEAF for n in (
    "policy", "show", "search", "depends", "rdepends", "showpkg", "stats", "madison", "pkgnames",
    "showsrc", "unmet", "dotty", "xvcg")})
_DPKG = _rule(
    acting={"-i", "--install", "-r", "--remove", "-P", "--purge", "--configure", "--unpack",
            "--set-selections", "--clear-selections", "--clear-avail", "--forget-old-unavail",
            "--update-avail", "--merge-avail", "-A", "--record-avail", "--add-architecture",
            "--remove-architecture", "--triggers-only"},
    needs={"-l", "--list", "-L", "--listfiles", "-s", "--status", "-S", "--search", "-p",
           "--print-avail", "--get-selections", "--print-architecture",
           "--print-foreign-architectures", "-c", "--contents", "-I", "--info",
            "--compare-versions", "-C", "--audit", "-V", "--verify", "--version", "--help"},
    options={"--admindir", "--root", "--instdir"})

_PIP = _rule(
    # --python runs another interpreter, --log writes a file
    acting={"--python", "--log", "--log-file"},
    options={"--cache-dir", "--proxy", "--retries", "--timeout", "--exists-action",
             "--trusted-host", "--cert", "--client-cert", "--use-feature", "--use-deprecated"},
    sub={
        **{n: _LEAF for n in ("list", "show", "freeze", "check", "inspect", "debug", "help")},
        "index": _rule(sub={"versions": _LEAF}, bare=False),
        "config": _rule(sub={"list": _LEAF, "get": _LEAF, "debug": _LEAF}, bare=False),
        "cache": _rule(sub={"dir": _LEAF, "info": _LEAF, "list": _LEAF}, bare=False),
    })

_NPM = _rule(
    options={"--prefix", "--userconfig", "--globalconfig", "-w", "--workspace", "--registry",
             "--cache", "--loglevel"},
    sub={
        **{n: _LEAF for n in ("ls", "list", "ll", "la", "view", "v", "info", "show", "outdated",
                              "search", "find", "s", "se", "explain", "why", "help", "root",
                              "prefix", "query", "fund")},
        "config": _rule(sub={"get": _LEAF, "list": _LEAF, "ls": _LEAF}, bare=False),
        "audit": _rule(sub={"signatures": _LEAF}),
    })

_FLATPAK = _rule(sub={n: _LEAF for n in (
    "list", "info", "search", "remotes", "remote-ls", "remote-info", "history", "ps", "documents",
    "permissions", "help")})
_SNAP = _rule(sub={n: _LEAF for n in (
    "list", "info", "find", "search", "version", "services", "changes", "change", "tasks",
    "connections", "interfaces", "known", "model", "aliases", "warnings", "logs", "get", "help")})

_NMCLI = _rule(
    options={"-f", "--fields", "-g", "--get-values", "-e", "--escape", "-c", "--colors", "-m",
             "--mode", "-w", "--wait"},
    order=("general", "networking", "radio", "connection", "device", "agent", "monitor", "help"),
    sub={
        "general": _rule(sub={"status": _LEAF, "permissions": _LEAF,
                              "hostname": _rule(operands="none"), "logging": _rule(operands="none")}),
        "networking": _rule(sub={"connectivity": _LEAF}),
        "radio": _rule(sub={n: _rule(operands="none") for n in ("all", "wifi", "wwan")}),
        "connection": _rule(sub={"show": _LEAF}),
        "device": _rule(sub={"status": _LEAF, "show": _LEAF,
                             "wifi": _rule(sub={"list": _LEAF}), "lldp": _rule(sub={"list": _LEAF})}),
        "monitor": _LEAF,
        "help": _LEAF,
    })

_IP_SHOW = _rule(sub=_SHOW_VERBS)
_IP_SHOW_GET = _rule(sub={**_SHOW_VERBS, "get": _LEAF})
_IP = _rule(
    acting={"-b", "-ba", "-bat", "-batc", "-batch", "-force"},
    options={"-n", "-netns", "-f", "-family", "-l", "-loops", "-rc", "-rcvbuf"},
    long1=True,
    # iproute2's own object table, in its order: an abbreviation is the first it begins
    order=("address", "addrlabel", "maddress", "route", "rule", "neighbor", "neighbour", "ntable",
           "ntbl", "link", "l2tp", "fou", "ila", "macsec", "tunnel", "tunl", "tuntap", "tap",
           "token", "tcpmetrics", "tcp_metrics", "monitor", "xfrm", "mroute", "mrule", "netns",
           "netconf", "vrf", "sr", "nexthop", "mptcp", "ioam", "help", "stats"),
    sub={"address": _IP_SHOW, "maddress": _IP_SHOW, "route": _IP_SHOW_GET, "rule": _IP_SHOW,
         "neighbor": _IP_SHOW_GET, "neighbour": _IP_SHOW_GET, "link": _IP_SHOW,
         "netconf": _IP_SHOW, "monitor": _LEAF, "help": _LEAF})

_READ_ONLY_COMMANDS: Dict[str, Dict[str, Any]] = {
    name: _LEAF for name in (
        "cat", "head", "tail", "grep", "wc", "ls", "pwd", "stat", "echo", "uname", "uptime",
        "whoami", "id", "df", "du", "free", "diff", "which", "file", "nproc", "lscpu", "lsblk",
        "ps", "basename", "dirname", "realpath", "readlink", "test", "[", "true", "false", "printf",
        "dpkg-query",
        # Windows' own read-only commands
        "dir", "type", "where", "findstr", "ver", "systeminfo", "tasklist",
    )
}
_READ_ONLY_COMMANDS.update({
    "date": _rule(acting={"-s", "--set"}, takes="dfIr"),
    "find": _rule(acting={"-delete", "-exec", "-execdir", "-ok", "-okdir",
                          "-fprint", "-fprint0", "-fprintf", "-fls"}, long1=True),
    # ``hostname box`` renames the machine
    "hostname": _rule(acting={"-F", "--file", "-b", "--boot"}, operands="none"),
    "timedatectl": _rule(options={"-H", "--host", "-M", "--machine", "-p", "--property"},
                         sub={n: _LEAF for n in ("status", "show", "list-timezones",
                                                 "timesync-status", "show-timesync")}),
    "journalctl": _rule(acting={"--vacuum-size", "--vacuum-time", "--vacuum-files", "--rotate",
                                "--flush", "--sync", "--relinquish-var", "--smart-relinquish-var",
                                "--setup-keys", "--update-catalog"}),
    # -K kills the sockets it lists, -D writes a dump file
    "ss": _rule(acting={"-K", "--kill", "-D", "--diag"}, takes="fAFDN"),
    "git": _GIT,
    "systemctl": _SYSTEMCTL,
    "docker": _CONTAINERS,
    "podman": _CONTAINERS,
    "apt": _APT,
    "apt-cache": _APT_CACHE,
    "dpkg": _DPKG,
    "pip": _PIP,
    "pip3": _PIP,
    "npm": _NPM,
    "flatpak": _FLATPAK,
    "snap": _SNAP,
    "nmcli": _NMCLI,
    "ip": _IP,
})

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


def _acts(rule: Dict[str, Any], word: str) -> bool:
    """Whether this option, as written, is one that makes the command change something."""
    if any(word == flag or word.startswith(flag + "=") for flag in rule["acting"]):
        return True
    if not rule["long1"] and word.startswith("-") and not word.startswith("--") and len(word) > 2:
        for letter in word[1:]:            # a cluster of short options: -tK, -us
            if "-" + letter in rule["acting"]:
                return True
            if letter in rule["takes"]:     # the rest is that option's value
                break
    return False


def _named(rule: Dict[str, Any], word: str) -> str:
    """The subcommand a word names: itself, or for a program that takes abbreviations, the first
    name in its own list the word begins (what the program itself would run)."""
    if not rule["order"]:
        return word
    return next((name for name in rule["order"] if name.startswith(word)), "")


def _reads(rule: Dict[str, Any], words: Sequence[str]) -> bool:
    flags: List[str] = []
    operands: List[str] = []
    i = 0
    while i < len(words):
        w = str(words[i])
        if w == "--":
            operands.extend(str(x) for x in words[i + 1:])
            break
        if w.startswith("-") and w != "-":
            if _acts(rule, w):
                return False
            flags.append(w.split("=", 1)[0])
            i += 2 if w in rule["options"] else 1
            continue
        if rule["sub"]:
            sub = rule["sub"].get(_named(rule, w)) if _UNKNOWN not in w else None
            return sub is not None and _reads(sub, words[i + 1:])
        operands.append(w)
        i += 1
    if rule["sub"] and not rule["bare"]:
        return False
    if rule["needs"] and not any(f in rule["needs"] for f in flags):
        return False
    if operands and rule["operands"] == "none" and not any(f in rule["listing"] for f in flags):
        return False
    return True


def only_reads(argv: Sequence[str]) -> bool:
    """True when this one command, as written, only reads: the command is in the table of those
    that only read (``_READ_ONLY_COMMANDS``), and its subcommand, options and operands are ones
    that only read (``git status`` and ``systemctl status`` do, ``git push`` and ``systemctl
    restart`` do not)."""
    if not argv:
        return False
    rule = _READ_ONLY_COMMANDS.get(_command_name(argv[0]))
    if rule is None or _UNKNOWN in argv[0]:
        return False
    return _reads(rule, [str(w) for w in argv[1:]])


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


def _same_command(cmd: Union[str, Sequence[Any]], approved: Any) -> bool:
    """Whether ``approved`` is this very command: the same text (outer spaces aside), or the same
    argv. Anything else, a line with one more command in it included, is another command."""
    if approved is None:
        return False
    if isinstance(cmd, (list, tuple)):
        return (isinstance(approved, (list, tuple)) and bool(cmd)
                and [str(x) for x in cmd] == [str(x) for x in approved])
    text = str(cmd).strip()
    return bool(text) and isinstance(approved, str) and text == approved.strip()


def check_command(cmd: Union[str, Sequence[Any]],
                  full_control: Optional[bool] = None,
                  approved: Optional[Union[str, Sequence[Any]]] = None) -> Optional[Dict[str, Any]]:
    """Evaluate a command line against the safety floor.

    Returns a block-result dict (caller should set ``["action"]``) when the command
    must be refused, otherwise ``None``. ``full_control=None`` queries the live toggle.
    A list is one command's own argv (no shell reads it); a string is read as the shell
    reads it, every command in it checked.

    ``approved`` is the exact command the owner said yes to, passed by the in-process caller
    that asked him (a confirmed repair, say). It is never taken from an action's arguments.
    When it is this very command, his yes stands for this one command in place of Full Control
    and ELI_ALLOWED_CMDS: the executable denylist and the only-reads rule step aside (a
    confirmed ``python3 -m pip install x`` runs). The destructive patterns, a line that cannot
    be read and a file written by redirection are refused all the same.
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
    consented = _same_command(cmd, approved)

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

    if not consented:
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

    if consented:
        return None

    for argv in commands:
        if only_reads(argv) or (_UNKNOWN not in argv[0] and _owner_allows(argv[0])):
            continue
        name = _command_name(argv[0]).replace(_UNKNOWN, "") or argv[0].replace(_UNKNOWN, "$")
        return _blocked_result(
            f"Not run: '{name}' is not a command that only reads, Full Control is off and "
            f"ELI_ALLOWED_CMDS does not list it.")

    return None

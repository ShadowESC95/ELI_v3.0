"""Shell and UNIX command routing, disk usage, habits and plugins.

Recognises real command shapes (grep/ls/cat/du/…) and bare UNIX verbs, so
they run as commands instead of being chatted about.

Extracted verbatim from ``router_enhanced.route`` (v3 modularisation). This is an
ordered stage: it returns a route dict when one of its guards matches, or None to
let the next stage try. Order within the file is the legacy order and is load
bearing — an earlier guard deliberately shadows later ones.
"""
from __future__ import annotations

import os
import re

from eli.execution.routing._helpers import (
    _mk,
)


def route_shell_ops(text: str, raw: str, low: str, text_l: str):
    shell_prefixes = (
        "execute ",
        "run cmd ",
        "bash ",
        "shell cmd ",
        "$ ",
        "terminal: ")
    for pfx in shell_prefixes:
        if low.startswith(pfx):
            cmd = raw[len(pfx):].strip().strip("`")
            if cmd:
                return _mk("SHELL_EXEC", {
                           "cmd": cmd}, 0.95, matched_by="shell.prefix", entities={"cmd": cmd})

    m = re.match(r'^(grep\s+.+)$', raw, re.I)
    if m:
        return _mk("SHELL_EXEC", {"cmd": m.group(
            1)}, 0.95, matched_by="shell.grep", entities={"cmd": m.group(1)})

    if re.match(r'^(ls|cat|head|tail|find|diff|wc)\s+[~/\.]', raw, re.I):
        return _mk("SHELL_EXEC", {
                   "cmd": raw}, 0.92, matched_by="shell.safeish_builtin", entities={"cmd": raw})

    m = re.match(r'^`([^`]+)`$', raw)
    if m:
        return _mk("SHELL_EXEC", {"cmd": m.group(
            1)}, 0.93, matched_by="shell.backticks", entities={"cmd": m.group(1)})

    # 14b placeholder — proactive daemon patterns moved before section 13

    # 14c) CREATE FOLDER
    m = re.match(
        r"^(?:create|make|mkdir)\s+(?:a\s+)?(?:new\s+)?(?:folder|directory|dir)(?:\s+called|\s+named)?\s+(.+)$",
        raw,
        re.I)
    if m:
        raw_name = m.group(1).strip()
        # Extract explicit path like /home/... or ~/...
        path_m = re.search(r"((?:~|/[^\s]+(?:/[^\s]+)*)/[^\s]+)", raw_name)
        if path_m:
            folder_path = path_m.group(1)
            # folder name is word before "in" or the path basename
            name_m = re.match(r"(\S+)\s+(?:in|at|inside|under)", raw_name)
            folder_name = name_m.group(
                1) if name_m else os.path.basename(folder_path)
            full_path = os.path.join(
                os.path.expanduser(folder_path),
                folder_name) if not folder_path.endswith(folder_name) else os.path.expanduser(folder_path)
        else:
            # Just take first token as folder name
            folder_name = re.split(
                r"\s+(?:in|at|inside|under|on)\s",
                raw_name)[0].strip()
            full_path = os.path.expanduser(f"~/{folder_name}")
        return _mk("CREATE_FOLDER", {"name": full_path}, 0.95,
                   matched_by="fs.create_folder", entities={"name": full_path})

    # 14d) CLOSE / KILL APP
    m = re.match(r"^(?:close|quit|kill|exit)\s+(.+)$", raw, re.I)
    if m:
        name = m.group(1).strip()
        return _mk("CLOSE_APP", {"name": name}, 0.9,
                   matched_by="system.close_app")

    # ------------------------------------------------------------
    # 15) DEFAULT CHAT
    # ------------------------------------------------------------
    m = re.match(
        r"^(?:run|execute)\s+(?:the\s+)?(?:shell\s+)?command\s+(.+)$",
        raw,
        flags=re.I)
    if m:
        cmd = m.group(1).strip()
        if cmd:
            return _mk("SHELL_EXEC", {
                       "cmd": cmd}, 0.95, matched_by="shell.run_command", entities={"cmd": cmd})

    # Bare "run <unix_command>" — match common CLI tools
    _UNIX_CMDS = {"ls", "cd", "pwd", "cat", "head", "tail", "grep", "find", "wc",
                  "date", "df", "du", "free", "top", "ps", "kill", "chmod", "chown",
                  "cp", "mv", "rm", "mkdir", "rmdir", "touch", "echo", "which", "whoami",
                  "uname", "uptime", "hostname", "ip", "ifconfig", "ping", "curl", "wget",
                  "tar", "zip", "unzip", "apt", "pip", "npm", "git", "docker", "systemctl"}
    m = re.match(r"^(?:run|execute)\s+(\S+(?:\s+.*)?)$", raw, flags=re.I)
    if m:
        parts = m.group(1).strip().split()
        if parts and parts[0].lower() in _UNIX_CMDS:
            cmd = m.group(1).strip()
            return _mk("SHELL_EXEC", {
                       "cmd": cmd}, 0.93, matched_by="shell.run_bare_unix", entities={"cmd": cmd})

    m = re.match(r"^(?:list|ls)\s+(.+)$", raw, flags=re.I)
    if m:
        captured = m.group(1).strip()
        # "list my notes" is about saved notes, not a directory — route to LIST_NOTES
        # (the old ≤2-word fallback below turned "my notes" into LIST_DIR path='my notes.').
        if re.fullmatch(r"(?:my\s+|the\s+)?notes?\.?", captured, re.I):
            return _mk("LIST_NOTES", {}, 0.95, matched_by="notes.list_from_list_verb")
        # Extract only the path-like segment, not natural language
        path_match = re.search(r'([~/.][\w/._~-]+|/[\w/._~-]+)', captured)
        if path_match:
            path_value = path_match.group(1).strip().rstrip(".,;:")
            return _mk("LIST_DIR", {"path": path_value}, 0.98,
                       matched_by="fs.list_dir_bare", entities={"path": path_value})
        # If no path found but it's a single bare token, treat as relative path. Require
        # ONE token (no spaces) so multi-word prose ("my notes", "the reports") falls
        # through to CHAT/LLM-resolver instead of being mistaken for a directory name.
        elif len(captured.split()) == 1 and not any(w in captured.lower() for w in ("every", "all", "each", "the")):
            return _mk("LIST_DIR", {"path": captured.rstrip(".,;:")}, 0.90,
                       matched_by="fs.list_dir_bare", entities={"path": captured.rstrip(".,;:")})
        # Otherwise it's natural language about listing — fall through to CHAT

    # ── Disk usage queries → SHELL_EXEC ──────────────────────────────────────
    _du_match = re.search(
        r"(?:disk\s+usage|size|how\s+(?:big|large|much\s+space))\s+(?:of\s+|for\s+|in\s+)?([~/][\w/._~-]+)",
        raw, re.I
    )
    if _du_match or re.search(r"\bdu\s+[-shH]", raw):
        _du_path = _du_match.group(1).strip() if _du_match else ""
        if _du_path:
            import os as _os
            _du_path_exp = _os.path.expanduser(_du_path)
            _du_cmd = f"du -sh {_du_path_exp!r}"
        else:
            _du_cmd = raw
        return _mk("SHELL_EXEC", {"cmd": _du_cmd}, 0.95, matched_by="shell.disk_usage",
                   entities={"cmd": _du_cmd})

    # Habit status
    if re.match(r"^(?:show\s+)?habits?(?:\s+status)?$",
                low) or re.match(r"^(?:my\s+)?habits?$", low):
        return _mk("HABIT_STATUS", {}, 0.95, matched_by="habits.status")
    # "what habits have you detected/noticed (in my behaviour)", "which habits do
    # you have for me", "any habits you've spotted" → grounded HABIT_STATUS (reads
    # real habit_rules/events) instead of generic CHAT, which confabulated a
    # literal template placeholder "[list up to 3 habits…]" (user-reported).
    if re.search(r"\bhabits?\b", low) and re.search(
        r"\b(detect(?:ed)?|notic(?:e|ed)|spot(?:ted)?|observed|"
        r"have you (?:found|seen|got|detected|noticed)|do you have|"
        r"picked up|learned|recogni[sz]ed)\b",
        low,
    ):
        return _mk("HABIT_STATUS", {}, 0.95, matched_by="habits.detected_query")

    # ── Coverage patch: additional patterns before fallback ──

    # Power: shutdown / restart / reboot — ONLY as an actual command. The bare words
    # "restart"/"reboot" occur in ordinary conversation ("feeling better after a
    # restart?", "the server needs a restart"), so a bare keyword match here opened
    # gnome power settings on a greeting (user-reported). Require an imperative frame:
    # the power verb LEADS the message (optionally behind please/can-you), OR it takes
    # the machine as its explicit object — and never fire on a how-to/state question.
    _power_cmd = (
        re.match(r"^\s*(?:please\s+|can\s+you\s+|could\s+you\s+|pls\s+)?"
                 r"(?:shut\s*down|power\s*off|poweroff|restart|reboot)\b", low)
        or re.search(r"\b(?:shut\s*down|power\s*off|poweroff|restart|reboot)\s+"
                     r"(?:the\s+|my\s+|this\s+)?"
                     r"(?:computer|system|pc|machine|laptop|device|server|box)\b", low)
    )
    _power_question = re.match(r"^\s*(?:how|what|why|when|where|do i|should i|is it|does)\b", low)
    if _power_cmd and not _power_question:
        return _mk("OPEN_POWER_SETTINGS", {}, 0.95, matched_by="system.power")

    # System settings (bare)
    if low.strip() in ("system settings", "settings", "preferences"):
        return _mk("OPEN_SYSTEM_SETTINGS", {}, 0.98,
                   matched_by="system.settings")

    # Skip (bare) → NEXT_MEDIA
    if low.strip() in ("skip", "skip it", "next"):
        return _mk("NEXT_MEDIA", {}, 0.88, matched_by="media.next_legacy")

    # Self test
    if re.search(r"\bself[\s_-]*test\b",
                 low) or low.strip() in ("run self test", "self test"):
        return _mk("SELF_TEST", {}, 0.95, matched_by="self.self_test")

    # Self analyze (space separated)
    if low.strip() in ("self analyze", "self analyse", "self-analyze", "self-analyse"):
        return _mk("SELF_ANALYZE", {}, 0.95, matched_by="self.analyze")

    # Google X → web search
    if re.match(r"^google\s+(.+)$", raw, re.I):
        query = re.match(r"^google\s+(.+)$", raw, re.I).group(1).strip()
        return _mk("OPEN_BROWSER", {"query": query}, 0.95, matched_by="web.search",
                   entities={"query": query})

    # Look up X → web search (but NOT "look in memory")
    if re.match(r"^look\s+up\s+(.+)$", raw, re.I):
        query = re.match(r"^look\s+up\s+(.+)$", raw, re.I).group(1).strip()
        return _mk("OPEN_BROWSER", {"query": query}, 0.93, matched_by="web.search",
                   entities={"query": query})

    # Find X → web search (generic, after memory patterns already checked)
    _find_m = re.match(
        r"^find\s+(?:me\s+)?(?:a\s+|the\s+)?(.{10,})$",
        raw,
        re.I)
    if _find_m:
        query = _find_m.group(1).strip()
        if query:
            return _mk("OPEN_BROWSER", {"query": query}, 0.90, matched_by="web.search",
                       entities={"query": query})

    # Installed plugins / plugin status
    if re.search(r"\binstalled\s+plugins\b",
                 low) or low.strip() == "plugin status":
        return _mk("PLUGIN_LIST", {"scope": "installed"},
                   0.95, matched_by="plugin.list_installed")

    # Bare unix commands: whoami, uptime, hostname, etc.
    _BARE_UNIX = {
        "whoami",
        "uptime",
        "hostname",
        "uname",
        "date",
        "pwd",
        "df",
        "free"}
    if low.strip() in _BARE_UNIX:
        return _mk("SHELL_EXEC", {"cmd": raw.strip()}, 0.92, matched_by="shell.safeish_builtin",
                   entities={"cmd": raw.strip()})

    # Disk usage (bare, no path)
    if low.strip() in ("disk usage", "disk space", "storage space"):
        return _mk("SHELL_EXEC", {"cmd": "df -h"}, 0.95, matched_by="shell.disk_usage",
                   entities={"cmd": "df -h"})

    return None

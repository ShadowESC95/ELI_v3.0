# eli/tools/router_enhanced.py
# Deterministic intent router (upgraded)
# Goals:
# - Stable / redistributable / no machine-specific paths
# - Strong URL/path detection
# - Backward compatibility for legacy tests (e.g. STOP_MEDIA)
# - Canonicalized aliases + STT cleanup
# - Route metadata for debugging / future planner integration
# - Deterministic first, extensible structure second

from __future__ import annotations

def _eli_pipeline_trace(stage: str, **data):
    try:
        import json, time, os
        from pathlib import Path
        from eli.core.paths import get_paths
        out = Path(get_paths().project_root) / "ops" / "reports" / "pipeline_visibility" / "runtime_pipeline_trace.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "stage": stage, **data}, ensure_ascii=False, default=str) + "\n")
        # CLI print: silent by default (file write still happens for forensics).
        # Set ELI_PIPELINE_TRACE_VERBOSE=1 to restore the per-call print.
        if os.environ.get("ELI_PIPELINE_TRACE_VERBOSE", "0").lower() in {"1", "true", "yes", "on"}:
            _preview = {k: (v[:60] + "..." if isinstance(v, str) and len(v) > 60 else v) for k, v in data.items()}
            log.debug(f"[PIPELINE_TRACE] {stage} {_preview}")
    except Exception as _e:
        log.debug(f"[PIPELINE_TRACE_ERR] {stage}: {_e}")

from eli.execution.routing.contracts import (  # noqa: F401
    _eli_phase10_is_codebase_audit_request,
    _eli_phase11_enrich_pdf_route,
    _eli_phase38_final_memory_question_contract,
    _eli_phase38_followup_passthrough_contract,
    _eli_phase38_frontier_status_contract,
    _eli_phase38_identity_contract,
    _eli_phase38_identity_name_source_single_safe_contract,
    _eli_phase38_identity_scope_post,
    _eli_phase38_media_query_cleaner_post,
    _eli_phase38_persona_override_contract,
    _eli_phase38_personal_memory_summary_compat_post,
    _eli_phase38_route_precedence_contract,
    _eli_phase38_runtime_status_or_name_source_contract,
    _eli_phase38_tiny_fragment_post,
    _eli_phase39_identity_audit_contract,
)




import json
import os
import re
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

# Logger for safety-wrapper suppressed-exception reporting (used before the
# module's main logger exists; must be defined at the top of the file).
import logging as _swlog_logging
_SWLOG = _swlog_logging.getLogger(__name__)


# ============================================================
# PRE-COMPILED REGEX — module-level so route() pays zero compile cost.
# Python caches up to 512 recently-used patterns, but 379 inline calls
# still pay a dict-lookup on every invocation; module-level compiled
# patterns bypass the cache lookup entirely.
# ============================================================
_RE_MEDIA_CONTROL = re.compile(
    r"\b(play|pause|stop|resume|next|previous|skip|mute|unmute|volume|repeat|shuffle)\b", re.I)
_RE_OPEN_APP = re.compile(
    r"\b(open|launch|start|run|load)\b.{1,30}\b(app|application|program|browser|terminal|ide|editor)\b",
    re.I)
_RE_MEMORY_QUERY = re.compile(
    r"\b(remember|recall|memory|what do you know|stored|from memory|profile)\b", re.I)
_RE_SELF_AWARE = re.compile(
    r"\b(who are you|what are you|tell me about yourself|your (identity|persona|purpose|capabilities))\b",
    re.I)
_RE_REASONING_MODE = re.compile(r"\breasoning\s+mode", re.I)
_RE_ALL_MODES = re.compile(
    r"\b(all|every|each|how many|list|explain|full|describe|detail|difference|differ|compare"
    r"|what are|tell me about|tell me all|tell me everything|what do|how do"
    r"|modes?\s+you\s+have|modes?\s+does|all.*mode|every.*mode)\b", re.I)
_RE_RUNTIME_AUDIT = re.compile(
    r"\b(audit|inspect|diagnose|scan|what('s| is) wrong|broken|missing|wiring|pipeline)\b", re.I)
# Wall-clock requests. Shared by the TIME/DATE routes and by the long-question
# guard, which must not divert a clock question into CHAT — the model has no
# clock in context there and truthfully answers that it cannot read one, which
# reads as ELI denying a capability it demonstrably has.
_WALLCLOCK_TIME_PATTERNS = (
    r"\bwhat(?:'?s|\s+is)?\s+(?:the\s+)?time\b",
    r"\bcurrent time\b", r"\bclock\b", r"^time[?!.\s]*$", r"\btell me the time\b",
    r"\bwhat\s+time\s+is\s+it\b",
    # Compound asks — "the day and time", "date & time".
    r"\b(?:day|date)\s*(?:,)?\s*(?:and|&|\+)\s*(?:the\s+)?time\b",
    r"\btime\s*(?:,)?\s*(?:and|&|\+)\s*(?:the\s+)?(?:day|date)\b",
    # Tolerate words between "the" and "time" — "what the actual time is",
    # "what the fucking time is". The trailing is/it-is keeps this off phrases
    # like "what is the best time to call".
    r"\bwhat(?:'?s|\s+is)?\s+(?:the\s+)?(?:\w+\s+){0,2}time\s+(?:is|it\s+is|was)\b",
)
_WALLCLOCK_DATE_PATTERNS = (
    r"\bwhat(?:'?s|\s+is)?\s+(?:the\s+)?date\b",
    r"\bcurrent date\b",
    # today's date / today is the date — no greedy .* (would match "update")
    r"\btoday'?s?\s+(?:is\s+the\s+|the\s+)?date\b",
    r"^date[?!.\s]*$",
    r"\bwhat(?:'s| is) today\b",
    r"\bwhat day is it\b", r"\bwhat day is this\b",
    r"\bwhat'?s?\s+the\s+day\b", r"\bwhat'?s?\s+the\s+days\b",
    r"\bwhat days\b", r"\bwhat is the day\b", r"\bday is it\b",
    r"\bwhat(?:'?s|\s+is)?\s+(?:the\s+)?(?:\w+\s+){0,2}(?:date|day)\s+(?:is|it\s+is|was)\b",
)


def _is_wallclock_question(low: str) -> bool:
    return any(re.search(p, low) for p in _WALLCLOCK_TIME_PATTERNS + _WALLCLOCK_DATE_PATTERNS)


_RE_SYSTEM_STATS = re.compile(
    r"\b(cpu|ram|memory usage|disk|gpu|system stats|hardware|uptime|load)\b", re.I)
_RE_CHAT_FILLER = re.compile(
    r"^\s*(ok|okay|yes|no|yeah|nope|sure|thanks|thank you|got it|understood"
    r"|alright|cool|nice|great|fine)\s*[.!?]*\s*$", re.I)
_RE_GROUNDED_PIPELINE = re.compile(
    r"\b(cognition pipeline|input to output|every step|memory system|db tables"
    r"|runtime audit|diagnostic|diagnostics|full audit|fulltime audit|full.time audit"
    r"|system audit|run.*audit|do.*audit|wanna.*audit)\b", re.I)
_RE_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_RE_SHELL_CMD = re.compile(r"^(sudo|bash|sh|python|pip|apt|dnf|brew|npm|git)\s", re.I)

# Tracks the last successfully resolved filesystem path across router calls
_last_used_path: str | None = None


def _eli_latest_screenshot() -> str | None:
    """Return the path to the most recent screenshot image, or None.

    Searches the user's Pictures dir (where SCREENSHOT saves) and a few common
    fallbacks for the newest screenshot-named image by modification time.
    """
    import glob as _g
    dirs: list[str] = []
    try:
        from eli.utils import platform_compat as _pf
        dirs.append(str(_pf.user_pictures_dir()))
    except Exception:
        _SWLOG.debug("suppressed exception", exc_info=True)
    dirs += [
        os.path.expanduser("~/Pictures"),
        os.path.expanduser("~/Pictures/Screenshots"),
        os.path.expanduser("~/Desktop"),
    ]
    candidates: list[str] = []
    seen: set[str] = set()
    for d in dirs:
        if not d or d in seen or not os.path.isdir(d):
            continue
        seen.add(d)
        for pat in ("Screenshot*.png", "Screenshot*.jpg",
                    "*creenshot*.png", "*creenshot*.jpg"):
            candidates.extend(_g.glob(os.path.join(d, pat)))
    if not candidates:
        return None
    try:
        return max(set(candidates), key=lambda p: os.path.getmtime(p))
    except Exception:
        return None


# ============================================================
# STATIC CONFIG
# ============================================================


# ── Router helpers moved to eli.execution.routing._helpers (v3 modularisation) ──
from eli.execution.routing.intents import (
    INTENT_STAGES_EARLY,
    INTENT_STAGES_TAIL,
    ROUTE_NONE,
)
from eli.execution.routing._helpers import (  # noqa: F401
    COMMON_DIRS,
    WEBSITE_ALIASES,
    _known_dir_path,
    _extract_path_from_text,
    _extract_pdf_path,
    _extract_pdf_paths,
    _is_likely_path,
    _is_likely_url,
    _mk,
    _normalize_text,
    _normalize_url,
)


DESKTOP_APP_PRIORITY = {
    "spotify", "steam", "discord", "slack", "code", "vscode", "codium",
    "thunderbird", "paraview", "powerview", "rhythmbox", "vlc", "mpv",
    "totem", "gimp", "inkscape", "blender", "krita", "libreoffice",
    "onlyoffice", "wps", "zoom", "skype", "teams", "telegram", "signal",
    "element", "minecraft", "lutris", "heroic", "retroarch", "obs",
    "obs-studio", "audacity", "shotcut", "kdenlive", "openshot",
    "handbrake", "makemkv", "calibre", "evince", "okular", "qbittorrent",
    "transmission", "deluge", "filezilla", "thunar", "nautilus",
    "dolphin", "pcmanfm", "kitty", "alacritty", "konsole", "gnome-terminal",
    "xfce4-terminal", "terminator", "guake", "yakuake", "dropbox",
    "nextcloud", "owncloud", "syncthing", "kodi", "plex", "jellyfin",
    "emby", "stremio", "anki", "obsidian", "joplin", "typora", "marktext",
    "zotero", "mendeley", "pycharm", "intellij", "webstorm", "phpstorm",
    "android-studio", "eclipse", "netbeans", "sublime", "sublime-text",
    "atom", "emacs", "vim", "nvim", "neovim", "gitk", "gitg", "gitkraken",
    "github-desktop", "postman", "insomnia", "docker", "kubectl", "lens",
    "virtualbox", "vmware", "vagrant", "ansible", "terraform", "packer",
    "qgis", "grass", "rstudio", "jupyter", "jupyter-lab", "spyder",
    "matlab", "octave", "gnuplot", "fritzing", "kicad", "eagle", "freecad",
    "openscad", "solvespace", "celestia", "stellarium", "geogebra",
    "code-oss",
    "firefox", "chrome", "chromium",
    "terminal",
}


APP_ALIASES = {
    "codes": "code",
    "code-oss": "code",
    "vs code": "code",
    "visual studio code": "code",
    "term": "x-terminal-emulator",
    "terminal app": "x-terminal-emulator",
    "chrome browser": "chrome",
    "google chrome": "chrome",
    "chromium browser": "chromium",
    "firefox browser": "firefox",
    # Spelling corrections
    "calender": "gnome-calendar",
    "calander": "gnome-calendar",
    "calendar": "gnome-calendar",
    "calandar": "gnome-calendar",
    "settings": "gnome-control-center",
    "setting": "gnome-control-center",
    "calculator": "gnome-calculator",
    "calc": "gnome-calculator",
    "files": "nautilus",
    "file manager": "nautilus",
    "text editor": "gedit",
    "editor": "gedit",
    "system monitor": "gnome-system-monitor",
    "monitor": "gnome-system-monitor",
    "music player": "rhythmbox",
    "image viewer": "eog",
    "photos": "eog",
    "disks": "gnome-disks",
    "disk usage": "baobab",
    "screenshot": "gnome-screenshot",
}

MEDIA_APPS = [
    "spotify",
    "vlc",
    "chrome",
    "firefox",
    "mpv",
    "rhythmbox",
    "audacious",
    "youtube",
    "netflix",
    "soundcloud",
    "primevideo",
    "prime",
    "disneyplus",
    "disney",
    "hulu",
    "twitch",
]


# ============================================================
# LOW-LEVEL HELPERS
# ============================================================

from eli.execution.routing.weather import (  # noqa: F401
    _eli_weather_prepass,
)



_SHELL_PREPASS_CMDS = {
    "ls", "cd", "pwd", "cat", "head", "tail", "grep", "find", "wc",
    "date", "df", "du", "free", "top", "ps", "kill", "chmod", "chown",
    "cp", "mv", "rm", "mkdir", "rmdir", "touch", "echo", "which", "whoami",
    "uname", "uptime", "hostname", "ip", "ifconfig", "ping", "curl", "wget",
    "tar", "zip", "unzip", "apt", "pip", "npm", "git", "docker", "systemctl",
    "python", "python3", "bash", "sh", "env", "export", "source", "less", "more",
}


def _eli_shell_prepass(user_text: str):
    """Early-stage shell command detector — fires before OPEN_APP can steal 'run X'."""
    import re as _re
    m = _re.match(r"^\s*(?:run|execute)\s+(\S+(?:\s+.*)?)$", user_text.strip(), _re.I)
    if m:
        parts = m.group(1).strip().split()
        if parts and parts[0].lower() in _SHELL_PREPASS_CMDS:
            cmd = m.group(1).strip()
            return {
                "action": "SHELL_EXEC",
                "args": {"cmd": cmd},
                "confidence": 0.95,
                "meta": {"matched_by": "shell.prepass"},
            }
    return None








from eli.execution.routing.schedule import (  # noqa: F401
    _SCHEDULE_TIME_RX,
    _eli_schedule_prepass,
    _strip_schedule_time,
)



def _eli_multi_command_prepass(user_text: str):
    """Detect one utterance chaining MULTIPLE imperative commands → MULTI_COMMAND.

    'close steam and set an alarm for 7am' → MULTI_COMMAND{commands:[...]} which the
    executor runs in order. Strongly guarded (see command_splitter) so single commands
    and chatter are never split. Runs before schedule_prepass/portable_route so each
    segment is routed individually (a chained 'morning report for 7am' still schedules)."""
    try:
        from eli.runtime.command_splitter import split_commands
        segs = split_commands(user_text or "")
    except Exception:
        segs = None
    if not segs:
        return None
    return {
        "action": "MULTI_COMMAND",
        "args": {"commands": segs, "raw": str(user_text or "")},
        "confidence": 0.92,
        "meta": {"matched_by": "multi_command.prepass"},
    }






# Person-attribute nouns for personal-knowledge routing ("what do you know about
# my <attr>" → PERSONAL_MEMORY_SUMMARY). Kept as one shared group so the two
# question frames stay in sync.


def _eli_resolve_known_eli_file(low: str):
    """Map a reference to a well-known ELI file (by name, no full path) to its
    real absolute path, or None. Only returns files that actually exist, so a
    rename/removal is reflected immediately (no stale hardcoded claim). Lets
    "read your persona.auto.txt" / "is your settings file up to date" resolve to
    the real file instead of confabulating."""
    try:
        from pathlib import Path as _P
        root = _P(__file__).resolve().parents[2]  # project root
        candidates = []
        if re.search(r"persona\.?\s*auto|auto\.txt|persona overlay|auto[- ]persona", low):
            candidates.append(root / "eli" / "cognition" / "persona.auto.txt")
        if "persona" in low and "auto" not in low:
            candidates.append(root / "eli" / "cognition" / "persona.txt")
        if "settings.json" in low or re.search(r"\bsettings\s+(file|json)\b", low):
            candidates.append(root / "config" / "settings.json")
        if re.search(r"capability[\s_]manifest", low):
            candidates.append(root / "capability_manifest.json")
        for p in candidates:
            if p.exists():
                return str(p)
    except Exception:
        return None
    return None


def _clean_app_name(s: str) -> str:
    s = s.strip()
    s = re.sub(r'^(?:the|my|a|an)\s+', '', s, flags=re.I)
    s = re.sub(r'^(?:application|app)\s+', '', s, flags=re.I)
    s = re.sub(r'\s+located.*$', '', s, flags=re.I).strip()
    s = APP_ALIASES.get(s.lower(), s)
    return s








def _expand_common_dir(s: str) -> str:
    key = s.strip()

    # If already a full/home path, just expand ~ and return
    if key.startswith(("~", "/", "./")):
        return str(Path(key).expanduser())

    low = re.sub(r'^(?:the|my|a|an)\s+', '', key.lower()).strip()

    for dirname, path in COMMON_DIRS.items():
        if re.search(rf"\b{re.escape(dirname)}\b", low):
            rest = re.sub(rf"\b{re.escape(dirname)}\b", "", low).strip()
            rest = re.sub(r"\b(?:folder|directory|dir|files?|contents?)\b", "", rest).strip()
            # Clean leftover slashes/dots
            rest = rest.strip("/. ")
            if rest:
                return str(Path(path).expanduser() / rest)
            return str(Path(path).expanduser())

    # Singular phrasings ("download directory") the plural-keyed loop misses.
    _known = _known_dir_path(key)
    if _known:
        return str(Path(_known).expanduser())

    return COMMON_DIRS.get(low, str(Path(key).expanduser()))











def _canonical_media_command(low: str) -> Optional[str]:
    # Whole-word matching only. Substring matching fired media commands from
    # ordinary prose — e.g. "prev" inside "prevent", "mute" inside "commuter".
    # (Word boundaries don't save "next" in "the next log lands" — that's a real
    # word — so callers must ALSO gate on command shape/length, see below.)
    # Specific ordering matters
    if re.search(r"\bunmute\b", low):
        return "unmute"
    if re.search(r"\bmute\b", low):
        return "mute"
    if re.search(r"\b(?:pause|stop)\b", low):
        return "pause"  # generic media stop often means pause
    if re.search(r"\b(?:resume|play)\b", low):
        return "play"
    if re.search(r"\b(?:next|skip)\b", low):
        return "next"
    if re.search(r"\b(?:previous|prev|back)\b", low):
        return "previous"
    if re.search(r"\bshuffle\b", low):
        return "shuffle"
    if re.search(r"\b(?:repeat|loop)\b", low):
        return "repeat"
    return None


def _is_time_recall_query(raw: str, low: str) -> bool:
    patterns = [
        r"\bwhat did (?:we|i|you) (?:discuss|talk|say|mention)\b",
        r"\bwhat was (?:the )?last (?:thing|topic|subject|conversation)\b",
        r"\b(?:an?\s+hour\s+ago|earlier today|this morning|yesterday)\b",
        r"\bwhat did i (?:say|ask|tell you)\b",
    ]
    return any(re.search(p, low) for p in patterns)


def _looks_like_conversation_summary(low: str) -> bool:
    return any(re.search(p, low) for p in [
        r"^summari[sz]e\s+(?:the\s+)?(?:conversation|chat|discussion|what we talked about)",
        r"^summari[sz]e\s+(?:the\s+)?last\s+\d+\s+(?:messages|conversations|turns)",
    ])


# ============================================================
# ROUTER
# ============================================================
def _route_set_user_name(raw: str, low: str) -> Optional[Dict[str, Any]]:
    """Detect explicit name-setting statements and route to SET_USER_NAME."""
    import re as _re

    # Negation guard: bail immediately on "my name is NOT", "my name isn't",
    # "that is not my name", "not my name", "I am not", etc.
    _negation_patterns = (
        r"\bmy name is(?:n't| not)\b",
        r"\bmy name isn't\b",
        r"\bnot my name\b",
        r"\bthat(?:'s| is) not my name\b",
        r"\bdon't call me\b",
        r"\bdo not call me\b",
        r"\bi am not\b",
        r"\bi'm not\b",
        r"\bmy name is not\b",
        # "User is not my name" / "X is not my name"
        r"\bis not my name\b",
        # "that's not my name" / "that is not my name"
        r"\bthat(?:'?s?|\s+is)\s+not\s+my\b",
        # "stop calling me X" / "don't call me X"
        r"\bstop calling me\b",
        # Questions/references about what ELI calls the user are NOT name-set
        # commands: "why did you call me speak", "you called me X", "what did
        # you call me". Bail so they fall through to CHAT.
        r"\byou\s+call(?:ed)?\s+me\b",
        r"\b(?:why|what|when|who|how|did|do|does)\b[^.?!]*\bcall(?:ed)?\s+me\b",
    )
    for _npat in _negation_patterns:
        if _re.search(_npat, low, _re.IGNORECASE):
            return None

    # "my name is X", "call me X", "i'm X" / "i am X" (followed by end or punctuation)
    _patterns = (
        r"(?:my name is|my name's)\s+([A-Za-z][A-Za-z\-']{1,30})\b",
        r"call me\s+([A-Za-z][A-Za-z\-']{1,30})\b",
        r"(?:you can call me|please call me)\s+([A-Za-z][A-Za-z\-']{1,30})\b",
        r"^(?:i'?m|i am)\s+([A-Z][a-z]{2,24})\s*[.,!?]?\s*$",
        # Bare name as full message: require actual uppercase first char (typed, not STT-lowercased)
        r"^([A-Z][a-z]{3,24})\s*[.,!?]?\s*$",
    )
    # Common words / abbreviations that must never be treated as names.
    _bad = {"you", "eli", "okay", "ok", "sure", "fine", "good", "here",
            "yes", "no", "hi", "hey", "hello", "there", "done", "ready",
            "sorry", "thanks", "thank", "great", "right", "wrong", "not",
            # Media control reserved words — must never become a name
            "play", "pause", "resume", "stop", "next", "skip", "back",
            "previous", "mute", "unmute", "louder", "quieter", "volume",
            "shuffle", "repeat", "rewind", "forward", "restart",
            # Voice/system reserved words
            "start", "go", "cancel", "abort", "quit", "exit", "close",
            "open", "search", "find", "show", "hide", "run", "launch",
            # Command keywords — must never become a user name
            "help", "commands", "command", "list", "check", "status",
            "info", "about", "what", "when", "where", "who", "why", "how",
            # Common English words that STT might produce as single-word utterances
            "port", "read", "write", "note", "chat", "call", "ping", "send",
            "move", "copy", "load", "save", "take", "make", "time", "date",
            "week", "test", "demo", "data", "text", "file", "code", "mode",
            "this", "that", "with", "from", "into", "just", "also", "only",
            "more", "less", "most", "very", "some", "none", "both", "each",
            "then", "than", "when", "well", "will", "been", "have", "does",
            "done", "used", "like", "want", "need", "make", "knew", "know",
            "told", "tell", "said", "says", "came", "come", "goes", "went",
            "seem", "seen", "been", "gave", "give", "took", "take", "keep",
            # Common abbreviations / acronyms — never a personal name
            "eta", "ata", "ota", "asap", "fyi", "btw", "tbd", "tba",
            "aka", "tldr", "diy", "imo", "imho", "afk", "brb", "wtf",
            "omg", "lol", "idk", "nvm", "tbh", "irl", "gg", "rn",
            "api", "gui", "cli", "url", "cpu", "gpu", "ram", "ssd",}

    for i, pat in enumerate(_patterns):
        # Use the LAST occurrence: "my name is speak, my name is alex" must
        # resolve to the final assertion (alex), not a quoted earlier error.
        _matches = list(_re.finditer(pat, raw, _re.IGNORECASE))
        m = _matches[-1] if _matches else None
        if m:
            candidate = m.group(1).strip().rstrip(".,!?")
            _clow = candidate.lower()
            _has_vowel = any(c in "aeiou" for c in _clow)
            # Bare-word pattern (index 4): require actual uppercase first letter in raw text.
            # STT produces all-lowercase; a typed name would be capitalised.
            if i == 4 and (not raw or not raw[0].isupper()):
                continue
            if _clow not in _bad and len(candidate) >= 3 and _has_vowel:
                return _mk(
                    "SET_USER_NAME",
                    {"name": candidate},
                    0.97,
                    matched_by="identity.set_user_name",
                )
    return None


def _route_set_communication_style(raw: str, low: str) -> Optional[Dict[str, Any]]:
    """Detect explicit 'speak to me like X' / 'change your tone to X' requests and route to
    SET_COMMUNICATION_STYLE so the tone preference persists into the persona brief."""
    _pats = (
        r"(?:speak|talk)\s+to\s+me\s+(?:like|as|more|in a way that'?s?|in)\s+(.{2,80})",
        r"(?:respond|reply|answer|talk)\s+(?:to me\s+)?(?:as|like)\s+(.{2,80})",
        r"(?:change|set|update|make)\s+your\s+(?:tone|persona|voice|style|character|way you (?:speak|talk))\s+(?:to|into)?\s*(.{2,80})",
        r"(?:be|talk|speak)\s+(?:more\s+|less\s+)?((?:funny|funnier|blunt|blunter|formal|casual|serious|sarcastic|playful|concise|verbose|warm|friendly|professional|direct|witty|humorous|relaxed)(?:\s+and\s+[\w ]+)?)",
        r"(?:use|with)\s+(?:a\s+)?(.{2,60}?)\s+(?:tone|voice|humou?r|style)\b",
        r"i want you to (?:speak|talk|respond|be)\s+(.{2,80})",
    )
    if re.search(r"\b(?:reset|clear|default|normal)\b.*\btone\b", low):
        return _mk("SET_COMMUNICATION_STYLE", {"style": "reset"}, 0.95,
                   matched_by="persona.reset_communication_style")
    for pat in _pats:
        m = re.search(pat, raw, re.IGNORECASE)
        if m:
            style = m.group(1).strip().strip('."\'!?,').strip()
            if len(style) >= 2:
                # Disambiguate the actual SPOKEN voice from persona TONE. "use the
                # alan voice" / "use the HAL voice" name a real TTS voice, not a
                # tone — route those to SET_VOICE so they switch the voice instead
                # of setting a persona style. A phrase that ISN'T a real voice
                # ("use a sarcastic voice", "speak formally") stays persona tone.
                if re.search(r"\bvoice\b", low):
                    try:
                        from eli.runtime.voice_assets import resolve_voice_query
                        if resolve_voice_query(raw).get("voice"):
                            return _mk("SET_VOICE", {"query": raw}, 0.95,
                                       matched_by="voice.set.persona_disambig")
                    except Exception:
                        log.debug("voice disambig in persona route failed", exc_info=True)
                # NOTE: v2 additionally routes a palette-resolvable style to SET_TONE
                # here. v3 deliberately does not — test_voice_actions asserts that a
                # tone request which isn't a real voice stays persona tone, so
                # "be more formal" / "use a funny voice" remain
                # SET_COMMUNICATION_STYLE. The standalone tone route below still
                # catches directives the persona patterns don't name ("be comedic").
                return _mk(
                    "SET_COMMUNICATION_STYLE",
                    {"style": style},
                    0.95,
                    matched_by="persona.set_communication_style",
                )
    return None


def _route_grounded_runtime_intent(
        raw: str, low: str) -> Optional[Dict[str, Any]]:
    if not raw:
        return None

    # ── Identity / personality questions → CHAT (clean fixed) ───────
    _identity_phrases = (
        "who are you", "what are you", "tell me about yourself",
        "who do you think you are", "what is your purpose", "what do you want",
        "what is your name", "what's your name"
    )

    _memory_system_keywords = (
        "db", "database", "sqlite", "memory system",
        "cognition", "pipeline", "runtime"
    )

    _is_identity = any(p in low for p in _identity_phrases)
    _has_system_kw = any(k in low for k in _memory_system_keywords)

    if re.search(r"\b(what are you actually running on|running on right now|model|context size|gpu layers|threads|batch|provider)\b", raw, re.I):
        return _mk("CHAT", {"message": raw}, 0.99, matched_by="runtime.status.identity_grounded_chat", allow_chat_without_evidence=False)

    # identity precedence
    # Pure persona/character questions go to CHAT so ELI answers from its own
    # voice and memory. SELF_REPORT is reserved for *technical* runtime queries
    # (model path, gpu layers, provider, context size) — not "who are you".
    # "do you know who I am AND who you are" — a rhetorical/relational identity check that
    # wants a natural answer addressing BOTH (who you are / I'm ELI), not a profile dump.
    if (re.search(r"\bwho\s+(?:i\s+am|am\s+i)\b", raw, re.I)
            and re.search(r"\bwho\s+(?:you\s+are|are\s+you)\b", raw, re.I)):
        return _mk("CHAT", {"message": raw}, 0.96,
                   matched_by="identity.relational_both", allow_chat_without_evidence=True)

    if re.search(r"\b(who are you|what are you(?!\s+\w)|what is your name|what's your name|tell me about yourself)\b", raw, re.I):
        return _mk("CHAT", {"message": raw}, 0.99, matched_by="identity.persona_chat", allow_chat_without_evidence=True)

    if re.search(
        r"\b(who am i|do you know who i am|do you know me|do you remember me|"
        r"what is my name|what('s| is) my name|what do you know about me|"
        r"you do not know who i am|you don'?t know who i am|"
        r"don'?t you know who i am|don'?t you know me|"
        r"you don'?t know me|you have no idea who i am)\b",
        raw, re.I
    ):
        return _mk("USER_IDENTITY_SUMMARY", {}, 0.99, matched_by="identity.user_summary_preempt", allow_chat_without_evidence=False)

    # Last-turn trace questions require retrieval + live _prev_bus_result
    # injection. Routing to COGNITION_STATUS returned static source-code line
    # numbers as evidence and caused the 7B to confabulate confidence/agent
    # values. CHAT lets stages 3-11 engage and the persona handoff splice in
    # the real prior-turn AgentBus metadata.
    if re.search(r"\b(confidence in (?:your|my) last response|which agents contributed|what agents contributed|agents contributed|grounded trace|trace metadata|last turn trace|previous response trace)\b", raw, re.I):
        return _mk("EXPLAIN_LAST_RESPONSE", {}, 0.99,
                   matched_by="router.explain_last_response",
                   allow_chat_without_evidence=False)

    # Provenance of ELI's OWN last statement, in the words people actually use.
    # The route above only fires on jargon ("which agents contributed", "grounded
    # trace"). A live v2 session asked the plain version — "where are you getting
    # this information?" — which fell to fallback.chat at 0.60, and the model
    # invented a source: it claimed to be "keeping tabs on your project's state"
    # from "the Simulation Lab", none of which exists. A scan of all four SQLite
    # stores found those names only in ELI's own replies, written after it said
    # them. The bus records which agents contributed to the previous turn, so this
    # is answerable from real data and must never be narrated.
    #
    # Gated on a demonstrative (that/this/it) so it stays about ELI's own prior
    # statement: "how do you know my name" has its own route and must not be caught.
    if re.search(
        r"\bwhere\s+(?:are|did)\s+you\s+get(?:ting)?\s+(?:that|this|it|the\s+\w+)\b"
        r"|\bwhere\s+(?:is|did)\s+(?:that|this|it)\s+(?:com(?:e|ing)\s+)?from\b"
        r"|\bhow\s+do\s+you\s+know\s+(?:that|this|any\s+of\s+(?:that|this))\b"
        r"|\bwhat\s+are\s+you\s+basing\s+(?:that|this|it)\s+on\b"
        r"|\bwhat(?:'s|\s+is)\s+(?:that|this)\s+based\s+on\b"
        r"|\bsays\s+who\b",
        raw, re.I,
    ):
        return _mk("EXPLAIN_LAST_RESPONSE", {}, 0.95,
                   matched_by="router.explain_last_response.provenance",
                   need_grounding=True, allow_chat_without_evidence=False)

    if _is_identity and not _has_system_kw:
        return _mk("CHAT", {"message": raw}, 0.95, matched_by="identity.chat_classified")


def _route_plugin_bridge_prepass(raw: str, low: str):
    """
    High-priority plugin routing prepass.

    This must run before broad runtime/status routing because phrases like
    "system stats" and "say system check complete" otherwise get stolen by
    generic status/identity rules.
    """
    # Web plugin: explicit only. Generic "search for X" stays browser-routed.
    _web_m = re.match(
        r"^(?:web\s+search|search\s+the\s+web\s+for|search\s+online\s+for|online\s+search\s+for|internet\s+search\s+for)\s+(.+)$",
        raw,
        re.I,
    )
    if _web_m:
        query = _web_m.group(1).strip()
        if query:
            return _mk("WEB_SEARCH", {"query": query}, 0.98, matched_by="plugin.prepass.web_search",
                       entities={"query": query})

    # TTS plugin: must beat runtime/status rules when spoken text contains "system".
    _speak_m = re.match(
        r"^(?:speak|say|read\s+aloud|tts)\s+(.+)$",
        raw,
        re.I,
    )
    if _speak_m:
        text_to_say = _speak_m.group(1).strip()
        if text_to_say:
            return _mk("SPEAK", {"text": text_to_say}, 0.98, matched_by="plugin.prepass.tts_speak",
                       entities={"text": text_to_say})

    # System stats plugin: must beat generic runtime/status routing.
    if re.match(r"^(?:system\s+stats|system\s+statistics|resource\s+usage|show\s+system\s+stats)$", low):
        return _mk("SYSTEM_STATS", {}, 0.98, matched_by="plugin.prepass.system_stats")

    if re.match(r"^(?:cpu\s+usage|processor\s+usage|show\s+cpu\s+usage|how\s+busy\s+is\s+the\s+cpu)$", low):
        return _mk("CPU_USAGE", {}, 0.98, matched_by="plugin.prepass.cpu_usage")

    if re.match(r"^(?:ram\s+usage|memory\s+usage|show\s+ram\s+usage|show\s+memory\s+usage|how\s+much\s+ram\s+is\s+used)$", low):
        return _mk("RAM_USAGE", {}, 0.98, matched_by="plugin.prepass.ram_usage")

    # Hardware/system TEMPERATURE → grounded GPU status (which reports the real temp),
    # NEVER weather. "temperature"/"temp"/"thermal"/"how hot" about the gpu/cpu/card/
    # system/your hardware — or a bare "temperature reading(s)" sensor follow-up — was
    # being misrouted to GET_WEATHER by the LLM resolver, which then denied having data
    # it had just shown (user-reported: reported 51°C, then "I have no live sensor data").
    # Guarded against an actual weather/location query.
    if (re.search(r"\b(?:temperatures?|temps?|thermal|how\s+hot|degrees|°c)\b", low)
            and re.search(r"\b(?:gpu|cpu|card|chip|core|vram|system|your|hardware|"
                          r"sensor|readings?|head)\b", low)
            and not re.search(r"\b(?:weather|forecast|outside|outdoors|humid|rain|"
                              r"snow|sunny|tomorrow|in\s+[a-z]+land)\b", low)):
        # CPU-specific temp → CPU_USAGE (now reports temp); otherwise GPU_STATUS
        # (its grounded report includes the GPU temperature).
        if re.search(r"\bcpu\b", low) and not re.search(r"\bgpu\b", low):
            return _mk("CPU_USAGE", {}, 0.95, matched_by="system.hw_temperature")
        return _mk("GPU_STATUS", {}, 0.95, matched_by="system.hw_temperature")

    # Pomodoro plugin.
    if re.match(r"^(?:start\s+pomodoro|begin\s+pomodoro|pomodoro\s+start)(?:\s+timer)?$", low):
        return _mk("POMODORO_START", {}, 0.98, matched_by="plugin.prepass.pomodoro_start")

    if re.match(r"^(?:stop\s+pomodoro|end\s+pomodoro|cancel\s+pomodoro|pomodoro\s+stop)$", low):
        return _mk("POMODORO_STOP", {}, 0.98, matched_by="plugin.prepass.pomodoro_stop")

    if re.match(r"^(?:pomodoro\s+status|show\s+pomodoro|pomodoro)$", low):
        return _mk("POMODORO_STATUS", {}, 0.96, matched_by="plugin.prepass.pomodoro_status")

    # Notes plugin.
    _new_note_m = re.match(
        r"^(?:new\s+note|create\s+note|write\s+note)\s+(.+)$",
        raw,
        re.I,
    )
    if _new_note_m:
        note_text = _new_note_m.group(1).strip()
        if note_text:
            return _mk("NEW_NOTE", {"text": note_text, "content": note_text}, 0.98,
                       matched_by="plugin.prepass.notes_new", entities={"text": note_text})

    _search_notes_m = re.match(
        r"^(?:search\s+notes\s+for|find\s+note\s+about|find\s+notes\s+about|search\s+my\s+notes\s+for)\s+(.+)$",
        raw,
        re.I,
    )
    if _search_notes_m:
        query = _search_notes_m.group(1).strip()
        if query:
            return _mk("SEARCH_NOTES", {"query": query}, 0.98, matched_by="plugin.prepass.notes_search",
                       entities={"query": query})

    if re.match(r"^(?:list\s+notes|show\s+notes|show\s+my\s+notes|notes\s+list)$", low):
        return _mk("LIST_NOTES", {}, 0.98, matched_by="plugin.prepass.notes_list")

    # Bluetooth voice control — connect/pair/disconnect a device, or route audio to it.
    # ("connect my headphones", "pair the speaker", "disconnect my earbuds",
    #  "play through the kitchen speaker", "use my headphones for audio")
    _BT_WORDS = (r"headphones?|headset|earbuds?|earphones?|buds|airpods|speakers?|soundbar|"
                 r"bluetooth|smart\s*watch|earpiece|dongle")
    if not re.search(r"\b(wi-?fi|internet|network|vpn|server|hotspot|ethernet|the\s+call)\b", low):
        # Route audio to a device: "play/route/send audio through|to the <speaker/headphones>".
        _bt_audio = re.search(
            r"\b(?:play|route|send|switch|output|put)\s+(?:the\s+)?"
            r"(?:audio|sound|music|it|everything)?\s*(?:through|to|on|via|out\s+of)\s+"
            r"(?:my\s+|the\s+)?(.+)$", low)
        # Explicit form: "use my headphones for audio/output".
        _bt_audio2 = re.search(
            r"\buse\s+(?:my\s+|the\s+)?(.+?)\s+(?:for|as)\s+(?:the\s+)?"
            r"(?:audio|sound|output|speaker|playback)\b", low)
        _excl_media = re.search(r"\b(spotify|youtube|netflix|playlist|song|track|video|album|podcast|radio)\b", low)
        # "change/play/switch music|media|audio to kitchen speaker / device 1"
        #
        # The media noun used to be OPTIONAL in front of an open-ended (.+)$
        # tail, so ordinary prose matched: "fair play ON your explanation,
        # but…" parsed as play → on → <device> and sent the rest of the
        # paragraph to the Bluetooth stack as a device name — nine seconds of
        # radio probing, then "pair your headphones, not printers". Now the
        # utterance must open like a command, the tail must LOOK like a device
        # name (bounded, no sentence punctuation), and either the media is
        # named or the tail is (headphones/speaker/…).
        _audio_out = re.search(
            r"^(?:\s*(?:eli|hey\s+eli|ok\s+eli|please|can\s+you|could\s+you)[,\s]+)?"
            r"(?:change|switch|move|play|route|send|put|output)\s+(?:the\s+)?"
            r"(?P<noun>music|media|audio|sound|playback|it|everything)?\s*"
            r"(?:to|on|through|via|out\s+(?:of|to))\s+(?:the\s+|my\s+)?"
            r"(?P<dev>[a-z0-9][a-z0-9 '’\-]*?)\s*[.!]?\s*$",
            low,
        )
        if not _excl_media and _audio_out:
            _dev = (_audio_out.group("dev") or "").strip(" .!?")
            _dev_named = bool(_audio_out.group("noun")) or bool(re.search(_BT_WORDS, _dev))
            if (_dev and _dev_named and len(_dev.split()) <= 5
                    and not re.search(r"\b(wi-?fi|internet|network|vpn|server|hotspot)\b", _dev)):
                return _mk("SMART_HOME", {"device": _dev, "command": "use_for_audio", "bt": True},
                           0.95, matched_by="audio.output", entities={"device": _dev})
        if not _excl_media and _bt_audio and re.search(_BT_WORDS, _bt_audio.group(1)):
            _dev = _bt_audio.group(1).strip(" .!?")
            return _mk("SMART_HOME", {"device": _dev, "command": "use_for_audio", "bt": True},
                       0.95, matched_by="bluetooth.audio", entities={"device": _dev})
        if not _excl_media and _bt_audio2:
            _dev = _bt_audio2.group(1).strip(" .!?")
            if _dev:
                return _mk("SMART_HOME", {"device": _dev, "command": "use_for_audio", "bt": True},
                           0.95, matched_by="bluetooth.audio", entities={"device": _dev})
        # Connect / pair — only when a BT device word (or "bluetooth") is present.
        _bt_conn = re.search(r"\b(connect|pair|reconnect)\s+(?:to\s+)?(?:my\s+|the\s+)?(.+)$", low)
        if _bt_conn and (re.search(_BT_WORDS, _bt_conn.group(2)) or "bluetooth" in low):
            _dev = re.sub(r"\b(over|via|by|using|through)\s+bluetooth\b", "", _bt_conn.group(2)).strip(" .!?")
            _cmd = "pair" if _bt_conn.group(1).startswith("pair") else "connect"
            if _dev:
                return _mk("SMART_HOME", {"device": _dev, "command": _cmd, "bt": True},
                           0.95, matched_by="bluetooth.connect", entities={"device": _dev})
        # Disconnect — same guard.
        _bt_disc = re.search(r"\bdisconnect\s+(?:from\s+)?(?:my\s+|the\s+)?(.+)$", low)
        if _bt_disc and (re.search(_BT_WORDS, _bt_disc.group(1)) or "bluetooth" in low):
            _dev = re.sub(r"\b(over|via|by|using|from)\s+bluetooth\b", "", _bt_disc.group(1)).strip(" .!?")
            if _dev:
                return _mk("SMART_HOME", {"device": _dev, "command": "disconnect", "bt": True},
                           0.95, matched_by="bluetooth.disconnect", entities={"device": _dev})

    # Smart-home plugin.
    _smart_m = re.match(
        r"^(?:smart\s+home|home\s+automation)\s+(.+)$",
        raw,
        re.I,
    )
    if _smart_m:
        command = _smart_m.group(1).strip()
        if command:
            return _mk("SMART_HOME", {"command": command, "text": command}, 0.97,
                       matched_by="plugin.prepass.smart_home", entities={"command": command})

    # Generic smart-home device commands with NO "smart home" prefix: "turn off the lights",
    # "dim the bedroom lamp", "set the thermostat to 20". SMART_HOME is a CORE action (routed
    # here, run via ELI's own MQTT device server) — not a catalog plugin — so the router must
    # reach it directly. Scoped to real device nouns, so it never eats "turn off the music".
    _dev_cmd = re.search(
        r"\b(turn|switch|toggle|dim|brighten|set)\b[^.?!]{0,40}?\b"
        r"(lights?|lamps?|bulbs?|thermostat|heating|radiator|plugs?|sockets?|"
        r"outlets?|fans?|blinds|curtains|shades|dimmer)\b",
        low,
    )
    if _dev_cmd:
        _cmd = raw.strip()
        return _mk("SMART_HOME", {"command": _cmd, "text": _cmd}, 0.94,
                   matched_by="smart_home.device_command", entities={"command": _cmd})

    return None



# === ELI non-Quick/meta-continuity routing contract v1 ===
def _eli_meta_continuity_probe(text: str) -> bool:
    t = (text or "").lower()
    return any(x in t for x in (
        "short answer", "short answers", "terribly short", "what is with",
        "what's with", "where is your continuity", "continuity", "awareness",
        "memory", "lobotom", "constitutional", "reasoning mode", "quick mode",
        "fallback", "orchestration", "pipeline", "why did you", "what the fuck",
    ))
# === END ELI non-Quick/meta-continuity routing contract v1 ===

from eli.execution.routing.content_guards import (  # noqa: F401
    _eli_proof_of_reading_challenge,
    _eli_react_to_content_prepass,
)



from eli.execution.routing.web_info import (  # noqa: F401
    _eli_web_lookup_prepass,
    _sanitise_news_topic,
)



# Words that are never a real news topic on their own — question fragments,
# politeness tails, and generic news adjectives. A topic that reduces to one of
# these (or to nothing) means "no specific topic → general briefing".

# Carrier/question tokens that, if they survive trimming ANYWHERE in a capture, prove the
# regex grabbed an instruction or question fragment ("can you tell me the latest news" →
# "can you tell") rather than a real subject. A topic containing any of these is rejected,
# so the ask becomes a general (synthesised) briefing instead of a garbage-topic raw dump.






def _trailing_request_clause(raw: str) -> str:
    """The final sentence of a multi-sentence utterance, if it is a short request.

    The long-question guard measures the whole utterance, but the request often
    lives in the last clause: "Nope, i don't want to be stuck in that loop anymore
    than you do, broken now anyway. What's the morning report?" is 20 words, while
    the thing being asked for is 5. Returns "" when there is no distinct trailing
    clause, or when it is not short enough to be an unambiguous request — the
    caller then falls back to CHAT exactly as before.
    """
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", str(raw or "").strip()) if p.strip()]
    if len(parts) < 2:
        return ""                       # single sentence: nothing to narrow to
    tail = parts[-1]
    if len(tail.split()) > 8:
        return ""                       # still rambling; the guard should handle it
    return tail


def route(text: str, _clause_depth: int = 0) -> Dict[str, Any]:
    """`_clause_depth` is internal: the long-question guard re-routes a trailing
    clause through this same function, and must not recurse further than once."""

    raw, low = _normalize_text(text)
    text_l = low  # compatibility alias for legacy route guards
    _plugin_prepass = _route_plugin_bridge_prepass(raw, low)
    if _plugin_prepass is not None:
        return _plugin_prepass

    # React-to-pasted-content guard: opinion/evaluation requests over quoted or
    # referenced text are conversational. Run before any status/grounding regex
    # so quoted ELI-internal vocabulary can't hijack the turn into a data dump.
    _react_prepass = _eli_react_to_content_prepass(text, raw, low)
    if _react_prepass is not None:
        return _react_prepass



    # --- primary contract diagnostics: proof / latency / inference ---
    # These are not casual chat. They require grounded runtime/file evidence.
    # This block belongs inside the primary route() function, not as an
    # end-of-file wrapper around route().
    # A PROOF challenge aimed at ELI's *own* reading/scanning action — see
    # _eli_proof_of_reading_challenge. This reads the whole utterance for the
    # challenge frame so "prove you read the file" routes here while the
    # imperative "read the data in that file and prove it's right" does not.
    if _eli_proof_of_reading_challenge(low):
        return _mk(
            "GUI_RUNTIME_AUDIT",
            {
                "question": raw,
                "proof_requested": True,
                "audit_depth": "proof",
            },
            0.995,
            matched_by="router.primary_contract_diagnostics.gui_audit_proof",
            need_grounding=True,
            allow_chat_without_evidence=False,
            task_family="grounded_audit",
        )

    # A conversational request to explain or justify a statement ELI MADE is not a
    # runtime-diagnostics request — even when it quotes ELI's own words back (which
    # may incidentally contain 'context window', 'max_tokens', 'confidence', etc.).
    # Left ungated, "can you explain what you meant by 'the context window was full
    # of noise'?" force-routed to a raw runtime data-dump ("that's not what i
    # asked"). Real diagnostics ("why did you take 20 minutes", "what's my context
    # window size") don't ask to *explain a prior claim*, so they still route below.
    _explain_prior_claim = bool(
        re.search(r"\b(can|could|would)\s+you\s+explain\b", low)
        or re.search(r"\bexplain\s+(what\s+you\s+meant|that|this|your\s+(?:last|previous|prior)\s+(?:reply|answer|response|point))\b", low)
        or re.search(r"\bwhat\s+(?:do|did)\s+you\s+mean\b", low)
        or (re.search(r"\byou\s+(?:said|told\s+me|claimed|mentioned|wrote|meant)\b", low)
            and re.search(r"\b(explain|clarify|mean|what)\b", low))
    ) and not re.search(r"\b(audit|diagnos|prove|proof|\bscan\b)\b", low)

    if not _explain_prior_claim and re.search(
        r"\b(took you|took so long|why did you take|response time|slow response|that took ages|20 minutes|twenty minutes|you don't believe me|dont believe me|took over|took nearly|took just under)\b",
        low,
    ):
        return _mk(
            "EXPLAIN_COGNITION_RUNTIME",
            {
                "question": raw,
                "diagnostic_focus": "latency_timing",
            },
            0.995,
            matched_by="router.primary_contract_diagnostics.latency_timing",
            need_grounding=True,
            allow_chat_without_evidence=False,
            task_family="grounded_audit",
        )

    if not _explain_prior_claim and re.search(
        r"\b(current inference|inference issues|gguf issues|prompt overflow|context overflow|context window|sacrificing reasoning|sacrifice reasoning|model slow|llama slow|gpu_layers|gpu layers|max_tokens|n_ctx|batch size|llama_context)\b",
        low,
    ):
        return _mk(
            "EXPLAIN_COGNITION_RUNTIME",
            {
                "question": raw,
                "diagnostic_focus": "inference_runtime",
            },
            0.995,
            matched_by="router.primary_contract_diagnostics.inference_runtime",
            need_grounding=True,
            allow_chat_without_evidence=False,
            task_family="grounded_audit",
        )
    # --- end primary contract diagnostics ---


    # --- primary contract diagnostics: self-report recent updates ---
    # Questions about what checks/updates ELI performed are not casual chat.
    # They must route to grounded self-report evidence, otherwise GGUF invents
    # plausible maintenance activity.
    # EXCLUDE introspective/experiential questions — "have you noticed changes in your
    # memory continuity / how you feel / your awareness" is about ELI's subjective/cognitive
    # state, NOT a code/git-update report. Those got a raw git-commit dump (user-reported);
    # they belong in reflective CHAT/introspection, not the recent-updates contract.
    # ── Intent stages (early group) ───────────────────────────────────────────
    # Ordered guards lifted into eli.execution.routing.intents. First match wins,
    # exactly as the inline chain did; a stage returns None to pass.
    for _stage_name, _stage in INTENT_STAGES_EARLY:
        _hit = _stage(text, raw, low, text_l)
        if _hit is ROUTE_NONE:
            return None          # the stage ended routing outright
        if _hit is not None:
            return _hit


    # Persona / active-profile refresh + stale-overlay purge. Backs the
    # conversational offer "purge the stale context and update the active
    # profile" so an affirmation actually executes it (PERSONA_REFRESH =
    # clean_auto_persona + refresh_user_info). Placed before the user-info
    # refresh below so persona/active-profile phrasings reach the richer
    # action; "user info" / "profile report" still fall through to it.
    if re.search(
        r"\b(?:"
        r"purge\b[\w\s'-]*\b(?:stale|persona|overlay|context)\b|"
        r"(?:refresh|rebuild|update|clean|prune)\s+(?:the\s+)?(?:my\s+|your\s+)?(?:active\s+|auto[\s-]?)?(?:persona|profile|overlay)(?!\s+report\b)|"
        r"refresh\s+(?:the\s+)?persona\b"
        r")",
        text_l,
    ):
        return _mk(
            "PERSONA_REFRESH",
            {"reason": "user_request"},
            0.97,
            matched_by="identity.persona.refresh",
            allow_chat_without_evidence=False,
        )

    if re.search(
        r"\b(refresh|rebuild|update)\s+(?:the\s+)?(?:user\s+info|profile\s+report)\b",
        text_l,
    ):
        return _mk(
            "REFRESH_USER_INFO",
            {"force": True},
            0.99,
            matched_by="identity.user_info.refresh",
            allow_chat_without_evidence=False,
        )
    if re.search(r"\b(what do you know about me from memory|what do you remember about me|tell me everything you know about me from memory)\b", low):
        return _mk("CHAT", {"message": raw}, 0.98,
                   matched_by="memory.user_recall.chat_handoff",
                   allow_chat_without_evidence=False)

    if re.fullmatch(r"\s*who am i\??\s*", low):
        return _mk("MEMORY_RECALL", {"query": "identity"}, 0.99, matched_by="memory.identity.preempt")

    # --- DIRECT SCREENSHOT MATCH ---
    if raw.strip().lower() == "screenshot":
        return _mk("SCREENSHOT", {"region": "full"},
                   1.0, matched_by="io.screenshot_exact")
    # --- SCREENSHOT: direct capture before anything else ---
    if re.search(
            r"\b(take|capture|grab)\s+(a\s+)?(screenshot|screen\s*shot|screen\s*capture|screen|picture of screen)\b", raw, re.I):
        region = "area" if re.search(
            r"\b(area|region|selection|part)\b", raw, re.I) else "full"
        return _mk("SCREENSHOT", {"region": region}, 0.99,
                   matched_by="io.screenshot_direct", entities={"region": region})
    # --- WRITE_NOTE: colon format ---
    if re.match(r"^write note:\s*(.+)", raw, re.I):
        note_text = re.match(
            r"^write note:\s*(.+)",
            raw,
            re.I).group(1).strip()
        return _mk("WRITE_NOTE", {"text": note_text},
                   0.99, matched_by="notes.write_colon_direct")

    grounded = _route_grounded_runtime_intent(raw, low)
    if grounded is not None:
        return grounded

    # Known ELI files referenced by name ("is your persona.auto.txt up to date?
    # read it…", "what's in your settings file") → resolve real path and
    # SUMMARIZE_FILE. Placed BEFORE the long-question guard so the long phrasing
    # isn't swallowed as CHAT (which then confabulated instead of reading it).
    _known_file = _eli_resolve_known_eli_file(low)
    if _known_file and re.search(
        r"\b(read|summari[sz]e|summarise|what'?s?\s+(?:in|inside)|what\s+is\s+in|"
        r"contents?\s+of|show\s+me|tell\s+me\s+what|up[\s-]?to[\s-]?date|"
        r"look\s+at|inspect|check|open)\b",
        low,
    ):
        return _mk("SUMMARIZE_FILE", {"path": _known_file},
                   0.9, matched_by="analyze.known_eli_file")

    # Relational / wellbeing / concern questions aimed at ELI → CHAT (never a status dump
    # like AWARENESS_STATUS / REASONING_MODE_STATUS / META_DIAGNOSTIC). These are
    # conversational — the user checking in on ELI — and the LLM intent resolver otherwise
    # mis-maps them to status actions ("what is happening with you" → AWARENESS_STATUS).
    if re.search(
        r"\bare\s+you\s+(?:ok|okay|alright|all\s+right|good|fine|well)\b"
        r"|\bis\s+everything\s+(?:ok|okay|alright|all\s+right|fine)\b"
        r"|\beverything\s+(?:ok|okay|alright|all\s+right)\s*\??$"
        r"|\bwhat'?s?\s+wrong(?:\s+with\s+you)?\b"
        r"|\bwhat(?:'?s?|\s+is)\s+(?:happening|going\s+on|up)\s+with\s+you\b"
        r"|\beli[,\s]+what(?:'?s?|\s+is)\s+(?:happening|going\s+on|wrong)\b"
        r"|\bwhat(?:'?s?|\s+is)\s+(?:happening|going\s+on)\b(?=.*\byou\b)"
        # "what's going on/happening, eli" — vocative anywhere (incl. trailing).
        r"|\bwhat(?:'?s?|\s+is)\s+(?:happening|going\s+on)\b(?=.*\beli\b)"
        # Meta-provenance about a PRIOR reply ("is that hardcoded / deterministic / canned /
        # scripted / pre-written / did you generate that") → conversational, NOT a full
        # EXPLAIN_COGNITION_RUNTIME pipeline dump. ('explain how you work' is unaffected.)
        r"|\b(?:is|was|are|were)\s+(?:that|this|it|those|the\s+(?:answer|response|reply)|you)\b"
        r"[^?.!]*\b(?:hard[\s-]?coded|deterministic|scripted|canned|pre[\s-]?written|"
        r"a\s+canned\s+response|templated|generated\s+(?:live|on\s+the\s+fly))\b",
        low,
    ):
        return _mk("CHAT", {"message": raw}, 0.9, matched_by="chat.relational_concern")

    # MCP — external tool servers. Kept above the long-question guard for the same
    # reason as COMPUTER_USE: these are naturally phrased as full questions.
    if re.search(r"\b(?:mcp|external\s+tool)\s+(?:server\s+)?status\b", low) or \
       re.search(r"\bwhat\s+mcp\s+servers?\b", low):
        return _mk("MCP_STATUS", {}, 0.95, matched_by="mcp.status")
    if re.search(r"\b(?:list|show|what)\s+(?:are\s+)?(?:my\s+|the\s+)?mcp\s+tools?\b", low) or \
       re.search(r"\bmcp\s+tools?\b", low):
        return _mk("MCP_TOOLS", {}, 0.94, matched_by="mcp.tools")
    _mcpm = re.search(r"\b(?:run|call|use|invoke)\s+(?:the\s+)?mcp\s+tool\s+(?P<t>[\w.\-]+)", low)
    if _mcpm:
        return _mk("MCP_CALL", {"tool": _mcpm.group("t")}, 0.94, matched_by="mcp.call")

    # COMPUTER_USE — a goal that needs several observe/act rounds on the real screen,
    # rather than one locate-and-click. Placed BEFORE the long-question guard because
    # these goals are naturally long sentences ("can you open settings and turn dark
    # mode on for me?" is 12 words with a question mark) and would otherwise be
    # swallowed into CHAT, which is precisely the case that used to end in ELI
    # describing an action it never took.
    _mcpm = re.search(
        r"\b(?:use|drive|take\s+over|control|operate)\s+(?:my\s+|the\s+)?"
        r"(?:computer|screen|desktop|machine|mouse\s+and\s+keyboard)\b(?:\s+(?:and|to)\s+(?P<g1>.+))?",
        low)
    if not _mcpm:
        _mcpm = re.search(r"(?P<g1>.+?)\s+for\s+me\s+on\s+(?:my|the)\s+screen\b", low)
    if not _mcpm:
        _mcpm = re.search(r"^(?:computer[\s_-]?use)\b[:,]?\s*(?P<g1>.+)$", low)
    if _mcpm:
        goal = (_mcpm.groupdict().get("g1") or "").strip(" .?!,")
        if not goal:
            goal = raw.strip()
        return _mk("COMPUTER_USE", {"goal": goal}, 0.93,
                   matched_by="computer_use.goal", entities={"goal": goal})

    # Long-question guard. Keeps a long, rambling question away from the LLM intent
    # resolver (which mis-maps them to arbitrary actions) by returning CHAT at 0.85 —
    # confident enough that the engine never calls the resolver.
    #
    # It sits above the deterministic keyword routes and shadows all of them, so the
    # exemption list grew one bug at a time: wallclock questions were carved out after
    # long clock questions were answered worse than terse ones. The comment stated the
    # principle generally — "asking completely must not be answered worse than asking
    # tersely" — while the code applied it to exactly one case.
    #
    # Live example that motivated the general fix: "Nope, i don't want to be stuck in
    # that loop anymore than you do, broken now anyway. What's the morning report?"
    # — 20 words, so the guard fired and the user got small-talk instead of the report
    # they asked for in the final clause.
    #
    # Deferring the guard until after all the keyword routes was tried and REJECTED:
    # it let loose routes catch conversational text. The length heuristic is earning
    # its keep against those.
    #
    # What is actually wrong is the unit of measurement. The guard measures the whole
    # utterance, but the request lives in a CLAUSE. A long preamble followed by a
    # crisp question should route on the crisp question. So: when the guard would
    # fire, re-route the trailing sentence alone, and accept that only if it is short
    # and produces a confident non-CHAT action. Anything vaguer still falls to CHAT.
    if (("?" in raw or "!" in raw) and len(low.split()) >= 12
            and not _is_wallclock_question(low)
            and not re.match(
            r"^(open|access|initiate|fabricate|check|run|execute|type|press|pause|resume|play|next|previous|stop|mute|unmute|read|list|show|write|add|analyse|analyze|improve)\b", low)):
        _tail = _trailing_request_clause(raw) if _clause_depth == 0 else ""
        if _tail:
            _tail_route = route(_tail, _clause_depth=_clause_depth + 1)
            _tail_action = str((_tail_route or {}).get("action") or "").upper()
            _tail_conf = float((_tail_route or {}).get("confidence") or 0.0)
            if _tail_action not in ("CHAT", "", "NOOP", "UNKNOWN") and _tail_conf >= 0.9:
                _tail_meta = dict((_tail_route or {}).get("meta") or {})
                _tail_meta["matched_by"] = (
                    f"{_tail_meta.get('matched_by', '?')}+long_question_clause")
                _tail_route["meta"] = _tail_meta
                return _tail_route
        return _mk("CHAT", {"message": raw}, 0.85,
                   matched_by="chat.long_question_guard")

    if not raw:
        return _mk("CHAT", {"message": ""}, 0.2, matched_by="empty_input")

    # "timestamps / dates / when for those articles/stories/news" is a FOLLOWUP on the news
    # just shown — NOT a calendar query. The LLM resolver used to mis-route it to a calendar/
    # events action ("Calendar integration is not configured"). Keep it on the conversational
    # news context (grounded followup) and away from calendar.
    if (re.search(r"\b(time-?stamps?|dates?|times?|when)\b", low)
            and re.search(r"\b(articles?|stories|story|headlines?|news)\b", low)
            and not re.search(r"\b(calendar|event|appointment|meeting|schedule|"
                              r"alarm|reminder|timer|set\s+a)\b", low)):
        return _mk("CHAT", {"message": raw}, 0.9, matched_by="news.timestamps_followup",
                   allow_chat_without_evidence=True)

    # ── NEWS / WEB LEARNING (before generic web/search routes) ───────────────
    if re.search(r"\b(fetch|get|pull|download|update)\b.{0,25}\bnews\b"
                 r"|\bnews\b.{0,20}(fetch|get|update|refresh)\b", low):
        _tm = re.search(r"(?:about|on|for|topic[:\s]+)\s*([a-z][a-z ]{2,30})", low)
        _topic = _sanitise_news_topic(_tm.group(1)) if _tm else ""
        return _mk("NEWS_FETCH",
                   {"mode": "fetch_and_show", "topic": _topic, "sources": ["all"]},
                   0.96, matched_by="news.fetch")

    # Unambiguous news asks — always news (minus chatty interjections).
    _news_explicit = re.search(
        r"\bwhat(?:'?s?|\s+is)\s+the\s+news\b"
        r"|\bcurrent\s+events?\b|\blatest\s+news\b"
        r"|\btoday'?s?\s+news\b|\bworld\s+news\b|\bnews\s+today\b"
        r"|\bany\s+news\b|\bwhat'?s?\s+new\b"
        r"|\bthe\s+news\b|\bheadlines?\b|\bnews[,\s]*eli\b|\beli[,\s]*news\b", low)
    # "what's happening / what's going on" is AMBIGUOUS: relational ("what's
    # going on WITH YOU") far more often than news in a companion assistant.
    # Only treat it as news when it's explicitly world/time-scoped, and never
    # when it's aimed at ELI or the user (you/your/me/here/this/that/blaming…).
    # Otherwise a casual "what's going on" detonates a 55s news dump mid-chat.
    _amb = re.search(r"\bwhat(?:'?s?|\s+is)\s+(?:happening|going\s+on)\b", low)
    _world_scope = re.search(
        r"\b(?:in|around|out)\s+(?:the\s+)?world\b|\bout\s+there\b|\bin\s+the\s+news\b"
        r"|\btoday\b|\blately\b|\brecently\b|\bglobally\b|\bworldwide\b|\banywhere\b"
        r"|\baround\s+the\s+globe\b", low)
    _relational = re.search(
        r"\b(?:you|your|yourself|me|my|here|us|this|that|blaming|wrong|"
        r"with\s+you)\b", low)
    _news_ambiguous = bool(_amb and _world_scope and not _relational)
    if (_news_explicit or _news_ambiguous) \
            and not re.search(r"\b(dude|bro|man|wtf|omg|lol|hey|seriously|really)\b", low):
        # Extract topic from "news in/about/on/for X" or "X news" phrasing.
        _topic = ""
        _tm = re.search(r"\bnews\s+(?:in|about|on|for|regarding|covering)\s+([a-z][a-z0-9 \-]{2,40})\b", low)
        if not _tm:
            _tm = re.search(r"\b(?:in|about|on|for|regarding|covering)\s+([a-z][a-z0-9 \-]{2,40})\s+news\b", low)
        if not _tm:
            _tm = re.search(r"\b([a-z][a-z0-9 \-]{2,40})\s+news\b", low)
        if _tm:
            # Sanitise: strips "for you", trailing/leading stop-words, and
            # rejects question fragments / pure-noise captures.
            _topic = _sanitise_news_topic(_tm.group(1))
        args_news = {"mode": "fetch_and_show", "sources": ["all"]}
        if _topic:
            args_news["topic"] = _topic
        return _mk("NEWS_FETCH", args_news, 0.95, matched_by="news.current_events")

    if re.search(r"\b(search|find)\b.{0,20}\bnews\b|\bnews\b.{0,20}(search|find)\b", low):
        _qm = re.search(r"(?:about|for|on)\s+(.+)$", low)
        return _mk("NEWS_FETCH",
                   {"mode": "search", "query": _qm.group(1).strip() if _qm else raw.strip()},
                   0.94, matched_by="news.search")

    if re.search(r"\b(show|list|give\s+me)\b.{0,30}\b(headlines?|recent\s+news|news\s+articles?)\b", low):
        return _mk("NEWS_FETCH", {"mode": "recent"}, 0.94, matched_by="news.recent")

    # "show me X news" / "give me X news" / "show me news on X"
    _topic_news = re.search(
        r"\b(?:show|list|give|tell|fetch|pull|get|bring)\s+(?:me\s+|us\s+)?"
        r"(?:some\s+|the\s+|any\s+)?"
        r"([a-z][a-z0-9 \-]{2,40}?)\s+news\b",
        low,
    )
    if _topic_news:
        topic = _topic_news.group(1).strip().rstrip("?.!,;:")
        if topic and topic not in {"the", "any", "today", "world", "current", "latest", "some"}:
            return _mk("NEWS_FETCH",
                       {"mode": "fetch_and_show", "topic": topic, "sources": ["all"]},
                       0.93, matched_by="news.show_topic")
    _topic_news_about = re.search(
        r"\b(?:show|list|give|tell|fetch|pull|get|bring)\s+(?:me\s+|us\s+)?"
        r"(?:some\s+|the\s+|any\s+)?news\s+(?:about|on|for|in|regarding|covering)\s+"
        r"([a-z][a-z0-9 \-]{2,40})",
        low,
    )
    if _topic_news_about:
        topic = _topic_news_about.group(1).strip().rstrip("?.!,;:")
        if topic:
            return _mk("NEWS_FETCH",
                       {"mode": "fetch_and_show", "topic": topic, "sources": ["all"]},
                       0.93, matched_by="news.show_about_topic")

    if re.search(r"\b(where|what)\b.{0,15}\bmy\s+news\b"
                 r"|\bshow\s+(?:me\s+)?(?:the\s+)?news\b"
                 r"|\bwhere(?:'?s?\s+|\s+is\s+)(?:the\s+)?news\b", low):
        return _mk("NEWS_FETCH", {"mode": "recent"}, 0.93, matched_by="news.show_cached")

    if re.search(r"\bnews\s+(stats?|status|count|database)\b", low):
        return _mk("NEWS_FETCH", {"mode": "stats"}, 0.91, matched_by="news.stats")

    if re.search(r"\b(learn|teach\s+yourself|update\s+yourself)\b.{0,30}"
                 r"\b(web|internet|online|news|current)\b", low):
        _tm = re.search(r"about\s+([a-z][a-z ]{2,30})", low)
        return _mk("NEWS_FETCH",
                   {"mode": "fetch_and_show", "topic": _tm.group(1).strip() if _tm else "", "sources": ["all"]},
                   0.93, matched_by="news.learn")

    # ── SELF_UPGRADE (before generic 'update' routes) ─────────────────────────
    if re.search(r"\b(upgrade|update)\s+(yourself|eli|the\s+system)\b"
                 r"|\bself.?upgrade\b|\brun\s+upgrade\b", low):
        return _mk("SELF_UPGRADE", {"request": raw}, 0.96, matched_by="self.upgrade")

    if re.search(r"\b(generate|create)\s+patch\b|\bpatch\s+(eli|system)\b", low):
        return _mk("SELF_UPGRADE", {"request": "generate_patch"}, 0.93, matched_by="self.patch")

    if re.search(r"\bpatch\s+yourself\b|\bself.?patch\b|\bapply.*patch\b|\bfix\s+your\s+own\s+code\b", low):
        return _mk("SELF_PATCH", {}, 0.95, matched_by="self.patch_cycle")

    # ── SELF_TEST (before generic 'test' routes) ──────────────────────────────
    if re.search(r"\b(run\s+)?self.?test\b|\btest\s+yourself\b|\brun\s+tests?\b", low):
        return _mk("SELF_TEST", {}, 0.94, matched_by="self.test")

        # ── Memory search (must be before web search to prevent mis-routing) ─
    # Bare forms: 'search your memory', 'search memory', 'check memory'
    _bare_mem = re.match(
        r'^(?:search|check|look\s+in|query)\s+(?:your\s+)?memor(?:y|ies)$', low
    )
    if _bare_mem:
        return _mk('MEMORY_RECALL', {'query': ''},
                   0.95, matched_by='memory.search_bare')
    # Explicit "what do you know about me" → MEMORY_RECALL
    if re.search(r"\bwhat\s+do\s+you\s+know\s+about\s+me\b", low):
        return _mk("MEMORY_RECALL", {"query": "information about user"},
                   0.98, matched_by="memory.user_recall", entities={"query": "user"})

    # Forms with 'for X': 'search your memory/memories for python'
    # NOTE: memor(?:y|ies) handles both singular and plural to prevent
    # "search your memories for X" from falling through to OPEN_BROWSER web.search.
    _mem_search_patterns = (
        r'^search\s+(?:your\s+)?memor(?:y|ies)\s+(?:for\s+)?(.+)$',
        r'^(?:look|check)\s+(?:in\s+)?(?:your\s+)?memor(?:y|ies)\s+(?:for\s+)?(.+)$',
        r'^(?:do\s+you\s+(?:have|know)\s+anything\s+(?:in\s+memor(?:y|ies)\s+)?(?:about|related\s+to))\s+(.+)$',
        r'^(?:recall|retrieve)\s+(?:from\s+memor(?:y|ies)\s+)?(?:anything\s+(?:about|related\s+to)\s+)?(.+)$',
    )
    for _mp in _mem_search_patterns:
        _mm = re.match(_mp, low, re.I)
        if _mm:
            _mq = _mm.group(1).strip(
            ) if _mm.lastindex and _mm.lastindex >= 1 else ''
            return _mk('MEMORY_RECALL', {'query': _mq}, 0.95, matched_by='memory.search_explicit',
                       entities={'query': _mq})

# ---- PLUGINS ----
    if re.search(
            r"\b(?:install|download|get)\s+(?:a\s+|the\s+)?plugin\s+(?:for\s+)?(.+)", low):
        m = re.search(
            r"\b(?:install|download|get)\s+(?:a\s+|the\s+)?plugin\s+(?:for\s+)?(.+)",
            low)
        query = m.group(1).strip() if m else raw
        return _mk("PLUGIN_INSTALL", {"query": query}, 0.95, matched_by="plugin.install_for",
                   entities={"query": query}, task_family="plugin")
    if re.search(
            r"\b(?:install|download|get)\s+(?:the\s+)?(?:plugin\s+)?([a-z_]+)\s+plugin\b", low):
        m = re.search(
            r"\b(?:install|download|get)\s+(?:the\s+)?(?:plugin\s+)?([a-z_]+)\s+plugin\b",
            low)
        pid = m.group(1).strip() if m else ""
        return _mk("PLUGIN_INSTALL", {"plugin": pid}, 0.97, matched_by="plugin.install_named",
                   entities={"plugin": pid}, task_family="plugin")
    if re.search(r"\buninstall\s+(?:the\s+)?(?:plugin\s+)?([a-z_]+)", low):
        m = re.search(r"\buninstall\s+(?:the\s+)?(?:plugin\s+)?([a-z_]+)", low)
        pid = m.group(1).strip() if m else ""
        return _mk("PLUGIN_UNINSTALL", {"plugin": pid}, 0.95, matched_by="plugin.uninstall",
                   entities={"plugin": pid}, task_family="plugin")
    if re.search(
            r"\b(?:list|show)\s+(?:all\s+)?(?:available\s+)?plugins\b", low):
        return _mk("PLUGIN_LIST", {"scope": "all"}, 0.95,
                   matched_by="plugin.list_all", task_family="plugin")
    if re.search(r"\b(?:list|show)\s+(?:my\s+)?installed\s+plugins\b", low):
        return _mk("PLUGIN_LIST", {"scope": "installed"}, 0.95,
                   matched_by="plugin.list_installed", task_family="plugin")
    if re.search(
            r"\b(?:search|find)\s+(?:a\s+)?plugin(?:s)?\s+(?:for\s+)?(.+)", low):
        m = re.search(
            r"\b(?:search|find)\s+(?:a\s+)?plugin(?:s)?\s+(?:for\s+)?(.+)", low)
        query = m.group(1).strip() if m else raw
        return _mk("PLUGIN_SEARCH", {"query": query}, 0.95, matched_by="plugin.search",
                   entities={"query": query}, task_family="plugin")
    # Ambient vision toggle — must beat the generic "enable/disable X" plugin
    # matcher below ("enable ambient vision" is not a plugin).
    if re.search(r"\bambient\s+vision\b", low):
        _av_off = bool(re.search(r"\b(?:disable|turn\s+off|stop|deactivate)\b", low))
        return _mk("AMBIENT_VISION", {"enabled": not _av_off, "text": raw}, 0.96,
                   matched_by="vision.ambient_off" if _av_off else "vision.ambient_on")
    if re.search(
            r"\benable\s+(?:the\s+)?(?:plugin\s+)?([a-z_]+)\s*(?:plugin)?\b", low):
        m = re.search(
            r"\benable\s+(?:the\s+)?(?:plugin\s+)?([a-z_]+)\s*(?:plugin)?\b", low)
        pid = m.group(1).strip() if m else ""
        return _mk("PLUGIN_ENABLE", {"plugin": pid}, 0.95, matched_by="plugin.enable",
                   entities={"plugin": pid}, task_family="plugin")
    if re.search(
            r"\bdisable\s+(?:the\s+)?(?:plugin\s+)?([a-z_]+)\s*(?:plugin)?\b", low):
        m = re.search(
            r"\bdisable\s+(?:the\s+)?(?:plugin\s+)?([a-z_]+)\s*(?:plugin)?\b", low)
        pid = m.group(1).strip() if m else ""
        return _mk("PLUGIN_DISABLE", {"plugin": pid}, 0.95, matched_by="plugin.disable",
                   entities={"plugin": pid}, task_family="plugin")

    # ---- RUNTIME AUDIT ----
    if re.search(
            r"\b(?:run|do|perform|execute|start|run\s+a|do\s+a|perform\s+a)\s+"
            r"(?:full\s+)?(?:runtime\s+audit|system\s+audit|health\s+check|diagnostic|diagnostics|audit)\b", low):
        return _mk("RUNTIME_AUDIT", {}, 0.95, matched_by="audit.runtime",
                   need_grounding=True, task_family="grounded_audit")
    if re.search(
            r"\bwhat(?:'s|\s+is)\s+(?:actually\s+)?broken\b", low):
        return _mk("RUNTIME_AUDIT", {}, 0.90, matched_by="audit.broken",
                   need_grounding=True, task_family="grounded_audit")
    if re.search(
            r"\b(?:audit|check|test|verify)\s+(?:all|every|the)\s+"
            r"(?:agents?|systems?|modules?|components?|pipeline)\b", low):
        return _mk("RUNTIME_AUDIT", {}, 0.90, matched_by="audit.all_agents",
                   need_grounding=True, task_family="grounded_audit")

    # ---- HARDWARE PROFILE ----
    if re.search(
            r"\b(?:hardware|system)\s+(?:profile|benchmark|scan|detect)\b", low):
        return _mk("HARDWARE_PROFILE", {}, 0.95, matched_by="hardware.profile",
                   need_grounding=True, task_family="grounded_audit")
    if re.search(r"\bapply\s+(?:the\s+)?hardware\s+recommendation\b", low):
        return _mk("HARDWARE_PROFILE", {"apply": True}, 0.97, matched_by="hardware.apply",
                   need_grounding=True, task_family="grounded_audit")
    if re.search(
            r"\b(?:what|which)\s+model\s+(?:should|would|do)\s+(?:i|you|we)\s+(?:use|recommend)\b", low):
        return _mk("HARDWARE_PROFILE", {}, 0.90, matched_by="hardware.recommend",
                   need_grounding=True, task_family="grounded_audit")
    if re.search(
            r"\boptim(?:ize|ise)\s+(?:my\s+)?(?:model|settings|parameters|config)\b", low):
        return _mk("HARDWARE_PROFILE", {}, 0.90, matched_by="hardware.optimize",
                   need_grounding=True, task_family="grounded_audit")

    # ---- AWARENESS / CODE CHANGES ----
    if re.search(
            r"\b(?:awareness|self[- ]?awareness)\s+(?:status|report|check)\b", low):
        return _mk("AWARENESS_STATUS", {"query": raw}, 0.95, matched_by="awareness.status",
                   entities={"query": raw}, need_grounding=True, task_family="grounded_audit")
    if re.search(r"memory\s*(status|stats|report|info|summary|check|usage|count|size|total)|how many memories|memories (do you have|count|total)", low):
        return _mk("MEMORY_STATUS", {}, 0.97, matched_by="memory.status")
    if re.search(r"\bcognition\s*(runtime\s+)?status\b|\bcognition\s*(stats|report|check|health)\b", low):
        return _mk("COGNITION_STATUS", {}, 0.99, matched_by="cognition.status")
    if re.search(r"\bruntime\s+status\b|\b(runtime|system)\s*(stats|report|health|check)\b", low):
        return _mk("RUNTIME_STATUS", {}, 0.99, matched_by="runtime.status")
    if re.search(r"\bwhat\s+code\s+(changed|has changed)\b|\bcode\s+changes?\b", low):
        return _mk("CODE_CHANGES", {"query": raw}, 0.95, matched_by="router.code_changes")
    if re.search(
            r"\b(?:code|source)\s+(?:changes?|diff|modifications?)\b", low):
        return _mk("CODE_CHANGES", {"query": raw}, 0.95, matched_by="awareness.code_changes",
                   entities={"query": raw}, need_grounding=True, task_family="grounded_audit")
    if re.search(
            r"\bwhat\s+(?:changed|has changed|was changed)\s+(?:in\s+)?(?:your|the|my)?\s*(?:code|source|codebase|repo)\b", low):
        return _mk("CODE_CHANGES", {"query": raw}, 0.93, matched_by="awareness.what_changed",
                   entities={"query": raw}, need_grounding=True, task_family="grounded_audit")
    if re.search(
            r"\b(?:capability|capabilities)\s+(?:changes?|updates?|diff)\b", low):
        return _mk("AWARENESS_STATUS", {"query": raw}, 0.93, matched_by="awareness.cap_changes",
                   entities={"query": raw}, need_grounding=True, task_family="grounded_audit")

        # ============================================================
    # LEGACY-COMPAT MEDIA INTENTS (must be before generic MEDIA_CONTROL)
    # Keeps test compatibility and deterministic semantics.
    # ============================================================
    # "what's playing?" — report the current track/source (before the play matchers
    # so it's never mistaken for a play command).
    if re.search(r"\bwhat(?:'?s| is| are you)\s+playing\b|\bwhat\s+(?:song|track|music)\s+is\s+"
                 r"(?:this|playing|that)\b|\bnow\s+playing\b|\bwhat\s+am\s+i\s+listening\s+to\b", low):
        return _mk("NOW_PLAYING", {}, 0.96, matched_by="media.now_playing")

    if re.search(r"\bstop\s+(?:the\s+)?(?:media|music|spotify|youtube)\b", low):
        return {"action": "STOP_MEDIA", "args": {}, "confidence": 0.96}

    if re.search(r"\bpause\s+(?:the\s+)?(?:media|music|spotify)\b", low):
        return {"action": "PAUSE_MEDIA", "args": {}, "confidence": 0.96}

    if re.search(
            r"\b(play|resume|start)\s+(?:the\s+)?(?:media|music|spotify)\b", low):
        return {"action": "PLAY_MEDIA", "args": {}, "confidence": 0.96}

    if re.search(r"\b(next|skip)\s+(?:the\s+)?(?:song|track|media)\b", low):
        return {"action": "NEXT_MEDIA", "args": {}, "confidence": 0.96}

    if re.search(
            r"\b(previous|prev|back)\s+(?:the\s+)?(?:song|track|media)\b", low):
        return {"action": "PREVIOUS_MEDIA", "args": {}, "confidence": 0.96}

    # ------------------------------------------------------------
    # 0) HARD COMPATIBILITY / REGRESSION CONTRACTS
    # ------------------------------------------------------------
    # Important: tests may expect STOP_MEDIA exactly for "stop media"
    if low.strip() == "stop media":
        return _mk("STOP_MEDIA", {}, 0.99,
                   matched_by="compat.stop_media_exact")

    # Bare domain / URL
    if _is_likely_url(raw):
        return _mk(
            "OPEN_URL",
            {"url": _normalize_url(raw)},
            0.99,
            matched_by="url.bare",
            entities={"url": _normalize_url(raw)},
        )

    # ------------------------------------------------------------
    # 1) SELF / SYSTEM INTELLIGENCE INTENTS
    # ------------------------------------------------------------
    if any(p in low for p in ["analyze yourself", "analyse yourself",
           "self analysis", "check yourself", "diagnostic"]):
        return _mk("SELF_ANALYZE", {}, 0.95, matched_by="self.analyze")

    # Bare greetings / salutations are conversation, never a report command.
    # Stops "morning" / "good morning" resolving to MORNING_REPORT (both via this
    # router and the slow LLM intent fallback). Must be a *whole-utterance*
    # greeting — "morning report" / "good morning, give me the report" fall
    # through to the report rule below.
    if re.fullmatch(
        r"(?:hi+|hey+|hello+|yo|sup|howdy|hiya|heya|hai|gm|gn|greetings|"
        r"good\s+(?:morning|afternoon|evening|day|night)|morning|afternoon|evening)"
        r"[\s,.!]*(?:eli|there|everyone|all|mate|buddy|bud|pal|man)?[\s,.!]*",
        low.strip(),
    ):
        return _mk("CHAT", {"message": raw}, 0.9, matched_by="chat.greeting")

    if any(p in low for p in ["morning report",
           "daily report", "intelligence report"]):
        # ...but not when the user is negating or merely referring to it
        # ("i said good morning, not morning report", "don't give me the report").
        if not re.search(
            r"\b(?:not|no|never|stop|cancel|don'?t|didn'?t|isn'?t|wasn'?t|"
            r"without|skip|instead\s+of|rather\s+than)\b", low
        ):
            return _mk("MORNING_REPORT", {}, 0.95,
                       matched_by="self.morning_report")

    if any(p in low for p in ["apply self-improvement patch", "apply patch", "run patch cycle",
                              "patch yourself", "fix your own code", "self-patch"]):
        return _mk("SELF_PATCH", {}, 0.95, matched_by="self.patch")

    # "improve / enhance / refactor / optimise / clean up <a NAMED .py file>" → the verified
    # CodeAgent on THAT file (same path FIX_FILE uses), in improve mode — NOT ELI's own
    # self-improvement. A named file wins over the generic self-improve catch below; bare
    # "improve yourself / your code" (no .py path) still falls through to SELF_IMPROVE.
    _imp_file = re.search(
        r"\b(?:improve|enhance|refactor|optimi[sz]e|clean\s*up|tidy(?:\s*up)?|make\s+better)\b"
        r".{0,40}?([\w./~ -]+\.py)\b", raw, re.I)
    if _imp_file:
        _imp_path = _imp_file.group(1).strip()
        return _mk("FIX_FILE", {"path": _imp_path, "mode": "improve"}, 0.92,
                   matched_by="file.improve_python", entities={"path": _imp_path})

    if "improve" in low and (
            "yourself" in low or "self" in low or "code" in low):
        return _mk("SELF_IMPROVE", {}, 0.9, matched_by="self.improve")

    if "suggest" in low and (
            "improvement" in low or "optimization" in low or "optimisation" in low):
        return _mk("SELF_ANALYZE", {"suggest": True},
                   0.9, matched_by="self.suggest_improvements")

    # ------------------------------------------------------------
    # 2) CODE / IDE / DEV WORKFLOWS
    # ------------------------------------------------------------
    if any(p in low for p in [
        "create project", "build project", "generate project", "make project",
        "new project", "start project"
    ]):
        return _mk("GENERATE_PROJECT", {"description": raw, "use_gguf_only": True,
                   "forbid_ollama": True}, 0.95, matched_by="dev.generate_project")

    # "run command X" / "run nvidia-smi" / "run the rm command on it" — explicit
    # shell requests.
    _run_cmd_m = re.match(
        r'^run\s+(?:the\s+)?(?:command\s+)?([a-zA-Z][\w\-]+(\s+[^,]+)?)$',
        raw,
        re.I)
    if _run_cmd_m:
        _cmd = _run_cmd_m.group(1).strip()
        # "the <bin> command [on/with/...] <tail>" idiom: the literal word
        # "command" is filler and <bin> is the real binary. Without this,
        # "run the rm command on it" was captured verbatim and executed as
        # `rm command on it` — trying to delete files named command/on/it.
        _idiom = re.match(r'^([a-zA-Z][\w\-]*)\s+command\b\s*(.*)$', _cmd, re.I)
        if _idiom:
            _cmd = (_idiom.group(1) + " " + _idiom.group(2)).strip()
        _parts = _cmd.split()
        _first = _parts[0].lower() if _parts else ""
        _rest = _parts[1:]
        # Destructive binaries need a concrete target. If the only "arguments"
        # are deictic / natural-language fillers ("on it", "that", ...) there is
        # no real path to act on — refuse and fall through to a clarifying chat
        # rather than running rm/mv/dd against garbage.
        _DESTRUCTIVE = {"rm", "rmdir", "mv", "dd", "kill", "killall",
                        "shred", "mkfs", "truncate", "unlink"}
        _DEICTIC = {"it", "that", "this", "them", "these", "those", "here",
                    "there", "on", "to", "with", "against", "for", "at", "in",
                    "the", "my", "your"}
        _vague_destructive = (
            _first in _DESTRUCTIVE
            and (not _rest
                 or all(re.sub(r'[.!?,]', '', w).lower() in _DEICTIC for w in _rest))
        )
        if (not re.search(r'\b(script|function|program|code)\b', _cmd, re.I)
                and not _vague_destructive):
            return _mk("SHELL_EXEC", {"cmd": _cmd}, 0.95, matched_by="shell.run_command",
                       entities={"cmd": _cmd})

    # Non-code "script" intents — must check BEFORE code triggers
    _NON_CODE_SCRIPT = re.compile(
        r"\b(?:for|about|on|regarding)\s+(?:[\w]+\s+)*?"
        r"(?:presentation|slides?|talk|speech|podcast|film|movie|play|show|actors?|"
        r"onboarding|marketing|campaign|video|youtube|event|ceremony|wedding|performance|"
        r"audience|interview|screenplay)\b", re.I)
    _CREATIVE_SCRIPT = re.compile(
        r"\b(?:film\s+script|movie\s+script|play\s+script|podcast\s+script|write\s+a\s+(?:podcast|film|stage|theatre|movie)\s+script|"
        r"theatre\s+script|stage\s+script|script\s+for\s+(?:a\s+)?(?:film|movie|play|"
        r"podcast|show|talk|video|event|presentation|slides?|actors?|onboarding|ceremony|"
        r"wedding|performance|audience|interview|marketing|campaign|youtube))\b", re.I)
    # _NON_CODE_SCRIPT must only fire when there's a clear creation intent.
    # Without this guard "...drowning on youtube" matches because "on" is in
    # the preposition set and "youtube" is in the keyword list.
    _CREATION_VERB = re.compile(
        r"\b(?:write|create|generate|make|build|prepare|draft|produce|develop|design)\b",
        re.I)
    if (_CREATION_VERB.search(raw) and _NON_CODE_SCRIPT.search(raw)) or _CREATIVE_SCRIPT.search(raw):
        return _mk("CHAT", {"message": raw}, 0.75,
                   matched_by="dev.non_code_script")

    code_triggers = [
        "create a script", "write a script", "generate a script",
        "create a python", "write a python", "make a python",
        "create a function", "write a function", "generate a function",
        "write code", "generate code", "write me code",
        "create code", "build a script", "make a script",
        "create me a script", "write me a script"
    ]
    # Also catch: "write a bash/python/shell/js script to/for/that"
    _CODE_SCRIPT_RE = re.compile(
        r"\b(?:write|create|generate|make|build)\s+(?:a\s+)?(?:bash|python|shell|sh|"
        r"js|javascript|ruby|perl|powershell|zsh|fish|lua|go|rust)\s+(?:script|function|"
        r"program|module|class|code)\b", re.I)
    _GENERATION_COMPLAINT = (
        low.startswith((
            "you did not", "you didn't", "you just", "that script", "this script",
            "why did you", "why didn't you", "where is", "where did",
        ))
        or any(p in low for p in (
            "did not generate", "didn't generate", "never ran", "not generated",
            "nothing to do with", "less in content", "no ide opened",
            "did not open", "didn't open", "dump it into chat", "not provide any path",
        ))
    )
    # Background-job inspection. Accept 'job 3', 'job #3', 'job no. 3', the common STT
    # mangle 'ob #3', and status/ping phrasings ("status of job #3", "ping me when job
    # #3 is done"). The old regex required 'job'+space+digits, so a '#' (or STT dropping
    # the 'j') sent every status query to SET_ALARM/LIST_DIR/CHAT and the model then
    # confabulated ("job #3 is a ghost"). CHECK_JOB reads the registry authoritatively.
    _JOB_REF = r"\bj?ob\s*#?\s*(?:no\.?\s*|number\s*)?(\d+)\b"
    if re.search(_JOB_REF, low):
        # Don't hijack a compound utterance whose PRIMARY action is something else
        # ("list the files in ~/Documents. Also, what is the status of job #3?" leads with
        # 'list' → that should win; the job mention is secondary). Only claim it when the
        # utterance is actually about the job: a job-intent word is present (or it's a short
        # bare "job 3") AND it does not lead with a different command verb.
        _starts_other = re.match(
            r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|hey[, ]+|eli[, ]+)*"
            r"(?:list|ls|open|launch|start|play|pause|close|quit|kill|create|make|write|"
            r"save|read|delete|move|rename|copy|set|search|find|analyse|analyze|"
            r"summari[sz]e|email|message|screenshot|take)\b", low)
        _job_intent = bool(re.search(
            r"\b(?:check|status|how'?s|view|ping|tell|notify|let\s+me\s+know|done|complete|"
            r"completed|ready|finished|finish|running|result|summary)\b", low))
        _short = len(low.split()) <= 6
        if (_job_intent or _short) and not _starts_other:
            _jm = re.search(_JOB_REF, low)
            _notify = bool(
                re.search(r"\b(?:ping|tell|notify|let\s+me\s+know|message|alert)\b", low)
                or re.search(r"\bwhen\b.*\b(?:done|complete|completed|ready|finished|finish)\b", low))
            return _mk("CHECK_JOB", {"job_id": _jm.group(1) if _jm else None,
                                     "notify": _notify, "text": raw},
                       0.95, matched_by="dev.check_job")
    if re.search(r"\b(?:background\s+jobs|list\s+(?:background\s+|running\s+)?jobs|running\s+jobs|what\s+jobs)\b", low):
        return _mk("BACKGROUND_JOBS", {}, 0.9, matched_by="dev.background_jobs")

    # ── Scheduled-task control ────────────────────────────────────────────────
    # Chat had no way to see or change the scheduler: the triggers above demand the
    # literal word "jobs", so "delete background TASK for 02:00 on Sunday" matched
    # nothing, fell through to CHAT, and the model asserted there were no scheduled
    # tasks while four were armed. These claim the "task" phrasings authoritatively.
    _TASK_WORD = r"(?:task|tasks|scheduled\s+task|background\s+task|scheduled\s+job)"

    if re.search(r"\b(?:cancel|delete|remove|kill|drop|clear|stop)\b[^.?!]{0,40}\b" + _TASK_WORD + r"\b", low):
        _m_id = re.search(r"\b(?:task|job)\s*#?\s*(\d{1,6})\b", low)
        return _mk("CANCEL_TASK",
                   {"job_id": _m_id.group(1) if _m_id else None, "text": raw},
                   0.95, matched_by="tasks.cancel")

    if re.search(r"\b(?:reschedule|re-schedule|move|push|shift|change)\b[^.?!]{0,40}\b" + _TASK_WORD + r"\b", low):
        _m_id = re.search(r"\b(?:task|job)\s*#?\s*(\d{1,6})\b", low)
        # The new time is whatever follows to/for/until/at.
        _m_when = re.search(r"\b(?:to|for|until|till|at)\s+(.{2,40})$", low)
        return _mk("RESCHEDULE_TASK",
                   {"job_id": _m_id.group(1) if _m_id else None,
                    "when": (_m_when.group(1).strip() if _m_when else ""), "text": raw},
                   0.95, matched_by="tasks.reschedule")

    if re.search(r"\b(?:list|show|what(?:'s| is| are)?|which|any)\b[^.?!]{0,30}\b"
                 r"(?:scheduled|background|pending|upcoming|queued)\b[^.?!]{0,20}\b" + _TASK_WORD + r"\b", low) \
            or re.search(r"\b" + _TASK_WORD + r"\b[^.?!]{0,20}\b(?:scheduled|pending|queued|upcoming)\b", low) \
            or re.search(r"\b(?:my|the)\s+(?:scheduled\s+)?tasks\b", low) \
            or re.search(r"\bwhat(?:'s| is| have you got)\s+(?:scheduled|queued|pending|planned)\b", low):
        return _mk("LIST_TASKS", {}, 0.9, matched_by="tasks.list")

    # CODE_SOLVE — the verified coding agent (plan → DAG/tree search → execute →
    # repair). Takes precedence over GENERATE_SCRIPT for requests that ask for
    # verification/testing/robustness; plain "write a script" stays GENERATE_SCRIPT.
    _CODE_SOLVE_RE = re.compile(
        r"\b(?:code[\s_-]?solve"
        r"|solve\s+(?:this\s+|the\s+)?(?:coding|programming|algorithm|leetcode|kata)"
        r"|(?:implement|write|build|create|fix|code)\b[^.?!]*\b(?:and|with|then|that)\b[^.?!]*"
        r"\b(?:tested|test|tests|verify|verified|unit\s+tests?|passes?\s+tests?|make\s+sure\s+it\s+(?:works|passes|runs))\b"
        r"|verified\s+(?:code|solution|implementation)"
        r"|use\s+the\s+coding\s+agent)\b", re.I)
    if not _GENERATION_COMPLAINT and _CODE_SOLVE_RE.search(raw):
        _cs_lang = "python"
        _cs_lm = re.search(r"\b(bash|shell|javascript|js|typescript|ts|ruby|go|golang|lua)\b", low)
        if _cs_lm:
            _cs_lang = {"js": "javascript", "ts": "typescript", "golang": "go",
                        "shell": "bash"}.get(_cs_lm.group(1), _cs_lm.group(1))
        return _mk("CODE_SOLVE", {"description": raw, "language": _cs_lang}, 0.95,
                   matched_by="dev.code_solve")

    if not _GENERATION_COMPLAINT and (any(p in low for p in code_triggers) or _CODE_SCRIPT_RE.search(raw)):
        return _mk("GENERATE_SCRIPT", {"description": raw, "use_gguf_only": True,
                   "forbid_ollama": True}, 0.95, matched_by="dev.generate_script")

    if re.search(
            r"(show|open|view|display)\s+.{0,20}?(diff|changes?|what.{0,10}fixed)", low):
        return _mk("SHOW_DIFF", {}, 0.9, matched_by="dev.show_diff")

    # fixed regex (no hidden control char nonsense)
    if re.search(
            r"(open|show|view)\s+.{0,20}?(?:in\s+)?(?:code|editor|ide)\b", low):
        pm = re.search(r"([~/\w.\- /]+\.py)\b", raw)
        path = pm.group(1).strip() if pm else ""
        return _mk("OPEN_IN_IDE", {"path": path}, 0.88, matched_by="dev.open_in_ide", entities={
                   "path": path} if path else None)

    if re.search(r"(?:open|launch|start|run)\s+(?:my\s+)?(?:ide|code\s+editor|vscode|visual\s+studio\s+code|pycharm|intellij|sublime|atom|gedit|notepad\+\+|nano|vim)", low):
        return _mk("OPEN_IDE", {}, 0.97, matched_by="dev.open_ide")

    # ------------------------------------------------------------
    # 3) MEMORY / CONTEXT / TEMPORAL RECALL
    # ------------------------------------------------------------
    if _is_time_recall_query(raw, low):
        # Let chat/planner handle temporal interpretation using memory backend
        return _mk("CHAT", {"message": raw}, 0.98,
                   matched_by="memory.temporal_recall_as_chat")

    if re.search(r"\bwhat(?:'s| is) my name\b", low) or re.search(
            r"\bwhat should you call me\b", low):
        return _mk("MEMORY_RECALL", {"query": "name"},
                   0.95, matched_by="memory.name_recall")

    m = re.match(r"^(?:recall|memory recall)\s+(.+)$", raw, re.I)
    if m:
        return _mk("MEMORY_RECALL", {"query": m.group(
            1).strip()}, 1.0, matched_by="memory.recall_explicit")

    if re.search(
            r"(what (is|was) (my|our) favourite colour|what (is|was) (my|our) favorite color|tell me (my|our) favorite color)", raw, re.I):
        return _mk("MEMORY_RECALL", {
                   "query": "favorite color"}, 0.95, matched_by="memory.favorite_color")

    # Name questions → let LLM answer naturally using injected memories
    # if re.search(r"(what (is|was) (my|our) name|what should you call me)", raw, re.I):
    # return _mk("MEMORY_RECALL", {"query": "name"}, 0.95,
    # matched_by="memory.name_recall")

    if re.search(r"\b(?:access|query)\s+neural\s+archive\b", raw, re.I):
        return _mk("MEMORY_RECALL", {
                   "query": "recent memories"}, 1.0, matched_by="memory.neural_archive_alias")

    if _looks_like_conversation_summary(low):
        m_num = re.search(r"last\s+(\d+)", low)
        limit = int(m_num.group(1)) if m_num else 10
        return _mk("MEMORY_RECALL", {
                   "query": "recent", "limit": limit}, 0.95, matched_by="memory.conversation_summary")

    # ── Note taking: check BEFORE memory store to avoid confusion ──
    _note_m = re.match(
        r'^(?:write|save|create|make|add|take|jot)\s+(?:(?:a|the|down)\s+)*notes?(?:\s+down)?[:\s]+(?:saying\s+|that\s+)?(.+)$',
        raw,
        re.I)
    if _note_m:
        return _mk("WRITE_NOTE", {"text": _note_m.group(
            1).strip()}, 0.97, matched_by="notes.write_early")
    _note_m2 = re.match(r'^(?:write|add|take|jot)\s+note\s+(.+)$', raw, re.I)
    if _note_m2:
        return _mk("WRITE_NOTE", {"text": _note_m2.group(
            1).strip()}, 0.95, matched_by="notes.add_early")

    # Explicit remember/store (keep this later than some command parses)
    m = re.match(
        r"^(?:remember|store|save|note)\s+(?:that\s+)?(.+)$",
        raw,
        re.I)
    if m:
        text_to_store = m.group(1).strip()
        return _mk(
            "MEMORY_STORE",
            {"text": text_to_store, "tags": ["user_memory", "remembered"]},
            1.0,
            matched_by="memory.store_explicit",
            entities={"text": text_to_store},
        )

    if re.search(r"\b(store|remember|save)\s+(?:this\s+)?memory\b", low):
        return _mk("MEMORY_STORE", {"text": raw},
                   0.8, matched_by="memory.store_generic")

    # ------------------------------------------------------------
    # 4) MEDIA CONTROL (specific → generic)
    # ------------------------------------------------------------
    # Specific next/prev track controls (legacy-friendly)
    if re.search(r"\b(next|skip)\s+(?:the\s+)?(?:song|track)\b", low):
        return _mk("NEXT_MEDIA", {}, 0.95,
                   matched_by="media.next_track_specific")
    if re.search(r"\b(previous|back)\s+(?:the\s+)?(?:song|track)\b", low):
        return _mk("PREVIOUS_MEDIA", {}, 0.95,
                   matched_by="media.prev_track_specific")

    # App-targeted media commands
    for app in MEDIA_APPS:
        if re.search(rf"\b{re.escape(app)}\b", low):
            cmd = _canonical_media_command(low)
            if cmd:
                # preserve STOP_MEDIA semantic if user literally says stop
                # media/music/spotify later
                if cmd == "pause" and re.search(r"\bstop\b", low) and app in {
                        "spotify"}:
                    return _mk("STOP_MEDIA", {
                    }, 0.93, matched_by="media.legacy_stop_specific_app", entities={"target": app})
                return _mk(
                    "MEDIA_CONTROL",
                    {"command": cmd, "target": app, "type": "app"},
                    0.95,
                    matched_by="media.app_targeted",
                    entities={"target": app, "command": cmd},
                )

    # Browser tab controls — handled before browser/media fallback so
    # "go to next tab" / "exit current tab" do not become MEDIA_CONTROL.
    if re.search(r"\b(?:go\s+to\s+|switch\s+to\s+|move\s+to\s+|next)\s*next\s+tab\b|\bnext\s+(?:browser\s+)?tab\b", low):
        return _mk("KEYBOARD", {"key": "ctrl+tab"}, 0.94,
                   matched_by="browser.tab_next.early")
    if re.search(r"\b(?:previous|prev|back\s+to)\s+tab\b|\bgo\s+back\s+(?:a\s+|one\s+)?tab\b", low):
        return _mk("KEYBOARD", {"key": "ctrl+shift+tab"}, 0.94,
                   matched_by="browser.tab_prev.early")
    if re.search(r"\b(?:close|exit|kill)\s+(?:current\s+|the\s+)?(?:browser\s+)?tab\b", low):
        return _mk("KEYBOARD", {"key": "ctrl+w"}, 0.94,
                   matched_by="browser.tab_close.early")
    if re.search(r"\bnew\s+(?:browser\s+)?tab\b|\bopen\s+(?:a\s+)?(?:new\s+)?tab\b", low):
        return _mk("KEYBOARD", {"key": "ctrl+t"}, 0.93,
                   matched_by="browser.tab_new.early")

    # ── Window management (screen control) ─────────────────────────────────
    # "optimise screen" / "tile windows" / "minimise all" / "focus X" / etc.
    _window_ops = (
        (r"\boptimi[sz]e\s+(?:my\s+)?screen\b|\btile\s+(?:all\s+)?windows?\b|"
         r"\barrange\s+(?:my\s+)?windows?\b|\borganis[ez]e\s+(?:my\s+)?windows?\b|"
         r"\bfit\s+(?:all\s+)?windows?\b",
         "TILE_WINDOWS"),
        (r"\bminimi[sz]e\s+(?:all|everything)\b|\bshow\s+(?:my\s+)?desktop\b|"
         r"\bclear\s+(?:my\s+)?screen\b|\bhide\s+all\s+windows?\b",
         "MINIMISE_ALL"),
        (r"\bmaximi[sz]e\s+(?:current|this|the\s+window|window)\b|"
         r"\bfullscreen\s+(?:current|this|the\s+window|window)?\b|"
         r"\bmake\s+(?:this|current|window)\s+fullscreen\b",
         "MAXIMISE_WINDOW"),
        (r"\brestore\s+(?:windows?|all|everything)\b|\bunminimi[sz]e\s+all\b|"
         r"\bbring\s+back\s+(?:windows?|everything)\b",
         "RESTORE_WINDOWS"),
        (r"\bnext\s+window\b|\bswitch\s+window\b|\bcycle\s+windows?\b",
         "NEXT_WINDOW"),
        (r"\bprevious\s+window\b|\bprior\s+window\b",
         "PREVIOUS_WINDOW"),
        (r"\bworkspace\s+left\b|\bnext\s+workspace\b|\bworkspace\s+right\b|"
         r"\bswitch\s+workspace\b|\bnext\s+desktop\b|\bprevious\s+desktop\b",
         "SWITCH_WORKSPACE"),
    )
    for _wre, _waction in _window_ops:
        if re.search(_wre, low):
            args_w: Dict[str, Any] = {}
            if _waction == "SWITCH_WORKSPACE":
                if re.search(r"\b(?:left|previous|prior)\b", low):
                    args_w["direction"] = "left"
                else:
                    args_w["direction"] = "right"
            return _mk(_waction, args_w, 0.93,
                       matched_by=f"window.{_waction.lower()}")

    # Focus a specific app: "focus spotify", "switch to firefox", "bring up X"
    m = re.match(
        r"^(?:focus|switch\s+to|bring\s+up|jump\s+to|raise|activate)\s+(.+?)\s*$",
        low,
    )
    if m:
        app_name = m.group(1).strip().rstrip("?.!,;:")
        if app_name and len(app_name.split()) <= 3:
            return _mk("FOCUS_APP", {"name": app_name}, 0.9,
                       matched_by="window.focus_app",
                       entities={"name": app_name})

    # ── Per-service quick commands ─────────────────────────────────────────
    # "next song on spotify", "next video on youtube",
    # "pause spotify", "pause youtube", "resume soundcloud", "volume up"
    m = re.search(
        r"\b(?:next|skip)\s+(?:song|track|video|episode|item|clip|chapter)\s+on\s+"
        r"(spotify|youtube|soundcloud|netflix|prime(?:\s+video)?|primevideo|"
        r"disney(?:\+|\s*plus)?|disneyplus|hulu|twitch|mpv|local|system)\b",
        low,
    )
    if m:
        target = m.group(1).strip().lower().replace(" ", "")
        target = {"prime": "primevideo", "primevideo": "primevideo",
                  "disney": "disneyplus", "disney+": "disneyplus",
                  "disneyplus": "disneyplus"}.get(target, target)
        return _mk("MEDIA_CONTROL", {"command": "next", "target": target,
                                     "type": "service"}, 0.94,
                   matched_by="media.next_on_service",
                   entities={"target": target, "command": "next"})
    m = re.search(
        r"\b(?:previous|prev|back)\s+(?:song|track|video|episode|item|clip|chapter)\s+on\s+"
        r"(spotify|youtube|soundcloud|netflix|prime(?:\s+video)?|primevideo|"
        r"disney(?:\+|\s*plus)?|disneyplus|hulu|twitch|mpv|local|system)\b",
        low,
    )
    if m:
        target = m.group(1).strip().lower().replace(" ", "")
        target = {"prime": "primevideo", "disney": "disneyplus",
                  "disney+": "disneyplus"}.get(target, target)
        return _mk("MEDIA_CONTROL", {"command": "previous", "target": target,
                                     "type": "service"}, 0.94,
                   matched_by="media.prev_on_service",
                   entities={"target": target, "command": "previous"})

    # Browser/video media. Whole-word match only — "tab" as a substring fired
    # on "me·tab·olise", which (with "next" from "the next log lands") routed a
    # whole pasted paragraph to a browser next-track command. The "bro"/"brows"
    # fragments were removed for the same reason. Also gate on command shape:
    # a real media control is terse, so anything longer than a short phrase is
    # not a bare "next/pause/play", no matter what words it contains.
    if (len(low.split()) <= 9
            and re.search(r"\b(?:netflix|youtube|browser|tab|video|prime|disney|twitch|hulu)\b", low)):
        cmd = _canonical_media_command(low)
        if cmd:
            if "forward" in low:
                cmd = "seek_forward"
            elif "rewind" in low:
                cmd = "seek_backward"
            return _mk(
                "MEDIA_CONTROL",
                {"command": cmd, "type": "browser", "target": "browser"},
                0.9,
                matched_by="media.browser",
                entities={"command": cmd},
            )

    # Generic media control
    media_keywords = [
        "media",
        "music",
        "audio",
        "song",
        "track",
        "player",
        "spotify"]
    exact_bare_media = {
        "pause": ("PAUSE_MEDIA", 0.88),
        "play": ("PLAY_MEDIA", 0.88),
        "resume": ("PLAY_MEDIA", 0.88),
        "stop": ("STOP_MEDIA", 0.85),
        "next": ("NEXT_MEDIA", 0.88),
        "next song": ("NEXT_MEDIA", 0.88),
        "next track": ("NEXT_MEDIA", 0.88),
        "previous": ("PREVIOUS_MEDIA", 0.88),
        "prev": ("PREVIOUS_MEDIA", 0.88),
        "previous song": ("PREVIOUS_MEDIA", 0.88),
        "previous track": ("PREVIOUS_MEDIA", 0.88),
    }
    if low in exact_bare_media:
        action, conf = exact_bare_media[low]
        return _mk(action, {}, conf, matched_by="media.bare_command",
                   entities={"command": low})

    if any(k in low for k in media_keywords):
        if re.search(r"\bstop\s+(?:the\s+)?(?:media|music|spotify)\b", low):
            return _mk("STOP_MEDIA", {}, 0.95, matched_by="media.stop_legacy")
        if re.search(r"\bpause\s+(?:the\s+)?(?:media|music|spotify)\b", low):
            return _mk("PAUSE_MEDIA", {}, 0.9, matched_by="media.pause_legacy")
        if re.search(
                r"\b(play|resume|start)\s+(?:the\s+)?(?:media|music|spotify)\b", low):
            return _mk("PLAY_MEDIA", {}, 0.9, matched_by="media.play_legacy")

    # "play X by Y" / "play X from Z" / "play some jazz" / "play movie soundtrack"
    _play_specific_m = re.match(
        r"^play\s+(?:me\s+|some\s+)?(.+?)(?:\s+on\s+([\w.]+))?$", raw, re.I
    )
    if _play_specific_m:
        query = _play_specific_m.group(1).strip()
        target = (_play_specific_m.group(2) or "").strip() or None
        if target and re.search(r"youtube\.com", target, re.I):
            target = "youtube website"
        # Only route as specific if it's not a bare "play" or "play media"
        if query and query.lower() not in ("media", "music", "audio", ""):
            return _mk("PLAY_MEDIA", {"query": query, "target": target}, 0.92,
                       matched_by="media.play_specific",
                       entities={"query": query, "target": target})

    # "shuffle" / "shuffle music" / "shuffle on"
    if re.match(r"^shuffle\b", low):
        return _mk("SHUFFLE_MEDIA", {}, 0.95, matched_by="media.shuffle")

    # "repeat" / "repeat track" / "loop"
    if re.match(r"^(?:repeat|loop)\b", low):
        return _mk("REPEAT_MEDIA", {}, 0.95, matched_by="media.repeat")

    # ------------------------------------------------------------
    # 5) TIME / DATE / CAPABILITIES
    # ------------------------------------------------------------
    # BEFORE the current-time route: "what time did I first/last send a message
    # today", "when did I first message you", "when did we start talking today"
    # ask about the conversation log, NOT the wall clock. These used to hit the
    # TIME route and return the CURRENT time. Route to a grounded query over
    # conversation_turns.
    if (
        re.search(r"\b(what time|when)\b", low)
        and re.search(r"\b(i|we|my)\b", low)
        and re.search(r"\b(first|last|latest|most recent|start|started|began|begin)\b", low)
        and re.search(r"\b(messag\w+|sent|send|talk\w*|spoke|speak|chat\w*|contact\w*|said|say)\b", low)
    ):
        _mt_which = "last" if re.search(r"\b(last|latest|most recent|recently)\b", low) else "first"
        _mt_scope = "today" if re.search(r"\btoday\b", low) else "all"
        return _mk("MESSAGE_TIME_QUERY", {"which": _mt_which, "scope": _mt_scope},
                   0.96, matched_by="memory.message_time", allow_chat_without_evidence=False)
    # The conversational/file pre-guards are computed here so the TIME route
    # gets them too. They used to sit below it and guard DATE only, which let
    # "…i have asked you what the time is quite a bit lately" answer with a bare
    # timestamp instead of being read as the remark it is.
    _date_conv_starters = frozenset({
        "yeah", "yes", "yep", "yup", "no", "nope", "ok", "okay",
        "so", "well", "fair", "right", "sure", "true", "exactly",
        "indeed", "agreed", "alright", "fine", "cool",
    })
    # Only habitual/reportive framings ("i have asked … quite a bit lately")
    # mean the sentence is *about* having asked. Bare "i said"/"i asked"/"i told"
    # are how an exasperated user re-states a request they were never given —
    # "i said the day AND time" wants the day and the time, not a discussion.
    # "asked you what" still covers the reportive "i asked you what the date is".
    _date_anti_signals = (
        "i have asked", "i've asked",
        "i keep asking", "i've been asking", "i was asking",
        "asked you what", "asked about the date",
    )
    _first_word_low = low.split()[0] if low.split() else ""
    _is_date_conv = (
        _first_word_low in _date_conv_starters
        or any(s in low for s in _date_anti_signals)
    )
    # File-command guard: "create/write/make/save a file … today's date …, then
    # read it back" is a file operation, not a date query — don't let "today's
    # date" hijack it into a bare DATE answer. (Fix: issue #5 DATE misroute.)
    _file_command = bool(
        re.search(r"\b(?:create|write|make|save|append|generate|put)\b[^.?!]*\bfile\b", low)
        or re.search(r"\bread it back\b", low)
        or re.search(r"/\S*\.(?:txt|py|md|json|csv|sh|log|ini|cfg|yaml|yml)\b", low)
    )

    if (not _is_date_conv and not _file_command
            and any(re.search(p, low) for p in _WALLCLOCK_TIME_PATTERNS)):
        # original_query carries the phrasing the TIME effector needs to tell
        # "the time" from "the day and the time", and to spot a named place.
        return _mk("TIME", {"original_query": text}, 1.0, matched_by="system.time")

    # CREATE_FILE: "create/make/write/save a file called PATH containing CONTENT
    # [, then read it back]". Requires both the word 'file' and a path with an
    # extension, so it can't steal "write a function/script". Runs before DATE
    # so "…containing today's date…" no longer answers with a bare date.
    if re.search(r"\b(?:create|make|write|save|generate)\s+(?:a\s+|an\s+|the\s+|new\s+)*[a-z]*\s*file\b", low):
        _cf_path_m = (
            re.search(r"(?:called|named|at|to|in)\s+[\"']?([~/]?[\w./\-]+\.[A-Za-z0-9]{1,8})", raw)
            or re.search(r"([~/][\w./\-]*\.[A-Za-z0-9]{1,8})", raw)
            or re.search(r"\b([\w.\-]+\.[A-Za-z0-9]{1,6})\b", raw)
        )
        if _cf_path_m:
            _cf_path = _cf_path_m.group(1)
            # Honour an explicit target directory ("…notes.txt in the ~/Documents
            # directory"). The path alternatives above only capture a filename, so
            # without this the requested directory was silently dropped and the file
            # landed under artifacts/scratch instead of where the user asked.
            if "/" not in _cf_path.strip("~/"):
                _cf_dir_m = (
                    re.search(r"\b(?:in|inside|into|under)\s+(?:the\s+)?"
                              r"([~/][\w./\-]+|[A-Za-z][\w \-]*?)\s*(?:dir(?:ectory)?|folder)\b",
                              raw, re.IGNORECASE)
                    or re.search(r"\b(?:in|inside|into|under)\s+(?:the\s+)?([~/][\w./\-]+)\b",
                                 raw, re.IGNORECASE))
                if _cf_dir_m:
                    _cf_dir = _expand_common_dir(_cf_dir_m.group(1).strip())
                    _cf_path = _cf_dir.rstrip("/") + "/" + _cf_path
            _cf_content_m = re.search(
                r"(?:containing|with\s+(?:the\s+)?(?:content|contents|text)|that\s+says|with)\s+(.+?)"
                r"(?:,?\s*(?:then|and\s+(?:then\s+)?read|and\s+read\s+it\s+back)\b|[.!?]?\s*$)",
                raw, re.IGNORECASE)
            _cf_content = (_cf_content_m.group(1).strip() if _cf_content_m else "")
            return _mk("CREATE_FILE", {"path": _cf_path, "content": _cf_content},
                       0.96, matched_by="fs.create_file")

    if (not _is_date_conv and not _file_command
            and any(re.search(p, low) for p in _WALLCLOCK_DATE_PATTERNS)):
        return _mk("DATE", {"original_query": text}, 1.0, matched_by="system.date")

    if (
        re.search(r"\b(?:list|show)\s+(?:me\s+)?(?:all\s+(?:of\s+)?)?(?:your\s+)?capabilities\b", low)
        or re.search(r"\bfull\s+list\s+of\s+(?:your\s+)?capabilities\b", low)
        or low in ("capabilities", "what can you do", "what can you do?")
        # NOTE: 'agents' REMOVED — agents (the 14 specialists) are NOT the 207-action
        # capability list. Questions about agents route to EXPLAIN_COGNITION_RUNTIME above.
        or re.search(r"\b(what|which|list|show)\b.{0,25}\b(capabilities|skills|actions|tools)\b", low)
        or re.search(r"\bwhat can you do\b|\bwhat are you capable\b|\byour capabilities\b|\byour skills\b", low)
    ):
        return _mk("LIST_CAPABILITIES", {}, 1.0,
                   matched_by="system.capabilities")

    if re.search(r"\b(?:check|what is)\s+chronal\s+alignment\b", raw, re.I):
        return _mk("TIME", {}, 1.0, matched_by="alias.chronal_alignment")

    # Alarm with am/pm format: "set alarm for 7am", "alarm at 8pm"
    _ampm_m = re.search(
        r"\balarm\s+(?:for\s+|at\s+)?(\d{1,2})\s*([ap]m)\b", low)
    if _ampm_m:
        _hour = int(_ampm_m.group(1))
        _ampm = _ampm_m.group(2)
        if _ampm == "pm" and _hour != 12:
            _hour += 12
        elif _ampm == "am" and _hour == 12:
            _hour = 0
        _alarm_time = f"{_hour:02d}:00"
        return _mk("SET_ALARM", {"time": _alarm_time}, 0.95, matched_by="timer.set_alarm_loose",
                   entities={"time": _alarm_time})

    # ------------------------------------------------------------
    # 5b) ALARM / TIMER
    # ------------------------------------------------------------
    m = re.search(
        r"\b(?:(?:set|start|create)\s+(?:a\s+)?)?(?:timer|countdown)\s+(?:for\s+)?(\d+)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b",
        low)
    if m:
        val = int(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("min"):
            val *= 60
        elif unit.startswith("hour") or unit.startswith("hr"):
            val *= 3600
        return _mk("SET_TIMER", {"duration": val}, 0.98,
                   matched_by="timer.set_duration", entities={"seconds": val})

    m = re.search(
        r"\b(?:(?:set|create)\s+(?:a\s+)?)?(?:alarm|reminder)\s+(?:for\s+)?(\d{1,2}[:.]\d{2})\b",
        low)
    if m:
        alarm_time = m.group(1).replace(".", ":")
        return _mk("SET_ALARM", {"time": alarm_time}, 0.98,
                   matched_by="timer.set_alarm", entities={"time": alarm_time})

    m = re.search(
        r"\b(?:set|create)\s+(?:a\s+)?(?:alarm|timer|reminder)\b",
        low)
    if m:
        # Try to find a time anywhere in the string
        time_m = re.search(r"(\d{1,2}[:.]\d{2})", raw)
        dur_m = re.search(
            r"(\d+)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)", low)
        if time_m:
            alarm_time = time_m.group(1).replace(".", ":")
            return _mk("SET_ALARM", {"time": alarm_time}, 0.95,
                       matched_by="timer.set_alarm_loose", entities={"time": alarm_time})
        elif dur_m:
            val = int(dur_m.group(1))
            unit = dur_m.group(2).lower()
            if unit.startswith("min"):
                val *= 60
            elif unit.startswith("hour") or unit.startswith("hr"):
                val *= 3600
            return _mk("SET_TIMER", {
                       "duration": val}, 0.95, matched_by="timer.set_duration_loose", entities={"seconds": val})
        return _mk("CHAT", {"message": raw}, 0.6,
                   matched_by="timer.no_time_found")

    # ------------------------------------------------------------
    # 6) FILE SYSTEM / PATH / DIRECTORY
    # ------------------------------------------------------------
    m = re.match(
        r"^(?:list|show|read)\s+(?:me\s+)?(?:my\s+|the\s+)?"
        r"(?:files?|contents?|directory|folder)\s+"
        r"(?:(?:in|inside|of)\s+)?(?:my\s+|the\s+)?(.+)$",
        raw,
        re.I)
    if m:
        _raw_path = m.group(1).strip()
        # Stop at a sentence/clause boundary so a trailing question isn't swallowed into
        # the path (observed: "…in ~/Documents. Also, what is the status of job #3?"
        # routed the whole tail as a path). Only split on sentence terminators and clear
        # clause joiners — never on a bare "and" (so "documents and settings" survives).
        _raw_path = re.split(r"\s*[.?!]\s+|,?\s+(?:also|and then|then)\b",
                             _raw_path, maxsplit=1)[0].strip().rstrip(".,;:")
        path = _expand_common_dir(_raw_path)
        return _mk("LIST_DIR", {
                   "path": path}, 0.95, matched_by="fs.list_dir_explicit", entities={"path": path})

    # Bare "list/show <path>" WITHOUT a files/contents/directory keyword — e.g.
    # "list ~/.../artifacts/db", "show ~/Downloads", "list or read ./eli".
    # Without this the LLM resolver emits the UNSUPPORTED 'LIST_FILE', which falls to
    # CHAT and the model CONFABULATES the directory contents (observed: invented db paths).
    # Require an explicit path token (/, ~, ./, ../) so it never false-matches prose.
    m_bare = re.search(
        r"\b(?:list|ls|show|read)\b(?:\s+(?:or|and)\s+\w+)?\s+(?:me\s+|out\s+)?(?:the\s+)?"
        r"((?:/|~/|\./|\.\./)[^\s,?!]+)",
        raw, re.I)
    if m_bare:
        path = _expand_common_dir(m_bare.group(1).strip())
        # "read/show <file>" is a FILE op, not a directory listing. When the
        # captured token is a file (has an extension, or exists as a file),
        # route to READ_FILE — otherwise a conversation .json / .docx path was
        # handed to LIST_DIR, which errors "Not a directory" and (historically)
        # got replayed as a recurring failure. Bare dirs still go to LIST_DIR.
        try:
            _pb = Path(path).expanduser()
            _looks_file = (bool(_pb.suffix) and not path.rstrip().endswith(("/", "\\"))) \
                or (_pb.exists() and _pb.is_file())
        except Exception:
            _looks_file = False
        if _looks_file:
            return _mk("READ_FILE", {"path": path}, 0.93,
                       matched_by="fs.read_file_bare_path", entities={"path": path})
        return _mk("LIST_DIR", {"path": path}, 0.93,
                   matched_by="fs.list_bare_path", entities={"path": path})

    if re.search(
            r"\b(?:list|show)\s+(?:my\s+|the\s+)?project\s+files?\b", raw, re.I):
        return _mk("LIST_DIR", {"path": "."}, 0.9,
                   matched_by="fs.list_project_files")

    if re.search(
            r"\b(list|show)\s+(?:the\s+)?(?:files?|contents?|directory)\s+(?:in\s+)?(?:the\s+)?(?:project|current)\b", raw, re.I):
        return _mk("LIST_DIR", {"path": "."}, 0.9,
                   matched_by="fs.list_project")

    if re.match(r"^(?:list|show)\s+directory\s*$", raw, re.I):
        return _mk("LIST_DIR", {"path": "."}, 0.9,
                   matched_by="fs.list_dir_default")

    # Home only — a conjunction means more targets follow ("open home directory
    # AND the downloads directory"), and this route can carry just one path, so
    # it used to open home, report success, and silently drop the rest. Defer
    # those to the open/launch block, which splits them into a SEQUENCE.
    if (re.match(
            r"^(?:open|launch|start|show)\s+(?:the\s+)?(?:home|home\s+(?:folder|directory)|file\s+manager|files?)\b", low)
            and not re.search(r"\band\b", low)):
        return _mk("OPEN_FILE_SYSTEM", {
                   "path": "~"}, 0.98, matched_by="fs.open_home")

    if re.search(r"\b(?:access|open|launch)\s+storage\s+matrix\b", raw, re.I):
        return _mk("OPEN_FILE_SYSTEM", {"path": "~"},
                   1.0, matched_by="alias.storage_matrix")

    # ------------------------------------------------------------
    # 6.5) PLUGIN ROUTER BRIDGE: explicit plugin-backed actions
    # ------------------------------------------------------------
    # Keep these patterns explicit. Do not hijack broad natural language.
    # Generic "search for X" should remain browser/search routing unless
    # the user explicitly asks for web/plugin/search-online behavior.

    # Web plugin
    _web_m = re.match(
        r"^(?:web\s+search|search\s+the\s+web\s+for|search\s+online\s+for|online\s+search\s+for|internet\s+search\s+for)\s+(.+)$",
        raw,
        re.I,
    )
    if _web_m:
        query = _web_m.group(1).strip()
        if query:
            return _mk("WEB_SEARCH", {"query": query}, 0.96, matched_by="plugin.web_search",
                       entities={"query": query})

    # TTS plugin
    _speak_m = re.match(
        r"^(?:speak|say|read\s+aloud|tts)\s+(.+)$",
        raw,
        re.I,
    )
    if _speak_m:
        text_to_say = _speak_m.group(1).strip()
        if text_to_say:
            return _mk("SPEAK", {"text": text_to_say}, 0.96, matched_by="plugin.tts_speak",
                       entities={"text": text_to_say})

    # System stats plugin
    if re.match(r"^(?:system\s+stats|system\s+statistics|resource\s+usage|show\s+system\s+stats)$", low):
        return _mk("SYSTEM_STATS", {}, 0.96, matched_by="plugin.system_stats")

    if re.match(r"^(?:cpu\s+usage|processor\s+usage|show\s+cpu\s+usage|how\s+busy\s+is\s+the\s+cpu)$", low):
        return _mk("CPU_USAGE", {}, 0.96, matched_by="plugin.cpu_usage")

    if re.match(r"^(?:ram\s+usage|memory\s+usage|show\s+ram\s+usage|show\s+memory\s+usage|how\s+much\s+ram\s+is\s+used)$", low):
        return _mk("RAM_USAGE", {}, 0.96, matched_by="plugin.ram_usage")

    # Pomodoro plugin
    if re.match(r"^(?:start\s+pomodoro|begin\s+pomodoro|pomodoro\s+start)(?:\s+timer)?$", low):
        return _mk("POMODORO_START", {}, 0.96, matched_by="plugin.pomodoro_start")

    if re.match(r"^(?:stop\s+pomodoro|end\s+pomodoro|cancel\s+pomodoro|pomodoro\s+stop)$", low):
        return _mk("POMODORO_STOP", {}, 0.96, matched_by="plugin.pomodoro_stop")

    if re.match(r"^(?:pomodoro\s+status|show\s+pomodoro|pomodoro)$", low):
        return _mk("POMODORO_STATUS", {}, 0.94, matched_by="plugin.pomodoro_status")

    # Notes plugin
    _new_note_m = re.match(
        r"^(?:new\s+note|create\s+note|write\s+note)\s+(.+)$",
        raw,
        re.I,
    )
    if _new_note_m:
        note_text = _new_note_m.group(1).strip()
        if note_text:
            return _mk("NEW_NOTE", {"text": note_text, "content": note_text}, 0.96,
                       matched_by="plugin.notes_new", entities={"text": note_text})

    _search_notes_m = re.match(
        r"^(?:search\s+notes\s+for|find\s+note\s+about|find\s+notes\s+about|search\s+my\s+notes\s+for)\s+(.+)$",
        raw,
        re.I,
    )
    if _search_notes_m:
        query = _search_notes_m.group(1).strip()
        if query:
            return _mk("SEARCH_NOTES", {"query": query}, 0.96, matched_by="plugin.notes_search",
                       entities={"query": query})

    if re.match(r"^(?:list\s+notes|show\s+notes|show\s+my\s+notes|notes\s+list)$", low):
        return _mk("LIST_NOTES", {}, 0.96, matched_by="plugin.notes_list")

    # Smart-home plugin
    _smart_m = re.match(
        r"^(?:smart\s+home|home\s+automation)\s+(.+)$",
        raw,
        re.I,
    )
    if _smart_m:
        command = _smart_m.group(1).strip()
        if command:
            return _mk("SMART_HOME", {"command": command, "text": command}, 0.95,
                       matched_by="plugin.smart_home", entities={"command": command})


    # ------------------------------------------------------------
    # 7) URL / BROWSER
    # ------------------------------------------------------------
    if re.match(r"^(?:open|launch)\s+url\s+(.+)$", raw, re.I):
        url = re.match(
            r"^(?:open|launch)\s+url\s+(.+)$",
            raw,
            re.I).group(1).strip()
        return _mk("OPEN_URL", {"url": _normalize_url(url)},
                   0.98, matched_by="web.open_url_explicit")

    m = re.match(r"^(?:open|launch)\s+(https?://\S+)\s*$", raw, re.I)
    if m:
        return _mk("OPEN_URL", {"url": m.group(1)}, 0.98,
                   matched_by="web.open_url_with_scheme")

    if re.search(r"\b(?:open|launch|access|start)\s+network\s+browser\b", low):
        return _mk("OPEN_BROWSER", {}, 0.98,
                   matched_by="web.browser_alias_network")

    if re.match(
            r"^(?:open|launch|start)\s+(?:the\s+)?(?:browser|web browser|internet browser|browse the web)\s*$", low):
        return _mk("OPEN_BROWSER", {}, 0.98, matched_by="web.browser_explicit")

    if re.match(r"^(?:open|launch|start)\s+(?:the\s+)?(?:internet|web)\b", low):
        return _mk("OPEN_BROWSER", {}, 0.98,
                   matched_by="web.browser_internet_alias")

    # Named site + optional search: "open youtube and search for cats"
    _BROWSER_SITES = {
        "youtube": "https://www.youtube.com",
        "netflix": "https://www.netflix.com",
        "gmail": "https://mail.google.com",
        "google": "https://www.google.com",
        "wikipedia": "https://www.wikipedia.org",
        "reddit": "https://www.reddit.com",
        "github": "https://www.github.com",
        "twitter": "https://www.twitter.com",
        "twitch": "https://www.twitch.tv",
        "prime": "https://www.primevideo.com",
        "disney": "https://www.disneyplus.com",
        "hulu": "https://www.hulu.com",
    }
    _site_m = re.match(
        r"^(?:open|launch|go\s+to|navigate\s+to)\s+(\w+)(?:\s+and\s+search\s+(?:for\s+)?(.+))?$",
        raw, re.I)
    if _site_m:
        site_key = _site_m.group(1).lower()
        search_q = (_site_m.group(2) or "").strip()
        if site_key in _BROWSER_SITES:
            base = _BROWSER_SITES[site_key]
            import urllib.parse as _up
            if search_q:
                if "youtube" in base:
                    url = "https://www.youtube.com/results?search_query=" + \
                        _up.quote_plus(search_q)
                else:
                    url = "https://www.google.com/search?q=" + \
                        _up.quote_plus(search_q)
            else:
                url = base
            return _mk("OPEN_URL", {"url": url, "query": search_q},
                       0.97, matched_by="web.site_open", entities={"url": url})

    # Multiple sites: "open wikipedia and google" / "open wikipedia, google
    # and reddit"
    _multi_m = re.match(
        r"^(?:open|launch)\s+(?:multiple\s+tabs?\s*(?:in\s+\w+)?\s*[:,]?\s*)?(.+)$",
        raw,
        re.I)
    if _multi_m:
        raw_sites = _multi_m.group(1).lower()
        parts = re.split(r"\s+and\s+|\s*,\s*|\s+one\s+for\s+|;", raw_sites)
        urls = []
        for p in parts:
            key = p.strip().split()[0] if p.strip() else ""
            if key in _BROWSER_SITES:
                urls.append(_BROWSER_SITES[key])
        if len(urls) > 1:
            return _mk("OPEN_BROWSER", {"url": urls[0], "urls": urls},
                       0.97, matched_by="web.multi_tab", entities={"urls": urls})

    # "browse to X.com" / "go to X.com" / "navigate to X"
    m_browse = re.match(
        r"^(?:browse\s+to|go\s+to|navigate\s+to|visit)\s+(.+)$",
        raw,
        re.I)
    if m_browse:
        target = m_browse.group(1).strip()
        if target:
            url = target if re.match(
                r"https?://", target) else "https://" + target.lstrip("/")
            return _mk("OPEN_URL", {"url": url}, 0.97,
                       matched_by="web.browse_to", entities={"url": url})

    # Guard: "search your memory/memories ..." must never fall through to web search.
    m = re.match(r"^(?:search\s+(?:for\s+)?)(.+)$", raw, re.I)
    if m:
        query = m.group(1).strip()
        if query and not re.match(r"(?:your\s+)?memor(?:y|ies)\b", query, re.I):
            return _mk("OPEN_BROWSER", {"query": query}, 0.95,
                       matched_by="web.search", entities={"query": query})

    # ------------------------------------------------------------
    # 8) SCREENSHOT / INPUT / VOLUME
    # ------------------------------------------------------------
    if re.search(
            r"\b(take|capture)\s+(a\s+)?(screenshot|screen\s*shot|screen\s*capture|screen)\b", raw, re.I):
        region = "area" if re.search(
            r"\b(area|region|selection|part)\b", raw, re.I) else "full"
        return _mk("SCREENSHOT", {"region": region}, 0.98,
                   matched_by="io.screenshot", entities={"region": region})

    # Volume (order matters: unmute before mute)
    if re.search(
            r"\bmax(?:imum)?\s+volume\b|\bvolume\s+max\b|\bfull\s+volume\b", low):
        return _mk("VOLUME", {"direction": "set", "level": 100},
                   0.99, matched_by="audio.volume_max")

    m = re.search(
        r"\b(?:(?:set|change)\s+volume(?:\s+to)?|volume\s+set)\s+(\d+)%?",
        raw,
        re.I)
    if m:
        level = max(0, min(100, int(m.group(1))))
        return _mk("VOLUME", {"direction": "set", "level": level}, 0.98,
                   matched_by="audio.volume_set", entities={"level": level})

    if re.search(r"\bunmute\b", low):
        return _mk("VOLUME", {"direction": "unmute"},
                   0.98, matched_by="audio.unmute")

    if re.search(r"\bmute\b", low):
        return _mk("VOLUME", {"direction": "mute"},
                   0.98, matched_by="audio.mute")

    _vol_up_m = re.search(
        r"\b(?:volume\s+up|turn\s+up\s+(?:the\s+)?(?:volume|sound|audio)|"
        r"increase\s+(?:the\s+)?(?:volume|sound|audio)|"
        r"raise\s+(?:the\s+)?(?:volume|sound|audio)|louder)\b",
        low,
    )
    if _vol_up_m:
        _pct = re.search(r"\b(?:by\s+)?(\d{1,3})\s*%?\b", low[_vol_up_m.end():])
        delta = max(1, min(100, int(_pct.group(1)))) if _pct else 15
        return _mk("VOLUME", {"direction": "up", "delta": delta},
                   0.95, matched_by="audio.volume_up")

    if re.search(r"\b(lower|decrease|volume\s+down)\b", low):
        return _mk("VOLUME", {"direction": "down", "delta": 15},
                   0.95, matched_by="audio.volume_down")

    # Browser tab controls — keyboard shortcuts for tab navigation.
    if re.search(r"\b(?:go\s+to\s+|switch\s+to\s+|move\s+to\s+|next)\s*(?:next\s+)?tab\b|\bnext\s+(?:browser\s+)?tab\b", low):
        return _mk("KEYBOARD", {"key": "ctrl+tab"}, 0.93,
                   matched_by="browser.tab_next")
    if re.search(r"\b(?:previous|prev|back\s+to)\s+tab\b|\bgo\s+back\s+(?:a\s+|one\s+)?tab\b", low):
        return _mk("KEYBOARD", {"key": "ctrl+shift+tab"}, 0.93,
                   matched_by="browser.tab_prev")
    if re.search(r"\b(?:close|exit|kill)\s+(?:current\s+|the\s+)?(?:browser\s+)?tab\b", low):
        return _mk("KEYBOARD", {"key": "ctrl+w"}, 0.93,
                   matched_by="browser.tab_close")
    if re.search(r"\bnew\s+(?:browser\s+)?tab\b|\bopen\s+(?:a\s+)?(?:new\s+)?tab\b", low):
        return _mk("KEYBOARD", {"key": "ctrl+t"}, 0.92,
                   matched_by="browser.tab_new")

    # Screen-locator routes — find/click visible UI text on the screen.
    m = re.search(r"\b(?:find|locate|where\s+is)\s+(?:the\s+)?(.+?)\s+on\s+(?:the\s+)?screen\b", low)
    if m:
        query = m.group(1).strip()
        if query:
            return _mk("SCREEN_LOCATE", {"query": query}, 0.93,
                       matched_by="screen.locate", entities={"query": query})
    m = re.search(r"\b(?:click|tap|press)\s+(?:on\s+)?(?:the\s+)?(.+?)\s+on\s+(?:the\s+)?screen\b", low)
    if m:
        query = m.group(1).strip()
        if query:
            return _mk("SCREEN_LOCATE", {"query": query, "click": True}, 0.94,
                       matched_by="screen.locate_click", entities={"query": query})

    # Keyboard typing/press
    m = re.match(r"^type\s+(.+)$", raw, re.I)
    if m:
        typed = m.group(1).strip()
        if typed:
            return _mk("KEYBOARD", {
                       "type": typed}, 0.95, matched_by="io.keyboard_type", entities={"text": typed})

    m = re.search(r"\b(press|hit)\s+(enter|return)\b", raw, re.I)
    if m:
        return _mk("KEYBOARD", {"key": "Return"}, 0.95,
                   matched_by="io.keyboard_enter")

    m = re.search(r"\b(press|hit)\s+([a-zA-Z0-9_\-]+)\b", raw, re.I)
    if m:
        key = m.group(2)
        # Guard: only fire KEYBOARD when the captured word is actually a
        # recognised key name.  "hit me with it", "hit that button" etc. must
        # NOT route here — "me", "that", "it" are not keys.
        _KNOWN_KEYS = frozenset({
            # navigation / control
            "enter", "return", "space", "tab", "escape", "esc", "backspace",
            "delete", "del", "insert", "ins", "home", "end",
            "pageup", "pagedown", "page_up", "page_down",
            # arrows
            "up", "down", "left", "right",
            # function keys
            "f1", "f2", "f3", "f4", "f5", "f6",
            "f7", "f8", "f9", "f10", "f11", "f12",
            # modifiers (used alone or named in combos)
            "ctrl", "control", "alt", "shift", "super", "win", "meta",
            # punctuation / symbol names
            "comma", "period", "dot", "slash", "backslash",
            "minus", "plus", "equals", "semicolon", "quote",
            "bracketleft", "bracketright",
            # single alpha keys a-z
            "a","b","c","d","e","f","g","h","i","j","k","l","m",
            "n","o","p","q","r","s","t","u","v","w","x","y","z",
            # digits 0-9
            "0","1","2","3","4","5","6","7","8","9",
        })
        if key.lower() in _KNOWN_KEYS:
            return _mk("KEYBOARD", {"key": key}, 0.9,
                       matched_by="io.keyboard_key", entities={"key": key})

    # ------------------------------------------------------------
    # 9) CALENDAR shortcuts before generic read/show
    # ------------------------------------------------------------
    if re.match(r"^(?:show|list)\s+(?:my\s+)?calendar$", low, re.I):
        return _mk("LIST_EVENTS", {}, 0.97, matched_by="calendar.show_exact")

    # ------------------------------------------------------------
    # 10) FILE READ / FIX / SUMMARIZE / ANALYZE
    # ------------------------------------------------------------
    # Show clipboard must be checked before generic "show X" → READ_FILE
    if "clipboard" in low and "show" in low:
        return _mk("GET_CLIPBOARD", {}, 0.9, matched_by="clipboard.show")

    # Exclude habit queries from file-read
    if re.match(r"^(?:show|list|read)\s+habits?", low):
        return _mk("HABIT_STATUS", {}, 0.95, matched_by="habits.show")
    # Exclude habit queries from file-read
    if re.match(r"^(?:show|list|read)\s+habits?$", low):
        return _mk("HABIT_STATUS", {}, 0.95, matched_by="habits.show")
    m = re.match(r"^(read|show)\s+(~?/?[\w./\-]+(?:\.[\w]+)?)", raw, re.I)
    if m:
        path = m.group(2).strip()
        # Require the candidate to look like an actual path: contain a slash,
        # an extension, leading ~/, or have at least one path segment delimiter.
        # Bare English words ("me", "the") and natural-language tokens are
        # excluded so phrases like "show me physics news" do not become file
        # reads.
        looks_like_path = (
            "/" in path or "." in path or path.startswith("~")
        )
        if looks_like_path:
            return _mk("READ_FILE", {"path": path}, 0.9,
                       matched_by="file.read_show", entities={"path": path})

    m = re.search(
        r"(fix|debug|repair|correct|patch)\s+.{0,30}?([\w./~ -]+\.py)\b",
        raw,
        re.I)
    if m:
        path = m.group(2).strip()
        return _mk("FIX_FILE", {"path": path}, 0.92,
                   matched_by="file.fix_python", entities={"path": path})

    m = re.search(
        r"(fix|debug|repair|correct|patch)\s+(?:the\s+)?(?:script|file|code)(?:\s+(?:in|at|located\s+at))?\s+(.+)$",
        raw,
        re.I)
    if m:
        path = (m.group(2) or "").strip()
        extracted = _extract_path_from_text(path) or path
        return _mk("FIX_FILE", {"path": extracted}, 0.9,
                   matched_by="file.fix_generic", entities={"path": extracted})

    if re.match(r"^(?:fix|debug|repair|correct|patch)\b", low):
        guessed = _extract_path_from_text(raw)
        if guessed:
            return _mk("FIX_FILE", {
                       "path": guessed}, 0.86, matched_by="file.fix_fallback", entities={"path": guessed})

    # Message starts with a file path (dropped file + instruction format)
    # e.g. "/home/user/research-doc.pdf , tell me what this is about"
    if raw.strip().startswith(("/", "~/")):
        if ".pdf" in raw.lower():
            _pdf_p = _extract_pdf_path(raw)
            if _pdf_p:
                return _mk("ANALYZE_PDF", {"path": _pdf_p, "instruction": raw},
                           0.95, matched_by="analyze.path_first_pdf")

    # Per-file / "put it in a document" follow-up against the last analysed
    # folder. "i want the summary for each file", "summarise each document",
    # "a docx, doc, pdf — i don't care" used to fall into CHAT (then the model
    # confabulated fake filenames and a document it never wrote). Route them to
    # ANALYZE_PDF_FOLDER's per-file mode, which summarises every doc and writes a
    # real .docx. Only fires when we actually have a folder in context.
    global _last_used_path
    if _last_used_path and os.path.isdir(os.path.expanduser(str(_last_used_path))):
        _per_file_intent = bool(
            re.search(r"\b(?:each|every|per[\s-]?|individual|one[\s-]by[\s-]one)\b", low)
            and re.search(r"(?:file|files|document|documents|doc|docs|pdf|pdfs|summar)", low)
        ) or bool(re.search(r"summar\w+\s+(?:for\s+|of\s+)?(?:each|every|all)\b", low))
        # Doc-output follow-up ("put it in a document", "as a docx", a bare
        # "docx, doc, pdf — i don't care"). This must be REFERENTIAL to the
        # analysis we just ran — NOT a fresh "create a document about <topic>",
        # which is a generative request (GENERATE_DOCUMENT). Guards:
        #   - no explicit path/topic ("about X", "on X", "explaining X"),
        #   - either a back-reference (it/them/these/the summaries/each) OR a
        #     short elliptical reply (a one-liner answering "which format?").
        _has_path_token = bool(re.search(r"[~/]", raw))
        _fresh_topic = bool(re.search(r"\b(?:about|regarding|explaining|describing|on\s+the\s+(?:topic|subject)|on\s+\w)", low))
        _referential = bool(re.search(r"\b(?:it|them|these|those|the\s+summar\w+|each|per[\s-]?file|the\s+results?|the\s+docs?|the\s+files?)\b", low))
        _short_reply = len(low.split()) <= 7
        _put_in_doc = bool(re.search(
            r"\b(?:put|save|export|turn|compile|write)\b[^.]{0,30}\b(?:in|into|to|as)\s+a?\s*(?:document|docx|doc|word|pdf|file)\b", low))
        _bare_fmt = bool(re.search(r"\b(?:document|docx|word\s+doc)\b", low))
        # Two+ distinct explicit format tokens with no topic = a "which format?"
        # reply (e.g. "docx, doc, pdf — i don't care"). "doc" is excluded here
        # because it over-matches "document"/"doctor".
        _multi_fmt = len(set(re.findall(r"\b(?:docx|pdf|word|markdown|md|odt|rtf)\b", low))) >= 2
        _doc_output_intent = (
            (not _has_path_token) and (not _fresh_topic)
            and (_put_in_doc or _multi_fmt or (_bare_fmt and (_referential or _short_reply)))
        )
        if _per_file_intent or _doc_output_intent:
            _fmt = "docx"
            if re.search(r"\bmark ?down\b|\bmd\b", low):
                _fmt = "md"
            return _mk("ANALYZE_PDF_FOLDER",
                       {"folder": _last_used_path, "recursive": True,
                        "per_file": True, "_force_background": True, "format": _fmt},
                       0.9, matched_by="analyze.pdf_per_file_followup")

    # summarize / analyse / read / look at: conversation vs file/path
    # Matches both:
    #   "summarise /path"  "analyse and read /path"  "read /path"
    #   "can you look at /path"  "look at, read, and analyse /path"
    m = re.match(
        r"^(?:please\s+)?(?:can\s+you\s+)?(?:summari[sz]e|analyse|analyze|read|look\s+at)"
        r"(?:[,\s]+(?:and\s+)?(?:summari[sz]e|analyse|analyze|read|look\s+at))*"
        r"[,\s]+(?:this\s+)?(.+)$",
        raw, re.I,
    ) or re.match(r"^(?:please\s+)?(?:summari[sz]e|analyse|analyze|read)\s+(.+)$", raw, re.I)
    if m:
        target = m.group(1).strip()
        # If target contains an absolute path, extract from the first / or ~/
        # This handles typos, extra verbs, and multi-word prefixes cleanly.
        _path_hit = re.search(r'[~/].*$', target)
        if _path_hit:
            # Capture the FULL remainder (paths legitimately contain spaces,
            # parens, ×, –, accents — e.g. physics dirs). Then prefer the LONGEST
            # existing prefix so a real spaced path is kept intact while trailing
            # chatter ("... and tell me about it") is dropped. Truncating at the
            # first space (old [^\s]+) cut spaced paths and led to phantom paths
            # the model then "summarised" from the name.
            _cand = _path_hit.group(0).strip()
            if not os.path.exists(os.path.expanduser(_cand)):
                _toks = _cand.split()
                for _k in range(len(_toks), 0, -1):
                    _sub = " ".join(_toks[:_k])
                    if os.path.exists(os.path.expanduser(_sub)):
                        _cand = _sub
                        break
            target = _cand
        else:
            # Fallback: strip leading filler words
            target = re.sub(
                r"^(?:and\s+)?(?:summari[sz]e|analyse|analyze|summaise|read|look\s+at|the|this|file|directory|folder)\s+",
                "", target, flags=re.I,
            ).strip()
        # If the target contains a PDF, use the robust extractor to avoid
        # Unicode/em-dash path issues (captures everything before the extension)
        if ".pdf" in target.lower():
            _pdf_path = _extract_pdf_path(raw)
            if _pdf_path:
                return _mk("ANALYZE_PDF", {"path": _pdf_path, "instruction": raw},
                           0.95, matched_by="analyze.summarize_pdf")
        expanded = os.path.expanduser(target)
        _has_file_ext = bool(re.search(r'\.[a-zA-Z0-9]{1,5}$', target.strip()))
        _has_path_prefix = target.strip().startswith(("/", "~", "./", "../"))
        _path_exists = os.path.exists(expanded)
        if (_has_path_prefix or _path_exists or _has_file_ext):
            abs_path = os.path.abspath(expanded)
            if _path_exists:
                _last_used_path = abs_path
            # Route PDFs → ANALYZE_PDF, CSVs → ANALYZE_CSV, everything else →
            # SUMMARIZE_FILE
            if abs_path.lower().endswith(".pdf"):
                return _mk("ANALYZE_PDF", {"path": abs_path, "instruction": raw},
                           0.95, matched_by="analyze.summarize_pdf")
            if abs_path.lower().endswith(".csv"):
                return _mk("ANALYZE_CSV", {"path": abs_path},
                           0.95, matched_by="analyze.summarize_csv")
            if abs_path.lower().endswith(
                    (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".tif")):
                return _mk("ANALYZE_IMAGE", {"path": abs_path},
                           0.95, matched_by="analyze.image")
            return _mk("SUMMARIZE_FILE", {
                       "path": abs_path}, 0.92, matched_by="analyze.summarize_file")
        # Referential: "summarise the pdfs in that directory" with no explicit path
        if any(w in low for w in ["pdf", "pdfs"]) and any(
                w in low for w in ["that", "there", "those", "the folder", "the directory"]):
            if _last_used_path and os.path.isdir(_last_used_path):
                return _mk("ANALYZE_PDF_FOLDER", {"folder": _last_used_path, "recursive": True},
                           0.90, matched_by="analyze.pdf_referential")
        # Referential: "analyse the recent/last screenshot" → resolve newest
        # screenshot file and OCR it, rather than confabulating from memory.
        if "screenshot" in low:
            _shot = _eli_latest_screenshot()
            if _shot:
                return _mk("ANALYZE_IMAGE", {"path": _shot},
                           0.93, matched_by="analyze.recent_screenshot")
        # Live screen: "look at my screen", "read the screen", "analyse my display"
        # → capture + vision, not a memory lookup.
        if re.search(r"\b(?:my|the)\s+(?:screen|display|monitor)\b", low):
            return _mk("SCREEN_READ_ANALYZE", {}, 0.92,
                       matched_by="screen.analyze_referential")
        # A bare generic file-noun ("read the document", "summarize the file")
        # with no actual filename/path is NOT a memory topic — there is nothing
        # stored to "recall". Defer to CHAT so ELI asks which file, rather than
        # recalling a phantom "document"/"file" topic.
        if target.strip().lower() in {
            "document", "documents", "doc", "docs", "file", "files",
            "it", "this", "that",
        }:
            return _mk("CHAT", {"message": raw}, 0.6, matched_by="chat.vague_file_ref")
        # Conversation back-reference ("read my previous reply", "what did I just
        # say", "read the response") is NOT a long-term topic to keyword-search —
        # it refers to the LIVE transcript. MEMORY_RECALL here does a LIKE on the
        # words "previous"/"reply" and dredges up days-old "from a previous
        # session…" chatter while never finding the actual last turn. Defer to
        # CHAT, whose context already carries the recent conversation_turns.
        if (re.search(r"\b(?:previous|last|that|my|the)\s+(?:reply|message|response|answer|comment)\b", low)
                or re.search(r"\bwhat\s+(?:did\s+)?i\s+(?:just\s+)?(?:say|said|wrote|ask|asked)\b", low)
                or re.search(r"\bread\s+(?:the|my)\s+(?:response|reply|message|answer)\b", low)
                or re.search(r"\bthe\s+(?:last\s+)?thing\s+i\s+(?:just\s+)?said\b", low)):
            return _mk("CHAT", {"message": raw}, 0.6, matched_by="chat.conversation_self_ref")
        return _mk("MEMORY_RECALL", {"query": target},
                   0.8, matched_by="memory.summarize_topic")

    # PDF analysis
    if any(w in low for w in ["pdf", "pdfs"]) and any(w in low for w in [
            "analyze", "analyse", "summarize", "summarise", "read", "extract"]):
        path_match = _extract_pdf_path(raw)
        if path_match:
            return _mk("ANALYZE_PDF", {"path": path_match, "instruction": raw}, 0.95,
                       matched_by="analyze.pdf", entities={"path": path_match})
        # Referential: "the pdfs in that directory" → use last resolved path
        _is_referential = any(w in low for w in ["that", "there", "those", "the folder", "the directory"])
        if _is_referential and _last_used_path and os.path.isdir(_last_used_path):
            return _mk("ANALYZE_PDF_FOLDER", {"folder": _last_used_path, "recursive": True},
                       0.90, matched_by="analyze.pdf_referential")
        return _mk("ANALYZE_PDF", {"path": ".", "instruction": raw},
                   0.85, matched_by="analyze.pdf_default")

    # CSV analysis
    if any(w in low for w in ["csv", "spreadsheet"]) or (
            any(w in low for w in ["read", "show"]) and ".csv" in low):
        if any(w in low for w in [
               "analyze", "analyse", "summarize", "summarise", "read", "parse", "show"]):
            m = re.search(r'([~/\w.\-/ ]+\.csv)\b', raw, re.I)
            if m:
                return _mk("ANALYZE_CSV", {"path": m.group(1).strip(
                )}, 0.95, matched_by="analyze.csv", entities={"path": m.group(1).strip()})

    # ------------------------------------------------------------
    # 10) DOCUMENT / NOTES / GENERATION
    # ------------------------------------------------------------
    note_m = re.match(
        r'^(?:write|save|create|make|add)\s+(?:a\s+)?notes?[:\s]+(?:saying\s+)?(.+)$',
        raw,
        re.I)
    if note_m:
        note_text = note_m.group(1).strip()
        return _mk("WRITE_NOTE", {"text": note_text}, 0.97,
                   matched_by="notes.write", entities={"text": note_text})

    note_m2 = re.match(r'^(?:write|add)\s+note\s+(.+)$', raw, re.I)
    if note_m2:
        note_text = note_m2.group(1).strip()
        return _mk("WRITE_NOTE", {"text": note_text}, 0.95,
                   matched_by="notes.add", entities={"text": note_text})

    if "generate" in low and "document" in low and not _GENERATION_COMPLAINT:
        # Strip the command framing, then peel any stacked leading connectives
        # ("a document with proposals…" → "proposals…"). Collapse whitespace so a
        # removed mid-word doesn't leave gaps or a leaked "with"/"for" in the topic
        # (user-reported: filename "with_proposals_for_upgrades_for_yourself.md").
        topic = re.sub(r'\b(generate|create|write|make|draft|produce|prepare)\b', ' ', low)
        topic = re.sub(r'\bdocuments?\b', ' ', topic)
        topic = re.sub(r'\s+', ' ', topic).strip()
        topic = re.sub(
            r'^(?:(?:a|an|the|with|about|on|of|for|regarding|covering|containing|'
            r'detailing|outlining|me|please)\s+)+', '', topic).strip()
        # keep the user's own pronouns ("yourself") so the executor can detect a
        # self-referential document and ground it in ELI's real architecture.
        return _mk("GENERATE_DOCUMENT", {"topic": topic or raw, "_raw_user_text": raw,
                   "use_advanced_generator": True, "use_gguf_only": True,
                   "forbid_ollama": True}, 0.95, matched_by="doc.generate_generic", entities={"topic": topic or raw})

    # ── Intent stages (tail group) ────────────────────────────────────────────
    # The tail of this function now lives in eli.execution.routing.intents as
    # ordered stages. They run in the legacy order and the first to match wins,
    # exactly as the inline guards did; a stage returns None to pass.
    for _stage_name, _stage in INTENT_STAGES_TAIL:
        _hit = _stage(text, raw, low, text_l)
        if _hit is ROUTE_NONE:
            return None          # the stage ended routing outright
        if _hit is not None:
            return _hit


    _eli_pipeline_trace("router.fallback_chat_selected", text=str(raw)[:160])
    return _mk("CHAT", {"message": raw}, 0.6, matched_by="fallback.chat")


# Backward-compatible route alias
_ROUTE_CORE = route


# Open-typo guard: catch common STT typos for OPEN_APP routing
# Handles common STT/typing variants before fallback chat.
import re as _eli_open_typo_re

def _eli_open_typo_norm(v):
    t = _eli_open_typo_re.sub(r"\s+", " ", str(v or "").lower()).strip(" .,!?:;")
    t = _eli_open_typo_re.sub(r"^opens\s+", "open ", t)
    t = _eli_open_typo_re.sub(r"\bspot\s*ify\b|\bspot\s+if\s+i\b|\bspotifyify\b|\bpotify\b", "spotify", t)
    return t



# Media query cleaner: normalize search query before media routing
# Clean residual open/play media query mistakes after routing.
import re as _eli_mqc_re




# Identity route wrapper, tiny-chat guard, follow-up pass-through,
# and persona-route override all use the top-level re import.



# voice_runtime_contract_guard
# Deterministic voice-command grammar for common local-control requests.
# This layer fixes ASR wording variants before the broad tiny-fragment/fallback
# chat guards see them. It contains no user-machine absolute paths.

from eli.execution.routing.voice import (  # noqa: F401
    _eli_voice_contract_norm,
    _eli_voice_contract_response,
    _eli_voice_contract_route,
    _eli_voice_contract_text_from_call,
    _eli_voice_contract_wrap_callable,
)


_eli_voice_contract_route_names = (
    "route",
    "route_text",
    "route_command",
    "route_intent",
    "parse",
    "parse_intent",
    "parse_command",
    "classify",
    "classify_intent",
)

for _name in _eli_voice_contract_route_names:
    _fn = globals().get(_name)
    if callable(_fn) and _fn is not _eli_voice_contract_route:
        globals()[_name] = _eli_voice_contract_wrap_callable(_fn)

for _obj in list(globals().values()):
    if isinstance(_obj, type):
        for _name in _eli_voice_contract_route_names:
            try:
                _method = getattr(_obj, _name, None)
                if callable(_method):
                    setattr(_obj, _name, _eli_voice_contract_wrap_callable(_method))
            except Exception:
                _SWLOG.debug("suppressed exception", exc_info=True)

# portable_runtime_contract_v3_router_hook


# ELI_LIVE_ROUTE_SECOND_FIX_20260505
# High-priority route wrapper for cases the base router currently lets fall into CHAT/search.
from eli.execution.routing.lrf import (  # noqa: F401
    _eli_lrf_mk,
    _eli_lrf_pre_route,
    _eli_lrf_word_to_int,
)





from eli.execution.routing.personal_memory import (  # noqa: F401
    _eli_pm_mk,
    _eli_pm_pre_route,
    _eli_pm_wants_personal_memory,
    _eli_pm_wants_raw_memory_truth,
)
from eli.execution.routing.self_ops import _eli_self_improvement_phrase_guard  # noqa: F401


# --- ELI runtime/cognition/failure high-priority route guard ---
from eli.execution.routing.memory_runtime import (  # noqa: F401
    _eli_memory_runtime_route_lock_result,
    _eli_memory_runtime_route_lock_should_trigger,
    _eli_recent_memory_processing_question,
    _eli_runtime_cognition_failure_guard,
    _eli_self_report_recent_updates_question,
)


# --- end ELI runtime/cognition/failure high-priority route guard ---




# =============================================================================

# =============================================================================
# ELI IDENTITY / NAME-SOURCE ROUTE FIX - SINGLE CLOSURE-SAFE INSTALL
# =============================================================================
# =============================================================================

# =============================================================================
# ELI FINAL RUNTIME STATUS ROUTE CONTRACT
# Runtime identity/status questions must route to grounded runtime evidence.
# Quick mode may display compact output downstream; non-quick modes must synthesize.
# =============================================================================
# =============================================================================

# =============================================================================
# ELI FINAL MEMORY QUESTION ROUTE CONTRACT
# Separates "what do you know about me" from "explain memory internals".
# =============================================================================
# =============================================================================

# =============================================================================
# ELI PERSONAL_MEMORY_SUMMARY COMPATIBILITY
# PERSONAL_MEMORY_SUMMARY is now a first-class evidence action. Keep this
# wrapper only to preserve the final route binding and metadata hygiene; do
# not collapse summary requests back into the old deep-response override.
# =============================================================================
# =============================================================================

# =============================================================================
# ELI IDENTITY SCOPE CONTRACT
# USER_IDENTITY_SUMMARY must carry the original question and must not be treated
# as a generic "what do you know about me" memory/profile dump.
# =============================================================================
# ELI_PHASE51_IDENTITY_SCOPE_HELPER_SHELL_PRUNE_V1
# Standalone helper retained from the obsolete identity-scope Try shell.
from eli.execution.routing.profile import (  # noqa: F401
    _eli_identity_scope_for_text,
    _eli_is_explicit_preference_request,
    _eli_is_full_profile_dump,
    _eli_is_generic_profile_inventory,
    _eli_is_memory_count_question,
    _eli_profile_scope_low,
    _eli_profile_scope_result,
)

import re as _eli_recent_mem_re
from eli.utils.log import get_logger

log = get_logger(__name__)



# =============================================================================
# ELI SELF-REPORT RECENT UPDATES ROUTE
# Self/status questions asking what updates/checks have happened must not fall
# into generic CHAT, because GGUF will invent plausible maintenance activity.
# =============================================================================
# ELI_PHASE53_HELPER_TRYBLOCK_HOIST_DIAGNOSTIC_SHELL_RETIREMENT_V1: self_report_recent_updates_helper
# Phase53: helper/import-only Try shell hoisted to module scope.
# The former except branch only printed a diagnostic and swallowed import-time
# helper-definition failure; that stale diagnostic shell is intentionally retired.





# =============================================================================
# ELI GUI AUDIT ACTUAL-SCAN PROOF ROUTE V2
# Catches direct "did you actually scan/read file in full" probes.
# =============================================================================
# ELI_PHASE53_HELPER_TRYBLOCK_HOIST_DIAGNOSTIC_SHELL_RETIREMENT_V1: gui_actual_scan_helper
# Phase53: helper/import-only Try shell hoisted to module scope.
# The former except branch only printed a diagnostic and swallowed import-time
# helper-definition failure; that stale diagnostic shell is intentionally retired.

def _eli_gui_audit_actual_scan_v2(text):
    q = " ".join(str(text or "").lower().split())
    if not q:
        return False

    file_hit = any(x in q for x in (
        "eli/gui/eli_pro_audio_gui_mki.py",
        "eli_pro_audio_gui_mki.py",
        "gui file",
        "audio gui",
    ))

    scan_hit = any(x in q for x in (
        "actually scan",
        "actually scanned",
        "actually read",
        "did you scan",
        "did you read",
        "scan the file",
        "read the file",
        "in full",
        "full file",
        "whole file",
        "entire file",
    ))

    return bool(file_hit and scan_hit)



# =============================================================================
# ELI_MEMORY_RUNTIME_ROUTE_LOCK_V1
# Memory-runtime architecture/control questions are first-class grounded telemetry.
# They must not be stolen by generic CHAT, OPEN_APP, or personal-memory/profile
# routing. This does not answer the question; it only guarantees the correct
# evidence action.
# =============================================================================
# ELI_PHASE53_HELPER_TRYBLOCK_HOIST_DIAGNOSTIC_SHELL_RETIREMENT_V1: memory_runtime_lock_helpers
# Phase53: helper/import-only Try shell hoisted to module scope.
# The former except branch only printed a diagnostic and swallowed import-time
# helper-definition failure; that stale diagnostic shell is intentionally retired.



# =============================================================================


# ELI_PATCH_FINAL_PERSONAL_MEMORY_ROUTE_PRECEDENCE_AFTER_ROUTE_LOCK_20260511
# Final route wrapper: installed after late memory-runtime route locks.
# The phrase logic now lives in eli.execution.route_contracts.
# This wrapper remains at EOF only to preserve precedence over legacy route wrappers.











# --- Phase 11: multi-PDF route contract wrapper -----------------------
# Purpose:
#   Existing route branches call _extract_pdf_path(raw), which returns only the
#   first PDF. Phase 10 added _extract_pdf_paths(raw). This wrapper preserves
#   existing route behaviour while enriching ANALYZE_PDF args with paths=[...].
# --- Phase 48: standalone Phase11 multi-PDF enrichment helper -----------
# Phase38 flattened dispatch uses this helper directly. The older Phase11
# route/route_intent capture shell has been removed as dead pre-marker debt.



# =============================================================================
# ELI_PHASE33_CANONICAL_PUBLIC_ROUTER_SURFACE_EXPORT
#
# Phase 32 proved that the exported router surfaces had drifted:
#   - route / route_intent were on the latest final route chain
#   - route_command was partially stale
#   - parse_command / classify were more stale
#
# Do not let these historical aliases capture old intermediate route wrappers.
# Until router_enhanced.py is structurally flattened, all exported public routing
# surfaces must resolve to the single final route() authority below.
# =============================================================================
try:
    _ELI_PHASE33_FINAL_CANONICAL_ROUTE = route

    if callable(_ELI_PHASE33_FINAL_CANONICAL_ROUTE):

        print(
            flush=True,
        )
    else:
        log.debug(
            "[ROUTER] canonical public routing surface export skipped: final route is not callable",
        )

except Exception as _eli_phase33_router_surface_err:
    log.debug(
        f"[ROUTER] canonical public routing surface export failed: "
        f"{_eli_phase33_router_surface_err}",
    )
# =============================================================================


# =============================================================================
# ELI_PHASE38_FLATTENED_CANONICAL_DISPATCH_V1
# =============================================================================
#
# Purpose:
#   Replace the active nested wrapper-chain public router surface with one
#   explicit canonical dispatch pipeline, while preserving the semantics proven
#   by Phase 36 v2 and the live-stage order proven by Phase 37.
#
# Important:
#   This phase intentionally does NOT delete historical wrapper source blocks.
#   It shadows them as the final exported route surface. Once semantic parity is
#   proven, a dedicated pruning pass can safely remove obsolete rebinding debt.
# =============================================================================

try:
    from eli.execution.portable_intent_contract import try_route as _eli_phase38_portable_try_route
except Exception:
    _eli_phase38_portable_try_route = None


def _eli_phase38_enrich_pdf_if_needed(raw, result):
    enricher = globals().get("_eli_phase11_enrich_pdf_route")
    if callable(enricher):
        try:
            return enricher(raw, result)
        except Exception:
            return result
    return result




















def _eli_phase38_open_typo_or_core_route(raw, *args, **kwargs):
    t = _eli_open_typo_norm(raw)

    if t in {"open spotify", "launch spotify", "start spotify", "spotify open"}:
        return {
            "action": "OPEN_APP",
            "args": {"name": "spotify"},
            "confidence": 0.999,
            "meta": {"matched_by": "eli.open_typo.spotify", "normalized": t},
        }

    if t in {"open browser", "open web browser", "browser", "launch browser", "start browser"}:
        return {
            "action": "OPEN_APP",
            "args": {"name": "browser"},
            "confidence": 0.999,
            "meta": {"matched_by": "eli.open_typo.browser", "normalized": t},
        }

    core = globals().get("_ROUTE_CORE")
    if callable(core):
        return core(raw, *args, **kwargs)

    return {
        "action": "CHAT",
        "args": {"message": str(raw or "")},
        "confidence": 0.25,
        "meta": {"matched_by": "phase38.missing_route_core_fallback"},
    }


def _eli_media_contract_post(raw, result):
    """Final media-routing contract for legacy GUI/STT command shapes."""
    try:
        # A chained utterance was already split into MULTI_COMMAND by the prepass
        # (e.g. "play X on youtube AND close spotify" → [play X on youtube, close
        # spotify]). Do NOT collapse it back into one PLAY_MEDIA whose query swallows
        # "and close spotify" and plays on the wrong platform — let the executor run
        # each segment in order.
        if isinstance(result, dict) and str(result.get("action") or "").upper() == "MULTI_COMMAND":
            return result
        original = str(raw or "")
        low = re.sub(r"\s+", " ", original.lower()).strip(" .,!?:;")
        if not low:
            return result
        text = re.sub(r"^(?:eli|vera|computer|assistant|buddy)\s+", "", low).strip()

        def _target(value: str) -> str:
            target = re.sub(r"^(?:the|a|an)\s+", "", str(value or "").strip(" .,!?:;"))
            target = target.replace("prime video", "primevideo")
            target = target.replace("disney plus", "disneyplus")
            if target in {"tv", "and tv", "television", "the tv"}:
                return "mpv"
            return target

        def _play(target: str, query: str, matched_by: str):
            return _mk(
                "PLAY_MEDIA",
                {"target": _target(target), "query": str(query or "").strip()},
                0.97,
                matched_by=matched_by,
                entities={"target": _target(target), "query": str(query or "").strip()},
            )

        if (
            re.match(r"^play\b", text)
            and "situation brief" in text
            and "conversation history" in text
        ):
            return _mk("CHAT", {"message": original}, 0.99, matched_by="media.internal_prompt_echo_guard")

        if re.fullmatch(r"play(?:\s+the)?", text) or re.fullmatch(r"play\s+.+\s+(?:by|on)", text):
            return _mk(
                "NOOP",
                {"message": "Incomplete command: tell me what to play and the artist name or service."},
                0.99,
                matched_by="media.incomplete_play_guard",
            )

        # ── "youtube web/website" variants → browser (yt-dlp resolves watch URL) ─
        _ytw = r"youtube\s+web(?:site)?"
        m = re.match(rf"^play\s+(.+?)\s+on\s+({_ytw})\s*$", text)
        if m:
            return _play(m.group(2), m.group(1), "media.play_on_youtube_web_contract")

        m = re.match(rf"^play\s+({_ytw})\s+(.+)$", text)
        if m:
            return _play(m.group(1), m.group(2), "media.play_youtube_web_prefix_contract")

        m = re.match(rf"^(.+?)\s+(?:by\s+.+?\s+)?on\s+({_ytw})\s*$", text)
        if m:
            return _play(m.group(2), m.group(1), "media.query_on_youtube_web_contract")

        m = re.match(r"^play\s+(youtube|spotify|soundcloud)\s+(.+)$", text)
        if m:
            return _play(m.group(1), m.group(2), "media.play_target_prefix_contract")

        m = re.match(r"^(youtube|spotify|soundcloud)\s+play\s+(.+)$", text)
        if m:
            return _play(m.group(1), m.group(2), "media.target_play_prefix_contract")

        m = re.match(r"^on\s+(youtube|spotify|soundcloud)\s+play\s+(.+)$", text)
        if m:
            return _play(m.group(1), m.group(2), "media.on_target_play_contract")

        m = re.match(r"^play\s+(.+?)\s+on\s+(youtube|spotify|soundcloud|mpv)\s*$", text)
        if m:
            return _play(m.group(2), m.group(1), "media.play_query_on_target_contract")

        m = re.match(r"^play\s+(.+?)\s+on\s+youtube\.com\s*$", text)
        if m:
            return _play("youtube website", m.group(1), "media.play_on_youtube_dot_com_contract")

        m = re.match(r"^play\s+(.+\s+by\s+.+)$", text)
        if m:
            return _play("spotify", m.group(1), "media.play_song_by_artist_contract")

        # Implied song request — "title by artist" with no "play" verb.
        # Matches "all eyez on me by tupac" / "bohemian rhapsody by queen".
        # Negative lookahead excludes sentences starting with action/question verbs
        # so "explain X by Y" / "search X by Y" / "what is X by Y" fall through.
        m = re.match(
            r"^(?!(?:search|find|look|show|get|tell|what|how|why|when|where|which|who"
            r"|is|are|was|were|will|can|could|should|would|do|does|did"
            r"|make|create|run|start|stop|open|close|pause|explain|describe|define"
            r"|read|write|list|audit|check|scan|review|analyse|analyze"
            r"|created?|written?|made|designed?|built|developed?)\b)"
            r"(.{4,70})\s+by\s+([a-z][a-z\s]{1,35})$",
            text,
        )
        if m:
            return _play("spotify", raw, "media.implied_song_by_artist")

        m = re.match(r"^(open|launch|start|close|quit|kill|exit)\s+(.+?)$", text)
        if m:
            verb = m.group(1)
            name = _clean_app_name(m.group(2)).lower()
            known = (
                name in WEBSITE_ALIASES
                or name in MEDIA_APPS
                or name in DESKTOP_APP_PRIORITY
                or name in APP_ALIASES
                or name in {"browser", "youtube"}
            )
            if known:
                if verb in {"open", "launch", "start"} and name in WEBSITE_ALIASES:
                    return _mk(
                        "OPEN_URL",
                        {"url": WEBSITE_ALIASES[name]},
                        0.98,
                        matched_by="app.wake_prefix_contract.web_alias",
                    )
                action = "CLOSE_APP" if verb in {"close", "quit", "kill", "exit"} else "OPEN_APP"
                return _mk(action, {"name": name}, 0.98, matched_by="app.wake_prefix_contract")

        controls = {
            "pause": ("PAUSE_MEDIA", "pause"),
            "resume": ("PLAY_MEDIA", "play"),
            "play": ("PLAY_MEDIA", "play"),
            "stop": ("STOP_MEDIA", "stop"),
            "next": ("NEXT_MEDIA", "next"),
            "skip": ("NEXT_MEDIA", "next"),
            "previous": ("PREVIOUS_MEDIA", "previous"),
            "prev": ("PREVIOUS_MEDIA", "previous"),
            "back": ("PREVIOUS_MEDIA", "previous"),
        }
        target_words = "|".join(sorted(map(re.escape, MEDIA_APPS + ["and tv", "tv"]), key=len, reverse=True))

        m = re.match(rf"^({'|'.join(controls)})\s+({target_words})$", text)
        if m:
            action, command = controls[m.group(1)]
            target = _target(m.group(2))
            return _mk(
                action,
                {"target": target},
                0.97,
                matched_by="media.targeted_control_contract",
                entities={"target": target, "command": command},
            )

        m = re.match(rf"^({target_words})\s+({'|'.join(controls)})$", text)
        if m:
            action, command = controls[m.group(2)]
            target = _target(m.group(1))
            return _mk(
                action,
                {"target": target},
                0.97,
                matched_by="media.reverse_targeted_control_contract",
                entities={"target": target, "command": command},
            )

        m = re.match(r"^(pause|resume)\s+(.+)$", text)
        if m:
            command = "play" if m.group(1) == "resume" else "pause"
            target = _target(m.group(2))
            if target and len(target.split()) <= 5:
                return _mk(
                    "MEDIA_CONTROL",
                    {"command": command, "target": target, "type": "dynamic"},
                    0.91,
                    matched_by="media.dynamic_control_contract",
                    entities={"target": target, "command": command},
                )
    except Exception:
        return result
    return result






def _eli_phase38_bottom_core_dispatch(raw, *args, **kwargs):
    identity = _eli_phase38_identity_contract(raw)
    if identity is not None:
        return identity

    result = _eli_phase38_open_typo_or_core_route(raw, *args, **kwargs)
    result = _eli_phase38_media_query_cleaner_post(result)
    result = _eli_phase38_tiny_fragment_post(raw, result)
    return result


def _eli_phase38_voice_portable_persona_lower_dispatch(raw, *args, **kwargs):
    if callable(_eli_phase38_portable_try_route):
        try:
            portable = _eli_phase38_portable_try_route(raw)
            if portable is not None:
                return portable
        except Exception:
            _SWLOG.debug("suppressed exception", exc_info=True)

    voice = globals().get("_eli_voice_contract_route")
    if callable(voice):
        try:
            shortcut = voice(raw)
            if shortcut is not None:
                return shortcut
        except Exception:
            _SWLOG.debug("suppressed exception", exc_info=True)

    persona = _eli_phase38_persona_override_contract(raw)
    if persona is not None:
        return persona

    followup = _eli_phase38_followup_passthrough_contract(raw)
    if followup is not None:
        return followup

    return _eli_phase38_bottom_core_dispatch(raw, *args, **kwargs)


def _eli_phase38_lower_contract_dispatch(raw, *args, **kwargs):
    lrf_pre = globals().get("_eli_lrf_pre_route")
    if callable(lrf_pre):
        try:
            out = lrf_pre(raw)
            if out is not None:
                return out
        except Exception:
            _SWLOG.debug("suppressed exception", exc_info=True)

    return _eli_phase38_voice_portable_persona_lower_dispatch(raw, *args, **kwargs)


def _eli_phase38_personal_memory_guard_dispatch(raw, *args, **kwargs):
    pm_pre = globals().get("_eli_pm_pre_route")
    if callable(pm_pre):
        try:
            out = pm_pre(raw)
            if out is not None:
                return out
        except Exception:
            _SWLOG.debug("suppressed exception", exc_info=True)

    return _eli_phase38_lower_contract_dispatch(raw, *args, **kwargs)


def _eli_phase38_self_improvement_dispatch(raw, *args, **kwargs):
    sig = globals().get("_eli_self_improvement_phrase_guard")
    if callable(sig):
        try:
            guarded = sig(raw)
            if guarded:
                return guarded
        except Exception:
            _SWLOG.debug("suppressed exception", exc_info=True)

    return _eli_phase38_personal_memory_guard_dispatch(raw, *args, **kwargs)


def _eli_phase38_runtime_cognition_failure_dispatch(raw, *args, **kwargs):
    rcfg = globals().get("_eli_runtime_cognition_failure_guard")
    if callable(rcfg):
        try:
            guarded = rcfg(raw)
            if guarded:
                return guarded
        except Exception:
            _SWLOG.debug("suppressed exception", exc_info=True)

    return _eli_phase38_self_improvement_dispatch(raw, *args, **kwargs)


def _eli_phase38_identity_name_dispatch(raw, *args, **kwargs):
    identity_name = _eli_phase38_identity_name_source_single_safe_contract(raw)
    if identity_name is not None:
        return identity_name

    return _eli_phase38_runtime_cognition_failure_dispatch(raw, *args, **kwargs)


def _eli_phase38_runtime_status_dispatch(raw, *args, **kwargs):
    status_or_name = _eli_phase38_runtime_status_or_name_source_contract(raw)
    if status_or_name is not None:
        return status_or_name

    return _eli_phase38_identity_name_dispatch(raw, *args, **kwargs)


def _eli_phase38_final_memory_dispatch(raw, *args, **kwargs):
    memory_contract = _eli_phase38_final_memory_question_contract(raw)
    if memory_contract is not None:
        return memory_contract

    return _eli_phase38_runtime_status_dispatch(raw, *args, **kwargs)






def _eli_phase38_profile_scope_dispatch(raw, *args, **kwargs):
    low = _eli_profile_scope_low(raw)

    if _eli_is_explicit_preference_request(low):
        return _eli_profile_scope_result(
            "PERSONAL_MEMORY_SUMMARY",
            raw,
            "preferences_detail",
            matched_by="profile.scope_contract.preferences_detail",
        )

    if _eli_is_full_profile_dump(low):
        return _eli_profile_scope_result(
            "PERSONAL_MEMORY_SUMMARY",
            raw,
            "full_profile",
            matched_by="profile.scope_contract.full_profile",
        )

    out = _eli_phase38_final_memory_dispatch(raw, *args, **kwargs)
    out = _eli_phase38_personal_memory_summary_compat_post(out)
    out = _eli_phase38_identity_scope_post(raw, out)

    if isinstance(out, dict):
        action = str(out.get("action") or "").upper()
        if action == "PERSONAL_MEMORY_SUMMARY" and _eli_is_generic_profile_inventory(low):
            out = dict(out)
            out["args"] = dict(out.get("args") or {})
            out["args"]["question"] = str(raw or "")
            out["args"]["profile_scope"] = "inventory_only"

            out["meta"] = dict(out.get("meta") or {})
            out["meta"]["profile_scope_contract"] = "inventory_only"
            out["meta"]["forbid_preference_detail"] = True
            out["meta"]["forbid_project_detail"] = True
            out["meta"]["active_user_scoped"] = True
            return out

    return out


def _eli_phase38_memory_count_dispatch(raw, *args, **kwargs):
    if _eli_is_memory_count_question(raw):
        return {
            "action": "MEMORY_STATUS",
            "args": {
                "question": str(raw or ""),
                "memory_scope": "count_only",
            },
            "confidence": 0.995,
            "meta": {
                "matched_by": "memory.count.grounded_synthesis",
                "allow_chat_without_evidence": False,
                "requires_grounded_synthesis": True,
                "requires_output_validation": True,
                "quick_direct_allowed": True,
                "forbid_unverified_generation": True,
            },
        }

    out = _eli_phase38_profile_scope_dispatch(raw, *args, **kwargs)

    if isinstance(out, dict) and str(out.get("action") or "").upper() == "MEMORY_STATUS":
        if _eli_is_memory_count_question(raw):
            real_args = dict(out.get("args") or {})
            real_args["question"] = str(raw or "")
            real_args["memory_scope"] = "count_only"
            out["args"] = real_args

            meta = dict(out.get("meta") or {})
            meta["matched_by"] = "memory.count.grounded_synthesis.post_route"
            meta["requires_grounded_synthesis"] = True
            meta["requires_output_validation"] = True
            meta["quick_direct_allowed"] = True
            meta["forbid_unverified_generation"] = True
            out["meta"] = meta

    return out


def _eli_phase38_recent_memory_dispatch(raw, *args, **kwargs):
    if _eli_recent_memory_processing_question(raw):
        return {
            "action": "MEMORY_STATUS",
            "args": {
                "question": str(raw or ""),
                "memory_scope": "recent_processing",
            },
            "confidence": 0.995,
            "meta": {
                "matched_by": "memory.recent_processing_grounded",
                "task_family": "memory_runtime",
                "grounded_required": True,
                "forbid_chat_fallback": True,
                "forbid_fake_memory_activity": True,
                "allow_chat_without_evidence": False,
            },
        }

    return _eli_phase38_memory_count_dispatch(raw, *args, **kwargs)


def _eli_phase38_self_report_recent_updates_dispatch(raw, *args, **kwargs):
    if _eli_self_report_recent_updates_question(raw):
        return {
            "action": "SELF_REPORT",
            "args": {
                "question": str(raw or ""),
                "self_report_scope": "recent_updates",
            },
            "confidence": 0.995,
            "meta": {
                "matched_by": "self_report.recent_updates_grounded",
                "task_family": "self_report_runtime",
                "grounded_required": True,
                "forbid_chat_fallback": True,
                "forbid_fake_update_claims": True,
                "allow_chat_without_evidence": False,
            },
        }

    return _eli_phase38_recent_memory_dispatch(raw, *args, **kwargs)


def _eli_phase38_gui_actual_scan_dispatch(raw, *args, **kwargs):
    if _eli_gui_audit_actual_scan_v2(raw):
        return {
            "action": "GUI_RUNTIME_AUDIT",
            "args": {
                "question": str(raw or ""),
                "proof_requested": True,
                "audit_depth": "proof",
                "require_timestamps": True,
                "require_full_file_read_evidence": True,
            },
            "confidence": 0.995,
            "meta": {
                "matched_by": "router.gui_audit_actual_scan_proof_v2",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_audit",
                "forbid_chat_fallback": True,
            },
        }

    return _eli_phase38_self_report_recent_updates_dispatch(raw, *args, **kwargs)


def _eli_phase38_memory_runtime_lock_dispatch(raw, *args, **kwargs):
    if _eli_memory_runtime_route_lock_should_trigger(raw):
        return _eli_memory_runtime_route_lock_result(raw)

    return _eli_phase38_gui_actual_scan_dispatch(raw, *args, **kwargs)


def _eli_phase38_flattened_route(raw="", *args, **kwargs):
    text = str(raw or "")

    precedence = _eli_phase38_route_precedence_contract(text)
    if precedence is not None:
        return _eli_phase38_enrich_pdf_if_needed(text, precedence)

    result = _eli_phase38_memory_runtime_lock_dispatch(text, *args, **kwargs)
    return _eli_phase38_enrich_pdf_if_needed(text, result)


log.debug("[ROUTER] router_enhanced module loaded — canonical dispatch pipeline active")

# =============================================================================
# End ELI_PHASE38_FLATTENED_CANONICAL_DISPATCH_V1
# =============================================================================

# =============================================================================
# ELI_ROUTE_PRIORITY_PIPELINE_V1
# Explicit priority pipeline replacing nested phase-chain dispatch as the
# active exported route surface.
# =============================================================================
try:
    if not globals().get("_ELI_ROUTE_PRIORITY_PIPELINE_V1"):
        _ELI_ROUTE_PRIORITY_PIPELINE_V1 = True

        def _eli_route_priority_stages():
            def _stage_precedence(text, *_a, **_k):
                return _eli_phase38_route_precedence_contract(text)

            def _stage_frontier_status(text, *_a, **_k):
                return _eli_phase38_frontier_status_contract(text)

            def _stage_identity_audit(text, *_a, **_k):
                return _eli_phase39_identity_audit_contract(text)

            def _stage_mcp_explicit(text, *_a, **_k):
                # "run mcp tool X" names the mechanism outright and must outrank the
                # generic "run <thing>" app-launch route further down.
                m = re.search(r"\b(?:run|call|use|invoke)\s+(?:the\s+)?mcp\s+tool\s+"
                              r"(?P<t>[\w.\-]+)(?:\s+with\s+(?P<rest>.+))?$",
                              str(text or ""), re.I)
                if not m:
                    return None
                args = {"tool": m.group("t")}
                rest = (m.groupdict().get("rest") or "").strip()
                if rest:
                    try:
                        parsed = json.loads(rest)
                        if isinstance(parsed, dict):
                            args["arguments"] = parsed
                    except Exception:
                        args["arguments"] = {"input": rest}
                return {"action": "MCP_CALL", "args": args, "confidence": 0.97,
                        "matched_by": "mcp.call_explicit"}

            def _stage_computer_use_explicit(text, *_a, **_k):
                # An explicit "computer use: <goal>" prefix is the user stating the
                # mechanism outright, so it must outrank keyword heuristics further
                # down (a goal mentioning "search for X" was landing on WEB_SEARCH).
                m = re.match(r"^\s*computer[\s_-]?use\b[:,]?\s*(.+)$", str(text or ""), re.I)
                if not m:
                    return None
                goal = m.group(1).strip(" .?!,")
                if not goal:
                    return None
                return {"action": "COMPUTER_USE", "args": {"goal": goal},
                        "confidence": 0.97, "matched_by": "computer_use.explicit"}

            def _stage_memory_runtime_lock(text, *_a, **_k):
                if _eli_memory_runtime_route_lock_should_trigger(text):
                    return _eli_memory_runtime_route_lock_result(text)
                return None

            def _stage_gui_actual_scan(text, *_a, **_k):
                if _eli_gui_audit_actual_scan_v2(text):
                    return {
                        "action": "GUI_RUNTIME_AUDIT",
                        "args": {
                            "question": str(text or ""),
                            "proof_requested": True,
                            "audit_depth": "proof",
                            "require_timestamps": True,
                            "require_full_file_read_evidence": True,
                        },
                        "confidence": 0.995,
                        "meta": {
                            "matched_by": "router.gui_audit_actual_scan_proof_v2",
                            "need_grounding": True,
                            "allow_chat_without_evidence": False,
                            "task_family": "grounded_audit",
                            "forbid_chat_fallback": True,
                        },
                    }
                return None

            def _stage_self_report_recent_updates(text, *_a, **_k):
                if _eli_self_report_recent_updates_question(text):
                    return {
                        "action": "SELF_REPORT",
                        "args": {"question": str(text or ""), "self_report_scope": "recent_updates"},
                        "confidence": 0.995,
                        "meta": {
                            "matched_by": "self_report.recent_updates_grounded",
                            "task_family": "self_report_runtime",
                            "grounded_required": True,
                            "forbid_chat_fallback": True,
                            "forbid_fake_update_claims": True,
                            "allow_chat_without_evidence": False,
                        },
                    }
                return None

            def _stage_recent_memory(text, *_a, **_k):
                if _eli_recent_memory_processing_question(text):
                    return {
                        "action": "MEMORY_STATUS",
                        "args": {"question": str(text or ""), "memory_scope": "recent_processing"},
                        "confidence": 0.995,
                        "meta": {
                            "matched_by": "memory.recent_processing_grounded",
                            "task_family": "memory_runtime",
                            "grounded_required": True,
                            "forbid_chat_fallback": True,
                            "forbid_fake_memory_activity": True,
                            "allow_chat_without_evidence": False,
                        },
                    }
                return None

            def _stage_memory_count(text, *_a, **_k):
                if _eli_is_memory_count_question(text):
                    return {
                        "action": "MEMORY_STATUS",
                        "args": {"question": str(text or ""), "memory_scope": "count_only"},
                        "confidence": 0.995,
                        "meta": {
                            "matched_by": "memory.count.grounded_synthesis",
                            "allow_chat_without_evidence": False,
                            "requires_grounded_synthesis": True,
                            "requires_output_validation": True,
                            "quick_direct_allowed": True,
                            "forbid_unverified_generation": True,
                        },
                    }
                return None

            def _stage_profile_scope_explicit(text, *_a, **_k):
                low = _eli_profile_scope_low(text)
                if _eli_is_explicit_preference_request(low):
                    return _eli_profile_scope_result(
                        "PERSONAL_MEMORY_SUMMARY",
                        text,
                        "preferences_detail",
                        matched_by="profile.scope_contract.preferences_detail",
                    )
                if _eli_is_full_profile_dump(low):
                    return _eli_profile_scope_result(
                        "PERSONAL_MEMORY_SUMMARY",
                        text,
                        "full_profile",
                        matched_by="profile.scope_contract.full_profile",
                    )
                return None

            def _stage_final_memory_contract(text, *_a, **_k):
                return _eli_phase38_final_memory_question_contract(text)

            def _stage_runtime_or_name_contract(text, *_a, **_k):
                return _eli_phase38_runtime_status_or_name_source_contract(text)

            def _stage_identity_name_source_contract(text, *_a, **_k):
                return _eli_phase38_identity_name_source_single_safe_contract(text)

            def _stage_generate_script_prepass(text, *_a, **_k):
                """Fire BEFORE GPU/runtime guards so 'write a bash script that monitors GPU...'
                always routes to GENERATE_SCRIPT, not GPU_STATUS."""
                import re as _rgs
                raw2 = str(text or "").strip()
                low2 = raw2.lower()
                # Skip complaint/follow-up phrases — they are not generation requests
                _complaint_starts = (
                    "you did not", "you didn't", "you just", "that script", "this script",
                    "why did you", "why didn't you", "where is", "where did",
                )
                _complaint_frags = (
                    "did not generate", "didn't generate", "never ran", "not generated",
                    "no ide opened", "did not open", "didn't open",
                )
                if low2.startswith(_complaint_starts) or any(f in low2 for f in _complaint_frags):
                    return None
                # Defer test-generation requests to GENERATE_TESTS (self-improvement guard).
                if _rgs.search(r"\b(generate|write|create)\b", low2) and \
                   _rgs.search(r"\b(behaviou?ral\s+|unit\s+)?tests?\b", low2):
                    return None
                # Skip questions
                if raw2.rstrip().endswith("?"):
                    return None
                # First token must be a creation verb (typo-tolerant whitelist).
                _CREATION_VERBS = {
                    "write", "writ", "writes", "wrtie", "wright",
                    "create", "creat", "craete", "cretae", "creaet", "crete", "crate",
                    "generate", "generat", "genrate", "genarate", "gnerate", "generete",
                    "make", "mak", "makes",
                    "build", "buil", "builds",
                    "code", "cod", "codes",
                }
                _first_tok = low2.split()[0] if low2.split() else ""
                if _first_tok not in _CREATION_VERBS:
                    return None
                # Reject negated generation: "not generate...", "don't write..."
                if _rgs.search(
                    r"\b(?:not|don'?t|never|no|stop|without|rather\s+than|instead\s+of)\b\s+\w*\s*"
                    r"(?:generate|write|create|build|make)\b", low2):
                    return None
                # Non-code creative scripts (film/podcast/etc.) → skip
                _NON_CODE_SCRIPT2 = _rgs.compile(
                    r"\b(?:for|about|on|regarding)\s+(?:[\w]+\s+)*?"
                    r"(?:presentation|slides?|talk|speech|podcast|film|movie|play|show|actors?|"
                    r"onboarding|marketing|campaign|video|youtube|event|ceremony|wedding|"
                    r"performance|audience|interview|screenplay)\b", _rgs.I)
                _CREATIVE_SCRIPT2 = _rgs.compile(
                    r"\b(?:film\s+script|movie\s+script|play\s+script|podcast\s+script|"
                    r"write\s+a\s+(?:podcast|film|stage|theatre|movie)\s+script|"
                    r"theatre\s+script|stage\s+script|script\s+for\s+(?:a\s+)?(?:film|movie|"
                    r"play|podcast|show|talk|video|event|presentation|slides?|actors?|"
                    r"onboarding|ceremony|wedding|performance|audience|interview|"
                    r"marketing|campaign|youtube))\b", _rgs.I)
                if _NON_CODE_SCRIPT2.search(raw2) or _CREATIVE_SCRIPT2.search(raw2):
                    return None
                # Explicit language keywords
                _LANG_RE = _rgs.compile(
                    r"\b(?:bash|python|shell|sh|javascript|js|typescript|ts|ruby|perl|"
                    r"powershell|zsh|fish|lua|go|golang|rust|c\+\+|cpp|java|"
                    r"script|function|program|module|code)\b", _rgs.I)
                if _LANG_RE.search(raw2):
                    return {
                        "action": "GENERATE_SCRIPT",
                        "args": {"description": raw2, "use_gguf_only": True, "forbid_ollama": True},
                        "confidence": 0.97,
                        "meta": {"matched_by": "eli.generate_script_prepass"},
                    }
                return None

            def _stage_runtime_cognition_failure_guard(text, *_a, **_k):
                rcfg = globals().get("_eli_runtime_cognition_failure_guard")
                if callable(rcfg):
                    try:
                        out = rcfg(text)
                        if out:
                            return out
                    except Exception:
                        _SWLOG.debug("suppressed exception", exc_info=True)
                return None

            def _stage_self_improvement_guard(text, *_a, **_k):
                sig = globals().get("_eli_self_improvement_phrase_guard")
                if callable(sig):
                    try:
                        out = sig(text)
                        if out:
                            return out
                    except Exception:
                        _SWLOG.debug("suppressed exception", exc_info=True)
                return None

            def _stage_personal_memory_pre_route(text, *_a, **_k):
                pm_pre = globals().get("_eli_pm_pre_route")
                if callable(pm_pre):
                    try:
                        out = pm_pre(text)
                        if out is not None:
                            return out
                    except Exception:
                        _SWLOG.debug("suppressed exception", exc_info=True)
                return None

            def _stage_lrf_pre_route(text, *_a, **_k):
                lrf_pre = globals().get("_eli_lrf_pre_route")
                if callable(lrf_pre):
                    try:
                        out = lrf_pre(text)
                        if out is not None:
                            return out
                    except Exception:
                        _SWLOG.debug("suppressed exception", exc_info=True)
                return None

            def _stage_portable_route(text, *_a, **_k):
                if callable(_eli_phase38_portable_try_route):
                    try:
                        return _eli_phase38_portable_try_route(text)
                    except Exception:
                        return None
                return None

            def _stage_voice_contract(text, *_a, **_k):
                voice = globals().get("_eli_voice_contract_route")
                if callable(voice):
                    try:
                        out = voice(text)
                        if out is not None:
                            return out
                    except Exception:
                        _SWLOG.debug("suppressed exception", exc_info=True)
                return None

            def _stage_persona_override(text, *_a, **_k):
                return _eli_phase38_persona_override_contract(text)

            def _stage_followup_passthrough(text, *_a, **_k):
                return _eli_phase38_followup_passthrough_contract(text)

            def _stage_identity_contract(text, *_a, **_k):
                return _eli_phase38_identity_contract(text)

            def _stage_set_user_name(text, *_a, **_k):
                import re as _re
                low = _re.sub(r"\s+", " ", str(text or "").lower()).strip()
                return _route_set_user_name(str(text or ""), low)

            def _stage_set_communication_style(text, *_a, **_k):
                import re as _re
                low = _re.sub(r"\s+", " ", str(text or "").lower()).strip()
                return _route_set_communication_style(str(text or ""), low)

            def _stage_web_lookup(text, *_a, **_k):
                """Real-time factual lookups / explicit web search → WEB_SEARCH
                when network is on. Runs late (just before the core router) so
                every specific grounded/memory/identity/media stage wins first;
                this only catches what would otherwise be a stale-weights CHAT."""
                try:
                    _raw, _low = _normalize_text(text)
                    return _eli_web_lookup_prepass(_raw, _low)
                except Exception:
                    return None

            def _stage_core_router(text, *a, **k):
                return _eli_phase38_open_typo_or_core_route(text, *a, **k)

            def _stage_pending_habit_confirm(text, *_a, **_k):
                """Pre-pass: when ELI has offered to add a detected habit, intercept
                YES/NO so a plain 'yes' approves it (enables the rule) and 'no'
                dismisses it — before anything else swallows the reply."""
                try:
                    import re as _re
                    from eli.planning.habits import get_pending_habit
                    from eli.runtime import grounded_remediation as _gr
                    if not get_pending_habit():
                        return None
                    low = _re.sub(r"\s+", " ", str(text or "").strip().lower()).strip(" .!")
                    # Only intercept a SHORT, unambiguous reply — never a real
                    # sentence that merely contains "do it"/"don't" (that is
                    # normal conversation and must route normally).
                    if len(low.split()) > 6:
                        return None
                    if _gr.NO_RE.match(low) or _re.fullmatch(
                            r"(no thanks|not now|dismiss|skip( it)?|nope)", low):
                        return {
                            "action": "DECLINE_HABIT",
                            "args": {"message": low},
                            "confidence": 0.98,
                            "meta": {"matched_by": "pending_habit.no_intercept"},
                        }
                    if _gr.YES_RE.match(low) or _re.fullmatch(
                            r"(add it|add the habit|sure(,? add it)?|please do|sounds good|yes please)", low):
                        return {
                            "action": "CONFIRM_HABIT",
                            "args": {"message": low},
                            "confidence": 0.98,
                            "meta": {"matched_by": "pending_habit.yes_intercept"},
                        }
                except Exception:
                    _SWLOG.debug("suppressed exception", exc_info=True)
                return None

            def _stage_pending_code_fix_confirm(text, *_a, **_k):
                """Pre-pass: when an EXAMINE_CODE fix offer is pending, intercept
                YES/NO (and 'fix it' / 'fix the logic ones too') before anything
                else swallows them. Separate pending-state from package repairs."""
                try:
                    import re as _re
                    from eli.runtime import code_examiner as _ce
                    from eli.runtime import grounded_remediation as _gr
                    if not _ce.get_pending_fix():
                        return None
                    low = _re.sub(r"\s+", " ", str(text or "").strip().lower()).strip(" .!")
                    # Only intercept a SHORT, unambiguous reply. A real sentence
                    # that merely contains "fix"/"do not" ("you do not know my
                    # name?", "few bugs to fix here") must route normally — the
                    # earlier broad search hijacked those for up to 10 minutes.
                    if len(low.split()) > 7:
                        return None
                    if _gr.NO_RE.match(low) or _re.fullmatch(
                            r"(leave it|not now|skip( it)?|don'?t|do not|no thanks|nope)", low):
                        return {
                            "action": "CANCEL_CODE_FIX",
                            "args": {"message": low},
                            "confidence": 0.98,
                            "meta": {"matched_by": "pending_code_fix.no_intercept"},
                        }
                    if _gr.YES_RE.match(low) or _re.fullmatch(
                            r"(fix it|fix them|fix it please|apply( it| them)?|patch it|"
                            r"yes please|yes,? fix (it|them)|fix the logic ones( too)?|"
                            r"yes,? includ(e|ing) the logic ones?)", low):
                        return {
                            "action": "CONFIRM_CODE_FIX",
                            "args": {"message": low},
                            "confidence": 0.98,
                            "meta": {"matched_by": "pending_code_fix.yes_intercept"},
                        }
                except Exception:
                    _SWLOG.debug("suppressed exception", exc_info=True)
                return None

            def _stage_pending_remediation_confirm(text, *_a, **_k):
                """
                Pre-pass: if a remediation offer is pending (e.g. 'install netflix?'),
                intercept YES/NO and direct install/download commands before anything
                else can swallow them as CHAT or block them as fragments.
                """
                try:
                    import re as _re
                    from eli.runtime import grounded_remediation as _gr
                    if not _gr.get_pending():
                        return None
                    low = _re.sub(r"\s+", " ", str(text or "").strip().lower())
                    if _gr.YES_RE.match(low):
                        return {
                            "action": "CONFIRM_PENDING_REMEDIATION",
                            "args": {"message": low},
                            "confidence": 0.99,
                            "meta": {"matched_by": "pending_remediation.yes_intercept"},
                        }
                    if _gr.NO_RE.match(low):
                        return {
                            "action": "CANCEL_PENDING_REMEDIATION",
                            "args": {"message": low},
                            "confidence": 0.99,
                            "meta": {"matched_by": "pending_remediation.no_intercept"},
                        }
                    # "install X" / "download X" — route to confirmation handler
                    # which will either advance the pending plan or diagnose fresh.
                    _im = _re.match(r"^(install|download|get)\s+(\S+)", low)
                    if _im:
                        return {
                            "action": "CONFIRM_PENDING_REMEDIATION",
                            "args": {"message": low},
                            "confidence": 0.97,
                            "meta": {"matched_by": "pending_remediation.install_download_intercept"},
                        }
                except Exception:
                    _SWLOG.debug("suppressed exception", exc_info=True)
                return None

            def _stage_pending_proposal_confirm(text, *_a, **_k):
                """If ELI offered to do something ("Want me to set a reminder?")
                and the user now affirms, re-route the stored proposal phrase
                through the pipeline so it actually executes (or asks for
                specifics). A decline clears it and falls through to chat."""
                try:
                    from eli.runtime.pending_proposal import (
                        get_pending_proposal, clear_pending_proposal,
                    )
                    prop = get_pending_proposal()
                    if not prop:
                        return None
                    low = re.sub(r"\s+", " ", str(text or "").strip().lower())
                    try:
                        from eli.runtime.grounded_remediation import (
                            is_affirmation, is_negation,
                        )
                    except Exception:
                        # Lenient local fallback (tolerates "yes please" etc.).
                        _yw = re.compile(r"\b(yes|yeah|yep|sure|ok|okay|go ahead|go for it|do it|please do|proceed)\b", re.I)
                        _nw = re.compile(r"\b(no|nope|nah|cancel|don'?t|never mind|leave it|skip it)\b", re.I)
                        is_affirmation = lambda t: bool(_yw.search(t or ""))
                        is_negation = lambda t: bool(_nw.search(t or ""))
                    # Affirmation wins when both appear ("yes, not no").
                    if is_negation(low) and not is_affirmation(low):
                        clear_pending_proposal()
                        return None
                    if is_affirmation(low):
                        cmd = str(prop.get("command") or "").strip()
                        clear_pending_proposal()
                        if cmd:
                            routed = route(cmd)  # pending already cleared → no recursion
                            if isinstance(routed, dict):
                                routed.setdefault("meta", {})
                                routed["meta"]["matched_by"] = "pending_proposal.confirm"
                                routed["meta"]["rerouted_from_proposal"] = cmd
                                return routed
                except Exception:
                    return None
                return None

            def _stage_react_to_pasted_content(text, *_a, **_k):
                """Highest-priority conversational guard: when the user asks ELI
                to react to / give an opinion on text they pasted or referenced,
                force CHAT before any status/memory/grounding stage can scan the
                quoted material and hijack the turn into a data dump."""
                try:
                    _raw, _low = _normalize_text(text)
                    return _eli_react_to_content_prepass(text, _raw, _low)
                except Exception:
                    return None

            return (
                ("pending_habit_confirm", _stage_pending_habit_confirm),
                ("pending_code_fix_confirm", _stage_pending_code_fix_confirm),
                ("pending_remediation_confirm", _stage_pending_remediation_confirm),
                ("pending_proposal_confirm", _stage_pending_proposal_confirm),
                ("react_to_pasted_content", _stage_react_to_pasted_content),
                ("mcp_explicit", _stage_mcp_explicit),
                ("computer_use_explicit", _stage_computer_use_explicit),
                ("precedence", _stage_precedence),
                ("identity_audit", _stage_identity_audit),
                ("frontier_status", _stage_frontier_status),
                ("memory_runtime_lock", _stage_memory_runtime_lock),
                ("gui_actual_scan", _stage_gui_actual_scan),
                ("self_report_recent_updates", _stage_self_report_recent_updates),
                ("recent_memory", _stage_recent_memory),
                ("memory_count", _stage_memory_count),
                ("profile_scope_explicit", _stage_profile_scope_explicit),
                ("final_memory_contract", _stage_final_memory_contract),
                ("runtime_or_name_contract", _stage_runtime_or_name_contract),
                ("identity_name_source_contract", _stage_identity_name_source_contract),
                ("generate_script_prepass", _stage_generate_script_prepass),
                ("runtime_cognition_failure_guard", _stage_runtime_cognition_failure_guard),
                ("self_improvement_guard", _stage_self_improvement_guard),
                ("personal_memory_pre_route", _stage_personal_memory_pre_route),
                ("lrf_pre_route", _stage_lrf_pre_route),
                # set_user_name runs before portable_route so explicit identity
                # assertions ("call me Alex", "my name is X") are never
                # misclassified as media-play requests by portable_intent_contract.
                # multi_command_prepass runs first so a chained utterance is split into
                # its commands (each then routed individually below).
                ("multi_command_prepass", lambda t, *a, **k: _eli_multi_command_prepass(t)),
                # schedule_prepass runs BEFORE portable_route so "open spotify at 8pm"
                # / "close steam in 2 hours" defer to the background workers instead of
                # being executed now by the app router (no-time commands fall through).
                ("schedule_prepass", lambda t, *a, **k: _eli_schedule_prepass(t)),
                ("set_user_name", _stage_set_user_name),
                ("set_communication_style", _stage_set_communication_style),
                ("portable_route", _stage_portable_route),
                ("weather_prepass", lambda t, *a, **k: _eli_weather_prepass(t)),
                ("shell_prepass",   lambda t, *a, **k: _eli_shell_prepass(t)),
                ("voice_contract", _stage_voice_contract),
                ("persona_override", _stage_persona_override),
                ("followup_passthrough", _stage_followup_passthrough),
                ("identity_contract", _stage_identity_contract),
                ("web_lookup", _stage_web_lookup),
                ("core_router", _stage_core_router),
            )

        _ELI_ROUTE_PRIORITY_STAGES = _eli_route_priority_stages()

        def _eli_route_apply_post_contracts(raw, out):
            out = _eli_media_contract_post(raw, out)
            out = _eli_phase38_media_query_cleaner_post(out)
            out = _eli_phase38_tiny_fragment_post(raw, out)
            out = _eli_phase38_personal_memory_summary_compat_post(out)
            out = _eli_phase38_identity_scope_post(raw, out)

            if isinstance(out, dict):
                low = _eli_profile_scope_low(raw)
                action = str(out.get("action") or "").upper()

                if action == "PERSONAL_MEMORY_SUMMARY" and _eli_is_generic_profile_inventory(low):
                    out = dict(out)
                    out["args"] = dict(out.get("args") or {})
                    out["args"]["question"] = str(raw or "")
                    out["args"]["profile_scope"] = "inventory_only"
                    out["meta"] = dict(out.get("meta") or {})
                    out["meta"]["profile_scope_contract"] = "inventory_only"
                    out["meta"]["forbid_preference_detail"] = True
                    out["meta"]["forbid_project_detail"] = True
                    out["meta"]["active_user_scoped"] = True

                if action == "MEMORY_STATUS" and _eli_is_memory_count_question(raw):
                    out = dict(out)
                    out["args"] = dict(out.get("args") or {})
                    out["args"]["question"] = str(raw or "")
                    out["args"]["memory_scope"] = "count_only"
                    out["meta"] = dict(out.get("meta") or {})
                    out["meta"]["matched_by"] = "memory.count.grounded_synthesis.post_route"
                    out["meta"]["requires_grounded_synthesis"] = True
                    out["meta"]["requires_output_validation"] = True
                    out["meta"]["quick_direct_allowed"] = True
                    out["meta"]["forbid_unverified_generation"] = True

            return out

        def _eli_priority_route(raw="", *args, **kwargs):
            text = str(raw or "")
            result = None
            matched_by = "unmatched"
            _trace = str(__import__("os").environ.get("ELI_PIPELINE_TRACE", "")).strip().lower() in {"1", "true", "yes", "on"}
            _perf = __import__("time").perf_counter
            _route_t0 = _perf()

            if _trace:
                _preview = text.replace("\n", " ").strip()
                if len(_preview) > 160:
                    _preview = _preview[:157] + "..."
                log.debug(f"[PIPELINE][ROUTER] begin text={_preview!r}")

            for stage_name, stage_fn in _ELI_ROUTE_PRIORITY_STAGES:
                _stage_t0 = _perf() if _trace else 0.0
                try:
                    candidate = stage_fn(text, *args, **kwargs)
                except Exception:
                    candidate = None
                if _trace:
                    _dt = (_perf() - _stage_t0) * 1000.0
                    log.debug(
                        f"[PIPELINE][ROUTER] stage={stage_name} hit={candidate is not None} dt_ms={_dt:.2f}",
                    )
                if candidate is not None:
                    result = candidate
                    matched_by = stage_name
                    break

            if result is None:
                result = {
                    "action": "CHAT",
                    "args": {"message": text},
                    "confidence": 0.25,
                    "meta": {"matched_by": "eli.priority_pipeline.fallback_chat"},
                }

            result = _eli_route_apply_post_contracts(text, result)
            if isinstance(result, dict):
                meta = dict(result.get("meta") or {})
                meta.setdefault("priority_pipeline_stage", matched_by)
                result["meta"] = meta
                if _trace:
                    _total = (_perf() - _route_t0) * 1000.0
                    log.debug(
                        f"[PIPELINE][ROUTER] final action={result.get('action')} "
                        f"confidence={result.get('confidence')} stage={matched_by} total_ms={_total:.2f}",
                    )
            return _eli_phase38_enrich_pdf_if_needed(text, result)

        route = _eli_priority_route  # type: ignore[assignment]
        route_intent = route
        route_command = route
        parse_command = route
        classify = route

        log.debug("[ROUTER] explicit priority pipeline installed")
except Exception as _eli_route_priority_pipeline_err:
    log.debug(f"[ROUTER] explicit priority pipeline install failed: {_eli_route_priority_pipeline_err}")

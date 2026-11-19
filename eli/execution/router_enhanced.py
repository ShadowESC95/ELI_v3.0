# Deterministic intent router. Stable and redistributable (no machine-specific paths), strong
# URL/path detection, backward compatible with legacy tests (e.g. STOP_MEDIA), canonical aliases
# and STT cleanup, route metadata for debugging. Deterministic first, extensible second.

from __future__ import annotations

def _eli_pipeline_trace(stage: str, **data):
    try:
        import json, time, os
        from pathlib import Path
        from eli.core.paths import logs_dir
        # this install's logs (paths.logs_dir), never the install's own tree: an engine start in a test
        # or an eval wrote ops/reports/ into the checkout
        out = Path(logs_dir()) / "pipeline_visibility" / "runtime_pipeline_trace.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "stage": stage, **data}, ensure_ascii=False, default=str) + "\n")
        # CLI print: silent by default (file write still happens for forensics).
        # Set ELI_PIPELINE_TRACE_VERBOSE=1 to restore the per-call print.
        if os.environ.get("ELI_PIPELINE_TRACE_VERBOSE", "0").lower() in {"1", "true", "yes", "on"}:
            _preview = {k: (v[:60] + "..." if isinstance(v, str) and len(v) > 60 else v) for k, v in data.items()}
            _SWLOG.debug(f"[PIPELINE_TRACE] {stage} {_preview}")
    except Exception as _e:
        _SWLOG.debug(f"[PIPELINE_TRACE_ERR] {stage}: {_e}")

def _eli_phase10_is_codebase_audit_request(text: str) -> bool:
    """
    Prevent broad memory-runtime regexes from hijacking codebase audits.

    A codebase audit is asked of ELI's code: a source file or module of it ("router_enhanced",
    "executor_enhanced.py", "/runtime"), the code as such ("the repo", "the codebase", "python
    files"), or one of its parts in a common word ("router", "engine", "pipeline") when ELI is named
    ("your router"). "my wifi router is broken", "how is the engine in my car running" and "what's
    the status of the oil pipeline" ran a codebase audit.
    """
    s = str(text or "").lower()

    audit_words = (
        "audit", "inspect", "scan", "check", "verify", "examine",
        "what is wrong", "what's wrong", "broken", "missing",
        "how is", "how's", "how are", "status", "health", "state",
    )
    # the code itself, or a name only ELI's code has
    code_words = (
        "agent_bus", "world_model",
        "gguf_inference", "output_governor",
        "output_governer", "response_governance", "response_governence",
        "vector_store", "working_memory", "introspection_agent",
        "llm_intent", "hardware_profile", "runtime_settings",
        "self_upgrade", "habits_memory_db", "knowledge_graph",
        "memory_adapter", "memory_truth", "memory_service", "sqlite_memory",
        "os_controller", "screen_locator", "log_rotation", "/runtime",
        "python files", ".py", "eli_pro_audio_gui", "router_enhanced",
        "executor_enhanced", "codebase", "code base", "repo", "repository",
    )
    # parts of ELI said in words that name other things too
    part_words = ("router", "executor", "engine", "orchestrator", "pipeline", "hyde", "reranker")

    def _has(word: str) -> bool:
        # "repo" must not match "report", "engine" must not match "engineer".
        if re.fullmatch(r"[a-z0-9_ ]+", word):
            return re.search(rf"\b{re.escape(word)}\b", s) is not None
        return word in s

    if not any(_has(a) for a in audit_words):
        return False
    return any(_has(c) for c in code_words) or (any(_has(c) for c in part_words) and _names_eli(text))


import os
import json
import re
from pathlib import Path
from typing import Dict, Any, Optional, Tuple, List

# Logger for safety-wrapper suppressed-exception reporting (used before the
# module's main logger exists; must be defined at the top of the file).
import logging as _swlog_logging
_SWLOG = _swlog_logging.getLogger(__name__)


# Wall-clock requests, shared by the TIME/DATE routes and the long-question guard. The guard must
# not divert a clock question into CHAT, where the model has no clock and truthfully says it can't
# read one, which reads as ELI denying a capability it has.
from eli.execution.routing._helpers import (  # noqa: F401  (moved there so intent stages can share them)
    _NEGATORS,
    _WALLCLOCK_DATE_PATTERNS,
    _WALLCLOCK_META_RE,
    _WALLCLOCK_TIME_PATTERNS,
    _has_wallclock_pattern,
    _is_wallclock_meta_question,
    _is_wallclock_question,
    _tone_is_negated,
)


def _last_used_path() -> str | None:
    """The file or folder this conversation last named ("summarise the pdfs in that folder"). Kept
    for the conversation (request_context.conversation_value), not in a module global: one value for
    the whole process gave one API user's folder to the next user's "that folder"."""
    from eli.kernel.request_context import conversation_value
    value = conversation_value("last_used_path")
    return str(value) if value else None


def _keep_last_used_path(path: str) -> None:
    from eli.kernel.request_context import keep_for_conversation
    keep_for_conversation("last_used_path", str(path))


def _eli_latest_screenshot() -> str | None:
    """The path to ELI's most recent screenshot, or None: eli.perception.os_controller.latest_screenshot
    is the one owner of where screenshots land and which is newest (this kept its own folder scan)."""
    try:
        from eli.perception.os_controller import latest_screenshot
    except ImportError:
        _SWLOG.debug("latest screenshot reader unavailable", exc_info=True)
        return None
    try:
        return latest_screenshot()
    except (OSError, ValueError, TypeError):
        _SWLOG.debug("latest screenshot not read", exc_info=True)
        return None


# ============================================================
# STATIC CONFIG
# ============================================================

from eli.execution.routing.intents import (
    INTENT_STAGES_EARLY,
    INTENT_STAGES_TAIL,
    ROUTE_NONE,
)
# Route helpers live in eli.execution.routing._helpers (v3 modularisation). Change them there:
# an inline copy here is what let a v2 port silently drop _mk(meta_extra=).
from eli.execution.routing._helpers import (  # noqa: F401
    COMMON_DIRS,
    WEBSITE_ALIASES,
    _known_dir_path,
    _extract_int,
    _extract_path_from_text,
    _extract_pdf_path,
    _extract_pdf_paths,
    _is_likely_path,
    _is_likely_url,
    _mk,
    _normalize_text,
    _normalize_url,
)
# Whether a message names ELI as what it is about ("your", "you" past an asking frame, "ELI", one of
# its source files): the rules for ELI's own reports ask it before taking a common word as theirs.
from eli.execution.route_contracts import names_eli as _names_eli  # noqa: E402


from eli.execution.route_contracts import asks_about_eli as _eli_about_eli  # noqa: E402


def _eli_this_one(term: str, low: str) -> bool:
    """Whether a thing is named as the one in front of us ("the context window", "my batch size"):
    ELI's own. "a context window" and "the biggest context window" are anyone's."""
    return bool(re.search(rf"\b(?:the|my|our|your|this)\s+{term}\b", low))

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


# Canonical alias table — see eli/execution/app_aliases.py. It used to be
# duplicated here and in executor_enhanced with ZERO overlap between them,
# so an alias worked or not depending on which layer saw the word first.
from eli.execution.app_aliases import APP_ALIASES

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

_COURTESY_TAIL = re.compile(
    r"(?:[\s,.!]+(?:please|pls|plz|thanks|thank\s+you|thx|cheers|mate|pal|bud|buddy|for\s+me|"
    r"right\s+now|now|eli))+[\s,.!?]*$", re.I)


# The same before a command: "good. play the first light by quiet machine" searched for
# "good. play the first light". Only with punctuation after it ("right click" is a command), and never
# yes/no: those answer a question ELI asked. A name or "again" may follow the word: "Thanks bud, open
# youtube please" went to the intent model (32 s) and came back as an app to install.
_COURTESY_HEAD = re.compile(
    r"^\s*(?:(?:good|great|nice|cool|ok|okay|right|alright|perfect|lovely|grand|brilliant|sweet|cheers|thanks|"
    r"thank\s+you)(?:\s+(?:mate|pal|bud|buddy|man|dude|eli|again|a\s+lot|so\s+much|very\s+much))*"
    r"\s*[,.!;:]+\s*)+(?=\w)", re.I)


def _without_courtesy(text: str) -> str:
    """`text` without the please / thanks / for me / now / mate after it, or the "good." / "ok," before
    it, when what is left still names something (a bare "play" is kept as said: "play thank you" is a song)."""
    stripped = _COURTESY_TAIL.sub("", _COURTESY_HEAD.sub("", str(text or ""))).strip()
    if stripped == str(text or "").strip() or len(stripped.split()) < 2:
        return str(text or "")
    return stripped


def _eli_weather_prepass(user_text: str):
    low = user_text.lower().strip()
    # Guard: only fire when the sentence is *requesting* weather info.
    # Skip if the user is talking *about* a weather-related topic (e.g.
    # "the script is for a weather forecast", "stop telling me the weather").
    _REQUEST_SIGNALS = re.compile(
        r"\b(?:what(?:'s|s|\s+is|\s+will)?\s+(?:the\s+)?(?:weather|forecast|temperature|temp)|"
        r"how(?:'s|\s+is)\s+(?:the\s+)?weather|"
        r"(?:check|get|tell\s+me|show(?:\s+me)?|give\s+me)\s+(?:the\s+)?(?:weather|forecast|temperature)|"
        r"(?:will\s+it|is\s+it|going\s+to)\s+(?:rain|snow|be\s+(?:hot|cold|warm|sunny|cloudy)))\b",
        re.I)
    # Also allow bare "weather in X" / "weather for X" at the start of input
    _BARE_WEATHER_REQUEST = re.compile(
        r"^(?:weather|forecast|temperature)\b[^.!?]{0,60}\b(?:in|at|for)\s+[A-Za-z]",
        re.I)
    _TOPIC_PHRASES = re.compile(
        r"\b(?:script|code|program|file|function|task|fix|issue|error|this|that|the\s+\w+)\s+"
        r"(?:is|was|are|were|for|about|regarding|related\s+to)\s+(?:a\s+)?(?:weather|forecast|temperature)\b|"
        r"\b(?:stop|don'?t|cease|quit)\b.{0,30}\bweather\b",
        re.I)
    if _TOPIC_PHRASES.search(low):
        return None
    if not _REQUEST_SIGNALS.search(low) and not _BARE_WEATHER_REQUEST.search(low):
        return None
    m = re.search(
        r"(?:weather|forecast|temperature).{0,40}?(?:in|at|for) ([A-Za-z][A-Za-z ,]+?)(?:\?|$|[,;]|\band\b)",
        user_text,
        re.I)
    if m:
        loc = m.group(1).strip().rstrip("?.!, ")
        # "weather for tomorrow", "forecast for this weekend": a day, not a place
        loc = re.sub(r"(?i)\b(?:today|tonight|tomorrow|now|later|this (?:morning|afternoon|evening|weekend|week)|"
                     r"next week|the weekend|the week|(?:on |next |this )?(?:mon|tues|wednes|thurs|fri|satur|sun)day)\b",
                     " ", loc)
        loc = re.sub(r"(?i)^(?:in|at|for|near)\s+", "", re.sub(r"\s+", " ", loc).strip(" ,")).strip(" ,") or None
        return {
            "action": "GET_WEATHER",
            "args": {"location": loc, "_raw_user_text": user_text},
            "confidence": 0.97,
            "meta": {"matched_by": "weather.prepass", "entities": {"location": loc} if loc else {}},
        }
    return None


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
    text = str(user_text or "").strip()
    # Deictic follow-up: "run that command" after ELI showed a shell block.
    try:
        from eli.runtime.shell_followup import resolve_run_prior_shell
        _follow = resolve_run_prior_shell(text)
        if _follow:
            return {
                "action": "SHELL_EXEC",
                "args": {"cmd": _follow},
                "confidence": 0.97,
                "meta": {"matched_by": "shell.prior_block_followup"},
            }
    except Exception:
        _SWLOG.debug("suppressed exception", exc_info=True)
    # Timestamp investigation — run real clock diagnostics, never fake bash in CHAT.
    # "check memory, logs, timestamps, etc., summarise everything" was matching this: "timestamp"
    # was just the last item in a list of things to check as part of a broad memory-recall
    # request, not a complaint about the clock itself. A real timestamp complaint is not also
    # asking for a discussion summary over a day range.
    _low = _re.sub(r"\s+", " ", text.lower())
    _broad_memory_request = bool(_re.search(
        r"\b(?:summaris|summariz|discuss(?:ed|ing|ion)?|past\s+\d+\s+days?|last\s+\d+\s+days?|"
        r"what have we|memory,?\s*logs?)\b",
        _low,
    ))
    if not _broad_memory_request and (_re.search(
        r"\b(?:dig(?:\s+in)?to|look(?:ing)?\s+(?:into|at)|check|investigate|trace|examine|fix)"
        r".{0,60}\btimestamp",
        _low,
    ) or _re.search(r"\btimestamp.{0,40}\b(?:dig|check|investigate|trace|fix)\b", _low)):
        return {
            "action": "TIMESTAMP_DIAG",
            "args": {},
            "confidence": 0.96,
            "meta": {"matched_by": "shell.timestamp_diag"},
        }
    m = _re.match(r"^\s*(?:run|execute)\s+(\S+(?:\s+.*)?)$", text, _re.I)
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


_SCHEDULE_TIME_RX = re.compile(
    r"\b(overnight|tonight|later tonight|tomorrow(?:\s+(?:morning|afternoon|evening|night))?|today"
    r"|this\s+(?:morning|afternoon|evening|noon)"
    r"|every\s+(?:morning|day|night|evening|week)|each\s+(?:morning|day|night)"
    r"|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?"
    r"|(?:for|by|at)\s+\d{1,2}[:\s]\d{2}\s*(?:am|pm)?"   # 'for 7 15', 'at 7:15'
    r"|(?:for|by|at)\s+\d{1,2}\s*(?:am|pm)"               # 'for 7am'
    r"|in\s+\d+\s*(?:hours?|hrs?|minutes?|mins?))\b", re.I)
# When the work is to be done, said outright: a clock time, a delay, "overnight", "every morning".
_EXPLICIT_WHEN_RX = re.compile(
    r"\b(overnight|later tonight"
    r"|every\s+(?:morning|day|night|evening|week)|each\s+(?:morning|day|night)"
    r"|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?"
    r"|(?:for|by|at)\s+\d{1,2}[:\s]\d{2}\s*(?:am|pm)?"
    r"|(?:for|by|at)\s+\d{1,2}\s*(?:am|pm)"
    r"|in\s+\d+\s*(?:hours?|hrs?|minutes?|mins?))\b", re.I)
# Words that ask for something to be done later rather than now.
_DEFER_RX = re.compile(
    r"\b(schedule|queue|remind|run|upgrade|update yourself|reflect|later|while\s+i(?:'m|\s+am)\s+(?:asleep|sleeping|out|away)"
    r"|when\s+i\s+wake|by\s+(?:tomorrow|tonight|morning|the\s+morning)|ready\s+(?:for|by))\b", re.I)
# Heavy/agentic task verbs (NOT 'set'/'get' — those would hijack alarms, weather,
# forecasts). Only unambiguous scheduling verbs are added.
_SCHEDULE_VERB_RX = re.compile(
    r"\b(build|make|write|code|create|generate|implement|develop|design"
    r"|research|analyse|analyze|investigate|study|work on|put together"
    r"|run|upgrade|update yourself|reflect|schedule|remind|prepare|queue)\b", re.I)
# Known schedulable actions. Naming one with a future-time marker means "schedule this action", even
# with a casual verb ("get a morning report ready for tomorrow"). The re-run request becomes just
# the action (no time), so the task fires once and doesn't reschedule itself.
_SCHEDULE_ACTION_RX = re.compile(
    r"\b(morning report|daily report|intelligence report|morning briefing"
    r"|daily briefing|daily summary|engine eval|test suite|test report"
    r"|self[\s_-]?test|generate tests?|test generation|lora|fine[\s_-]?tune"
    r"|self[\s_-]?upgrade|reflection)\b", re.I)


_DAY_QUALIFIER_RX = re.compile(r"^(today|tonight|tomorrow|overnight)$", re.I)


def _extract_when(t: str) -> str:
    """The time markers of a request, for the time parser. A clock time and its day are separate
    matches and both are needed ("for 8:45am today"), but a clock time beats a vague day-part from
    another clause: in "it rained this morning. Schedule it for 8:45am today", "this morning" is
    narration. The whole sentence fed the parser a paragraph."""
    parts: List[str] = []
    for m in _SCHEDULE_TIME_RX.finditer(t):
        p = m.group(0).strip()
        if p.lower() not in {s.lower() for s in parts}:
            parts.append(p)
    if any(ch.isdigit() for p in parts for ch in p):
        parts = [p for p in parts if any(ch.isdigit() for ch in p) or _DAY_QUALIFIER_RX.match(p)]
    return " ".join(parts).strip()


def _strip_schedule_time(t: str) -> str:
    """Remove time/scheduling words so a re-run can't re-trigger the prepass."""
    s = _SCHEDULE_TIME_RX.sub(" ", t)
    s = re.sub(r"\b(for me|ready|please|tomorrow|morning|tonight)\b", " ", s, flags=re.I)
    s = re.sub(r"\s+", " ", s).strip(" ,.")
    return s or t


# Imperative command verbs — "open spotify at 8pm", "get the news at 7am", "play X
# at 9", "close steam in 2 hours". With a future-time marker these mean DEFER the
# command to the background workers.
_IMPERATIVE_RX = re.compile(
    r"\b(open|launch|start|play|pause|stop|close|quit|kill|get|fetch|show|check"
    r"|run|remind|turn|mute|unmute|screenshot|take|send|search|find|read|enable"
    r"|disable|update|download|email|message|post)\b", re.I)
# Genuine questions aren't scheduling ("what's on tonight?"). do/does/did/are/is + pronoun is
# ELI being asked something, so it's chat. can/could/would/will you is a polite request and must
# still schedule ("can you open spotify at 8pm?").
_QUESTION_RX = re.compile(
    r"^\s*(what|whats|what's|how|why|who|where|which|whose|tell me|show me what)\b"
    r"|^\s*(do|does|did|are|is|was|were|have|has|had|am)\s+"
    r"(you|i|we|it|that|this|there|they)\b",
    re.I,
)

def _eli_outside_quotes(text: str) -> str:
    """The utterance with quoted passages removed — what the user actually asked.

    Quoting ELI's own words back is a conversational move, not a request to run
    whatever those words mention. Anything left empty (the message was ONLY a
    quote) falls back to the whole text so a bare quote still routes as before.
    """
    # DOUBLE quotes only. An apostrophe is not a quote mark in English, and
    # including it made "you're building..." look like a quoted span, eating half
    # the sentence including the words that mattered.
    stripped = re.sub(r"[\"\u201c\u201d][^\"\u201c\u201d]{12,}?[\"\u201c\u201d]",
                      " ", str(text or ""))
    return stripped if stripped.strip() else str(text or "")


# These have their own dedicated time-aware handlers — never hijack them.
_DEDICATED_TIME_RX = re.compile(r"\b(alarm|timer|stopwatch|pomodoro)\b", re.I)


def _eli_final_clause(text: str) -> str:
    """The last sentence of an utterance, for tests that must not only see the
    opening. Splits on sentence punctuation and returns the final non-empty part."""
    parts = [p.strip() for p in re.split(r"[.!?;]+", str(text or "")) if p.strip()]
    return parts[-1] if parts else str(text or "").strip()


_AG_CAL = r"(?:calendar|calender|calander|diary|agenda|schedule)"
_AG_ITEM = r"(?:events?|appointments?|meetings?|reminders?|alarms?)"
_AG_WHOSE = r"(?:my|your|our|the)"
_AG_POLITE = r"(?:(?:please|pls|eli|hey|ok(?:ay)?|so|now|then|just|you|can you|could you|would you|will you|let'?s|let me)[\s,]+)*"
_AG_DAY = r"(?:today|tonight|tomorrow|this (?:morning|afternoon|evening|week)|next week|(?:on\s+)?(?:mon|tues|wednes|thurs|fri|satur|sun)day)"
_AG_LIST_RX = re.compile(
    rf"^{_AG_POLITE}(?:show|list|check|read|double-?check|look at|go through|pull up|what(?:'s| is)(?:\s+(?:on|in))?)\s+(?:me\s+)?{_AG_WHOSE}\s+{_AG_CAL}\b"
    rf"|^{_AG_POLITE}(?:show|list|check|read)\s+(?:me\s+)?(?:{_AG_WHOSE}|all {_AG_WHOSE}|any)\s+(?:upcoming\s+)?{_AG_ITEM}\b"
    rf"|\bwhat(?:'s| is| have i got| do i have)\s+(?:on|planned|scheduled|coming up)\b[^?]{{0,40}}\b{_AG_DAY}\b"
    rf"|\bwhat(?:'s| is| does)\s+my\s+(?:{_AG_CAL}|day|week)\b"
    rf"|\bdo i have\s+(?:anything|something|any\s+{_AG_ITEM}|an?\s+{_AG_ITEM})\b[^?]{{0,40}}(?:\b(?:on|planned|scheduled)\b|\b{_AG_DAY}\b|\?|$)"
    rf"|\bwhat\s+{_AG_ITEM}\s+do i have\b|\bwhat are my\s+{_AG_ITEM}\b|\bany(?:thing)?\s+on\s+{_AG_DAY}\b"
    rf"|\b(?:am i|are we)\s+free\b[^?]{{0,30}}\b{_AG_DAY}\b",
    re.I)
_AG_NOT_LIST_RX = re.compile(r"\b(?:tv|telly|television|netflix|cinema|radio|the news|spotify|youtube)\b", re.I)
_AG_REMIND_RX = re.compile(
    rf"\bremind\s+(?:me|us)\b|^{_AG_POLITE}(?:set|add|create|make|setting)\s+(?:up\s+)?(?:(?:a|an|another|the)\s+)?reminders?\b"
    r"|^(?:consider\s+)?setting\s+(?:(?:a|an|another)\s+)?reminders?\b|^reminder\b", re.I)
_AG_REMIND_QUESTION_RX = re.compile(r"\bremind\s+(?:me|us)\s+(?:again\s+)?(?:what|why|how|who|where|which|whether|if)\b", re.I)
_AG_ADD_RX = re.compile(
    rf"\b(?:add|put|pop|stick|create|make|schedule|book|pencil in|save|log)\b[^?!\n]{{0,160}}?"
    rf"(?:\b(?:in|into|to|on|onto)\s+{_AG_WHOSE}\s+{_AG_CAL}\b|\b(?:an?|the|new)\s+(?:calendar\s+)?(?:event|entry|appointment)\b)", re.I)
_AG_BOOK_RX = re.compile(
    rf"^{_AG_POLITE}(?:schedule|book|pencil in)\s+(?:a|an|my|the|our)\s+(?:[\w'-]+\s+){{0,2}}?"
    r"(?:presentation|meeting|appointment|interview|exam|class|lecture|call|session|dinner|lunch|breakfast|brunch|party|"
    r"flight|gig|concert|match|seminar|workshop|check-?up|review|demo|stand-?up|webinar|tutorial|haircut|dentist|doctor|"
    r"gp|physio|vet|drinks|trip|rehearsal|training|table)\b", re.I)
_AG_ADD_IT_RX = re.compile(
    rf"^{_AG_POLITE}(?:add|put|pop|stick|schedule|save|log|book)\s+(?:it|that|this|them|those|the event)\b"
    rf"(?:\s+(?:in|into|to|on|onto)(?:\s+{_AG_WHOSE})?\s+{_AG_CAL})?(?:\s+(?:then|please|now|for me|in))*[\s.!]*$", re.I)


_AG_NOUN = (r"(?:event|reminder|appointment|meeting|alarm|presentation|interview|exam|class|lecture|call|session|dinner|"
            r"lunch|breakfast|brunch|party|flight|gig|concert|match|seminar|workshop|check-?up|review|demo|stand-?up|"
            r"webinar|tutorial|haircut|dentist|doctor|gp|physio|vet|drinks|trip|rehearsal|training)s?")
_AG_REMOVE_RX = re.compile(
    rf"^{_AG_POLITE}(?:cancel|delete|remove|drop|scrap|clear|wipe)\b[^?!\n]{{0,120}}?"
    rf"(?:\b{_AG_NOUN}\b|\b(?:from|in|on|off)\s+{_AG_WHOSE}\s+{_AG_CAL}\b|\b{_AG_WHOSE}\s+{_AG_CAL}\b)", re.I)
_AG_MOVE_RX = re.compile(
    rf"^{_AG_POLITE}(?:no[,.!\s]+|sorry[,.!\s]+|actually[,.!\s]+|wait[,.!\s]+|cancel that[,.!\s]+)*"
    r"(?:i meant|i mean|make (?:it|that)|change (?:it|that|the [\w' -]{2,40}?) to|move (?:it|that|the [\w' -]{2,40}?) to|"
    r"push (?:it|that|the [\w' -]{2,40}?) (?:back |forward )?to|reschedule (?:it|that|the [\w' -]{2,40}?)(?: (?:to|for))?|"
    r"it'?s actually(?: at| on)?|it is actually(?: at| on)?)\s+\S"
    r"|^(?:no|sorry|actually|wait)[,.!\s]+(?:it'?s|it is|that'?s)\s+(?:at|on)\s+\S", re.I)
_AG_ASKING_RX = re.compile(
    r"^(?:did|have|has|had|was|were|is|are)\s+(?:you|it|that|this|the|my|there)\b[^?]*\b(?:add|added|put|set|saved|in|on)\b", re.I)


def _eli_agenda_prepass(user_text: str):
    """Calendar and reminder requests, by what they say and not by a stray word.

    The rule this replaces matched substrings: "add the presentation at 7.30pm to my calendar"
    listed events (it contains "my"), "what a boring meeting" listed events, ELI's own
    "check your calendar for any scheduled events" added one (it contains "schedule"), and
    "remind me at 4pm to prepare" became a background work task."""
    t = (user_text or "").strip()
    if not t or len(t) > 400:
        return None
    low = t.lower()
    try:
        from eli.runtime.command_splitter import split_commands
        if len(split_commands(t) or []) >= 2:
            return None  # each command is routed on its own
    except Exception:
        log.debug("agenda prepass: split probe failed", exc_info=True)
    # "did you add it to my calendar?" asks what is there; showing the calendar answers it.
    if _AG_ASKING_RX.search(low) and re.search(rf"\b{_AG_CAL}\b|\b{_AG_ITEM}\b", low):
        return _mk("LIST_EVENTS", {"text": t}, 0.96, matched_by="agenda.list")
    # The rules anchored on the start of the message read the request's own form ("You add it!",
    # "can you cancel the dentist"), so they say so (meta "spoken_as_remark"). The two that may match
    # further in ("remind me", "add/put ... to my calendar") take an instruction of the message that
    # starts with their verb: "you remind me of my brother" set an alarm, and "my boss forgot to put
    # it on the schedule again" added an event.
    _own_form = {"spoken_as_remark": True}
    try:
        from eli.runtime.command_splitter import read_message as _ag_read
        _ag_told = [part["tells"] for part in _ag_read(t) if part["tells"]]
    except Exception:
        _ag_told = []
    if _AG_MOVE_RX.search(t):
        try:
            from eli.runtime.agenda import parse_when as _ag_when
            if _ag_when(t) is not None:
                # "i meant 8pm", "move the dentist to friday": changes what is there, adds nothing
                return _mk("ADD_EVENT", {"text": t, "move": True}, 0.96, matched_by="agenda.move", meta_extra=_own_form)
        except Exception:
            log.debug("agenda prepass: time probe failed", exc_info=True)
    if _AG_REMOVE_RX.search(t) and not re.search(
            r"\b(?:timer|pomodoro|download|upload|job|task|order|subscription|files?|folders?|notes?|documents?|photos?|"
            r"pictures?|emails?|messages?|apps?|contacts?|songs?|playlists?|tabs?|windows?|recordings?|minutes)\b", low):
        return _mk("REMOVE_EVENT", {"text": t}, 0.96, matched_by="agenda.remove", meta_extra=_own_form)
    if _AG_ADD_IT_RX.search(t):
        return _mk("ADD_EVENT", {"text": t, "from_context": True}, 0.96, matched_by="agenda.add_it", meta_extra=_own_form)
    if not _AG_REMIND_QUESTION_RX.search(t):
        _anchored_remind = _AG_REMIND_RX.match(t) and not _AG_REMIND_RX.match(t).group(0).lower().startswith("remind")
        if _anchored_remind or any(_AG_REMIND_RX.match(told) for told in _ag_told):
            return _mk("SET_ALARM", {"text": t}, 0.96, matched_by="agenda.remind",
                       meta_extra=_own_form if _anchored_remind else None)
    if _AG_BOOK_RX.search(t):
        return _mk("ADD_EVENT", {"text": t}, 0.96, matched_by="agenda.add", meta_extra=_own_form)
    if any(_AG_ADD_RX.match(told) for told in _ag_told):
        return _mk("ADD_EVENT", {"text": t}, 0.96, matched_by="agenda.add")
    if _AG_LIST_RX.search(low) and not _AG_NOT_LIST_RX.search(low):
        return _mk("LIST_EVENTS", {"text": t}, 0.96, matched_by="agenda.list")
    return None


# Banter and second-person commentary are not task requests, however many clock words they hold.
_SCHEDULE_CHATTER_RX = re.compile(
    r"\b(you think|you're|you are|being so|stop being|i think|i reckon|do you want|don't want|dont want"
    r"|go back to sleep|haha|lol|jesus christ|for fuck'?s sake|shut up|calm down|wake up)\b", re.I)
# "can you open spotify at 8pm?" is a request put politely, and the only question that schedules.
_SCHEDULE_POLITE_ASK_RX = re.compile(r"^\s*(?:please\s+)?(?:can|could|would|will)\s+you\b", re.I)


def _eli_schedule_prepass(user_text: str):
    """Detect 'do <any command> at <future time>' → SCHEDULE_TASK (background workers).

    Fires when a future-time marker is present AND the utterance is an IMPERATIVE
    command — a heavy task verb, a known schedulable action, OR an app/media/system
    verb (open/play/get/close…). So 'get a morning report ready for tomorrow',
    'open spotify at 8pm', and 'get the news at 7am' all SCHEDULE, while questions
    ('what's on tonight?'), alarms/timers (own handlers), and bare 'morning report'
    are NOT captured. The re-run `request` is the clean command (time stripped) so the
    background worker (eng.process) executes the action once and never re-schedules."""
    t = (user_text or "").strip()
    if len(t.split()) < 3:
        return None
    if not _SCHEDULE_TIME_RX.search(t):
        return None
    if _DEDICATED_TIME_RX.search(t):
        return None
    if _QUESTION_RX.search(t):
        return None
    # _QUESTION_RX is ^-anchored and only saw the start, so an imperative opener followed by a
    # question slipped through ("Stop talking about engines.. How are things, this evening?"
    # created a background task). Test the closing sentence too. Polite requests ("can you open
    # spotify at 8pm?") still schedule; only a real trailing question is refused.
    if _QUESTION_RX.search(_eli_final_clause(t)):
        return None
    # The opener only sees the start: "you think i want to run that at 8:30 on a sunday?" got scheduled.
    if any(not _SCHEDULE_POLITE_ASK_RX.match(q) for q in re.findall(r"[^.!?\n]+\?", t)):
        return None
    if _SCHEDULE_CHATTER_RX.search(t):
        return None
    m_action = _SCHEDULE_ACTION_RX.search(t)
    # The verb and the time belong to one sentence: anywhere in a paragraph, "stop" and "this morning" made a job.
    _timed = [s for s in re.split(r"(?<=[.!?])\s+|\n+", t) if _SCHEDULE_TIME_RX.search(s)]
    if not (m_action or any(_SCHEDULE_VERB_RX.search(s) or _IMPERATIVE_RX.search(s) for s in _timed)):
        return None
    # A day on its own usually says what the request is about, not when to do it: "help me plan
    # a study session for tonight", "check the weather for tomorrow", "write a speech for
    # tomorrow". Those were queued as background jobs for 2am. A bare "tonight" or "tomorrow"
    # makes a job only with a named report or a word that asks for it to be done later.
    if not (_EXPLICIT_WHEN_RX.search(t) or m_action or _DEFER_RX.search(t)):
        return None
    inner = m_action.group(0) if m_action else _strip_schedule_time(t)
    # the name of a report is a command on its own; the name of a test run is not
    if re.fullmatch(r"(?i)test suite|test report|engine eval|self[\s_-]?test", inner):
        inner = f"run the {inner}"
    return {
        "action": "SCHEDULE_TASK",
        "args": {"request": inner, "when": _extract_when(t)},
        "confidence": 0.9,
        "meta": {"matched_by": "schedule.prepass"},
    }


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
    # "open the browser and search KITE" is one browser request, not two. Splitting it opened the
    # home page and answered the search in chat. Only decline to split when the whole phrase
    # resolves to a browser search with a query; other chained utterances still split.
    try:
        from eli.execution.portable_intent_contract import try_route as _pic_route
        _whole = _pic_route(user_text or "")
        if (isinstance(_whole, dict)
                and _whole.get("action") == "OPEN_BROWSER"
                and (_whole.get("args") or {}).get("query")):
            return None
    except Exception:
        log.debug("multi_command: browser-request probe failed", exc_info=True)
    # "get out of your room and do some work" joins two remarks: each went to chat alone and came back
    # as a bulleted pair of replies. At least one part has to be something ELI can carry out.
    try:
        from eli.execution.route_contracts import is_command
        if not any(is_command(route(seg)) for seg in segs):
            return None
    except Exception:
        log.debug("multi_command: part check failed", exc_info=True)
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
_PM_PERSON_ATTR = (
    r"(?:interest|interests|habit|habits|personality|character|background|"
    r"academ\w+|research|work|studies|field|life|preference|preferences|"
    r"trait|traits|history|project|projects)"
)


def _eli_resolve_known_eli_file(low: str):
    """Map a reference to a well-known ELI file (by name, no full path) to its
    real absolute path, or None. Only returns files that actually exist, so a
    rename/removal is reflected immediately (no stale hardcoded claim). Lets
    "read your persona.auto.txt" / "is your settings file up to date" resolve to
    the real file instead of confabulating."""
    try:
        from pathlib import Path as _P
        from eli.core.paths import persona_auto_path, persona_path
        root = _P(__file__).resolve().parents[2]  # project root
        candidates = []
        if re.search(r"persona\.?\s*auto|auto\.txt|persona overlay|auto[- ]persona", low):
            # Where the overlay lives is eli.core.paths' to say (the config folder in an install, the
            # source tree in development); a fixed source-tree path found nothing in an install. The
            # overlay is ELI's own file whether or not it has been written yet: asked to read it,
            # SUMMARIZE_FILE says what is there, or that it is not there.
            return str(persona_auto_path())
        if "persona" in low and "auto" not in low:
            candidates.append(persona_path())
        if "settings.json" in low or re.search(r"\bsettings\s+(file|json)\b", low):
            from eli.core.runtime_settings import _settings_file
            candidates.append(_settings_file())
        if re.search(r"capability[\s_]manifest", low):
            from eli.tools.registry.capability_updater import current_manifest_path
            candidates.append(current_manifest_path())
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


def _media_words_beyond(low: str, app: str) -> List[str]:
    """The words a media command to `app` says beyond the command itself: its verbs, the app, what
    a command is made of ("the", "on", "please", "music", "it"). "pause spotify please" says nothing
    more; "play the long road playlist spotify" names the long road playlist."""
    from eli.execution.route_contracts import _GRAMMAR
    from eli.execution.routing.command_parser import _MEDIA_NOUNS
    verbs = ("unmute", "mute", "pause", "stop", "resume", "play", "next", "skip", "previous", "prev", "back",
             "shuffle", "repeat", "loop", "continue", "start")
    app_words = set(app.lower().split()) | {"app", "website", "site", "player", "using", "via", "through"}
    out = []
    for w in re.findall(r"[a-z0-9']+", low.lower()):
        if (w in _GRAMMAR or w in app_words or w in _MEDIA_NOUNS or w in ("thanks", "cheers", "mate", "now")
                or any(w == v or (w.startswith(v) and len(w) - len(v) <= 3) for v in verbs)):
            continue
        out.append(w)
    return out


def _canonical_media_command(low: str) -> Optional[str]:
    # Whole-word matching only: substring matching fired media commands from prose ("prev" in
    # "prevent", "mute" in "commuter"). Word boundaries don't save "next" in "the next log lands",
    # so callers must also gate on command shape and length. Ordering matters.
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
    if any(re.search(p, low) for p in patterns):
        return True
    # Any request to recall what was said between the user and ELI over a period it names
    # ("timestamp and summary of our conversations over the past 5 days", "what have we discussed
    # this week"). The period is the query planner's to read; it was matched here by four phrasings
    # only, and the rest went to the model's guess at an action (a status report, live).
    if _OUR_TALK_RE.search(low):
        try:
            from eli.cognition.query_planner import plan_window
            return plan_window(raw) is not None
        except Exception:
            log.debug("period not read for a recall request", exc_info=True)
    return False


_OUR_TALK_RE = re.compile(
    r"\b(?:we|our|us|you and (?:i|me)|me and you)\b.{0,60}?\b(?:conversations?|chats?|sessions?|discussions?|talk(?:s|ed|ing)?|"
    r"discuss(?:ed|ing)?|spoke|spoken|said|covered|went (?:over|through)|been (?:doing|working on))\b"
    r"|\b(?:conversations?|chats?|sessions?|discussions?)\b.{0,40}?\b(?:we|our|us)\b"
    r"|\b(?:conversation|chat|session)s?\s+(?:history|logs?|records?)\b", re.I)


def _looks_like_conversation_summary(low: str) -> bool:
    return any(re.search(p, low) for p in [
        r"^summari[sz]e\s+(?:the\s+)?(?:conversation|chat|discussion|what we talked about)",
        r"^summari[sz]e\s+(?:the\s+)?last\s+\d+\s+(?:messages|conversations|turns)",
    ])


# ============================================================
# ROUTER
# ============================================================
def _eli_asked_for_the_name() -> bool:
    """ELI's last message asked the user what they are called."""
    try:
        from eli.runtime.pending_proposal import get_follow_up
        asked = str((get_follow_up() or {}).get("question") or "").lower()
    except Exception:
        return False
    return bool(re.search(r"\b(?:your name|call you|who (?:am i|i'?m|i am) (?:talking|speaking|working) (?:to|with))\b", asked))


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

    # "my name is X" / "call me X" go through the same extractor as the profile and the
    # graph: it rejects "my name is listed wrongly, ..." (renamed a user "listed").
    from eli.runtime.identity_validation import (
        _DECLARATION_RE, _drop_hypothetical_clauses, extract_explicit_identity_facts)
    _said = _drop_hypothetical_clauses(_re.sub(r"(?i)\bmy name's\b", "my name is", raw))
    # The last declaration wins: "my name is speak, my name is alex" means alex.
    _decls = list(_DECLARATION_RE.finditer(_said))
    _facts = extract_explicit_identity_facts(_said[_decls[-1].start():]) if _decls else {}
    _declared = _facts.get("name") or _facts.get("preferred_name") or _facts.get("nickname") or ""
    if _declared and _declared.lower() not in {"you", "eli"}:
        # A declaration is the request said as a statement: "my name is Alex" tells ELI nothing to
        # do by its form, and is what it is (route_contracts.fits).
        return _mk("SET_USER_NAME", {"name": _declared}, 0.97, matched_by="identity.set_user_name",
                   meta_extra={"spoken_as_remark": True, "declared_by": "my_name_is"})
    if _re.search(r"\b(?:my name is|my name's|call me)\b", low):
        return None

    # "i'm X" / "i am X" as the whole message, or a bare capitalised name. "I'm tired", "i am bored"
    # and "I'm home!" say how or where the user is, not who: after "I'm" a word is a name only as
    # typed with a capital (a proper noun), or as the answer when ELI has just asked for the name
    # (speech arrives in lower case). The pattern matched any case, and every one of those renamed
    # the user.
    _asked = _eli_asked_for_the_name()
    _patterns = (
        r"^(?i:i'?m|i am|im)\s+([A-Za-z][a-z]{2,24})\s*[.,!?]?\s*$" if _asked
        else r"^(?i:i'?m|i am|im)\s+([A-Z][a-z]{2,24})\s*[.,!?]?\s*$",
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
        _matches = list(_re.finditer(pat, raw.strip()))
        m = _matches[-1] if _matches else None
        if m:
            candidate = m.group(1).strip().rstrip(".,!?")
            _clow = candidate.lower()
            _has_vowel = any(c in "aeiou" for c in _clow)
            # Bare-word pattern (index 1): require actual uppercase first letter in raw text.
            # STT produces all-lowercase; a typed name would be capitalised.
            if i == 1 and (not raw or not raw[0].isupper()):
                continue
            # A word on its own is a name only as the answer to being asked for one. Without
            # this "Dublin", "Tuesday" or "Pizza", typed in reply to anything, renamed the user.
            if i == 1 and not _asked:
                continue
            # the one validator of a name every identity path uses (runtime/identity_validation)
            from eli.runtime.identity_validation import normalize_identity_candidate
            if not normalize_identity_candidate(candidate, max_words=1):
                continue
            if _clow not in _bad and len(candidate) >= 3 and _has_vowel:
                # read from one word, not from "my name is": what it replaces is the executor's to
                # keep until the user confirms, so the executor is told how it was said
                _how = "answer_to_name_question" if _asked else "i_am_word"
                return _mk(
                    "SET_USER_NAME",
                    {"name": candidate, "declared_by": _how},
                    0.97,
                    matched_by="identity.set_user_name",
                    meta_extra={"spoken_as_remark": True, "declared_by": _how},
                )
    return None


# What may follow how ELI is to speak without being about something else: "use a dry tone with me",
# "... from now on". "use the apa style for citations" names the citations, not ELI's voice.
_STYLE_FOR_ELI = re.compile(
    r"\s*(?:(?:with|for|to)\s+(?:me|us)|when\s+(?:you\s+)?(?:talk|speak|answer|reply|respond)(?:\s+to\s+(?:me|us))?"
    r"|in\s+(?:your\s+)?(?:answers|replies|responses)|from\s+now\s+on|for\s+now|please|thanks|again|instead"
    r"|[\s,.!?])*$", re.I)
# A way of speaking for a moment ("let's be serious for a second") is not a standing preference.
_FOR_A_MOMENT = re.compile(r"\bfor\s+(?:a|one)\s+(?:sec(?:ond)?|moment|minute|bit|while|tick)\b", re.I)


def _route_set_communication_style(raw: str, low: str) -> Optional[Dict[str, Any]]:
    """Detect explicit 'speak to me like X' / 'change your tone to X' requests and route to
    SET_COMMUNICATION_STYLE so the tone preference persists into the persona brief.

    A standing change to how ELI speaks is made only when the user tells ELI so: each pattern is
    read at the start of an instruction (command_splitter.instruction), not anywhere in a sentence.
    "answer as soon as you can" asks for speed ("as ... as" compares, it names no manner), "use the
    apa style for citations" is about the citations, and "let's be serious for a second" is a moment
    the user shares, not an instruction for ELI's persona. All three were stored as ELI's style."""
    import re as _re
    from eli.runtime.command_splitter import instruction, sentences
    _manner = r"(?:as(?!\s+(?:\w+\s+)?as\b)(?!\s+(?:well|much|many|long|soon|far|if\s+i)\b)|like)"
    _pats = (
        r"(?:speak|talk)\s+to\s+me\s+(?:like|as|more|in a way that'?s?|in)\s+(.{2,80})",
        r"(?:respond|reply|answer|talk)\s+(?:to me\s+)?" + _manner + r"\s+(.{2,80})",
        r"(?:change|set|update|make)\s+your\s+(?:tone|persona|voice|style|character|way you (?:speak|talk))\s+(?:to|into)?\s*(.{2,80})",
        r"(?:be|talk|speak)\s+(?:more\s+|less\s+)?((?:funny|funnier|blunt|blunter|formal|casual|serious|sarcastic|playful|concise|verbose|warm|friendly|professional|direct|witty|humorous|relaxed)(?:\s+and\s+[\w ]+)?)",
        r"(?:use|with)\s+(?:a\s+)?(.{2,60}?)\s+(?:tone|voice|humou?r|style)\b",
    )
    told = []
    for _sentence in sentences(raw):
        if _re.match(r"\s*let'?s\b", _sentence, _re.I) or _FOR_A_MOMENT.search(_sentence):
            continue
        _said = instruction(_sentence)
        if _said:
            told.append(_said)
    found = None
    for pat in _pats:
        for _said in told:
            m = _re.match(pat, _said, _re.IGNORECASE)
            if m and (pat is not _pats[4] or _STYLE_FOR_ELI.match(_said[m.end():])):
                found = m
                break
        if found:
            break
    if found is None and not _FOR_A_MOMENT.search(raw):
        found = _re.search(r"\bi want you to (?:speak|talk|respond|be)\s+(.{2,80})", raw, _re.IGNORECASE)
    for m in ((found,) if found else ()):
        if m:
            style = m.group(1).strip().strip('."\'!?,').strip()
            if len(style) >= 2:
                # Disambiguate a spoken voice from persona tone. "use the alan voice" / "use the HAL
                # voice" name a real TTS voice, so route to SET_VOICE; a phrase that isn't a real
                # voice ("use a sarcastic voice", "speak formally") stays persona tone.
                if _re.search(r"\bvoice\b", low):
                    try:
                        from eli.runtime.voice_assets import resolve_voice_query
                        if resolve_voice_query(raw).get("voice"):
                            return _mk("SET_VOICE", {"query": raw}, 0.95,
                                       matched_by="voice.set.persona_disambig")
                    except Exception:
                        log.debug("voice disambig in persona route failed", exc_info=True)
                # A named emotion/tone from the palette ("be comedic", "more sarcastic",
                # "professional tone") → SET_TONE (shades delivery, keeps core persona).
                # A free-text style that isn't a palette tone stays persona.
                try:
                    from eli.cognition.emotion_palette import resolve_tone as _rt
                    if _rt(style) or _rt(raw):
                        return _mk("SET_TONE", {"tone": (_rt(style) or _rt(raw))}, 0.94,
                                   matched_by="tone.set.persona_disambig")
                except Exception:
                    log.debug("tone disambig in persona route failed", exc_info=True)
                return _mk(
                    "SET_COMMUNICATION_STYLE",
                    {"style": style},
                    0.95,
                    matched_by="persona.set_communication_style",
                )
    return None


# Technical runtime questions: routing/_helpers._RUNTIME_STATUS_RE (one owner; the self-report
# stage reads the same one).
from eli.execution.routing._helpers import runtime_status_asked  # noqa: E402


# The GUI turns an attached image into a marker: [Attached image: shot.png — path: /home/u/
# Pictures/shot.png]. The generic path extractor swept up the closing bracket, so the path
# didn't exist and an image went to SUMMARIZE_FILE instead of ANALYZE_IMAGE. Parse the marker
# exactly, which also handles spaces and brackets.
_ATTACHMENT_MARKER_RE = re.compile(
    r"\[\s*Attached\s+(?:image|file)\s*:[^\]]*?\bpath\s*:\s*(?P<path>[^\]]+?)\s*\]",
    re.I,
)


def _attachment_marker_path(raw: str) -> Optional[str]:
    """Path out of the GUI's attachment marker, without its closing bracket."""
    m = _ATTACHMENT_MARKER_RE.search(str(raw or ""))
    if not m:
        return None
    return (m.group("path") or "").strip() or None


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

    if runtime_status_asked(raw):
        return _mk("RUNTIME_STATUS", {"question": raw}, 0.99,
                   matched_by="runtime.status.identity_grounded_chat", allow_chat_without_evidence=False)

    # Identity precedence: pure persona questions go to CHAT so ELI answers in its own voice.
    # SELF_REPORT is only for technical runtime queries (model path, gpu layers, provider, context
    # size), not "who are you". "do you know who I am AND who you are" wants one natural answer.
    if (re.search(r"\bwho\s+(?:i\s+am|am\s+i)\b", raw, re.I)
            and re.search(r"\bwho\s+(?:you\s+are|are\s+you)\b", raw, re.I)):
        return _mk("CHAT", {"message": raw}, 0.96,
                   matched_by="identity.relational_both", allow_chat_without_evidence=True)

    if re.search(
        r"\b(?:who are you|what are you(?!\s+\w)|what is your name|what's your name|"
        r"tell me about yourself|what do you know about yourself|"
        r"what do you know of yourself|what else can you tell me about yourself|"
        r"describe yourself)\b",
        raw, re.I
    ):
        return _mk("CHAT", {"message": raw}, 0.99, matched_by="identity.persona_chat", allow_chat_without_evidence=True)

    # Last-turn trace questions need retrieval plus the live _prev_bus_result. COGNITION_STATUS
    # returned static line numbers and made the 7B confabulate confidence/agent values. CHAT lets
    # stages 3-11 engage and splices in the real prior-turn AgentBus metadata.
    if re.search(r"\b(confidence in (?:your|my) last response|which agents contributed|what agents contributed|agents contributed|grounded trace|trace metadata|last turn trace|previous response trace)\b", raw, re.I):
        return _mk("EXPLAIN_LAST_RESPONSE", {}, 0.99,
                   matched_by="router.explain_last_response",
                   allow_chat_without_evidence=False)

    # Belief audit — "why did you think I'm a developer?" → stored memories + ledger.
    if re.search(
        r"\bwhy\s+did\s+you\s+(?:think|believe|assume|remember|say)\b"
        r"|\bwhy\s+do\s+you\s+think\s+(?:that|this|i)\b"
        r"|\bmemory\s+audit\s+for\b"
        r"|\bexplain\s+(?:your|the)\s+(?:belief|memory|assumption)\b",
        raw, re.I,
    ):
        q = raw
        m = re.search(
            r"\bwhy\s+did\s+you\s+(?:think|believe|assume|remember|say)\s+(.+?)[?.!]*$",
            raw, re.I,
        )
        if m:
            q = m.group(1).strip(" ?.")
        m2 = re.search(r"\bmemory\s+audit\s+for\s+(.+?)[?.!]*$", raw, re.I)
        if m2:
            q = m2.group(1).strip(" ?.")
        return _mk("MEMORY_AUDIT", {"query": q or raw}, 0.96,
                   matched_by="router.memory_audit.belief", need_grounding=True,
                   allow_chat_without_evidence=False)


    if _is_identity and not _has_system_kw:
        return _mk("CHAT", {"message": raw}, 0.95, matched_by="identity.chat_classified")


_JOB_PROGRESS = re.compile(
    r"\b(?:eta(?!\s+(?:on|for|of)\s+(?!(?:it|that|this|the\s+job)\b))|how\s+much\s+longer|how\s+far\s+along|time\s+(?:left|remaining)|any\s+progress|"
    r"how\s+long\s+(?:left|to\s+go|(?:will|does|is|should)\s+(?:it|that|this)(?:\s+(?:take|be))?)|"
    r"(?:is\s+(?:it|that)|it'?s)\s+(?:done|finished|ready)(?:\s+yet)?|"
    r"how'?s\s+(?:it|that)\s+(?:going|coming\s+along))\b")


_PATH_START = re.compile(r"(?<![\w/~.])(?:~/|[A-Za-z]:[\\/]|/(?=\S))")
_DOC_ASK = re.compile(r"\b(?:summari[sz]e|analy[sz]e|read|look\s+(?:at|through)|evaluate|go\s+through|process|index)\b", re.I)
_REMAINING = re.compile(r"\b(?:remaining|the\s+rest|the\s+others?|other\s+\d*\s*(?:pdf|file|folder|document|paper))", re.I)
_REFERS_TO_WORK = re.compile(r"\b(?:them|those|these)\b|" + _REMAINING.pattern, re.I)


def paths_named(text: str) -> list:
    """Every file or folder a message names that exists, as (as written, absolute). Paths hold spaces
    and brackets, so each is the longest stretch that exists; what follows it is searched too. The
    rule that read one path took the first of four folders and dropped the others without a word."""
    found = []
    for line in str(text or "").splitlines():
        pos = 0
        while True:
            m = _PATH_START.search(line, pos)
            if not m:
                break
            words = line[m.start():].split(" ")
            hit = next((c for c in (" ".join(words[:k]).rstrip(" ,;:") for k in range(len(words), 0, -1))
                        if os.path.exists(os.path.expanduser(c))), None)
            if hit is None:
                pos = m.end()
                continue
            whole = os.path.abspath(os.path.expanduser(hit))
            if whole not in (p for _, p in found):
                found.append((hit, whole))
            pos = m.start() + len(hit)
    return found


def _holds_a_pdf(path: str) -> bool:
    if os.path.isfile(path):
        return path.lower().endswith(".pdf")
    return any(n.lower().endswith(".pdf") for _, _, names in os.walk(path) for n in names)


def _route_several_paths(text: str, low: str):
    """Several files or folders and a word asking to read them: all of them, as one piece of work."""
    if "/" not in text and "\\" not in text:
        return None
    named = paths_named(text)
    if len(named) < 2:
        return None
    rest = text
    for spelt, _ in named:
        rest = rest.replace(spelt, " ")
    if not _DOC_ASK.search(rest):
        return None
    paths = [p for _, p in named]
    _keep_last_used_path(paths[-1])
    if all(_holds_a_pdf(p) for p in paths):
        return _mk("ANALYZE_PDF_FOLDER", {"folders": paths, "recursive": True, "instruction": text},
                   0.95, matched_by="analyze.several_paths")
    return _mk("MULTI_COMMAND", {"commands": [f"summarise {p}" for p in paths], "raw": text},
               0.92, matched_by="analyze.several_paths")


def _route_work_in_hand(raw: str, low: str):
    """"Them", "those", "the 3 remaining folders": the files or folders of the job in hand, not a
    topic to look up in memory and not the current folder ("Not a PDF: /home/x"). None with nothing
    in hand. "The rest" of a job that finished has nothing to run: that one is talked through."""
    if not _REFERS_TO_WORK.search(low):
        return None
    from eli.runtime.background_tasks import work_targets
    remaining = bool(_REMAINING.search(low))
    targets = work_targets(unfinished_only=remaining)
    if not targets and remaining and work_targets():
        return _mk("CHAT", {"message": raw}, 0.85, matched_by="chat.nothing_remaining")
    _last = _last_used_path()
    if not targets and _last and not remaining:
        targets = [_last]
    targets = [t for t in targets if os.path.exists(os.path.expanduser(str(t)))]
    if not targets:
        return None
    return _mk("ANALYZE_PDF_FOLDER",
               {"folders": targets, "recursive": True, "instruction": raw, "_force_background": True},
               0.9, matched_by="analyze.work_in_hand")


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

    # ELI's inference RAM (model + KV cache) — not system psutil, not SQLite memory DB.
    try:
        from eli.runtime.inference_footprint import is_inference_ram_question
        if is_inference_ram_question(raw):
            return _mk(
                "EXPLAIN_COGNITION_RUNTIME",
                {"question": raw, "diagnostic_focus": "inference_ram"},
                0.995,
                matched_by="plugin.prepass.inference_ram",
                need_grounding=True,
                allow_chat_without_evidence=False,
                task_family="grounded_audit",
            )
    except Exception:
        log.debug("inference RAM prepass unavailable", exc_info=True)

    if re.match(r"^(?:ram\s+usage|memory\s+usage|show\s+ram\s+usage|show\s+memory\s+usage|how\s+much\s+ram\s+is\s+used)$", low):
        return _mk("RAM_USAGE", {}, 0.98, matched_by="plugin.prepass.ram_usage")

    # Hardware/system temperature goes to grounded GPU status (which has the real temp), never
    # weather. "temperature/thermal/how hot" about the gpu/cpu/card/system, or a bare "temperature
    # reading(s)" follow-up, went to GET_WEATHER and ELI then denied data it had just shown. Still
    # guarded against a real weather/location query.
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
        # "change/play/switch music|media|audio to kitchen speaker / device 1". The media noun was
        # optional before an open-ended tail, so prose ("fair play ON your explanation...") parsed as a
        # device name and hit the Bluetooth stack. Now the utterance must open like a command, the tail
        # must look like a device name, and the media word or the tail (headphones/speaker) is named.
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

    # Generic smart-home commands with no "smart home" prefix ("turn off the lights", "dim the
    # bedroom lamp", "set the thermostat to 20"). SMART_HOME is a core action on ELI's own MQTT
    # device server. Scoped to real device nouns so "turn off the music" and "turn off the pc"
    # are untouched.
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


def _eli_react_to_content_prepass(original_text: str, raw: str, low: str):
    """Force CHAT when the user asks ELI to react to / give an opinion on text
    they have pasted or referenced.

    Quoted material must never be scanned for status/command intent: the words
    inside a pasted block belong to the thing being judged, not to an
    instruction. Without this, a long paste that happens to mention ELI's
    internals ("memory", "faiss", "runtime", "context") trips a status regex
    and the turn is hijacked into a deterministic data dump instead of an
    actual reply. Routing to CHAT with the full (whitespace-normalised) text as
    the message also guarantees the referenced content is present in the prompt
    so the model can genuinely evaluate it rather than confabulate.
    """
    if not low:
        return None
    react_frame = re.search(
        r"\b(what(?:'s| is| do| are)?\s+(?:you|your)\s+(?:think|reckon|make|take|opinion|view|thoughts?)"
        r"|what do you (?:think|reckon|make) of"
        r"|your (?:thoughts?|opinion|take|view)\b"
        r"|thoughts on\b"
        r"|respond to\b|react to\b|reply to\b"
        r"|the following (?:reply|response|message|text|paragraph)"
        r"|this (?:reply|response|message|paste|text)\b"
        r"|(?:reply|response|message|text) (?:from|by) \w+"
        r"|i (?:just )?(?:sent|pasted|posted|shared|wrote)\b"
        r"|(?:message|reply|response|text) (?:above|earlier)\b"
        r"|(?:above|earlier) (?:message|reply|response)\b"
        r"|read (?:this|the following)\b)",
        low,
    )
    if not react_frame:
        return None
    src = original_text or ""
    has_paste = ("-->" in src) or (src.count("\n") >= 2) or (len(src) > 600)
    points_at_content = bool(re.search(
        r"\b(the following|this (?:reply|response|message|paste|text)"
        r"|(?:reply|response|message|text) (?:from|by) \w+"
        r"|i (?:just )?(?:sent|pasted|posted|shared)"
        r"|above|earlier|chatgpt|gpt|that (?:reply|response|message))\b",
        low,
    ))
    if not (has_paste or points_at_content):
        return None
    return _mk(
        "CHAT",
        {"message": raw},
        0.9,
        matched_by="chat.react_to_pasted_content",
    )


_LINK_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.I)
_OPEN_LINK_RE = re.compile(r"^\s*(?:open|launch|visit|browse|go\s+to|navigate\s+to|show)\b", re.I)


def _eli_linked_page_prepass(raw: str):
    """A link with a question or request about it ("<link> --> what do you think?", "summarise
    this <link>"): the page is read (WEB_FETCH) and the reply answers from it. Answered as pasted
    text, ELI said it could not browse the web. A link on its own, or with "open"/"go to", is left
    to the rules that open it."""
    m = _LINK_RE.search(raw or "")
    if not m:
        return None
    url = m.group(0).rstrip(".,;:!?")
    rest = re.sub(r"[-=]+>|<[-=]+|[\s,;:]+", " ", (raw[:m.start()] + " " + raw[m.end():])).strip()
    if not rest or _OPEN_LINK_RE.match(rest) or len(rest.split()) > 40:
        return None
    from eli.runtime.command_splitter import is_request, starts_with_command
    asks = "?" in rest or bool(re.match(r"(?:what|how|why|who|which|is|are|does|do|can|could|would|should)\b",
                                        rest, re.I))
    if not (asks or starts_with_command(rest, after_lead=True) or is_request(rest)):
        return None
    return _mk("WEB_FETCH", {"url": url}, 0.95, matched_by="web.read_linked_page", entities={"url": url})


def _eli_web_lookup_prepass(raw: str, low: str):
    """Route real-time factual lookups and explicit web searches to WEB_SEARCH
    when networking is enabled.

    Without this, 'search for X', 'when is X out', 'release date of X',
    'latest news on X' fell through to CHAT and were answered from the model's
    stale training weights — and ELI would then argue with the user about
    current facts it cannot actually know. Gated on network_allowed(): offline
    mode returns None (stays CHAT) so the persona can say it cannot check live
    rather than confabulate.
    """
    if not low:
        return None
    # Never hijack ELI-internal / self / memory introspection or media control.
    if re.search(r"\b(my|your|you)\b.{0,30}\b(memory|memories|remember|stored|profile|runtime|cognition|capabilit|status|identity|persona)", low):
        return None
    if re.match(r"^(play|pause|resume|stop|next|previous|open|close|volume|mute|unmute)\b", low):
        return None
    # Local notes search/list is SEARCH_NOTES/LIST_NOTES, not a web lookup.
    if re.search(r"\b(search|find|list|show|read|open)\s+(?:my\s+|the\s+)?notes?\b", low):
        return None
    # "news" has a dedicated NEWS_FETCH path — let core_router handle it.
    if re.search(r"\bnews\b", low):
        return None
    # Meta/clarification questions about a previous answer are conversation, not lookups ("how do
    # you know X if you don't know Y", "what do you mean", "why did you say that"). Don't web-search
    # the sentence; let CHAT answer, even if a trigger like "release date" is in it.
    if re.match(
        r"^\s*(?:how (?:do|would|can|could) (?:you|u) know"
        r"|what do you mean|what'?s that supposed to mean"
        r"|why (?:did|do|would) you (?:say|think|claim|tell|state)"
        r"|that(?:'?s| is) (?:not right|wrong|incorrect|nonsense)"
        r"|you(?:'?re| are) (?:wrong|lying|making|confus))",
        low,
    ):
        return None

    explicit_search = re.search(
        r"\b(search\s+(?:the\s+)?(?:web|internet|online)|search\s+(?:for|on|about)|search\b|"
        r"(?:do|run|try|make)\s+(?:a|another|one\s+more)\s+(?:quick\s+|web\s+|proper\s+)?search(?:\s+(?:on|for|about))?|"
        r"look\s+(?:it|this|that)?\s*up|google\b|"
        r"check\s+the\s+(?:internet|web|online)|find\s+out)\b",
        low,
    )
    realtime_fact = re.search(
        r"\b(when\b.{0,45}\b(out|air|airs|airing|aired|release|released|releasing|"
        r"premier|premiere|coming\s+out|due|drop|drops)"
        r"|(?:season|episode|movie|film|game|album|series)\b.{0,30}\b(out|air|airing|"
        r"release|released|coming|drop|when)"
        r"|release\s+date|what'?s\s+the\s+latest|current\s+(?:price|score)"
        r"|who\s+won|how\s+much\s+is|is\s+.+\s+out\s+yet)\b",
        low,
    )
    if not (explicit_search or realtime_fact):
        return None

    try:
        from eli.core.config import network_allowed
        if not network_allowed():
            return None  # offline: stay CHAT; persona explains it can't check live
    except Exception:
        return None

    try:
        from eli.runtime.conversation_thread import (
            build_thread_aware_query,
            web_query_has_substance,
        )
        # "search again!!" searched for the words "search again": what was asked about last is in
        # the recent messages. A bare "search" with nothing pointing back asks, as before.
        _points_back = bool(re.search(r"\b(?:again|once more|one more time|it|that|this|those|them)\b", low))
        query = build_thread_aware_query(raw, _recent_turns_for_search() if _points_back else [])
    except Exception:
        query = raw.strip()
    if realtime_fact and realtime_fact.start() > 0 and query == raw:
        # No explicit "search for..." stem, but a real-time fact phrase appears mid-sentence after a
        # conversational or profane preamble. Trim to the factual ask ("when is X out") so the
        # search query isn't the whole frustrated sentence.
        query = raw[realtime_fact.start():].strip()
    query = re.sub(r"\b(for me|please|now|on the internet|online)\b\.?$", "", query, flags=re.I).strip(" .?!")
    if not query:
        query = raw.strip()
    # Self-referential internals — "search your code and tell me how your agents work",
    # "search your codebase for how cognition works" — are about ELI's OWN system, NEVER the
    # web. Fall through (return None) so the grounded cognition/agents/memory guards answer.
    if re.search(r"\byour\s+(?:own\s+)?(?:code|codebase|agents?|cognition|"
                 r"pipeline|architecture|internals?|memory\s+system)\b", raw, re.I):
        return None
    # "what did you actually search for?" asks about ELI's last turn, and searches for nothing.
    from eli.cognition.self_claims import asks_about_own_behaviour
    from eli.runtime.command_splitter import tells_eli_to_do
    if asks_about_own_behaviour(raw) and not tells_eli_to_do(raw):
        return None
    # Bare search instruction with NO subject — fall back to CHAT so ELI asks
    # what to look up instead of searching its own instruction.
    if not web_query_has_substance(query):
        return None
    return _mk("WEB_SEARCH", {"query": query}, 0.92,
               matched_by="web.realtime_lookup", entities={"query": query})


# Words that are never a real news topic on their own — question fragments,
# politeness tails, and generic news adjectives. A topic that reduces to one of
# these (or to nothing) means "no specific topic → general briefing".
_NEWS_TOPIC_NOISE = frozenset({
    "the", "a", "an", "any", "some", "today", "todays", "world", "current",
    "latest", "recent", "news", "what", "whats", "is", "are", "was", "were",
    "it", "me", "you", "us", "them", "that", "this", "please", "now", "thanks",
    "thank", "for", "about", "on", "in", "of", "more", "story", "stories",
})

# Carrier/question tokens that, surviving trimming anywhere in a capture, show the regex grabbed an
# instruction fragment ("can you tell") and not a subject. A topic with any of them is rejected, so
# the ask becomes a general briefing and not a garbage-topic dump.
_NEWS_TOPIC_CARRIER = frozenset({
    "can", "could", "would", "should", "will", "shall", "do", "does", "did",
    "have", "has", "had", "tell", "give", "show", "get", "find", "fetch",
    "let", "know", "want", "ask", "say", "bring", "pull",
    "your", "my", "our", "i", "we", "they", "he", "she", "him", "her",
})


def _sanitise_news_topic(topic: str) -> str:
    """Normalise a raw regex-captured news topic into a clean subject, or "".

    Strips politeness/filler tails ("for you", "please", "right now"), leading
    and trailing stop-words, and rejects topics that reduce to pure noise. This
    is what stops "...the latest news for you" extracting topic="you" and
    "...on Hubble for you" extracting topic="hubble for you".
    """
    t = (topic or "").strip().lower()
    t = re.sub(r"[?.!,;:]+$", "", t).strip()
    # Drop trailing politeness / deictic filler.
    t = re.sub(r"\s+(?:for\s+(?:you|me|us)|right\s+now|please|thanks?|"
               r"today|now|for\s+a\s+\w+)\s*$", "", t).strip()
    # Trim leading/trailing stop-words token by token.
    toks = [w for w in re.split(r"\s+", t) if w]
    while toks and toks[0] in _NEWS_TOPIC_NOISE:
        toks.pop(0)
    while toks and toks[-1] in _NEWS_TOPIC_NOISE:
        toks.pop()
    cleaned = " ".join(toks).strip()
    if not cleaned or all(w in _NEWS_TOPIC_NOISE for w in toks):
        return ""
    # A surviving carrier/question token means the capture is an instruction fragment
    # ("can you tell"), not a subject — reject so the ask falls through to general news.
    if any(w in _NEWS_TOPIC_CARRIER for w in toks):
        return ""
    if len(cleaned) < 2:
        return ""
    return cleaned


def _eli_proof_of_reading_challenge(low: str) -> bool:
    """True when the user is challenging ELI to prove it actually read/scanned a
    file or its own code — skepticism about ELI's *own action*.

    This reads the whole utterance for the challenge frame rather than matching
    isolated keywords, so it separates two superficially-similar shapes that a
    bare keyword pair cannot:

      • CHALLENGE  → audit:  "prove that you actually scanned the file",
        "timestamps as proof that you read the file", "did you even open it?"
      • IMPERATIVE → not audit: "read the data in that file and prove it's right"
        — here "prove" is about the *data's* correctness and "read" is a request,
        not a claim that ELI already read anything.

    The discriminator is whether the proof is aimed at ELI's reading action: a
    second-person past/perfect claim ("you read/scanned/opened…"), a "proof that
    you …" / "prove (that) you …" frame, or a "did/have you (actually) read…"
    frame — OR an explicit reference to ELI's own runtime/source surface.
    """
    if not re.search(
        r"\b(prove|proof|timestamps?|don'?t believe|do not believe|"
        r"are you lying|you'?re lying|convince me)\b",
        low,
    ):
        return False
    # (a) explicit reference to ELI's own runtime/source.
    runtime_ref = re.search(
        r"\b(gui|audit|runtime|your (?:code|source|files?)|source code|the codebase)\b",
        low,
    )
    # (b) a challenge aimed at ELI's *own* reading/scanning action. Bare "you
    #     read" is excluded (it collides with the request "can you read…");
    #     it only counts with an emphatic adverb or inside a proof/did-you frame.
    eli_read_claim = (
        re.search(r"\bproof that you\b", low)
        or re.search(r"\bprove (?:to me )?(?:that )?(?:you|u)\b", low)
        or re.search(r"\b(?:did|have|haven'?t|didn'?t) (?:you|u) "
                     r"(?:actually |even |really )?(?:read|scan|open|look)", low)
        or re.search(r"\b(?:you|u) (?:actually|really|even|genuinely|truly) "
                     r"(?:read|scann?ed|looked at|opened|examined|checked)\b", low)
        or re.search(r"\b(?:you|u) (?:scann?ed|looked at|opened|examined)\b", low)
    )
    return bool(runtime_ref or eli_read_claim)


def _trailing_request_clause(raw: str) -> str:
    """The final sentence of a multi-sentence utterance, if it is a short request.

    The long-question guard measures the whole utterance, but the request often
    lives in the last clause: "No, i don't want to go round in circles either, the old one
    is broken now anyway. What's the morning report?" is 21 words, while
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


_ON_SCREEN_RE = re.compile(
    r"\b(?:on|in)\s+(?:my|the|this)\s+(?:screen|display|monitor)\b|\bwhat\s+(?:do\s+you\s+)?see\s+on\s+(?:my|the)\b"
    r"|\blook(?:ing)?\s+at\s+(?:my|the)\s+screen\b", re.I)
_SEE_CUE_RE = re.compile(r"\b(?:see|visible|showing|shown|displayed|open|appears?|looking|look|there\s+(?:a|an|any)|is\s+there|are\s+there|what(?:'s| is| are))\b", re.I)


def _asks_what_is_on_screen(low: str) -> bool:
    """A question about what the screen currently shows. It needs the screen read, not a chat answer."""
    return bool(_ON_SCREEN_RE.search(low) and _SEE_CUE_RE.search(low))


def _asks_now_playing(low: str) -> bool:
    """A question about the LIVE media session — what is currently playing or being watched —
    including wrapped request forms ('do you know what I'm watching', 'can you check what I'm
    watching now'). Routes to NOW_PLAYING so ELI reads the active session instead of guessing from
    memory/history. Declarative remarks ('you can see what I'm watching') carry no request lead, so
    they deliberately fall through to chat. 'what's on' is left out — too ambiguous with the
    calendar/agenda ('what's on today')."""
    if re.search(r"\bnow\s+playing\b", low):
        return True
    if re.search(r"\bwhat(?:'?s| is| are\s+you)\s+playing\b", low):
        return True
    if re.search(r"\bwhat\s+(?:song|track|music|movie|film|show|episode|series)\s+is\s+"
                 r"(?:this|that|playing)\b", low):
        return True
    # bare interrogative: "what am i (currently) watching/playing/listening to"
    if re.search(r"\bwhat\s+am\s+i\s+(?:currently\s+)?(?:watching|playing|listening(?:\s+to)?)\b", low):
        return True
    # wrapped request: "do you know / can|could you … what i'm watching/playing/listening"
    if re.search(r"\b(?:do\s+you\s+know|can\s+you|could\s+you|tell\s+me|show\s+me)\b[^?.!\n]{0,40}"
                 r"\bwhat\s+i(?:'m|\s+am)\s+(?:watching|playing|listening(?:\s+to)?)\b", low):
        return True
    if re.search(r"^(?:\w+[,!\s]+){0,2}?(?:look\s+at|check)\s+what\s+i(?:'m|\s+am)\s+(?:watching|playing|listening(?:\s+to)?)\b", low):
        return True
    return False


# "when did I tell you about X", "what day did I drive to X", "the day that I went to X", "the
# timestamps of that interaction": answered from the conversation log, which has every message's
# time. ELI answered from memories whose dates had moved when they were stored again, and said the
# times weren't recorded.
_WHEN_SAID_RE = re.compile(
    r"\b(?:when|what\s+(?:day|date|time))\s+did\s+(?:i|we)\s+(?:first\s+|last\s+|ever\s+)?"
    r"(?:tell|told|mention|say|said|talk|speak|bring|ask|go|went|drive|travel|visit|fly|head)\w*\s+"
    r"(?:(?:you|me|up|about|of|to|from|on|that)\s+)*(?P<topic>[^,.;!?]+)", re.I)
_DAY_I_RE = re.compile(
    r"\bthe\s+(?:\w+\s+)?(?:day|date|time)\s+(?:that\s+|when\s+)?(?:i|we)\s+"
    r"(?:went|drove|travelled|traveled|flew|headed|visited|told|mentioned|said|talked|spoke)\w*\s+"
    r"(?:(?:you|me|about|of|to|from|that)\s+)*(?P<topic>[^,.;!?]+)", re.I)
_TIMESTAMPS_OF_RE = re.compile(
    r"\btime\s?stamps?\s+(?:of|for|on)\s+(?:that|this|the|those|our)\s+"
    r"(?:interaction|conversation|chat|message|talk|exchange)s?\b"
    r"|^(?:\w+[\s,]+){0,3}(?:give|show|tell)\s+me\s+the\s+time\s?stamps?(?:\s+then)?[\s.!?]*$"
    # "when was that timestamp from exactly", "do you have logs", "can i have the full logs from that
    # comment on the 27th": the record of the exchange just had, with its times
    r"|\b(?:when|what\s+(?:time|date|day))\s+(?:was|is|were)\s+(?:that|this|the)\s+"
    r"(?:time\s?stamps?|message|comment|conversation|chat|exchange|log|record)\s+(?:from|of|for)\b"
    # "do you have logs eli?": the record is asked for with nothing else named
    r"|\b(?:do\s+you\s+have|have\s+you\s+got|is\s+there)\s+(?:the\s+|those\s+|any\s+|some\s+|a\s+)?"
    r"(?:full\s+|raw\s+|exact\s+|complete\s+|actual\s+)?(?:logs?|transcripts?|records?|time\s?stamps?)"
    r"(?:\s+(?:of|from|for|on)\s+(?:that|this|it|those))?\s*(?:[,.!?]|$|\beli\b)"
    # "can i have the full logs from that comment on the 27th": the record of a thing said
    r"|\b(?:can\s+i\s+(?:have|see|get)|could\s+i\s+(?:have|see|get)|give\s+me|show\s+me|send\s+me|pull\s+up|"
    r"i\s+(?:want|need)|do\s+you\s+have|have\s+you\s+got)\s+(?:the\s+|those\s+|any\s+|some\s+|me\s+the\s+|a\s+)?"
    r"(?:full\s+|raw\s+|exact\s+|complete\s+|actual\s+)?(?:logs?|transcripts?|records?|time\s?stamps?)\s+"
    r"(?:of|from|for|on)\s+(?:that|this|it|those|(?:that|this|the|our|my)\s+"
    r"(?:comment|message|conversation|chat|exchange|interaction|session|turn|remark|statement|thing\s+i\s+said))\b", re.I)


def _recent_turns_for_search(limit: int = 8) -> List[Dict[str, str]]:
    """The last few messages, for a search that points back at them ("search again", "look it up").
    Read only; nothing when memory cannot be read."""
    try:
        from eli.kernel.request_context import turn_session
        from eli.memory import get_memory
        turns = get_memory().get_recent_conversation(limit=limit, session_id=turn_session()) or []
        return [{"role": str(t.get("role") or ""), "content": str(t.get("content") or "")} for t in turns
                if isinstance(t, dict)]
    except Exception:
        return []


def when_said_topic(text: str) -> Optional[str]:
    """What a "when did I mention X" question asks about: X; "" when it points back at the exchange
    just had ("give me the timestamps of that interaction"); None when it is not that question."""
    t = str(text or "").strip()
    for rx in (_WHEN_SAID_RE, _DAY_I_RE):
        m = rx.search(t)
        if m:
            topic = re.split(r"\s+(?:because|but|since|cause|cos|and then)\b", m.group("topic"), maxsplit=1)[0]
            topic = topic.strip(" '\"")
            if topic:
                return topic
    return "" if _TIMESTAMPS_OF_RE.search(t) else None


def route(text: str, _clause_depth: int = 0) -> Dict[str, Any]:
    """`_clause_depth` is internal: the long-question guard re-routes a trailing
    clause through this same function, and must not recurse further than once."""

    raw, low = _normalize_text(text)
    text_l = low  # compatibility alias for legacy route guards
    _several = _route_several_paths(str(text or ""), low)
    if _several is not None:
        return _several
    _plugin_prepass = _route_plugin_bridge_prepass(raw, low)
    if _plugin_prepass is not None:
        return _plugin_prepass

    # React-to-pasted-content guard: opinion/evaluation requests over quoted or
    # referenced text are conversational. Run before any status/grounding regex
    # so quoted ELI-internal vocabulary can't hijack the turn into a data dump.
    _react_prepass = _eli_react_to_content_prepass(text, raw, low)
    if _react_prepass is not None:
        return _react_prepass


    # Asking ELI to explain something it said isn't a runtime-diagnostics request, even if it
    # quotes words like 'context window' or 'max_tokens'. Ungated, "can you explain what you meant
    # by 'the context window was full of noise'?" got a raw runtime dump. Real diagnostics ("why did
    # you take 20 minutes") still route below.
    _explain_prior_claim = bool(
        re.search(r"\b(can|could|would)\s+you\s+explain\b", low)
        or re.search(r"\bexplain\s+(what\s+you\s+meant|that|this|your\s+(?:last|previous|prior)\s+(?:reply|answer|response|point))\b", low)
        or re.search(r"\bwhat\s+(?:do|did)\s+you\s+mean\b", low)
        or (re.search(r"\byou\s+(?:said|told\s+me|claimed|mentioned|wrote|meant)\b", low)
            and re.search(r"\b(explain|clarify|mean|what)\b", low))
    ) and not re.search(r"\b(audit|diagnos|prove|proof|\bscan\b)\b", low)

    # Primary-contract diagnostics: proof / latency / inference. They need grounded runtime/file
    # evidence and belong in route() itself, not an end-of-file wrapper. A proof challenge aimed at
    # ELI's own reading (_eli_proof_of_reading_challenge) reads the whole utterance, so "prove you
    # read the file" routes here and "read the data in that file and prove it's right" doesn't.
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

    # How long the last reply took is the last-turn rule's (pre-route, ROUTING_FAULT_EXPLAIN, answered
    # from the audit rows). This rule answered "that took over a minute??" with RAM figures, twice.
    # What it keeps: RAM questions, and how to make replies faster (the runtime settings).
    _asks_speed_up = re.search(
        r"\b(response time|slow response|decrease latency|reduce latency|speed (?:it|you|this|replies) up)\b", low)
    _asks_ram = re.search(
        r"\b(how much ram\b|how much memory\b|ram\b.{0,30}\butili[sz]|memory\b.{0,30}\bfor (?:the )?model)\b",
        low) and (_eli_about_eli(raw) or not re.search(r"\bhow much (?:ram|memory)\b", low))
    if not _explain_prior_claim and (_asks_speed_up or _asks_ram) and not re.search(
            r"\b(memories|sqlite|database|faiss|embedder|remember me)\b", low):
        return _mk(
            "EXPLAIN_COGNITION_RUNTIME",
            {
                "question": raw,
                "diagnostic_focus": "inference_ram" if _asks_ram else "inference_runtime",
            },
            0.995,
            matched_by="router.primary_contract_diagnostics.runtime_or_ram",
            need_grounding=True,
            allow_chat_without_evidence=False,
            task_family="grounded_audit",
        )

    if not _explain_prior_claim and re.search(
        r"\b(gguf\s+(?:error|issue|fail|problem)|empty response|returned empty|"
        r"nemotron.*(?:not|fail|error|work|load)|model not (?:load|work)|"
        r"unknown model architecture|why.*nemotron|analyse?.*gguf)\b",
        low,
    ):
        return _mk(
            "EXPLAIN_GGUF_DIAGNOSTICS",
            {"question": raw},
            0.995,
            matched_by="router.primary_contract_diagnostics.gguf_runtime",
            need_grounding=True,
            allow_chat_without_evidence=False,
            task_family="grounded_audit",
        )

    if not _explain_prior_claim and re.search(
        r"\b(?:(?:what was|show|explain)\s+(?:the\s+)?(?:last|most recent|latest)\s+failure|"
        r"why did (?:that|it) fail)\b",
        low,
    ):
        return _mk(
            "EXPLAIN_LAST_FAILURE",
            {"question": raw},
            0.995,
            matched_by="router.primary_contract_diagnostics.last_failure",
            need_grounding=True,
            allow_chat_without_evidence=False,
            task_family="grounded_audit",
        )

    # ELI's own failure log. "what is heart failure?" and "what are common failures of hard drives"
    # name another thing's failures and got the executor's log: the failures asked for are ELI's
    # when the message says so ("your failures", "what failed just now") or names nothing else
    # ("what are the failures", "show me the last 20 failures").
    if not _explain_prior_claim and (
        re.search(r"\b(?:what (?:are|have been|were)|show|list|get)\s+(?:me\s+)?"
                  r"(?:(?:the|all|any|recent|latest|last|most|\d+)\s+)*failures?\s*(?:(?:so far|today|lately|recently|"
                  r"again|please|now)\s*)*[?.!]*$", low)
        or re.search(r"^\s*what(?:'s|\s+has|\s+have)?\s+failed\b", low)
        or re.search(r"\b(?:failure log|executor failures?)\b", low)
        or (_names_eli(raw) and re.search(r"\b(?:failures?|failed|tripping (?:you )?up)\b", low))
    ):
        return _mk(
            "EXPLAIN_FAILURE_LOG",
            {"question": raw, "limit": 50},
            0.995,
            matched_by="router.primary_contract_diagnostics.failure_log",
            need_grounding=True,
            allow_chat_without_evidence=False,
            task_family="grounded_audit",
        )

    # ELI's inference runtime: its own terms, or a common one ("context window", "batch size") said of
    # ELI or of the one in front of us ("the context window", "my batch size").
    _generic_runtime = re.search(r"\b(context window|model slow|gpu layers|batch size)\b", low)
    if not _explain_prior_claim and (
        re.search(r"\b(current inference|inference issues|gguf issues|prompt overflow|context overflow|sacrificing reasoning|"
                  r"sacrifice reasoning|llama slow|gpu_layers|max_tokens|n_ctx|llama_context)\b", low)
        or (_generic_runtime and (_eli_about_eli(raw) or _eli_this_one(_generic_runtime.group(1), low)))
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


    # ── Intent stages (early group) ───────────────────────────────────────────
    # Ordered guards lifted into eli.execution.routing.intents (self-report, runtime/voice,
    # input/agents). First match wins, exactly as the inline chain did; a stage returns None to pass.
    # Change a guard in its stage module, not here.
    for _stage_name, _stage in INTENT_STAGES_EARLY:
        _hit = _stage(text, raw, low, text_l)
        if _hit is ROUTE_NONE:
            return None          # the stage ended routing outright
        if _hit is not None:
            return _hit

    # Persona / active-profile refresh and stale-overlay purge. Backs the offer "purge the stale
    # context and update the active profile" so a "yes" executes it (PERSONA_REFRESH =
    # clean_auto_persona + refresh_user_info). Before the user-info refresh below so those phrasings
    # reach the richer action; "user info" / "profile report" still fall through.
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

    # Known ELI files named in a question ("is your persona.auto.txt up to date? read it", "what's
    # in your settings file") resolve to the real path and SUMMARIZE_FILE. Placed before the
    # long-question guard so the long phrasing isn't swallowed as CHAT, which then confabulated.
    _known_file = _eli_resolve_known_eli_file(low)
    if _known_file and re.search(
        r"\b(read|summari[sz]e|summarise|what'?s?\s+(?:in|inside)|what\s+is\s+in|"
        r"contents?\s+of|show\s+me|tell\s+me\s+what|up[\s-]?to[\s-]?date|"
        r"look\s+at|inspect|check|open)\b",
        low,
    ):
        return _mk("SUMMARIZE_FILE", {"path": _known_file, "instruction": raw},
                   0.9, matched_by="analyze.known_eli_file")

    # Relational / wellbeing / concern questions aimed at ELI go to CHAT, never a status dump
    # (AWARENESS_STATUS, REASONING_MODE_STATUS, META_DIAGNOSTIC); the resolver mis-maps them ("what
    # is happening with you"). Meta-provenance about a prior reply ("is that hardcoded / canned")
    # goes to EXPLAIN_LAST_RESPONSE with real trace data, not a Think-mode stream that blocks the UI.
    if re.search(
        r"\b(?:is|was|are|were)\s+(?:that|this|it|those|the\s+(?:answer|response|reply)|you)\b"
        r"[^?.!]*\b(?:hard[\s-]?coded|deterministic|scripted|canned|pre[\s-]?written|"
        r"a\s+canned\s+response|templated|generated\s+(?:live|on\s+the\s+fly))\b",
        low,
    ):
        return _mk("EXPLAIN_LAST_RESPONSE", {}, 0.95,
                   matched_by="router.explain_last_response.provenance_style",
                   need_grounding=True, allow_chat_without_evidence=False)

    # Fix provenance follow-ups ("how did you fix that file/LOC?") → deterministic
    # patch log via EXAMINE_CODE, not an open-ended reasoning stream.
    if re.search(
        r"\bhow\s+(?:did|do)\s+you\s+fix\b"
        r"|\b(?:which|what)\s+(?:line|loc|lines?)\s+(?:did\s+you|was|were)\s+(?:fix|change|patch|edit)"
        r"|\bhow\s+did\s+you\s+(?:change|patch|edit)\s+(?:that|the|this)\s+file\b"
        r"|\b(?:show|explain|tell\s+me)\s+(?:how|what)\s+you\s+(?:fix|change|patch)\b"
        r"|\b(?:is|was)\s+(?:that|it|the)\s+fix\s+(?:still\s+)?(?:there|gone|applied|in\s+place)\b",
        low,
    ):
        return _mk("EXAMINE_CODE", {"request": raw}, 0.95,
                   matched_by="router.code_fix_recall",
                   allow_chat_without_evidence=False)

    if re.search(
        r"\bare\s+you\s+(?:ok|okay|alright|all\s+right|good|fine|well)\b"
        r"|\bis\s+everything\s+(?:ok|okay|alright|all\s+right|fine)\b"
        r"|\beverything\s+(?:ok|okay|alright|all\s+right)\s*\??$"
        r"|\bwhat'?s?\s+wrong(?:\s+with\s+you)?\b"
        r"|\bwhat(?:'?s?|\s+is)\s+(?:happening|going\s+on|up)\s+with\s+you\b"
        r"|\beli[,\s]+what(?:'?s?|\s+is)\s+(?:happening|going\s+on|wrong)\b"
        r"|\bwhat(?:'?s?|\s+is)\s+(?:happening|going\s+on)\b(?=.*\byou\b)"
        # "what's going on/happening, eli" — vocative anywhere (incl. trailing).
        r"|\bwhat(?:'?s?|\s+is)\s+(?:happening|going\s+on)\b(?=.*\beli\b)",
        low,
    ):
        return _mk("CHAT", {"message": raw}, 0.9, matched_by="chat.relational_concern")

    _said = when_said_topic(raw)
    if _said is not None:
        return _mk("MESSAGE_TIME_QUERY", {"topic": _said, "question": raw}, 0.95,
                   matched_by="memory.when_said", allow_chat_without_evidence=False)
    # "you remember him, Eli?" straight after ELI named who was playing: a bare pronoun points at
    # what ELI itself last named, and the answer is what ELI remembers of them, not a guess.
    if re.match(r"^\W*(?:eli[,!]?\s+)?(?:do\s+you\s+|d'?you\s+|you\s+|can\s+you\s+|still\s+)?"
                r"(?:remember|recall|recognise|recognize|know|heard\s+of|know\s+of)\s+"
                r"(?:him|her|them|it|that\s+(?:one|guy|band|song|place|name|person|artist)|this\s+one|that)"
                r"[\s,]*(?:eli|bud|mate|pal|man)?\W*$", low):
        return _mk("MEMORY_RECALL", {"query": "", "referent": "last_reply"}, 0.92,
                   matched_by="memory.remember_that_one", allow_chat_without_evidence=False)

    # A question about the live media session reads the active session (NOW_PLAYING), not a guess from
    # history — even when wrapped in a longer sentence ("do you know what i am watching now? and we
    # can get to the project later"). This runs BEFORE the long-question guard, which otherwise swallowed the
    # wrapped forms into CHAT: observed live, ELI claimed it was blind while NOW_PLAYING could see the
    # stream the whole time (the bare "what am i watching?" worked, the wrapped ones didn't).
    if _asks_now_playing(low):
        return _mk("NOW_PLAYING", {}, 0.95, matched_by="media.now_playing")

    # Long-question guard: a long rambling question goes to CHAT at 0.85 so the LLM intent resolver
    # never mis-maps it. It shadows ~300 keyword routes, so asking completely must never get a worse
    # answer than asking tersely. The guard measures the whole message but the request lives in a
    # clause, so re-route the last sentence alone and accept it only if it's short and confident.
    if (("?" in raw or "!" in raw) and len(low.split()) >= 12
            and not _is_wallclock_question(low)
            and not _asks_what_is_on_screen(low)
            and not re.match(
            r"^(open|access|initiate|fabricate|check|run|execute|type|press|pause|resume|play|next|previous|stop|mute|unmute|read|list|show|write|add|analyse|analyze|improve|look\s+at\s+(?:my|the)\s+screen)\b", low)):
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

    # "timestamps / dates / when for those articles/stories/news" is a follow-up on the news just
    # shown, not a calendar query (the resolver sent it to a calendar action and got "Calendar
    # integration is not configured"). Keep it on the news context.
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
        r"|\bany\s+news\b"
        r"|\bthe\s+news\b|\bheadlines?\b|\bnews[,\s]*eli\b|\beli[,\s]*news\b", low)
    # "what's happening / what's going on" is ambiguous, in a companion it's usually relational
    # ("what's going on with you"). News only when explicitly world/time-scoped and never when aimed
    # at ELI or the user (you/your/me/here/this/that), or a casual one triggers a 55s news dump.
    _amb = re.search(r"\bwhat(?:'?s?|\s+is)\s+(?:happening|going\s+on)\b", low)
    _world_scope = re.search(
        r"\b(?:in|around|out)\s+(?:the\s+)?world\b|\bout\s+there\b|\bin\s+the\s+news\b"
        r"|\btoday\b|\blately\b|\brecently\b|\bglobally\b|\bworldwide\b|\banywhere\b"
        r"|\baround\s+the\s+globe\b", low)
    _relational = re.search(
        r"\b(?:you|your|yourself|me|my|here|us|this|that|blaming|wrong|"
        r"with\s+you)\b", low)
    _news_ambiguous = bool(_amb and _world_scope and not _relational)
    # "what's new" is the news only as a question about the world. Said, not asked ("that's what's
    # new", "here's what's new"), it tells ELI something; "what's new with you?" asks about ELI or the
    # user. Both went to the news: ELI asked what was new with the user, was shown what was playing,
    # and the reply saying that was what's new got three headlines.
    _whats_new = re.search(r"\bwhat(?:'?s?|\s+is)\s+new\b", low)
    if _whats_new and not _news_explicit and (
            _relational or re.search(
                r"\b(?:that|this|it|here|there|which)(?:'?s|\s+is|\s+was|\s+are)\s+what(?:'?s?|\s+is)\s+new\b"
                r"|\b(?:guess|know|see|show|tell)\s+(?:you\s+|me\s+)?what(?:'?s?|\s+is)\s+new\b", low)):
        _whats_new = None
    _news_explicit = _news_explicit or _whats_new
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

    if re.search(
        r"\b(self help|repair playbook|maintenance playbook|fix playbook|"
        r"how do i fix eli|which self command|self maintenance help|"
        r"how should i fix|what self command)\b",
        low,
    ):
        return _mk("SELF_REPAIR_PLAYBOOK", {"question": raw}, 0.99, matched_by="self.repair_playbook")

    if re.search(r"\b(generate|create)\s+patch\b|\bpatch\s+(eli|system)\b", low):
        return _mk("SELF_IMPROVE", {"mode": "propose", "_raw_user_text": raw}, 0.93, matched_by="self.patch_propose")

    # The owner's yes or no to the fix candidates ELI proposed (self_improvement.waiting_candidates):
    # "approve candidate 12", "decline candidates 3 and 4", "apply that fix", "apply the patch".
    # "apply a nicotine patch" names no fix of ELI's.
    _cand = re.match(r"^(?:please\s+|yes,?\s+|ok(?:ay)?,?\s+)?(approve|apply|accept|decline|reject|discard)\s+"
                     r"(?:the\s+)?(?:fix\s+)?candidates?\s+(\d+(?:\s*(?:,|and|&)?\s*\d+)*)\s*[.!]?$", low)
    if _cand:
        _decline = _cand.group(1) in ("decline", "reject", "discard")
        return _mk("APPROVE_CANDIDATE", {"candidate_ids": [int(x) for x in re.findall(r"\d+", _cand.group(2))],
                                         "decision": "decline" if _decline else "approve"},
                   0.97, matched_by="self.candidate_by_number")
    _cand = re.match(r"^(?:please\s+|yes,?\s+|ok(?:ay)?,?\s+)?(approve|apply|accept|decline|reject|discard)\s+"
                     r"(?:(?:the|that|this|those|these|your|its)\s+)?(?:self[- ]?improvement\s+|own\s+|proposed\s+)?"
                     r"(?:fix(?:es)?|patch(?:es)?|candidates?)\s*[.!]?$", low)
    if _cand:
        _decline = _cand.group(1) in ("decline", "reject", "discard")
        return _mk("APPROVE_CANDIDATE", {"decision": "decline" if _decline else "approve"},
                   0.95, matched_by="self.candidate_in_hand")

    # ELI's own patch: "apply a nicotine patch" is not one. It proposes; the yes is APPROVE_CANDIDATE.
    if re.search(r"\bpatch\s+yourself\b|\bself.?patch\b|\bfix\s+your\s+own\s+code\b", low):
        return _mk("SELF_PATCH", {}, 0.95, matched_by="self.patch_cycle")

    # User file fix — must beat the generic "fix the bugs/errors" self-patch guard.
    m = re.search(
        r"\b(fix|debug|repair|correct|patch)\s+(?:the\s+)?(?:bugs?|errors?|issues?)\s+(?:in\s+)?([\w./~-]+\.py)\b",
        raw,
        re.I,
    )
    if m:
        path = m.group(2).strip()
        return _mk("FIX_FILE", {"path": path}, 0.98,
                   matched_by="file.fix_python_early", entities={"path": path})

    if re.search(r"\bself[- ]?fix\w*\b|\bfix\s+yourself\b", low):
        return _mk("SELF_PATCH", {}, 0.96, matched_by="self.fix")

    # ELI's own code: the errors it just listed ("fix those issues"), or its code named as its own.
    # "can you fix the errors in this paragraph" and "please fix the issues with my cv" are the
    # user's text, and ran a patch cycle on ELI's source.
    if re.search(
        r"\bfix\s+(?:all\s+)?(?:those|the|these|them)\s+(?:errors?|issues?|bugs?|findings?|problems?)"
        r"\s*(?:(?:for me|please|now|then|too|as well|again)\s*)*[.!?]*$"
        r"|\bfix\s+(?:your|eli'?s)\s+(?:own\s+)?(?:code|errors?|issues?|bugs?|source)\b",
        low,
    ) or (_names_eli(raw) and re.search(
            r"\b(?:can you|could you|please)\s+fix\b.{0,50}\b(?:code|errors?|issues?|bugs?)\b", low)):
        return _mk("SELF_PATCH", {}, 0.97, matched_by="self.fix_errors_or_code")

    # ── SELF_TEST (before generic 'test' routes) ──────────────────────────────
    if re.search(r"\b(run\s+)?self.?test\b|\btest\s+yourself\b|\brun\s+tests?\b", low):
        return _mk("SELF_TEST", {}, 0.94, matched_by="self.test")

        # ── Memory search (must be before web search to prevent mis-routing) ─
    # Bare forms: 'search your memory', 'search memory', 'check memory'
    _bare_mem = re.match(
        r'^(?:search|check|look\s+in|query)\s+(?:your\s+)?memor(?:y|ies)$', low
    )
    if _bare_mem:
        return _mk('MEMORY_RECALL', {'query': 'recent conversations'},
                   0.95, matched_by='memory.search_bare')
    # A recap of this conversation's last few messages is read from the conversation itself, every
    # reply whole (session_continuity), not searched for: "check the last, say 7-8 messages, summarise
    # what has been said" went to the intent model (90 s) and came back as a memory dump from August.
    try:
        from eli.cognition.correction_patterns import refers_to_earlier_reply as _rter
        if _rter(text) and re.search(
                r"\b(?:summar\w*|recap\w*|break\s*down|go\s+over|run\s+through|"
                r"what\s+(?:has|have|had|was|were)?\s*(?:been\s+)?(?:said|happening|going\s+on|discussed))\b", low):
            return _mk("CHAT", {"message": text}, 0.95, matched_by="conversation.recap")
    except Exception:
        _SWLOG.debug("suppressed exception", exc_info=True)

    # Conversation recall — "do you remember what we were talking about last week?"
    if re.search(
        r"\b(?:do you|can you|did you)\s+remember\b",
        low,
    ) or re.search(
        r"\bwhat\s+(?:were|was)\s+we\s+(?:talking|discussing|chatting)\s+about\b",
        low,
    ) or re.search(
        r"\bremember\s+what\s+we\s+(?:were\s+)?(?:talking|discussing)\b",
        low,
    ):
        # A named period ("last week", "yesterday", "on thursday") goes to chat, like
        # memory.temporal_recall_as_chat: that path reads the period's turns and actions.
        try:
            from eli.cognition.query_planner import parse_window as _pw_rc
            if _pw_rc(text):
                return _mk("CHAT", {"message": text}, 0.98, matched_by="memory.temporal_recall_as_chat")
        except Exception:
            _SWLOG.debug("suppressed exception", exc_info=True)
        return _mk(
            "MEMORY_RECALL",
            {"query": text.strip()},
            0.93,
            matched_by="memory.recall_conversation",
        )
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
    # MCP runtime — what the configured servers expose right now.
    if re.search(r"\b(?:mcp|external\s+tool)\s+(?:server\s+)?status\b", low) or \
       re.search(r"\bwhat\s+mcp\s+servers?\b", low):
        return _mk("MCP_STATUS", {}, 0.95, matched_by="mcp.status")
    _mcpm = re.search(r"\b(?:run|call|use|invoke)\s+(?:the\s+)?mcp\s+tool\s+(?P<t>[\w.\-]+)", low)
    if _mcpm:
        return _mk("MCP_CALL", {"tool": _mcpm.group("t")}, 0.94, matched_by="mcp.call")
    if re.search(r"\b(?:list|show|what)\s+(?:are\s+)?(?:my\s+|the\s+)?mcp\s+tools?\b", low) or \
       re.search(r"\bmcp\s+tools?\b", low):
        return _mk("MCP_TOOLS", {}, 0.94, matched_by="mcp.tools")

    # MCP servers are a separate process ELI configures, not code it loads. Matched before the
    # generic plugin patterns and ordered verb-first, because "remove the mcp server X" contains the
    # literal "mcp server" a list pattern would claim.
    _mcp_rm = re.search(r"\b(?:remove|uninstall|delete|drop)\s+(?:the\s+)?"
                        r"mcp(?:\s+server)?\s+['\"]?([a-z0-9._@/-]+)", low)
    if _mcp_rm:
        sid = _mcp_rm.group(1)
        return _mk("MCP_REMOVE", {"server": sid}, 0.96, matched_by="mcp.remove",
                   entities={"server": sid}, task_family="plugin")
    _mcp_add = re.search(r"\b(?:add|install|set\s*up|connect)\s+(?:an?\s+|the\s+)?"
                         r"mcp(?:\s+server)?(?:\s+['\"]?([a-z0-9._@/-]+))?", low)
    if _mcp_add:
        name = (_mcp_add.group(1) or "").strip()
        return _mk("MCP_ADD", {"server": name}, 0.96, matched_by="mcp.add",
                   entities={"server": name}, task_family="plugin")
    if re.search(r"\bmcp\b", low) and re.search(
            r"\b(?:doctor|diagnos\w*|not\s+work\w*|broken|failing|check|test|"
            r"trouble\w*|debug)\b", low):
        return _mk("MCP_DOCTOR", {}, 0.96, matched_by="mcp.doctor", task_family="plugin")
    if re.search(r"\b(?:list|show|what)\b.*\bmcp\b", low) \
            or re.search(r"\bmcp\s+(?:status|servers?)\b", low):
        return _mk("MCP_LIST", {}, 0.95, matched_by="mcp.list", task_family="plugin")
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
    # "run the audit", "yes start the audit please", "run_audit", "audit yourself": the same audit. Told
    # to type "run_audit", the user did, and ELI said it was a text token, not a trigger.
    if re.search(
            r"\b(?:run|do|perform|execute|start|begin|launch|kick\s+off|go\s+ahead\s+with|carry\s+out)"
            r"(?:\s+(?:a|an|the|your|that|this|my|full|quick|minimal|same|another|proper|real))*\s+"
            r"(?:runtime\s+audit|system\s+audit|health\s+check|diagnostic|diagnostics|audit|self[- ]?check|self[- ]?audit)\b"
            r"|^\W*(?:please\s+)?(?:run_audit|runtime_audit|self_audit|audit_now|audit)\W*$"
            r"|\baudit\s+(?:yourself|your\s*self|your\s+(?:own\s+)?(?:code|codebase|runtime|system|pipeline|agents?))\b", low):
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
    # How the memory is doing gets the short health report. "and the memory, how is that doing?" went
    # to the model's intent guess, which ran a recall of "memory status" and dumped eighteen messages.
    if re.search(r"\bhow(?:'s|\s+is|\s+are)\s+(?:your|the|ur)\s+memor(?:y|ies)\b(?!\s+(?:of|about|for|on)\b)"
                 r"|\bmemor(?:y|ies)\W{0,4}how(?:'s|\s+is|\s+are)\s+(?:that|it|they)\s+(?:doing|holding|working|going|getting|coming)\b"
                 r"|\bis\s+(?:your|the)\s+memory\s+(?:ok|okay|fine|good|working|back|alright|holding)\b", low):
        return _mk("MEMORY_STATUS", {"memory_scope": "health", "question": raw}, 0.97, matched_by="memory.health")
    # "how many memories" is ELI's count only asked of ELI: a brain's memories are not (_eli_about_eli).
    if (re.search(r"memory\s*(status|stats|report|info|summary|check|usage|count|size|total)|memories (do you have|count|total)", low)
            or (re.search(r"how many memories", low) and _eli_about_eli(raw))):
        try:
            from eli.runtime.inference_footprint import is_inference_ram_question
            if is_inference_ram_question(raw):
                return _mk(
                    "EXPLAIN_COGNITION_RUNTIME",
                    {"question": raw, "diagnostic_focus": "inference_ram"},
                    0.995,
                    matched_by="router.inference_ram_not_memory_status",
                    need_grounding=True,
                    allow_chat_without_evidence=False,
                    task_family="grounded_audit",
                )
        except Exception:
            log.debug("inference RAM guard unavailable", exc_info=True)
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

        # Legacy-compat media intents, before generic MEDIA_CONTROL, to keep test compatibility and
        # deterministic semantics. "what's playing?" reports the current track/source, matched
        # before the play matchers so it's never taken for a play command.
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

    # 0) Hard compatibility / regression contracts. Tests may expect STOP_MEDIA exactly for "stop
    # media".
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

    # Bare greetings ("morning", "good morning") are conversation, never a report command. Must be a
    # whole-utterance greeting: "morning report" and "good morning, give me the report" fall through
    # to the report rule below.
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

    if any(p in low for p in ["run patch cycle", "patch yourself", "fix your own code", "self-patch", "self fix"]):
        return _mk("SELF_PATCH", {}, 0.95, matched_by="self.patch")

    # "improve / enhance / refactor / optimise / clean up <a named .py file>" goes to the verified
    # CodeAgent on that file (the FIX_FILE path) in improve mode, not ELI's own self-improvement. A
    # bare "improve yourself / your code" (no .py path) still reaches SELF_IMPROVE.
    _imp_file = re.search(
        r"\b(?:improve|enhance|refactor|optimi[sz]e|clean\s*up|tidy(?:\s*up)?|make\s+better)\b"
        r".{0,40}?([\w./~ -]+\.py)\b", raw, re.I)
    if _imp_file:
        _imp_path = _imp_file.group(1).strip()
        return _mk("FIX_FILE", {"path": _imp_path, "mode": "improve"}, 0.92,
                   matched_by="file.improve_python", entities={"path": _imp_path})

    if "improve" in low and (
            "yourself" in low or "self" in low or "code" in low):
        return _mk("SELF_IMPROVE", {"_raw_user_text": raw}, 0.9, matched_by="self.improve")

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
        # "the <bin> command [on/with/...] <tail>": "command" is filler and <bin> is the real
        # binary. Without this, "run the rm command on it" ran `rm command on it`.
        _idiom = re.match(r'^([a-zA-Z][\w\-]*)\s+command\b\s*(.*)$', _cmd, re.I)
        if _idiom:
            _cmd = (_idiom.group(1) + " " + _idiom.group(2)).strip()
        _parts = _cmd.split()
        _first = _parts[0].lower() if _parts else ""
        _rest = _parts[1:]
        # Destructive binaries need a concrete target. If the only arguments are deictic or
        # natural-language fillers ("on it", "that"), refuse and fall through to a clarifying chat
        # rather than run rm/mv/dd against garbage.
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
    # Background-job inspection: 'job 3', 'job #3', 'job no. 3', the STT mangle 'ob #3', and
    # status/ping phrasings ("status of job #3", "ping me when job #3 is done"). The old regex needed
    # 'job'+space+digits, so status queries went to SET_ALARM/LIST_DIR/CHAT and the model made things
    # up. CHECK_JOB reads the registry authoritatively.
    _JOB_REF = r"\bj?ob\s*#?\s*(?:no\.?\s*|number\s*)?(\d+)\b"
    if re.search(_JOB_REF, low):
        # Don't hijack a compound utterance whose main action is something else ("list the files in
        # ~/Documents. Also, what is the status of job #3?" leads with 'list'). Claim it only when it's
        # about the job: a job-intent word (or a short bare "job 3") and no different leading verb.
        _starts_other = re.match(
            r"^\s*(?:please\s+|could\s+you\s+|can\s+you\s+|hey[, ]+|eli[, ]+)*"
            r"(?:list|ls|open|launch|start|play|pause|close|quit|kill|create|make|write|"
            r"save|read|delete|move|rename|copy|set|search|find|analyse|analyze|"
            r"summari[sz]e|email|message|screenshot|take)\b", low)
        _job_intent = bool(re.search(
            r"\b(?:check|status|how'?s|view|ping|tell|notify|let\s+me\s+know|done|complete|"
            r"completed|ready|finished|finish|running|results?|summary)\b", low))
        _short = len(low.split()) <= 6
        if (_job_intent or _short) and not _starts_other:
            _jm = re.search(_JOB_REF, low)
            _notify = bool(
                re.search(r"\b(?:ping|tell|notify|let\s+me\s+know|message|alert)\b", low)
                or re.search(r"\bwhen\b.*\b(?:done|complete|completed|ready|finished|finish)\b", low))
            return _mk("CHECK_JOB", {"job_id": _jm.group(1) if _jm else None,
                                     "notify": _notify, "text": raw},
                       0.95, matched_by="dev.check_job")
    if re.search(r"\b(?:background\s+jobs|(?:list|check)\s+(?:the\s+|my\s+|on\s+)?(?:background\s+|running\s+)?jobs|"
                 r"running\s+jobs|what\s+jobs)\b", low):
        return _mk("BACKGROUND_JOBS", {}, 0.9, matched_by="dev.background_jobs")
    # "do you have an eta?" while a job runs: the job's own progress, not a guess ("several hours" for a
    # ten-minute read of a few hundred PDFs, after a detour through the intent model)
    if _JOB_PROGRESS.search(low):
        from eli.runtime.background_tasks import get_background_tasks as _bt_running
        if _bt_running().list(limit=1, status="running"):
            return _mk("CHECK_JOB", {"job_id": None, "text": raw}, 0.9, matched_by="dev.running_job_progress")
    # a bare "results?" / "the results" asks for the job in hand (the latest, within the hours a job
    # counts as work in hand), not a web search for the word
    if re.fullmatch(r"\W*(?:(?:and|so|any|the|my)\s+)*results?\W*", low):
        from eli.runtime.background_tasks import job_in_hand
        _in_hand = job_in_hand()
        if _in_hand:
            return _mk("CHECK_JOB", {"job_id": _in_hand["id"], "text": raw}, 0.9, matched_by="dev.job_results_in_hand")

    # ── Scheduled-task control ────────────────────────────────────────────────
    # Chat had no way to see or change the scheduler: the triggers above demand the
    # literal word "jobs", so "delete background TASK for 02:00 on Sunday" matched
    # nothing, fell through to CHAT, and the model asserted there were no scheduled
    # tasks while four were armed. These claim the "task" phrasings authoritatively.
    _TASK_WORD = r"(?:task|tasks|scheduled\s+task|background\s+task|scheduled\s+job)"
    # What may stand between the verb and the task it acts on: the words that name the task
    # ("the 2am research", "background", "#12"), not another clause. Any forty characters used to
    # do, and "you need to stop lying to me and giving false task updates" cancelled a task.
    _ITS_TASK = r"(?:\s+(?!(?:to|me|you|u|i|we|and|or|but|is|are|was|were|it|that)\b)[\w#:'.-]+){0,5}?\s+"

    if re.search(r"\b(?:cancel|delete|remove|kill|drop|clear|stop)\b" + _ITS_TASK + _TASK_WORD + r"\b", low):
        _m_id = re.search(r"\b(?:task|job)\s*#?\s*(\d{1,6})\b", low)
        return _mk("CANCEL_TASK",
                   {"job_id": _m_id.group(1) if _m_id else None, "text": raw},
                   0.95, matched_by="tasks.cancel")

    if re.search(r"\b(?:reschedule|re-schedule|move|push|shift|change)\b" + _ITS_TASK + _TASK_WORD + r"\b", low):
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
        # the language from the one reader of it (portable_intent_contract.infer_script_language): a bare
        # word list read "go through" as Go
        from eli.execution.portable_intent_contract import infer_script_language
        _cs_lang = infer_script_language(raw)
        _cs_lang = "python" if _cs_lang in ("", "auto") else _cs_lang
        return _mk("CODE_SOLVE", {"description": raw, "language": _cs_lang}, 0.95,
                   matched_by="dev.code_solve")

    if not _GENERATION_COMPLAINT and (any(p in low for p in code_triggers) or _CODE_SCRIPT_RE.search(raw)):
        return _mk("GENERATE_SCRIPT", {"description": raw, "use_gguf_only": True,
                   "forbid_ollama": True}, 0.95, matched_by="dev.generate_script")

    if re.search(
            r"(show|open|view|display)\s+.{0,20}?(diff|changes?|what.{0,10}fixed)", low):
        # "diff a.py and b.py": the files named are what to compare
        _diff_paths = [m.group(0) for m in re.finditer(r"(?<![\w:/])(?:~|\.{1,2})?/?[\w./-]*[\w-]\.[A-Za-z0-9]{1,8}\b", raw)
                       if not m.group(0).lower().startswith("www.")]
        return _mk("SHOW_DIFF", {"paths": _diff_paths[:2]} if len(_diff_paths) >= 2 else {}, 0.9,
                   matched_by="dev.show_diff")

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

    m = re.match(r"^confirm\s+forget\s+documents?\s+([\d\s,]+)$", raw.strip(), re.I)
    if m:
        return _mk("MEMORY_FORGET", {"document_ids": [int(x) for x in re.findall(r"\d+", m.group(1))], "confirm": True},
                   1.0, matched_by="memory.forget_document_confirm")
    m = re.match(r"^confirm\s+forget\s+memor(?:y|ies)\s+([\d\s,]+)$", raw.strip(), re.I)
    if m:
        ids = [int(x) for x in re.findall(r"\d+", m.group(1))]
        return _mk("MEMORY_FORGET", {"ids": ids, "confirm": True}, 1.0, matched_by="memory.forget_confirm")
    m = re.match(
        r"^(?:please\s+)?(?:forget\s+(?:about\s+|that\s+|what\s+i\s+(?:said|told\s+you)\s+about\s+|everything\s+about\s+)?"
        r"|(?:delete|erase|remove)\s+(?:the\s+)?(?:memory|memories)\s+(?:about|of|that)\s+"
        r"|erase\s+what\s+you\s+know\s+about\s+)(.{4,200}?)[.!]?$", raw.strip(), re.I)
    if m and not re.match(r"^(?:(?:it|this|that|me)$|(?:everything|all)\b)", m.group(1).strip(), re.I):
        return _mk("MEMORY_FORGET", {"query": m.group(1).strip()}, 0.97, matched_by="memory.forget_request")

    # "Please store this fact in memory: X. Then confirm ..." stores X; the trailing request is not part of it.
    m = re.match(
        r"^(?:please\s+)?(?:store|save|record|remember|keep)\s+(?:this|the following|that)?\s*(?:fact|information|detail|note)?\s*"
        r"(?:in|to|into|inside)\s+(?:your\s+)?(?:long[- ]term\s+)?memory\s*[:\-]\s*(.+?)"
        r"(?:\.\s+(?:then|and|also)\s+(?:confirm|tell|say|report|let|reply|answer)\b.*)?$",
        raw, re.I | re.S)
    if m and m.group(1).strip():
        text_to_store = m.group(1).strip().rstrip(".")
        return _mk("MEMORY_STORE", {"text": text_to_store, "tags": ["user_memory", "remembered"]}, 1.0,
                   matched_by="memory.store_in_memory_phrase", entities={"text": text_to_store})

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

    # 4) Media control, specific to generic. Specific next/previous track controls
    # (legacy-friendly).
    if re.search(r"\b(next|skip)\s+(?:the\s+)?(?:song|track)\b", low):
        return _mk("NEXT_MEDIA", {}, 0.95,
                   matched_by="media.next_track_specific")
    if re.search(r"\b(previous|back)\s+(?:the\s+)?(?:song|track)\b", low):
        return _mk("PREVIOUS_MEDIA", {}, 0.95,
                   matched_by="media.prev_track_specific")

    # App-targeted media commands: a transport verb said to an app ("pause spotify", "skip it on
    # youtube"). "play <X> playlist spotify", "no, play <X> on youtube" and "play <X> in youtube"
    # were read as "resume Spotify/YouTube" and <X> was dropped: a "play" with words of its own asks
    # for them on that app. (Any other verb is the transport whatever else is said; whether the
    # message tells ELI to do it is the fit test's.)
    from eli.runtime.command_splitter import without_correction_lead as _wcl
    _told_low = _wcl(low).lower()
    for app in MEDIA_APPS:
        if re.search(rf"\b{re.escape(app)}\b", _told_low):
            cmd = _canonical_media_command(_told_low)
            _own = _media_words_beyond(_told_low, app)
            if cmd == "play" and _own:
                _q = re.sub(r"^.*?\b(?:play|resume)\b\s*", "", _told_low, count=1)
                _q = re.sub(rf"(?:[\s,]+(?:on|in|at|from|using|with|via|through))?[\s,]+(?:the\s+)?"
                            rf"{re.escape(app)}(?:\s+(?:app|website|site|player))?\b", " ", f" {_q}")
                _q = re.sub(r"\s+", " ", _without_courtesy(_q)).strip(" ,.!?")
                if not _q:
                    break
                return _mk("PLAY_MEDIA", {"query": _q, "target": app}, 0.93,
                           matched_by="media.app_targeted_query", entities={"query": _q, "target": app})
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
        (r"\bminimi[sz]e\s+(?:this|the|current)\s+window\b|"
         r"\bminimi[sz]e\s+this\b|\bminimi[sz]e\s+the\s+window\b",
         "MINIMIZE_APP"),
        (r"\bmaximi[sz]e\s+(?:current|this|the\s+window|window)\b|"
         r"\bfullscreen(?:\s+(?:current|this|the\s+window|window))?\s*$|"
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
    # "go to the futurama tab" is the same request ("go to" alone belongs to addresses, below).
    m = m or re.match(r"^(?:go|change|move)\s+to\s+(.+?\s+tab(?:\s+(?:in|on|of)\s+.+?)?)\s*$", low)
    if m:
        from eli.execution.portable_intent_contract import window_target
        _to = window_target(m.group(1).strip().rstrip("?.!,;:"))
        app_name = _to["name"]
        if app_name and len(app_name.split()) <= 3:
            return _mk("FOCUS_APP", _to, 0.9,
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

    # Browser/video media: whole-word match only. "tab" fired inside "me-tab-olise" and with "next"
    # from "the next log lands" sent a pasted paragraph to a browser next-track command. The
    # "bro"/"brows" fragments went for the same reason. Also gate on shape: a real media control is
    # terse, so anything longer than a short phrase isn't a bare "next/pause/play".
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
        "top": ("STOP_MEDIA", 0.82),
        "next": ("NEXT_MEDIA", 0.88),
        "next song": ("NEXT_MEDIA", 0.88),
        "next track": ("NEXT_MEDIA", 0.88),
        "previous": ("PREVIOUS_MEDIA", 0.88),
        "prev": ("PREVIOUS_MEDIA", 0.88),
        "previous song": ("PREVIOUS_MEDIA", 0.88),
        "previous track": ("PREVIOUS_MEDIA", 0.88),
        "shuffle": ("SHUFFLE_MEDIA", 0.9),
        "repeat": ("REPEAT_MEDIA", 0.9),
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
    # the service or site follows "on", or "in" / "at" / "from" when it names one ("play X in
    # youtube", "play X at example.ch"; "play songs from the eighties" keeps its words)
    _play_specific_m = re.match(
        r"^play\s+(?:me\s+|my\s+|some\s+|the\s+)?(.+?)(?:\s+on\s+(.+))?$", raw, re.I
    )
    _play_from_m = re.match(r"^play\s+(?:me\s+|my\s+|some\s+|the\s+)?(.+?)\s+(?:in|at|from)\s+(.+)$", raw, re.I)
    if _play_from_m and not (_play_specific_m and _play_specific_m.group(2)):
        from eli.execution.portable_intent_contract import _known_media_service
        if _known_media_service(_play_from_m.group(2)):
            _play_specific_m = _play_from_m
    if _play_specific_m:
        query = _play_specific_m.group(1).strip()
        target = (_play_specific_m.group(2) or "").strip() or None
        browser = False
        if target:
            browser = bool(re.search(
                r"\.com|\s+com\b|\bwebsite\b|\bweb(?:site)?\b", target, re.I
            ))
            if re.search(r"youtube\s*(?:\.com|\s+com|website|web)\b", target, re.I):
                target = "youtube website"
                browser = True
            else:
                try:
                    from eli.execution.media_runtime import normalize_streaming_target
                    _canon = normalize_streaming_target(target)
                    if _canon:
                        target = _canon
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
        # Only route as specific if it's not a bare "play" or "play media"
        if query and query.lower() not in ("media", "music", "audio", ""):
            _args = {"query": query, "target": target}
            if browser:
                _args["browser"] = True
            from eli.execution.portable_intent_contract import media_kind
            if media_kind(query):
                _args["kind"] = media_kind(query)
            return _mk("PLAY_MEDIA", _args, 0.92,
                       matched_by="media.play_specific",
                       entities={"query": query, "target": target})

    # "shuffle" toggles; "shuffle on" / "shuffle off" (and "turn shuffle off") ask for that state, and
    # carry it as "state" ("on"/"off"), the argument the shuffle action sets it from
    # (effectors/media.shuffle_media). Both were a toggle, so "shuffle off" said with shuffle already
    # off turned it on; then the router said "mode" and the action read "state", so it still toggled.
    _shuffle = (re.match(r"^shuffle(?:\s+(on|off|music))?\b", low)
                or re.match(r"^turn\s+(?:the\s+)?shuffle\s+(on|off)\b|^turn\s+(on|off)\s+(?:the\s+)?shuffle\b", low))
    if _shuffle:
        _state = next((g for g in _shuffle.groups() if g in ("on", "off")), None)
        return _mk("SHUFFLE_MEDIA", {"state": _state} if _state else {}, 0.95, matched_by="media.shuffle",
                   entities={"state": _state} if _state else None)

    # "repeat" / "repeat track" / "loop" / "repeat all"
    if re.match(r"^(?:repeat|loop)(?:\s+(?:one|track|all|playlist|off))?\b", low):
        return _mk("REPEAT_MEDIA", {}, 0.95, matched_by="media.repeat")

    # 5) Time / date / capabilities. Before the current-time route: "what time did I first/last
    # send a message today" and "when did we start talking today" ask about the conversation log, not
    # the clock. They used to return the current time. Route to a grounded query on conversation_turns.
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
    # The conversational/file pre-guards are computed here so the TIME route gets them too. They sat
    # below it and guarded DATE only, so "...i have asked you what the time is quite a bit lately"
    # answered with a bare timestamp.
    _date_conv_starters = frozenset({
        "yeah", "yes", "yep", "yup", "no", "nope", "ok", "okay",
        "so", "well", "fair", "right", "sure", "true", "exactly",
        "indeed", "agreed", "alright", "fine", "cool",
    })
    # Only habitual/reportive framings ("i have asked ... quite a bit lately") mean the sentence is
    # about having asked. Bare "i said" / "i asked" / "i told" is how an exasperated user restates an
    # unanswered request ("i said the day AND time"). "asked you what" still covers "i asked you what
    # the date is".
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

    _wallclock_meta = _is_wallclock_meta_question(low)

    # "what time is it and how long until the presentation?" wants more than the clock: answered
    # as TIME it got "09:05" and nothing else.
    from eli.cognition.query_planner import asks_date_arithmetic as _asks_time_sum
    if (not _is_date_conv and not _file_command and not _wallclock_meta
            and not _asks_time_sum(low)
            and any(re.search(p, low) for p in _WALLCLOCK_TIME_PATTERNS)):
        # original_query carries the phrasing the TIME effector needs to tell
        # "the time" from "the day and the time", and to spot a named place.
        return _mk("TIME", {"original_query": text}, 1.0, matched_by="system.time")

    # CREATE_FILE: "create/make/write/save a file called PATH containing CONTENT [, then read it
    # back]". Needs the word 'file' and a path with an extension so it can't steal "write a
    # function/script". Before DATE so "...containing today's date..." doesn't return a bare date.
    if re.search(r"\b(?:create|make|write|save|generate|overwrite|replace)\s+(?:a\s+|an\s+|the\s+|new\s+)*[a-z]*\s*file\b", low):
        _cf_path_m = (
            re.search(r"(?:called|named|at|to|in)\s+[\"']?([~/]?[\w./\-]+\.[A-Za-z0-9]{1,8})", raw)
            or re.search(r"([~/][\w./\-]*\.[A-Za-z0-9]{1,8})", raw)
            or re.search(r"\b([\w.\-]+\.[A-Za-z0-9]{1,6})\b", raw)
        )
        if _cf_path_m:
            _cf_path = _cf_path_m.group(1)
            # Honour an explicit target directory ("...notes.txt in the ~/Documents directory"). The
            # path alternatives above capture only a filename, so the requested directory was
            # dropped and the file landed in artifacts/scratch.
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
            _cf_args = {"path": _cf_path, "content": _cf_content}
            # an existing file is replaced only when the request says so
            if re.search(r"\b(?:overwrite|overwriting|replace|replacing)\b", low):
                _cf_args["overwrite"] = True
            return _mk("CREATE_FILE", _cf_args, 0.96, matched_by="fs.create_file")

    # "What date is it, and how many days ago was 09-09-2026?" asks for arithmetic as well; DATE
    # answered only the date. Chat answers both, with the days worked out for it (date facts).
    from eli.cognition.query_planner import asks_date_arithmetic
    _date_and_more = asks_date_arithmetic(low)
    if (not _is_date_conv and not _file_command and not _wallclock_meta and not _date_and_more
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
        r"\b(?:(?:set|create)\s+(?:an?\s+)?)?(?:alarm|reminder)\s+(?:for\s+)?(\d{1,2}[:.]\d{2})\b",
        low)
    if m:
        # "7:30pm" is 19:30: the clock reader keeps the am or pm the rule's own pattern passes over
        from eli.execution.routing.command_parser import clock_time
        alarm_time = clock_time(low) or m.group(1).replace(".", ":")
        return _mk("SET_ALARM", {"time": alarm_time}, 0.98,
                   matched_by="timer.set_alarm", entities={"time": alarm_time})

    m = re.search(
        # "an": "set an alarm for 30 minutes" matched nothing and went to chat
        r"\b(?:set|create)\s+(?:an?\s+)?(?:alarm|timer|reminder)\b",
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
        # Stop at a sentence/clause boundary so a trailing question isn't swallowed into the path
        # ("...in ~/Documents. Also, what is the status of job #3?"). Split only on sentence terminators
        # and clear clause joiners, never a bare "and" ("documents and settings" survives).
        _raw_path = re.split(r"\s*[.?!]\s+|,?\s+(?:also|and then|then)\b",
                             _raw_path, maxsplit=1)[0].strip().rstrip(".,;:")
        path = _expand_common_dir(_raw_path)
        return _mk("LIST_DIR", {
                   "path": path}, 0.95, matched_by="fs.list_dir_explicit", entities={"path": path})

    # Bare "list/show <path>" without a files/contents/directory keyword ("show ~/Downloads").
    # Without it the resolver emits the unsupported 'LIST_FILE', which falls to CHAT and the model
    # invents the contents. Needs an explicit path token (/, ~, ./, ../) so it never matches prose.
    m_bare = re.search(
        r"\b(?:list|ls|show|read)\b(?:\s+(?:or|and)\s+\w+)?\s+(?:me\s+|out\s+)?(?:the\s+)?"
        r"((?:/|~/|\./|\.\./)[^\s,?!]+)",
        raw, re.I)
    if m_bare:
        path = _expand_common_dir(m_bare.group(1).strip())
        # "read/show <file>" is a file op, not a directory listing. When the token is a file (has an
        # extension, or exists as one) route to READ_FILE; otherwise a conversation .json/.docx went to
        # LIST_DIR, errored "Not a directory" and was replayed as a recurring failure. Dirs still LIST_DIR.
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

    # Home only: a conjunction means more targets follow ("open home directory AND the downloads
    # directory") and this route carries one path, so it opened home, reported success and dropped
    # the rest. Defer those to the open/launch block, which splits them into a sequence.
    if (re.match(
            r"^(?:open|launch|start|show)\s+(?:the\s+)?(?:home|home\s+(?:folder|directory)|file\s+manager|files?)\b", low)
            and not re.search(r"\band\b", low)):
        return _mk("OPEN_FILE_SYSTEM", {
                   "path": "~"}, 0.98, matched_by="fs.open_home")

    if re.search(r"\b(?:access|open|launch)\s+storage\s+matrix\b", raw, re.I):
        return _mk("OPEN_FILE_SYSTEM", {"path": "~"},
                   1.0, matched_by="alias.storage_matrix")


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
    _BROWSER_SITES = WEBSITE_ALIASES  # the one table (eli/execution/app_aliases.py)
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
        # The address is what was given, a site in the table, or one found when the command
        # runs. It used to be "https://" in front of whatever followed: "go to the youtube tab"
        # opened https://the youtube tab.
        target = m_browse.group(1).strip().rstrip("?.!,;:")
        from eli.execution.portable_intent_contract import web_open_target
        _web = web_open_target(target)
        if _web is None and target.lower() in WEBSITE_ALIASES:
            _web = ("OPEN_URL", {"url": WEBSITE_ALIASES[target.lower()]})
        _site = re.sub(r"^(?:the|my|a|an)\s+", "", target, flags=re.I)
        if _web is None and not raw.lower().startswith("go ") and 0 < len(_site.split()) <= 4:
            _web = ("OPEN_URL", {"site": _site})
        if _web is not None:
            return _mk(_web[0], _web[1], 0.97, matched_by="web.browse_to", entities=dict(_web[1]))

    # Guard: "search your memory/memories ..." must never fall through to web search.
    m = re.match(r"^(?:search\s+(?:for\s+)?)(.+)$", raw, re.I)
    if m:
        query = m.group(1).strip()
        # "search your fucking memories, ..." opened a web search: a word or two between "your" and the
        # store, or another of ELI's own stores, is still a lookup in ELI's memory.
        if query and not re.match(r"(?:(?:through|in|into)\s+)?(?:(?:your|my|the)\s+)?(?:\w+\s+){0,2}?"
                                  r"(?:memor(?:y|ies)|records|logs|history|notes|database|conversations?|chats?)\b",
                                  query, re.I):
            return _mk("OPEN_BROWSER", {"query": query}, 0.95,
                       matched_by="web.search", entities={"query": query})

    # ------------------------------------------------------------
    # 8) INPUT / VOLUME (a screenshot is taken by the direct rule near the top)
    # ------------------------------------------------------------
    # Volume: said by its noun ("vol down 10%", "volume 60%") it is read by command_parser.volume_said,
    # its one grammar; said by its verb ("turn it down", "raise the volume") by that parser's volume
    # frames. Order matters: unmute before mute.
    if re.search(r"\bmax(?:imum)?\s+volume\b|\bfull\s+volume\b", low):
        return _mk("VOLUME", {"level": 100, "percent": 100, "mode": "absolute", "direction": "set"},
                   0.99, matched_by="audio.volume_max")
    from eli.execution.routing.command_parser import volume_said
    _volume = volume_said(raw)
    if _volume is not None:
        return _volume

    if re.search(r"\bunmute\b", low):
        return _mk("VOLUME", {"direction": "unmute"},
                   0.98, matched_by="audio.unmute")

    if re.search(r"\bmute\b", low):
        return _mk("VOLUME", {"direction": "mute"},
                   0.98, matched_by="audio.mute")


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
    # "find the word compose" names on-screen text without saying "on screen" (it searched the web).
    m = (re.search(r"\b(?:find|locate|where\s+is)\s+(?:the\s+)?(.+?)\s+on\s+(?:the\s+)?(?:screen|page)\b", low)
         or re.match(r"^(?:please\s+|can\s+you\s+)?(?:find|locate|highlight|show\s+me|where\s+is)\s+(?:the\s+)?"
                     r"(?:word|words|text|button|link|label)\s+(.{1,40}?)[\s.!?]*$", low))
    if m:
        query = re.sub(r"^(?:word|words|text|label)\s+", "", m.group(1).strip(" \"'"))
        if query:
            return _mk("SCREEN_LOCATE", {"query": query}, 0.93,
                       matched_by="screen.locate", entities={"query": query})
    if re.search(r"\b(?:click|tap|press)\s+(?:on\s+)?(?:the\s+)?play\s+button\b", low):
        return _mk("SCREEN_LOCATE", {"query": "Play", "click": True}, 0.95,
                   matched_by="screen.click_play_button", entities={"query": "Play"})
    m = re.search(
        r"\b(?:click|tap|press)\s+(?:on\s+)?(?:the\s+)?(.+?)\s+button\b", low
    )
    if m:
        query = (m.group(1) or "").strip(" .,:;-")
        if query and query.lower() not in {"play", "a", "the"}:
            return _mk("SCREEN_LOCATE", {"query": query, "click": True}, 0.94,
                       matched_by="screen.click_named_button", entities={"query": query})
    m = re.search(
        r"\b(?:click|tap|press)\s+(?:on\s+)?(?:the\s+)?(.+?)\s+on\s+(?:the\s+)?screen\b", low
    )
    if m:
        query = m.group(1).strip()
        if query:
            return _mk("SCREEN_LOCATE", {"query": query, "click": True}, 0.94,
                       matched_by="screen.locate_click", entities={"query": query})
    # Also bare "click compose" / "tap send" (it went to the model's guess: dictation, and a raw
    # "<click> compose </click>" as the reply). "press" alone stays with the keyboard.
    m = re.search(
        r"\b(?:click|tap|press)\s+(?:on\s+)?(?:the\s+)?(.+?)\s+(?:link|tab|icon|menu|item)\b",
        low,
    ) or re.match(r"^(?:please\s+|can\s+you\s+|eli,?\s+)?(?:click|tap)\s+(?:on\s+)?(?:the\s+)?"
                  r"(\S+(?:\s+\S+){0,4}?)[\s.!]*$", low)
    if m:
        query = (m.group(1) or "").strip(" .,:;-")
        if query and len(query) >= 2 and not re.match(
                r"^(?:the\s+)?(?:left|right|middle|double|it|that|this|here|there|screen|mouse|cursor|at|where|"
                r"play|pause|stop|next|previous|\d)\b", query):
            return _mk("SCREEN_LOCATE", {"query": query, "click": True}, 0.91,
                       matched_by="screen.click_ui_element", entities={"query": query})
    if re.search(r"\b(?:click|tap|press)\s+play\b", low):
        return _mk("MEDIA_CONTROL", {"command": "play", "target": "browser", "type": "browser"},
                   0.92, matched_by="media.click_play_mpris",
                   entities={"command": "play", "target": "browser"})

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

    # 10) File read / fix / summarize / analyze. "Show clipboard" must be checked before generic
    # "show X" -> READ_FILE.
    if "clipboard" in low and "show" in low:
        return _mk("GET_CLIPBOARD", {}, 0.9, matched_by="clipboard.show")

    # Exclude habit queries from file-read
    if re.match(r"^(?:show|list|read)\s+habits?$", low):
        return _mk("HABIT_STATUS", {}, 0.95, matched_by="habits.show")
    m = re.match(r"^(read|show)\s+(~?/?[\w./\-]+(?:\.[\w]+)?)", raw, re.I)
    if m:
        path = m.group(2).strip()
        # Require the candidate to look like a path: a slash, an extension, a leading ~/, or a path
        # delimiter. Bare words ("me", "the") are excluded so "show me physics news" doesn't become
        # a file read.
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

    # Per-file / "put it in a document" follow-up on the last analysed folder ("i want the summary
    # for each file"). It fell into CHAT and the model invented filenames and a document it never
    # wrote. Route to ANALYZE_PDF_FOLDER's per-file mode. Only when a folder is in context.
    _last = _last_used_path()
    if _last and os.path.isdir(os.path.expanduser(_last)):
        _per_file_intent = bool(
            re.search(r"\b(?:each|every|per[\s-]?|individual|one[\s-]by[\s-]one)\b", low)
            and re.search(r"(?:file|files|document|documents|doc|docs|pdf|pdfs|summar)", low)
        ) or bool(re.search(r"summar\w+\s+(?:for\s+|of\s+)?(?:each|every|all)\b", low))
        # Doc-output follow-up ("put it in a document", "as a docx", a bare "docx, doc, pdf — i don't
        # care"). Must refer to the analysis just run, not a fresh "create a document about <topic>"
        # (GENERATE_DOCUMENT). So: no explicit path or topic ("about X", "on X"), and either a
        # back-reference (it/them/these/each) or a short elliptical reply.
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
                       {"folder": _last, "recursive": True,
                        "per_file": True, "_force_background": True, "format": _fmt},
                       0.9, matched_by="analyze.pdf_per_file_followup")

    # summarize / analyse / read / look at / evaluate: conversation vs file/path. Matches
    # "summarise /path", "analyse and read /path", "read /path", "can you look at /path",
    # "evaluate /path". One verb group so this list can't drift from the one below it again —
    # "evaluate X" used to fall through to CHAT entirely (no verb here matched it at all,
    # not even as a plain summary), which is worse than the instruction-dropping bug below.
    _DOC_VERBS = r"summari[sz]e|analyse|analyze|read|look\s+at|evaluate"
    m = re.match(
        rf"^(?:please\s+)?(?:can\s+you\s+)?(?:{_DOC_VERBS})"
        rf"(?:[,\s]+(?:and\s+)?(?:{_DOC_VERBS}))*"
        r"[,\s]+(?:this\s+)?(.+)$",
        raw, re.I,
    ) or re.match(rf"^(?:please\s+)?(?:{_DOC_VERBS})\s+(.+)$", raw, re.I)
    if m:
        # Text supplied in the request is not a memory-search topic, and stays data, not orders.
        from eli.cognition.supplied_in_message import supplied_as_data
        if supplied_as_data(raw):
            return _mk("CHAT", {"message": raw}, 0.97, matched_by="chat.supplied_text")

        target = m.group(1).strip()
        # If target contains an absolute path, extract from the first / or ~/
        # This handles typos, extra verbs, and multi-word prefixes cleanly.
        _attached = _attachment_marker_path(raw)
        _path_hit = None if _attached else re.search(r'[~/].*$', target)
        if _attached:
            target = _attached
        elif _path_hit:
            # Capture the full remainder (paths contain spaces, parens, ×, –, accents), then take the
            # longest existing prefix, so a spaced path stays whole and trailing chatter is dropped. The old
            # [^\s]+ cut spaced paths and produced phantom paths the model "summarised" from the name.
            _cand = _path_hit.group(0).strip()
            if not os.path.exists(os.path.expanduser(_cand)):
                # A closing delimiter glued to the last token survives the walk-back below because
                # no token prefix drops it. Applied only when it helps, so a real path ending in ')'
                # ("paper (2).pdf") is left alone.
                _untrimmed = _cand
                _cand = _cand.rstrip("]})>\"'`,;")
                if _cand != _untrimmed and not os.path.exists(os.path.expanduser(_cand)):
                    _cand = _untrimmed
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
                rf"^(?:and\s+)?(?:{_DOC_VERBS}|summaise|the|this|file|directory|folder)\s+",
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
                _keep_last_used_path(abs_path)
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
                       "path": abs_path, "instruction": raw},
                       0.92, matched_by="analyze.summarize_file")
        # Referential: "summarise the pdfs in that directory" with no explicit path
        if any(w in low for w in ["pdf", "pdfs"]) and any(
                w in low for w in ["that", "there", "those", "the folder", "the directory"]):
            _last = _last_used_path()
            if _last and os.path.isdir(_last):
                return _mk("ANALYZE_PDF_FOLDER", {"folder": _last, "recursive": True},
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
        # "summarise what that folder contains", "read that file": the place the last command opened
        # or listed. Taken as a topic, it was looked up in memory and old messages came back.
        from eli.execution.routing.command_parser import place_referred_to
        _place = place_referred_to(target)
        if _place is None:
            # "read this folder": the "this" the verb's pattern passed over is the pointer
            _place = place_referred_to(re.sub(rf"^.*?\b(?:{_DOC_VERBS})\b[,\s]+(?:(?:and\s+)?(?:{_DOC_VERBS})[,\s]+)*",
                                              "", raw, count=1, flags=re.I))
        if _place:
            # a folder of PDFs is read as papers (ANALYZE_PDF_FOLDER); any other place by its files
            try:
                _names = [n for n in os.listdir(_place) if not n.startswith(".")] if os.path.isdir(_place) else []
            except OSError:
                _names = []
            if _names and all(n.lower().endswith(".pdf") for n in _names if os.path.isfile(os.path.join(_place, n))) \
                    and any(n.lower().endswith(".pdf") for n in _names):
                return _mk("ANALYZE_PDF_FOLDER", {"folder": _place, "recursive": True, "instruction": raw}, 0.9,
                           matched_by="analyze.pdf_folder_referred", entities={"folder": _place, "said": target})
            return _mk("SUMMARIZE_FILE", {"path": _place, "instruction": raw}, 0.9,
                       matched_by="analyze.summarize_referred", entities={"path": _place, "said": target})
        if _place == "":
            return _mk("CHAT", {"message": raw}, 0.6, matched_by="chat.vague_file_ref")
        # A bare generic file-noun ("read the document", "summarize the file") with no filename or
        # path isn't a memory topic; there's nothing to recall. Defer to CHAT so ELI asks which
        # file.
        if target.strip().lower() in {
            "document", "documents", "doc", "docs", "file", "files",
            "it", "this", "that",
        }:
            return _mk("CHAT", {"message": raw}, 0.6, matched_by="chat.vague_file_ref")
        # A conversation back-reference ("read my previous reply", "what did I just say") means the
        # live transcript, not a long-term topic. MEMORY_RECALL does a LIKE on "previous"/"reply" and
        # dredges up days-old chatter. Defer to CHAT, whose context carries recent conversation_turns.
        if (re.search(r"\b(?:previous|last|that|my|the)\s+(?:reply|message|response|answer|comment)\b", low)
                or re.search(r"\bwhat\s+(?:did\s+)?i\s+(?:just\s+)?(?:say|said|wrote|ask|asked)\b", low)
                or re.search(r"\bread\s+(?:the|my)\s+(?:response|reply|message|answer)\b", low)
                or re.search(r"\bthe\s+(?:last\s+)?thing\s+i\s+(?:just\s+)?said\b", low)):
            return _mk("CHAT", {"message": raw}, 0.6, matched_by="chat.conversation_self_ref")
        _in_hand = _route_work_in_hand(raw, low)
        if _in_hand is not None:
            return _in_hand
        # Summarising a topic is a question for memory. "Analyse the 3 remaining folders", "look at
        # what i'm watching" and "analyse them" are not: each got a dump of old messages.
        if re.match(r"^(?:please\s+)?(?:can\s+you\s+)?(?:summari[sz]e\b|read\s+about\b)", raw, re.I):
            return _mk("MEMORY_RECALL", {"query": target},
                       0.8, matched_by="memory.summarize_topic")

    # PDF analysis
    if any(w in low for w in ["pdf", "pdfs"]) and (any(w in low for w in [
            "analyze", "analyse", "summarize", "summarise", "read", "extract"])
            or re.match(r"^(?:\w+[,!\s]+){0,2}do\s+(?:the|those|these|them|my)\b", low)):
        path_match = _extract_pdf_path(raw)
        if path_match:
            return _mk("ANALYZE_PDF", {"path": path_match, "instruction": raw}, 0.95,
                       matched_by="analyze.pdf", entities={"path": path_match})
        # Referential: "the pdfs in that directory" → use last resolved path
        _is_referential = any(w in low for w in ["that", "there", "those", "the folder", "the directory"])
        _last = _last_used_path()
        if _is_referential and _last and os.path.isdir(_last):
            return _mk("ANALYZE_PDF_FOLDER", {"folder": _last, "recursive": True},
                       0.90, matched_by="analyze.pdf_referential")
        _in_hand = _route_work_in_hand(raw, low)
        if _in_hand is not None:
            return _in_hand
        return _mk("ANALYZE_PDF", {"path": "", "instruction": raw},
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
        # Strip the command framing, then peel stacked leading connectives ("a document with
        # proposals..." -> "proposals..."). Collapse whitespace so a removed word doesn't leave gaps or a
        # leaked "with"/"for" (it produced "with_proposals_for_upgrades_for_yourself.md").
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
    # The tail of this function lives in eli.execution.routing.intents as ordered stages. They run
    # in the legacy order and the first to match wins, exactly as the inline guards did; a stage
    # returns None to pass. Change a guard in its stage module, not here.
    for _stage_name, _stage in INTENT_STAGES_TAIL:
        _hit = _stage(text, raw, low, text_l)
        if _hit is ROUTE_NONE:
            return None          # the stage ended routing outright
        if _hit is not None:
            return _hit

    # A question about ELI's own tastes or views ("what's your favourite kind of music?", "do you like
    # jazz?") is conversation. Left at the fallback's confidence it went to the intent model, and a
    # small one sent it to NEWS_FETCH. Every specific route above has already had its turn.
    if re.search(r"\b(?:what|which)(?:'?s|\s+is|\s+are|\s+would\s+be)?\s+(?:your|ur)\s+(?:favou?rite|fav|preferred)\b"
                 r"|\bdo\s+(?:you|u)\s+(?:like|love|enjoy|prefer|hate|dislike)\b"
                 r"|\bhow\s+do\s+(?:you|u)\s+feel\s+about\b"
                 r"|\bwhat\s+(?:kind|sort|type)s?\s+of\s+\w+(?:\s+\w+)?\s+do\s+(?:you|u)\s+"
                 r"(?:like|enjoy|prefer|listen\s+to|watch|read)\b", low):
        return _mk("CHAT", {"message": raw}, 0.9, matched_by="chat.opinion_of_eli")

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

def _eli_mqc_clean_query(q: str) -> str:
    """
    Normalize whitespace and strip leading verb tokens, but preserve the
    original casing of title/artist names. Apply known STT alias fixes
    case-insensitively without lowercasing the rest of the query.
    """
    q = _eli_mqc_re.sub(r"\s+", " ", str(q or "")).strip(" .,!?:;")
    q = _eli_mqc_re.sub(r"^(open|play|search|find)\s+", "", q, flags=_eli_mqc_re.I).strip()
    # Punctuation between words is not part of the song: "play evil; by orlo" searched "evil; orlo".
    # Inside a word it stays (P!nk, AC/DC, B.I.G.).
    q = _eli_mqc_re.sub(r"[\"“”]+|(?:[;!?,]+|\.{2,}|…)(?=\s|$)", " ", q)
    # Strip trailing service qualifiers — the router already extracted `target`.
    # Without this, "logic by veridian on spotify" → query="logic by veridian on spotify"
    # instead of the expected "logic by veridian".
    q = _eli_mqc_re.sub(
        r"\s+on\s+(spotify|youtube|yt|youtube\.com|mpv|soundcloud|apple\s+music|tidal|deezer|amazon\s+music)\s*$",
        "",
        q,
        flags=_eli_mqc_re.I,
    ).strip()
    q = _eli_mqc_re.sub(
        r"\b(?:alt\s*[- ]?\s*0|hours\s+of\s+zero|i'll\s+just\s+air|"
        r"and\s+i'll\s+just\s+air|algezera|algezira|algazear|algazare|"
        r"algiers\s+era)\b",
        "al jazeera",
        q,
        flags=_eli_mqc_re.I,
    )
    return _eli_mqc_re.sub(r"\s+", " ", q).strip()


# Identity route wrapper, tiny-chat guard, follow-up pass-through,
# and persona-route override all use the top-level re import.


# voice_runtime_contract_guard: deterministic voice-command grammar for common local-control
# requests. Fixes ASR wording variants before the tiny-fragment/fallback chat guards see them. No
# absolute paths.

def _eli_voice_contract_text_from_call(args, kwargs):
    for item in args:
        if isinstance(item, str) and item.strip():
            return item
    for key in ("text", "message", "command", "prompt", "query", "utterance"):
        item = kwargs.get(key)
        if isinstance(item, str) and item.strip():
            return item
    return ""

def _eli_voice_contract_norm(text):
    import re
    text = str(text or "").lower()
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = text.replace(" per cent", "%").replace(" percent", "%")
    text = re.sub(r"[^a-z0-9%'\s]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

def _eli_voice_contract_route(text):
    import re

    raw = str(text or "").strip()
    norm = _eli_voice_contract_norm(raw)
    if not norm:
        return None

    # The volume is read by its one grammar (routing/command_parser.volume_said).
    from eli.execution.routing.command_parser import volume_said
    volume = volume_said(raw)
    if volume is not None:
        return volume

    # Keep "open settings" on the direct-execution path. Do not send this to
    # cognition/broker after the OS action has already succeeded.
    if re.fullmatch(r"(?:open|launch|show)\s+(?:system\s+)?settings", norm):
        return {
            "action": "OPEN_APP",
            "args": {"name": "settings", "app": "settings"},
            "confidence": 0.999,
            "meta": {
                "matched_by": "voice_runtime_contract.open_settings_direct",
                "normalized": "open settings",
            },
        }

    return None

def _eli_voice_contract_wrap_callable(fn):
    if not callable(fn) or getattr(fn, "_eli_voice_contract_wrapped", False):
        return fn

    def _wrapped(*args, **kwargs):
        shortcut = _eli_voice_contract_route(_eli_voice_contract_text_from_call(args, kwargs))
        if shortcut is not None:
            return shortcut
        return fn(*args, **kwargs)

    try:
        _wrapped.__name__ = getattr(fn, "__name__", "_wrapped")
        _wrapped.__doc__ = getattr(fn, "__doc__", None)
        _wrapped._eli_voice_contract_wrapped = True
    except Exception:
        _SWLOG.debug("suppressed exception", exc_info=True)
    return _wrapped

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
import re as _eli_lrf_re


def _eli_lrf_mk(action, args=None, confidence=0.99, matched_by="eli.live_route_second_fix"):
    mk = globals().get("_mk")
    if callable(mk):
        try:
            return mk(action, args or {}, confidence, matched_by=matched_by, allow_chat_without_evidence=False)
        except TypeError:
            try:
                return mk(action, args or {}, confidence, matched_by=matched_by)
            except TypeError:
                _SWLOG.debug("suppressed exception", exc_info=True)
    return {
        "action": action,
        "args": args or {},
        "confidence": confidence,
        "meta": {"matched_by": matched_by, "allow_chat_without_evidence": False},
    }

def _eli_lrf_word_to_int(s):
    s = str(s or "").strip().lower()
    table = {
        "one": 1, "two": 2, "to": 2, "too": 2,
        "three": 3, "tree": 3, "free": 3,
        "four": 4, "for": 4, "fore": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8,
    }
    if s.isdigit():
        return int(s)
    return table.get(s)

# "continue/resume the X project" -> goal_store lookup, not a CHAT guess. The task-noun
# requirement is load-bearing: bare "resume X" also matches media's dynamic_control_contract
# post-contract ("resume spotify" -> MEDIA_CONTROL) and would otherwise lose to it — see
# _eli_media_contract_post's RESUME_TASK short-circuit below.
_RESUME_TASK_RX = re.compile(
    r"\b(?:continue|resume|pick\s+(?:back\s+)?up|go\s+back\s+to|get\s+back\s+to)\s+"
    r"(?:working\s+on\s+)?(?:the\s+|my\s+|that\s+|our\s+)?"
    r"([^.?!,;]{2,80}\b(?:project|task|work|assignment|plan|build|effort)\b[^.?!,;]{0,30})",
    re.I,
)


def _eli_resume_task_pre_route(text):
    raw = str(text or "").strip()
    low = raw.lower()
    m = _RESUME_TASK_RX.search(low)
    if not m:
        return None
    topic = m.group(1).strip().rstrip(" .")
    if not topic:
        return None
    return _mk(
        "RESUME_TASK",
        {"topic": topic, "question": raw},
        0.9,
        matched_by="resume_task.explicit_continue",
        need_grounding=True,
    )


def _eli_lrf_pre_route(text):
    raw = str(text or "").strip()
    low = raw.lower().strip()
    low = low.replace("×", "x")

    # Exact/near-exact mode query: status action only, not full RUNTIME_STATUS.

    if _eli_phase10_is_codebase_audit_request(raw):
        return _mk(
            "RUNTIME_AUDIT",
            {"query": raw, "audit_depth": "codebase"},
            0.99,
            matched_by="phase10.codebase_audit_guard",
            allow_chat_without_evidence=False,
            need_grounding=True,
            task_family="grounded_audit",
        )

    # Only report current mode status if the query is specifically about the active/current mode.
    # Queries asking about ALL modes, explaining modes, differences, etc. must reach CHAT/synthesis.
    _lrf_asking_all_modes = _eli_lrf_re.search(
        r"\b(all|every|each|how many|list|explain|full|describe|detail|difference|differ|compare|what are|tell me about|tell me all|tell me everything|what do|how do|modes?\s+you\s+have|modes?\s+does)\b",
        low,
    )
    # A frustrated correction that merely MENTIONS a mode ("you are not in quick mode",
    # "what are you talking about", "that's not right") must NOT trigger the all-modes
    # explainer — that's a relational CHAT turn, not a request to list the modes.
    _is_mode_correction = bool(_eli_lrf_re.search(
        r"\byou('?re| are| were)\s+not\b|\bnot\s+in\b|\bwhat\s+are\s+you\s+talking\s+about\b"
        r"|\bthat('?s| is| was)\s+not\b|\byou\s+said\b|\bnot\s+(?:quick|normal|advanced|research|expert)\b",
        low))
    # Require an actual modes TOPIC ("reasoning mode(s)" or plural "modes"), not a bare
    # singular "<x> mode" mention, so "quick mode" in passing doesn't match.
    if (_lrf_asking_all_modes and not _is_mode_correction
            and _eli_lrf_re.search(r"\b(reasoning modes?|modes)\b", low)):
        return _eli_lrf_mk(
            "EXPLAIN_ALL_REASONING_MODES", {},
            0.99,
            "reasoning.all_modes_grounded.second_fix",
        )
    if "reasoning mode" in low and not _lrf_asking_all_modes and not _eli_lrf_re.search(
        r"\b(cognition pipeline|input to output|every step|memory system|db tables|functions|files|runtime audit|diagnostic|diagnostics|full audit)\b",
        low,
    ):
        return _eli_lrf_mk("REASONING_MODE_STATUS", {}, 0.995, "reasoning.mode_status.second_fix")

    # Internal memory architecture question must not become document/search plugin output.
    if _eli_lrf_re.search(r"\b(memory system|memory internally|memory.*db tables|db tables|which files|which functions)\b", low) and _eli_lrf_re.search(r"\b(memory|db|sqlite|faiss|functions?|files?|internally|runtime)\b", low):
        return _eli_lrf_mk(
            "EXPLAIN_MEMORY_RUNTIME",
            {"question": raw, "detail": "full"},
            0.985,
            "memory.runtime_architecture.second_fix",
        )

    # Bare grid follow-up, including STT's "tree" for "three" and optional trailing "grid/layout/windows".
    m = _eli_lrf_re.fullmatch(
        r"\s*(\d{1,2}|one|two|three|tree|four|five|six|seven|eight)\s*(?:x|by)\s*(\d{1,2}|one|two|three|tree|four|five|six|seven|eight)\s*(?:grid|layout|windows?)?\s*",
        low,
    )
    if m:
        cols = _eli_lrf_word_to_int(m.group(1))
        rows = _eli_lrf_word_to_int(m.group(2))
        if cols and rows and 1 <= cols <= 8 and 1 <= rows <= 8:
            return _eli_lrf_mk(
                "TILE_WINDOWS",
                {"cols": cols, "rows": rows, "grid": [cols, rows]},
                0.995,
                "window.grid_followup.second_fix",
            )

    return None


# ELI_PERSONAL_MEMORY_MODE_AWARE_ROUTER_FIX_20260505
# High-priority route wrapper: personal memory questions need synthesis, not raw truth reports/search.
import re as _eli_pm_re
from eli.cognition.self_claims import WHAT_JUST_HAPPENED as _WHAT_JUST_HAPPENED


def _eli_pm_mk(action, args=None, confidence=0.99, matched_by="eli.personal_memory_mode_aware"):
    mk = globals().get("_mk")
    if callable(mk):
        try:
            return mk(action, args or {}, confidence, matched_by=matched_by, allow_chat_without_evidence=False)
        except TypeError:
            try:
                return mk(action, args or {}, confidence, matched_by=matched_by)
            except TypeError:
                _SWLOG.debug("suppressed exception", exc_info=True)
    return {
        "action": action,
        "args": args or {},
        "confidence": confidence,
        "meta": {"matched_by": matched_by, "allow_chat_without_evidence": False},
    }

def _eli_pm_wants_raw_memory_truth(low):
    return bool(_eli_pm_re.search(
        r"\b(memory truth report|memory count|how many memories|memory status|memory runtime status|raw counts?|db counts?|diagnostic counts?)\b",
        low,
    ))

def _eli_pm_wants_personal_memory(low):
    if _eli_pm_wants_raw_memory_truth(low):
        return False

    has_memory = bool(_eli_pm_re.search(r"\b(memory|remember|stored memories|what do you know about me|what you know about me|actual(?:ly)? remember)\b", low))
    has_depth = bool(_eli_pm_re.search(
        r"\b(full|in[- ]?depth|personalised|personalized|properly|not quick|not in quick mode|"
        r"stop giving me data dumps|data dumps|what you actually remember|about me|which files|db tables|functions|internally|cognition pipeline)\b",
        low,
    ))
    return has_memory and has_depth

# "why did you go to the browser / search the web for that": a complaint about ELI's own
# dispatch. Was any "why ... search/web/online", so "why the vector search is drifting"
# got a routing-trace dump about an earlier, unrelated command.
_ROUTING_FAULT_COMPLAINT_RE = _eli_pm_re.compile(
    r"\bwhy\b[^.?!]{0,40}?\b(?:did|do|does|are|were|would|have|has)\s+(?:you|eli|it)\b"
    r"[^.?!]{0,40}?\b(?:go|went|going|open|opened|opening|send|sent|sending|search|searched|"
    r"searching|use|used|using|launch|launched|pull|pulled|take|took|bring|brought|"
    r"redirect|redirected)\b[^.?!]{0,40}?\b(?:browser|web|online|internet|google)\b"
    r"|\bwhy\b[^.?!]{0,30}?\b(?:go|goes|went)\b[^.?!]{0,20}?\bbrowser\b",
    _eli_pm_re.I,
)

# "why did you NOT search properly" / "why didn't you search the web right" is a complaint about the
# QUALITY of a search the user wanted done, not a complaint that ELI went to the web at all: the
# opposite of a routing fault. Routed to ROUTING_FAULT_EXPLAIN it dumped the routing table at the
# user. Those stay out of the routing-fault path and go to chat (ELI apologises / offers a fresh search).
_WANTED_BETTER_SEARCH_RE = _eli_pm_re.compile(
    r"\bwhy\b[^.?!]{0,40}?\b(?:did\s*n[o']?t|didn'?t|not|never|failed\s+to|couldn'?t|could\s+not)\b"
    r"[^.?!]{0,25}?\b(?:search|look|check|google|browse)\b"
    r"|\b(?:search|look|google|browse)\w*\b[^.?!]{0,20}?\b(?:properly|correctly|right|better|"
    r"thoroughly|harder|again)\b",
    _eli_pm_re.I,
)


def wants_routing_fault_explain(text: str) -> bool:
    s = str(text or "")
    if _WANTED_BETTER_SEARCH_RE.search(s):
        return False
    return bool(_ROUTING_FAULT_COMPLAINT_RE.search(s))


def _eli_dispatch_is_the_subject(raw: str, low: str) -> bool:
    """Whether a "why ... fail / not work / route" question is about what ELI dispatched: ELI is
    named ("you", "your", "ELI"), or the thing that failed is one of ELI's own commands ("the
    search", "the timer", "that command"), not a thing of the world ("the roman empire", "my wifi")."""
    if _names_eli(raw):
        return True
    m = re.search(r"\bwhy\s+(?:did|does|do|didn'?t|doesn'?t|is|was|isn'?t|wasn'?t|has|hasn'?t)\s+"
                  r"(?:the|that|this)\s+([a-z]+)\b", low)
    if not m:
        return False
    from eli.runtime.command_splitter import _command_words
    return m.group(1) in _command_words()


def _eli_pm_pre_route(text):
    raw = str(text or "").strip()
    low = raw.lower()

    if wants_routing_fault_explain(low):
        return _eli_pm_mk(
            "ROUTING_FAULT_EXPLAIN",
            {"question": raw},
            0.995,
            "routing_fault.browser_complaint",
        )

    # "why did that one work and the others didn't", "why did you fail to open downloads", "why was
    # that routed wrong": questions about ELI's own dispatch. Runs in the pre-route, ahead of
    # chat.long_question_guard which would force 12+ words to CHAT with no trace, and the model
    # invented a cause. ROUTING_FAULT_EXPLAIN answers from the actual router result.
    # The second and third forms name no subject of their own: "why did the roman empire fail", "why
    # does my wifi not work" and "why is this route closed" got ELI's routing trace. They are about
    # ELI's dispatch when ELI is their subject ("why did you fail to open it", "why was my request
    # routed there") or one of ELI's own commands is ("why did the search fail").
    if (
        _eli_pm_re.search(
            r"\bwhy\b[^?]*\b(?:did|does|didn'?t|do)\b[^?]*"
            r"\b(?:that|this|it|those|the\s+(?:others?|first|last|previous|other\s+ones?))\b"
            r"[^?]*\b(?:work(?:ing|ed)?|fail(?:ed)?|open|run|route[ds]?)\b", low)
        or (_eli_dispatch_is_the_subject(raw, low) and (
            _eli_pm_re.search(r"\bwhy\b[^?]*\b(?:fail(?:ed)?|not\s+work|didn'?t\s+work|did\s+not\s+work)\b", low)
            or _eli_pm_re.search(r"\bwhy\b[^?]*\b(?:routed|routing|mis-?rout\w*)\b", low)))
        or _eli_pm_re.search(r"\bwhy\b[^?]*\b(?:was|were|is|got)\b[^?]*\b(?:mis-?routed|routed)\b", low)
    ):
        return _eli_pm_mk(
            "ROUTING_FAULT_EXPLAIN",
            {"question": raw},
            0.98,
            "routing_fault.dispatch_question",
        )

    # "why did that take so long", "what exactly did you do to answer that": the last turn's audit
    # row has its route, what ran, the model calls and the time. Sent to chat, this was web-searched.
    if len(low.split()) <= 30 and (
        _eli_pm_re.search(
            r"\bwhy\s+(?:the\s+\w+\s+)?(?:did|does|is|was)\s+(?:(?:it|that|this)\s+tak\w+\s+(?:you\s+)?|you\s+tak\w+\s+)"
            r"(?:so|that)\s+long(?:\s+to\s+(?:answer|reply|respond|do|get|come|load|think|say|work)\b[^?,]*)?\s*(?:\?|$|,|\band\b)", low)
        or _eli_pm_re.search(r"\bwhy\s+(?:are|were|was|is)\s+(?:you|that|it)\s+(?:so\s+|that\s+)?slow\b", low)
        # a remark that the last reply was slow: "that took over a minute??", "it took you 20 minutes"
        or _eli_pm_re.search(
            r"\b(?:that|it|this|you)\s+took\s+(?:you\s+)?(?:(?:over|nearly|almost|more\s+than|about|just\s+under|like)\s+)?"
            r"(?:(?:a|an|one|\d+)\s*(?:minutes?|mins?|seconds?|secs?|hours?)|so\s+long|too\s+long|way\s+too\s+long|ages|forever)\b", low)
        or _eli_pm_re.search(
            r"\b(?:you(?:'re|’re|\s+are|\s+were)|that(?:'s|’s|\s+was|\s+is)|it(?:'s|’s|\s+was|\s+is))\s+"
            r"(?:(?:so|too|really|very)\s+slow|taking\s+(?:so|too|way\s+too)\s+long)\b", low)
        or _eli_pm_re.search(
            r"\bwhat\s+(?:exactly\s+)?did\s+you\s+(?:just\s+|actually\s+)?do\s*"
            r"(?:\?|$|there\b|to\s+(?:answer|get)\b|just\s+now\b|for\s+that\b|with\s+that\b)", low)
        or _eli_pm_re.search(_WHAT_JUST_HAPPENED, low)
    ):
        try:
            from eli.cognition.query_planner import parse_window as _pw_last
            _asks_period = bool(_pw_last(raw))
        except Exception:
            _asks_period = False
        if not _asks_period:
            return _eli_pm_mk(
                "ROUTING_FAULT_EXPLAIN",
                {"question": raw},
                0.98,
                "routing_fault.last_turn_question",
            )

    if _eli_pm_re.search(r"\bstop giving me data dumps\b|\bwe are not in quick mode\b|\bfull and personalised response\b|\bfull and personalized response\b", low):
        return _eli_pm_mk(
            "PERSONAL_MEMORY_DEEP_EXPLAIN",
            {"question": raw, "reason": "user_rejected_data_dump"},
            0.995,
            "memory.personalized_no_data_dump",
        )

    if _eli_pm_wants_personal_memory(low):
        return _eli_pm_mk(
            "PERSONAL_MEMORY_DEEP_EXPLAIN",
            {"question": raw},
            0.99,
            "memory.personalized_deep_explain",
        )

    return None


# --- ELI high-priority self-improvement route guard ---
# A failure in code: a traceback's frames, an exception class, a source file, a line number, pytest.
_CODE_FAILURE_EVIDENCE = re.compile(
    r"\b[A-Z]\w*(?:Error|Exception|Warning)\b|\.py\b|\bline\s+\d+\b|File \"|\beli/|\bpytest\b|\btests?/\w"
    r"|\bstack\s*trace\b|\btraceback\b")


def _eli_self_improvement_phrase_guard(text):
    raw = str(text or "")
    low = raw.lower().strip()

    # Self/system routes that the generic portable open_app / shell / memory
    # matchers would otherwise steal (claims-suite findings: "run a self test" →
    # OPEN_APP, etc.). Tight patterns, matched here BEFORE portable_route.
    # GENERATE_TESTS writes tests for ELI's own functions: "write unit tests for my react app" is the
    # user's code, a script to write.
    _gen_tests = re.search(r"\b(generate|write|create)\s+(behaviou?ral\s+|unit\s+)?tests?\b|"
                           r"\btest\s+generation\b|\bgrow\s+(your\s+)?(test\s+)?coverage\b|"
                           r"\bwrite\s+tests?\s+for\s+(your|the)\b", low)
    if _gen_tests and "report" not in low and (
            _names_eli(raw) or not re.search(r"\b(?:for|of|in|on)\s+(?:my|our|his|her|their|a|an|this|these|those)\b"
                                             r"|\bmy\b", low[_gen_tests.start():])):
        return _eli_schedule_prepass(raw) or _mk("GENERATE_TESTS", {}, 0.95, matched_by="tests.generate.guard")
    if re.search(r"\btest\s+review\b|\breview\s+(the\s+)?test(s| results| suite)\b|"
                 r"\brun\s+(the\s+)?(full\s+)?(tests?|test\s+suite|project)\b.*\b(review|summari[sz]e|"
                 r"what('?s| is)?\s+(wrong|to\s+fix)|options|tell\s+me)\b|"
                 r"\b(run|do)\s+(a\s+)?(full\s+)?(test|project)\s+(run\s+and\s+)?(review|summary)\b", low):
        return _mk("TEST_REVIEW", {}, 0.96, matched_by="tests.review.guard")
    if re.search(r"\b(run\s+(the\s+|your\s+)?(test\s+suite|tests|pytest|claims\s+suite)|"
                 r"test\s+report|generate\s+(a\s+)?test\s+report|how('?s| is)\s+the\s+test\s+suite)\b", low):
        # "run the test suite tonight" asks for it then, not now.
        return _eli_schedule_prepass(raw) or _mk("RUN_TESTS", {}, 0.96, matched_by="tests.run.guard")
    if re.search(r"\bself[\s_-]?test\b|\brun\s+(a\s+)?self[\s_-]?test\b", low):
        return _mk("SELF_TEST", {}, 0.96, matched_by="self.test.guard")
    if re.search(r"\borchestrat(ion|or)\s+status\b|\b(show|explain)\s+(me\s+)?(your\s+)?(the\s+)?"
                 r"(agent\s+)?(dag|orchestration)\b|\bagent\s+dag\b|\bhow\s+are\s+your\s+agents\s+wired\b|"
                 r"\b(execution|agent)\s+layers\b", low):
        return _mk("ORCHESTRATION_STATUS", {}, 0.95, matched_by="orchestration.status.guard")
    # CODEBASE_GRAPH: own-architecture questions answered from the live import graph
    # (codebase_graph.py). Triggers on an explicit graph/map request or a component name plus a
    # connection verb ("how does the router reach the executor"). Below ORCHESTRATION_STATUS so
    # agent-DAG questions still win there.
    _comp = (r"(router|executor|engine|agent\s*bus|orchestrat(?:or|ion)|gguf|inference|"
             r"memory|vector\s*store|plugins?|perception|planning|world|netguard|"
             r"components?|modules?|subsystems?|internals?)")
    if (re.search(r"\b(codebase|code\s*base|component|module|dependency)\s+(graph|map|diagram)\b", low)
            or re.search(r"\b(show|explain|draw|describe|map)\s+(me\s+)?(your\s+)?"
                         r"(codebase|code\s*base|how\s+your\s+code\s+(is\s+)?(built|organi[sz]ed|connects|fits))\b", low)
            or re.search(r"\bhow\s+(are|do)\s+your\s+" + _comp + r"\s+(connect|wired|linked|relate)", low)
            # A part of ELI said in common words ("memory", "world", "engine") is ELI's when ELI is
            # named or what it connects to is a part as well: "how does the router reach the
            # executor", not "how does memory relate to sleep".
            or ((_names_eli(raw) or len(re.findall(r"\b" + _comp + r"\b", low)) >= 2)
                and re.search(r"\bhow\s+does\s+(the\s+|your\s+)?" + _comp +
                              r"\b.{0,40}\b(connect|talk|link|relate|reach|wire|depend|feed|work\s+with)\b", low))
            or (_names_eli(raw)
                and re.search(r"\bwhat\s+does\s+(the\s+|your\s+)?" + _comp + r"\b.{0,20}\bdepend\s+on\b", low))):
        return _mk("CODEBASE_GRAPH", {"question": low}, 0.9, matched_by="codebase.graph.guard")
    # AUTOPILOT_DEBUG — a pasted traceback / pytest failure, or an explicit "debug/diagnose
    # this error" request. Passes the RAW text (case-preserved) so file paths in the
    # traceback parse correctly. A bare Python traceback signature is enough on its own.
    # The plan is drawn from ELI's own git history, so the failure has to be one in code: a traceback,
    # a pytest line, an exception class, a source file, or ELI named. "why did the plane crash?" was
    # given a debugging plan for ELI's own code.
    if ("traceback (most recent call last)" in low
            or re.search(r"^failed\s+\S+\.py|^\s*failed\s+tests?/", low, re.M)
            or (re.search(r"\b(debug|diagnose|root[\s-]?cause|troubleshoot|"
                          r"what('?s| is)\s+(causing|wrong\s+with)|why\s+(is|does|did)\b[^.?!]{0,40}\b"
                          r"(fail|crash|break|error|except))\b", low)
                and re.search(r"\b(error|traceback|exception|failure|failing|crash(ed)?|"
                              r"stack\s*trace|pytest|assert(ion)?|bug)\b", low)
                and (_names_eli(raw) or _CODE_FAILURE_EVIDENCE.search(raw)))):
        _dbg_args = {"error_text": raw}
        # "reproduce/verify/confirm this" → run the failing tests (read-only). v2 keeps the
        # verify half only; the governed apply→fix loop is a v3 frontier feature.
        if re.search(r"\b(reproduce|verify|confirm)\b", low):
            _dbg_args["verify"] = True
        return _mk("AUTOPILOT_DEBUG", _dbg_args, 0.93, matched_by="autopilot.debug.guard")
    # ELI's own model: "fine-tune the eq on my speakers" and "can you train my dog" are not.
    _own_model = r"(?:yourself|your\s+(?:own\s+)?(?:model|weights|llm|brain|voice|persona)|eli|the\s+(?:model|llm)|a\s+lora|an?\s+adapter)"
    if re.search(r"\blora\s+status\b|\b(lora|training)\s+(status|readiness|ready)\b|"
                 r"\bis\s+lora\s+ready\b|\bcheck\s+(the\s+)?lora\b|"
                 r"\bcan\s+you\s+(?:fine[- ]?tune|train)(?:\s+" + _own_model + r")?\s*[?.!]*$", low):
        return _mk("LORA_STATUS", {}, 0.95, matched_by="lora.status.guard")
    if re.search(r"\b(train|run|start)\s+(a\s+|the\s+)?lora\b|\blora\s+train(ing)?\b|"
                 r"\bfine[- ]?tune\s+" + _own_model + r"\b|^\s*fine[- ]?tune\s*[.!]*$|\btrain\s+(an?\s+)?adapter\b", low):
        return _mk("LORA_TRAIN", {}, 0.95, matched_by="lora.train.guard")
    if re.search(r"\banaly[sz]e\s+(your(self)?|its?\s+own)\s+"
                 r"(failures?|errors?|mistakes|performance|metrics)\b", low):
        return _mk("SELF_ANALYZE", {}, 0.96, matched_by="self.analyze.guard")
    if re.search(r"\bproactive\s+status\b|\bstatus\s+of\s+(the\s+)?proactive\b", low):
        return _mk("PROACTIVE_STATUS", {}, 0.96, matched_by="proactive.status.guard")
    # ELI's self-generated agenda: "do you have any proposals/suggestions/ideas for me",
    # "what are you proposing", "what's on your agenda". Surfaces proposal_only goals +
    # capability proposals (GET_PROPOSALS) — was mis-routing to unrelated status dumps.
    # The proposals are ELI's own when nothing else is what they are for: "i want to repaint my kitchen,
    # any suggestions?" and "do you have any ideas for me for dinner" ask for advice, and got ELI's
    # list of its own goals.
    _ask_proposals = re.search(
        r"\b(?:do you have|have you got|got|any|you have)\b[^?]*\b(?:proposals?|suggestions?|ideas?|"
        r"recommendations?)\b[^?]*\b(?:for me|to make)\b(?P<rest>.*)$", low)
    if ((_ask_proposals and (re.fullmatch(r"[\s?.!,]*(?:(?:please|then|now|today|at all|yet)[\s?.!,]*)*",
                                         _ask_proposals.group("rest"))
                             or _names_eli(_ask_proposals.group("rest"))))
            or re.search(r"\bwhat\s+(?:are\s+you|do\s+you)\s+propos", low)
            or re.fullmatch(r"\s*(?:(?:so|ok|okay|well|and|eli)[,\s]+)*(?:got\s+)?(?:any|some)\s+(?:proposals?|"
                            r"suggestions?|recommendations?)(?:\s+for\s+(?:me|us))?\s*[?.!]*", low)
            or re.search(r"\bwhat'?s?\s+on\s+your\s+agenda\b|\byour\s+(?:own\s+)?(?:proposals?|agenda)\b", low)):
        return _mk("GET_PROPOSALS", {}, 0.95, matched_by="proactive.get_proposals")
    if re.search(r"\b(start|launch|enable|turn\s+on)\s+(the\s+)?proactive(\s+(mode|daemon))?\b", low):
        return _mk("PROACTIVE_START", {}, 0.96, matched_by="proactive.start.guard")
    if re.search(r"\b(stop|kill|disable|turn\s+off|shut\s+down)\s+(the\s+)?proactive(\s+(mode|daemon))?\b", low):
        return _mk("PROACTIVE_STOP", {}, 0.96, matched_by="proactive.stop.guard")
    if re.search(r"\b(execute|run)\s+(the\s+)?goal\b", low):
        _g = re.sub(r"\b(execute|run)\s+(the\s+)?goal\b", "", low).strip()
        return _mk("EXECUTE_GOAL", {"goal": _g or raw}, 0.95, matched_by="goal.execute.guard")

    if re.search(r"\b(self[- ]?improvement|improvement)\s+log\b", low) or (
        re.search(r"\bexact\s+error\s+message\b", low)
        and re.search(r"\blast\s+failure\b", low)
    ):
        return {
            "action": "SELF_IMPROVEMENT_LOG",
            "args": {"question": raw, "limit": 5},
            "confidence": 0.99,
            "meta": {
                "matched_by": "eli.self_improvement_log_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "self_improvement",
            },
        }

    if re.search(
        r"\b(run|start|perform|execute)\s+(?:an?\s+)?(?:self[- ]?)?improvement(?:\s+cycle)?\b"
        r"|\bimprove\s+yourself\b"
        r"|\bself[- ]?improve\b",
        low,
    ):
        return {
            "action": "SELF_IMPROVE",
            # the executor reads the words for "propose" / "self fix" / "in the foreground"
            "args": {"_raw_user_text": raw},
            "confidence": 0.99,
            "meta": {
                "matched_by": "eli.self_improvement_cycle_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "self_improvement",
            },
        }

    if re.search(
        r"\b(self help|repair playbook|maintenance playbook|fix playbook|"
        r"how do i fix|which self command|self maintenance help)\b",
        low,
    ):
        return {
            "action": "SELF_REPAIR_PLAYBOOK",
            "args": {"question": raw},
            "confidence": 0.99,
            "meta": {
                "matched_by": "eli.self_repair_playbook_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "self_improvement",
            },
        }

    if re.search(r"\bself[- ]?fix\b|\bfix\s+yourself\b", low):
        return {
            "action": "SELF_PATCH",
            "args": {},
            "confidence": 0.96,
            "meta": {
                "matched_by": "eli.self_fix_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "self_improvement",
            },
        }

    if re.search(r"\bwhat\s+have\s+you\s+improved\b|\bself[- ]?improvement\s+status\b", low):
        return {
            "action": "SELF_ANALYZE",
            "args": {},
            "confidence": 0.98,
            "meta": {
                "matched_by": "eli.self_improvement_status_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "self_improvement",
            },
        }

    # SELF_UPGRADE must be claimed here, before the portable open-app contract, or "run upgrade"
    # becomes OPEN_APP for a phantom app. "run bash upgrade.sh" isn't caught: the regex needs
    # 'upgrade' directly after run/the/a/self, so an interposed binary stays on the shell path.
    if re.search(
        r"\b(?:run|start|perform|execute|do|go)\s+(?:an?\s+|the\s+)?(?:self[- ]?)?upgrade\b"
        r"|\bself[- ]?upgrade\b"
        r"|\bupgrade\s+(?:yourself|eli|the\s+system)\b"
        r"|\bupgrade\s+your\s+(?:own\s+)?(?:code|self)\b",
        low,
    ):
        return {
            "action": "SELF_UPGRADE",
            "args": {"request": raw},
            "confidence": 0.99,
            "meta": {
                "matched_by": "eli.self_upgrade_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "self_improvement",
            },
        }

    # A request to see the findings from the last code audit again ("pull up each of those 59
    # findings once more") replays the stored audit, not chat, where the model invented a fake file
    # tree of files that do not exist. Only fires when a stored audit actually exists, so an unrelated
    # "pull those up" cannot route here.
    try:
        from eli.runtime import code_examiner as _ce_recall
        if _ce_recall.is_recall_request(raw) and _ce_recall.get_last_audit():
            return {
                "action": "EXAMINE_CODE",
                "args": {"request": raw},
                "confidence": 0.97,
                "meta": {
                    "matched_by": "eli.examine_code_recall",
                    "need_grounding": True,
                    "allow_chat_without_evidence": False,
                    "task_family": "grounded_audit",
                },
            }
    except Exception:
        pass

    # EXAMINE_CODE: "examine/review/scan <file|module|codebase> for errors". Needs a code word, .py
    # path or eli.* module so "examine this image" doesn't match. GUI/runtime audits with proof go to
    # the core router's gui_audit_proof contract instead.
    _runtime_audit_proof = bool(
        re.search(r"\b(prove|proof|evidence|timestamps?|wiring|hooks?)\b", low)
        and re.search(r"\b(gui|runtime|wiring|pipeline|hooks?)\b", low)
    )
    # a diff of files is SHOW_DIFF's (core router), not an examination of them
    _asks_a_diff = bool(re.search(r"\b(?:diff|differences?\s+between|changes\s+between|compare)\b", low))
    if (
        not _runtime_audit_proof
        and not _asks_a_diff
        and (
        re.search(
            r"\b(examine|review|inspect|scan|audit)\b[^.?!]*"
            r"\b(code|codebase|repo|repository|file|files|module|modules|script|scripts|"
            r"function|functions|class|classes|for\s+errors|for\s+bugs|for\s+issues|"
            r"for\s+mistakes)\b",
            low,
        )
        or re.search(r"\b(examine|review|inspect|scan|audit|check)\b[^.?!]*\.py\b", low)
        or re.search(r"\b(examine|review|inspect|scan|audit|check)\b[^.?!]*\beli(?:\.\w+)+\b", low)
        # "check/look at <files|the code> for errors/issues" and the bare "is there any issues with the
        # files/code?" must run the real examiner, not FILE_AUDIT (a directory counter whose output got
        # confabulated into invented buggy files). Needs an error word and a code/file word, so casual
        # "issues" don't match.
        or (
            # NOT a fix request — "fix the bugs in foo.py" is FIX_FILE's job; this is the
            # diagnose-only "is there any issues with the files?" form.
            not re.search(r"\b(fix|repair|correct|patch|solve|resolve|debug|rewrite)\b", low)
            # The issue word and the code word must be about each other, not just co-occur ("i do not
            # mean close files (the app's name)... that is the problem" went to a code examination at
            # 0.96). Requiring them within a few words keeps "any issues with the files?" and "the code has
            # bugs" and drops incidental co-occurrence.
            and re.search(
                r"\b(issues?|errors?|bugs?|problems?|mistakes?|faults?)\b\s+"
                r"(?:\w+\s+){0,3}?(?:with|in|inside|across|throughout|within)\s+"
                r"(?:the\s+|my\s+|your\s+|our\s+|any\s+|these\s+|those\s+|that\s+|this\s+)?"
                r"(?:file|files|code|codebase|module|modules|script|scripts|"
                # "code of conduct" is a document in this very repo, not code.
                r"function|functions|class|classes)\b(?!\s+of\s+conduct)"
                r"|\b(?:file|files|code|codebase|module|modules|script|scripts|"
                r"function|functions|class|classes)\b\s+"
                r"(?:\w+\s+){0,2}?\b(issues?|errors?|bugs?|problems?|mistakes?|faults?)\b"
                r"|\.py\b",
                low,
            )
        )
        or re.search(r"\b(check|look\s+(?:at|over|through)|go\s+through)\b[^.?!]*"
                     r"\b(file|files|code|codebase|module|modules|script|scripts)\b[^.?!]*"
                     r"\b(error|errors|bug|bugs|issue|issues|problem|problems|wrong|broken)\b", low)
        )
    ):
        return {
            "action": "EXAMINE_CODE",
            "args": {"request": raw},
            "confidence": 0.96,
            "meta": {
                "matched_by": "eli.examine_code_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_audit",
            },
        }

    return None

# --- end ELI high-priority self-improvement route guard ---


# --- ELI runtime/cognition/failure high-priority route guard ---
def _eli_runtime_cognition_failure_guard(text):
    raw = str(text or "")
    low = raw.lower().strip()
    # Route on what the user asked, not on words they're quoting back. The _explain_prior_claim
    # guard says this but never reached this trigger: a user quoting ELI's sentence ("...you might
    # push toward more layers...") fired GPU_STATUS at 0.995 on "layers" + "you" inside the quote.
    _asked = _eli_outside_quotes(low)

    # "how many layers are you using?" means GPU offload layers, live in the GPU_STATUS snapshot.
    # Left to chat it made things up. The old trigger fired on any talk about layers, and a trailing
    # "?" turned a fifty-word rant into a request. Now the turn must be short, or its last sentence
    # must mention the runtime.
    _last_sentence = (re.split(r"(?<=[.!?])\s+", _asked.strip()) or [""])[-1]
    _ends_in_relevant_question = bool(
        _asked.rstrip().endswith("?")
        and (len(_asked.split()) <= 25
             or re.search(r"\b(?:gpu|vram|cuda|nvidia|layers?|offload\w*|batch|"
                          r"context|ctx|memory|model)\b", _last_sentence))
    )
    # The user stating ELI's runtime state already knows it: that's a correction, not a request for
    # a report. Answering it with the same report made "you are on 28 layers - why are you still
    # lying?" come back as a telemetry dump.
    _asserts_state = bool(re.search(
        r"\b(?:you(?:'re| are) (?:only )?(?:on|at|running|using)\s+\d+"
        r"|(?:the )?gpu is (?:not|n'?t)\b"
        r"|(?:is )?not back to full"
        r"|you(?:'re| are) (?:still )?(?:lying|wrong|mistaken|not right))\b",
        _asked))

    _is_request = bool(
        _ends_in_relevant_question
        or re.match(r"^\s*(?:how|what|which|why|is|are|am|do|does|did|can|could|"
                    r"should|show|tell|check|report|give|list|status)\b", _asked)
        or re.search(r"\b(?:show me|tell me|check|report on|how many|how much|"
                     r"what(?:'s| is| are)|status of)\b", _asked)
        # A bare noun-phrase command is still a request: "gpu status", "vram usage", "gpu stats" are
        # the documented activation phrases and have no interrogative. Length separates them from
        # conversation; nobody chats in four words about offload layers.
        or (len(_asked.split()) <= 5
            and re.search(r"\b(?:status|stats|usage|diagnostics?|temp|temperature)\b", _asked))
    )
    # An explicit brush-off is not a request, whatever else the sentence contains.
    _dismissed = bool(re.search(
        r"\b(?:i don'?t care|don'?t care about|not asking about|forget (?:that|it)|"
        r"never ?mind|stop (?:that|it)|i know that|not what i (?:asked|meant|said))\b",
        _asked))

    _gpu_layer_question = (
        _is_request and not _dismissed and not _asserts_state
        and re.search(r"\blayers?\b", _asked)
        and re.search(r"\b(you|your|offload\w*|gpu|vram|cuda|model|running|loaded|use|using)\b", _asked)
        and not re.search(r"\b(image|photo|photoshop|after\s?effects|composit\w*|design|"
                          r"canvas|css|z-index|onion\s?skin)\b", _asked)
    )
    if re.search(r"\bnvidia-smi\b", _asked) or _gpu_layer_question or (
        _is_request and not _dismissed and not _asserts_state
        and re.search(r"\b(gpu|vram|cuda|nvidia)\b", _asked)
        # "free/available/left/spare" were missing, so "how much vram is free" fell to CHAT and was
        # answered from imagination. Safe to widen now that the branch requires an actual request.
        and re.search(r"\b(stat|stats|status|diagnostic|diagnostics|usage|using|use|memory|"
                      r"performance|running on|running|offload\w*|accelerat\w*|"
                      # "used" was absent, so "is the gpu being used?" -- as
                      # plain a status question as exists -- fell through to
                      # CHAT and got answered from imagination.
                      "used|"
                      r"free|available|left|spare|"
                      r"tell me what it means|temp|temperature|how hot)\b", _asked)
    ):
        # A GPU question that also names other runtime dimensions needs the full runtime
        # evidence; GPU_STATUS covers the GPU only.
        _final_sentence = re.split(r"(?<=[.!?])\s+", _asked.strip())[-1]
        _other_dims = set(re.findall(
            r"\b(cpu|ram|ctx|context|batch|btch|threads?|settings?|params?|"
            r"parameters?|config(?:uration)?)\b", _final_sentence))
        if _other_dims and not re.search(r"\bnvidia-smi\b", _asked):
            return {
                "action": "RUNTIME_STATUS",
                "args": {"question": raw},
                "confidence": 0.995,
                "meta": {
                    "matched_by": "eli.runtime_status_broad_settings_guard",
                    "need_grounding": True,
                    "allow_chat_without_evidence": False,
                    "task_family": "grounded_status",
                    "response_contract": "quick_direct_nonquick_persona_synthesis",
                },
            }
        return {
            "action": "GPU_STATUS",
            "args": {"question": raw, "explain": True},
            "confidence": 0.995,
            "meta": {
                "matched_by": "eli.gpu_status_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_diagnostic",
            },
        }

    if re.search(r"\b(full\s+)?runtime\s+audit\b", low) or re.search(r"\bwhat'?s?\s+actually\s+(broken|missing)\b", low):
        return {
            "action": "RUNTIME_AUDIT",
            "args": {},
            "confidence": 0.99,
            "meta": {
                "matched_by": "eli.runtime_audit_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_audit",
            },
        }

    if re.search(r"\bcognition\s+pipeline\b", low) or re.search(r"\binput\s+to\s+output\b", low):
        return {
            "action": "EXPLAIN_COGNITION_RUNTIME",
            "args": {},
            "confidence": 0.99,
            "meta": {
                "matched_by": "eli.cognition_pipeline_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_audit",
            },
        }

    # SELF_ANALYZE inspects ELI's OWN failure log — so require a self-reference
    # ("your/you/eli"), not a bare "recent failures" / "root cause" which over-matched
    # questions about the user's own code/project.
    if (re.search(r"\b(your|you'?ve had|eli'?s)\s+(?:recent\s+)?failures?\b", low)
            or re.search(r"\bactual root cause of (?:your|eli'?s|the last)\b", low)
            or re.search(r"\banalyse?\s+(?:your|eli'?s)\s+(?:own\s+)?(?:recent\s+)?failures?\b", low)):
        return {
            "action": "SELF_ANALYZE",
            "args": {},
            "confidence": 0.98,
            "meta": {
                "matched_by": "eli.failure_analysis_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "self_improvement",
            },
        }

    return None

# --- end ELI runtime/cognition/failure high-priority route guard ---


# =============================================================================

# Identity / name-source route fix: a single closure-safe install.

# Final runtime status route contract: runtime identity/status questions route to grounded runtime
# evidence. Quick mode may show compact output downstream; non-quick modes synthesise.

# Final memory question route contract: separates "what do you know about me" from "explain memory
# internals".

# PERSONAL_MEMORY_SUMMARY compatibility: it is now a first-class evidence action. Keep this wrapper
# only to preserve the final route binding and metadata hygiene; don't collapse summary requests
# back into the old deep-response override.

# Identity scope contract: USER_IDENTITY_SUMMARY must carry the original question and not be treated
# as a generic "what do you know about me" memory/profile dump.
def _eli_identity_scope_for_text(text):
    from eli.execution.route_contracts import identity_scope
    return identity_scope(text)
# =============================================================================

# Profile memory scope contract. Separates identity-only questions, memory-presence questions,
# generic profile inventory, explicit preference detail requests, and full profile dumps. No user
# names hardcoded.

def _eli_profile_scope_low(text):
    import re as _re
    return _re.sub(r"\s+", " ", str(text or "").strip().lower())

def _eli_profile_scope_result(action, question, scope, confidence=0.995, matched_by="profile.scope_contract"):
    return {
        "action": action,
        "args": {
            "question": str(question or ""),
            "profile_scope": scope,
        },
        "confidence": confidence,
        "meta": {
            "matched_by": matched_by,
            "profile_scope_contract": scope,
            "active_user_scoped": True,
            "forbid_schema_dump": True,
            "forbid_reflection_spam": True,
            "forbid_news_rows": True,
        },
    }

def _eli_is_explicit_preference_request(low):
    import re as _re
    if _re.search(r"\b(show|list|summari[sz]e|tell me|what are|display|read)\b.{0,80}\b(my|stored|profile)?\s*preferences\b", low):
        return True
    if _re.search(r"\bmy stored preferences\b", low):
        return True
    if low in {"my preferences", "show preferences", "show my preferences"}:
        return True
    return False

def _eli_is_generic_profile_inventory(low):
    return low in {
        "what do you know about me",
        "what do you remember about me",
        "what do you remember of me",
        "do you know me",
    }

def _eli_is_full_profile_dump(low):
    import re as _re
    if _re.search(
        r"\b(dump|show|print|read|display)\b.{0,80}\b(full|complete|entire|all)\b.{0,80}\b(profile|personal memory|memory profile)\b",
        low,
    ):
        return True
    if _re.search(
        r"\b(give me everything|everything you know|don'?t summarise|don'?t summarize|"
        r"do not summarise|do not summarize|without summar(?:y|ies|izing|ising)|"
        r"no summary|not summarise|not summarize)\b",
        low,
    ) and _re.search(
        r"\b(about me|from memory|what you know|what you remember|my memory|personal memory|who am i)\b",
        low,
    ):
        return True
    return False
# =============================================================================

# Memory count grounded synthesis contract: count questions are evidence-backed. Quick mode may
# direct-return; non-quick modes synthesise from compact evidence and validate the answer.

def _eli_is_memory_count_question(text):
    """A count of ELI's own memories: its store's words ("memory rows", "stored memories"), or
    "memories" asked of ELI ("how many memories do you have"). "how many memories can a human brain
    hold" is not one."""
    import re
    low = str(text or "").strip().lower()
    return bool(
        re.search(r"\b(how many|number of|count)\b.{0,80}\b(memory entries|stored memories|memory rows)\b", low)
        or (re.search(r"\b(how many|number of|count)\b.{0,80}\bmemories\b", low) and _eli_about_eli(text))
        or re.fullmatch(r"(memory count|count memories|count memory|memories count|memory rows)", low)
    )

# Recent memory processing route: "what memories have you been processing lately?" is not CHAT. It
# needs grounded memory/runtime evidence or the model invents fake memory activity.
import re as _eli_recent_mem_re


from eli.utils.log import get_logger
log = get_logger(__name__)

def _eli_recent_memory_processing_question(text: object) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False

    # Preserve existing count-only route.
    if _eli_recent_mem_re.search(r"\b(how many|count|number of)\b.{0,40}\b(memories|memory rows|memory entries)\b", low):
        return False

    # Looking something up in memory, or asking about the user's own life, is recall, not the memory
    # store's activity: "look in your memories for the latest series i have been watching" got
    # a count of memory rows.
    if (_eli_recent_mem_re.search(r"\b(?:check|search|look|dig|go|scan)\b.{0,24}\b(?:memor(?:y|ies)|records|logs|history|notes)\b"
                                  r".{0,16}\b(?:for|and|to|about|what|when|which|if)\b", low)
            or _eli_recent_mem_re.search(r"\bi(?:'ve|'m|\s+have|\s+am|\s+was|\s+had)?(?:\s+been)?\s+"
                                         r"(?:watch|said|told|mention|ask|play|read|listen|work)\w*", low)):
        return False

    # "processed"/"processing" are deliberately not memory terms. They're also in recent_terms
    # below, so counting them here collapsed the two-signal AND at the end into a bare "processing"
    # + "you" test and routed complaints ("you're not processing my questions properly, are you
    # okay?") to a SQLite memory inventory. Explicit patterns below still catch "what memories have
    # you been processing".
    memory_terms = _eli_recent_mem_re.search(
        r"\b(memories|memory|remembered|remembering|learning|stored|recalled|recall)\b",
        low,
    )
    recent_terms = _eli_recent_mem_re.search(
        r"\b(lately|recently|latest|last|currently|been|processing|working on|what .* processing)\b",
        low,
    )

    patterns = [
        r"\bwhat\s+memories\s+have\s+you\s+been\s+processing\b",
        r"\bwhat\s+memory\s+have\s+you\s+been\s+processing\b",
        r"\bwhat\s+have\s+you\s+been\s+remembering\b",
        r"\bwhat\s+have\s+you\s+remembered\s+(?:recently|lately)\b",
        r"\bwhat\s+have\s+you\s+been\s+learning\s+(?:recently|lately)\b",
        r"\bwhat\s+memories\s+(?:did|do)\s+you\s+(?:process|have)\s+(?:recently|lately)\b",
        r"\bwhat\s+recent\s+memories\b",
        r"\bshow\s+(?:me\s+)?recent\s+memories\b",
        r"\blatest\s+memory\s+(?:activity|processing|updates)\b",
        r"\brecent\s+memory\s+(?:activity|processing|updates)\b",
    ]
    if any(_eli_recent_mem_re.search(p, low) for p in patterns):
        return True

    return bool(memory_terms and recent_terms and "you" in low)

# Self-report recent updates route: questions about what updates/checks happened must not fall into
# generic CHAT, or GGUF invents plausible maintenance activity.


# "What have you been doing?" is two questions. After "what updates have you made to the code"
# it's the maintenance question this contract is for; mid-chat about Oblivion it's small talk and
# a git/runtime dump is worse than the invented activity it prevents. Bare activity phrasings
# need a development subject somewhere in the question; ones that name it themselves don't.
from eli.execution.routing.memory_runtime import (  # noqa: F401  (moved there so intent stages can share them)
    _BARE_ACTIVITY_PHRASES,
    _DEV_UPDATE_SUBJECT_RE,
    _MEMORY_MACHINERY_RE,
    _RECALL_CONTENT_RE,
    _asks_for_remembered_content,
    _eli_activity_question_is_about_dev_work,
    _eli_memory_runtime_route_lock_should_trigger,
)


def _eli_self_report_recent_updates_question(text):
    low = str(text or "").strip().lower()
    if not low:
        return False

    self_ref = any(x in low for x in (
        "eli",
        "yourself",
        "about yourself",
        "your runtime",
        "your system",
    ))

    update_ref = any(x in low for x in (
        "what updates",
        "which updates",
        "updates and checks",
        "checks have been",
        "checks were",
        "performed as of late",
        # NOTE: bare "as of late" removed — it fires on casual idioms like
        # "are you saying i'm obsessive these days as of late".
        # "performed as of late" above is specific enough.
        "recent checks",
        "routine updates",
        "recent updates",
    )) or any(p in low for p in _BARE_ACTIVITY_PHRASES)

    if not _eli_activity_question_is_about_dev_work(low):
        return False

    return bool(update_ref and (self_ref or " you " in f" {low} " or " your " in f" {low} "))


# GUI audit actual-scan proof route: catches direct "did you actually scan/read the file in full"
# probes.

def _eli_gui_audit_actual_scan_v2(text):
    q = " ".join(str(text or "").lower().split())
    if not q:
        return False

    file_hit = any(x in q for x in (
        "eli/gui/main_window",
        "gui_eli_original",
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


# Memory-runtime route lock (ELI_MEMORY_RUNTIME_ROUTE_LOCK_V1): architecture/control questions
# about memory are grounded telemetry and must not be stolen by generic CHAT, OPEN_APP or
# personal-memory routing. It only guarantees the right evidence action, it doesn't answer.


def _eli_memory_runtime_route_lock_result(raw):
    return {
        "action": "EXPLAIN_MEMORY_RUNTIME",
        "args": {"question": str(raw or ""), "detail": "full"},
        "confidence": 0.995,
        "meta": {
            "matched_by": "eli.memory_runtime_route_lock_v1",
            "need_grounding": True,
            "allow_chat_without_evidence": False,
            "task_family": "memory_runtime",
            "response_contract": "canonical_grounded_memory_runtime_no_raw_gguf",
        },
    }
# =============================================================================


# Final route wrapper (ELI_PATCH_FINAL_PERSONAL_MEMORY_ROUTE_PRECEDENCE_AFTER_ROUTE_LOCK_20260511),
# installed after the late memory-runtime locks. Phrase logic is in eli.execution.route_contracts;
# this stays at EOF only to keep precedence over legacy route wrappers.


# Multi-PDF route contract wrapper. Existing branches call _extract_pdf_path(raw), which
# returns only the first PDF; _extract_pdf_paths(raw) returns all. Keeps route behaviour and adds
# paths=[...] to ANALYZE_PDF args. Also a standalone helper for the flattened dispatch.
def _eli_phase11_enrich_pdf_route(raw, result):
    try:
        if not isinstance(result, dict):
            return result

        action = str(result.get("action") or "").upper().strip()
        if action != "ANALYZE_PDF":
            return result

        args = result.setdefault("args", {})
        if not isinstance(args, dict):
            return result

        text = str(raw or "")
        if ".pdf" not in text.lower():
            return result

        paths = []
        try:
            paths = list(_extract_pdf_paths(text))
        except Exception:
            paths = []

        if not paths:
            return result

        args["path"] = str(args.get("path") or paths[0])
        args["paths"] = paths

        meta = result.setdefault("meta", {})
        if isinstance(meta, dict):
            entities = meta.setdefault("entities", {})
            if isinstance(entities, dict):
                entities["path"] = args["path"]
                entities["paths"] = paths
            meta["multipdf_count"] = len(paths)
            # ELI_PHASE33_MULTIPDF_IDEMPOTENT
            _phase11_matched_by = str(meta.get("matched_by") or "analyze.pdf")
            if "+phase11_multipdf" not in _phase11_matched_by:
                meta["matched_by"] = _phase11_matched_by + "+phase11_multipdf"
            else:
                meta["matched_by"] = _phase11_matched_by

        return result
    except Exception:
        return result


# Canonical public router surface (ELI_PHASE33_CANONICAL_PUBLIC_ROUTER_SURFACE_EXPORT). The
# exported surfaces had drifted (route/route_intent latest, route_command partly stale,
# parse_command/classify more so). Aliases must not capture old wrappers: until this file is
# flattened every exported surface resolves to the final route() below.
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


# Flattened canonical dispatch (ELI_PHASE38_FLATTENED_CANONICAL_DISPATCH_V1): one explicit
# pipeline instead of the nested wrapper chain, same semantics and stage order. It shadows the
# old wrapper blocks rather than deleting them; prune once parity is proven.

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


def _eli_phase38_route_precedence_contract(raw):
    try:
        from eli.execution.route_contracts import classify_precedence_route
        return classify_precedence_route(raw)
    except Exception:
        return None


def _eli_phase38_frontier_status_contract(raw):
    import re as _re

    low = _re.sub(r"\s+", " ", str(raw or "").lower()).strip()
    if not low:
        return None

    if _re.search(
        r"\b("
        r"frontier status"
        r"|full system (?:status|audit|wiring|matrix)"
        r"|cross[- ]system (?:status|audit|wiring|matrix)"
        r"|full (?:project|eli) (?:audit|wiring|matrix)"
        r"|memory.*self[- ]aware.*proactive.*image.*world.*labs"
        r"|chat flow.*memory.*self.*proactive.*image.*world.*labs"
        r")\b",
        low,
    ):
        return {
            "action": "FRONTIER_STATUS",
            "args": {"question": str(raw or "")},
            "confidence": 0.997,
            "meta": {
                "matched_by": "eli.frontier_status_route_contract",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_audit",
                "response_contract": "quick_direct_nonquick_persona_synthesis",
            },
        }
    return None


def _eli_phase39_identity_audit_contract(raw):
    import re as _re

    low = _re.sub(r"\s+", " ", str(raw or "").lower()).strip()
    if not low:
        return None

    if _re.search(
        r"\b("
        r"eli identity audit"
        r"|classif(?:y|ication) (?:eli|yourself|you)"
        r"|what (?:exactly )?is eli"
        r"|what should eli be classified as"
        r"|what are you classified as"
        r"|verified (?:eli )?(?:identity|classification) audit"
        r"|full (?:verified )?(?:eli )?(?:identity|classification) audit"
        r")\b",
        low,
    ):
        return {
            "action": "ELI_IDENTITY_AUDIT",
            "args": {"question": str(raw or "")},
            "confidence": 0.997,
            "meta": {
                "matched_by": "eli.identity_audit_route_contract",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_identity_audit",
                "response_contract": "verified_local_classification_matrix",
            },
        }
    return None


def _eli_phase38_runtime_status_or_name_source_contract(raw):
    import re as _re

    low = _re.sub(r"\s+", " ", str(raw or "").lower()).strip()

    runtime_status_query = (
        ("who are you" in low or "what are you" in low)
        and (
            "actually running" in low
            or "running on right now" in low
            or "model" in low
            or "context size" in low
            or "gpu layers" in low
            or "everything" in low
        )
    )

    if runtime_status_query:
        return {
            "action": "RUNTIME_STATUS",
            "args": {"question": str(raw or "")},
            "confidence": 0.995,
            "meta": {
                "matched_by": "eli.final_runtime_status_route_contract",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_status",
                "response_contract": "quick_direct_nonquick_persona_synthesis",
            },
        }

    # The user's own name, in words: "where is the file named report.pdf located", "which file has the
    # username in it" and "how do you know the name of that song" got the audit of where ELI keeps
    # the user's name (route_contracts.asks_where_the_name_comes_from).
    from eli.execution.route_contracts import asks_where_the_name_comes_from
    if asks_where_the_name_comes_from(raw):
        return {
            "action": "NAME_SOURCE_AUDIT",
            "args": {"question": str(raw or "")},
            "confidence": 0.99,
            "meta": {
                "matched_by": "eli.final_name_source_audit_route_contract",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_audit",
            },
        }

    return None


def _eli_phase38_final_memory_question_contract(raw):
    import re as _re

    low = _re.sub(r"\s+", " ", str(raw or "").lower()).strip()

    _mem_kw = ("memory" in low or "memori" in low)
    # Compliments ("how well your memory is working") are casual CHAT — not
    # requests for the memory-internals audit (which dumps grounded counts).
    _memory_compliment = bool(
        _re.search(
            r"\b(how well|scary how well|pretty well|so well|working well|works well|"
            r"working great|memory is working|remembering well)\b",
            low,
        )
        or _re.search(r"\b(good job|well done|impressive|scary)\b.{0,40}\b(memory|remembering)\b", low)
        or _re.search(r"\bhow\s+well\b.{0,40}\b(memory|remembering)\b", low)
    )
    # This report is about ELI's own memory. "how does memory work in the brain" and "what is a
    # knowledge graph" are questions about the world and got ELI's table counts.
    _about_eli = bool(_re.search(r"\b(?:you|your|yours|yourself|eli|eli's)\b", low))
    _other_subject = (not _about_eli) and bool(_re.search(
        r"\b(?:in the brain|the brain|brains|human|humans|people|person's|children|elderly|animals?|goldfish|"
        r"muscle memory|computer memory|in (?:python|linux|windows|java|rust|c\+\+|general)|psycholog\w*|neuro\w*)\b", low))
    _mechanism = (r"(?:faiss|fts5?|knowledge graphs?|hyde|rag pipelines?|vector stores?|embedders?|"
                  r"vector index(?:es)?|dag pipelines?)")
    _general_definition = (not _about_eli) and bool(_re.search(
        r"\b(?:what(?:'s| is| are)|whats|define|explain what|tell me what)\s+(?:(?:a|an|the)\s+)?" + _mechanism +
        r"(?:\s+(?:is|are|used for|for|in general|exactly|actually|anyway|mean|means))*\s*[?.!]*$"
        r"|\b(?:a|an)\s+" + _mechanism + r"\b", low))
    from eli.execution.routing.memory_runtime import _not_about_the_machinery
    asks_memory_internals = (
        not _memory_compliment
        and not _other_subject
        and not _general_definition
        and not _asks_for_remembered_content(low)
        and not _not_about_the_machinery(raw)
        and (
        "memory system" in low
        or "memory internals" in low
        or "memory pipeline" in low
        or "memory architecture" in low
        or ("which files" in low and ("db tables" in low or "database" in low or "tables" in low))
        or ("which db tables" in low)
        or ("which functions" in low and _mem_kw)
        # "how/explain/describe/walk me through … your memory … work/store/recall/pipeline …"
        or (
            _mem_kw
            and _re.search(r"\b(how|explain|describe|walk me through|break ?down|outline|detail)\b", low)
            and not _re.search(r"\bhow\s+well\b", low)
            and _re.search(
                r"\b(work|works|working|function|functions|operate|store|storage|stored|"
                r"recall|retriev|pipeline|architecture|internal|internally|flow|mechanism|"
                r"start to finish|end to finish|end to end)\b", low)
        )
        # Mechanism / recall+storage phrasing — common as a follow-up that omits
        # the word "memory" entirely ("the full info, all paths, databases,
        # mechanism of recall and storage, everything").
        or _re.search(r"\bmechanism(s)?\s+of\s+(recall|storage|retrieval|memory)", low)
        or _re.search(r"\b(recall and storage|storage and recall|store and recall|recall and store)\b", low)
        or ("all paths" in low and ("database" in low or "db" in low or "sqlite" in low))
        or "all the databases" in low
        or "all databases" in low
        # Explicit mechanism names → unmistakably about the retrieval/storage stack.
        # "kg" and bare "rag" were dropped: ELI's own log line "kg sync complete" matched them
        # and misrouted pasted logs to EXPLAIN_MEMORY_RUNTIME.
        or _re.search(r"\b(faiss|fts5?|knowledge graph|hyde|rag pipeline|"
                      r"vector store|embedder|vector index|dag pipeline)\b", low)
        )
    )

    asks_personal_memory = (
        "what do you know about me" in low
        or "what you know about me" in low
        or "what have you stored about me" in low
        or "what you actually remember" in low
        or "remember about me" in low
        or ("most recent things" in low and "stored" in low and "me" in low)
        or "patterns have you detected" in low
        or "how i interact with you" in low
        # "what do you know about my interests/habits/personality...", "tell me about my research" are
        # personal-knowledge questions and go to the clean PERSONAL_MEMORY_SUMMARY (which excludes the
        # news cache). They fell to MEMORY_RECALL, which surfaced news digests and synthesised a
        # 39k-char prompt into a lone "-".
        or bool(_re.search(
            r"\b(?:what (?:do|can) you know|what do you (?:remember|recall)|"
            r"do you (?:know|remember) (?:anything|much|something)|"
            r"what have you (?:learned|gathered|got))\b"
            r".{0,40}\babout (?:me\b|my " + _PM_PERSON_ATTR + r")",
            low,
        ))
        or bool(_re.search(
            r"\btell me (?:more )?(?:about|what you know about)\s+(?:me\b|my "
            + _PM_PERSON_ATTR + r")",
            low,
        ))
    )

    if asks_memory_internals and asks_personal_memory:
        return {
            "action": "PERSONAL_MEMORY_DEEP_EXPLAIN",
            "args": {"question": str(raw or "")},
            "confidence": 0.995,
            "meta": {
                "matched_by": "eli.final_memory_hybrid_route_contract",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "personal_memory",
                "forbid_schema_dump": True,
                "forbid_reflection_spam": True,
                "forbid_news_rows": True,
                "response_contract": "quick_direct_nonquick_persona_synthesis",
            },
        }

    if asks_memory_internals:
        return {
            "action": "EXPLAIN_MEMORY_RUNTIME",
            "args": {"question": str(raw or "")},
            "confidence": 0.995,
            "meta": {
                "matched_by": "eli.final_memory_internals_route_contract",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_audit",
            },
        }

    if asks_personal_memory:
        return {
            "action": "PERSONAL_MEMORY_SUMMARY",
            "args": {"question": str(raw or "")},
            "confidence": 0.995,
            "meta": {
                "matched_by": "eli.final_personal_memory_summary_route_contract",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "personal_memory",
                "forbid_schema_dump": True,
                "forbid_reflection_spam": True,
                "forbid_news_rows": True,
            },
        }

    return None


def _eli_phase38_persona_override_contract(raw):
    import re as _re

    low = _re.sub(r"\s+", " ", str(raw or "").lower()).strip(" .,!?:;")

    if low in {
        "elaborate more",
        "elaborate",
        "continue",
        "go on",
        "more detail",
        "explain more",
        "tell me more",
    }:
        return _mk(
            "CHAT",
            {
                "message": (
                    "Continue the immediately previous answer. "
                    "No role prefix. No HR filler. Stay in ELI's direct voice."
                )
            },
            0.95,
            matched_by="eli.final_followup_override",
            allow_chat_without_evidence=False,
        )

    if low in {
        "what's the story here we go",
        "whats the story here we go",
        "what's the story",
        "whats the story",
    }:
        return _mk(
            "CHAT",
            {
                "message": (
                    "Brief operational status in ELI's direct, dry voice. "
                    "One or two sentences max. Say what's running, call out anything broken or "
                    "degraded if relevant, say what's next if there is something. "
                    "No bullet points. No corporate framing. No \"I am happy to report\" filler. "
                    "Just the facts, ELI's way."
                )
            },
            0.95,
            matched_by="eli.final_story_status_override",
            allow_chat_without_evidence=False,
        )

    return None


def _eli_phase38_followup_passthrough_contract(raw):
    import re as _re

    low = _re.sub(r"\s+", " ", str(raw or "").lower()).strip(" .,!?:;")

    if low in {
        "elaborate more",
        "elaborate",
        "continue",
        "go on",
        "more",
        "more detail",
        "expand",
        "explain more",
        "tell me more",
        "go ahead",
        "go ahead please",
        "yeah go ahead",
        "please go ahead",
        "yes go ahead",
        "sure go ahead",
        "go",
        "proceed",
        "keep going",
        "be more in depth",
        "more in depth",
        "go deeper",
        "be more detailed",
    }:
        return _mk(
            "CHAT",
            {"message": str(raw or "")},
            0.90,
            matched_by="eli.followup.short_contextual",
            allow_chat_without_evidence=False,
        )

    # This is for short nudges ("be more in depth", "go deeper") that continue whatever ELI just
    # said. A full new sentence that happens to contain "in depth" is a fresh question, not a
    # continuation — "give me an in depth response about X" was matching here and getting the
    # quick, evidence-free follow-up treatment instead of a real answer. Gate it on brevity, the
    # same signal the literal short_contextual set above already relies on.
    from eli.runtime.control_contracts import is_identity_depth_followup
    if is_identity_depth_followup(low):
        return _mk(
            "CHAT",
            {"message": str(raw or "")},
            0.92,
            matched_by="eli.followup.identity_depth",
            allow_chat_without_evidence=True,
        )

    return None


def _eli_phase38_identity_contract(raw):
    import re as _re

    low = _re.sub(r"\s+", " ", str(raw or "").lower()).strip(" .,!?:;")

    # Symbolic-world / room questions go to CHAT so the persona handoff's live current room and
    # 9-room topology are used. They fell to the llm_intent fallback, which guessed GAZE_STATUS (no
    # world context), and the model defaulted to "Core Room".
    if _re.search(
        r"\b(?:what|which)\s+room\s+(?:are\s+you|you'?re|is\s+eli)\b"
        r"|\bwhere\s+are\s+you\s+(?:right\s+now|currently|at|in\s+your\s+world)\b"
        r"|\b(?:are\s+you\s+(?:still\s+)?(?:stuck\s+)?in|been\s+in)\s+(?:the\s+)?\w+(?:\s+\w+)?\s+room\b"
        r"|\byour\s+(?:favou?rite|other\s+favou?rite|other)\s+room\b"
        r"|\bwhich\s+room\b",
        low,
    ):
        return _mk(
            "CHAT",
            {"message": raw},
            0.97,
            matched_by="world.room_query",
            allow_chat_without_evidence=True,
        )

    if _re.search(
        r"\b(?:who are you|what are you(?!\s+\w)|what is your name|what's your name|"
        r"tell me about yourself|what do you know about yourself|"
        r"what do you know of yourself|what else can you tell me about yourself|"
        r"describe yourself)\b",
        low,
    ):
        # Persona/identity questions answered from ELI's character + memory.
        # SELF_REPORT (raw spec JSON) is wrong here — that's for runtime/model queries.
        if not _re.search(r"\b(model|running on|provider|context|gpu|llm|specs?|technical|runtime|layers|threads|batch)\b", low):
            return _mk(
                "CHAT",
                {"message": raw},
                0.99,
                matched_by="identity.persona_chat",
                allow_chat_without_evidence=True,
            )
        return _mk(
            "SELF_REPORT",
            {},
            0.99,
            matched_by="identity.final_self_report",
            allow_chat_without_evidence=False,
        )

    # "do you know who I am AND who you are" — a rhetorical/relational identity check that
    # wants a natural answer covering BOTH (who you are / I'm ELI), not a one-sided
    # user-identity summary dump.
    if (_re.search(r"\bwho\s+(?:i\s+am|am\s+i)\b", low)
            and _re.search(r"\bwho\s+(?:you\s+are|are\s+you)\b", low)):
        return _mk(
            "CHAT", {"message": raw}, 0.96,
            matched_by="identity.relational_both", allow_chat_without_evidence=True,
        )

    # Who the user is, their name ("and my name?" too, a bare name question after the topic was
    # raised: it fell to CHAT, where the critique replaced the stored name), whether ELI knows them,
    # and the report of what ELI keeps on them: one classifier decides all of it
    # (route_contracts.identity_question), here and nowhere else in the router.
    from eli.execution.route_contracts import identity_question
    asked = identity_question(raw)
    if asked is not None:
        return _mk(asked["action"], asked["args"], 0.99, matched_by=asked["matched_by"],
                   allow_chat_without_evidence=False)

    return None


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
        # A chained utterance was already split into MULTI_COMMAND by the prepass ("play X on
        # youtube AND close spotify"). Don't collapse it into one PLAY_MEDIA whose query swallows
        # "and close spotify"; let the executor run each segment in order.
        if isinstance(result, dict) and str(result.get("action") or "").upper() == "MULTI_COMMAND":
            return result
        # Already resolved to a real task lookup — don't let the "resume X" catch-all below
        # reinterpret it as media playback.
        if isinstance(result, dict) and str(result.get("action") or "").upper() == "RESUME_TASK":
            return result
        original = str(raw or "")
        low = re.sub(r"\s+", " ", original.lower()).strip(" .,!?:;")
        if not low:
            return result
        text = re.sub(r"^(?:eli|vera|computer|assistant|buddy)\s+", "", low).strip()

        def _target(value: str) -> str:
            target = re.sub(r"^(?:the|a|an)\s+", "", str(value or "").strip(" .,!?:;"))
            target = re.sub(r"(?:\s+com|\.com)\s*$", "", target, flags=re.I).strip()
            target = target.replace("prime video", "primevideo")
            target = target.replace("disney plus", "disneyplus")
            if re.search(r"youtube\s*(?:\.com|\s+com|website|web)\b", target, re.I):
                return "youtube website"
            if target in {"tv", "and tv", "television", "the tv"}:
                return "mpv"
            try:
                from eli.execution.media_runtime import normalize_streaming_target
                canon = normalize_streaming_target(target)
                if canon:
                    return canon
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            return target

        def _wants_browser_target(value: str) -> bool:
            return bool(re.search(
                r"\.com|\s+com\b|\bwebsite\b|\bweb(?:site)?\b", str(value or ""), re.I
            ))

        def _play(target: str, query: str, matched_by: str):
            # "play it on youtube": what was last played, or nothing to search for
            from eli.execution.routing.command_parser import what_was_played
            args = {"target": _target(target), "query": what_was_played(query)}
            if _wants_browser_target(target):
                args["browser"] = True
            from eli.execution.portable_intent_contract import media_kind
            if media_kind(args["query"]):
                args["kind"] = media_kind(args["query"])
            return _mk(
                "PLAY_MEDIA",
                args,
                0.97,
                matched_by=matched_by,
                entities=dict(args),
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

        _yt_com = r"youtube(?:\s+com|\.com|website|web(?:site)?)?"
        m = re.match(rf"^play\s+(.+?)\s+by\s+(.+?)\s+on\s+({_yt_com})\s*$", text)
        if m:
            return _play("youtube website", f"{m.group(1).strip()} by {m.group(2).strip()}",
                          "media.play_by_artist_on_youtube_com_contract")

        m = re.match(rf"^play\s+(.+?)\s+on\s+({_yt_com})\s*$", text)
        if m:
            tgt = m.group(2).strip()
            if re.search(r"youtube\s*(?:\.com|\s+com|website|web)", tgt, re.I):
                return _play("youtube website", m.group(1).strip(),
                            "media.play_on_youtube_com_contract")
            return _play(m.group(2), m.group(1).strip(), "media.play_query_on_target_contract")

        _streaming = (
            r"netflix(?:\.com|\s+com)?|prime(?:\s+video)?(?:\.com|\s+com)?|primevideo|amazon\s+prime|"
            r"disney(?:\+|\s*plus)?(?:\.com|\s+com)?|disneyplus|hulu(?:\.com|\s+com)?|"
            r"max(?:\.com|\s+com)?|paramount(?:\+|\s*plus)?(?:\.com|\s+com)?|"
            r"peacock(?:\.com|\s+com)?|appletv|apple\s+tv|plex(?:\.com|\s+com)?|"
            r"crunchyroll(?:\.com|\s+com)?|discovery(?:\+|\s*plus)?(?:\.com|\s+com)?|"
            r"tubi(?:\.com|\s+com)?|pluto(?:\.com|\s+com)?|twitch(?:\.com|\s+com)?|"
            r"sound\s*cloud(?:\.com|\s+com)?|apple\s+music|tidal(?:\.com|\s+com)?|"
            r"deezer(?:\.com|\s+com)?|amazon\s+music"
        )
        m = re.match(rf"^play\s+(.+?)\s+on\s+({_streaming})\s*$", text)
        if m:
            return _play(m.group(2), m.group(1), "media.play_on_streaming_contract")

        m = re.match(r"^play\s+(.+\s+by\s+.+)$", text)
        if m and not re.search(r"\bon\s+(?:youtube|spotify|soundcloud|mpv)\b", text):
            return _play("spotify", m.group(1), "media.play_song_by_artist_contract")

        # A "<title> by <artist>" with no "play" is read by its one test in the portable contract
        # (implied_song_by_artist), as a stage held to the fit test. Applied here as well, after the
        # stages, it played "my car was hit by a truck".

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
                # "open browser" listed "browser" as a known app name, so it
                # tried to launch a program called browser instead of opening
                # the user's default one.
                if verb in {"open", "launch", "start"} and name in {
                    "browser", "web browser", "internet browser",
                }:
                    return _mk("OPEN_BROWSER", {}, 0.98,
                               matched_by="app.wake_prefix_contract.browser")
                if verb in {"open", "launch", "start"} and name in WEBSITE_ALIASES:
                    return _mk(
                        "OPEN_URL",
                        {"url": WEBSITE_ALIASES[name]},
                        0.98,
                        matched_by="app.wake_prefix_contract.web_alias",
                    )
                action = "CLOSE_APP" if verb in {"close", "quit", "kill", "exit"} else "OPEN_APP"
                if action == "CLOSE_APP":
                    from eli.execution.portable_intent_contract import site_named
                    _site = site_named(name)
                    if _site:
                        return _mk(action, {"name": name, "target": name, "site": _site, "tab": True}, 0.98,
                                   matched_by="app.wake_prefix_contract.site")
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


def _eli_phase38_media_query_cleaner_post(result):
    try:
        if isinstance(result, dict) and str(result.get("action") or "").upper() == "PLAY_MEDIA":
            args = result.setdefault("args", {})
            query = str(args.get("query") or "")
            cleaned = _eli_mqc_clean_query(query)
            if cleaned:
                args["query"] = cleaned
                result.setdefault("meta", {})["query_cleaned_by"] = "eli.final_media_query_cleaner"
    except Exception:
        _SWLOG.debug("suppressed exception", exc_info=True)
    return result


def _eli_phase38_tiny_fragment_post(raw, result):
    import re as _re

    # A typed one-word message ("thanks", "cheers", "bye") is what the user meant to send; asking
    # them to say it again is for speech, where a word can be all that was caught.
    try:
        from eli.kernel.request_context import input_channel_var
        if input_channel_var.get() == "typed":
            return result
    except Exception:
        _SWLOG.debug("suppressed exception", exc_info=True)
    try:
        if isinstance(result, dict) and str(result.get("action") or "").upper() == "CHAT":
            low = _re.sub(r"\s+", " ", str(raw or "").lower()).strip(" .,!?:;")
            words = _re.findall(r"[a-z0-9']+", low)

            allowed_short = _re.search(
                r"\b("
                r"who are you|who am i|who made you|who built you|"
                r"what are you|what is this|what is it|"
                r"are you (?:sentient|alive|conscious|real|there|ok|okay)|"
                r"describe yourself|describe your|introduce yourself|tell me about yourself|"
                r"how many memories|"
                r"elaborate more|elaborate|continue|go on|more detail|expand|explain more|tell me more|"
                r"remember this|save this|help me|explain|why|how|what|where|when|who|"
                r"okay|ok|yes|no|nope|yep|yeah|sure|agreed|correct|exactly|right|fair|"
                r"nice|cool|great|perfect|good|got it|understood|alright|indeed|true|false|"
                r"hi|hello|hey|howya|hiya|yo|sup|afternoon|morning|evening|night|"
                r"for fucksake|fucksake|for fuck sake|jesus|jaysus|seriously|what the|"
                r"fuck off|piss off|cop on|come on|wise up"
                r")\b",
                low,
            )

            ends_in_terminator = bool(_re.search(r"[.?!]\s*$", str(raw or "").strip()))

            looks_fragmentary = (
                not ends_in_terminator
                and (
                    len(words) <= 1
                    or str(raw or "").strip().endswith("-")
                    or low in {"preview", "resil", "i u", "here i will", "find your mo"}
                )
            )

            # One word straight after ELI asked or offered something is the answer ("B", "2",
            # "tomorrow"), not a fragment to ask again about.
            if looks_fragmentary and not allowed_short:
                if str((result.get("meta") or {}).get("matched_by") or "").startswith("pending_proposal."):
                    return result
                try:
                    from eli.runtime.pending_proposal import get_follow_up as _open_follow_up
                    if _open_follow_up():
                        return result
                except Exception:
                    _SWLOG.debug("suppressed exception", exc_info=True)

            if looks_fragmentary and _re.fullmatch(
                r"(?:date|the\s+date|is\s+the\s+date|what\s+date|what\s+is\s+the\s+date|what's\s+the\s+date|today|what\s+day|what\s+days|their\s+date|tell\s+me\s+their\s+days)",
                low,
            ):
                return _mk("DATE", {}, 0.999, matched_by="system.date.fragment")

            if looks_fragmentary and _re.fullmatch(
                r"(?:time|the\s+time|what\s+time|what\s+is\s+the\s+time|what's\s+the\s+time)",
                low,
            ):
                return _mk("TIME", {}, 0.999, matched_by="system.time.fragment")

            if looks_fragmentary and low in {
                "date",
                "the date",
                "is the date",
                "what date",
                "what days",
                "what's the day",
                "what's the days",
                "what day",
                "what day is it",
                "what day is this",
                "their date",
                "tell me their days",
            }:
                return _mk("DATE", {}, 0.999, matched_by="system.date.fragment")

            if looks_fragmentary and low in {
                "time",
                "the time",
                "what time",
                "what is the time",
                "what's the time",
            }:
                return _mk("TIME", {}, 0.999, matched_by="system.time.fragment")

            if looks_fragmentary and not allowed_short:
                # install/download commands are valid 2-word actions — bypass
                # the fragment guard and let the remediation handler process them.
                _idm = _re.match(r"^(install|download|get|setup)\s+(\S+)", low)
                if _idm:
                    return _mk(
                        "CONFIRM_PENDING_REMEDIATION",
                        {"message": str(raw or "").strip()},
                        0.97,
                        matched_by="remediation.install_download_fragment_bypass",
                    )

                # Pending-repair confirmation bypass — if a repair plan is waiting
                # and the input contains a clear yes/no word (even with STT noise),
                # route to remediation instead of dropping as a fragment.
                try:
                    from eli.runtime import grounded_remediation as _gr_frag
                    if _gr_frag.get_pending():
                        if _re.search(r'\b(yes|confirm|confirmed|proceed|go ahead|do it)\b', low, _re.I):
                            return _mk(
                                "CONFIRM_PENDING_REMEDIATION",
                                {"message": str(raw or "").strip()},
                                0.95,
                                matched_by="pending_remediation.yes_intercept",
                            )
                        if _re.search(r'\b(no|cancel|abort|stop|never mind)\b', low, _re.I):
                            return _mk(
                                "CONFIRM_PENDING_REMEDIATION",
                                {"message": str(raw or "").strip()},
                                0.95,
                                matched_by="pending_remediation.no_intercept",
                            )
                except Exception:
                    _SWLOG.debug("suppressed exception", exc_info=True)

                grid_text = str(raw or "").strip().lower().replace("×", "x")
                grid_text = _re.sub(r"\btree\b", "3", grid_text)
                grid_text = _re.sub(r"\bthree\b", "3", grid_text)
                grid_text = _re.sub(r"\btwo\b", "2", grid_text)
                grid_text = _re.sub(r"\bfour\b", "4", grid_text)

                grid_m = _re.fullmatch(r"(\d{1,2})\s*(?:x|by)\s*(\d{1,2})", grid_text)
                if grid_m:
                    cols, rows = int(grid_m.group(1)), int(grid_m.group(2))
                    if 1 <= cols <= 8 and 1 <= rows <= 8:
                        return _mk(
                            "TILE_WINDOWS",
                            {"cols": cols, "rows": rows, "grid": [cols, rows]},
                            0.985,
                            matched_by="window.grid_followup",
                        )

                # The structured record belongs in the log and meta, not in `response`/`content`, which the
                # user reads and TTS speaks. A JSON blob there put {"event": "input_fragment_guard", ...} on
                # screen for a mistyped "ply". diagnostic_patterns.py and memory.py filter that string as a
                # workaround for a leak that shouldn't happen at source.
                _heard = str(raw or "").strip()
                _diag = {
                    "event": "input_fragment_guard",
                    "heard": _heard,
                    "routed_to_cognition": False,
                    "reason": "fragmentary_input",
                }
                msg = (f"I only caught \u201c{_heard}\u201d \u2014 could you say that again?"
                       if _heard else "I didn't catch that \u2014 could you say it again?")

                return _mk(
                    "NOOP",
                    {"message": msg, "response": msg, "content": msg,
                     "diagnostic": _diag},
                    0.999,
                    matched_by="eli.tiny_chat_fragment_guard",
                )

    except Exception:
        _SWLOG.debug("suppressed exception", exc_info=True)

    return result


def _eli_phase38_personal_memory_summary_compat_post(out):
    if isinstance(out, dict) and out.get("action") == "PERSONAL_MEMORY_SUMMARY":
        out = dict(out)
        meta = dict(out.get("meta") or {})
        meta["matched_by"] = "eli.personal_memory_summary_first_class"
        meta["forbid_schema_dump"] = True
        meta["forbid_reflection_spam"] = True
        meta["forbid_news_rows"] = True
        out["meta"] = meta
    return out


def _eli_phase38_identity_scope_post(raw, out):
    if isinstance(out, dict) and str(out.get("action") or "").upper() == "USER_IDENTITY_SUMMARY":
        out = dict(out)

        route_args = dict(out.get("args") or {})
        route_args["question"] = str(raw or "")
        route_args["identity_scope"] = _eli_identity_scope_for_text(raw)
        out["args"] = route_args

        meta = dict(out.get("meta") or {})
        meta["identity_scope_contract"] = route_args["identity_scope"]
        meta["forbid_profile_memory_dump"] = True
        meta["forbid_preferences"] = True
        out["meta"] = meta

    return out


log.debug("[ROUTER] router_enhanced module loaded — canonical dispatch pipeline active")

# =============================================================================
# End ELI_PHASE38_FLATTENED_CANONICAL_DISPATCH_V1
# =============================================================================

# Route priority pipeline (ELI_ROUTE_PRIORITY_PIPELINE_V1): an explicit priority pipeline that
# replaces nested phase-chain dispatch as the exported route surface.
try:
    if not globals().get("_ELI_ROUTE_PRIORITY_PIPELINE_V1"):
        _ELI_ROUTE_PRIORITY_PIPELINE_V1 = True

        def _eli_route_priority_stages():
            def _stage_precedence(text, *_a, **_k):
                return _eli_phase38_route_precedence_contract(text)

            def _stage_frontier_status(text, *_a, **_k):
                return _eli_phase38_frontier_status_contract(text)

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

            def _stage_identity_audit(text, *_a, **_k):
                return _eli_phase39_identity_audit_contract(text)

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
                    # "build me a script at 2am" asks for it then, not now.
                    return _eli_schedule_prepass(raw2) or {
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

            def _stage_resume_task_pre_route(text, *_a, **_k):
                resume_pre = globals().get("_eli_resume_task_pre_route")
                if callable(resume_pre):
                    try:
                        out = resume_pre(text)
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
                    # Intercept only a short, unambiguous reply. A real sentence that merely
                    # contains "fix" / "do not" ("you do not know my name?") must route normally;
                    # the broader search hijacked those for up to 10 minutes.
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

            def _stage_timestamp_and_shell_followup(text, *_a, **_k):
                """Real timestamp diagnostics and deictic shell follow-ups before
                pending-proposal 'yes' hijacks or OPEN_APP steals 'that command'."""
                try:
                    routed = _eli_shell_prepass(str(text or ""))
                    if isinstance(routed, dict) and routed.get("action"):
                        return routed
                except Exception:
                    _SWLOG.debug("suppressed exception", exc_info=True)
                return None

            def _stage_pending_proposal_confirm(text, *_a, **_k):
                """The user's message read against what ELI's last reply left open
                (eli.runtime.pending_proposal): a "yes" runs what was offered, a pick
                ("the second one", "do 1-3") runs those steps, an answer to a detail an
                action asked for completes that request. A decline clears it and falls
                through to chat."""
                try:
                    from eli.runtime import pending_proposal as _pp
                    prop = _pp.get_follow_up()
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
                    # The answer is the one said first: "yes, not no" agrees, and "no, don't do it"
                    # declines (it holds "do it", and an affirmation anywhere had won, so the offer
                    # stayed open for the next turn to agree to).
                    _said = low.split()

                    def _first(reader):
                        return next((i for i in range(1, len(_said) + 1) if reader(" ".join(_said[:i]))), None)

                    _no_at, _yes_at = _first(is_negation), _first(is_affirmation)
                    if _no_at is not None and (_yes_at is None or _no_at < _yes_at):
                        _pp.clear_pending_proposal()
                        return None
                    offered = bool(prop.get("command") or prop.get("items"))
                    chosen = []
                    if offered and _pp.is_consent(low):
                        chosen = _pp.chosen_items(prop, low)
                    elif offered:
                        chosen = _pp.selection(prop, low)
                    if chosen:
                        _pp.clear_pending_proposal()
                        got = _pp.agreed(chosen, offer=prop.get("question") or prop.get("summary") or "",
                                         written=prop.get("written") or "")
                        cmds = got["commands"]
                        if got["tasks"] or got["explicit"] or got["saves"]:
                            # Some of it ELI does by writing, or may only do on the user's own
                            # words: the engine carries the whole agreement out (_carry_out_agreed).
                            return _mk("CHAT", {"message": text, "agreed": got}, 0.95,
                                       matched_by="pending_proposal.confirm")
                        if len(cmds) == 1:
                            routed = route(cmds[0])  # pending already cleared → no recursion
                            if isinstance(routed, dict):
                                routed.setdefault("meta", {})
                                routed["meta"]["matched_by"] = "pending_proposal.confirm"
                                routed["meta"]["rerouted_from_proposal"] = cmds[0]
                                return routed
                        elif cmds:
                            # Several steps were offered and agreed to: each runs as its own command.
                            return _mk("MULTI_COMMAND", {"commands": cmds, "raw": " and ".join(cmds),
                                                         "results_only": True}, 0.95,
                                       matched_by="pending_proposal.confirm")
                        return None
                    waiting = prop.get("awaiting") if isinstance(prop.get("awaiting"), dict) else None
                    if waiting and _pp.is_the_detail(text, waiting.get("needs")):
                        # An action asked for a missing detail ("When is it?") and the reply is
                        # that detail: the original request is run again with it.
                        _pp.clear_pending_proposal()
                        _asked_cmd = str(waiting.get("command") or "")
                        _detail = str(text or "").strip().rstrip("?.!")
                        whole = route(_asked_cmd.replace("{answer}", _detail) if "{answer}" in _asked_cmd
                                      else f"{_asked_cmd} {_detail}".strip())
                        if str(whole.get("action") or "").upper() == str(waiting.get("action") or "").upper():
                            whole.setdefault("meta", {})
                            whole["meta"]["matched_by"] = "pending_proposal.answer"
                            return whole
                        return None
                    # Going further into something the last result listed ("the second one",
                    # "tell me more about the tennis story"): the action and arguments the
                    # listing declared, for the item named. Not a "yes" and not an answer to a
                    # question, both of which were read above.
                    follow = _pp.follow_up_on(prop, text)
                    if follow:
                        return _mk(follow["action"], follow["args"], 0.9,
                                   matched_by="pending_proposal.follow_up",
                                   meta_extra={"follow_up_of": follow["listed_by"],
                                               "follow_up_item": (follow.get("item") or {}).get("n")})
                    if offered and is_affirmation(low):
                        # "yes please dig into the timestamps" is a new request, not consent to
                        # what was offered.
                        _pp.clear_pending_proposal()
                        return None
                except Exception:
                    _SWLOG.debug("pending proposal stage failed", exc_info=True)
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
                ("timestamp_and_shell_followup", _stage_timestamp_and_shell_followup),
                ("pending_proposal_confirm", _stage_pending_proposal_confirm),
                # before pasted text: a link to read is not text to react to
                ("read_linked_page", lambda t, *a, **k: _eli_linked_page_prepass(str(t or ""))),
                ("react_to_pasted_content", _stage_react_to_pasted_content),
                ("mcp_explicit", _stage_mcp_explicit),
                ("computer_use_explicit", _stage_computer_use_explicit),
                ("precedence", _stage_precedence),
                ("identity_audit", _stage_identity_audit),
                ("frontier_status", _stage_frontier_status),
                ("memory_runtime_lock", _stage_memory_runtime_lock),
                ("gui_actual_scan", _stage_gui_actual_scan),
                # Before the self-report and schedule stages: "check your calendar for any recent
                # updates" is a calendar request, and a reminder is not a background work task.
                ("agenda", lambda t, *a, **k: _eli_agenda_prepass(t)),
                ("self_report_recent_updates", _stage_self_report_recent_updates),
                ("recent_memory", _stage_recent_memory),
                ("memory_count", _stage_memory_count),
                ("profile_scope_explicit", _stage_profile_scope_explicit),
                ("final_memory_contract", _stage_final_memory_contract),
                ("runtime_or_name_contract", _stage_runtime_or_name_contract),
                ("generate_script_prepass", _stage_generate_script_prepass),
                ("runtime_cognition_failure_guard", _stage_runtime_cognition_failure_guard),
                ("self_improvement_guard", _stage_self_improvement_guard),
                ("personal_memory_pre_route", _stage_personal_memory_pre_route),
                ("lrf_pre_route", _stage_lrf_pre_route),
                ("resume_task_pre_route", _stage_resume_task_pre_route),
                # set_user_name runs before portable_route so "call me Alex" / "my name is X" aren't taken for
                # media-play requests by portable_intent_contract. multi_command_prepass runs first so a chained
                # utterance is split and each command routed on its own.
                ("multi_command_prepass", lambda t, *a, **k: _eli_multi_command_prepass(t)),
                # schedule_prepass runs BEFORE portable_route so "open spotify at 8pm"
                # / "close steam in 2 hours" defer to the background workers instead of
                # being executed now by the app router (no-time commands fall through).
                ("schedule_prepass", lambda t, *a, **k: _eli_schedule_prepass(t)),
                ("set_user_name", _stage_set_user_name),
                ("set_communication_style", _stage_set_communication_style),
                # shell_prepass BEFORE portable_route so "run that command" and
                # "run date" are not misread as OPEN_APP ("that command").
                ("shell_prepass",   lambda t, *a, **k: _eli_shell_prepass(t)),
                ("portable_route", _stage_portable_route),
                ("weather_prepass", lambda t, *a, **k: _eli_weather_prepass(t)),
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

        # The stages whose rules match on words anywhere in the text. What they return is carried
        # out only when the sentence is telling ELI to do it.
        _ELI_WORD_RULE_STAGES = frozenset((
            "schedule_prepass", "shell_prepass", "portable_route", "voice_contract", "web_lookup", "core_router"))
        # The stages that read a reply to something ELI itself left open (a "yes" to its offer, a
        # pick from its list), an explicit tool prefix, or a chained message whose parts are each
        # routed, and held to the tests below, on their own. What they return is carried out as is.
        _ELI_REPLY_STAGES = frozenset((
            "pending_habit_confirm", "pending_code_fix_confirm", "pending_remediation_confirm",
            "pending_proposal_confirm", "mcp_explicit", "computer_use_explicit", "multi_command_prepass"))

        def _eli_fits(text, candidate):
            try:
                from eli.execution.route_contracts import fits
                from eli.runtime.command_splitter import without_correction_lead
                # "i said play X", "no, i meant open downloads": the instruction is behind the lead
                return fits(without_correction_lead(text), candidate)
            except Exception:
                _SWLOG.debug("fits check failed", exc_info=True)
                return True

        def _eli_told(stage_name, text, candidate) -> bool:
            """Whether a stage's match is carried out. By what the action does, not only by which
            stage matched it: every stage's rules match words, and "you remind me of my brother" set
            an alarm from a stage the six word-rule stages did not include. An action that changes
            something is carried out only when a sentence of the message tells ELI to do it
            (route_contracts.fits); a word-rule stage's reading action only when it was asked for."""
            if stage_name in _ELI_REPLY_STAGES or not isinstance(candidate, dict):
                return True
            try:
                from eli.execution.route_contracts import asks_what_was_done, changes_something, does_work
                action = candidate.get("action")
                # "did you inspect any of those files?" asks what was done, and the record answers it:
                # it is not a request to do it. A rule matching "inspect ... files" in it started a
                # lint of ELI's own core nobody asked for. Reading a state ("did you add it to my
                # calendar?" shows the calendar) still answers it.
                if (changes_something(action) or does_work(action)) and asks_what_was_done(text):
                    return False
                if stage_name in _ELI_WORD_RULE_STAGES or changes_something(action):
                    return _eli_fits(text, candidate)
            except Exception:
                _SWLOG.debug("route gate failed", exc_info=True)
            return True

        def _eli_target_took_the_rest(whole) -> bool:
            """Whether a command took the rest of the message as its target: "open steam, set an
            alarm for 30 minutes, and tell me the meaning of life" opened an app by that whole
            name, and "look up the tide tables and tell me what you can see" searched for the instruction."""
            from eli.execution.route_contracts import TARGET_ARGS
            from eli.runtime.command_splitter import holds_more_than_one
            args = (whole or {}).get("args") or {}
            return any(isinstance(args.get(k), str) and holds_more_than_one(args[k]) for k in TARGET_ARGS)

        def _eli_carried_by(tells, told, whole, told_parts=()) -> bool:
            """Whether the command the whole message routed to carries this instruction: it is that
            command with what the instructions name ("write a note saying buy milk", "open the
            browser and search kite"), or the instruction is inside what it was given ("call mom" in
            the note's text). "open steam, set an alarm for 30 minutes" set the timer and left "open
            steam" out. The same command is not enough: "<a complaint> man.. play <song> by <artist>"
            played a search for the complaint and the song together, because the whole message had
            also routed to a play. What the whole command was given has to be said by the
            instructions that are that command (`told_parts`, with this one)."""
            from eli.execution.route_contracts import TARGET_ARGS
            words = lambda t: re.findall(r"[a-z0-9']+", str(t).lower())   # noqa: E731
            whole_args = (whole or {}).get("args") or {}
            action = str((whole or {}).get("action") or "").upper()
            if str((told or {}).get("action") or "").upper() == action:
                said = set()
                for part_tells, part_told in [(tells, told), *told_parts]:
                    if str((part_told or {}).get("action") or "").upper() != action:
                        continue
                    part_args = (part_told or {}).get("args") or {}
                    said |= set(words(part_tells)) | {w for k in TARGET_ARGS if isinstance(part_args.get(k), str)
                                                      for w in words(part_args[k])}
                return all(w in said for k in TARGET_ARGS if isinstance(whole_args.get(k), str)
                           for w in words(whole_args[k]))
            given = " ".join(str(v) for v in whole_args.values() if isinstance(v, str))
            return bool(words(tells)) and " ".join(words(tells)) in " ".join(words(given))

        def _eli_instructions_and_talk(text, whole, *args, **kwargs):
            """A message of several sentences or instructions that is not one command: the ones that
            tell ELI to do something are carried out, and the rest is talk to be answered.

            "see, you can do it! open spotify" was answered by the model alone, which wrote that
            it had opened Spotify. A complaint ending in "open nasa website" stopped the film and
            never opened anything."""
            try:
                from eli.execution.route_contracts import is_command
                from eli.runtime.command_splitter import read_message
                # Commands are read from the person's own words, not from what the message quotes.
                from eli.cognition.supplied_in_message import supplied_as_data, without_supplied_text
                if supplied_as_data(text):
                    return None
                own_words = without_supplied_text(text)
                # chained instructions are looked for when one command took the message, or a
                # command rule matched it and was turned down for the other words it holds
                chained = is_command(whole) or bool(((whole or {}).get("meta") or {}).get("not_told"))
                parts = read_message(own_words, chains=chained)
                if not (2 <= len(parts) <= 12):
                    return None
                steps, talk, told_parts = [], [], []
                for part in parts:
                    tells = part["tells"]
                    told = _eli_route_with_courtesy(tells, *args, **kwargs) if tells else None
                    if tells and is_command(told):
                        steps.append(tells.rstrip(" .!"))
                        told_parts.append((tells, told))
                    else:
                        talk.append(part["said"])
                if not steps or len(steps) > 5:
                    return None
                if (is_command(whole) and not _eli_target_took_the_rest(whole)
                        and all(_eli_carried_by(tells, told, whole, told_parts) for tells, told in told_parts)):
                    return None                    # one command that carries every instruction in it
                rest = " ".join(talk).strip()
                if len(re.findall(r"[A-Za-z0-9']+", _without_courtesy(rest) if rest else "")) < 3:
                    rest = ""                      # "well done." before a command is not a second subject
                # One thing to read and something to say about it ("look up ORBIT and tell me what
                # you can see"): the read, with its own words as its target, answered against the
                # whole message, as "look up ORBIT" alone is. Shown first as a result and then
                # replied to, the search was a list of links and the reply saw 600 characters of it.
                # A read that points back at the rest ("explain whether you recalled this") needs it.
                from eli.execution.route_contracts import changes_something
                if (rest and len(told_parts) == 1 and not changes_something(told_parts[0][1].get("action"))
                        and not re.search(r"\b(?:this|that|these|those|it|them)\b", told_parts[0][0], re.I)):
                    out = dict(told_parts[0][1])
                    out["meta"] = dict(out.get("meta") or {}, read_as="message.read_and_answer")
                    return out
                if not rest:
                    if len(steps) == 1:
                        out = _eli_route_with_courtesy(steps[0], *args, **kwargs)
                        out.setdefault("meta", {})["read_as"] = "message.instructions"
                        return out
                    return _mk("MULTI_COMMAND", {"commands": steps, "raw": text}, 0.92,
                               matched_by="message.instructions")
                return _mk("CHAT", {"message": text,
                                    "agreed": {"commands": steps, "tasks": [rest], "offer": "",
                                               "rest_of_message": True}},
                           0.95, matched_by="message.instructions_and_talk")
            except Exception:
                _SWLOG.debug("sentence reading failed", exc_info=True)
                return None

        def _eli_route_with_courtesy(raw="", *args, **kwargs):
            # Courtesy after a command ended up in its target: "open spotify please" looked for an
            # app called "spotify please" and offered to install it. A command is routed without
            # it; anything that is not a command keeps the user's words as they were.
            text = str(raw or "")
            clean = _without_courtesy(text)
            if clean != text:
                out = _eli_priority_route_core(clean, *args, **kwargs)
                if isinstance(out, dict) and str(out.get("action") or "").upper() not in ("", "CHAT"):
                    return out
            return _eli_priority_route_core(text, *args, **kwargs)

        def _eli_misspelt_command(text, whole, *args, **kwargs):
            """A short message that is a command with a letter wrong ("vol don"): the one reading
            with a word corrected that routes to a command. It went to chat, which wrote "Volume
            down again." while nothing ran."""
            try:
                from eli.execution.route_contracts import TARGET_ARGS, is_command
                from eli.runtime.command_splitter import COMPLETERS, misspelt_commands
                if is_command(whole):
                    return None
                found = []
                for reading, corrected in misspelt_commands(text):
                    out = _eli_route_with_courtesy(reading, *args, **kwargs)
                    given = " ".join(str(v) for k, v in (out.get("args") or {}).items()
                                     if k in TARGET_ARGS + ("text", "message", "content") and isinstance(v, str))
                    target = re.findall(r"[a-z]+", given.lower())
                    # "clock off" is not "click off": a word that only completes a command is no target
                    if is_command(out) and corrected not in target and not (target and set(target) <= set(COMPLETERS)):
                        found.append((reading, out))
                if len({str(o.get("action")) for _r, o in found}) != 1:
                    return None                      # nothing, or two different commands: not a guess to act on
                reading, out = found[0]
                out = dict(out)
                out["meta"] = dict(out.get("meta") or {}, read_as=f"misspelt: {reading}")
                return out
            except Exception:
                _SWLOG.debug("misspelling check failed", exc_info=True)
                return None

        def _eli_priority_route(raw="", *args, **kwargs):
            # "i said play X", "no, i meant open downloads": a correction's lead stands in front of the
            # instruction it repeats, which is routed as said (command_splitter.correction). A reply
            # to something ELI left open is read whole ("no, do it later" declines).
            try:
                from eli.runtime.command_splitter import correction, starts_with_command
                from eli.runtime.pending_proposal import get_follow_up
                _repeated = correction(str(raw or ""))
                if _repeated and starts_with_command(_repeated, after_lead=True) and not get_follow_up():
                    raw = _repeated
            except Exception:
                _SWLOG.debug("correction lead not read", exc_info=True)
            whole = _eli_route_with_courtesy(raw, *args, **kwargs)
            return (_eli_instructions_and_talk(str(raw or ""), whole, *args, **kwargs)
                    or _eli_misspelt_command(str(raw or ""), whole, *args, **kwargs)
                    or whole)

        def _eli_priority_route_core(raw="", *args, **kwargs):
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

            not_told = None
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
                if candidate is not None and not _eli_told(stage_name, text, candidate):
                    # A rule matched a word in a sentence that is not telling ELI to do that
                    # (route_contracts.fits). The later stages still get the message.
                    not_told = not_told or {
                        "action": str(candidate.get("action") or ""), "stage": stage_name,
                        "matched_by": str((candidate.get("meta") or {}).get("matched_by") or "")}
                    continue
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
            if not_told and str(result.get("action") or "").upper() in ("", "CHAT", "NOOP"):
                # Talk that mentions a command is talk: it goes to chat as it is, and is not
                # handed to the intent model to be turned into the same command again.
                result = {
                    "action": "CHAT",
                    "args": {"message": text},
                    "confidence": 0.6,
                    "meta": {"matched_by": "router.not_an_instruction", "not_told": not_told,
                             "allow_chat_without_evidence": True},
                }
                matched_by = "not_an_instruction"

            # The final contracts normalise what was chosen. One that turns the result into another
            # action is held to the same tests as a stage: "my car was hit by a truck" was made a
            # song request after the stages had all passed it by.
            contracted = _eli_route_apply_post_contracts(text, result)
            if (isinstance(contracted, dict) and isinstance(result, dict)
                    and str(contracted.get("action") or "").upper() != str(result.get("action") or "").upper()
                    and not _eli_told("post_contract", text, contracted)):
                contracted = result
            result = contracted
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

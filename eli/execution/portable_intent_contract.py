from __future__ import annotations

def _catalogue_literal(text: str):
    """The route for a sentence that is, word for word, a catalogue phrase naming an action with nothing
    variable in it (routing/command_parser.literals), or None."""
    try:
        from eli.execution.routing.command_parser import _literal
        return _literal(str(text or ""))
    except Exception:
        return None

def _eli_phase10_blocks_media_intent(text: str) -> bool:
    """
    Hard block document/code/analysis prompts from PLAY_MEDIA.
    This prevents long academic PDF prompts being interpreted as songs.
    """
    s = str(text or "").lower().strip()

    # This blocker stops long academic prompts being heard as song requests, but try_route used it
    # as an early return for the whole contract, so one word ("theory") disabled app, window,
    # browser and shell routes. An utterance opening with an app/window verb isn't a play request.
    import re as _re10
    if _re10.match(
        r"^(?:please\s+|pls\s+|kindly\s+)?"
        r"(?:(?:could|can|would|will)\s+you(?:\s+please)?\s+)?"
        r"(?:open|opens|launch|close|closed|quit|exit|"
        r"minimi[sz]e|maximi[sz]e|hide|focus)\b",
        s,
    ):
        return False

    if len(s) > 260:
        return True

    blockers = (
        ".pdf", "[pdf content", "pdf content", "analyse", "analyze",
        "summarise", "summarize", "read and summarise", "read and summarize",
        "abstract", "research", "experiment", "hypothesis", "dataset",
        "simulation", "scientific", "framework", "theory",
        "audit", "router", "executor", "gguf_inference", "orchestrator",
        "python files", "codebase",
    )
    return any(b in s for b in blockers)


import re
from typing import Optional


def _known_user_dir(target: str) -> str:
    """Path when *target* names a well-known user folder, else "". Lazy import
    keeps this module free of a hard dependency at import time."""
    try:
        from eli.utils.platform_compat import known_user_dir
        return known_user_dir(target)
    except Exception:
        return ""


def normalise_voice_text(text: str) -> str:
    text = str(text or "").strip().lower()
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = text.replace(" per cent", "%").replace(" percent", "%")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clean_target(text: str) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip(" .,:;")
    return re.sub(r"\s+", " ", s).strip(" .,:;")


_WINDOW_WORDS_RE = re.compile(r"^(?:the|my|that|this)\s+|\s+(?:window|app|application|program)$", re.I)
_A_TAB_RE = re.compile(r"\s+tab(?:\s+(?:in|on|of)\s+(?:the\s+|my\s+)?[\w\s-]{1,30})?$", re.I)


def window_target(text: str) -> dict:
    """The window a command names, without the words around it: "the browser window" ->
    {"name": "browser"}; "the futurama tab in browser" -> {"name": "futurama", "tab": True}.
    The name is "" for "this window", "it", "the window": the one in front."""
    from eli.system.portable_app_control import BARE_WINDOW_TARGETS
    s = _clean_target(text)
    tab = bool(_A_TAB_RE.search(s))
    if s.lower() not in BARE_WINDOW_TARGETS:
        s = _A_TAB_RE.sub("", s)
        for _ in range(2):
            s = _WINDOW_WORDS_RE.sub("", s).strip()
    name = "" if s.lower() in BARE_WINDOW_TARGETS else s
    return {"name": name, "tab": True} if (tab and name) else {"name": name}


_MEDIA_SERVICE_ALIASES = {
    "spotify": "spotify",
    "youtube": "youtube",
    "yt": "youtube",
    "you tube": "youtube",
    "soundcloud": "soundcloud",
    "netflix": "netflix",
    "net flix": "netflix",
    "prime": "primevideo",
    "prime video": "primevideo",
    "primevideo": "primevideo",
    "amazon prime": "primevideo",
    "amazon": "primevideo",
    "disney": "disneyplus",
    "disney+": "disneyplus",
    "disney plus": "disneyplus",
    "disneyplus": "disneyplus",
    "hulu": "hulu",
    "hbo": "max",
    "hbo max": "max",
    "hbomax": "max",
    "max": "max",
    "paramount": "paramountplus",
    "paramount+": "paramountplus",
    "paramount plus": "paramountplus",
    "paramountplus": "paramountplus",
    "peacock": "peacock",
    "peacock tv": "peacock",
    "apple tv": "appletv",
    "apple tv+": "appletv",
    "appletv": "appletv",
    "plex": "plex",
    "crunchyroll": "crunchyroll",
    "discovery": "discoveryplus",
    "discovery+": "discoveryplus",
    "discovery plus": "discoveryplus",
    "discoveryplus": "discoveryplus",
    "tubi": "tubi",
    "tubi tv": "tubi",
    "pluto": "pluto",
    "pluto tv": "pluto",
    "twitch": "twitch",
}


def _normalise_media_service(target: str) -> str:
    key = _clean_target(target).lower()
    key = re.sub(r"\s+", " ", key)
    return _MEDIA_SERVICE_ALIASES.get(key, key)


def _known_media_service(target: str) -> bool:
    """Whether a phrase names a place to play from: a media service ELI knows (Spotify, YouTube, a
    streaming site) or a site ("example.ch", a site ELI knows by name)."""
    key = re.sub(r"\s+", " ", _clean_target(target).lower()).strip()
    if key in _MEDIA_SERVICE_ALIASES:
        return True
    try:
        from eli.execution.media_runtime import normalize_streaming_target
        if normalize_streaming_target(key):
            return True
    except Exception:
        pass
    return bool(site_named(key))


# What kind of thing a play asks for, when its words say: "the album X", "songs similar to X", "the
# film X", "episode 3 of X". The effector searches for that kind. None when they do not say.
_MEDIA_KIND = re.compile(
    r"^(?:the\s+|an?\s+|some\s+)?(?P<kind>album|track|song|artist|episode|film|movie|genre)\b"
    r"|\b(?P<similar>similar\s+to|(?:songs?|music|tracks?|stuff)\s+like)\b", re.I)
_MEDIA_KINDS = {"song": "track", "movie": "film"}


def media_kind(query: str) -> Optional[str]:
    """The kind of thing a play's query asks for (track, album, artist, episode, film, genre,
    similar), or None."""
    m = _MEDIA_KIND.search(str(query or ""))
    if not m:
        return None
    if m.group("similar"):
        return "similar"
    kind = m.group("kind").lower()
    return _MEDIA_KINDS.get(kind, kind)


# A generic browser request is a browser request, not an app called "browser kite". OPEN_BROWSER
# already accepts a query and makes a search page; this contract matched first at 0.995 and shadowed
# it.
_BROWSER_HEAD_RE = re.compile(r"^(?:(?:a|an|the)\s+)?(?:(?:web|internet)\s+)?browsers?\b", re.I)
_BROWSER_JOINER_RE = re.compile(r"^(?:on|for|about|to|at|with|and|then|showing)\s+", re.I)
_BROWSER_VERB_RE = re.compile(r"^(?:search(?:\s+for)?|look\s*up|lookup|find|google|browse)\s+", re.I)


def _browser_open_target(target: str):
    """Return the search query for a generic browser request, else None.

    ""   -> open the browser with no query
    "kite"-> open the browser on a search for kite
    None -> not a browser request at all
    """
    s = str(target or "").strip()
    m = _BROWSER_HEAD_RE.match(s)
    if not m:
        return None
    rest = s[m.end():].strip(" .,:;-")
    # The query ends at the first sentence break: "…search for kite. open a
    # browser search page" is one subject followed by a restated instruction,
    # and the instruction is not part of what to search for.
    rest = re.split(r"(?<=\w)[.!?](?:\s|$)", rest, maxsplit=1)[0].strip()
    rest = re.sub(r"\s*(?:,|and)?\s*(?:then\s+)?open\s+(?:a|an|the)?\s*"
                  r"(?:web\s+)?browser(?:\s+search)?(?:\s+page)?\s*$",
                  "", rest, flags=re.I).strip()
    for _ in range(3):
        before = rest
        rest = _BROWSER_JOINER_RE.sub("", rest).strip()
        rest = _BROWSER_VERB_RE.sub("", rest).strip()
        if rest == before:
            break
    return rest.strip(" .,:;-")


def site_named(target: str) -> str:
    """The web address a close or open target names when it is a site and not an installed app:
    a domain ("example.com"), a site ELI knows by name (app_aliases.WEBSITE_ALIASES), or a streaming
    service (media_runtime). "" for anything else."""
    from eli.execution.app_aliases import APP_ALIASES, WEBSITE_ALIASES
    key = re.sub(r"\s+", " ", _clean_target(target).lower()).strip()
    if not key or key in APP_ALIASES:
        return ""
    if re.fullmatch(r"(?:https?://)?(?:www\.)?[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}(?:/\S*)?", key):
        return key if key.startswith("http") else f"https://{key}"
    if key in WEBSITE_ALIASES:
        return WEBSITE_ALIASES[key]
    try:
        from eli.execution.media_runtime import _STREAMING_CANONICAL_URLS, normalize_streaming_target
        canon = normalize_streaming_target(key)
        return str(_STREAMING_CANONICAL_URLS.get(canon) or "") if canon else ""
    except Exception:
        return ""


def _looks_like_app_target(target: str) -> bool:
    """A real OPEN/CLOSE/RUN target is an app's name, by the command language's one test
    (routing/command_parser.names_an_app): not a sentence, not a word pointing at something."""
    from eli.execution.routing.command_parser import names_an_app
    return names_an_app(target)


# Common TLDs the user might dictate. STT drops the dot, so "open github com"
# arrives as the two-token target "github com" and "open github dot com" as
# "github dot com" — both are web addresses, not installable apps.
_URL_TLDS = frozenset({
    "com", "org", "net", "io", "gov", "edu", "co", "uk", "ie", "dev",
    "app", "ai", "me", "info", "xyz", "tv", "fm", "cloud", "online", "site",
})

# The endings that are also ordinary words. Said without a dot after a name they describe the
# thing ("nasa site", "apple tv", "notes app", "google cloud"), so they make an address only
# when written or spoken with the dot: "open nasa site" used to open https://nasa.site.
_SPOKEN_TLDS = _URL_TLDS - {"site", "app", "tv", "me", "info", "cloud", "online", "dev", "ai", "fm", "co"}

# Named sites users say without a TLD — open in the browser, not as an installed app.
from eli.execution.app_aliases import WEBSITE_ALIASES


# Dotted names that are files, not websites: "open notes.txt" is a file.
_FILE_EXTENSIONS = frozenset({
    "txt", "md", "pdf", "doc", "docx", "odt", "rtf", "xls", "xlsx", "ods", "csv", "tsv",
    "ppt", "pptx", "odp", "json", "yaml", "yml", "toml", "ini", "cfg", "conf", "log", "xml",
    "py", "js", "ts", "jsx", "tsx", "sh", "bash", "c", "h", "cpp", "hpp", "rs", "go", "java",
    "kt", "rb", "php", "lua", "sql", "db", "sqlite", "sqlite3", "gguf", "bin", "safetensors",
    "png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "ico", "mp3", "wav", "flac", "ogg",
    "m4a", "mp4", "mkv", "avi", "mov", "webm", "zip", "tar", "gz", "xz", "7z", "rar", "deb",
    "rpm", "appimage", "exe", "msi", "dmg", "iso", "desktop", "html", "htm", "css", "ipynb",
})
# "open cinejoy.pk in web" / "... in the browser": says where to open it, not what it is.
_IN_WEB_SUFFIX_RE = re.compile(
    r"\s+(?:in|on|with|using)\s+(?:the\s+|a\s+|my\s+)?(?:web(?:\s+browser)?|browser|"
    r"internet|chrome|chromium|firefox|brave)\s*$", re.I)


_ORDINALS = {"first": 1, "1st": 1, "top": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
             "fifth": 5, "5th": 5}
_A_PAGE = r"(?:web\s?sites?|sites?|web\s?pages?|home\s?pages?|pages?|links?|urls?|address(?:es)?|results?|articles?)"
_AGAIN = r"(?:\s+(?:again|you (?:just )?(?:found|mentioned|listed|gave me|showed me|linked)|from (?:before|earlier|above)))?"
# "that website", "the second link". A bare "it" or "that" points at a page only when the
# command says where to open it ("open it in the browser").
_POINTS_AT_A_PAGE_RE = re.compile(
    r"(?:that|this|the|the same|those|these)(?:\s+(?P<which>" + "|".join(_ORDINALS) + r"))?\s+" + _A_PAGE + _AGAIN, re.I)
_POINTS_BACK_RE = re.compile(r"(?:it|that|this|them|those|these|(?:that|the) (?:same )?one)" + _AGAIN, re.I)
_AFTERTHOUGHT_RE = re.compile(r"(?:\s+(?:instead|then|please|now|for me|too|as well))+\s*$", re.I)
_NOT_A_NAME = frozenset(("my", "the", "a", "an", "this", "that", "our", "your", "some", "any", "web", "new", "which", "what"))
_NAMED_SITE_RE = re.compile(
    r"(?:the\s+|my\s+)?(?P<name>[\w&.'’+-]+(?:\s+[\w&.'’+-]+){0,4}?)(?:'s|’s)?\s+(?:official\s+)?"
    r"(?:web\s?site|site|web\s?page|home\s?page)", re.I)


def _looks_like_url_target(target: str) -> bool:
    """True when an OPEN target is a web address rather than an app name."""
    s = _IN_WEB_SUFFIX_RE.sub("", str(target or "").strip().lower())
    if not s:
        return False
    if re.match(r"https?://", s):
        return True
    collapsed = re.sub(r"\s+dot\s+", ".", s)
    # Spoken "<name> com" (dot dropped by STT): two tokens, trailing TLD word.
    toks = collapsed.split()
    if len(toks) == 2 and toks[1] in _SPOKEN_TLDS and re.fullmatch(r"[a-z0-9\-]+", toks[0]):
        return True
    # A single dotted name with any real TLD: "github.com", "docs.python.org",
    # "cinejoy.pk". The fixed TLD list made "open cinejoy.pk" an app to install.
    m = re.fullmatch(r"(?:www\.)?[a-z0-9\-]+(?:\.[a-z0-9\-]+)*\.([a-z]{2,24})/?(?:[/?#]\S*)?", collapsed)
    if m and m.group(1) not in _FILE_EXTENSIONS:
        return True
    # Embedded dotted domain from the known list, e.g. "github.com page".
    if re.search(r"\b[a-z0-9\-]+\.(?:%s)\b" % "|".join(_URL_TLDS), collapsed):
        return True
    return False


def web_open_target(target: str):
    """(action, args) when an OPEN target is a website or names the browser, else None.

    "cinejoy.pk in web" -> OPEN_URL https://cinejoy.pk; "quantum news in the browser"
    -> OPEN_BROWSER with that query. Shared by the router's literal pre-empt and the
    portable contract so the two can't disagree."""
    raw = str(target or "").strip()
    if _looks_like_url_target(raw):
        return "OPEN_URL", {"url": _build_url(raw)}
    rest = _IN_WEB_SUFFIX_RE.sub("", _AFTERTHOUGHT_RE.sub("", raw)).strip(" .,")
    rest = _AFTERTHOUGHT_RE.sub("", rest).strip(" .,")
    # "that website", "the second link": an address named earlier in the conversation.
    points = _POINTS_AT_A_PAGE_RE.fullmatch(rest)
    if points:
        return "OPEN_URL", {"refers_to": rest, "which": _ORDINALS.get((points.group("which") or "").lower(), 1)}
    if _IN_WEB_SUFFIX_RE.search(raw) and _POINTS_BACK_RE.fullmatch(rest):
        return "OPEN_URL", {"refers_to": rest, "which": 1}
    # "nasa website", "the bbc's home page": a site known by its name. Its address is found
    # when the command runs (eli.runtime.site_address), never made up from the name.
    site = _NAMED_SITE_RE.fullmatch(rest)
    if site and site.group("name").strip().lower() not in _NOT_A_NAME:
        name = re.sub(r"['’]s$", "", site.group("name").strip())
        known = WEBSITE_ALIASES.get(name.lower())
        return "OPEN_URL", ({"url": known} if known else {"site": name})
    if _IN_WEB_SUFFIX_RE.search(raw):
        return "OPEN_BROWSER", ({"query": rest} if rest else {})
    return None


def _build_url(target: str) -> str:
    """Normalise a dictated web address into a real https URL."""
    s = _IN_WEB_SUFFIX_RE.sub("", str(target or "").strip().lower())
    if re.match(r"https?://", s):
        return s
    s = re.sub(r"\s+dot\s+", ".", s)
    toks = s.split()
    if len(toks) == 2 and toks[1] in _SPOKEN_TLDS:
        s = toks[0] + "." + toks[1]
    s = re.sub(r"\s+", "", s)
    return "https://" + s


_SCRIPT_LANGUAGE_ALIASES = {
    "py": "python",
    "python3": "python",
    "node": "javascript",
    "nodejs": "javascript",
    "js": "javascript",
    "ts": "typescript",
    "sh": "bash",
    "shell": "bash",
    "csharp": "c#",
    "rs": "rust",
    "golang": "go",
    "ps": "powershell",
    "pwsh": "powershell",
}

_SCRIPT_LANGUAGE_NAMES = frozenset({
    "python", "bash", "zsh", "javascript", "typescript", "c", "c++", "cpp",
    "c#", "java", "rust", "go", "ruby", "php", "lua", "r", "swift",
    "kotlin", "scala", "sql", "html", "css", "json", "yaml", "yml",
    "julia", "fortran", "perl", "powershell",
})

_SCRIPT_LANGUAGE_REJECTS = frozenset({
    "a", "an", "the", "ide", "labs", "tab", "default", "previous",
    "following", "this", "that", "advanced", "basic", "simple", "new",
    "powerful", "potent",
})


def _normalise_script_language(candidate: str) -> str | None:
    value = str(candidate or "").strip().lower().rstrip(".,:;")
    if not value or value in _SCRIPT_LANGUAGE_REJECTS:
        return None
    value = _SCRIPT_LANGUAGE_ALIASES.get(value, value)
    if value in _SCRIPT_LANGUAGE_NAMES:
        return value
    return None


def infer_script_language(text: str) -> str:
    norm = normalise_voice_text(text)

    m = re.search(
        r"\b(?:generate|write|create|build|make)\s+(?:a|an|the)?\s*([a-z0-9+#.\-]{1,40})\s+(?:script|code|program|module|tool|app)\b",
        norm,
    )
    if m:
        language = _normalise_script_language(m.group(1))
        if language:
            return language

    for m in re.finditer(r"\b(?:in|using|with)\s+([a-z0-9+#.\-]{1,40})\b", norm):
        language = _normalise_script_language(m.group(1))
        if language:
            return language

    if (
        re.search(r"\b(?:generate|write|create|build|make|define)\b", norm)
        and re.search(r"\b(?:script|code|program|class|function|module|object|constructor|inherit)\b", norm)
    ):
        return "python"

    return "auto"


def _looks_like_generation_complaint(norm: str) -> bool:
    if not norm:
        return False
    complaint_starts = (
        "you did not", "you didn't", "you just", "that script", "this script",
        "why did you", "why didn't you", "where is", "where did", "it did not",
        "it didn't", "your code", "your script",
    )
    complaint_phrases = (
        "did not generate", "didn't generate", "never ran", "not generated",
        "nothing to do with", "less in content", "no ide opened",
        "did not open", "didn't open", "dump it into chat", "not provide any path",
        "where is it",
    )
    return norm.startswith(complaint_starts) or any(p in norm for p in complaint_phrases)


# What follows "by" in a phrase, not an artist: "who is that by the way", "by now", "what did you mean by
# that", and a time a thing is due by ("finish the report by tomorrow", "by friday", "by 5pm").
_NOT_AN_ARTIST = re.compile(
    r"(?:the\s+(?:way|time|end)|now|then|far|default|chance|accident|mistake|hand)\b.*"
    r"|(?:me|you|him|her|it|us|them|that|this|these|those|myself|yourself|himself|herself|itself"
    r"|ourselves|yourselves|themselves)"
    r"|(?:today|tonight|tomorrow|noon|midnight|(?:mon|tues|wednes|thurs|fri|satur|sun)day|next\s+\w+"
    r"|this\s+(?:morning|afternoon|evening|week|weekend|month|year)|(?:the\s+)?(?:end|start)\s+of\b.*"
    r"|\d{1,2}(?::\d{2})?\s*(?:am|pm|o'?clock)?)\b.*", re.I)
# A song is not asked for with the words a request or a statement is made of. Pronouns are not among
# them: titles are full of them ("all eyez on me", "you mean everything to me"), and a sentence that
# opens on one is a statement the route's own fit test turns down.
_NOT_A_TITLE_WORD = frozenset((
    "will", "would", "could", "should", "can", "please", "did", "does", "done", "been", "got", "get", "go", "run",
    "come", "take", "give", "send", "also", "then", "just", "not", "never", "no", "yes", "ok", "okay", "now", "is",
    "are", "was", "were", "be", "am"))
# The question words and verbs of another command: "explain X by Y", "search X by Y", "what is X by Y".
_NOT_A_TITLE_START = re.compile(
    r"^(?:search|find|look|show|get|tell|what|how|why|when|where|which|who|is|are|was|were|will|can|could|should"
    r"|would|do|does|did|make|create|run|start|stop|open|close|pause|explain|describe|define|read|write|list|audit"
    r"|check|scan|review|analyse|analyze|created?|written?|made|designed?|built|developed?|then|so|but|and)\b", re.I)
# "by" after a passive or a participle names who did it: "my car was hit by a truck", "the book was
# written by tolkien", "i got stung by a bee".
_BY_AN_AGENT = re.compile(r"\b(?:am|is|are|was|were|be|been|being|get|gets|got|gotten|getting)\s+(?:\w+ly\s+)?\w+$", re.I)

def implied_song_by_artist(raw: str, norm: str = "") -> str:
    """The query when a message is "<title> by <artist>" said with no "play" ("bohemian rhapsody by
    queen"), or "" when it is a sentence with "by" in it. The one test of that shape: a copy of it
    ran on every message after routing, so "my car was hit by a truck" played Spotify."""
    raw = str(raw or "").strip()
    norm = norm or normalise_voice_text(raw)
    m = re.fullmatch(r"(.+?)\s+by\s+(.+)", norm)
    if not m or len(norm.split()) < 4:
        return ""
    before, artist = m.group(1).strip(), m.group(2).strip()
    if ("?" in raw or re.search(r"[.!?]", raw.strip().rstrip(".!? "))
            or set(before.lower().split()) & _NOT_A_TITLE_WORD or _NOT_A_TITLE_START.match(before)
            or _BY_AN_AGENT.search(before) or _NOT_AN_ARTIST.fullmatch(artist)
            # "so what do you mean by the second one": a clarification, not a title
            or re.search(r"\b(?:what|why|how|who|when|where)\b[^.?!]{0,25}\byou\s+(?:mean|meant|said|say|think|thought)\b",
                         before)):
        return ""
    return re.sub(r"(?i)^\s*play\s+", "", _clean_target(raw)).strip()


def try_route(text: str) -> Optional[dict]:
    if _eli_phase10_blocks_media_intent(text):
        return None
    raw = str(text or "").strip()
    norm = normalise_voice_text(raw)
    if not norm:
        return None

    if re.fullmatch(r"(?:start\s+pomodoro|begin\s+pomodoro|pomodoro\s+start)(?:\s+timer)?", norm):
        return None

    # ── Shell command prepass ────────────────────────────────────────────────
    # Must fire BEFORE the OPEN_APP "run X" pattern so "run ls", "run ps",
    # "run git status" etc. route to SHELL_EXEC, not PulseAudio / some random app.
    _PORTABLE_SHELL_CMDS = frozenset({
        "ls", "cd", "pwd", "cat", "head", "tail", "grep", "find", "wc",
        "date", "df", "du", "free", "top", "ps", "kill", "chmod", "chown",
        "cp", "mv", "rm", "mkdir", "rmdir", "touch", "echo", "which", "whoami",
        "uname", "uptime", "hostname", "ip", "ifconfig", "ping", "curl", "wget",
        "tar", "zip", "unzip", "apt", "pip", "npm", "git", "docker", "systemctl",
        "python", "python3", "bash", "sh", "env", "export", "source", "less", "more",
        "htop", "nano", "vim", "vi", "ssh", "scp", "rsync", "nc", "netstat", "ss",
        "lsblk", "lsusb", "lspci", "dmesg", "journalctl", "lsof", "strace",
        "make", "cmake", "gcc", "g++", "cargo", "go", "java", "node", "ruby",
    })
    m_shell = re.fullmatch(r"(?:run|execute)\s+(\S+(?:\s+.+)?)", norm)
    if m_shell:
        parts = m_shell.group(1).strip().split()
        if parts and parts[0].lower() in _PORTABLE_SHELL_CMDS:
            # Extract the command from the original text, not the lowercased norm: shell commands
            # are case-sensitive (paths, -R vs -r). norm only detects that this is a shell command.
            _raw_collapsed = re.sub(r"\s+", " ", raw).strip()
            m_shell_raw = re.match(r"(?i)(?:run|execute)\s+(\S+(?:\s+.+)?)", _raw_collapsed)
            _cmd = (m_shell_raw.group(1).strip() if m_shell_raw
                    else m_shell.group(1).strip())
            return {
                "action": "SHELL_EXEC",
                "args": {"cmd": _cmd},
                "confidence": 0.96,
                "meta": {"matched_by": "portable_intent_contract.shell_exec"},
            }

    # Voice/STT diagnostics — defer to core_router (STT_DIAGNOSTICS), not OPEN_APP.
    if re.search(
        r"\b(voice|stt|speech|whisper|microphone|mic)\s+diagnostics?\b"
        r"|\bdiagnostics?\s+(voice|stt|speech|whisper|microphone|mic)\b"
        r"|\brun\s+voice\s+diagnostics?\b"
        r"|\bcheck\s+(the\s+)?(microphone|mic)\b",
        norm,
    ):
        return None

    # "please open the browser and search for KITE" never reached this matcher because of the leading
    # politeness marker, so nothing opened. Stripped for this match only, to keep the blast radius
    # small.
    _norm_cmd = re.sub(r"^(?:please|pls|kindly|(?:could|can|would|will)\s+you(?:\s+please)?)\s+",
                       "", norm, flags=re.I).strip() or norm
    m = re.fullmatch(r"(?:open|opens|launch|start|run)\s+(.+)", _norm_cmd)
    if m:
        target = _clean_target(m.group(1))
        # Deictic targets ("open it / that / this / here / there") mean "open what I'm looking at",
        # a gaze-cursor double-click, not an app launch. Let them fall through to the gaze router.
        if target.lower().strip() in {
            "it", "that", "this", "these", "those", "here", "there", "them",
        } or re.fullmatch(r"(?:that|this|the|it)\s+command", target.lower().strip()):
            pass
        elif (
            target.lower().strip() in {
                "home", "home folder", "home directory", "home dir", "my home",
                "my files", "files", "file manager", "file explorer", "explorer",
                "the home folder", "the home directory", "trash",
            }
            or re.search(r"\b(folder|directory)$", target.strip(), re.I)
            or target.strip().startswith(("/", "~"))
            # Any well-known user folder (downloads, documents, desktop, pictures...), resolved from
            # the one cross-platform table. The literal list only had "downloads", so "open
            # documents" offered to install a program called "documents".
            or _known_user_dir(target)
        ):
            # File-manager and "<x> folder" requests are not app launches; let them fall through to
            # fs.open_home (OPEN_FILE_SYSTEM) so "open home folder" opens the file browser.
            pass
        elif _looks_like_url_target(target):
            # "open github com" / "open github.com" is a web address, not an app —
            # route to the browser instead of the not-installed install dialogue.
            return {
                "action": "OPEN_URL",
                "args": {"url": _build_url(target)},
                "confidence": 0.97,
                "meta": {"matched_by": "portable_intent_contract.open_url"},
            }
        elif target.lower().strip() in WEBSITE_ALIASES:
            return {
                "action": "OPEN_URL",
                "args": {"url": WEBSITE_ALIASES[target.lower().strip()]},
                "confidence": 0.97,
                "meta": {"matched_by": "portable_intent_contract.open_named_site"},
            }
        elif web_open_target(target) is not None:
            _web = web_open_target(target)
            return {
                "action": _web[0],
                "args": _web[1],
                "confidence": 0.95,
                "meta": {"matched_by": "portable_intent_contract.open_in_web"},
            }
        elif _browser_open_target(target) is not None:
            _bq = _browser_open_target(target)
            return {
                "action": "OPEN_BROWSER",
                "args": ({"query": _bq} if _bq else {}),
                "confidence": 0.98,
                "meta": {"matched_by": "portable_intent_contract.open_browser"},
            }
        elif _catalogue_literal(norm) is not None:
            # "start a pomodoro", "start the audit": a phrase the catalogue gives an action of its own
            # is that action, not an app by that name (routing/command_parser.literals)
            return _catalogue_literal(norm)
        elif _looks_like_app_target(target):
            return {
                "action": "OPEN_APP",
                "args": {"name": target, "target": target},
                "confidence": 0.995,
                "meta": {"matched_by": "portable_intent_contract.open_app"},
            }

    if re.fullmatch(r"(?:close|closed|exit|quit)\s+(?:current\s+|the\s+)?(?:browser\s+)?tab", norm):
        return {
            "action": "KEYBOARD",
            "args": {"key": "ctrl+w"},
            "confidence": 0.995,
            "meta": {"matched_by": "portable_intent_contract.close_current_tab"},
        }

    m = re.fullmatch(r"(?:close|closed|exit|quit)\s+(.+)", norm)
    if m:
        target = _clean_target(m.group(1))
        if _looks_like_app_target(target):
            # A site ELI opens in the browser ("close netflix", "close example.com") is a tab, not a
            # process: the route says so (args "site"), and what closes it is the browser's tab.
            site = site_named(target)
            return {
                "action": "CLOSE_APP",
                "args": {"name": target, "target": target, **({"site": site, "tab": True} if site else {})},
                "confidence": 0.995,
                "meta": {"matched_by": "portable_intent_contract.close_site" if site
                         else "portable_intent_contract.close_app"},
            }

    m = re.fullmatch(r"(?:kill|force close|force quit)\s+(.+)", norm)
    if m:
        target = _clean_target(m.group(1))
        if _looks_like_app_target(target):
            return {
                "action": "CLOSE_APP",
                "args": {"name": target, "target": target, "force": True},
                "confidence": 0.995,
                "meta": {"matched_by": "portable_intent_contract.force_close_app"},
            }

    m = re.fullmatch(r"(?:minimize|minimise|hide)\s+(.+)", norm)
    if m:
        target = window_target(m.group(1))["name"]
        if _looks_like_app_target(target):
            return {
                "action": "MINIMIZE_APP",
                "args": {"name": target, "target": target},
                "confidence": 0.995,
                "meta": {"matched_by": "portable_intent_contract.minimize_app"},
            }

    # The same for making a named window big. There was no rule for it: "maximise browser" went
    # to the model, which answered MAXIMISE_WINDOW with no name, and ELI maximised its own window.
    m = (re.fullmatch(r"(?:maximize|maximise|full\s?screen)\s+(.+)", norm)
         or re.fullmatch(r"make\s+(.+?)\s+(?:full\s?screen|maximi[sz]ed|as big as (?:possible|it goes))", norm))
    if m:
        target = window_target(m.group(1))["name"]
        if _looks_like_app_target(target):
            return {
                "action": "MAXIMISE_WINDOW",
                "args": {"name": target, "target": target},
                "confidence": 0.995,
                "meta": {"matched_by": "portable_intent_contract.maximise_app"},
            }

    # The volume is read by its one grammar (routing/command_parser.volume_said).
    from eli.execution.routing.command_parser import volume_said
    volume = volume_said(raw)
    if volume is not None:
        return volume

    # "no, play X on youtube" is a correction that asks for X there: its lead is not part of the
    # request. A service is named after "in", "at" or "from" too ("play X in youtube"); those take a
    # service ELI knows, so "play X from the eighties" keeps its words.
    from eli.runtime.command_splitter import without_correction_lead
    _play_norm = normalise_voice_text(without_correction_lead(norm))
    _play_raw = without_correction_lead(raw)
    m = re.fullmatch(r"play\s+(.+?)\s+(?:on|using|with)\s+(.+)", _play_norm)
    if not m:
        m = re.fullmatch(r"play\s+(.+?)\s+(?:in|at|from)\s+(.+)", _play_norm)
        if m and not _known_media_service(m.group(2)):
            m = None
    if m:
        query = _clean_target(m.group(1))
        try:
            raw_m = re.fullmatch(r"play\s+(.+?)[\s,]+(?:on|using|with|in|at|from)\s+(.+)", _play_raw, re.I)
            if raw_m and _normalise_media_service(raw_m.group(2)) == _normalise_media_service(m.group(2)):
                query = _clean_target(raw_m.group(1))
        except Exception:
            pass
        target = _normalise_media_service(m.group(2))
        # "no, play it on youtube": "it" is what was last asked to play, not a title to search for
        from eli.execution.routing.command_parser import what_was_played
        query = what_was_played(query)
    if m and query:
        _kind = media_kind(query)
        return {
            "action": "PLAY_MEDIA",
            "args": {"query": query, "target": target, "service": target, **({"kind": _kind} if _kind else {})},
            "confidence": 0.995,
            "meta": {"matched_by": "portable_intent_contract.play_query_on_target"},
        }

    # Desktop control — defer to core router (MOUSE_CONTROL / SCREEN_LOCATE).
    if re.search(
        r"\b(?:move|nudge|shift|scroll|click|tap|press)\b.*\b(?:mouse|cursor|pointer)\b"
        r"|\b(?:mouse|cursor|pointer)\b.*\b(?:move|nudge|shift|click|scroll)\b"
        r"|\bscroll\s+(?:up|down)\b"
        r"|\b(?:click|tap|press)\s+(?:the\s+)?\w+\s+button\b",
        norm,
    ):
        return None

    implied = implied_song_by_artist(raw, norm)
    if implied:
        return {
            "action": "PLAY_MEDIA",
            "args": {"query": implied, "target": "spotify", "service": "spotify"},
            "confidence": 0.96,
            "meta": {"matched_by": "portable_intent_contract.implied_song_by_artist"},
        }

    wants_generation = re.search(r"\b(?:generate|write|create|build|make)\b", norm)
    wants_code = re.search(r"\b(?:script|code|program|module|tool|app)\b", norm)
    # A question is conversation, not an imperative generation command ("you do have the ability to
    # write your own code?"). Genuine "write a python script" commands are imperatives without a
    # '?'; a real request that slips through still reaches the LLM intent resolver.
    _is_question = "?" in raw
    # Reject if a negation word precedes the generation verb anywhere in the
    # same clause — "not generate a python script", "i did NOT ask you to write
    # a code" (note the words between the negation and the verb).
    _negated_generation = bool(wants_generation and re.search(
        r"\b(?:not|don'?t|didn'?t|doesn'?t|won'?t|never|no|stop|without|"
        r"cannot|can'?t|rather\s+than|instead\s+of)\b[^.?!]*?\b"
        r"(?:generate|write|create|build|make)\b", norm))
    # Reject when the generation verb is subordinate to another verb (it is
    # being talked ABOUT, not commanded) — "the ability to write code",
    # "i asked you to write", "trying to make a tool".
    _subordinate_generation = bool(wants_generation and re.search(
        r"\b(?:ability|able|want|wanted|need|needed|ask|asked|tell|told|"
        r"trying|try|going|supposed|meant|ought|refuse|refused|capable)\b"
        r"[^.?!]*?\bto\s+(?:generate|write|create|build|make)\b", norm))
    # "make sure/sense/it/certain/use/note/do" are conversational idioms, not
    # code-generation commands. Only flag as generation if a real imperative
    # verb (generate/write/create/build) is present, or "make" is not in an idiom.
    _make_idiom_only = bool(
        wants_generation
        and not re.search(r"\b(?:generate|write|create|build)\b", norm)
        and re.search(r"\bmake\s+(?:sure|sense|it|this|that|certain|use|note|do|up|mention)\b", norm)
    )
    # "code" used as a noun referring to existing code (noticed/seen/the/your/their
    # code changes/base/review) is not a generation target.
    _code_is_reference = bool(
        wants_code
        and re.search(r"\b(?:script|code|program)\b", norm)
        and re.search(r"\b(?:noticed|seen|the|your|their|my|our|its|this|that|"
                      r"existing|current|latest)\s+(?:own\s+)?(?:code|script|program)\b", norm)
    )
    if (wants_generation and wants_code
            and not _is_question
            and not _looks_like_generation_complaint(norm)
            and not _negated_generation
            and not _subordinate_generation
            and not _make_idiom_only
            and not _code_is_reference):
        language = infer_script_language(raw)
        return {
            "action": "GENERATE_SCRIPT",
            "args": {
                "description": raw,
                "prompt": raw,
                "language": language,
                "destination": "labs_sim_ide",
                "open_in_labs": True,
                "open_in_ide": True,
            },
            "confidence": 0.995,
            "meta": {"matched_by": "portable_intent_contract.generate_script"},
        }

    return None


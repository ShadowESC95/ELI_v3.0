"""Router helper utilities — path/URL/int extraction + normalisation.

Pure leaf helpers lifted from router_enhanced.py (v3 modularisation). No router
back-references; imported back so the dispatch is unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from eli.utils.log import get_logger

log = get_logger(__name__)


try:
    # Resolved per-machine (honours XDG user-dirs, so localised folder names
    # like ~/Téléchargements work) rather than assuming the English defaults.
    from eli.utils.platform_compat import user_dirs as _user_dirs
    COMMON_DIRS = _user_dirs()
except Exception:
    COMMON_DIRS = {
        "home": "~",
        "desktop": str(Path.home() / "Desktop"),
        "downloads": str(Path.home() / "Downloads"),
        "documents": str(Path.home() / "Documents"),
        "music": str(Path.home() / "Music"),
        "pictures": str(Path.home() / "Pictures"),
        "videos": str(Path.home() / "Videos"),
    }


# Bare site names an "open X" command may use instead of a URL. Lives here rather
# than in the router so the intent stages can resolve a site without importing
# router_enhanced back.
# One table: eli/execution/app_aliases.py (the portable contract and the executor each had their own).
from eli.execution.app_aliases import WEBSITE_ALIASES  # noqa: E402,F401


def _known_dir_path(s: str) -> str:
    """Expanded path when *s* NAMES a well-known user folder, else "".

    The decision counterpart to _expand_common_dir, which always returns
    something (falling back to the raw token) and therefore cannot be used to
    decide whether a phrase IS a folder. Without a predicate, "open downloads"
    was indistinguishable from "open spotify" and got launched as an app —
    answered with "downloads is not installed, shall I install it?".

    Singular/plural tolerant: people say "download directory", the map is keyed
    on "downloads".
    """
    try:
        from eli.utils.platform_compat import known_user_dir as _kud
        return _kud(s)
    except Exception:
        log.debug("known_user_dir unavailable", exc_info=True)
        return ""


def _mk(
    action: str,
    args: Optional[Dict[str, Any]] = None,
    confidence: float = 0.7,
    *,
    matched_by: Optional[str] = None,
    entities: Optional[Dict[str, Any]] = None,
    need_grounding: Optional[bool] = None,
    required_capabilities: Optional[List[str]] = None,
    allow_chat_without_evidence: Optional[bool] = None,
    task_family: Optional[str] = None,
    meta_extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Canonical route object with debug/planner metadata.

    `meta_extra` merges caller-supplied keys into meta for contracts that carry
    fields with no dedicated parameter (response_contract, requires_plan, …).
    The named parameters win, so a caller cannot quietly redefine matched_by.
    """
    out = {
        "action": action,
        "args": args or {},
        "confidence": float(max(0.0, min(1.0, confidence))),
    }
    meta: Dict[str, Any] = dict(meta_extra or {})
    if matched_by:
        meta["matched_by"] = matched_by
    if entities:
        meta["entities"] = entities
    if need_grounding is not None:
        meta["need_grounding"] = bool(need_grounding)
    if required_capabilities:
        meta["required_capabilities"] = list(required_capabilities)
    if allow_chat_without_evidence is not None:
        meta["allow_chat_without_evidence"] = bool(allow_chat_without_evidence)
    if task_family:
        meta["task_family"] = task_family
    if meta:
        out["meta"] = meta
    return out


def _normalize_text(text: str) -> Tuple[str, str]:
    """
    Returns (raw_clean, low_clean)
    Removes weird whitespace/control chars that can break regexes.
    """
    raw = (text or "")
    raw = raw.replace("\u200b", " ").replace("\ufeff", " ")
    raw = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", raw)  # strip control chars
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw, raw.lower()


def _is_likely_url(s: str) -> bool:
    """
    Detects URLs and bare domains (google.com, www.x.y, foo.io/bar).
    Avoids false positives for local files when possible.
    """
    s = s.strip().lower()
    if not s:
        return False

    if s.startswith(("http://", "https://", "www.")):
        return True

    # domain.tld[/...]
    # conservative but broad enough for practical routing
    if re.match(r"^(?:[a-z0-9-]+\.)+[a-z]{2,24}(?:[:/].*)?$", s):
        return True

    return False


def _normalize_url(s: str) -> str:
    s = s.strip()
    if s.lower().startswith(("http://", "https://")):
        return s
    if s.lower().startswith("www."):
        return f"https://{s}"
    if _is_likely_url(s):
        return f"https://{s}"
    return s


def _is_likely_path(s: str) -> bool:
    s = s.strip()
    if not s:
        return False

    # URL check first to avoid "google.com" becoming a file
    if _is_likely_url(s):
        return False

    if s.startswith(("./", "../", "~", "/")):
        return True

    if "/" in s or "\\" in s:
        return True

    if s.lower() in COMMON_DIRS:
        return True

    # file.ext style
    if "." in s:
        ext = s.rsplit(".", 1)[-1]
        if ext.isalpha() and 1 <= len(ext) <= 8:
            return True

    keywords = {
        "directory", "folder", "file", "path", "location",
        "desktop", "documents", "downloads", "music", "pictures", "videos"
    }
    toks = set(re.findall(r"[a-zA-Z]+", s.lower()))
    if toks & keywords:
        return True

    return False


def _extract_path_from_text(raw: str) -> Optional[str]:
    # Remove common command prefixes like "file", "in", etc.
    raw = re.sub(
        r'^(?:fix|read|show|open|list)\s+(?:file|directory)?\s*',
        '',
        raw,
        flags=re.IGNORECASE)
    raw = re.sub(r'\s+in\s+', ' ', raw)  # "file in /path" -> "/path"
    patterns = [
        r'([~/][\w.\- /]+(?:\.\w+)?)',
        r'(\./[\w.\- /]+(?:\.\w+)?)',
        r'(\.\./[\w.\- /]+(?:\.\w+)?)',
        r'([\w.\-]+/\S+)',
        r'([A-Za-z0-9_.-]+\.(?:py|txt|md|json|yaml|yml|csv|pdf|log|ini|cfg|toml|sh))',
    ]
    for p in patterns:
        m = re.search(p, raw)
        if m:
            return m.group(1).strip()
    return None


def _extract_pdf_paths(raw: str) -> list[str]:
    """
    Robust PDF path extractor.

    Fixes the old bug where:
        <path-to-pdf-file>
    became:
        /File.pdf

    Supports:
    - absolute paths
    - ~/ paths
    - ./ and ../ paths
    - multiple PDFs in one prompt
    - basename fallback search for bracketed PDF-content prompts
    """
    import os
    import re
    from pathlib import Path

    text = str(raw or "")
    # ELI PATCH: correct common STT errors
    if 'taste' in text and ('today' in text or 'now' in text):
        text = text.replace('taste','date')
    found: list[str] = []

    full_path_re = re.compile(
        r'(?P<path>(?:~|/|\.{1,2}/)[^\n\r\t"\'<>]*?\.pdf)\b',
        re.IGNORECASE,
    )

    for m in full_path_re.finditer(text):
        p = m.group("path").strip()
        p = p.strip(" ,.;:)]}>")
        p = os.path.abspath(os.path.expanduser(p))
        found.append(p)

    # Basename fallback for prompts like:
    #   [PDF content — Exergetic_Coherence_Revoloution.pdf]
    # Only used to help route; executor still verifies existence.
    name_re = re.compile(r'(?P<name>[A-Za-z0-9_. -]+\.pdf)\b', re.IGNORECASE)
    # Where a bare name is looked for: the working folder, the home folder, the user's own document
    # folders and ELI's documents folder; below that, only inside the user's document folders. Two
    # folders of one machine's owner were named here, and the whole home folder was searched while routing.
    user_folders = [Path.home() / d for d in ("Desktop", "Documents", "Downloads")]
    roots = [Path.cwd(), Path.home(), *user_folders]
    try:
        from eli.core.paths import documents_dir as _documents_dir
        roots.append(Path(_documents_dir()))
    except Exception:
        log.debug("documents folder unavailable for a PDF name", exc_info=True)

    def _look_for(name: str) -> Optional[str]:
        for root in roots:
            if not root.exists():
                continue
            try:
                direct = root / name
                if direct.exists():
                    return str(direct.resolve())
                if root in user_folders:
                    matches = list(root.rglob(name))
                    if matches:
                        return str(matches[0].resolve())
            except Exception:
                continue
        return None

    for m in name_re.finditer(text):
        name = m.group("name").strip().strip(" ,.;:)]}>")
        if any(Path(x).name == name for x in found):
            continue
        # The name may have spaces, so the words before it come with it ("summarise orbit_paper.pdf"):
        # the longest run of the words that names a file is the name.
        words = name.split(" ")
        for start in range(len(words)):
            hit = _look_for(" ".join(words[start:]))
            if hit:
                found.append(hit)
                break

    deduped: list[str] = []
    seen: set[str] = set()
    for p in found:
        if p not in seen:
            seen.add(p)
            deduped.append(p)

    return deduped


def _extract_pdf_path(raw: str) -> Optional[str]:
    """
    Backward-compatible single-PDF wrapper.
    Prefer _extract_pdf_paths() for new routing.
    """
    paths = _extract_pdf_paths(raw)
    return paths[0] if paths else None


def _extract_int(raw: str, default: int, min_value: int,
                 max_value: int) -> int:
    m = re.search(r'(\d+)', raw)
    if not m:
        return default
    n = int(m.group(1))
    return max(min_value, min(max_value, n))



_WALLCLOCK_TIME_PATTERNS = (
    r"\bwhat(?:'?s|\s+is)?\s+(?:the\s+)?time\b",
    r"\bcurrent time\b", r"^(?:the\s+)?clock[?!.\s]*$", r"\b(?:what|check|read|see|look\s+at|show)\b[^.?!]*\bclock\b", r"^(?:(?:i\s+(?:need|want)\s+)?the\s+)?time(?:\s+please)?[?!.\s]*$", r"\btell me the time\b",
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
    r"^(?:(?:i\s+(?:need|want)\s+)?the\s+)?date(?:\s+please)?[?!.\s]*$",
    r"\bwhat(?:'s| is) today\b",
    r"\bwhat day is it\b", r"\bwhat day is this\b",
    r"\bwhat'?s?\s+the\s+day\b", r"\bwhat'?s?\s+the\s+days\b",
    r"\bwhat days\b", r"\bwhat is the day\b", r"\bday is it\b",
    r"\bwhat(?:'?s|\s+is)?\s+(?:the\s+)?(?:\w+\s+){0,2}(?:date|day)\s+(?:is|it\s+is|was)\b",
)


_WALLCLOCK_META_RE = re.compile(
    r"(?i)\b(?:"
    r"care to explain|explain how|explain why|"
    r"why (?:didn'?t|did not|weren'?t|were not|couldn'?t|wouldn'?t) you|"
    r"not smart enough|making a plan|execut(?:e|ing|uing)|incorporat\w+|"
    r"calling that function|by calling (?:that |the )?function|"
    r"how (?:can|do|did) you (?:tell|know|get)|"
    r"should (?:have|'ve) (?:known|checked|called|used)|"
    r"without (?:showing|calling|checking|verifying|making)"
    r")\b"
)


def _has_wallclock_pattern(low: str) -> bool:
    return any(re.search(p, low) for p in _WALLCLOCK_TIME_PATTERNS + _WALLCLOCK_DATE_PATTERNS)


def _is_wallclock_meta_question(low: str) -> bool:
    """True when the user is criticising DATE/TIME behaviour, not asking the clock."""
    if not low or not _has_wallclock_pattern(low):
        return False
    if _WALLCLOCK_META_RE.search(low):
        return True
    # Long compound with explain/why/plan + an embedded date/day clause.
    if len(low.split()) >= 18 and re.search(
        r"\b(explain|why|how come|instead of|plan(?:ning)?|function|incorporat)\b",
        low,
    ):
        return True
    return False


def _is_wallclock_question(low: str) -> bool:
    """Genuine clock/date ask — excludes meta-critique that merely embeds 'what day'."""
    return _has_wallclock_pattern(low) and not _is_wallclock_meta_question(low)


_NEGATORS = ("not", "n't", "never", "aint", "ain't", "isnt", "isn't", "wasnt", "wasn't",
             "dont", "don't", "stop", "no")


def _tone_is_negated(low: str, tone: str) -> bool:
    """True when the emotion word is being DENIED rather than requested.

    "i am not sad" must never set the tone to sad. Without this, ELI's own
    proactive check-in ("you've been reading more sad lately") provoked a reply
    containing the word, that reply was parsed as an order to BE sad, the sad
    reading was recorded, and the check-in fired again — a loop that escalated
    sad×3 to sad×7 in one session, each round telling the user they seemed sad
    because they had just said they were not.

    Scans a short window before the tone word rather than the whole sentence, so
    "not really working, sound cheerful" still sets cheerful.
    """
    try:
        from eli.cognition.emotion_palette import resolve_tone as _rt
    except Exception:
        return False
    words = re.findall(r"[a-z']+", low)
    for i, w in enumerate(words):
        # Find the token that produced this tone, then look back a few words.
        try:
            if _rt(w) != tone:
                continue
        except Exception:
            continue
        window = words[max(0, i - 4):i]
        if any(n in window or any(x.endswith("n't") for x in window) for n in _NEGATORS):
            return True
    return False


# Technical runtime questions ("what model are you running", "how many gpu layers"). The old
# version matched bare model/threads/batch/provider at 0.99, so "what's going on with your world
# model?" went to runtime status. Each alternative now needs the runtime sense of the word.
_RUNTIME_STATUS_RE = re.compile(
    r"\bwhat are you actually running on\b"
    r"|\brunning on right now\b"
    r"|\b(?:what|which|whats|what's)(?:\s+\w+){0,4}\s+model\b"
    r"|\bmodel\s+(?:are|is|do|does)\s+(?:you|it)\b"
    r"|\bmodel\s+(?:name|path|file|size|id|weights|quant\w*)\b"
    r"|\b(?:running|loaded|load(?:ing)?|using|swap\w*\s+to)\s+(?:a\s+|the\s+|which\s+|what\s+)?model\b"
    r"|\bcontext\s+(?:size|window|length)\b|\bn_ctx\b"
    r"|\bgpu\s+layers?\b|\bn_gpu_layers\b|\boffload(?:ed|ing)?\s+layers?\b"
    r"|\bbatch\s+size\b|\bn_batch\b"
    r"|\b(?:how many|number of)\s+threads\b|\bthread\s+count\b|\bn_threads\b"
    r"|\b(?:inference|model|llm|backend)\s+provider\b"
    r"|\bwhich\s+provider\b|\bwhat\s+provider\b",
    re.I,
)



def runtime_status_asked(text: str):
    """The runtime-status words a message asks about (a match of _RUNTIME_STATUS_RE), or None. "what's
    the best model for coding" and "which model has the biggest context window" ask about models in
    general: a bare "what/which ... model" is ELI's when asked of ELI (route_contracts.asks_about_eli) or of
    the model running now ("what model is loaded")."""
    low = str(text or "").lower()
    m = _RUNTIME_STATUS_RE.search(low)
    if (m and re.fullmatch(r"(?:what|which|whats|what's)(?:\s+\w+){0,4}\s+model", m.group(0))
            and not re.search(r"\b(?:loaded|running|in use|being used|mounted|this)\b", low)):
        from eli.execution.route_contracts import asks_about_eli
        if not asks_about_eli(text):
            return None
    return m


__all__ = ['_RUNTIME_STATUS_RE', 'runtime_status_asked', 'COMMON_DIRS', '_known_dir_path', '_mk', '_normalize_text', '_is_likely_url', '_normalize_url', '_is_likely_path', '_extract_path_from_text', '_extract_pdf_paths', '_extract_pdf_path', '_extract_int']

"""Router helper utilities — path/URL/int extraction + normalisation.

Pure leaf helpers lifted from router_enhanced.py (v3 modularisation). No router
back-references; imported back so the dispatch is unchanged.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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
WEBSITE_ALIASES = {
    "youtube": "https://youtube.com",
    "google": "https://google.com",
    "facebook": "https://facebook.com",
    "twitter": "https://twitter.com",
    "x": "https://x.com",
    "instagram": "https://instagram.com",
    "linkedin": "https://linkedin.com",
    "reddit": "https://reddit.com",
    "github": "https://github.com",
    "stackoverflow": "https://stackoverflow.com",
    "wikipedia": "https://wikipedia.org",
    "amazon": "https://amazon.com",
    "ebay": "https://ebay.com",
    "netflix": "https://netflix.com",
    "hulu": "https://hulu.com",
    "twitch": "https://twitch.tv",
    "gmail": "https://mail.google.com",
    "outlook": "https://outlook.live.com",
    "yahoo": "https://mail.yahoo.com",
    "maps": "https://maps.google.com",
    "news": "https://news.google.com",
    "weather": "https://weather.com",
}


def _known_dir_path(s: str) -> str:
    """Expanded path when *s* NAMES a well-known user folder, else "".

    The decision counterpart to _expand_common_dir, which always returns
    something (falling back to the raw token) and therefore cannot be used to
    decide whether a phrase IS a folder. Without a predicate, "open downloads"
    was indistinguishable from "open spotify" and got launched as an app —
    answered with "downloads is not installed, shall I install it?".
    """
    try:
        from eli.utils.platform_compat import known_user_dir as _kud
        return _kud(s)
    except Exception:
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
    roots = [
        Path.cwd(),
        Path.home(),
        Path.home() / "Desktop",
        Path.home() / "Desktop/Physics",
        Path.home() / "Desktop/Physics/Theory_MATHEMATICS",
    ]

    for m in name_re.finditer(text):
        name = m.group("name").strip().strip(" ,.;:)]}>")
        if any(Path(x).name == name for x in found):
            continue

        # If user supplied only a basename, try known local roots.
        for root in roots:
            if not root.exists():
                continue
            try:
                direct = root / name
                if direct.exists():
                    found.append(str(direct.resolve()))
                    break

                # Keep this bounded to likely user document locations.
                matches = list(root.rglob(name))
                if matches:
                    found.append(str(matches[0].resolve()))
                    break
            except Exception:
                continue

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

__all__ = ['COMMON_DIRS', '_known_dir_path', '_mk', '_normalize_text', '_is_likely_url', '_normalize_url', '_is_likely_path', '_extract_path_from_text', '_extract_pdf_paths', '_extract_pdf_path', '_extract_int']

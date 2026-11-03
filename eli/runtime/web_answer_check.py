"""A reply that denies what a web search just found. Live, handed results for "resident evil 2026
movie", the model answered "there is no Resident Evil movie scheduled for 2026 ... I searched. Nothing
came back." When the reply says nothing was found, or that the thing asked about does not exist, and
the results plainly carry it, the reply is not ELI's answer: the results are."""
from __future__ import annotations

import re
from typing import Any, Dict, List

_DENIES = re.compile(
    r"\b(?:there (?:is|are|was|were) (?:no|not any)\b|no (?:such|announced|scheduled|upcoming|results?|record|sign|mention|trace)\b"
    r"|nothing (?:came back|turned up|showed up|was found|found|there)\b|(?:i )?(?:couldn['’]?t|could not|can['’]?t|cannot|didn['’]?t|did not) find\b"
    r"|(?:does|did|do)\s*n[o']?t exist\b|(?:is|are)n['’]?t (?:a|any|one|out|real)\b|has\s*n[o']?t been (?:announced|released|made|confirmed)\b"
    r"|not (?:scheduled|announced|out|released|a real|a thing)\b|(?:search|results?) (?:came back|returned) (?:empty|nothing|with nothing)\b"
    r"|dead end\b|drew a blank\b)", re.I)
_STOP = {"the", "a", "an", "of", "for", "and", "or", "in", "on", "to", "is", "it", "that", "this", "with", "about",
         "new", "newest", "latest", "movie", "film", "search", "web", "me", "please", "what", "who", "when"}


def _words(text: str) -> List[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        if w not in _STOP and len(w) > 1 and w not in out:
            out.append(w)
    return out


def results_carry(query: str, results: List[Dict[str, Any]]) -> bool:
    """Whether any result's title or snippet holds most of the query's own words."""
    words = _words(query)
    if not words:
        return False
    need = max(1, (len(words) + 1) // 2)
    for r in results or []:
        if not isinstance(r, dict):
            continue
        text = " ".join(str(r.get(k) or "") for k in ("title", "body", "snippet", "href", "url")).lower()
        if sum(w in text for w in words) >= need:
            return True
    return False


def denies_what_was_found(reply: str, results: List[Dict[str, Any]], query: str) -> bool:
    """True when the reply says nothing was found or the thing does not exist, and the results carry it."""
    head = " ".join(str(reply or "").split())[:600]
    return bool(_DENIES.search(head)) and results_carry(query, results)


def from_the_results(query: str, results: List[Dict[str, Any]], limit: int = 4) -> str:
    """The results themselves, in words, for when the reply would deny them."""
    lines = []
    for r in (results or [])[:limit]:
        if not isinstance(r, dict):
            continue
        title = " ".join(str(r.get("title") or "").split())
        body = " ".join(str(r.get("body") or r.get("snippet") or "").split())[:220]
        site = re.sub(r"^https?://(?:www\.)?([^/]+).*$", r"\1", str(r.get("href") or r.get("url") or ""))
        if title:
            lines.append(f"- {title}" + (f" — {body}" if body else "") + (f" ({site})" if site else ""))
    if not lines:
        return ""
    return f"What the web has on “{query}” just now:\n" + "\n".join(lines)

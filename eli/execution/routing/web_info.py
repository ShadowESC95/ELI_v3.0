"""Router web/news intents — live web-lookup prepass and news-topic sanitiser. Moved from router_enhanced.py."""
from __future__ import annotations

import re

from eli.utils.log import get_logger
from eli.execution.routing._helpers import _mk  # noqa: F401


_NEWS_TOPIC_NOISE = frozenset({
    "the", "a", "an", "any", "some", "today", "todays", "world", "current",
    "latest", "recent", "news", "what", "whats", "is", "are", "was", "were",
    "it", "me", "you", "us", "them", "that", "this", "please", "now", "thanks",
    "thank", "for", "about", "on", "in", "of", "more", "story", "stories",
})

_NEWS_TOPIC_CARRIER = frozenset({
    "can", "could", "would", "should", "will", "shall", "do", "does", "did",
    "have", "has", "had", "tell", "give", "show", "get", "find", "fetch",
    "let", "know", "want", "ask", "say", "bring", "pull",
    "your", "my", "our", "i", "we", "they", "he", "she", "him", "her",
})


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
    # Meta / clarification questions ABOUT a previous answer are conversation,
    # not new lookups. "how do you know X if you don't know Y", "what do you
    # mean", "why did you say that" must NOT trigger a web search of the literal
    # sentence (that returned generic 'how to release an album' junk). Let CHAT/
    # persona address the contradiction. This wins even if a trigger word like
    # "release date" appears inside the meta-question.
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
        r"\b(search\s+(?:the\s+)?(?:web|internet|online)|search\s+for|search\b|"
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

    query = raw
    m = re.match(
        r"^(?:can\s+you\s+|could\s+you\s+|please\s+|hey\s+)?(?:do\s+a\s+|run\s+a\s+)?"
        r"(?:web\s+search|search\s+the\s+web\s+for|search\s+online\s+for|"
        r"search\s+the\s+internet\s+for|search\s+for|search|look\s+up|google|"
        r"find\s+out|check\s+the\s+internet\s+for|check\s+the\s+web\s+for)\s+(.+)$",
        raw, re.I,
    )
    if m and m.group(1).strip():
        query = m.group(1).strip()
    elif realtime_fact and realtime_fact.start() > 0:
        # No explicit "search for ..." stem, but a real-time fact phrase appears
        # mid-sentence after a conversational/profanity preamble (e.g. "what the
        # fuck are you talking about when is X out"). Trim to the factual ask so
        # the search query is "when is X out", not the whole frustrated sentence.
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
    # Bare search instruction with NO subject ("do a search", "you have access
    # to the web, do a search", "look it up"): the query is pure meta-instruction
    # about searching. Searching the literal sentence returns "how to browse the
    # dark web" garbage. Strip search/meta/stop words; if nothing of substance
    # remains, there is no subject — don't web-search, fall back to CHAT so ELI
    # asks what to look up instead of searching its own instruction.
    _subject = re.sub(
        r"\b(?:you|i|we|can|could|would|please|do|go|run|just|a|an|the|to|for|me|my|now|"
        r"have|has|access|use|using|web|internet|online|search|searching|searches|google|"
        r"googling|look|looking|looks|up|it|this|that|these|those|check|checking|confirm|"
        r"verify|find|finding|out|on|and|or|so|then|actual|actually|real|really)\b",
        " ", query, flags=re.I)
    _subject = re.sub(r"[^a-z0-9]+", " ", _subject, flags=re.I).strip()
    if len(_subject) < 3:
        return None  # no subject → stay CHAT; don't search the meta-instruction
    return _mk("WEB_SEARCH", {"query": query}, 0.92,
               matched_by="web.realtime_lookup", entities={"query": query})


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

"""Canonical scoring + confidence primitives for ELI.

ONE owner for the small numeric helpers that were copy-pasted across retrieval, ranking,
reflection, and the agent bus: text tokenisation + stopwords, term
overlap, and the agent evidence→confidence mappings. Pure, dependency-free, deterministic.

Import from here instead of re-deriving the same maths inline:

    from eli.cognition.scoring import (
        tokenize, STOPWORDS, term_overlap,
        conf_from_flag, conf_from_count,
    )
"""
from __future__ import annotations

import re

# General-purpose retrieval stopwords (seeded from the reranker's set — its behaviour is the
# reference). Topic-detection code that needs a HEAVIER list (reflection/proactive) keeps its
# own; this is the canonical base for relevance scoring.
STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "to", "of", "in", "on", "at",
    "for", "with", "from", "by", "is", "are", "was", "were", "be", "been", "being",
    "what", "which", "who", "whom", "whose", "when", "where", "why", "how",
    "do", "does", "did", "can", "could", "should", "would", "will", "may", "might",
    "about", "into", "over", "under", "again", "more", "most", "some", "any", "all",
    "your", "you", "me", "my", "i", "we", "our", "they", "them", "their",
})

_WORD_RE = re.compile(r"[^a-zA-Z0-9_]+")


def tokenize(text: str) -> list[str]:
    """Lowercase content tokens with stopwords + 1-char noise removed."""
    return [t for t in _WORD_RE.split((text or "").lower())
            if t and t not in STOPWORDS and len(t) > 1]


def term_overlap(query: str, text: str) -> float:
    """Fraction of query content-terms present in `text` (0..1)."""
    q = set(tokenize(query))
    if not q:
        return 0.0
    return len(q & set(tokenize(text))) / len(q)


def jaccard(a_tokens, b_tokens) -> float:
    """How alike two token sets are, 0..1: the share of their content words they have in common."""
    a, b = set(a_tokens or ()), set(b_tokens or ())
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# Maximal marginal relevance (Carbonell & Goldstein, 1998): the share of the choice that goes to
# relevance, the rest to being unlike what is already chosen. Relevance-leaning, the usual setting:
# likeness only breaks near-ties, so five ways of saying one thing do not fill the context.
MMR_LAMBDA = 0.7


def mmr_select(items: list, limit: int, *, text=None, score=None, lam: float = MMR_LAMBDA) -> list:
    """Maximal marginal relevance over an already ranked list. The first keeps its place; each
    next place goes to the item with the most relevance less its likeness to the items already
    chosen (the share of content words in common), weighted lam : 1 - lam. Relevance is the item's
    own ranking score as a share of the best (`score`), so items that scored alike are told apart
    by what they add; with no scores, its rank (1 - rank/n). Nothing is dropped: the same items,
    reordered so look-alikes fall back, cut to `limit`."""
    items = list(items or [])
    n = len(items)
    if n <= 1 or limit <= 0:
        return items[:limit] if limit > 0 else []
    if text is None:
        def text(row):
            return str((row or {}).get("text") or (row or {}).get("content") or "") if isinstance(row, dict) else str(row)
    words = [set(tokenize(text(row))) for row in items]
    given = [score(row) for row in items] if score is not None else []
    if given and all(isinstance(v, (int, float)) for v in given) and max(given) > 0:
        relevance = [max(0.0, float(v)) / float(max(given)) for v in given]
    else:
        relevance = [1.0 - i / n for i in range(n)]
    chosen = [0]
    left = list(range(1, n))
    while left and len(chosen) < n:
        best, best_value = None, None
        for i in left:
            value = lam * relevance[i] - (1.0 - lam) * max(jaccard(words[i], words[j]) for j in chosen)
            if best_value is None or value > best_value:
                best, best_value = i, value
        chosen.append(best)
        left.remove(best)
    return [items[i] for i in chosen][:limit]


# Overlap floor for "genuinely about the query" vs. "merely vouched for." Shared by the
# reranker and the agent bus.
RELEVANCE_OVERLAP_FLOOR = 0.2
RELEVANCE_STRONG_CREDIT = 1.0
RELEVANCE_WEAK_CREDIT = 0.55
RELEVANCE_MIN_CREDIT = 0.2


def relevance_gate(overlap: float, *, strong_signal: bool = False, weak_signal: bool = False) -> float:
    """3-tier credit: full above the overlap floor or strong_signal; partial on weak_signal
    alone; minimal otherwise. strong_signal overrides the floor; weak_signal only matters
    when the floor isn't met."""
    if overlap >= RELEVANCE_OVERLAP_FLOOR or strong_signal:
        return RELEVANCE_STRONG_CREDIT
    if weak_signal:
        return RELEVANCE_WEAK_CREDIT
    return RELEVANCE_MIN_CREDIT


def conf_from_flag(ok: bool, *, hi: float = 0.9, lo: float = 0.2) -> float:
    """Binary evidence → confidence (the 'did the action succeed' agent pattern)."""
    return hi if ok else lo


def conf_from_count(n: int, *, base: float = 0.3, step: float = 0.04,
                    cap: float = 0.9) -> float:
    """Evidence-count → confidence, saturating at `cap` (the 'how many hits' agent pattern)."""
    try:
        n = max(0, int(n))
    except Exception:
        n = 0
    return min(cap, base + n * step)


__all__ = [
    "STOPWORDS", "tokenize", "term_overlap", "jaccard",
    "relevance_gate", "RELEVANCE_OVERLAP_FLOOR",
    "conf_from_flag", "conf_from_count",
    "mmr_select", "MMR_LAMBDA",
]

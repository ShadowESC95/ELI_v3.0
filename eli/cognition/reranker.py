from __future__ import annotations

import time
from typing import Any, Dict, Iterable, List

# Canonical text primitives (one owner — no bespoke stopwords/tokeniser here).
from eli.cognition.scoring import tokenize as _tok


def _as_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)

# Reciprocal Rank Fusion constant. 60 is the value from the original paper and the usual default in
# hybrid search: large enough that the top ranks aren't winner-take-all, small enough that deep
# ranks stop mattering.
RRF_K = 60


def fuse_ranked_lists(lists: "dict[str, list]", *, k: int = RRF_K,
                      key=None) -> "list[dict]":
    """Merge several ranked candidate lists by RANK, not by score.

    Why rank fusion rather than blending the scores: the two retrievers produce
    numbers that are not comparable and, in ELI's case, barely discriminate. FAISS
    similarity here is `1/(1+L2)`, which compressed a real query to 0.559 and pure
    gibberish to 0.524 — 0.035 of separation, with no threshold that could
    separate them. FTS5 emits BM25, an unbounded negative. Any weighted sum of the
    two is arbitrary and needs retuning whenever either side changes.

    RRF sidesteps that entirely: a document scores `sum(1 / (k + rank))` over the
    lists it appears in. Only its POSITION in each list matters, so the scales never
    have to agree — and a document found by both channels naturally outranks one
    found by either alone, which is the property a hybrid retriever exists for.

    `lists` maps a channel name to its ranked candidates (best first). The channel
    each document came from is recorded on the result as `_channels`, so a caller
    can tell a both-channels agreement from a single-channel guess.
    """
    if key is None:
        def key(c):
            text = str((c or {}).get("text") or (c or {}).get("content") or "")
            return text[:220].strip().lower()

    fused: dict = {}
    for channel, items in (lists or {}).items():
        for rank, cand in enumerate(items or []):
            if not isinstance(cand, dict):
                continue
            ident = key(cand)
            if not ident:
                continue
            slot = fused.get(ident)
            if slot is None:
                slot = {"candidate": dict(cand), "rrf": 0.0, "channels": [],
                        "ranks": {}}
                fused[ident] = slot
            slot["rrf"] += 1.0 / (k + rank + 1)
            slot["channels"].append(channel)
            slot["ranks"][channel] = rank
            # Union the metadata when a memory arrives from both channels. A vector hit lacks the
            # id/tags/kind the SQL row has, and comparing text length missed that when both texts
            # matched, so the field-poor record won on arrival order.
            for field, value in cand.items():
                if value in (None, "", []):
                    continue
                if slot["candidate"].get(field) in (None, "", []):
                    slot["candidate"][field] = value

    # the retrievers that ran: a hit's agreement is the share of these that found it (relevance)
    ran = sorted(name for name, items in (lists or {}).items() if items is not None)
    out = []
    for slot in fused.values():
        row = dict(slot["candidate"])
        # 9dp, not 6: at k=60 adjacent ranks differ in the 7th place, and
        # rounding to 6 collapsed distinct ranks into equal scores.
        row["rrf_score"] = round(slot["rrf"], 9)
        row["_channels"] = sorted(set(slot["channels"]))
        row["_channel_ranks"] = slot["ranks"]
        row["_channels_ran"] = ran
        out.append(row)
    out.sort(key=lambda r: r.get("rrf_score", 0.0), reverse=True)
    return out


# The retrievers Memory.recall_memory runs. A hit that does not say which ran (a caller's own list)
# is read against these, with any channel it names.
RETRIEVERS = ("vector", "keyword")


def _get(hit: Dict[str, Any], name: str, default: Any = None) -> Any:
    """A field of the hit, or of the row it was made from (a normalised hit keeps it under meta)."""
    if hit.get(name) is not None:
        return hit.get(name)
    meta = hit.get("meta")
    return meta.get(name, default) if isinstance(meta, dict) and meta.get(name) is not None else default


def agreement(hit: Dict[str, Any]) -> float:
    """How strongly the retrievers that ran agree on the hit, 0..1: its RRF over the best a hit can
    have (first in every one of them); with no RRF, the share of them that found it."""
    found = set(_get(hit, "_channels") or ())
    ran = set(_get(hit, "_channels_ran") or ()) or (set(RETRIEVERS) | found)
    if not ran:
        return 0.0
    rrf = _as_float(_get(hit, "rrf_score", 0.0), 0.0)
    if rrf > 0.0:
        return min(1.0, rrf / (len(ran) / (RRF_K + 1)))
    return len(found & ran) / len(ran)


def relevance(hit: Dict[str, Any], query_tokens: Iterable[str]) -> float:
    """How well a hit answers the question, 0..1, in the units it is measured in: the share of the
    question's content words it holds (coverage), and among the words it does not hold, the
    retrievers' agreement on it counted as at most one word more:

        relevance = coverage + (1 - coverage) * agreement / (words in the question + 1)

    So holding more of what was asked always comes first (a retriever's confidence never beats what
    the user said), and agreement orders what holds the same. A row found by the period the question
    asked about (Memory.memories_between, _source "window") is relevant by being in it."""
    source = str(_get(hit, "source") or _get(hit, "_source") or _get(hit, "kind") or "").lower()
    if source == "window":
        return 1.0
    q = set(query_tokens or ())
    text = str(hit.get("text") or hit.get("content") or "")
    coverage = (len(q & set(_tok(text))) / len(q)) if q else 0.0
    return coverage + (1.0 - coverage) * agreement(hit) / (len(q) + 1)


def prior(hit: Dict[str, Any]) -> float:
    """How likely the hit is wanted, and right, before the question is read: how readily it comes to
    mind (eli/memory/policy.retrievable, from its faded uses, its importance and who said it) or, for
    a row the recall did not measure, its importance; times the chance it is right where one is given
    (a claim's, eli/memory/claims.chance). 0.5 for a hit nothing is known of."""
    p = _get(hit, "accessibility")
    if p is None:
        p = _get(hit, "importance", 0.5)
    chance = _get(hit, "chance")
    return max(0.0, min(1.0, _as_float(p, 0.5) * (_as_float(chance, 1.0) if chance is not None else 1.0)))


def hit_score(hit: Dict[str, Any]) -> float:
    """One hit's score as the ranking left it: the chance it is of use where the outcomes have fitted
    one, else its relevance (rerank_candidates). The one reading for every consumer of a ranked hit."""
    for name in ("recall_chance", "rerank_score"):
        value = _get(hit, name)
        if value is not None:
            return _as_float(value, 0.0)
    return _as_float(_get(hit, "score", 0.5), 0.5)


def rerank_candidates(query: str, candidates: Iterable[Dict[str, Any]], limit: int = 8) -> List[Dict[str, Any]]:
    """The one ranking of recalled evidence (memories, claims, turns, archive rows): relevance first,
    what is known of the hit before the question as its prior.

    Before any outcome is known the order is relevance (relevance()), and among hits equally relevant
    the number of retrievers that found each, then how likely each is wanted and right (prior()),
    then the retrievers' own ranks. Once the next turns
    have said which memories shown went into answers that were endorsed or corrected, the chance a
    hit is of use, fitted on those outcomes (eli/cognition/epistemics, kind recall: the relevance's
    logit with how readily it came to mind, its importance, how much of the question it holds and
    whether more than one retriever found it), decides the order: the weight the priors get is
    learned, never set by hand. Nothing below is a hand-set weight."""
    q_toks = set(_tok(query))
    out: list[dict] = []
    seen: set[str] = set()

    for idx, c in enumerate(candidates or []):
        if not isinstance(c, dict):
            continue

        text = str(c.get("text") or c.get("content") or "").strip()
        if not text:
            continue

        dedupe_key = text[:220].lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)

        t_toks = set(_tok(text))
        overlap = (len(q_toks & t_toks) / max(1, len(q_toks))) if q_toks else 0.0
        channels = _get(c, "_channels") or []

        row = dict(c)
        row["rerank_score"] = round(relevance(c, q_toks), 6)
        row["_prior"] = round(prior(c), 6)
        row["rerank_rank"] = idx
        # what the learned ranking reads (eli/cognition/epistemics, kind recall)
        row["_overlap"] = round(overlap, 4)
        row["_agreement"] = 1.0 if len(channels) > 1 else 0.0
        out.append(row)

    try:
        from eli.cognition.epistemics import RECALL, apply_rows, weights_for
        fit = weights_for(RECALL)
    except Exception:  # noqa: BLE001 - no outcomes to be had: relevance orders
        fit = {"n": 0}
    if fit.get("n"):
        for row in out:
            row["recall_chance"] = round(apply_rows(
                min(1.0 - 1e-3, max(1e-3, _as_float(row.get("rerank_score"), 0.0))),
                {"accessibility": _as_float(_get(row, "accessibility"), 0.0),
                 "importance": _as_float(_get(row, "importance", 0.5), 0.5),
                 "overlap": row["_overlap"], "agreement": row["_agreement"]},
                fit["weights"], fit["shift"], fit["names"]), 6)

    out.sort(key=lambda x: (
        _as_float(x.get("recall_chance", x.get("rerank_score", 0.0)), 0.0),
        len(_get(x, "_channels") or ()),
        _as_float(x.get("_prior", 0.0), 0.0),
        _as_float(_get(x, "rrf_score", 0.0), 0.0),
        _as_float(_get(x, "event_ts", _get(x, "ts", _get(x, "timestamp", 0.0))), 0.0),
    ), reverse=True)

    return out[: int(limit or 8)]

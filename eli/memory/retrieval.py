"""Shared turn retrieval — single owner for semantic + conversation recall.

Both ``AgentBus`` (``BusMemoryAgent``) and ``AgentOrchestrator`` call here so
memory is not searched twice with divergent budgets on the same turn.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)


@dataclass
class TurnRetrievalResult:
    semantic_hits: List[Dict[str, Any]] = field(default_factory=list)
    conv_hits: List[Dict[str, Any]] = field(default_factory=list)
    recent_turns: List[Dict[str, Any]] = field(default_factory=list)
    summaries: List[Dict[str, Any]] = field(default_factory=list)
    contradictions: List[Dict[str, Any]] = field(default_factory=list)
    elapsed_ms: float = 0.0
    cache_key: str = ""
    window_stats: Dict[str, Any] = field(default_factory=dict)
    searched: Dict[str, Any] = field(default_factory=dict)
    # With a time window: every action ELI ran in it, from the evidence ledger.
    actions: List[Dict[str, Any]] = field(default_factory=list)
    # With a time window: the bodies that came and went in ELI's rooms (the world layer's presence
    # records), whole and in order: ranked among the period's memories they were cut.
    presence: List[Dict[str, Any]] = field(default_factory=list)

# Per-process turn cache: key → (monotonic_ts, result)
_TURN_CACHE: Dict[str, tuple[float, TurnRetrievalResult]] = {}
_CACHE_TTL_S = 8.0


def _cache_get(key: str) -> Optional[TurnRetrievalResult]:
    slot = _TURN_CACHE.get(key)
    if slot is None:
        return None
    ts, entry = slot
    if (time.monotonic() - ts) > _CACHE_TTL_S:
        _TURN_CACHE.pop(key, None)
        return None
    return entry


def invalidate_turn_cache(session_id: str = "") -> None:
    if not session_id:
        _TURN_CACHE.clear()
        return
    prefix = f"{session_id}:"
    for k in list(_TURN_CACHE):
        if k.startswith(prefix):
            _TURN_CACHE.pop(k, None)


_THIN_EVIDENCE = 3
# A period question reads every turn and action in the period; the period log picks what fits
# when it is rendered. 72 user turns covered half of one busy day.
_WINDOW_ROWS = 400


def _is_owner(user_id: str) -> bool:
    """Executions recorded without a user (everything before they carried one, and the desktop
    app's own) are the owner's; another API user must not be shown them (eli/memory/policy.is_active_user)."""
    from eli.memory.policy import is_active_user
    return is_active_user(user_id)


def _tunable(key: str, fallback: int) -> int:
    """Tier-scaled tunable (these were a fixed 4)."""
    try:
        from eli.core.cognition_tunables import snapshot as _cog_snapshot
        return max(0, int(_cog_snapshot().get(key, fallback)))
    except Exception:
        return fallback
_EXACT_TOKEN = re.compile(r"[A-Za-z]{2,}-\d{2,}[A-Za-z0-9-]*|\b\d{4,}\b|\"([^\"]{3,60})\"")


def _exact_token_hits(mem: Any, query: str, have: List[Dict[str, Any]], verified_only: bool) -> List[Dict[str, Any]]:
    """Codes, numbers and quoted phrases are looked up as written, so an embedding that blurs them cannot lose them."""
    seen = {h.get("id") for h in have if h.get("id")}
    out: List[Dict[str, Any]] = []
    for m in list(_EXACT_TOKEN.finditer(query or ""))[:3]:
        token = m.group(1) or m.group(0)
        try:
            for h in mem.recall_memory(token, limit=_tunable("cog.mem_exact_token_recall", 6),
                                       verified_only=verified_only) or []:
                if h.get("id") not in seen and token.lower() in str(h.get("text") or h.get("content") or "").lower():
                    seen.add(h.get("id"))
                    out.append(h)
        except Exception:
            log.debug("exact token lookup failed", exc_info=True)
    return out


def _archive_hits(mem: Any, query: str) -> List[Dict[str, Any]]:
    """Faded rows are out of normal recall, not gone: search them only when live evidence is thin, and label them."""
    try:
        return [{"id": f"archive:{r['id']}", "text": r["text"], "content": r["text"], "source": "archive", "kind": "archived",
                 "score": 0.5, "importance": 0.4, "timestamp": r.get("archived_at"), "origin": r.get("origin"),
                 "verification_status": "verified"}
                for r in mem.search_archive(query, limit=_tunable("cog.mem_archive_recall", 8))]
    except Exception:
        log.debug("archive search failed", exc_info=True)
        return []


def _claim_evidence(c: Dict[str, Any]) -> Dict[str, Any]:
    """A claim as a piece of evidence: scored by the chance it is right (its sureness calibrated on
    what became of such claims, eli/memory/claims.chance), marked disputed when it is, and assessed
    for this turn under the turn's request id, so the next turn can settle it (the engine settles
    the claims a reply rested on with the reply)."""
    from eli.memory import claims as _claims
    text = _claims.describe(c)
    disputed = bool(c.get("contested") or c.get("status") == "disputed")
    p = float(c.get("confidence") or 0.0)
    try:
        p = float(_claims.chance(c).p)
    except (OSError, ValueError, ImportError) as failure:
        log.debug("claim chance unavailable: %s", failure)
    try:
        from eli.cognition.epistemics import CLAIM, assess, claim_request_id
        from eli.kernel.request_context import request_id_var, turn_facts_var
        rid = str(request_id_var.get() or "")
        facts = turn_facts_var.get()
        if rid and facts is not None:
            used = facts.setdefault("claims_used", {})
            if str(c["id"]) not in used:
                assess(CLAIM, float(c.get("confidence") or 0.0), request_id=claim_request_id(c["id"], rid),
                       class_=str(c.get("origin") or ""),
                       features={"confirmations": int(c.get("confirmations") or 1), "contested": disputed})
                used[str(c["id"])] = {"p": round(p, 4), "relation": str(c.get("relation") or ""),
                                      "value": str(c.get("value") or ""), "request_id": claim_request_id(c["id"], rid)}
                # the claim the reply will rest on, as a thought on the Evidence Wall, settled with the reply
                try:
                    from eli.cognition.thoughts import record as _thought
                    _thought("claim", f"{str(c.get('relation') or '').replace('_', ' ')}: {c.get('value')}",
                             why=[_claims.basis(c)], chance=p, decision="shown",
                             reason="the question touches it" + (", though it is disputed" if disputed else ""),
                             subject=str(c.get("value") or ""), request_id=rid, ref=claim_request_id(c["id"], rid))
                except (OSError, ValueError, ImportError) as thought_failure:
                    log.debug("claim thought not kept: %s", thought_failure)
    except (OSError, ValueError, ImportError, LookupError) as failure:
        log.debug("claim not assessed for the turn: %s", failure)
    return {"id": f"claim:{c['id']}", "text": text, "content": text,
            "source": "claims", "kind": "claim", "score": p, "importance": p, "chance": p,
            "timestamp": c["recorded_at"], "event_ts": c["valid_from"] or c["recorded_at"],
            "origin": str(c.get("origin") or "user_said"),
            "verification_status": "disputed" if disputed else "verified"}


def _claim_hits(mem: Any, query: str, window: Optional[tuple]) -> List[Dict[str, Any]]:
    """Dated claims as evidence: what held during the period asked about, else the standing claims the question touches."""
    try:
        from eli.memory import claims as _claims
        if window:
            rows = mem.claims_during(window[0], window[1])
        else:
            # Words that say what the question is about. Every question shares "you" and "what" with some
            # stored preference that quotes the user, and the first eight of those crowded out the claim
            # asked for ("what am I currently watching" got eight preferences and no show).
            from eli.memory.unified_retrieval import _STOP
            words = {w for w in re.findall(r"[a-z]{3,}", (query or "").lower())} - _STOP
            broad = bool(re.search(r"\b(?:about me|know about|remember about|my (?:job|work|home|pet))\b",
                                   (query or "").lower()))

            def _fit(c):
                in_relation = len(words & set(re.findall(r"[a-z]{3,}", str(c["relation"]).replace("_", " ").lower())))
                in_value = len(words & set(re.findall(r"[a-z]{3,}", str(c["value"]).lower())))
                return 2 * in_relation + in_value

            rows = [c for c in (mem.claims_current() + mem.claims_disputed()) if broad or _fit(c)]
            rows.sort(key=lambda c: (_fit(c), float(c.get("valid_from") or c.get("recorded_at") or 0)), reverse=True)
        return [_claim_evidence(c) for c in rows[:8]]
    except Exception:
        log.debug("claim evidence unavailable", exc_info=True)
        return []


def retrieve_for_turn(
    mem: Any,
    query: str,
    *,
    user_id: str = "",
    session_id: str = "",
    semantic_limit: int = 12,
    conv_limit: int = 8,
    recent_limit: int = 12,
    summary_limit: int = 4,
    hop2_limit: int = 6,
    merge_cap: int = 24,
    enable_hop2: bool = True,
    rerank: bool = True,
    use_cache: bool = True,
    verified_only: bool = True,
    window: Optional[tuple] = None,
) -> TurnRetrievalResult:
    """Retrieve, optionally deepen, rerank, and dedupe memory evidence for one turn."""
    t0 = time.perf_counter()
    q = (query or "").strip()
    # Every limit is part of the key: the deepening loop repeats the same query with larger limits,
    # and must not be handed the shallower result it just had.
    cache_key = (f"{session_id}:{user_id}:{q.lower()[:240]}:{window}:"
                 f"{semantic_limit}:{conv_limit}:{recent_limit}:{summary_limit}:{hop2_limit}:{merge_cap}:{verified_only}")
    if use_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

    raw_hits: List[Dict[str, Any]] = []
    try:
        raw_hits = list(
            mem.recall_memory(q, limit=int(semantic_limit), verified_only=verified_only) or []
        )
    except Exception as exc:
        log.debug(f"[RETRIEVAL] recall_memory failed: {exc}")

    conv_hits: List[Dict[str, Any]] = []
    try:
        if window:
            # what the user said inside the period asked about, oldest first: all of the owner's turns
            # (the install's earlier ids too), only a member's own for a member
            from eli.memory import policy as _policy
            member = _policy.is_member(user_id)
            conv_hits = [t for t in (mem.get_recent_conversation(
                limit=max(int(conv_limit) * 3, _WINDOW_ROWS), since=window[0], until=window[1],
                role="user", user_id=user_id if member else None) or [])
                if member or not _policy.is_member(t.get("user_id"))]
        else:
            conv_hits = list(mem.search_conversations(q, user_id=user_id, limit=int(conv_limit)) or [])
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    recent: List[Dict[str, Any]] = []
    try:
        from eli.cognition.context_budget import is_recall_question
        from eli.cognition.evidence_format import this_conversation
        recent = list(mem.get_recent_conversation(limit=int(recent_limit), user_id=user_id) or [])
        # A question about the past ("what did we talk about earlier?") keeps earlier sessions'
        # turns. Anything else gets this conversation only, not the last argument.
        if not is_recall_question(q):
            recent = this_conversation(recent)
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    summaries: List[Dict[str, Any]] = []
    try:
        summaries = list(mem.get_session_summaries(user_id=user_id, limit=int(summary_limit)) or [])
    except Exception:
        log.debug("suppressed exception", exc_info=True)

    if enable_hop2 and 0 < len(raw_hits) < 5:
        try:
            from eli.cognition.agent_bus import _memory_seed_terms
            _seed = (raw_hits[0].get("text") or raw_hits[0].get("content") or "")
            _terms = _memory_seed_terms(_seed, k=5)
            if _terms:
                _seen_ids = {h.get("id") for h in raw_hits if h.get("id")}
                _seen_txt = {(h.get("text") or h.get("content") or "")[:80] for h in raw_hits}
                _hop2 = mem.recall_memory(
                    " ".join(_terms), limit=int(hop2_limit), verified_only=verified_only,
                ) or []
                for _h in _hop2:
                    _hid = _h.get("id")
                    _ht = (_h.get("text") or _h.get("content") or "")[:80]
                    if (_hid and _hid in _seen_ids) or _ht in _seen_txt:
                        continue
                    raw_hits.append(_h)
                    _seen_ids.add(_hid)
                    _seen_txt.add(_ht)
                    if len(raw_hits) >= int(merge_cap):
                        break
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    window_stats: Dict[str, Any] = {}
    actions: List[Dict[str, Any]] = []
    presence: List[Dict[str, Any]] = []
    if window:
        from eli.cognition.evidence_format import row_time
        candidates = len(raw_hits)
        added = 0
        try:
            seen = {h.get("id") for h in raw_hits if h.get("id")}
            timed = [h for h in mem.memories_between(window[0], window[1], limit=max(int(merge_cap), 24),
                                                     verified_only=verified_only) if h["id"] not in seen]
            raw_hits += timed
            added = len(timed)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        _before_window = list(raw_hits)
        raw_hits = [h for h in raw_hits if window[0] <= (row_time(h) or 0) < window[1]]
        outside = 0
        if not raw_hits and _before_window:
            # Nothing on the days asked about. Keep the closest matches, dated and marked,
            # so the answer can be "nothing then; the nearest is Fri 02 Oct" instead of an
            # empty context the model fills in (live: it invented the dates).
            raw_hits = [dict(h, outside_window=True) for h in _before_window[:6]]
            outside = len(raw_hits)
        if window and not conv_hits:
            try:
                conv_hits = [dict(t, outside_window=True) for t in
                             (mem.search_conversations(q, user_id=user_id, limit=int(conv_limit)) or [])]
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        # What ELI did in the period, as the executor reported it. ELI's own prose replies stay
        # out of recall (a made-up reply would become history); these are measured results.
        try:
            from eli.runtime.evidence_ledger import actions_between
            actions = actions_between(window[0], window[1], limit=_WINDOW_ROWS, user_id=user_id,
                                      include_unattributed=_is_owner(user_id),
                                      db_path=getattr(mem, "db_path", None))
        except Exception:
            log.debug("action log unavailable", exc_info=True)
        try:
            presence = list(mem.presence_between(window[0], window[1], limit=_WINDOW_ROWS, user_id=user_id) or [])
        except Exception:
            log.debug("presence records unavailable", exc_info=True)
        window_stats = {"since": window[0], "until": window[1], "candidates": candidates,
                        "added_by_time": added, "in_window": len(raw_hits) - outside,
                        "outside_window": outside, "turns": len(conv_hits),
                        "actions": len(actions), "presence": len(presence)}
        log.debug("[RETRIEVAL] time window %s..%s: %d in window (%d of %d semantic candidates + %d found by time), "
                  "%d turns, %d actions",
                  time.strftime("%Y-%m-%d %H:%M", time.localtime(window[0])),
                  time.strftime("%Y-%m-%d %H:%M", time.localtime(window[1])),
                  len(raw_hits), len(raw_hits) - added, candidates, added, len(conv_hits), len(actions))

    claim_hits = _claim_hits(mem, q, window)
    exact_hits = _exact_token_hits(mem, q, raw_hits, verified_only)
    raw_hits += claim_hits + exact_hits
    searched: Dict[str, Any] = {
        "semantic": len(raw_hits) - len(claim_hits) - len(exact_hits), "exact": len(exact_hits),
        "claims": len(claim_hits), "window": bool(window)}
    if len(raw_hits) - len(claim_hits) < _THIN_EVIDENCE:
        archived = _archive_hits(mem, q)
        raw_hits += archived
        searched["archive"] = len(archived)
    else:
        searched["archive"] = None

    contradictions: List[Dict[str, Any]] = []
    if rerank and raw_hits:
        try:
            from eli.cognition.reranker import rerank_candidates
            raw_hits = rerank_candidates(q, raw_hits, limit=len(raw_hits))
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    try:
        from eli.cognition.agent_bus import _detect_contradictions
        contradictions = _detect_contradictions(raw_hits)
    except Exception:
        contradictions = []

    elapsed = (time.perf_counter() - t0) * 1000
    result = TurnRetrievalResult(
        semantic_hits=raw_hits,
        conv_hits=conv_hits,
        recent_turns=recent,
        summaries=summaries,
        contradictions=contradictions,
        elapsed_ms=elapsed,
        cache_key=cache_key,
        window_stats=window_stats,
        searched=searched,
        actions=actions,
        presence=presence,
    )
    if use_cache and q:
        _TURN_CACHE[cache_key] = (time.monotonic(), result)
    return result

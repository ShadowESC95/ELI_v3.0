"""Storage policy: who produced a row, how strong it stays, when it is archived. Pure functions.

Origins: user_said (never archived), eli_said, telemetry, news, tool. Only a known user source is
user_said; anything else is eli_said. Strength is a forgetting
curve reinforced by use; the half-life comes from how often this person actually uses ELI.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Iterable, Sequence

from eli.core.self_provenance import is_bookkeeping_memory

ORIGIN_USER = "user_said"
ORIGIN_ELI = "eli_said"
ORIGIN_TELEMETRY = "telemetry"
ORIGIN_NEWS = "news"
ORIGIN_TOOL = "tool"

_NEWS = {"news_synthesis", "news", "briefing", "news_reflection", "news_digest"}
# What a tool read or the world layer saw: document text, the body arriving in ELI's rooms, a game's
# result. Recorded as seen, never as the user's words.
_TOOL = {"tool", "executor", "observation", "document_reader", "document", "embodiment", "games"}
_ELI = {"assistant", "eli"}
_USER_TAGS = {"user_confirmed", "user_explicit"}
# The user's own words, by the source that stored them: a turn they typed or spoke, what they asked
# ELI to keep ("chat" is the MEMORY_STORE effector's), a correction, their onboarding answers, and
# what ELI pinned from their message this session. Everything else ELI wrote itself: an unknown
# source used to count as the user's own words, so "ELI noticed a pattern" insights, session digests
# quoting the last message and document text were stored as things the user said.
_USER = {"user", "chat", "conversation", "voice_direct", "user_correction", "user_explicit",
         "onboarding_interview", "identity", "identity_extract", "session_context", "relation",
         "runtime_identity_extractor", "runtime_relational_extractor"}
# What the user asked to be kept, by the way it reached memory: the MEMORY_STORE action ("chat"), a
# "remember that…" pin (its tag user_explicit), a correction, an onboarding answer, or a row they
# confirmed. The words of the request are not read here: the route that took them is.
_EXPLICIT = {"chat", "user_explicit", "user_correction", "onboarding_interview"}
# A working-memory row carries the source of the pin it was flushed from among its tags. Besides the
# user sources above, a pin of a recalled memory ("memory_recall", no longer flushed; the older rows
# are the user's statements recalled) and one the turn's fact reader took from the user's message
# ("auto_<kind>", eli/kernel/stages/memory_commit) are the user's words.
_PIN_TAGS = {"working_memory", "session_pin"}
_USER_PINS = {"memory_recall"}

DEFAULT_HALF_LIFE_DAYS = 30.0
MIN_HALF_LIFE_DAYS = 14.0
MAX_HALF_LIFE_DAYS = 120.0
IMPORTANCE_STRETCH = 3.0
REINFORCEMENT = 0.6
PIN_IMPORTANCE = 0.85
MIN_WEIGHT = 0.05
ARCHIVE_WEIGHT = 0.12

_ORIGIN_FACTOR = {ORIGIN_USER: 1.0, ORIGIN_ELI: 0.5, ORIGIN_TOOL: 0.35, ORIGIN_NEWS: 0.25, ORIGIN_TELEMETRY: 0.15}


def _tags(tags) -> set:
    if isinstance(tags, str):
        tags = tags.split(",")
    return {str(t).strip().lower() for t in (tags or []) if str(t).strip()}


def classify_origin(source: str = "user", kind: str = "memory", tags=None, text: str = "") -> str:
    src, knd, tg = str(source or "user").lower(), str(kind or "memory").lower(), _tags(tags)
    if tg & _USER_TAGS:
        return ORIGIN_USER
    if src in _NEWS or knd in _NEWS or "news_reflection" in tg:
        return ORIGIN_NEWS
    if src in _TOOL or knd == "document" or "tool_observation" in tg:
        return ORIGIN_TOOL
    if is_bookkeeping_memory({"kind": knd, "source": src, "tags": ",".join(tg)}):
        return ORIGIN_TELEMETRY
    if src == "working_memory":
        pinned_from = tg - _PIN_TAGS
        if pinned_from & (_USER | _USER_PINS) or any(t.startswith("auto_") for t in pinned_from):
            return ORIGIN_USER
        return ORIGIN_TOOL if pinned_from & _TOOL else ORIGIN_ELI
    return ORIGIN_USER if src in _USER else ORIGIN_ELI


def is_explicit(source: str = "", tags=None) -> bool:
    """The user asked for this to be kept (eli/memory/salience.importance_of counts it as their word)."""
    return str(source or "").lower() in _EXPLICIT or bool(_tags(tags) & _USER_TAGS)


# Bumped when classify_origin changes, so rows labelled by an older rule are labelled again
# (Memory.backfill_policy_fields).
ORIGIN_VERSION = "2"


# ── whose a row is ───────────────────────────────────────────────────────────────────────────────
# Memories, claims, graph relations and vector entries are the owner's ('' in a user_id column) or
# one RBAC member's (their id). A member is an id the API authenticates as a user of its own
# (eli/runtime/api_users); every other id is the owner, as one person on a single-user install
# (ELI VR's requests, the web console, the install's own earlier generated ids). Supplied ids are
# not identities there: any caller can send any. Every member's facts used to be recalled into
# everyone's prompts and a member's claim superseded the owner's.
_MEMBERS_SEEN: list = [None, frozenset()]


def rbac_members() -> frozenset:
    """The ids of the API's users (eli/runtime/api_users), read again whenever its file changes."""
    try:
        from eli.runtime import api_users
        path = api_users._store_path()
        st = path.stat()
    except (OSError, ImportError):
        return frozenset()
    seen = (str(path), st.st_mtime_ns, st.st_size)
    if _MEMBERS_SEEN[0] != seen:
        try:
            members = frozenset(str(u.get("user_id") or "").strip() for u in api_users.list_users()) - {""}
        except Exception:
            members = frozenset()
        _MEMBERS_SEEN[:] = [seen, members]
    return _MEMBERS_SEEN[1]


def _active_user_id() -> str:
    try:
        from eli.kernel.state import get_active_user_id
        return str(get_active_user_id() or "")
    except Exception:
        return ""


def is_member(user_id) -> bool:
    """An RBAC member other than the install's own active user."""
    uid = str(user_id or "").strip()
    return bool(uid) and uid in rbac_members() and uid != _active_user_id()


def owner_key(user_id=None) -> str:
    """The key a row is stored under and read for: '' for the owner, the member's id otherwise.
    With no id given, the turn's own (request_context.user_id_var); a background job is the owner's."""
    if user_id is None:
        try:
            from eli.kernel.request_context import user_id_var
            user_id = user_id_var.get()
        except Exception:
            user_id = None
    return str(user_id).strip() if is_member(user_id) else ""


def scope_sql(column: str, key: str, *, raw_ids: bool = False) -> tuple:
    """(" AND ...", params) keeping only the rows of `key` (owner_key). raw_ids: the table keeps the
    id as it was given (conversation turns), so the owner's rows are every row that is not a member's."""
    if key:
        return f" AND {column} = ?", (key,)
    if not raw_ids:
        return f" AND COALESCE({column}, '') = ''", ()
    members = sorted(rbac_members())
    if not members:
        return "", ()
    return f" AND COALESCE({column}, '') NOT IN ({','.join('?' * len(members))})", tuple(members)


def is_active_user(user_id) -> bool:
    """No id, or the install's active user id: the rule the document index and the action log keep
    for a supplied id (executions recorded without a user are the owner's)."""
    uid = str(user_id or "").strip()
    if not uid:
        return True
    active = _active_user_id()
    return not active or uid == active


KEY_VERSION = "2"
_EDGE_PUNCT = "\"'\u201c\u201d\u2018\u2019()[]{}<>,;:!?."


def normalise_text(text: str) -> str:
    """Lower-case, single-spaced, with sentence punctuation trimmed from each word's edges.

    Signs, symbols and inner marks stay, because they carry meaning: "C++" is not "C", "-5" is not
    "5", "3.5" is not "35".
    """
    words = (w.strip(_EDGE_PUNCT) for w in str(text or "").lower().split())
    return " ".join(w for w in words if any(ch.isalnum() for ch in w))


def text_key(text: str) -> str:
    n = normalise_text(text)
    return hashlib.sha1(n.encode("utf-8")).hexdigest()[:20] if n else ""


def wants_vector_index(origin: str) -> bool:
    return origin != ORIGIN_TELEMETRY


def counts_as_evidence_about_user(origin: str) -> bool:
    return origin == ORIGIN_USER


def adaptive_half_life_days(active_day_gaps: Sequence[float]) -> float:
    """Default scaled by sqrt(median gap between days of use), within limits."""
    gaps = sorted(float(g) for g in active_day_gaps if g is not None and float(g) >= 0)
    if len(gaps) < 5:
        return DEFAULT_HALF_LIFE_DAYS
    mid = len(gaps) // 2
    median = gaps[mid] if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2.0
    return max(MIN_HALF_LIFE_DAYS, min(MAX_HALF_LIFE_DAYS, DEFAULT_HALF_LIFE_DAYS * math.sqrt(max(median, 1.0))))


def strength(now: float, last_touch_ts: float, importance: float, origin: str,
             seen_count: int = 1, recall_count: int = 0,
             half_life_days: float = DEFAULT_HALF_LIFE_DAYS, pinned: bool = False) -> float:
    """2 ** (-days since last touch / half_life); half_life grows with importance, origin and use."""
    if pinned or (float(importance or 0.0) >= PIN_IMPORTANCE and origin == ORIGIN_USER):
        return 1.0
    try:
        days = max(0.0, (float(now) - float(last_touch_ts or 0.0)) / 86400.0)
    except (TypeError, ValueError):
        return 1.0
    imp = max(0.0, min(1.0, float(importance if importance is not None else 0.5)))
    uses = max(0, int(seen_count or 1) - 1) + max(0, int(recall_count or 0))
    half = (float(half_life_days) * _ORIGIN_FACTOR.get(origin, 1.0)
            * (1.0 + IMPORTANCE_STRETCH * imp) * (1.0 + REINFORCEMENT * math.log1p(uses)))
    return max(MIN_WEIGHT, min(1.0, 2.0 ** (-days / max(half, 1e-6))))


def should_archive(origin: str, weight: float, recall_count: int, importance: float) -> bool:
    if origin == ORIGIN_USER or int(recall_count or 0) > 0 or float(importance or 0.0) >= PIN_IMPORTANCE:
        return False
    return float(weight or 0.0) <= ARCHIVE_WEIGHT


def merge_group(rows: Iterable[dict]) -> dict:
    """Earliest event time survives; sightings and recalls sum; latest sighting is last_seen."""
    rows = list(rows)
    firsts = [f for f in (float(r.get("event_ts") or r.get("ts") or 0.0) for r in rows) if f > 0]
    return {
        "seen_count": sum(max(1, int(r.get("seen_count") or 1)) for r in rows),
        "importance": max(float(r.get("importance") or 0.0) for r in rows),
        "event_ts": min(firsts) if firsts else 0.0,
        "last_seen": max(float(r.get("last_seen") or r.get("ts") or 0.0) for r in rows),
        "recall_count": sum(int(r.get("recall_count") or 0) for r in rows),
        "last_recalled": max(float(r.get("last_recalled") or 0.0) for r in rows),
    }


# ── how readily a memory comes to mind ───────────────────────────────────────────────────────────
# The forgetting curve above says how long a row is KEPT. How readily it comes to mind when asked is
# another matter: ten uses spread over months and one use yesterday are not alike, and two memories
# that say nearly the same thing compete for the same cue. Both follow ACT-R's account of
# declarative memory (Anderson & Schooler, 1991), used as engineering, not as a claim about brains:
# a base-level activation summed over a memory's uses, each fading as a power of its age, and a
# share among look-alikes. The recall ranking reads these (eli/memory/memory.py, reranker.py).
ACTR_DECAY = 0.5          # d: how fast each past use fades, the value across ACT-R's literature
DAY = 86400.0
TICK = 1.0 / DAY          # the stored timestamps' own resolution (a second) in days: no age is under it


def base_activation(uses: int, lifetime_days: float, known_ages_days: Sequence[float],
                    *, decay: float = ACTR_DECAY) -> float:
    """ln of a memory's summed, faded uses: each use whose time is known at its age (days), and the
    uses whose times are not kept spread evenly from the oldest known age back to the first encounter
    (the usual approximation when every use's time is not stored). Higher comes to mind more readily;
    -inf for a memory never used at all."""
    d = float(decay)
    known = sorted(max(TICK, float(a)) for a in known_ages_days if a is not None and float(a) >= 0.0)
    total = sum(t ** -d for t in known)
    rest = max(0, int(uses) - len(known))
    life = max(TICK, float(lifetime_days))
    if rest:
        start = known[-1] if known else TICK          # the oldest known use, or just now with none known
        if life > start:
            if abs(1.0 - d) < 1e-9:
                mean = (math.log(life) - math.log(start)) / (life - start)
            else:
                mean = (life ** (1.0 - d) - start ** (1.0 - d)) / ((1.0 - d) * (life - start))
            total += rest * mean                      # the mean of t^-d over [start, life]
        else:
            total += rest * life ** -d
    return math.log(total) if total > 0.0 else -math.inf


def activation(now: float, event_ts: float, last_seen: float, last_recalled: float, seen_count: int,
               recall_count: int, importance: float, origin: str, *, decay: float = ACTR_DECAY) -> float:
    """A row's activation: the base-level activation of its uses, plus the forgetting curve's own
    factors as log terms (the origin's factor and the importance stretch), so what the user stated,
    or said matters, comes to mind more readily, as it is kept longer."""
    now = float(now)
    ages = [(now - float(t)) / DAY for t in (last_seen, last_recalled) if t]
    first = float(event_ts or last_seen or now)
    uses = max(1, int(seen_count or 1)) + max(0, int(recall_count or 0))
    base = base_activation(uses, (now - first) / DAY, ages, decay=decay)
    imp = max(0.0, min(1.0, float(importance if importance is not None else 0.5)))
    return base + math.log(_ORIGIN_FACTOR.get(origin, 1.0)) + math.log1p(IMPORTANCE_STRETCH * imp)


def compete(activations: Sequence[float], likeness: Sequence[Sequence[float]]) -> list:
    """Each activation less the competition of its look-alikes: A_i + ln(share_i), the share being
    e^A_i over e^A_i plus every other e^A_j weighted by how alike j is to i (0..1, the caller's
    measure: the recall uses eli/cognition/scoring.jaccard over content words). A look-alike as
    strong as i halves i's share; one unlike it takes nothing. So two memories of nearly the same
    thing do not both come first, and the stronger of them is the one that does."""
    out = []
    for i, a in enumerate(activations):
        if a == -math.inf:
            out.append(a)
            continue
        rival = 0.0
        for j, b in enumerate(activations):
            weight = float(likeness[i][j]) if j != i else 0.0
            if weight > 0.0 and b != -math.inf:
                rival += weight * math.exp(float(b) - float(a))
        out.append(float(a) - math.log1p(rival))
    return out


def retrievable(activations: Sequence[float]) -> list:
    """Each activation as a chance of coming to mind among these, 0..1: a logistic on the activation
    standardised against the others (ACT-R's retrieval chance, its threshold and noise read off the
    set itself rather than fixed). All alike: a half each. Never used: 0."""
    finite = [float(a) for a in activations if a != -math.inf]
    if not finite:
        return [0.0 for _ in activations]
    mean = sum(finite) / len(finite)
    sd = math.sqrt(sum((a - mean) ** 2 for a in finite) / len(finite)) if len(finite) > 1 else 0.0
    out = []
    for a in activations:
        if a == -math.inf:
            out.append(0.0)
        elif sd <= 0.0:
            out.append(0.5)
        else:
            out.append(1.0 / (1.0 + math.exp(-(float(a) - mean) / sd)))
    return out


# ── how sure a claim about the user is ───────────────────────────────────────────────────────────

def claim_confidence(confirming: Iterable[float], contradicting: Iterable[float] = ()) -> float:
    """How sure ELI is of a claim from how it was borne out: the mean of a Beta posterior with one
    pseudo-count each way, each source counted by its weight (what the user said outright 1, a
    document 0.6, something observed 0.5, ELI's own inference 0.35: eli/cognition/belief.py). One
    user statement gives 0.67, two 0.75, three 0.8, five 0.86; a contradiction pulls it back, and
    the user's own word contradicted by an inference stays ahead of the inference. Never 1: no number
    of sources makes a claim certain."""
    c = sum(max(0.0, float(w)) for w in confirming)
    x = sum(max(0.0, float(w)) for w in contradicting)
    return (1.0 + c) / (2.0 + c + x)

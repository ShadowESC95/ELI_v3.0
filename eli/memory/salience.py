"""
eli/memory/salience.py
──────────────────────
How much a memory matters: its importance, from what can be measured about it.

Importance is the chance the memory will be wanted again, kept as the mean of a Beta posterior over
the evidence for it, the way a claim's sureness is kept (eli/memory/policy.claim_confidence). It used
to be a keyword score (+0.30 for "remember", +0.20 for "i am", +0.18 for "my dog") that knew nothing
of other languages, made most first-person chatter a "fact", and was never checked against use; and a
surprise raised whatever a keyword search found about its subject, so every failed song request made
the memories naming that song matter more ("importance raised on 4 rows (surprise: action)").

The evidence, each piece counted at its source's weight (eli/cognition/belief.PROVENANCE_WEIGHT, the
weights claims and beliefs are counted by):

  asked to keep    the user asked for it to be kept: a "remember that…" request, the MEMORY_STORE
                   action, a correction, an onboarding answer (eli/memory/policy.is_explicit), their
                   own word: user_explicit
  stated about     a statement the user makes about themselves (states_about_user: a first-person
  themselves       statement, not a question, a request, a wish or a hedge), counted by its novelty:
                   one less the chance ELI already gave it. A fact ELI held at 0.8, said again, adds
                   0.2; a new fact, or a changed one, adds its whole weight. That is the
                   Rescorla–Wagner prediction error (Rescorla & Wagner, 1972): the surprise of the
                   statement against what ELI holds (novelty).
  what became of   later, the answers it went into, endorsed or corrected (Memory.record_recall_outcome,
  it               settled from the next turn), which the day's upkeep folds in (Memory.promote_used).

    importance = (1 + evidence for) / (2 + evidence for)       Beta(1, 1)'s mean; nothing stated: 0.5

A surprise that arrives later (note_surprise) is the same signal: the memory that holds the news (the
correction, the changed fact) matters more by the size of the prediction error. The memories that held
the belief that failed are not raised: a correction counts against them (Memory.record_recall_outcome),
and an action that failed is a fact about the action (eli/runtime/action_outcomes), not about the
memories that happen to name what it was for.

    importance_of(text, source, kind, tags, ...)   a new memory's importance, and the evidence for it
    states_about_user(text)                        the user states something about themselves
    stated_facts(text)                             the facts ELI's readers read from it
    is_question(text)                              it asks rather than states
    note_surprise(kind, magnitude, ...)            a prediction missed: the memories that hold the news
                                                   matter more, and a thought of it is kept
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)

GAIN = 0.25                 # the learning rate: the share of the remaining distance to one a full surprise covers


def _weight(provenance: str) -> float:
    from eli.cognition.belief import PROVENANCE_WEIGHT
    return float(PROVENANCE_WEIGHT.get(provenance, PROVENANCE_WEIGHT["unknown"]))


def beta_mean(evidence_for: float, evidence_against: float = 0.0) -> float:
    """The mean of Beta(1 + for, 1 + against): 0.5 with nothing known either way, never 0 or 1."""
    from eli.memory.policy import claim_confidence
    return claim_confidence([max(0.0, float(evidence_for))], [max(0.0, float(evidence_against))])


# ── what a message states ─────────────────────────────────────────────────────────────────────────
# The words below are the grammar of English statements (who is speaking, how a question or a wish is
# put), not topics: what the statement is about is never listed.

# A question to ELI: it ends asking, or opens the way an English question does.
_ASKS = re.compile(r"^(?:what|who|whom|whose|when|where|why|how|which|whether|is|are|was|were|am|do|does|did|"
                   r"can|could|will|would|should|shall|may|might|have|has|had|tell\s+me)\b", re.I)
# Words that only open a sentence ("by the way, I'm…", "oh and my…"): taken off before the speaker is read.
_OPENER = re.compile(r"^(?:(?:by\s+the\s+way|btw|fyi|well|so|also|oh|and|but|anyway|actually|honestly|ok|okay|yeah|"
                     r"yes|no|hey|hi|right|just\s+so\s+you\s+know|for\s+the\s+record)\b[\s,:-]*)+", re.I)
# The user speaking of themselves: the clause's subject is the first person.
_FIRST_PERSON = re.compile(r"^(?:i|i['’]m|im|i['’]ve|i['’]d|i['’]ll|we|we['’]re|we['’]ve|my|our|mine)\b", re.I)
# A clause put to ELI ("I want you to…", "I need you…"): about the exchange, not the user's life.
_TO_ELI = re.compile(r"\b(?:you|your|yours|u)\b", re.I)


def _clauses(text: str) -> List[str]:
    return [c.strip() for c in re.split(r"(?<=[.!?])\s+|\n+|;\s*|,\s+(?=(?:and|but)\s)", str(text or "")) if c.strip()]


def is_question(text: str) -> bool:
    """The text asks rather than states: it ends with a question mark or opens as a question does."""
    t = _OPENER.sub("", str(text or "").strip()).strip()
    return bool(t) and (t.endswith("?") or bool(_ASKS.match(t)))


def _hedged(text: str) -> bool:
    from eli.memory.claims import _HEDGED
    return bool(_HEDGED.search(str(text or "")))


def stated_facts(text: str) -> List[Dict[str, Any]]:
    """The facts about the user the message states, as ELI's two readers read them: the claims reader
    (eli/memory/claims.extract: where they live and work, their pets, what they are doing) and the
    knowledge graph's (eli/memory/knowledge_graph.read_triples: tastes, kinds, possessions, names).
    Each {"relation", "value", "store"}; [] for a question, a hedge or anything about someone else."""
    out: List[Dict[str, Any]] = []
    try:
        from eli.memory import claims as _claims
        out.extend({"relation": c["relation"], "value": c["value"], "store": "claims"}
                   for c in _claims.extract(text) if not c.get("retire"))
    except (ImportError, ValueError, TypeError, KeyError) as failure:
        log.debug("claims reader unavailable: %s", failure)
    try:
        from eli.memory.knowledge_graph import read_triples
        seen = {(f["relation"], str(f["value"]).lower()) for f in out}
        for subject, predicate, obj in read_triples(text, source="user"):
            if str(subject).lower() == "user" and (predicate, str(obj).lower()) not in seen:
                seen.add((predicate, str(obj).lower()))
                out.append({"relation": predicate, "value": obj, "store": "graph"})
    except (ImportError, ValueError, TypeError) as failure:
        log.debug("graph reader unavailable: %s", failure)
    return out


def states_about_user(text: str) -> bool:
    """The user states something about themselves: a fact one of ELI's readers reads (stated_facts),
    or a clause whose subject is the first person that is not a question, a hedge or a wish, is not
    put to ELI ("I want you to…") and says something of something (two content words at least: a
    statement relates the speaker to what it is about). Questions ("what is my dog's name?"),
    requests ("remind me…"), "I can't remember where…" put as a question, and chit-chat are not."""
    t = str(text or "").strip()
    if not t or is_question(t):
        return False
    if stated_facts(t):
        return True
    from eli.cognition.scoring import tokenize
    for clause in _clauses(t):
        body = _OPENER.sub("", clause).strip()
        if (_FIRST_PERSON.match(body) and not is_question(body) and not _hedged(body)
                and not _TO_ELI.search(body) and len(tokenize(body)) >= 2):
            return True
    return False


# ── novelty: what the statement tells ELI that it did not already hold ─────────────────────────────

def _held_chance(fact: Dict[str, Any], held_claims: List[Dict[str, Any]], graph_holds) -> float:
    """The chance ELI already gave this fact: the sureness of the same value standing as a claim, 1 for a
    relation the graph already holds live, 0 for a fact ELI did not hold (or held otherwise: a change)."""
    if fact.get("store") == "claims":
        from eli.memory.claims import _same
        for c in held_claims:
            if c.get("relation") == fact["relation"] and _same(c.get("value"), fact["value"]):
                return max(0.0, min(1.0, float(c.get("confidence") or 0.0)))
        return 0.0
    try:
        return 1.0 if graph_holds and graph_holds(fact["relation"], fact["value"]) else 0.0
    except (OSError, ValueError, TypeError) as failure:
        log.debug("graph not read for novelty: %s", failure)
        return 0.0


def novelty(text: str, *, held_claims: Optional[List[Dict[str, Any]]] = None, graph_holds=None) -> Optional[float]:
    """How much of what the text states ELI did not already hold, 0..1: one less the chance ELI gave
    its least-known fact (the statement's prediction error). None when its readers read no fact (ELI
    holds nothing of the kind to compare it with)."""
    facts = stated_facts(text)
    if not facts:
        return None
    return max(1.0 - _held_chance(f, list(held_claims or []), graph_holds) for f in facts)


def importance_of(text: str, *, source: str = "user", kind: str = "memory", tags=None, origin: str = "",
                  held_claims: Optional[List[Dict[str, Any]]] = None, graph_holds=None) -> Dict[str, Any]:
    """A new memory's importance from the evidence for it (module docstring): {"importance", "asked",
    "stated", "novelty"}. Only what the user said is evidence of what matters to them; any other
    origin starts at nothing known (0.5) and is moved by what becomes of it."""
    from eli.memory import policy as _policy
    origin = origin or _policy.classify_origin(source, kind, tags, text)
    out: Dict[str, Any] = {"asked": False, "stated": False, "novelty": None}
    evidence = 0.0
    if origin == _policy.ORIGIN_USER:
        if _policy.is_explicit(source, tags):
            out["asked"] = True
            evidence += _weight("user_explicit")
        if states_about_user(text):
            out["stated"] = True
            new = novelty(text, held_claims=held_claims, graph_holds=graph_holds)
            out["novelty"] = new
            evidence += _weight("user_explicit") * (1.0 if new is None else new)
    out["importance"] = round(beta_mean(evidence), 4)
    return out


# ── surprise ──────────────────────────────────────────────────────────────────────────────────────

def importance_gain(magnitude: float) -> float:
    """The importance a surprise of this size adds: GAIN times the error, 0..GAIN."""
    return GAIN * max(0.0, min(1.0, float(magnitude or 0.0)))


def note_surprise(kind: str, magnitude: float, *, subjects: Iterable[str], why: str, user_id: str = "",
                  request_id: str = "", now: Optional[float] = None, db_path=None,
                  memory_ids: Iterable[Any] = ()) -> Dict[str, Any]:
    """Something did not fit what ELI believed. The memories that hold the news (`memory_ids`: the
    correction, the changed fact) matter more by the surprise; `subjects` are what it was about, for
    ELI's curiosity and the thought kept in the Anomaly Room. Nothing is looked up by subject: the
    memories that merely name it are not what was learned. {"touched": ids, "gain": the gain}."""
    gain = importance_gain(magnitude)
    named = [str(s).strip() for s in (subjects or []) if str(s).strip()]
    touched: List[int] = sorted({int(i) for i in (memory_ids or ()) if str(i).isdigit() and int(i) > 0})
    if gain <= 0.0:
        return {"touched": [], "gain": gain}
    if touched:
        try:
            from eli.memory.memory import get_memory
            mem = get_memory(db_path) if db_path else get_memory()
            if not mem.raise_importance(touched, gain, reason=f"surprise: {kind}"):
                touched = []
        except Exception as failure:  # noqa: BLE001 - the store refusing the raise leaves the thought
            log.debug("importance not raised: %s", failure)
            touched = []
    try:
        # ELI's world hears it: its curiosity is the mean size of the sitting's surprises
        # (eli/world/agency/autonomy_engine.py), and the subjects are what it is curious about
        from eli.world.world_event_bus import fire_world_event
        fire_world_event("surprise", "salience", str(why or kind)[:200],
                         {"magnitude": max(0.0, min(1.0, float(magnitude or 0.0))), "subjects": named[:3]})
    except (OSError, ValueError, TypeError, ImportError, RuntimeError) as failure:  # noqa: BLE001 - the world is never in the way of the signal
        log.debug("surprise not heard by the world: %s", failure)
    try:
        from eli.cognition.thoughts import record
        record("surprise", f"{str(why or kind).strip()}", why=[f"{kind} did not fit", f"surprise {float(magnitude):.0%}"],
               chance=max(0.0, min(1.0, float(magnitude or 0.0))), decision="raised" if touched else "noted",
               reason=(f"{len(touched)} memor{'y' if len(touched) == 1 else 'ies'} holding it "
                       f"matter {gain:.0%} more") if touched else "kept as something to be curious about",
               subject=", ".join(named[:3]), request_id=request_id, user_id=user_id, now=now, db_path=db_path)
    except (OSError, ValueError, TypeError, ImportError) as failure:  # noqa: BLE001 - the thought store is never in the way of the signal
        log.debug("surprise thought not kept: %s", failure)
    return {"touched": touched, "gain": gain}


__all__ = ["GAIN", "beta_mean", "importance_gain", "importance_of", "is_question", "note_surprise", "novelty",
           "stated_facts", "states_about_user"]

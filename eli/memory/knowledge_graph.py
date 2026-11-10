from __future__ import annotations

import os
from eli.core.paths import get_paths
"""
ELI Knowledge Graph — SQLite-backed entity-relation store.
Lives inside user.sqlite3 alongside the memories/FTS5 tables.

Schema
------
  kg_entities  (id, name, type, aliases, description, confidence, ts)
  kg_relations (id, subject_id, predicate, object_id, weight, source, ts)

Predicate vocabulary (open — any string is valid, these are conventions):
  is_a, has_name, nickname, works_on, uses, prefers, knows, located_in,
  created_by, part_of, related_to, caused_by, contradicts

Query API
---------
  kg = get_knowledge_graph()
          kg.search_entities("developer")     # → fuzzy name/description match
  """


import sqlite3
import threading
import time
import re

# Common English words that should never become KG entities
_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "shall", "can", "not", "no", "nor",
    "and", "or", "but", "if", "in", "on", "at", "to", "for", "of", "with",
    "by", "from", "up", "about", "into", "through", "after", "it", "its",
    "this", "that", "these", "those", "i", "me", "my", "we", "us", "our",
    "you", "your", "he", "him", "his", "she", "her", "they", "them", "their",
    "what", "which", "who", "whom", "when", "where", "why", "how", "all",
    "just", "so", "then", "than", "also", "very", "too", "some", "any",
    "also", "yes", "yeah", "ok", "okay", "hey", "hi", "hello", "well",
    # quantifiers and frequency words: "Nobody uses cash anymore" made an entity "Nobody"
    "nobody", "everybody", "everyone", "someone", "somebody", "anyone", "anybody", "noone",
    "nothing", "everything", "something", "anything", "people", "none", "each", "every",
    "many", "most", "never", "always", "often", "sometimes", "usually", "still",
})

# Family / pet relationship nouns for possessive-fact extraction
# ("my dog Biscuit", "Biscuit (my dog)"). Maps surface word -> normalised relation.
_REL_NORMALISE = {
    "puppy": "dog", "pup": "dog", "kitten": "cat", "kitty": "cat",
    "mum": "mother", "mom": "mother", "mummy": "mother", "mommy": "mother",
    "dad": "father", "daddy": "father", "kid": "child", "baby": "child",
    "hubby": "husband", "fiancee": "fiance",
}
_REL_WORDS = (
    "dog", "puppy", "pup", "cat", "kitten", "kitty", "pet", "horse", "bird",
    "wife", "husband", "hubby", "partner", "spouse", "fiance", "fiancee",
    "girlfriend", "boyfriend",
    "son", "daughter", "kid", "child", "baby",
    "mother", "father", "mum", "mom", "mummy", "mommy", "dad", "daddy",
    "brother", "sister", "friend", "colleague", "boss", "roommate",
)
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


from eli.utils.log import get_logger
log = get_logger(__name__)

_INSTANCE: Optional["KnowledgeGraph"] = None
_LOCK = threading.Lock()


# ── Schema ──────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS kg_entities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    type        TEXT    DEFAULT 'concept',
    aliases     TEXT    DEFAULT '',
    description TEXT    DEFAULT '',
    confidence  REAL    DEFAULT 1.0,
    ts          REAL    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS kg_entities_name ON kg_entities(LOWER(name));

CREATE TABLE IF NOT EXISTS kg_relations (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id  INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    predicate   TEXT    NOT NULL,
    object_id   INTEGER NOT NULL REFERENCES kg_entities(id) ON DELETE CASCADE,
    weight      REAL    DEFAULT 1.0,
    source      TEXT    DEFAULT 'inferred',
    ts          REAL    NOT NULL,
    user_id     TEXT    DEFAULT ''
);
CREATE INDEX IF NOT EXISTS kg_rel_subj ON kg_relations(subject_id);
CREATE INDEX IF NOT EXISTS kg_rel_obj  ON kg_relations(object_id);

CREATE VIRTUAL TABLE IF NOT EXISTS kg_entities_fts
    USING fts5(name, aliases, description, content='kg_entities', content_rowid='id');

CREATE TRIGGER IF NOT EXISTS kg_ent_ai AFTER INSERT ON kg_entities BEGIN
    INSERT INTO kg_entities_fts(rowid, name, aliases, description)
    VALUES (new.id, new.name, COALESCE(new.aliases,''), COALESCE(new.description,''));
END;
CREATE TRIGGER IF NOT EXISTS kg_ent_au AFTER UPDATE ON kg_entities BEGIN
    INSERT INTO kg_entities_fts(kg_entities_fts, rowid, name, aliases, description)
    VALUES ('delete', old.id, old.name, COALESCE(old.aliases,''), COALESCE(old.description,''));
    INSERT INTO kg_entities_fts(rowid, name, aliases, description)
    VALUES (new.id, new.name, COALESCE(new.aliases,''), COALESCE(new.description,''));
END;
CREATE TRIGGER IF NOT EXISTS kg_ent_ad AFTER DELETE ON kg_entities BEGIN
    INSERT INTO kg_entities_fts(kg_entities_fts, rowid, name, aliases, description)
    VALUES ('delete', old.id, old.name, COALESCE(old.aliases,''), COALESCE(old.description,''));
END;
"""


def _db_path() -> Path:
    try:
        from eli.core.paths import knowledge_graph_db_path
        return Path(knowledge_graph_db_path())
    except Exception:
        return Path(os.environ.get("ELI_MEMORY_DB", os.environ.get("ELI_MEMORY_DB_PATH", str(get_paths().user_db))))


# ── Connection helper ────────────────────────────────────────────────────────

def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL where the filesystem allows it, DELETE where it doesn't (NTFS/exFAT/
    # network mounts) — the pragma used to live in _DDL, which made the whole
    # schema build fail readonly on a WAL-hostile portable install.
    from eli.core.sqlite_util import apply_pragmas
    apply_pragmas(conn, db_path=str(path), synchronous="NORMAL")
    return conn


# ── KnowledgeGraph class ─────────────────────────────────────────────────────

_NOT_A_KIND = frozenset("""not just still only well actually your the in all good happy high awake
pretty certain it doing saying so too very really
bit little tad touch wee lot smidge fair""".split())   # "I'm a bit tired" is a state, not a kind

# The conflict handler retires a relation by setting its weight to RETIRED_WEIGHT; anything at or
# under LIVE_WEIGHT is retired and never shown.
RETIRED_WEIGHT = 0.05
LIVE_WEIGHT = 0.1


# Point-in-time activities change constantly; the bitemporal claims store (eli.memory.claims,
# CHANGES_OFTEN) is their authority and recall surfaces the CURRENT one. A durable KG attribute for
# them goes stale: "watching the movie Oppenheimer" was still shown, re-dated, while the user was on
# a different film. These are hidden from the prompt so the claims store speaks for them.
_VOLATILE_ACTIVITY_VERBS = ("watching", "watched", "playing", "played", "reading", "read",
                            "listening", "listening_to", "listened")


def _junk_name_relation(rel: Dict[str, Any]) -> bool:
    """A relation an earlier extractor stored that isn't a durable fact: a has_name/nickname whose
    value isn't a name ("listed as unknown"), an is_a that is a mood or an activity ("going to bed
    now"), or a has_attribute that is a volatile activity ("watching the movie X" — the claims store
    owns those). Hidden from the prompt rather than deleted."""
    predicate = str(rel.get("predicate") or "").lower()
    obj = str(rel.get("object") or "").strip()
    if predicate == "is_a":
        # A kind is a noun phrase ("an engineering student", "a sibling"); a state ("a bit tired") opens
        # with a degree word, and an activity an earlier extractor stored ("going to bed now", "trying
        # my best") carries function words a kind never has.
        words = [w.lower() for w in obj.split()]
        return (not words or words[0] in _NOT_A_KIND or len(words) > 4
                or any(w in _STOPWORDS for w in words[1:]))
    if predicate == "has_attribute":
        first = (obj.split() or [""])[0].lower()
        return first in _VOLATILE_ACTIVITY_VERBS
    if predicate not in ("has_name", "nickname"):
        return False
    try:
        from eli.runtime.identity_validation import normalize_identity_candidate
        return not normalize_identity_candidate(obj)
    except Exception:
        return False


def _as_of_day(rel: Dict[str, Any]) -> str:
    try:
        return " (as of " + time.strftime("%d %b %Y", time.localtime(float(rel.get("ts")))) + ")"
    except (TypeError, ValueError):
        return ""


def _who(user_id: Optional[str] = None) -> str:
    """The user a relation is stored under or read for: eli/memory/policy.owner_key."""
    from eli.memory.policy import owner_key
    return owner_key(user_id)


def live_relation(rel: Dict[str, Any]) -> bool:
    """A relation the graph still stands by: not retired by a correction and not junk an earlier
    extractor stored (_junk_name_relation). Every reader of relations goes through this, so what the
    one-hop block hides cannot come back through another (the multi-hop chains used to show all)."""
    try:
        weight = float(rel.get("weight", 1.0) if rel.get("weight") is not None else 1.0)
    except (TypeError, ValueError):
        weight = 1.0
    return weight > LIVE_WEIGHT and not _junk_name_relation(rel)


def relation_line(subject: str, predicate: str, obj: str, ts: Any = None) -> str:
    """One relation as the prompt shows it, dated by when the graph learned it."""
    from eli.cognition.evidence_format import as_of
    return f"{subject} —[{predicate}]→ {as_of(obj, ts)}{_as_of_day({'ts': ts})}"


# ── statements: who holds what, read clause by clause ───────────────────────────────────────────
# A preference or a kind is the user's only when a first-person holder stands right before the verb
# ("I really like X", "user prefers X"): "My brother likes football" is not theirs. A negation
# between the holder and the verb turns a liking into a dislike ("I don't like mushrooms" was stored
# as a preference for "mushrooms."), a conditional with "to" is a wish and no taste ("I'd like to
# book a table"), and an object ends where its clause does.
_OBJ = r"(?P<obj>[A-Za-z0-9][A-Za-z0-9 _\-\.'’]{1,39}?)"
_OBJ_END = (r"(?=\s+(?:but|because|though|although|whereas|unless|except|when|if|while)\b"
            r"|\s+(?:and|or)\s+(?:i|i'm|i'd|i've|my|he|she|they|we|you)\b"
            r"|\s+(?:a\s+lot|a\s+bit|so\s+much|very\s+much|too\s+much|as\s+well|too)(?=[\s.,;:!?]|$)"
            # when it holds is not part of what is liked: "olives any more", "olives again"
            r"|\s+(?:any\s*more|again|now|these\s+days|nowadays|lately|recently)(?=[\s.,;:!?]|$)"
            r"|[,;:!?\n]|\.(?:\s|$)|$)")
_ADV = (r"(?:\s+(?:really|actually|still|also|just|truly|honestly|generally|usually|always|definitely|"
        r"quite|totally|absolutely|much|kind\s+of|sort\s+of))*")
_NEGATION = re.compile(r"\b(?:not|never|no|nobody|noone|nothing|none|neither|nor|hardly|barely|rarely|seldom)\b"
                       r"|n['’]t\b", re.I)
_LIKING = ("prefer", "like", "love", "enjoy", "adore")
_DISLIKING = ("dislike", "hate", "detest", "loathe")
_STATEMENT_PATTERNS: List[Tuple[str, "re.Pattern"]] = [
    ("taste", re.compile(
        rf"\b(?P<holder>i|user)(?P<aux>['’]d|\s+would|\s+do|\s+does|\s+did)?{_ADV}"
        rf"(?:\s+(?:not|never|no\s+longer)|n['’]t)?{_ADV}\s+(?P<verb>{'|'.join(_DISLIKING + _LIKING)})s?"
        rf"(?P<not_to>\s+not\s+to)?\s+{_OBJ}{_OBJ_END}", re.I)),
    # "I am a X" / "I'm an X". The article is required: without it every "I'm going to bed now" /
    # "I'm not happy" became a fact (44 of a user's 83 relations).
    ("is_a", re.compile(rf"\b(?P<holder>i)(?:\s+am|['’]m)\s+(?P<verb>an?)\s+(?P<obj>[A-Za-z][a-zA-Z ]{{2,30}}?){_OBJ_END}",
                        re.I)),
    ("works_on", re.compile(rf"\b(?P<subj>[A-Za-z][a-zA-Z0-9]{{1,20}})\s+(?P<verb>works\s+on)\s+{_OBJ}{_OBJ_END}", re.I)),
    ("uses", re.compile(rf"\b(?P<subj>[A-Za-z][a-zA-Z0-9]{{1,20}})\s+(?P<verb>uses?)\s+{_OBJ}{_OBJ_END}", re.I)),
]
_CLAUSE_START = re.compile(r"\b(?:but|because|though|although|whereas|unless|and|or|so)\s+", re.I)


def statements(text: str) -> List[Tuple[str, str, str]]:
    """(subject, predicate, object) for each plain statement in `text`: a question or a hedged
    sentence ("maybe I'd like X", eli/memory/claims._HEDGED) states nothing. A liking negated is a
    dislike, a dislike negated is nothing anyone can say; a negated "uses"/"works on" ("Nobody uses
    cash anymore", "Sam never uses Vim") and a mood stored as a kind ("I'm a bit tired") are skipped."""
    from eli.memory.claims import _HEDGED
    out: List[Tuple[str, str, str]] = []
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", str(text or "")):
        sentence = sentence.strip()
        if not sentence or sentence.endswith("?") or _HEDGED.search(sentence):
            continue
        for kind, pattern in _STATEMENT_PATTERNS:
            for m in pattern.finditer(sentence):
                obj = m.group("obj").strip(" .,;:!?-_'\"’")
                first = (obj.split() or [""])[0].lower()
                # "I love my dog", "I like it when...": a pronoun or a possessive names no thing
                if len(obj) < 2 or obj.lower() in _STOPWORDS or (first in _STOPWORDS and first not in ("the", "a", "an")):
                    continue
                if "holder" in pattern.groupindex:
                    subj = "User"
                    said = sentence[m.start("holder"):m.start("verb")]
                else:
                    subj = m.group("subj").strip()
                    if subj.lower() in _STOPWORDS:
                        continue
                    starts = [c.end() for c in _CLAUSE_START.finditer(sentence, 0, m.start("subj"))]
                    said = sentence[(starts[-1] if starts else 0):m.start("verb")]
                negated = bool(_NEGATION.search(said)) or bool(m.groupdict().get("not_to"))
                if kind == "taste":
                    # "I'd like to book a table", "I would prefer not to talk about it": a wish for
                    # now, not a taste
                    if (m.group("aux") or "").strip().lower() in ("'d", "’d", "would") and (
                            m.group("not_to") or obj.lower().startswith("to ")):
                        continue
                    disliking = m.group("verb").lower() in _DISLIKING
                    if negated and disliking:
                        continue
                    predicate = "dislikes" if negated or disliking else "prefers"
                elif negated:
                    continue
                else:
                    predicate = kind
                if predicate == "is_a" and _junk_name_relation({"predicate": "is_a", "object": obj}):
                    continue
                out.append((subj, predicate, obj))
    return out


# Identity predicates and possessions are read only from what the user wrote, never from assistant
# turns or internal memory synthesis, so a hallucinated reply cannot poison the graph.
_TRUSTED_SOURCES = frozenset({"user", "user_explicit", "identity_extract"})
_IDENTITY_PREDICATES = frozenset({"has_name", "nickname"})
# A statement that undoes another about the same thing: a liking and a dislike of one object cannot
# both stand, so the newer retires the older ("I don't like X any more" after "I love X").
_CONTRARY = {"prefers": "dislikes", "dislikes": "prefers"}
_IDENTITY_PATTERNS = (
    # "User's name is X" / "my name is X"
    (r"(?:user(?:'s)?\s+name\s+is|my\s+name\s+is)\s+([A-Za-z][a-zA-Z ]{1,24})", "has_name"),
    # "nickname: X" / "also known as X" / "call me X"
    (r"(?:nickname[:\s]+|also\s+known\s+as\s+|call\s+me\s+)([A-Za-z][a-zA-Z ]{1,20})", "nickname"),
)
_POSSESSIVE = frozenset(f"has_{_REL_NORMALISE.get(w, w)}" for w in _REL_WORDS)


def possessions(text: str) -> List[Tuple[str, str]]:
    """(has_<rel>, Name) from possessive phrases: "my dog Biscuit", "my dog is/named/called
    Biscuit", "my dog's name is Biscuit", "Biscuit (my dog)", "Biscuit, my dog", "Biscuit is my
    dog". Names must be capitalised; noise is filtered via the stopword set. This is the gap that
    dropped "Biscuit (my dog)" on the floor."""
    out: List[Tuple[str, str]] = []
    rel_alt = "|".join(sorted(set(_REL_WORDS), key=len, reverse=True))
    name_grp = r"[A-Z][A-Za-z'’\-]{1,20}"
    patterns = [
        # name AFTER: my <rel> [is/named/called/'s name is] <Name> ("My" opening a sentence too)
        rf"\b[Mm]y\s+(?P<rel>{rel_alt})"
        rf"(?:’s\s+name\s+is|'s\s+name\s+is|\s+(?:is\s+)?(?:called\s+|named\s+)?)"
        rf"\s*(?P<name>{name_grp})",
        # name BEFORE: <Name> (my <rel>) | <Name>, my <rel> | <Name> is my <rel>
        rf"(?P<name>{name_grp})\s*(?:\(\s*|,\s*|\s+is\s+)my\s+(?P<rel>{rel_alt})\b",
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            rel = (m.group("rel") or "").lower()
            nm = (m.group("name") or "").strip()
            if nm and nm.lower() not in _STOPWORDS:
                out.append((f"has_{_REL_NORMALISE.get(rel, rel)}", nm))
    return out


def read_triples(text: str, source: str = "memory") -> List[Tuple[str, str, str]]:
    """Every (subject, predicate, object) the graph takes from a memory string, without writing
    anything: the user's declared name and nickname and their possessions (trusted sources only),
    and the plain statements statements() reads. extract_from_memory writes these, and forgetting a
    memory takes back exactly these."""
    text = str(text or "").strip()
    if not text or len(text) < 10:
        return []
    out: List[Tuple[str, str, str]] = []
    if source in _TRUSTED_SOURCES:
        # Names come from the shared extractor, which refuses "my name is listed as unknown" and
        # "my name is not fucking listed" (both were stored as the user's name).
        declared: Dict[str, str] = {}
        try:
            from eli.runtime.identity_validation import extract_explicit_identity_facts
            facts = extract_explicit_identity_facts(text)
            if facts.get("name"):
                declared["has_name"] = facts["name"]
            if facts.get("nickname") or facts.get("preferred_name"):
                declared["nickname"] = facts.get("nickname") or facts["preferred_name"]
        except Exception:
            log.debug("identity extraction unavailable", exc_info=True)
        for pattern, predicate in _IDENTITY_PATTERNS:
            value = declared.get(predicate, "").strip()
            if (value and len(value) > 1 and value.lower() not in _STOPWORDS
                    and re.search(pattern, text, re.I)):
                out.append(("User", predicate, value))
    out.extend(statements(text))
    if source in _TRUSTED_SOURCES:
        out.extend(("User", predicate, name) for predicate, name in possessions(text))
    return out


class KnowledgeGraph:
    """SQLite-backed entity-relation knowledge graph."""

    relation_line = staticmethod(relation_line)     # for callers holding the graph (agent_bus)

    def __init__(self, db_path: Optional[Path] = None):
        self._path = Path(db_path) if db_path else _db_path()
        self._lock = threading.Lock()
        self._init_schema()

    @property
    def db_path(self) -> Path:
        """Canonical SQLite file backing the active knowledge graph."""
        return self._path

    @property
    def path(self) -> Path:
        """Compatibility alias for callers that inspect store locations."""
        return self._path

    def _init_schema(self) -> None:
        # executescript() handles multi-statement DDL including triggers that
        # contain internal semicolons (which a naive split(";") would mangle).
        conn = _connect(self._path)
        try:
            conn.executescript(_DDL)
            # Relations are a user's (eli/memory/policy.owner_key): '' the owner, a member's id
            # otherwise. The same triple from two users is two relations, so the uniqueness carries
            # the user too; every relation from before is the owner's.
            if "user_id" not in {r[1] for r in conn.execute("PRAGMA table_info(kg_relations)")}:
                conn.execute("ALTER TABLE kg_relations ADD COLUMN user_id TEXT DEFAULT ''")
            conn.execute("DROP INDEX IF EXISTS kg_rel_unique")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS kg_rel_unique_user "
                         "ON kg_relations(subject_id, LOWER(predicate), object_id, user_id)")
            conn.commit()
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        return _connect(self._path)

    # ── Entity CRUD ──────────────────────────────────────────────────────────

    def upsert_entity(
        self,
        name: str,
        entity_type: str = "concept",
        aliases: Optional[List[str]] = None,
        description: str = "",
        confidence: float = 1.0,
    ) -> int:
        """Insert or update an entity. Returns its id."""
        name = name.strip()
        if not name:
            return -1
        aliases_str = ", ".join(a.strip() for a in (aliases or []) if a.strip())
        now = time.time()
        with self._lock:
            conn = self._conn()
            try:
                row = conn.execute(
                    "SELECT id, aliases, description FROM kg_entities WHERE LOWER(name)=LOWER(?)",
                    (name,)
                ).fetchone()
                if row:
                    eid = row["id"]
                    # Merge aliases
                    existing_aliases = set(a.strip() for a in (row["aliases"] or "").split(",") if a.strip())
                    new_aliases = set(a.strip() for a in aliases_str.split(",") if a.strip())
                    merged = ", ".join(sorted(existing_aliases | new_aliases))
                    desc = description or row["description"] or ""
                    conn.execute(
                        "UPDATE kg_entities SET aliases=?, description=?, confidence=?, ts=? WHERE id=?",
                        (merged, desc, confidence, now, eid)
                    )
                else:
                    cur = conn.execute(
                        "INSERT INTO kg_entities(name, type, aliases, description, confidence, ts) "
                        "VALUES (?,?,?,?,?,?)",
                        (name, entity_type, aliases_str, description, confidence, now)
                    )
                    eid = cur.lastrowid
                conn.commit()
                return eid
            finally:
                conn.close()

    def get_entity_id(self, name: str) -> Optional[int]:
        """The entity of this name, or of this alias exactly. An alias was matched as a substring of the
        alias list, so a short name resolved to whichever entity's aliases held it, and "" to the first."""
        name = str(name or "").strip()
        if not name:
            return None
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT id FROM kg_entities WHERE LOWER(name)=LOWER(?)", (name,)
            ).fetchone()
            if row:
                return row["id"]
            # an alias, as one whole entry of the comma-separated list
            row = conn.execute(
                "SELECT id FROM kg_entities WHERE ',' || REPLACE(LOWER(COALESCE(aliases, '')), ', ', ',') || ',' "
                "LIKE '%,' || ? || ',%'", (name.lower(),)
            ).fetchone()
            return row["id"] if row else None
        finally:
            conn.close()

    # ── Relation CRUD ─────────────────────────────────────────────────────────

    def add_relation(
        self,
        subject: str,
        predicate: str,
        obj: str,
        weight: float = 1.0,
        source: str = "inferred",
        auto_create: bool = True,
        user_id: Optional[str] = None,
    ) -> bool:
        """Add (subject)-[predicate]->(object) triple for one user (user_id None: the turn's,
        eli/memory/policy.owner_key). Returns True if new."""
        predicate = predicate.strip().lower().replace(" ", "_")
        now = time.time()
        who = _who(user_id)
        sid = self.get_entity_id(subject)
        oid = self.get_entity_id(obj)
        if sid is None and auto_create:
            sid = self.upsert_entity(subject)
        if oid is None and auto_create:
            oid = self.upsert_entity(obj)
        if sid is None or oid is None:
            return False
        if sid == oid:
            return False  # skip self-loops (e.g. alias resolving to same entity)
        with self._lock:
            conn = self._conn()
            try:
                existing = conn.execute(
                    "SELECT id FROM kg_relations WHERE subject_id=? AND LOWER(predicate)=? AND object_id=? "
                    "AND COALESCE(user_id, '')=?",
                    (sid, predicate, oid, who)
                ).fetchone()
                if existing:
                    # ts stays when it was first learned. The profile sync re-adds every relation, and
                    # stamping each with "now" made all of the user's shows look equally recent.
                    conn.execute(
                        "UPDATE kg_relations SET weight=MAX(weight,?) WHERE id=?",
                        (weight, existing["id"])
                    )
                    conn.commit()
                    return False
                conn.execute(
                    "INSERT INTO kg_relations(subject_id, predicate, object_id, weight, source, ts, user_id) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (sid, predicate, oid, weight, source, now, who)
                )
                conn.commit()
                return True
            finally:
                conn.close()

    # ── Query ─────────────────────────────────────────────────────────────────

    def query_entity(self, name: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Return entity dict with its outbound and inbound relations: the user's own (user_id None:
        the turn's user), never another's."""
        who = _who(user_id)
        eid = self.get_entity_id(name)
        if eid is None:
            return None
        conn = self._conn()
        try:
            ent = conn.execute("SELECT * FROM kg_entities WHERE id=?", (eid,)).fetchone()
            if not ent:
                return None
            result = dict(ent)

            out_rows = conn.execute(
                """SELECT r.predicate, e.name AS object, r.weight, r.ts
                   FROM kg_relations r JOIN kg_entities e ON r.object_id=e.id
                   WHERE r.subject_id=? AND COALESCE(r.user_id, '')=? ORDER BY r.weight DESC""",
                (eid, who)
            ).fetchall()
            in_rows = conn.execute(
                """SELECT e.name AS subject, r.predicate, r.weight, r.ts
                   FROM kg_relations r JOIN kg_entities e ON r.subject_id=e.id
                   WHERE r.object_id=? AND COALESCE(r.user_id, '')=? ORDER BY r.weight DESC""",
                (eid, who)
            ).fetchall()

            result["outbound"] = [dict(r) for r in out_rows]
            result["inbound"] = [dict(r) for r in in_rows]
            return result
        finally:
            conn.close()

    def related(self, name: str, hops: int = 2, user_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Multi-hop BFS from entity `name`: the live relations (live_relation) within `hops` steps,
        strongest and newest first, each with the ts it was learned (relation_line dates it).
        """
        start_id = self.get_entity_id(name)
        if start_id is None:
            return []
        who = _who(user_id)
        conn = self._conn()
        try:
            visited: set = {start_id}
            frontier = [start_id]
            results: List[Dict[str, Any]] = []

            for _ in range(hops):
                if not frontier:
                    break
                placeholders = ",".join("?" * len(frontier))
                rows = conn.execute(
                    f"""SELECT r.predicate, e_s.name AS subject, e_o.name AS object,
                               r.subject_id, r.object_id, r.weight, r.ts
                        FROM kg_relations r
                        JOIN kg_entities e_s ON r.subject_id=e_s.id
                        JOIN kg_entities e_o ON r.object_id=e_o.id
                        WHERE (r.subject_id IN ({placeholders})
                           OR r.object_id  IN ({placeholders}))
                          AND COALESCE(r.user_id, '') = ?
                        ORDER BY r.weight DESC, r.ts DESC""",
                    frontier + frontier + [who]
                ).fetchall()
                new_frontier = []
                for row in rows:
                    # a retired name or a mood stored as a kind must not travel: neither shown nor
                    # walked through to what lies beyond it
                    if not live_relation(dict(row)):
                        continue
                    results.append(dict(row))
                    for nid in (row["subject_id"], row["object_id"]):
                        if nid not in visited:
                            visited.add(nid)
                            new_frontier.append(nid)
                frontier = new_frontier
            return results
        finally:
            conn.close()

    def search_entities(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        """FTS5 search over entity names, aliases, and descriptions."""
        q = query.strip()
        if not q:
            return []
        conn = self._conn()
        try:
            # FTS5 attempt
            try:
                fts_q = " OR ".join(
                    f'"{t}"' for t in re.split(r"[^a-zA-Z0-9_]+", q) if len(t) > 1
                )
                if fts_q:
                    # by how well they match (BM25), then the newest: confidence was 1.0 for every entity
                    rows = conn.execute(
                        """SELECT e.* FROM kg_entities e
                           JOIN kg_entities_fts f ON e.id=f.rowid
                           WHERE kg_entities_fts MATCH ?
                           ORDER BY bm25(kg_entities_fts), e.ts DESC LIMIT ?""",
                        (fts_q, limit)
                    ).fetchall()
                    return [dict(r) for r in rows]
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            # LIKE fallback
            like = f"%{q.lower()}%"
            rows = conn.execute(
                """SELECT * FROM kg_entities
                   WHERE LOWER(name) LIKE ? OR LOWER(aliases) LIKE ? OR LOWER(description) LIKE ?
                   ORDER BY (LOWER(name) LIKE ?) DESC, ts DESC LIMIT ?""",
                (like, like, like, like, limit)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # Patterns that imply the user is asking about their own identity
    _IDENTITY_RE = re.compile(
        r"\b(who am i|my name|know me|who i am|do you know|what('?s| is) my|"
        r"remember me|you know me|call me|am i|i am)\b",
        re.I,
    )

    def context_for_prompt(self, query: str, max_chars: int = 800, *,
                           max_entities: Optional[int] = None,
                           max_relations: Optional[int] = None,
                           user_id: Optional[str] = None) -> str:
        """
        Return a compact KG context block suitable for LLM injection.
        Searches entities matching `query`, then expands one hop.
        Tries the full query first, then individual tokens (skipping stopwords).
        For identity queries, seeds with user identity context if explicitly stored.

        Entity and relation counts default to cog.kg_entities / cog.kg_relations
        (tier-scaled). They were a fixed 4-6 entities and 5 relations, so a large
        max_chars could never be filled.
        """
        if max_entities is None or max_relations is None:
            try:
                from eli.core.cognition_tunables import snapshot as _cog_snapshot
                _tn = _cog_snapshot()
            except Exception:
                _tn = {}
            if max_entities is None:
                max_entities = int(_tn.get("cog.kg_entities", 8))
            if max_relations is None:
                max_relations = int(_tn.get("cog.kg_relations", 8))
        max_entities = max(1, int(max_entities))
        max_relations = max(1, int(max_relations))
        hits = self.search_entities(query, limit=max_entities)
        if not hits:
            # Seed with identity entities for first-person queries
            if self._IDENTITY_RE.search(query):
                for seed in ("User",):
                    for h in self.search_entities(seed, limit=2):
                        if h not in hits:
                            hits.append(h)

        if not hits:
            # Try individual tokens (proper nouns / meaningful words only)
            tokens = [
                t for t in re.split(r"[^a-zA-Z0-9_]+", query)
                if len(t) > 1 and t.lower() not in _STOPWORDS
            ]
            seen_ids: set = set()
            for tok in tokens:
                for h in self.search_entities(tok, limit=3):
                    if h["id"] not in seen_ids:
                        seen_ids.add(h["id"])
                        hits.append(h)
                if len(hits) >= max_entities:
                    break
        if not hits:
            return ""

        # Each relation says when the graph learned it. Undated, "User —[is]→ DRIVING FROM X TO Y" read
        # as what the user is doing now, weeks after the trip.
        from eli.cognition.evidence_format import as_of
        lines: List[str] = []
        seen_triples: set = set()

        for ent in hits:
            name = ent["name"]
            ent_detail = self.query_entity(name, user_id=user_id)
            if not ent_detail:
                continue
            desc = ent_detail.get("description", "").strip()
            aliases = ent_detail.get("aliases", "").strip()
            header = f"[{as_of(name, ent_detail.get('ts'))}]"
            if ent_detail.get("type") and ent_detail["type"] != "concept":
                header += f" ({ent_detail['type']})"
            if desc:
                header += f": {desc}"
            if aliases:
                header += f" — also known as: {aliases}"
            # Query-aware ordering: float relations whose predicate/object match a
            # query token to the top, so "what is my dog's name" surfaces
            # has_dog→Biscuit instead of it being crowded out by the [:5] cap.
            _q_toks = {
                t.lower() for t in re.split(r"[^a-zA-Z0-9]+", query)
                if len(t) > 2 and t.lower() not in _STOPWORDS
            }
            _q_toks |= {t[:-1] for t in list(_q_toks) if t.endswith("s") and len(t) > 3}

            def _rel_relevance(_rel: Dict[str, Any]) -> int:
                _blob = f"{_rel.get('predicate','')} {_rel.get('object','')}".lower()
                return sum(1 for _t in _q_toks if _t in _blob)

            # Skip retired/contradicted relations (weight demoted to ~0.05 by the
            # has_name conflict handler) so a corrected name never leaks back in.
            _outbound = sorted(
                (r for r in (ent_detail.get("outbound") or []) if live_relation(r)),
                key=_rel_relevance, reverse=True,
            )
            # Inbound too: "User is_a saying your memory is fine now" reached a recall reply from the
            # value's side, where nothing filtered it.
            _inbound = [r for r in (ent_detail.get("inbound") or [])
                        if live_relation({**r, "object": name})]
            if not (desc or aliases or _outbound or _inbound):
                continue                     # nothing of this user's to show: junk, or another user's
            lines.append(header)
            for rel in _outbound[:max_relations]:
                triple = (name, rel["predicate"], rel["object"])
                if triple not in seen_triples:
                    seen_triples.add(triple)
                    lines.append("  " + relation_line(name, rel["predicate"], rel["object"], rel.get("ts")))
            for rel in _inbound[:max(3, max_relations // 2)]:
                triple = (rel["subject"], rel["predicate"], name)
                if triple not in seen_triples:
                    seen_triples.add(triple)
                    lines.append("  " + relation_line(rel["subject"], rel["predicate"], name, rel.get("ts")))

        text = "\n".join(lines)
        if len(text) > max_chars:
            text = text[:max_chars].rsplit("\n", 1)[0]
        return text

    def stats(self) -> Dict[str, int]:
        conn = self._conn()
        try:
            e = conn.execute("SELECT COUNT(*) FROM kg_entities").fetchone()[0]
            r = conn.execute("SELECT COUNT(*) FROM kg_relations").fetchone()[0]
            return {"entities": e, "relations": r}
        finally:
            conn.close()

    # ── Automatic extraction from memory text ────────────────────────────────

    def extract_from_memory(self, text: str, source: str = "memory", user_id: Optional[str] = None) -> int:
        """Write the triples read_triples() finds in a memory string. A new name or nickname for the
        user retires the one held before (its weight drops to RETIRED_WEIGHT). Returns how many
        triples were new."""
        added = 0
        who = _who(user_id)
        for subj, predicate, obj in read_triples(text, source):
            if predicate in _IDENTITY_PREDICATES:
                self._retire_other_identity(predicate, obj, who)
            if predicate in _CONTRARY:
                self._retire_relation(subj, _CONTRARY[predicate], obj, who)
            self.upsert_entity(subj, "person" if subj == "User" else "concept")
            self.upsert_entity(obj, "concept" if predicate in _POSSESSIVE or subj != "User" else "value")
            if self.add_relation(subj, predicate, obj, source=source, user_id=who):
                added += 1
        return added

    def holds(self, predicate: str, obj: str, user_id: Optional[str] = None, subject: str = "User") -> bool:
        """The graph stands by subject —[predicate]→ obj for this user (a live relation, live_relation)."""
        sid, oid = self.get_entity_id(subject), self.get_entity_id(obj)
        if sid is None or oid is None:
            return False
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT r.predicate, r.weight, e.name AS object FROM kg_relations r JOIN kg_entities e ON e.id = r.object_id "
                "WHERE r.subject_id=? AND r.object_id=? AND LOWER(r.predicate)=? AND COALESCE(r.user_id, '')=?",
                (sid, oid, str(predicate or "").strip().lower(), _who(user_id))).fetchall()
        finally:
            conn.close()
        return any(live_relation(dict(r)) for r in rows)

    def _retire_relation(self, subject: str, predicate: str, obj: str, who: str = "") -> int:
        """A newer statement undid this one: its weight drops to RETIRED_WEIGHT (kept, never shown), as
        a corrected name's does. How many were retired."""
        sid, oid = self.get_entity_id(subject), self.get_entity_id(obj)
        if sid is None or oid is None:
            return 0
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(
                    "UPDATE kg_relations SET weight = ? WHERE subject_id=? AND object_id=? AND LOWER(predicate)=? "
                    "AND COALESCE(user_id, '')=? AND weight > ?", (RETIRED_WEIGHT, sid, oid, predicate, who, LIVE_WEIGHT))
                conn.commit()
                return int(cur.rowcount or 0)
            finally:
                conn.close()

    def _retire_other_identity(self, predicate: str, value: str, who: str = "") -> None:
        """Contradiction detection for identity predicates: a different has_name/nickname the user
        already has is retired, not left beside the new one."""
        try:
            uid = self.get_entity_id("User")
            if uid is None:
                return
            conn = self._conn()
            try:
                for rid, old in conn.execute(
                        "SELECT r.id, e.name FROM kg_relations r JOIN kg_entities e ON e.id = r.object_id "
                        "WHERE r.subject_id=? AND LOWER(r.predicate)=? AND r.weight > ? AND COALESCE(r.user_id, '')=?",
                        (uid, predicate, LIVE_WEIGHT, who)).fetchall():
                    if old.lower() != value.lower():
                        conn.execute("UPDATE kg_relations SET weight = ? WHERE id = ?", (RETIRED_WEIGHT, rid))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            log.debug("suppressed exception", exc_info=True)


# ── Singleton ────────────────────────────────────────────────────────────────

def get_knowledge_graph() -> KnowledgeGraph:
    global _INSTANCE
    if _INSTANCE is None:
        with _LOCK:
            if _INSTANCE is None:
                try:
                    _INSTANCE = KnowledgeGraph()
                except Exception as e:
                    log.debug(f"[KG] init failed: {e}")
                    raise
    return _INSTANCE


def reset_knowledge_graph() -> None:
    global _INSTANCE
    with _LOCK:
        _INSTANCE = None


def feeds_graph(origin: str) -> bool:
    """Only what the user said becomes graph facts about them. The extractor reads text as the
    user's own words, so a physics book filed from a folder became "User prefers screening"."""
    from eli.memory import policy
    return policy.counts_as_evidence_about_user(origin)


def rebuild_from_memories(conn) -> int:
    """Extract the graph again from the stored memories. Returns how many were read."""
    from eli.memory import policy
    kg = get_knowledge_graph()
    n = 0
    whose = "COALESCE(user_id, '')" if "user_id" in {r[1] for r in conn.execute("PRAGMA table_info(memories)")} else "''"
    for text, source, kind, tags, who in conn.execute(
            "SELECT COALESCE(text, content, ''), COALESCE(source, 'user'), COALESCE(kind, 'memory'), "
            f"COALESCE(tags, ''), {whose} FROM memories ORDER BY id").fetchall():
        t = (text or "").strip()
        if t and feeds_graph(policy.classify_origin(source, kind, tags, t)):
            kg.extract_from_memory(t, source=source, user_id=who)
            n += 1
    return n

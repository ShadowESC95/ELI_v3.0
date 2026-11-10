"""Bitemporal claims: what ELI believes about the user, when it was true, and when ELI learned it.

Each claim has a valid interval (valid_from .. valid_to, open while it still holds) and a record
interval (recorded_at .. superseded_at, open while ELI still believes it). A single-valued fact such
as where someone lives supersedes the old value instead of sitting beside it, and the old claim is
kept, so "what did I do for work last spring" and "what did you think on 1 March" both have answers.

Where a claim came from is one vocabulary (ORIGINS, as eli/cognition/belief.PROVENANCE_WEIGHT weighs
it): the user's own word (user_said, user_explicit, user_passing), a document, something observed, ELI's
inference. Each confirmation is kept with its own source, so a claim is as sure as what bore it out
(each source by its weight, the user's word once per day it was said), and a restatement on another day
is another confirmation.

A disputed claim is never settled silently: when something that is not the user's word contradicts the
user's, both stand marked disputed, ELI raises it (a "conflict" thought, and a turn fact the reply reads,
eli/cognition/belief.voice), and the user's next word on it settles it: their statement of either value,
or their answer to the question ELI asked (answer_conflicts).
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from eli.utils.log import get_logger

log = get_logger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL DEFAULT 'user',
    relation TEXT NOT NULL,
    value TEXT NOT NULL,
    single_valued INTEGER NOT NULL DEFAULT 1,
    valid_from REAL,
    valid_to REAL,
    recorded_at REAL NOT NULL,
    superseded_at REAL,
    status TEXT NOT NULL DEFAULT 'current',
    supersedes INTEGER,
    source_memory_id INTEGER,
    source_text TEXT,
    origin TEXT,
    confidence REAL DEFAULT 0.7,
    confirmations INTEGER DEFAULT 1,
    last_confirmed REAL,
    source_roots TEXT,
    contested INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_claims_rel ON memory_claims(subject, relation, status)
"""

_KEY = re.compile(r"\s+")

# Who a claim is about. SUBJECT_USER is the owner of this install; ELI's own positions (its stances,
# eli/cognition/stance_store) are SUBJECT_ELI and never stand among the user's facts. They used to be
# recorded under the default subject and came back as things known about the user.
SUBJECT_USER = "user"
SUBJECT_ELI = "eli"


def user_subject(owner_key: str = "") -> str:
    """The subject of one user's claims: SUBJECT_USER for the owner (owner_key ''), "user:<id>" for
    an API member, so one member's "I work nights" never supersedes or confirms another's."""
    key = str(owner_key or "").strip()
    return f"{SUBJECT_USER}:{key}" if key else SUBJECT_USER

# What the user is doing at the moment. These change often, so a newer value replaces the old one
# whoever reported it, instead of the two standing side by side as a dispute.
CHANGES_OFTEN = frozenset({"watching", "playing", "reading", "listening_to"})

# Bumped when extraction changes, so the history is read again (Memory.backfill_claims).
EXTRACT_VERSION = "2"

# Where a claim came from, as belief revision names provenance (eli/cognition/belief.PROVENANCE_WEIGHT),
# so a source counts toward a claim exactly as it counts toward a belief: the user's own word 1 (said
# outright) or 0.65 (in passing), a document 0.6, something observed 0.5, ELI's own inference 0.35, a
# source nobody recorded 0.3. Profile facts and ELI's stances are recorded under the belief names.
ORIGINS = {"user_said": "user_explicit", "user_explicit": "user_explicit", "user_passing": "user_passing",
           "document": "document", "observed": "observed", "eli_said": "inferred", "inferred": "inferred"}
# The origins that are the user's own word: a newer one replaces an older one (the user's life
# changed), and one of them is what a claim of anything else is disputed against.
USERS_WORD = frozenset({"user_said", "user_explicit", "user_passing"})


def provenance_of(origin: str) -> str:
    """The belief provenance an origin counts as ("unknown" for one nobody recorded)."""
    return ORIGINS.get(str(origin or ""), "unknown")


def is_users_word(origin: str) -> bool:
    return str(origin or "") in USERS_WORD


def _weight(origin: str) -> float:
    from eli.cognition.belief import PROVENANCE_WEIGHT
    return PROVENANCE_WEIGHT[provenance_of(origin)]


def sureness(origin: str, confirmations: int, contradicted_by: str = "") -> float:
    """How sure ELI is of a claim borne out `confirmations` times by one kind of source (confirmed()
    for several): a Beta posterior over its confirmations, each weighted by its source, and the
    contradiction standing against it, if any (eli/memory/policy.claim_confidence). One user statement
    0.67, two 0.75, five 0.86; the user's word contradicted by an inference 0.6, the inference 0.4."""
    return confirmed([origin] * max(1, int(confirmations)), contradicted_by)


def confirmed(origins: List[str], contradicted_by: str = "") -> float:
    """How sure ELI is of a claim confirmed once by each of these sources: one user statement and one
    observation is 1 + 0.5 of evidence (0.71), not two user statements (0.75)."""
    from eli.memory.policy import claim_confidence
    against = [_weight(contradicted_by)] if contradicted_by else []
    return round(claim_confidence([_weight(o) for o in (origins or ["unknown"])], against), 4)


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(float(ts)))


def _confirmations(roots_json: Any, origin: str) -> List[Dict[str, Any]]:
    """A claim's confirmations, each {"root", "origin", "day"}. Rows written before each confirmation
    kept its own source list bare roots; those count as the claim's own origin."""
    try:
        raw = json.loads(roots_json or "[]")
    except (TypeError, ValueError):
        raw = []
    out = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            out.append({"root": item.get("root"), "origin": str(item.get("origin") or origin or ""),
                        "day": str(item.get("day") or "")})
        else:
            out.append({"root": item, "origin": str(origin or ""), "day": ""})
    return out


def _is_new(confs: List[Dict[str, Any]], root: Any, origin: str, day: str) -> bool:
    """Whether a statement is another confirmation: never the same source again (a summary of a
    statement shares its root, and is no corroboration), and the user's own word at most once a day
    (one statement is read by the turn and again by the memory it was kept as)."""
    if root is not None and any(c["root"] == root for c in confs):
        return False
    if is_users_word(origin) and any(is_users_word(c["origin"]) and c["day"] == day for c in confs):
        return False
    return True


# The databases whose claims table this process has made sure of (PRAGMA database_list's file).
_ensured: set = set()


def ensure_schema(conn) -> None:
    """Make sure of the claims table and its columns, once per database. Each statement runs on its
    own: executescript commits whatever the caller had open first (Memory.forget's deletions were
    committed before its claims were withdrawn), so it is never used inside a caller's transaction."""
    try:
        path = str((conn.execute("PRAGMA database_list").fetchone() or ("", "", ""))[2] or "")
        # the file as it is now: a store wiped and made again is a new one
        where = f"{path}:{os.stat(path).st_ino}" if path else ""
    except Exception:  # noqa: BLE001 - a connection that cannot say its file is made sure of each time
        where = ""
    if where and where in _ensured:
        return
    for statement in SCHEMA.split(";"):
        if statement.strip():
            conn.execute(statement)
    have = {r[1] for r in conn.execute("PRAGMA table_info(memory_claims)")}
    for name, decl in (("source_roots", "TEXT"), ("contested", "INTEGER DEFAULT 0")):
        if name not in have:
            conn.execute(f"ALTER TABLE memory_claims ADD COLUMN {name} {decl}")
    if where:
        _ensured.add(where)


def _norm(value: str) -> str:
    return _KEY.sub(" ", str(value or "").strip().lower()).strip(" .,!?\"'")


def _same(a: str, b: str) -> bool:
    """The same value, or one is the other's initials: "GBBO" is "Great British Bake Off"."""
    na, nb = _norm(a), _norm(b)
    if na == nb or sorted(re.findall(r"[a-z0-9]+", na)) == sorted(re.findall(r"[a-z0-9]+", nb)):
        return True
    for short, long_ in ((na, nb), (nb, na)):
        words = re.findall(r"[a-z0-9]+", long_)
        if " " not in short and 2 <= len(short) <= 6 and len(words) >= 2 and short == "".join(w[0] for w in words):
            return True
    return False


def _confirm(conn, cid: int, roots_json: Any, c_origin: str, origin: str, root: Any, vfrom: float, now: float,
             contradicted_by: str = "") -> None:
    """One more statement of a standing claim's value: counted when it is another confirmation
    (_is_new), and the claim as sure as all its sources together (confirmed)."""
    confs = _confirmations(roots_json, c_origin)
    if not confs:       # a claim from before its first statement was kept: that statement counts
        confs = [{"root": None, "origin": str(c_origin or ""), "day": ""}]
    day = _day(vfrom)
    if not _is_new(confs, root, origin, day):
        return
    confs.append({"root": root, "origin": str(origin or ""), "day": day})
    held = c_origin if _weight(c_origin) >= _weight(origin) else origin
    conn.execute("UPDATE memory_claims SET confirmations = ?, source_roots = ?, last_confirmed = ?, confidence = ?, "
                 "origin = ? WHERE id = ?",
                 (len(confs), json.dumps(confs), now, confirmed([c["origin"] for c in confs], contradicted_by),
                  held, int(cid)))


def _first(origin: str, root: Any, vfrom: float) -> str:
    return json.dumps([{"root": root, "origin": str(origin or ""), "day": _day(vfrom)}])


def record(conn, relation: str, value: str, *, subject: str = "user", single_valued: bool = True,
           valid_from: Optional[float] = None, source_memory_id: Optional[int] = None,
           source_text: str = "", origin: str = "user_said",
           now: Optional[float] = None, historical: bool = False, root_id: Optional[Any] = None,
           verdict: str = "", changed: Optional[bool] = None) -> Optional[int]:
    """Add a claim. Returns its id, or None when there was nothing to add.

    The same value again confirms the standing claim when it is another confirmation (another source,
    or the user's word on another day). A different value on a single-valued relation closes the
    standing claim at valid_from and replaces it, unless the newcomer is not the user's own word and
    the standing claim is: then both stand, marked as disputed, and ELI raises it. The user's word on a
    relation in dispute settles the dispute (resolve_conflict): the value they state stands.
    ``historical`` records something that was true and no longer is ("I used to work nights"): it held
    up to when it was said, from a time not known, and a standing claim of that value is closed then.
    ``verdict`` is a decision already weighed (eli/cognition/belief, by the profile's writer, which
    voices it): "concede" replaces the standing value, "question" disputes it, "hold" adds nothing.
    ``changed`` is whether the statement says its value changed ("now", "we moved"): read from a
    sentence (record_from_text), a contradiction of the user's own word that does not is weighed
    (belief.assess_claim) and, unless it clearly outweighs what they said before, raised or held, and
    voiced either way. None (a caller with no sentence) takes the user's newer word as the change.
    """
    ensure_schema(conn)
    now = float(now if now is not None else time.time())
    vfrom = float(valid_from if valid_from is not None else now)
    value = str(value or "").strip()
    if not relation or not value:
        return None
    if verdict == "hold":
        return None
    root = root_id if root_id is not None else source_memory_id
    if historical:
        return _record_former(conn, relation, value, subject=subject, said_at=vfrom, now=now,
                              source_memory_id=source_memory_id, source_text=source_text, origin=origin, root=root)
    if is_users_word(origin) and verdict != "question":
        settled = _settle_dispute_by_word(conn, relation, value, subject=subject, now=now, root=root,
                                          origin=origin, said_at=vfrom)
        if settled is not None:
            return settled
    cur = conn.execute(
        "SELECT id, value, origin, source_roots, valid_from FROM memory_claims "
        "WHERE subject = ? AND relation = ? AND status = 'current'",
        (subject, relation)).fetchall()
    for cid, cval, c_origin, roots_json, _vf in cur:
        if _same(cval, value):
            _confirm(conn, cid, roots_json, c_origin, origin, root, vfrom, now)
            return int(cid)
    weighed = ""
    if (not verdict and changed is False and single_valued and cur and relation not in CHANGES_OFTEN
            and is_users_word(origin) and any(is_users_word(o) for _c, _v, o, _r, _f in cur)
            and vfrom >= max(float(r[4] or 0.0) for r in cur)):
        weighed = _weigh(conn, cur, value, origin, vfrom, now, subject=subject, relation=relation)
        if weighed == "hold":
            return None
        verdict = weighed
    # Said before what ELI already holds (history read back in order, or a late report): it held until
    # the newer value took over, and it does not replace it.
    standing_from = max((float(r[4] or 0.0) for r in cur), default=0.0)
    if single_valued and cur and vfrom < standing_from:
        cursor = conn.execute(
            "INSERT INTO memory_claims (subject, relation, value, single_valued, valid_from, valid_to, recorded_at, "
            "superseded_at, status, source_memory_id, source_text, origin, confidence, last_confirmed, source_roots) "
            "VALUES (?,?,?,1,?,?,?,?,'superseded',?,?,?,?,?,?)",
            (subject, relation, value, vfrom, standing_from, now, now, source_memory_id, str(source_text or "")[:400],
             origin, sureness(origin, 1), now, _first(origin, root, vfrom)))
        return int(cursor.lastrowid)
    disputes = (verdict == "question" or (
        verdict != "concede" and not is_users_word(origin)
        and any(is_users_word(o) for _c, _v, o, _r, _f in cur)))
    if single_valued and cur and relation not in CHANGES_OFTEN and disputes:
        # the standing claim is now contradicted: its sureness carries that, as the newcomer's carries it
        for cid, _cval, c_origin, roots_json, _vf in cur:
            confs = _confirmations(roots_json, c_origin) or [{"origin": c_origin}]
            conn.execute("UPDATE memory_claims SET contested = 1, confidence = ? WHERE id = ?",
                         (confirmed([c["origin"] for c in confs], contradicted_by=origin), cid))
        standing_origin = next((o for _c, _v, o, _r, _f in cur if is_users_word(o)), cur[-1][2])
        cursor = conn.execute(
            "INSERT INTO memory_claims (subject, relation, value, single_valued, valid_from, recorded_at, status, "
            "source_memory_id, source_text, origin, confidence, last_confirmed, source_roots, contested) "
            "VALUES (?,?,?,?,?,?,'disputed',?,?,?,?,?,?,1)",
            (subject, relation, value, 1 if single_valued else 0, vfrom, now, source_memory_id,
             str(source_text or "")[:400], origin, sureness(origin, 1, contradicted_by=standing_origin), now,
             _first(origin, root, vfrom)))
        disputed_id = int(cursor.lastrowid)
        if not verdict:      # ELI found this one itself: it says so (a verdict weighed or passed in is voiced already)
            _raise_conflict(conn, subject, relation, [str(r[1]) for r in cur], value, origin, disputed_id)
        return disputed_id
    superseded = None
    if single_valued and cur:
        superseded = int(cur[-1][0])
        conn.execute("UPDATE memory_claims SET status = 'superseded', valid_to = ?, superseded_at = ? "
                     "WHERE subject = ? AND relation = ? AND status = 'current'", (vfrom, now, subject, relation))
    cursor = conn.execute(
        "INSERT INTO memory_claims (subject, relation, value, single_valued, valid_from, valid_to, recorded_at, "
        "status, supersedes, source_memory_id, source_text, origin, confidence, last_confirmed, source_roots) "
        "VALUES (?,?,?,?,?,NULL,?,'current',?,?,?,?,?,?,?)",
        (subject, relation, value, 1 if single_valued else 0, vfrom, now, superseded, source_memory_id,
         str(source_text or "")[:400], origin, sureness(origin, 1), now, _first(origin, root, vfrom)))
    return int(cursor.lastrowid)


def _weigh(conn, cur: List[tuple], value: str, origin: str, said_at: float, now: float, *, subject: str,
           relation: str) -> str:
    """A statement of the user's that contradicts what they said before, with nothing in it saying the
    value changed, weighed against the standing claim (eli/cognition/belief.assess_claim: its
    confirmations and their source, and how long since it was borne out): "concede" when the new word
    clearly outweighs it; otherwise "question" or "hold", voiced (belief.voice) so the reply asks which
    is right or says ELI is keeping what it has."""
    from eli.cognition.belief import CONCEDE, HOLD, Belief, assess_claim, voice
    cid, cval, c_origin, roots_json, _vf = next((r for r in cur if is_users_word(r[2])), cur[-1])
    row = conn.execute("SELECT COALESCE(last_confirmed, recorded_at, ?) FROM memory_claims WHERE id = ?",
                       (now, cid)).fetchone()
    standing = Belief(statement=str(cval), provenance=provenance_of(c_origin),
                      corroboration=max(1, len(_confirmations(roots_json, c_origin))),
                      last_seen=float(row[0] if row else now))
    verdict = assess_claim(standing, Belief(statement=value, provenance=provenance_of(origin), corroboration=1,
                                            last_seen=said_at), now)
    if verdict.action == CONCEDE:
        return "concede"
    voice(verdict.action, about=relation, standing=str(cval), challenger=value, reasons=verdict.reasons,
          subject=subject, ref=f"conflict:{subject}:{relation}", conn=conn)
    return "hold" if verdict.action == HOLD else "question"


def _record_former(conn, relation: str, value: str, *, subject: str, said_at: float, now: float,
                   source_memory_id: Optional[int], source_text: str, origin: str, root: Any) -> Optional[int]:
    """Something that held and no longer does ("I used to work nights"): it held, from a time the
    statement does not give, until it was said. A standing claim of that value ends there; the same
    former claim said again confirms it."""
    for cid, cval, c_origin, roots_json in conn.execute(
            "SELECT id, value, origin, source_roots FROM memory_claims WHERE subject = ? AND relation = ? "
            "AND status = 'former'", (subject, relation)).fetchall():
        if _same(cval, value):
            _confirm(conn, cid, roots_json, c_origin, origin, root, said_at, now)
            return int(cid)
    retire(conn, relation, subject=subject, value=value, at=said_at, now=now)
    cursor = conn.execute(
        "INSERT INTO memory_claims (subject, relation, value, single_valued, valid_from, valid_to, recorded_at, "
        "status, source_memory_id, source_text, origin, confidence, last_confirmed, source_roots) "
        "VALUES (?,?,?,1,NULL,?,?,'former',?,?,?,?,?,?)",
        (subject, relation, value, said_at, now, source_memory_id, str(source_text or "")[:400], origin,
         sureness(origin, 1), now, _first(origin, root, said_at)))
    return int(cursor.lastrowid)


def _settle_dispute_by_word(conn, relation: str, value: str, *, subject: str, now: float, root: Any = None,
                            origin: str = "user_said", said_at: Optional[float] = None) -> Optional[int]:
    """The user's own word on a relation in dispute settles it: the value they state stands (a value
    of either side; a third is a new statement, and supersedes both). Only another statement does: the
    statement the dispute was raised from, read again (by the turn and by the memory it was kept as),
    settles nothing. The id of the claim that stands (or of the side named again), or None when nothing
    was in dispute or they named neither side."""
    rows = conn.execute("SELECT id, value, source_roots, origin FROM memory_claims WHERE subject = ? AND relation = ? "
                        "AND status = 'disputed'", (subject, relation)).fetchall()
    if not rows:
        return None
    sides = rows + conn.execute("SELECT id, value, source_roots, origin FROM memory_claims WHERE subject = ? "
                                "AND relation = ? AND status = 'current'", (subject, relation)).fetchall()
    hit = next((r for r in sides if _same(r[1], value)), None)
    if hit is None:
        return None
    if not _is_new(_confirmations(hit[2], hit[3]), root, origin, _day(said_at if said_at is not None else now)):
        return int(hit[0])
    match = str(hit[1])
    resolve_conflict(conn, relation, match, subject=subject, now=now)
    row = conn.execute("SELECT id FROM memory_claims WHERE subject = ? AND relation = ? AND status = 'current' "
                       "ORDER BY id DESC LIMIT 1", (subject, relation)).fetchone()
    return int(row[0]) if row else None


def _raise_conflict(conn, subject: str, relation: str, standing: List[str], disputed: str, origin: str,
                    claim_id: int) -> None:
    """A dispute ELI found is raised, never kept silently (eli/cognition/belief.voice)."""
    try:
        from eli.cognition.belief import QUESTION, voice
        voice(QUESTION, about=relation, standing=", ".join(standing), challenger=disputed,
              reasons=[f"{origin.replace('_', ' ')} says otherwise than what you said"], subject=subject,
              ref=f"conflict:{subject}:{relation}", claim_id=claim_id, conn=conn)
    except (ImportError, OSError, ValueError, TypeError, AttributeError) as failure:
        log.debug("conflict not raised: %s", failure)


def retire(conn, relation: str, *, subject: str = "user", value: str = "", at: Optional[float] = None,
           now: Optional[float] = None) -> int:
    """The standing claim on this relation stopped being true ("I no longer work nights"). With
    `value`, only a standing claim of that value ends: "I no longer work at Acme" says nothing of
    another employer ELI holds."""
    ensure_schema(conn)
    now = float(now if now is not None else time.time())
    at = float(at if at is not None else now)
    if value:
        ids = [cid for cid, cval in conn.execute(
            "SELECT id, value FROM memory_claims WHERE subject = ? AND relation = ? AND status = 'current'",
            (subject, relation)).fetchall() if _same(cval, value)]
        if not ids:
            return 0
        cur = conn.execute(f"UPDATE memory_claims SET status = 'superseded', valid_to = ?, superseded_at = ? "
                           f"WHERE id IN ({','.join('?' * len(ids))})", (at, now, *ids))
        return int(cur.rowcount or 0)
    cur = conn.execute("UPDATE memory_claims SET status = 'superseded', valid_to = ?, superseded_at = ? "
                       "WHERE subject = ? AND relation = ? AND status = 'current'", (at, now, subject, relation))
    return int(cur.rowcount or 0)


def move_stance_claims(conn) -> int:
    """Stances recorded before they had their own subject: the claims eli_stances points at, whose
    relation and value are the stance's topic and position, become ELI's. Returns how many moved."""
    ensure_schema(conn)
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(eli_stances)")}
    except Exception:
        return 0
    if "claim_id" not in cols:
        return 0
    cur = conn.execute(
        "UPDATE memory_claims SET subject = ? WHERE subject = ? AND id IN ("
        "  SELECT s.claim_id FROM eli_stances s WHERE s.claim_id IS NOT NULL"
        "   AND lower(s.topic) = lower(memory_claims.relation) AND s.position = memory_claims.value)",
        (SUBJECT_ELI, SUBJECT_USER))
    return int(cur.rowcount or 0)


def retract_from_memory(conn, memory_id: int, *, erase: bool = False) -> int:
    """A source memory was deleted: claims resting only on it are withdrawn, not left as orphans.
    With erase (the user asked to forget it), the claim's value and the words it was read from go too."""
    ensure_schema(conn)
    if erase:
        return erase_claims(conn, "source_memory_id = ?", (int(memory_id),))
    cur = conn.execute("UPDATE memory_claims SET status = 'retracted', superseded_at = ? "
                       "WHERE source_memory_id = ? AND status IN ('current', 'former')", (time.time(), int(memory_id)))
    return int(cur.rowcount or 0)


def contradict(conn, claim_id: Any, *, by: str = "user_said") -> bool:
    """A claim a reply rested on was corrected (by the user's word, `by`) without a value of its own
    taking its place: it stands contested, as sure as its sources are against that word (confirmed),
    as a claim disputed by a newcomer is. False when there is no such standing claim."""
    ensure_schema(conn)
    row = conn.execute("SELECT origin, source_roots FROM memory_claims WHERE id = ? AND status IN ('current', 'disputed')",
                       (int(claim_id),)).fetchone()
    if not row:
        return False
    confs = _confirmations(row[1], row[0]) or [{"origin": row[0]}]
    conn.execute("UPDATE memory_claims SET contested = 1, confidence = ? WHERE id = ?",
                 (confirmed([c["origin"] for c in confs], contradicted_by=by), int(claim_id)))
    return True


def repoint_sources(conn, doomed_ids: Iterable[Any], keep_id: int) -> int:
    """Memories merged into `keep_id` (Memory.consolidate_memories): the claims read from them are read
    from it, as their source_memory_id and in each confirmation's root (either form _confirmations
    reads), so forgetting the row it joined still takes them back. Each confirmation stays one
    statement; only an entry that became the very same one is kept once. Returns the claims changed."""
    doomed = {int(i) for i in doomed_ids if int(i) != int(keep_id)}
    if not doomed:
        return 0
    ensure_schema(conn)
    marks = ",".join("?" * len(doomed))
    changed = {int(r[0]) for r in conn.execute(f"SELECT id FROM memory_claims WHERE source_memory_id IN ({marks})",
                                                sorted(doomed)).fetchall()}
    conn.execute(f"UPDATE memory_claims SET source_memory_id = ? WHERE source_memory_id IN ({marks})",
                 [int(keep_id), *sorted(doomed)])
    for cid, roots in conn.execute("SELECT id, source_roots FROM memory_claims WHERE source_roots IS NOT NULL "
                                   "AND source_roots != '[]'").fetchall():
        try:
            have = json.loads(roots or "[]")
        except ValueError:
            continue
        if not isinstance(have, list):
            continue
        moved: List[Any] = []
        for item in have:
            if isinstance(item, dict):
                item = dict(item, root=int(keep_id)) if item.get("root") in doomed else item
            elif item in doomed:
                item = int(keep_id)
            if item not in moved:
                moved.append(item)
        if moved != have:
            conn.execute("UPDATE memory_claims SET source_roots = ? WHERE id = ?", (json.dumps(moved), cid))
            changed.add(int(cid))
    return len(changed)


def erase_claims(conn, where: str, args: Tuple[Any, ...]) -> int:
    """Withdraw the claims matching `where` and blank what they held (value, source text): a
    retracted claim kept the forgotten words. Returns how many."""
    ensure_schema(conn)
    cur = conn.execute(f"UPDATE memory_claims SET status = 'retracted', superseded_at = COALESCE(superseded_at, ?), "
                       f"value = '', source_text = '' WHERE ({where}) AND (status != 'retracted' OR value != '' "
                       f"OR COALESCE(source_text, '') != '')", (time.time(), *args))
    return int(cur.rowcount or 0)


_COLS = ("id, subject, relation, value, valid_from, valid_to, recorded_at, superseded_at, status, "
         "supersedes, source_memory_id, confidence, confirmations, contested, origin")


def _rows(conn, sql: str, args: Tuple[Any, ...]) -> List[Dict[str, Any]]:
    names = [c.strip() for c in _COLS.split(",")]
    return [dict(zip(names, r)) for r in conn.execute(sql, args).fetchall()]


def current(conn, subject: str = "user") -> List[Dict[str, Any]]:
    ensure_schema(conn)
    return _rows(conn, f"SELECT {_COLS} FROM memory_claims WHERE subject = ? AND status = 'current' "
                       "ORDER BY relation, id", (subject,))


def history(conn, relation: str, subject: str = "user") -> List[Dict[str, Any]]:
    ensure_schema(conn)
    return _rows(conn, f"SELECT {_COLS} FROM memory_claims WHERE subject = ? AND relation = ? "
                       "AND status != 'retracted' ORDER BY valid_from, id", (subject, relation))


def valid_during(conn, start: float, end: float, subject: str = "user") -> List[Dict[str, Any]]:
    """Claims that held at any point in [start, end)."""
    ensure_schema(conn)
    return _rows(conn, f"SELECT {_COLS} FROM memory_claims WHERE subject = ? AND status != 'retracted' "
                       "AND COALESCE(valid_from, 0) < ? AND (valid_to IS NULL OR valid_to > ?) "
                       "ORDER BY relation, valid_from", (subject, float(end), float(start)))


def known_at(conn, when: float, subject: str = "user") -> List[Dict[str, Any]]:
    """What ELI believed at `when`: claims already recorded then and not yet superseded then."""
    ensure_schema(conn)
    return _rows(conn, f"SELECT {_COLS} FROM memory_claims WHERE subject = ? AND status != 'retracted' "
                       "AND recorded_at <= ? AND (superseded_at IS NULL OR superseded_at > ?) "
                       "ORDER BY relation, id", (subject, float(when), float(when)))


def open_conflicts(conn, subject: str = "user") -> List[Dict[str, Any]]:
    """Relations where the user's standing claim is contested by something that is not their own word."""
    ensure_schema(conn)
    out = []
    for rel, in conn.execute("SELECT DISTINCT relation FROM memory_claims WHERE subject = ? AND status = 'disputed'", (subject,)).fetchall():
        rows = _rows(conn, f"SELECT {_COLS} FROM memory_claims WHERE subject = ? AND relation = ? AND status IN ('current', 'disputed') "
                           "ORDER BY status DESC, id", (subject, rel))
        out.append({"relation": rel, "standing": [r for r in rows if r["status"] == "current"], "disputed": [r for r in rows if r["status"] == "disputed"]})
    return out


def conflict_question(conflict: Dict[str, Any]) -> str:
    rel = str(conflict["relation"]).replace("_", " ")
    mine = ", ".join(str(r["value"]) for r in conflict["standing"])
    other = ", ".join(str(r["value"]) for r in conflict["disputed"])
    return f"I have your {rel} as {mine}, but something else says {other}. Which is right?"


def resolve_conflict(conn, relation: str, value: str, subject: str = "user", now: Optional[float] = None) -> int:
    """The user said which one is right: that value stands, with their word as one more confirmation
    and nothing standing against it now; the others are closed as superseded, the figures given for
    each settled with it, and the conflict thought ELI raised is settled. How many claims were closed."""
    ensure_schema(conn)
    now = float(now if now is not None else time.time())
    keep = _norm(value)
    closed = 0
    settled: List[Tuple[int, bool]] = []
    for cid, cval, status, roots_json, c_origin in conn.execute(
            "SELECT id, value, status, source_roots, origin FROM memory_claims WHERE subject = ? AND relation = ? "
            "AND status IN ('current', 'disputed')", (subject, relation)).fetchall():
        if _norm(cval) == keep:
            # the user settled it: one more statement of theirs, and nothing stands against it now
            confs = _confirmations(roots_json, c_origin) or [{"root": None, "origin": str(c_origin or ""), "day": ""}]
            confs.append({"root": None, "origin": "user_said", "day": _day(now)})
            conn.execute("UPDATE memory_claims SET status = 'current', contested = 0, confidence = ?, origin = 'user_said', "
                         "confirmations = ?, source_roots = ?, last_confirmed = ? WHERE id = ?",
                         (confirmed([c["origin"] for c in confs]), len(confs), json.dumps(confs), now, cid))
            settled.append((cid, True))
        else:
            conn.execute("UPDATE memory_claims SET status = 'superseded', valid_to = ?, superseded_at = ?, contested = 0 WHERE id = ?", (now, now, cid))
            settled.append((cid, False))
            closed += 1

    def _settle_all() -> None:
        for claim_id, right in settled:
            _settle_figures(claim_id, right)
        from eli.cognition.thoughts import settle as _settle_thought
        _settle_thought(f"conflict:{subject}:{relation}", "done", how=f"you said {value}"[:120])
    try:
        from eli.cognition import thoughts
        from eli.core.sqlite_util import write_beside
        write_beside(conn, thoughts._default_db_path(), _settle_all)
    except (ImportError, OSError, ValueError) as failure:
        log.debug("conflict figures not settled: %s", failure)
    return closed


def answer_conflicts(conn, text: str, question: str, *, subject: str = SUBJECT_USER, now: Optional[float] = None) -> List[str]:
    """The user's answer to the question ELI asked about a dispute (`question`, the one its last reply
    left open, eli/runtime/pending_proposal): for each relation in dispute whose sides the question
    named, the side the answer names, alone, stands (resolve_conflict). The relations settled."""
    if not str(text or "").strip() or not str(question or "").strip():
        return []
    said, asked = _norm(text), _norm(question)
    settled: List[str] = []
    for conflict in open_conflicts(conn, subject):
        sides = [str(r["value"]) for r in conflict["standing"] + conflict["disputed"]]
        if not all(_mentions(asked, v) for v in sides):
            continue
        named = [v for v in sides if _mentions(said, v)]
        if len(named) == 1:
            resolve_conflict(conn, conflict["relation"], named[0], subject=subject, now=now)
            settled.append(str(conflict["relation"]))
    return settled


def _mentions(text_norm: str, value: str) -> bool:
    """Whether normalised text names a value: its words in order, as whole words."""
    v = _norm(value)
    return bool(v) and re.search(rf"(?<![a-z0-9]){re.escape(v)}(?![a-z0-9])", text_norm) is not None


def _settle_figures(claim_id: int, right: bool) -> int:
    """The user settled the claim itself: every figure given for it that a reply rested on, and that
    nothing settled yet, is settled with it (eli/cognition/epistemics, kind "claim")."""
    try:
        from eli.cognition.epistemics import CLAIM, settle_open
        return settle_open(CLAIM, f"claim:{int(claim_id)}:", right, how="conflict_resolved")
    except (OSError, ValueError, ImportError) as failure:
        log.debug("claim figures not settled: %s", failure)
        return 0


def chance(claim: Dict[str, Any]):
    """The chance the claim is right: its sureness calibrated on what became of claims of its
    origin that replies rested on (eli/cognition/epistemics, kind "claim"); the sureness itself
    until outcomes say otherwise. A Probability, with `n` the outcomes behind it."""
    from eli.cognition.epistemics import CLAIM, calibrate
    try:
        sure = float(claim.get("confidence") or 0.0)
    except (TypeError, ValueError):
        sure = 0.0
    return calibrate(CLAIM, sure, class_=str(claim.get("origin") or ""),
                     features={"confirmations": int(claim.get("confirmations") or 1),
                               "contested": bool(claim.get("contested") or claim.get("status") == "disputed")})


def describe(claim: Dict[str, Any]) -> str:
    def day(ts):
        return time.strftime("%Y-%m-%d", time.localtime(float(ts))) if ts else "?"
    rel = str(claim["relation"]).replace("_", " ")
    if not claim.get("valid_to"):
        span = f"since {day(claim['valid_from'])}"
    elif claim.get("valid_from") is None:          # held, from a time not said, until then
        span = f"until {day(claim['valid_to'])}"
    else:
        span = f"{day(claim['valid_from'])} to {day(claim['valid_to'])}"
    flag = "; DISPUTED, unresolved" if claim.get("status") == "disputed" else ("; contested" if claim.get("contested") else "")
    return f"{rel}: {claim['value']} ({span}; learned {day(claim['recorded_at'])}{flag}; {basis(claim)})"


_ORIGIN_WORDS = {"user_said": "you said so", "user_explicit": "you said so", "user_passing": "you mentioned it",
                 "document": "from a document", "observed": "seen", "eli_said": "ELI's inference",
                 "inferred": "ELI's inference"}


def origin_words(origin: Any) -> str:
    """Where a claim came from, in words ("you said so"); an origin not in ORIGINS is unknown and says
    so, never ELI's inference."""
    return _ORIGIN_WORDS.get(str(origin or ""), "its source was not recorded")


def basis(claim: Dict[str, Any]) -> str:
    """What a claim's sureness rests on, in words: "sure 0.75: you said so twice"."""
    try:
        sure = float(claim.get("confidence") or 0.0)
    except (TypeError, ValueError):
        sure = 0.0
    n = max(1, int(claim.get("confirmations") or 1))
    how = origin_words(claim.get("origin"))
    times = {1: "once", 2: "twice"}.get(n, f"{n} times")
    against = ", contradicted" if claim.get("contested") or claim.get("status") == "disputed" else ""
    checked = ""
    try:
        prob = chance(claim)
        if prob.n:
            checked = f"; chance {prob.p:.2f} after {prob.n} such claims were checked"
    except (OSError, ValueError, ImportError) as failure:   # the ledger unreadable: the sureness stands
        log.debug("claim chance unavailable: %s", failure)
    return f"sure {sure:.2f}: {how} {times}{against}{checked}"


# ── extraction: a small, exact set of first-person statements ──────────────────────────────────────
# One rule for every relation: the frame a statement is said in ("I live in", "My dog is called") is
# read whatever its case, at the start of a sentence or in the middle; only a value that has to look
# like a name or a place (a capital, (?-i:...)) is held to its case. Some patterns were compiled
# case-blind and some not, so "My dog is called Pepper" (a sentence's capital M) was no claim at all,
# and its contradiction could never be raised.

_SCHEDULE = r"(nights?|days?|evenings?|weekends?|mornings?|part[- ]time|full[- ]time|from home|remotely|shifts?)"
_PLACE = r"((?-i:[A-Z][\w'’-]*(?:\s+[A-Z][\w'’-]*){0,3}))"
_NAME = r"((?-i:[A-Z][\w'-]{1,20}))"
_PATTERNS: List[Tuple[str, str, bool, "re.Pattern"]] = [
    ("work_schedule", "single", True, re.compile(rf"\bI(?:'m| am)?\s+(?:now\s+|currently\s+)?work(?:ing)?\s+{_SCHEDULE}\b", re.I)),
    ("employer", "single", True, re.compile(rf"\bI\s+(?:now\s+)?work\s+(?:at|for)\s+{_PLACE}", re.I)),
    ("occupation", "single", True, re.compile(r"\bI\s+work\s+as\s+(?:an?\s+)?([a-z][a-z -]{2,40}?)(?=[.,;!?]|\s+(?:at|for|in|now|but|and)\b|$)", re.I)),
    ("lives_in", "single", True, re.compile(rf"\bI(?:'ve| have)?\s+(?:now\s+)?(?:live in|moved to|relocated to|am based in|'m based in)\s+{_PLACE}", re.I)),
]
# Whatever the user names: "my <one word> is called / is named / 's name is <Name>" is that thing's
# name (dog_name, sister_name, boat_name). A bare "my <x> is <Word>" says a name only of an animal
# (the kinds this always read): "my brother is Irish" is not his name.
_PET = re.compile(rf"\bmy\s+([a-z]+)(?:'s\s+name\s+is|\s+is\s+(?:called|named)|,?\s+(?:called|named))\s+{_NAME}\b"
                  rf"|\bmy\s+(dog|cat|rabbit|parrot|horse)\s+is\s+{_NAME}\b", re.I)
# A change the statement itself says happened ("now", "we moved", "the shift changed"): the user's new
# word replaces their old one. Without one, a value that contradicts what they said is weighed, and
# raised when the evidence does not settle it (record(changed=False)).
_CHANGED = re.compile(r"\b(?:now|nowadays|these days|any\s*more|no longer|moved|moving|changed|switched|started|"
                      r"new|since|recently|just|anymore)\b", re.I)
_PAST = re.compile(r"\b(?:I\s+used\s+to|I\s+no\s+longer|I\s+don'?t\s+.{0,20}\s+any\s*more|I\s+stopped|I\s+quit)\b", re.I)
_NO_LONGER = re.compile(r"\bI\s+(?:no longer|don'?t\s+(?:work|live)\b.{0,30}\bany\s*more|stopped working|quit working)\b", re.I)


_HEDGED = re.compile(r"\b(?:maybe|might|perhaps|thinking of|considering|planning to|hoping to|hope to|would like to|if i|when i|imagine|suppose|what if)\b", re.I)


# "I'm watching X", "still watching X", "I've been playing X", "X is on in the background". Present tense
# only: "going to play", "was watching" and "watching it was days ago" are not what is happening now.
_VERBS = (("watching", r"w[a-z]{0,2}tch(?:ing|in)"), ("playing", r"pla?y(?:ing|in)"),
          ("reading", r"read(?:ing|in)"), ("listening_to", r"listen(?:ing|in)\s+to"))
_ADVERBS = r"(?:(?:still|currently|now|just|actually|also|literally|only|really)\s+)*"
_VALUE = r"(?P<v>[^,.;:!?()\n]{1,60})"
_ACTIVITY = [(rel, re.compile(
    rf"(?:\bi(?:'m|\s+am|'ve\s+been|\s+have\s+been)\s+{_ADVERBS}|\bi\s+(?:just\s+)?(?:started|began)\s+|^{_ADVERBS}"
    rf"|,\s*(?:still|currently|now|just|actually)\s+{_ADVERBS}){verb}\s+{_VALUE}",
    re.I)) for rel, verb in _VERBS]
_ON_SCREEN = re.compile(r"(?:^|[,;:]\s*|\band\s+)(?P<v>[^,.;:!?()\n]{1,40}?)\s+is\s+on\s+"
                        r"(?:in\s+the\s+b\w+|the\s+(?:tv|telly|box|television)|tv|telly)\b", re.I)
_TAIL = re.compile(r"\s+(?:and|but|though|tho|with|while|whilst|because|cos|so|haha\w*|lol|lmao|now|tonight|today|"
                   r"again|atm|lately|right now|at the (?:moment|minute)|in the b\w+|for (?:a|the) \w+|for ages|"
                   r"all (?:week|day|night|morning|evening|weekend)|this (?:week|morning|afternoon|evening|weekend|month)|"
                   r"(?:every|each) (?:day|night|morning|afternoon|evening|week|weekend)|most (?:days|nights|mornings|evenings)|"
                   r"these days|recently|since \w+|"
                   r"on (?:netflix|prime(?: video)?|amazon|disney\+?|hulu|youtube|now tv|sky|the (?:tv|telly)|tv|telly|my \w+))\b.*$",
                   re.I)
_NOT_A_TITLE = {"it", "that", "this", "them", "something", "nothing", "stuff", "tv", "telly", "the tv", "the telly",
                "a film", "a movie", "a show", "films", "movies", "shows", "youtube", "videos", "music", "a game",
                "games", "some tv", "a bit", "around", "with you", "you", "me", "along", "out", "the news",
                "please", "pls", "plz", "mate", "bud", "now", "again"}
# "check what series I am watching" asks; it does not tell.
_ASKED = re.compile(r"\b(?:what|which|whatever|whichever)\b[^.!?]{0,80}$", re.I)
_TAG_QUESTION = re.compile(r",\s*(?:and\s+)?(?:you|u|yourself|how about you|what about you|wbu|hbu)\s*\?+\s*$", re.I)
# A season and episode: "season 3, episode 6", "S1 E10", "S02E05". Also read by grounding_escalation.
EPISODE_MARKER = r"\b(?:season|s)\s*\d{1,2}\s*[,·x]?\s*(?:episode|ep|e)\.?\s*\d{1,3}\b"
_EPISODE = re.compile(rf"^\s*(?P<show>[^—–|]+?)\s*(?:[—–:|-]+\s*)?{EPISODE_MARKER}", re.I)


def _title(raw: str) -> str:
    value = " ".join(str(raw or "").split())
    while True:
        cut = _TAIL.sub("", value).strip()
        if cut == value:
            break
        value = cut
    value = value.strip(" -'\"")
    if (not re.search(r"[A-Za-z]", value) or value.lower() in _NOT_A_TITLE or len(value.split()) > 6
            or value.lower().startswith(("to ", "for ", "at ", "the fact"))):
        return ""
    return value


def activities(text: str) -> List[Dict[str, Any]]:
    """What the user says they are doing now, sentence by sentence (a question is not a statement)."""
    out: List[Dict[str, Any]] = []
    for sentence in re.split(r"(?<=[.!?])\s+", str(text or "").strip()):
        sentence = _TAG_QUESTION.sub("", sentence.strip())
        if not sentence or sentence.endswith("?") or _HEDGED.search(sentence):
            continue
        found = [(rel, m.group("v")) for rel, pat in _ACTIVITY for m in [pat.search(sentence)]
                 if m and not _ASKED.search(sentence[:m.start()])]
        m = _ON_SCREEN.search(sentence)
        if m:
            found.append(("watching", m.group("v")))
        for rel, raw in found:
            value = _title(raw)
            if value and not any(o["relation"] == rel for o in out):
                out.append({"relation": rel, "value": value, "single_valued": True, "historical": False, "retire": False})
    return out


def observed_show(title: str) -> str:
    """The show in a player's title when it is an episode ("Great British Bake Off — S2 E5 – ..."), else ""."""
    m = _EPISODE.match(str(title or ""))
    return _title(m.group("show")) if m else ""


def extract(text: str) -> List[Dict[str, Any]]:
    """Claims stated in one user message. Each is {relation, value, single_valued, historical, retire}."""
    t = str(text or "").strip()
    now_doing = activities(t)
    if len(t.split()) < 3 or t.endswith("?") or _HEDGED.search(t):
        return now_doing
    stop = bool(_NO_LONGER.search(t))
    past = bool(_PAST.search(t)) and not stop
    # Take the negating or past marker out so the same patterns read what the statement is about.
    body = re.sub(r"\b(?:used to|no longer|stopped|quit|don'?t)\b\s*", "", t, flags=re.I) if (stop or past) else t
    out: List[Dict[str, Any]] = []
    for relation, _kind, single, pat in _PATTERNS:
        m = pat.search(body)
        if not m:
            continue
        value = m.group(1).strip()
        if relation == "work_schedule":
            value = re.sub(r"^night$", "nights", value.lower())
        out.append({"relation": relation, "value": value, "single_valued": single,
                    "historical": past, "retire": stop})
    m = _PET.search(t)
    if m:
        thing, name = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        out.append({"relation": f"{thing.lower()}_name", "value": name, "single_valued": True,
                    "historical": past, "retire": False})
    changed = bool(_CHANGED.search(t))
    for o in out:
        o["changed"] = changed
    return out + [a for a in now_doing if not any(o["relation"] == a["relation"] for o in out)]


def record_from_text(conn, text: str, *, source_memory_id: Optional[int] = None, when: Optional[float] = None,
                     origin: str = "user_said", root_id: Optional[int] = None,
                     subject: str = SUBJECT_USER) -> List[int]:
    ids: List[int] = []
    for c in extract(text):
        if c["retire"]:
            # only the value the user said ended ("I no longer work at Acme" leaves another employer)
            retire(conn, c["relation"], subject=subject, value=c["value"], at=when)
            continue
        cid = record(conn, c["relation"], c["value"], subject=subject, single_valued=c["single_valued"],
                     valid_from=when, source_memory_id=source_memory_id, source_text=text, origin=origin,
                     historical=c["historical"], root_id=root_id, changed=c.get("changed"))
        if cid:
            ids.append(cid)
    return ids

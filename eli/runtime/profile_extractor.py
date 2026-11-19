from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from eli.runtime.identity_validation import extract_explicit_identity_facts
from eli.utils.log import get_logger

log = get_logger(__name__)


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _user_db() -> Path:
    # Canonical user store: the same file every other subsystem uses (eli.core.paths honours
    # ELI_USER_DB/ELI_DB_DIR/ELI_DATA_DIR, then platformdirs). This used to hardcode
    # <repo>/artifacts/db/user.sqlite3, a different file from paths.user_db_path() on an installed
    # package, so the User Model and patterns were written here but read from the canonical store
    # and the brief never surfaced. Delegate so writer and reader agree.
    try:
        from eli.core.paths import user_db_path
        return Path(user_db_path())
    except Exception:
        return _root() / "artifacts" / "db" / "user.sqlite3"


def _clean(value: Any, limit: int = 600) -> str:
    s = "" if value is None else str(value)
    s = s.replace("\n", " ").replace("\r", " ")
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > limit:
        return s[: limit - 1].rstrip() + "…"
    return s


def _table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    return bool(
        cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?",
            (table,),
        ).fetchone()
    )


def pattern_owner(user_id: Any = None) -> str:
    """The user key profile rows are written and read under: "" for the machine's owner, else the
    API member's id. With no id given, the user of the turn running in this context."""
    try:
        from eli.kernel.state import member_user_id
        return member_user_id(user_id)
    except Exception:
        log.debug("pattern owner unavailable; the owner's", exc_info=True)
        return ""


def _ensure_user_column(cur: sqlite3.Cursor) -> None:
    """Whose fact a row is: "" (NULL on rows written before it was kept) for the machine's owner, an
    API member's id for theirs (kernel.state.member_user_id). One pooled table let a member's
    "my name is ..." challenge the owner's name, and put the owner's profile in members' prompts.
    Other stores create user_patterns too, so the writers check for it as well."""
    try:
        if "user_id" not in {r[1] for r in cur.execute("PRAGMA table_info(user_patterns)")}:
            cur.execute("ALTER TABLE user_patterns ADD COLUMN user_id TEXT")
    except sqlite3.OperationalError:
        log.debug("user_patterns has no user column", exc_info=True)


# The semantic tier keeps the owner's rows under "default", as it always has.
def _semantic_user(who: str) -> str:
    return who or "default"


def ensure_profile_tables(db_path: Path | None = None) -> None:
    db = db_path or _user_db()
    db.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(db))
    cur = con.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_type TEXT,
            pattern_data TEXT,
            timestamp REAL,
            ts REAL
        )
        """
    )
    # One fact, one revision history, one provenance graph: claim_id points this
    # row at its entry in memory_claims (eli.memory.claims), written alongside
    # it in _insert_user_pattern rather than backfilled onto existing rows.
    try:
        cur.execute("ALTER TABLE user_patterns ADD COLUMN claim_id INTEGER")
    except sqlite3.OperationalError:
        pass
    _ensure_user_column(cur)

    # The semantic tier: durable user facts. Four readers depend on it (recall injects these first
    # on identity questions with a +0.5 boost, two status surfaces count them). Nothing wrote it:
    # its only writer, MemorySystem.store_semantic(), had zero callers, so the table was never
    # created and every read threw "no such table: semantic". Created here beside the patterns
    # table it's populated from, so the schema exists from first boot.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS semantic (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            fact TEXT,
            tags TEXT,
            confidence REAL DEFAULT 0.8,
            created_at REAL,
            evidence_count INTEGER DEFAULT 1,
            last_seen REAL,
            evidence TEXT
        )
        """
    )
    for _col, _decl in (("evidence_count", "INTEGER DEFAULT 1"), ("last_seen", "REAL"), ("evidence", "TEXT")):
        try:
            cur.execute(f"ALTER TABLE semantic ADD COLUMN {_col} {_decl}")
        except sqlite3.OperationalError:
            pass

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS session_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            user_id TEXT,
            summary TEXT,
            content TEXT,
            turns_count INTEGER,
            started_at REAL,
            ended_at REAL,
            source TEXT,
            timestamp REAL,
            ts REAL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            category TEXT,
            observation TEXT,
            content TEXT,
            text TEXT,
            details TEXT,
            timestamp REAL,
            ts REAL
        )
        """
    )

    # Continuous User Model: one synthesised row per user_id. The structured JSON columns and
    # free-text dossier are the in-depth view, `brief` is a pre-rendered block for a fast per-turn
    # read (single SELECT, no joins). Scoped by user_id so one user's model never bleeds into
    # another's. Evidence stays in user_patterns/memories/KG, this is synthesis.
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS user_model (
            user_id       TEXT PRIMARY KEY,
            identity      TEXT,
            comms_style   TEXT,
            current_focus TEXT,
            interests     TEXT,
            habits        TEXT,
            goals         TEXT,
            relationship  TEXT,
            dossier       TEXT,
            brief         TEXT,
            sources       TEXT,
            confidence    REAL,
            updated_at    REAL,
            ts            REAL
        )
        """
    )

    # Belief storage and the corroboration/provenance columns the weighing needs.
    # Before the backfill, so those passes see the migrated shape.
    try:
        from eli.cognition.stance_store import ensure_tables as _ensure_belief
        _ensure_belief(cur)
    except Exception:
        log.debug("belief tables unavailable", exc_info=True)

    backfill_semantic_from_patterns(cur)
    reconcile_single_valued_patterns(cur)
    fold_restated_patterns(cur)
    retired = retire_unfounded_readings(cur)

    con.commit()
    con.close()
    _refresh_after_retiring(db, retired)


def retire_unfounded_readings(cur: sqlite3.Cursor, *, reweigh: bool = False) -> dict[str, list[str]]:
    """Take out of the profile ELI's readings of the user that no longer hold.

    A reading (a fact ELI's model wrote about the user, provenance "inferred") that bears out a claim
    of the user's stands on that claim (_insert_user_pattern links it there). When the user's word on
    it changes ("we moved, I live in Ashby Green now"), the claim is superseded and the readings that
    rest on it go with it: each written to belief_revisions first, as every retired value is, so history
    keeps it, then out of user_patterns and its semantic copy. They used to accumulate: "Lives in Fenwick
    Bay" stood in the profile, and in every prompt, beside the user's word that they had moved.

    `reweigh` (the summariser's pass, once a session) also weighs the readings kept before they were
    weighed, which stand on a claim of their own: one that contradicts what the user said is retired
    the same way, and one that bears a claim of theirs out is moved onto it (its own claim closed).

    Returns {owner: [values that no longer hold]}, for the brief rebuilt from these rows."""
    retired: dict[str, list[str]] = {}
    if not (_table_exists(cur, "user_patterns") and _table_exists(cur, "memory_claims")):
        return retired
    try:
        from eli.memory import claims as _claims
        from eli.cognition.stance_store import ensure_tables, record_revision
        ensure_tables(cur)
    except (ImportError, sqlite3.Error) as failure:
        log.debug("profile_extractor: readings not checked: %s", failure)
        return retired
    _ensure_user_column(cur)
    now = time.time()

    def _retire(row_id: int, ptype: str, data: str, who: str, rested: str, standing: str, why: str) -> None:
        record_revision(cur, "user_pattern", ptype, data, standing, reason=why, now=now)
        cur.execute("DELETE FROM user_patterns WHERE id = ?", (row_id,))
        cur.execute("DELETE FROM semantic WHERE lower(COALESCE(fact, '')) = lower(?) "
                    "AND COALESCE(NULLIF(user_id, ''), 'default') = ? "
                    "AND ',' || lower(COALESCE(tags, '')) || ',' LIKE ?",
                    (split_evidence(data)[0], _semantic_user(who), f"%,{str(ptype).lower()},%"))
        retired.setdefault(who, []).append(rested)

    def _standing(subject: str, relation: str) -> str:
        row = cur.execute("SELECT value FROM memory_claims WHERE subject = ? AND relation = ? AND status = 'current' "
                          "ORDER BY id DESC LIMIT 1", (subject, relation)).fetchone()
        return str(row[0]) if row else ""

    try:
        if reweigh:
            for row_id, ptype, data, who, ts in cur.execute(
                    "SELECT p.id, p.pattern_type, p.pattern_data, COALESCE(p.user_id, ''), COALESCE(p.ts, p.timestamp) "
                    "FROM user_patterns p LEFT JOIN memory_claims c ON c.id = p.claim_id "
                    "WHERE COALESCE(p.provenance, '') = 'inferred' "
                    "AND (c.id IS NULL OR lower(c.relation) = lower(p.pattern_type))").fetchall():
                if not _summary_section_meaningful(split_evidence(str(data or ""))[0]):
                    # kept before an empty section was read as empty ("None specified"): no fact at all
                    _claims.retire(cur, str(ptype), subject=_claims.user_subject(who), value=str(data or ""), now=now)
                    _retire(row_id, ptype, str(data or ""), who, str(data or ""), "", "it says nothing about you")
                    continue
                found = [c for c in _claims.extract_about(split_evidence(str(data or ""))[0]) if not c["retire"]]
                if not found:
                    continue
                read_as = _weigh_reading(cur, str(data or ""), who, float(ts or now))
                if read_as is None or read_as:
                    # its own claim is closed: the reading is now the claim it bears out, or no fact at all
                    _claims.retire(cur, str(ptype), subject=_claims.user_subject(who), value=str(data or ""), now=now)
                if read_as is None:
                    rel = found[0]["relation"]
                    _retire(row_id, ptype, str(data or ""), who, found[0]["value"],
                            _standing(_claims.user_subject(who), rel),
                            f"ELI's reading of {rel.replace('_', ' ')} is not what you said")
                elif read_as:
                    cur.execute("UPDATE user_patterns SET claim_id = ? WHERE id = ?", (int(read_as[0]), row_id))
        for row_id, ptype, data, who, subject, relation, value in cur.execute(
                "SELECT p.id, p.pattern_type, p.pattern_data, COALESCE(p.user_id, ''), c.subject, c.relation, c.value "
                "FROM user_patterns p JOIN memory_claims c ON c.id = p.claim_id "
                "WHERE COALESCE(p.provenance, '') = 'inferred' AND c.status IN ('superseded', 'retracted') "
                "AND lower(c.relation) != lower(p.pattern_type)").fetchall():
            _retire(row_id, ptype, str(data or ""), who, str(value or ""), _standing(subject, relation),
                    f"rested on {str(relation).replace('_', ' ')} = {value}, which no longer holds")
    except sqlite3.Error as failure:
        log.debug("profile_extractor: readings not checked: %s", failure)
    for who, values in retired.items():
        log.debug("profile_extractor: %d reading(s) of %s no longer hold: %s", len(values), who or "the owner", values)
    return retired


def _refresh_after_retiring(db: Path, retired: dict[str, list[str]]) -> None:
    """What is built from the profile's rows is built again once readings were taken out of them: the
    per-turn brief (eli/runtime/user_model), its narrative without the sentences naming what no longer
    holds, until the next synthesis writes it anew."""
    for who, values in retired.items():
        try:
            from eli.runtime.user_model import refresh_user_model_brief
            refresh_user_model_brief(user_id=who or None, db_path=db, retired=values)
        except Exception:
            log.debug("user model refresh after retiring readings skipped", exc_info=True)


def _scrub_onboarding_snapshot(cur: sqlite3.Cursor, stale_values: list) -> None:
    """Drop the onboarding baseline row when it embeds a value we just retired.

    ``_baseline_report`` writes one composite memory (kind='identity',
    source='onboarding_interview') that concatenates the same four answers held
    individually in user_patterns. It is never rewritten, so once a single-valued
    answer is corrected the snapshot is a duplicate that disagrees with the
    canonical row — and being in memories_fts, it is the copy recall is most
    likely to surface. The canonical rows survive; only the stale composite goes.
    """
    for value in stale_values:
        core = (value or "").strip().rstrip(".")
        # Match on a distinctive middle chunk: the snapshot joins sentences, so
        # the stored value appears inside it rather than as the whole field.
        if len(core) < 12:
            continue
        try:
            # Capture ids BEFORE deleting so the vector store can be tombstoned
            # too (same pattern as consolidate_memories) -- otherwise a
            # retracted fact's embedding survives and can resurface via recall.
            cur.execute(
                """
                SELECT id FROM memories
                 WHERE lower(COALESCE(source,'')) = 'onboarding_interview'
                   AND instr(lower(COALESCE(text,'')), lower(?)) > 0
                """,
                (core,),
            )
            doomed = [row[0] for row in cur.fetchall()]
            cur.execute(
                """
                DELETE FROM memories
                 WHERE lower(COALESCE(source,'')) = 'onboarding_interview'
                   AND instr(lower(COALESCE(text,'')), lower(?)) > 0
                """,
                (core,),
            )
            if doomed:
                try:
                    from eli.memory.vector_store import get_vector_store
                    _vs = get_vector_store()
                    if _vs is not None:
                        _vs.mark_memories_deleted(doomed)
                except Exception:
                    log.debug("could not tombstone scrubbed onboarding snapshot", exc_info=True)
        except Exception:
            log.debug("could not scrub onboarding snapshot", exc_info=True)


def reconcile_single_valued_patterns(cur: sqlite3.Cursor) -> int:
    """Collapse pre-existing duplicates of a single-valued key to the newest.

    The write path now supersedes on insert, but databases written before that
    already carry the contradictions — on the machine this was diagnosed on,
    preference.style held two mutually exclusive values simultaneously. Newest
    wins, because for a single-valued field a later answer is a correction.

    Idempotent, and a no-op on a clean database, so it is safe to run on every
    ensure_profile_tables().
    """
    removed = 0
    _ensure_user_column(cur)
    try:
        owners = [str(r[0] or "") for r in cur.execute(
            "SELECT DISTINCT COALESCE(user_id, '') FROM user_patterns").fetchall()]
    except Exception:
        owners = [""]
    for who in owners or [""]:
        for ptype in sorted(_SINGLE_VALUED_PATTERNS):
            try:
                rows = cur.execute(
                    """
                    SELECT rowid, pattern_data FROM user_patterns
                     WHERE lower(COALESCE(pattern_type,'')) = lower(?)
                       AND COALESCE(user_id, '') = ?
                     ORDER BY COALESCE(timestamp, ts, 0) DESC, rowid DESC
                    """,
                    (ptype, who),
                ).fetchall()
            except Exception:
                log.debug("reconcile: could not read %s", ptype, exc_info=True)
                continue
            if len(rows) < 2:
                continue
            keep_value = rows[0][1]
            stale = [r[0] for r in rows[1:]]
            stale_values = [r[1] for r in rows[1:]]
            try:
                cur.executemany(
                    "DELETE FROM user_patterns WHERE rowid = ?", [(r,) for r in stale]
                )
                _supersede_single_valued(cur, ptype, keep_value, who=who)
                if not who:
                    _scrub_onboarding_snapshot(cur, stale_values)
                removed += len(stale)
                log.info(
                    "profile: %s had %d competing values; kept the newest (%s)",
                    ptype, len(rows), str(keep_value)[:80],
                )
            except Exception:
                log.debug("reconcile: could not collapse %s", ptype, exc_info=True)
    return removed


def backfill_semantic_from_patterns(cur: sqlite3.Cursor) -> int:
    """Seed the semantic tier from user_patterns already on disk.

    The tier only started being written in 2.1.87, so an existing install has a
    populated user_patterns table and an empty semantic one — every durable fact
    ELI had already learned stayed invisible to the identity-recall path that
    reads semantic first.

    Runs only when semantic is EMPTY, which makes it a one-shot: afterwards the
    extractor keeps it current, and a user who deliberately clears the tier does
    not get it silently refilled from history on the next start.

    Promotion goes through _promote_to_semantic, so the durable/transient filter
    and the dedupe are the ones the live path uses — not a second copy that could
    drift away from it.
    """
    try:
        if not _table_exists(cur, "semantic") or not _table_exists(cur, "user_patterns"):
            return 0
        existing = cur.execute("SELECT COUNT(*) FROM semantic").fetchone()
        if existing and int(existing[0] or 0) > 0:
            return 0
        rows = cur.execute(
            "SELECT pattern_type, pattern_data, COALESCE(ts, timestamp, 0), COALESCE(user_id, '') "
            "FROM user_patterns ORDER BY COALESCE(ts, timestamp, 0) ASC"
        ).fetchall()
    except Exception:
        log.debug("profile_extractor: semantic backfill query failed", exc_info=True)
        return 0

    promoted = 0
    for ptype, pdata, ts_value, who in rows:
        try:
            if _promote_to_semantic(cur, str(ptype or ""), str(pdata or ""),
                                    float(ts_value or time.time()), who=str(who or "")):
                promoted += 1
        except Exception:
            log.debug("profile_extractor: semantic backfill row failed", exc_info=True)
    if promoted:
        log.info("profile_extractor: seeded %d durable fact(s) into the semantic "
                 "tier from existing user_patterns", promoted)
    return promoted


# Profile keys that describe one thing about the user (one role, one answer style, one primary
# goal). A later answer is a correction, not another fact. The dedupe below keys on
# (pattern_type, pattern_data), which is wrong here: a correction wrote a second row and both
# survived, so ELI held contradictory answers.
_SINGLE_VALUED_PATTERNS = frozenset({
    "identity.name",
    "identity.preferred_name",
    "identity.nickname",
    "identity.role",
    "preference.style",
    "goal.primary",
    # One current work focus / near-term plan — a correction must replace, not
    # accumulate, or retrieval order decides which mutually exclusive trip wins.
    "project.current",
})


def _supersede_single_valued(cur: sqlite3.Cursor, pattern_type: str,
                            pattern_data: str,
                            provenance: str = "user_passing",
                            said_at: float | None = None,
                            who: str = "") -> str:
    """Weigh a replacement for a single-valued key, and retire the old value only
    if it actually loses. Returns the verdict the caller applies to the claims
    spine too ("concede" or "question"), or "" when the standing value holds.

    This used to delete unconditionally: whoever wrote last won. That is the
    yes-man mechanism sitting in the data layer — it makes ASSERTION equal to
    EVIDENCE, so a passing mention could overwrite something the user had stated
    outright and reaffirmed a dozen times, silently and with no record.

    Now the standing value is weighed against the new one (see
    `eli.cognition.belief`), each as of when it was said: `said_at` is the new
    statement's own time (a replayed old turn is weighed as old, not as said now),
    and a statement older than the value it would replace cannot correct it.
    A truthy verdict means the replacement should proceed. "" means the standing
    value held and the CALLER MUST NOT INSERT — an unweighed insert would leave
    both values present, which is worse than either outcome. Neither a hold nor a
    question is kept silent: each is voiced (eli/cognition/belief.voice), so the
    reply says ELI is keeping what it holds, or asks which is right. Only `who`'s
    rows (pattern_owner) are weighed or retired: one user's statement never touches
    another's facts.

    Retiring still DELETES rather than flagging, for the original reason: several
    consumers read these rows and a 'superseded' column each would have to honour
    is a filter waiting to be forgotten. What is new is that the retired value is
    written to `belief_revisions` first, so it is recoverable and ELI can say what
    it used to hold and why that changed.
    """
    now = time.time()
    said = float(said_at) if said_at else now
    decided = "concede"
    try:
        from eli.cognition.belief import (
            Belief, HOLD, QUESTION, assess_claim, voice,
        )
        from eli.memory.claims import user_subject
        from eli.cognition.stance_store import ensure_tables, record_revision
        ensure_tables(cur)
        row = cur.execute(
            "SELECT pattern_data, COALESCE(corroboration, 1), "
            "       COALESCE(provenance, 'user_passing'), "
            "       COALESCE(timestamp, ts, 0) "
            "  FROM user_patterns "
            " WHERE lower(COALESCE(pattern_type,'')) = lower(?) "
            "   AND lower(COALESCE(pattern_data,'')) <> lower(?) "
            "   AND COALESCE(user_id, '') = ? "
            " ORDER BY COALESCE(timestamp, ts, 0) DESC LIMIT 1",
            (pattern_type, pattern_data, who),
        ).fetchone()
        if row:
            standing_seen = float(row[3] or now)
            if said < standing_seen:
                # An earlier statement cannot correct a later one: replaying history at boot used
                # to set the name, role or style back to whatever the oldest window said.
                log.debug("profile_extractor: keeping %s=%r, said after %r", pattern_type, row[0], pattern_data)
                return ""
            standing = Belief(statement=row[0], corroboration=int(row[1] or 1),
                              provenance=row[2], last_seen=standing_seen)
            challenger = Belief(statement=pattern_data, corroboration=1,
                                provenance=provenance, last_seen=said)
            verdict = assess_claim(standing, challenger, now)
            # Refuse only on HOLD. A QUESTION means the evidence doesn't settle it, and on a single-valued
            # key something has to be stored. Deferring to the newer statement when nothing distinguishes
            # them isn't caving (caving is yielding when you hold the better evidence, which HOLD catches).
            # Refusing here would bring back the original defect of a correction leaving two mutually
            # exclusive values.
            if verdict.action in (HOLD, QUESTION):
                voice(verdict.action, about=pattern_type, standing=row[0], challenger=pattern_data,
                      reasons=verdict.reasons, subject=user_subject(who), conn=cur)
            if verdict.action == HOLD:
                log.debug("profile_extractor: keeping %s=%r over %r (%s)",
                          pattern_type, row[0], pattern_data, verdict.action)
                return ""
            decided = "question" if verdict.action == QUESTION else "concede"
            record_revision(cur, "user_pattern", pattern_type, row[0], pattern_data,
                            reason="; ".join(verdict.reasons),
                            standing_weight=verdict.standing_weight,
                            challenger_weight=verdict.challenger_weight, now=now)
    except Exception:
        # Never block a write on the belief layer. Falling back to the old
        # last-writer-wins is worse than weighing, but far better than losing
        # the correction entirely.
        log.debug("belief assessment unavailable; superseding unweighed",
                  exc_info=True)

    try:
        cur.execute(
            """
            DELETE FROM user_patterns
             WHERE lower(COALESCE(pattern_type,'')) = lower(?)
               AND lower(COALESCE(pattern_data,'')) <> lower(?)
               AND COALESCE(user_id, '') = ?
            """,
            (pattern_type, pattern_data, who),
        )
    except Exception:
        log.debug("could not retire earlier %s rows", pattern_type, exc_info=True)
    try:
        # _promote_to_semantic stores the key inside tags as
        # "semantic,user_fact,<pattern_type>" — there is no dedicated column.
        cur.execute(
            "DELETE FROM semantic "
            " WHERE ',' || lower(COALESCE(tags,'')) || ',' LIKE ? "
            "   AND lower(COALESCE(fact,'')) <> lower(?) "
            "   AND COALESCE(NULLIF(user_id, ''), 'default') = ?",
            (f"%,{pattern_type.lower()},%", split_evidence(pattern_data)[0], _semantic_user(who)),
        )
    except Exception:
        log.debug("could not retire earlier semantic %s rows", pattern_type, exc_info=True)
    return decided


def _link_pattern_to_claim(cur: sqlite3.Cursor, pattern_type: str, pattern_data: str,
                          provenance: str, now: float, who: str = "", verdict: str = "",
                          claim_id: int | None = None) -> None:
    """Give this user_patterns row a real entry in the bitemporal claims spine
    (eli.memory.claims) instead of being a disconnected free-text duplicate.
    pattern_type/pattern_data are already relation/value-shaped — this is a
    direct claims.record(), not the first-person-text extraction claims.py
    also offers, which would re-parse text that's already structured.

    The claim is the user's (`who`, as claims.user_subject keys members), its
    origin the fact's provenance (one vocabulary: claims.ORIGINS), the day it was
    said what makes a restatement another confirmation, and `verdict` the one
    the profile's weighing reached, so the two stores never decide one fact two
    ways (the spine used to dispute a value the profile had already replaced).

    `claim_id`: the claim this row is ELI's reading of (_weigh_reading), which it is linked to in
    place of a claim of its own, so it stands and falls with what the user said.

    Best-effort: a failure here must never break the user_patterns write it
    rides alongside, so every exception is swallowed after a debug log.
    """
    try:
        from eli.memory import claims as _claims
        if claim_id is None:
            claim_id = _claims.record(
                cur, pattern_type, pattern_data,
                subject=_claims.user_subject(who),
                single_valued=pattern_type.lower() in _SINGLE_VALUED_PATTERNS,
                origin=provenance, valid_from=now, now=time.time(), verdict=verdict,
            )
        if claim_id is not None:
            cur.execute(
                "UPDATE user_patterns SET claim_id = ? "
                " WHERE lower(COALESCE(pattern_type, '')) = lower(?) "
                "   AND lower(COALESCE(pattern_data, '')) = lower(?) "
                "   AND COALESCE(user_id, '') = ?",
                (claim_id, pattern_type, pattern_data, who),
            )
    except Exception:
        log.debug("could not link user_pattern to a claim", exc_info=True)


def _durable(pattern_type: str) -> bool:
    """Whether a kind of fact is kept in the semantic tier (a lasting fact, not a session's state)."""
    ptype = str(pattern_type or "").strip().lower()
    return ptype.startswith(_SEMANTIC_PATTERN_PREFIXES) and not ptype.startswith(_SEMANTIC_PATTERN_EXCLUDE)


def _same_fact_row(cur: sqlite3.Cursor, pattern_type: str, pattern_data: str, who: str) -> tuple[str, str] | None:
    """The stored row this statement says again, if any, as (its kind, its data): the same label of the
    same kind, whatever quote stands beside it (a row's data is its label plus the user's quote,
    _evidence, so comparing whole strings counted every rephrasing as a rival fact and corroboration
    never built); else a row of that kind that says it in other words (claims.said_again); else, for a
    kind that keeps many facts, a row of another kind that does. The model files one fact under one
    kind in one pass and another in the next, and each was kept as a fact of its own. A kind that
    holds one value is always kept under its own name, and a lasting fact is never taken for a
    session's state said in the same words."""
    from eli.memory.claims import said_again
    label = split_evidence(pattern_data)[0]
    kind = str(pattern_type or "").lower()
    rows = [(str(t or ""), str(d or "")) for t, d in cur.execute(
        "SELECT pattern_type, pattern_data FROM user_patterns WHERE COALESCE(user_id, '') = ? ORDER BY id",
        (who,)).fetchall()]
    own = [(t, d) for t, d in rows if t.lower() == kind]
    for t, d in own:
        if split_evidence(d)[0].lower() == label.lower():
            return t, d
    for t, d in own:
        if said_again(d, label):
            return t, d
    if kind in _SINGLE_VALUED_PATTERNS:
        return None
    for t, d in rows:
        if t.lower() != kind and (_durable(t) or not _durable(kind)) and said_again(d, label):
            return t, d
    return None


def fold_restated_patterns(cur: sqlite3.Cursor) -> int:
    """One fact kept once: rows of one user that say the same thing (claims.said_again) under several
    kinds, or in several wordings of one kind, kept from before a restatement was read as one
    (_same_fact_row), are folded into one. The row kept is the one whose kind holds one value (a name
    stays the name), then a lasting kind over a session's, the heavier provenance, the more often said,
    the first kept. It takes the heaviest provenance and the most days said of them all; each other row
    is written to belief_revisions, as every value taken out is, its own claim closed, and removed.
    Two kinds that each hold one value both stand. Returns the rows folded away; nothing on a store
    with no fact kept twice, so it runs at every start."""
    if not _table_exists(cur, "user_patterns"):
        return 0
    try:
        from eli.cognition.belief import PROVENANCE_WEIGHT
        from eli.cognition.stance_store import ensure_tables, record_revision
        from eli.memory import claims as _claims
        ensure_tables(cur)
        _ensure_user_column(cur)
        rows = cur.execute(
            "SELECT id, COALESCE(user_id, ''), pattern_type, pattern_data, COALESCE(corroboration, 1), "
            "COALESCE(provenance, ''), COALESCE(ts, timestamp, 0), claim_id FROM user_patterns ORDER BY id").fetchall()
    except (ImportError, sqlite3.Error) as failure:
        log.debug("profile_extractor: restated facts not folded: %s", failure)
        return 0
    facts: dict[tuple, list] = {}
    for row in rows:
        words, denies = _claims.says(str(row[3] or ""))
        if words:
            facts.setdefault((row[1], words, denies), []).append(row)
    folded = 0
    now = time.time()
    for (who, _words, _denies), same in facts.items():
        if len(same) < 2:
            continue

        def single(row) -> bool:
            return str(row[2] or "").lower() in _SINGLE_VALUED_PATTERNS

        kept = max(same, key=lambda r: (single(r), _durable(r[2]), PROVENANCE_WEIGHT.get(r[5], 0.0), int(r[4] or 1),
                                        -int(r[0])))
        others = [r for r in same if r is not kept
                  and not (single(r) and str(r[2] or "").lower() != str(kept[2] or "").lower())]
        if not others:
            continue
        try:
            heaviest = max([kept, *others], key=lambda r: PROVENANCE_WEIGHT.get(r[5], 0.0))[5]
            cur.execute("UPDATE user_patterns SET corroboration = ?, provenance = ?, ts = ?, timestamp = ? WHERE id = ?",
                        (max(int(r[4] or 1) for r in [kept, *others]), heaviest or None,
                         max(float(r[6] or 0.0) for r in [kept, *others]),
                         max(float(r[6] or 0.0) for r in [kept, *others]), kept[0]))
            for row_id, _who, ptype, data, _n, _prov, _ts, _claim in others:
                record_revision(cur, "user_pattern", str(ptype or ""), str(data or ""), str(kept[3] or ""),
                                reason=f"the same fact as {kept[2]}, kept once", now=now)
                _claims.retire(cur, str(ptype or ""), subject=_claims.user_subject(who), value=str(data or ""), now=now)
                cur.execute("DELETE FROM user_patterns WHERE id = ?", (row_id,))
                if (_table_exists(cur, "semantic")
                        and split_evidence(str(data or ""))[0].lower() != split_evidence(str(kept[3] or ""))[0].lower()):
                    cur.execute("DELETE FROM semantic WHERE lower(COALESCE(fact, '')) = lower(?) "
                                "AND COALESCE(NULLIF(user_id, ''), 'default') = ? "
                                "AND ',' || lower(COALESCE(tags, '')) || ',' LIKE ?",
                                (split_evidence(str(data or ""))[0], _semantic_user(who),
                                 f"%,{str(ptype or '').lower()},%"))
                folded += 1
        except sqlite3.Error as failure:
            log.warning("profile_extractor: a restated fact was not folded: %s", failure)
    if folded:
        log.info("profile: %d fact(s) kept twice folded into the one that says them", folded)
    return folded


def _insert_user_pattern(
    cur: sqlite3.Cursor,
    pattern_type: str,
    pattern_data: str,
    ts_value: float | None = None,
    provenance: str = "user_passing",
    user_id: Any = None,
) -> bool:
    """Store one fact about a user. `ts_value` is when it was said; `user_id` whose it is (the
    turn's user when not given, pattern_owner). Returns True when a new row was written."""
    pattern_type = _clean(pattern_type, 120)
    pattern_data = _clean(pattern_data, 900)
    now = float(ts_value or time.time())
    who = pattern_owner(user_id)

    if not pattern_type or not pattern_data:
        return False

    _ensure_user_column(cur)
    # ELI's reading of what the user said is weighed against their word before it is kept: one that
    # contradicts it is no fact about them; one that bears a claim of theirs out stands on that claim
    read_as = _weigh_reading(cur, pattern_data, who, now) if provenance == "inferred" else []
    if read_as is None:
        return False
    rests_on = read_as[0] if read_as else None
    said = _same_fact_row(cur, pattern_type, pattern_data, who)
    same = said[1] if said else None
    if said:
        pattern_type = said[0]      # said again under another kind: it is that row's fact
    verdict = ""
    if same is None and pattern_type.lower() in _SINGLE_VALUED_PATTERNS:
        # A refusal means the standing value won. Inserting anyway would leave
        # both values present on a key that is single-valued by definition.
        verdict = _supersede_single_valued(cur, pattern_type, pattern_data,
                                           provenance=provenance, said_at=now, who=who)
        if not verdict:
            return False

    if same is not None:
        # Reaffirmation refreshes recency and corroborates once per day of use, not once per extraction
        # pass (which counted one fact 19,219 times). Provenance only goes up, and recency never goes
        # back: an old statement replayed (the boot backfill) leaves a newer time standing.
        _match = ("WHERE lower(COALESCE(pattern_type, '')) = lower(?) "
                  "AND lower(COALESCE(pattern_data, '')) = lower(?) AND COALESCE(user_id, '') = ?")
        _key = (pattern_type, same, who)
        try:
            cur.execute(
                """
                UPDATE user_patterns
                   SET corroboration = COALESCE(corroboration, 1)
                         + (date(COALESCE(ts, 0), 'unixepoch', 'localtime') < date(?, 'unixepoch', 'localtime')),
                       timestamp = MAX(COALESCE(timestamp, 0), ?), ts = MAX(COALESCE(ts, 0), ?)
                """ + _match,
                (now, now, now, *_key),
            )
        except Exception:
            # Pre-migration database without the column — recency alone.
            try:
                cur.execute(
                    "UPDATE user_patterns SET timestamp = MAX(COALESCE(timestamp, 0), ?), "
                    "ts = MAX(COALESCE(ts, 0), ?) " + _match,
                    (now, now, *_key),
                )
            except Exception:
                log.debug("could not refresh %s", pattern_type, exc_info=True)
        try:
            from eli.cognition.belief import PROVENANCE_WEIGHT
            # Compare the new provenance weight against the existing row's, in Python: PROVENANCE_WEIGHT is
            # a Python dict, not a SQL table, so it can't be one UPDATE. (It used to compare the new weight
            # against itself via `(SELECT ?)`, always False, so provenance was never upgraded here.
            # belief.py's corroborate() had the correct comparison and this mirrors it.)
            existing_row = cur.execute("SELECT provenance FROM user_patterns " + _match + " LIMIT 1",
                                       _key).fetchone()
            existing_provenance = (existing_row or (None,))[0]
            if (PROVENANCE_WEIGHT.get(provenance, 0.0)
                    > PROVENANCE_WEIGHT.get(existing_provenance, 0.0)):
                cur.execute("UPDATE user_patterns SET provenance = ? " + _match, (provenance, *_key))
        except Exception:
            log.debug("could not upgrade provenance", exc_info=True)
        if same.lower() != pattern_data.lower():
            _promote_to_semantic(cur, pattern_type, pattern_data, now, who=who)   # the new words are evidence
        _link_pattern_to_claim(cur, pattern_type, same, provenance, now, who=who, claim_id=rests_on)
        return False

    try:
        cur.execute(
            "INSERT INTO user_patterns(pattern_type, pattern_data, timestamp, ts, "
            "                          corroboration, provenance, user_id) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (pattern_type, pattern_data, now, now, provenance, who or None),
        )
    except Exception:
        # Pre-migration database without the belief columns.
        log.debug("insert with provenance failed; using the legacy shape",
                  exc_info=True)
        cur.execute(
            """
            INSERT INTO user_patterns(pattern_type, pattern_data, timestamp, ts)
            VALUES (?, ?, ?, ?)
            """,
            (pattern_type, pattern_data, now, now),
        )

    # Must run on BOTH insert paths. An earlier cut returned straight after the
    # new insert and skipped it, so nothing reached the semantic tier at all.
    _promote_to_semantic(cur, pattern_type, pattern_data, now, who=who)
    _link_pattern_to_claim(cur, pattern_type, pattern_data, provenance, now, who=who, verdict=verdict,
                           claim_id=rests_on)
    return True


def _weigh_reading(cur: sqlite3.Cursor, pattern_data: str, who: str, when: float) -> list | None:
    """ELI's reading of the user (a fact its model wrote about them) weighed in the claims spine as the
    claims it states (claims.record_reading): the ids of the claims it bears out or adds, [] when it
    states none the claims read, None when it contradicts what the user said."""
    try:
        from eli.memory import claims as _claims
        return _claims.record_reading(cur, split_evidence(pattern_data)[0], subject=_claims.user_subject(who),
                                      when=when, now=time.time())
    except (sqlite3.Error, ImportError, ValueError, TypeError) as failure:
        log.debug("profile_extractor: reading not weighed: %s", failure)
        return []


# Which pattern kinds are durable facts about the user and not transient session state. Only these
# are promoted: the semantic tier is injected ahead of ordinary recall on identity questions, so
# per-session chatter would push real facts down the list it exists to top.
_SEMANTIC_PATTERN_PREFIXES = ("identity.", "preference.", "project.", "research.", "interest.")
_SEMANTIC_PATTERN_EXCLUDE = ("preference.session",)


_SAID_RE = re.compile(r'\s+Said:\s*"(.*?)"?\s*$', re.S)
MAX_EVIDENCE_QUOTES = 5


def split_evidence(fact: str) -> tuple[str, str]:
    """('User prefers depth.', 'be more in depth') from 'User prefers depth. Said: "be more in depth"'."""
    m = _SAID_RE.search(fact or "")
    return ((fact[:m.start()].strip(), m.group(1).strip()) if m else ((fact or "").strip(), ""))


def add_evidence(quotes_json: str | None, quote: str) -> str:
    quotes = [q for q in json.loads(quotes_json or "[]") if q != quote]
    return json.dumps(([*quotes, quote] if quote else quotes)[-MAX_EVIDENCE_QUOTES:], ensure_ascii=False)


def _promote_to_semantic(
    cur: sqlite3.Cursor,
    pattern_type: str,
    pattern_data: str,
    ts_value: float,
    who: str = "",
) -> bool:
    """Record a newly-learned durable user fact in the semantic tier, under its user (`who`,
    pattern_owner: the owner's rows keep the tier's "default").

    Called when _insert_user_pattern inserted a row or the user said a stored fact again in other
    words, so this inherits that dedupe rather than repeating it — and it checks the semantic table
    too, since the two can diverge on an existing install whose user_patterns predate this tier
    being written at all.
    """
    ptype = (pattern_type or "").strip().lower()
    if not ptype.startswith(_SEMANTIC_PATTERN_PREFIXES):
        return False
    if ptype.startswith(_SEMANTIC_PATTERN_EXCLUDE):
        return False
    fact = _clean(pattern_data, 900)
    if not fact:
        return False
    owner = _semantic_user(who)
    try:
        if not _table_exists(cur, "semantic"):
            return False
        # One row per fact; each new utterance of it is evidence on that row, not a new fact.
        label, quote = split_evidence(fact)
        row = cur.execute(
            "SELECT id, evidence FROM semantic WHERE lower(COALESCE(fact,'')) = lower(?) "
            "AND COALESCE(NULLIF(user_id, ''), 'default') = ? LIMIT 1", (label, owner),
        ).fetchone()
        if row:
            cur.execute("UPDATE semantic SET evidence_count = COALESCE(evidence_count, 1) + ?, "
                        "last_seen = MAX(COALESCE(last_seen, 0), ?), evidence = ? WHERE id = ?",
                        (1 if quote else 0, float(ts_value), add_evidence(row[1], quote), row[0]))
            return False
        cur.execute(
            "INSERT INTO semantic (user_id, fact, tags, confidence, created_at, evidence_count, last_seen, evidence) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?)",
            (owner, label, f"semantic,user_fact,{ptype}", 0.8, float(ts_value), float(ts_value),
             add_evidence(None, quote)),
        )
        return True
    except Exception:
        log.debug("profile_extractor: semantic promotion failed", exc_info=True)
        return False


def _evidence(raw: str, match: "re.Match[str] | None", width: int = 120) -> str:
    """The user's own words around a match, for appending to a canned label.

    Every rule below emits a FIXED sentence, which is why hundreds of turns
    collapsed into ten rows: "User prefers in-depth, meticulous, thorough
    responses." is byte-identical whether the user wrote 'be thorough' or three
    paragraphs about how they work, so `_insert_user_pattern`'s dedupe folds them
    all into one. The label is still worth keeping — it is what the persona and
    proactive surfaces read — so the quote is appended rather than replacing it.
    Two different statements now produce two different rows, both traceable to
    what was actually said.
    """
    if match is None:
        return ""
    start = max(0, match.start() - width // 3)
    end = min(len(raw), match.end() + width)
    quote = raw[start:end].strip()
    # Snap to word boundaries so the quote does not start or end mid-word.
    if start > 0 and " " in quote:
        quote = quote.split(" ", 1)[1]
    if end < len(raw) and " " in quote:
        quote = quote.rsplit(" ", 1)[0]
    quote = " ".join(quote.split())
    if len(quote) < 8:
        return ""
    return f' Said: "{quote}"'


def _pref(out: list, raw: str, low: str, pattern: str, ptype: str, label: str) -> None:
    """Emit a preference rule with the user's own phrasing attached."""
    m = re.search(pattern, low)
    if m:
        out.append((ptype, label + _evidence(raw, m)))


def extract_patterns_from_text(text: Any) -> list[tuple[str, str]]:
    # the user's own words only: what they paste is someone else's (ChatGPT's scorecard was quoted
    # back as something the user "Said")
    from eli.cognition.correction_patterns import split_pasted
    raw = _clean(split_pasted(str(text or ""))[0], 1600)
    low = raw.lower()
    out: list[tuple[str, str]] = []

    if not raw:
        return out

    # Identity: explicit declarations only. Broad grammar fragments such as
    # "it is ..." or "or ..." are not identity evidence.
    identity_facts = extract_explicit_identity_facts(raw)
    if identity_facts.get("name"):
        out.append(("identity.name", f"User's name is {identity_facts['name']}."))
    if identity_facts.get("preferred_name"):
        out.append(("identity.preferred_name", f"User prefers to be called {identity_facts['preferred_name']}."))
    if identity_facts.get("nickname"):
        out.append(("identity.nickname", f"User uses {identity_facts['nickname']} as a nickname."))

    # Communication / collaboration preferences.
    _pref(out, raw, low, r"\bin depth\b|\bin-depth\b|\bdetailed\b|\bmeticulous\b|\bthorough\b", "preference.detail",
          "User prefers in-depth, meticulous, thorough responses.")

    # Each of these is its own facet of how the user wants answers, not a value of the one answer
    # style they chose (preference.style, single-valued, set by the onboarding interview): written
    # there, a passing "that was generic" replaced the style the user had picked.
    _pref(out, raw, low, r"\bno vague descriptions\b|\bno vague\b|\bnot vague\b", "preference.concreteness",
          "User dislikes vague descriptions and wants concrete detail.")

    _pref(out, raw, low, r"\bno bias\b|\bwithout bias\b|\bno bullshit\b|\bbullshit-free\b", "preference.directness",
          "User prefers direct, low-bias, bullshit-free analysis.")

    _pref(out, raw, low, r"\bbrutally honest\b|\bchallenge\b|\bcorrect me\b|\btell me.*wrong\b", "preference.challenge",
          "User wants assumptions challenged and errors corrected directly.")

    _pref(out, raw, low, r"\bgeneric\b|\brepetitive\b|\bshallow\b|\bunhelpful\b|\bfiller\b|\bhr[- ]?speak\b|\bcustomer[- ]?service\b", "preference.originality",
          "User rejects generic, repetitive, shallow, customer-service style responses.")

    _pref(out, raw, low, r"\bstubs?\b|\btemplates?\b|\bplaceholder\b|\bboilerplate\b", "preference.output_quality",
          "User rejects stubs, templates, placeholders, and boilerplate as generated output.")

    _pref(out, raw, low, r"\bmore depth\b|\bdeeper\b|\bcharacter\b|\bfull persona\b|\bpersonality\b", "preference.persona",
          "User wants ELI to keep a deeper, more characterful persona while staying technically grounded.")

    _pref(out, raw, low, r"\bfull runtime audit\b|\bfull audit\b|\bdiagnostic\b|\bwhat'?s actually broken\b|\bwhat has changed\b", "preference.debugging",
          "User prefers full diagnostics/audits with explicit broken/missing components.")

    _pref(out, raw, low, r"\bevery step\b|\bstep by step\b", "preference.process",
          "User prefers step-by-step technical explanations.")

    _pref(out, raw, low, r"\bcommands\b|\bbash\b|\bsed\b|\bterminal\b", "preference.commands",
          "User prefers executable terminal/Bash commands for repairs.")

    _pref(out, raw, low, r"\bdrop[- ]?in python\b", "preference.commands",
          "User does not want vague drop-in Python snippets; prefers complete command workflows.")

    # The hard-coded keyword -> canned-phrase "project facts" are gone. They emitted frozen
    # sentences ("User is actively developing ELI...") every session, so the proactive
    # 'active_project' signal never changed. ELI's current work is inferred live from the
    # conversation (see _route_summary_to_profile, which writes a fresh 'project.current'
    # user_pattern from each session's LLM hand-off summary).

    # Research / technical-science interest (generic — no user-specific frameworks).
    if re.search(r"\bphysics\b|\bchemistry\b|\bbiology\b|\bengineering\b|\bsimulation\b|\bresearch\b|\bexperiment\b", raw, re.IGNORECASE):
        out.append(("research.science", "User works on technical/scientific research material."))

    # Biographical facts from explicit first-person statements (high precision —
    # anchored to "I am/I'm/I study/I work as" so casual chat isn't mis-extracted).
    # These enrich recall beyond response-preferences (identity, role, interests).
    _m = re.search(
        r"\bi(?:'m| am)\s+(?:a|an)\s+("
        r"physicist|engineer|inventor|researcher|scientist|developer|programmer|"
        r"mathematician|academic|professor|lecturer|phd\s*(?:student|candidate)?|"
        r"postdoc|student|founder|author|writer|designer|analyst)\b",
        low,
    )
    if _m:
        _role = _m.group(1).strip()
        _art = "an" if _role[:1].lower() in "aeiou" else "a"
        out.append(("identity.role", f"User is {_art} {_role}."))

    _m = re.search(r"\bi(?:'m| am)\s+(?:really |very |quite |particularly )?interested in\s+([a-z0-9][\w ,/&+'-]{2,70})", low)
    if _m:
        _v = _m.group(1).strip().rstrip(".,;")
        out.append(("interest.explicit", f"User is interested in {_v}."))

    _m = re.search(
        r"\b(?:i study|i'?m studying|i am studying|i research|i'?m researching|"
        r"i am researching|my field is|my research is in|i speciali[sz]e in|"
        r"i work in)\s+([a-z0-9][\w ,/&+'-]{2,70})",
        low,
    )
    if _m:
        _v = _m.group(1).strip().rstrip(".,;")
        out.append(("research.field", f"User studies/researches {_v}."))

    _m = re.search(r"\b(?:remember that|remember,? |please remember)\s+(i\b.{4,180}|my\b.{4,180})", low)
    if _m:
        _v = _m.group(1).strip().rstrip(".,;")
        out.append(("user.explicit_note", f"User asked to remember: {_v}."))

    # De-duplicate while preserving order.
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for item in out:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)

    return deduped


# How each of extract_patterns_from_text's facts came to be known (eli.cognition.belief's provenance
# weights). Its identity, role, interest, field and "remember that I ..." rules match only the user
# saying it of themselves outright; the rest are a keyword seen in passing. Every writer used to
# store "user_passing", so nothing ever outweighed anything and the weighing could never hold a value.
_STATED_OUTRIGHT = frozenset({
    "identity.name", "identity.preferred_name", "identity.nickname", "identity.role",
    "interest.explicit", "research.field", "user.explicit_note",
})


def provenance_of(pattern_type: str) -> str:
    """The provenance of a fact extract_patterns_from_text found."""
    return "user_explicit" if str(pattern_type or "").lower() in _STATED_OUTRIGHT else "observed"


def write_patterns_from_turn(
    user_text: Any,
    db_path: Path | None = None,
    ts_value: float | None = None,
    user_id: Any = None,
) -> int:
    """The facts in one message of the user's, stored as theirs (`user_id`, else the user of the turn
    running in this context) and as of when it was said (`ts_value`)."""
    ensure_profile_tables(db_path)

    db = db_path or _user_db()
    patterns = extract_patterns_from_text(user_text)
    question = _open_question()

    if not patterns and not question:
        return 0

    con = sqlite3.connect(str(db))
    cur = con.cursor()

    inserted = 0
    try:
        if question:
            _answer_open_conflicts(cur, str(user_text or ""), question, pattern_owner(user_id), ts_value)
        for ptype, pdata in patterns:
            if _insert_user_pattern(cur, ptype, pdata, ts_value=ts_value,
                                    provenance=provenance_of(ptype), user_id=user_id):
                inserted += 1
        con.commit()
    finally:
        con.close()
    return inserted


def _open_question() -> str:
    """The question ELI's last reply in this conversation left open, if any (pending_proposal)."""
    try:
        from eli.runtime.pending_proposal import get_follow_up
        return str((get_follow_up() or {}).get("question") or "")
    except (OSError, ValueError, ImportError, AttributeError, TypeError) as failure:
        log.debug("open question unreadable: %s", failure)
        return ""


def _answer_open_conflicts(cur: sqlite3.Cursor, user_text: str, question: str, who: str,
                           said_at: float | None) -> list:
    """The user's answer to the question ELI asked about a dispute settles it in the claims spine
    (claims.answer_conflicts), and the profile takes the value that stands: one fact, decided once."""
    try:
        from eli.memory import claims as _claims
        subject = _claims.user_subject(who)
        settled = _claims.answer_conflicts(cur, user_text, question, subject=subject)
    except (sqlite3.Error, ImportError, ValueError, TypeError) as failure:
        log.debug("conflict answer not read: %s", failure)
        return []
    for relation in settled:
        row = cur.execute("SELECT value FROM memory_claims WHERE subject = ? AND relation = ? AND status = 'current' "
                          "ORDER BY id DESC LIMIT 1", (subject, relation)).fetchone()
        if not row or not _table_exists(cur, "user_patterns"):
            continue
        kept = str(row[0])
        try:
            has = cur.execute("SELECT 1 FROM user_patterns WHERE lower(COALESCE(pattern_type,'')) = lower(?) "
                              "AND lower(COALESCE(pattern_data,'')) = lower(?) AND COALESCE(user_id, '') = ?",
                              (relation, kept, who)).fetchone()
            cur.execute("DELETE FROM user_patterns WHERE lower(COALESCE(pattern_type,'')) = lower(?) "
                        "AND lower(COALESCE(pattern_data,'')) <> lower(?) AND COALESCE(user_id, '') = ?",
                        (relation, kept, who))
            when = float(said_at or time.time())
            if has:
                cur.execute("UPDATE user_patterns SET provenance = 'user_explicit', timestamp = ?, ts = ? "
                            "WHERE lower(COALESCE(pattern_type,'')) = lower(?) AND COALESCE(user_id, '') = ?",
                            (when, when, relation, who))
            else:
                cur.execute("INSERT INTO user_patterns(pattern_type, pattern_data, timestamp, ts, corroboration, "
                            "provenance, user_id) VALUES (?, ?, ?, ?, 1, 'user_explicit', ?)",
                            (relation, kept, when, when, who or None))
        except sqlite3.Error as failure:
            log.debug("profile not brought in line with the settled claim: %s", failure)
    return settled


def backfill_user_patterns(db_path: Path | None = None, limit: int = 2500) -> dict[str, Any]:
    """Run the per-turn extractor over what was said before it existed, once.

    Each user turn and each user/identity memory is replayed one time ever: `pattern_backfill_log`
    keeps how far each source has been read (oldest first, `limit` rows per call, so a long history
    is covered over a few starts), and the live path covers everything said since. It used to replay
    the OLDEST 2500 rows on every start, which set reaffirmed facts' times back to the replayed
    turn's and let old statements, weighed as if said that minute, reset the name, role or style.
    A replayed statement keeps its own time and its own user, so it can neither outweigh nor
    overwrite what was said after it."""
    ensure_profile_tables(db_path)

    db = db_path or _user_db()
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS pattern_backfill_log (source TEXT PRIMARY KEY, upto REAL)")

    def _upto(source: str) -> float:
        row = cur.execute("SELECT upto FROM pattern_backfill_log WHERE source = ?", (source,)).fetchone()
        return float(row[0] or 0.0) if row else 0.0

    def _mark(source: str, upto: float) -> None:
        cur.execute("INSERT INTO pattern_backfill_log(source, upto) VALUES (?, ?) "
                    "ON CONFLICT(source) DO UPDATE SET upto = MAX(upto, excluded.upto)", (source, upto))

    scanned = 0
    inserted = 0

    def _replay(rows: list, source: str, text_of, keep=lambda r: True) -> None:
        nonlocal scanned, inserted
        upto = _upto(source)
        for r in rows:
            scanned += 1
            said_at = float(r["at"] or 0.0) or time.time()
            if not keep(r):          # read past, never as the user's words
                upto = max(upto, float(r["at"] or 0.0))
                continue
            for ptype, pdata in extract_patterns_from_text(text_of(r)):
                if _insert_user_pattern(cur, ptype, pdata, ts_value=said_at, provenance=provenance_of(ptype),
                                        user_id=str(r["who"] or "")):
                    inserted += 1
            upto = max(upto, float(r["at"] or 0.0))
        if rows:
            _mark(source, upto)

    if _table_exists(cur, "conversation_turns"):
        rows = cur.execute(
            """
            SELECT content, user_id AS who, COALESCE(ts, timestamp, 0) AS at
            FROM conversation_turns
            WHERE lower(COALESCE(role, '')) = 'user' AND COALESCE(ts, timestamp, 0) > ?
            ORDER BY COALESCE(ts, timestamp, 0) ASC
            LIMIT ?
            """,
            (_upto("conversation_turns"), int(limit)),
        ).fetchall()
        _replay(rows, "conversation_turns", lambda r: r["content"])

    if _table_exists(cur, "memories"):
        cols = {r[1] for r in cur.execute("PRAGMA table_info(memories)")}
        who = "user_id" if "user_id" in cols else "NULL"
        kind = "kind" if "kind" in cols else "''"
        rows = cur.execute(
            f"""
            SELECT text, value, content, {who} AS who, COALESCE(ts, timestamp, 0) AS at,
                   COALESCE(source, '') AS source, COALESCE(tags, '') AS tags, {kind} AS kind
            FROM memories
            WHERE (lower(COALESCE(source, '')) = 'user'
               OR lower(COALESCE(tags, '')) LIKE '%identity%'
               OR lower(COALESCE(tags, '')) LIKE '%user%')
              AND COALESCE(ts, timestamp, 0) > ?
            ORDER BY COALESCE(ts, timestamp, 0) ASC
            LIMIT ?
            """,
            (_upto("memories"), int(limit)),
        ).fetchall()
        # Only the user's own words are facts about the user: a row whose tags merely mention "user"
        # (ELI's reflection telemetry, "User correction/challenge signals: 1 recent events") was read as
        # if the user had said it, and became a claim about them. Who wrote a row is the memory
        # policy's one rule (classify_origin).
        from eli.memory.policy import ORIGIN_USER, classify_origin

        def _users_own(r) -> bool:
            text = r["text"] or r["value"] or r["content"] or ""
            return classify_origin(r["source"] or "", r["kind"] or "memory", r["tags"] or "", text) == ORIGIN_USER
        _replay(rows, "memories", lambda r: r["text"] or r["value"] or r["content"] or "", keep=_users_own)

    con.commit()

    total = cur.execute("SELECT COUNT(*) FROM user_patterns").fetchone()[0]
    con.close()

    return {
        "db": str(db),
        "scanned": scanned,
        "inserted": inserted,
        "user_patterns_total": total,
    }


_SUMMARY_SECTION_RE = re.compile(
    r"^\s*(SUMMARY|DECISIONS|OPEN\s+THREADS|USER\s+PREFERENCES|CURRENT\s+WORK|USER\s+FACTS)\s*:\s*"
    r"([\s\S]*?)(?=^\s*(?:SUMMARY|DECISIONS|OPEN\s+THREADS|USER\s+PREFERENCES|CURRENT\s+WORK|USER\s+FACTS)\s*:|\Z)",
    re.I | re.M,
)

# A durable fact line -> the pattern type it becomes. First match wins, so specific verbs come
# before general ones. Every prefix here is one _promote_to_semantic accepts, so a fact captured
# tonight is in the semantic tier and the knowledge graph by morning.
#
# A summary line is the model's account of the conversation, not the user's own words, so it never
# lands in a single-valued key (_route_facts_to_patterns): the user's name and role come from what
# they say of themselves (extract_patterns_from_text). The name and work routes take the user as the
# subject ("the user is called ..."): typed by "is called" / "is a" alone, "their cat is called X"
# and "the user is a keen cyclist" replaced the user's stored name and role.
_USER_AS_SUBJECT = r"\b(?:the\s+)?user(?:'s|’s)?\s+"
_FACT_ROUTES: tuple[tuple[str, str], ...] = (
    (_USER_AS_SUBJECT + r"(?:(?:full\s+|preferred\s+)?name\s+is|is\s+called|goes\s+by|prefers\s+to\s+be\s+called)\b",
     "identity.name_mention"),
    (_USER_AS_SUBJECT + r"(?:works\s+as|is\s+employed\s+as|role\s+is|job\s+(?:title\s+)?is)\b", "identity.work"),
    (r"\b(?:lives in|based in|is from|located in|timezone)\b", "identity.location"),
    (r"\b(?:studies|researches|research(?:es|ing)?|specialis|specializ|"
     r"phd|thesis|dissertation|field is)\b", "research.field"),
    (r"\b(?:is (?:building|developing|working on)|project|prototype|"
     r"is writing|is designing)\b", "project.named"),
    (r"\b(?:prefers|wants|likes it when|dislikes|hates|expects|always asks)\b",
     "preference.stated"),
    (r"\b(?:is interested in|cares about|enjoys|is a fan of|follows)\b", "interest.explicit"),
)

# A "fact" that is really ELI talking about itself is not a fact about the user.
# Recall already filters ELI's own telemetry at read time; keeping it out of
# user_patterns stops it reaching the semantic tier and the KG in the first place.
_FACT_REJECT = re.compile(
    r"\b(?:eli|the assistant|the model|the system)\b\s+(?:is|was|has|had|will|can|"
    r"logged|recorded|stored|generated|failed|reported)\b|"
    r"\b(?:reflection|telemetry|failure count|token|gpu layers|vram|"
    r"session summary|conversation volume)\b",
    re.I,
)


# About ELI itself or this conversation with it: not a fact about the user.
_ABOUT_THIS_SOFTWARE = re.compile(
    r"\beli(?:'s|’s)?\b|\bthis (?:assistant|software|session|conversation)\b|\bthe assistant's\b", re.I)
_QUOTED = re.compile(r"\"[^\"]{12,}\"|“[^”]{12,}”")
_WORK_STOP = frozenset("""the a an and or of to in on for with from by about into over is are was were be been
their they user users working work currently actively specifically technical reasons behind""".split())


def _said_by_user(claim: str, user_text: str, *, statements: bool = False) -> bool:
    """Whether the user's own words carry this claim. Text they quote back ("...") is ELI's:
    a user who pasted ELI's offer of "a deep dive into why the vector search is drifting" and
    said yes had that stored as their project. With `statements`, what they asked carries
    nothing: "what should I call a sourdough starter?" says nothing of keeping one. A word is
    carried in any of its forms ("learns" by "learning": route_contracts._same_word)."""
    from eli.execution.route_contracts import _same_word
    own = _QUOTED.sub(" ", str(user_text or ""))
    if statements:
        from eli.runtime.command_splitter import sentences
        own = " ".join(x for x in sentences(own) if not x.rstrip().endswith("?"))
    said = set(re.findall(r"[a-z]{3,}", own.lower()))
    words = {w for w in re.findall(r"[a-z]{4,}", str(claim or "").lower()) if w not in _WORK_STOP}
    if len(words) < 3:
        return True
    return sum(1 for w in words if any(_same_word(w, o) for o in said)) >= (len(words) + 1) // 2


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# A project stored from a session that was really about fixing the assistant.
_TROUBLESHOOTING = re.compile(
    r"\b(?:resolv\w+ (?:a |the )?(?:conflict|issue|problem|bug)|debugg\w+|troubleshoot\w+|missing \w+ logs?|"
    r"persistent storage|correcting the|fix(?:ing)? (?:the|its|his|her|their) (?:memory|name|identity|logs?|dates?))\b", re.I)


def purge_software_talk(db_path: Path | None = None) -> int:
    """Take out of the stored profile what is about ELI rather than about the user.

    Before the summariser was told otherwise, a session spent troubleshooting ELI was stored as
    the user's project ("Analyzing the technical reasons behind vector search drift") and as
    facts about them ("in a period of high frustration with ELI's temporal awareness"). Those
    rows feed the user model, the profile and so every prompt: two days later a small model
    answered a remark about a presentation with a lecture on vector decay.

    A project is kept only if the user's own words around that time carry it. Sentences about
    ELI are cut from the other rows; a row left empty is deleted. Returns how many rows changed."""
    db = db_path or _user_db()
    changed = 0
    try:
        con = sqlite3.connect(str(db), timeout=5.0)
    except Exception:
        return 0
    try:
        cur = con.cursor()
        if not _table_exists(cur, "user_patterns"):
            return 0
        rows = cur.execute(
            "SELECT id, pattern_type, COALESCE(pattern_data, ''), COALESCE(ts, timestamp, 0) FROM user_patterns "
            "WHERE pattern_type LIKE 'project%' OR pattern_type LIKE 'identity.%' OR pattern_type LIKE 'research%'").fetchall()
        for row_id, ptype, data, ts in rows:
            keep = str(data)
            if str(ptype).startswith("project"):
                said = ""
                if _table_exists(cur, "conversation_turns") and ts:
                    said = " ".join(r[0] or "" for r in cur.execute(
                        "SELECT content FROM conversation_turns WHERE lower(role)='user' AND "
                        "COALESCE(ts, timestamp, 0) BETWEEN ? AND ?", (float(ts) - 86400, float(ts) + 3600)))
                if (_ABOUT_THIS_SOFTWARE.search(keep) or _TROUBLESHOOTING.search(keep)
                        or (said and not _said_by_user(keep, said))):
                    keep = ""
            else:
                keep = " ".join(x for x in _SENTENCE_END.split(keep) if x and not _ABOUT_THIS_SOFTWARE.search(x)).strip()
            if keep == str(data):
                continue
            if keep:
                cur.execute("UPDATE user_patterns SET pattern_data=? WHERE id=?", (keep, row_id))
            else:
                cur.execute("DELETE FROM user_patterns WHERE id=?", (row_id,))
            changed += 1
            log.debug("profile_extractor: removed talk about ELI from profile row %s (%s)", row_id, ptype)
        con.commit()
    except Exception:
        log.debug("profile purge failed", exc_info=True)
    finally:
        con.close()
    if changed:
        # what is built from those rows is rebuilt now, not at the next lucky refresh
        try:
            from eli.runtime.user_model import refresh_user_model_brief
            refresh_user_model_brief(db_path=db_path)
        except Exception:
            log.debug("user model refresh after purge skipped", exc_info=True)
        try:
            from eli.kernel.state import update_user_profile
            con = sqlite3.connect(str(db), timeout=5.0)
            try:
                left = [r[0] for r in con.execute(
                    "SELECT pattern_data FROM user_patterns WHERE pattern_type LIKE 'project%' ORDER BY id DESC LIMIT 4")]
            finally:
                con.close()
            update_user_profile({"active_projects": left})
        except Exception:
            log.debug("profile refresh after purge skipped", exc_info=True)
    return changed


def _fact_pattern_type(line: str) -> str:
    """Classify a durable user fact into a promotable pattern type.

    Defaults to `identity.fact` rather than dropping the line: an unclassified
    durable fact is still worth remembering, and `identity.` is a prefix
    _promote_to_semantic accepts. It is deliberately NOT one of the
    _SINGLE_VALUED_PATTERNS, so facts accumulate instead of overwriting.
    """
    low = (line or "").lower()
    for pattern, ptype in _FACT_ROUTES:
        if re.search(pattern, low):
            return ptype
    return "identity.fact"


def _route_facts_to_patterns(cur: "sqlite3.Cursor", facts_text: str, user_id: Any = None,
                             user_text: str = "") -> int:
    """Write each durable fact the model found as its own accumulating pattern.

    This is the width of the funnel. The regex extractor upstream recognises about
    twenty fixed phrasings and most of them emit a CANNED sentence — the same
    string whatever the user actually said — so on a live machine 619 turns and
    441 memories produced only 10 user_patterns, 6 semantic facts and 27 KG
    entities. Everything downstream inherits that.

    The session summariser already reads the whole transcript; it just had nowhere
    to put anything except two single-slot rows (`project.current`,
    `preference.session`) that are deleted and rewritten every session. Facts go
    to accumulating types instead, and `_insert_user_pattern` dedupes them and
    refreshes recency on reaffirmation, so a fact repeated across sessions stays
    fresh rather than duplicating.

    A line that says several things ("Lives in Fenwick Bay. Has a sister named Maren. Works days at
    the bakery.") is each of its sentences, each its own fact, weighed on its own against what the
    user said (_insert_user_pattern), so one that no longer holds cannot ride on the ones that do
    (_facts_of: a sentence that points back to the one before is part of that fact).
    With `user_text` (what the user typed in the session summarised), a fact the claims cannot weigh
    (it states no relation they read) that the user's own words do not carry is not kept
    (_said_by_user): the summary also reads ELI's replies, and what ELI said of the user is not a fact
    about them unless the user said it too. One the claims read is weighed against what the user
    said instead, whatever words it is put in.

    Returns the number of facts written.
    """
    if not _summary_section_meaningful(facts_text):
        return 0
    written = 0
    seen: set[str] = set()
    for raw in (facts_text or "").splitlines():
        for part in _facts_of(str(raw or "").strip().lstrip("-•*0123456789. ")):
            line = _clean(part, 300).strip().lstrip("-•*0123456789. ").strip()
            # Too short to be a fact, or no letters at all.
            if len(line) < 12 or not re.search(r"[A-Za-z]", line):
                continue
            if _FACT_REJECT.search(line) or _ABOUT_THIS_SOFTWARE.search(line):
                log.debug("profile_extractor: dropped self-referential 'fact': %s", line[:80])
                continue
            if user_text and not _states_a_claim(line) and not _said_by_user(line, user_text, statements=True):
                log.debug("profile_extractor: a 'fact' the user's words do not carry: %s", line[:80])
                continue
            key = line.lower()
            if key in seen:
                continue
            seen.add(key)
            ptype = _fact_pattern_type(line)
            if ptype in _SINGLE_VALUED_PATTERNS:
                ptype = "identity.fact"
            try:
                # the model's reading of the conversation: weighed against what the user said
                if _insert_user_pattern(cur, ptype, line, provenance="inferred", user_id=user_id):
                    written += 1
            except Exception:
                log.debug("profile_extractor: fact insert failed", exc_info=True)
    return written


# A sentence that points back to the one before it says nothing on its own: "Likes podcasts about the
# sea. Bakes while listening to them." is one fact, not "Bakes while listening to them."
_POINTS_BACK = re.compile(r"\b(?:them|it|its|there|these|those)\b", re.I)


def _facts_of(line: str) -> list[str]:
    """The facts a line of the summary states: its sentences (command_splitter.sentences), a sentence
    that points back joined to the one it points to."""
    from eli.runtime.command_splitter import sentences
    out: list[str] = []
    for part in sentences(line):
        if out and _POINTS_BACK.search(part):
            out[-1] = f"{out[-1]} {part}"
        else:
            out.append(part)
    return out


def _states_a_claim(line: str) -> bool:
    """Whether the claims read a relation in a line written about the user (claims.extract_about)."""
    try:
        from eli.memory.claims import extract_about
        return bool(extract_about(line))
    except ImportError:
        return False


# A section the summary had nothing to put in says so in many wordings ("None specified", "No specific
# preferences were mentioned", "Nothing new this session"): it opens with a word of absence and every
# other word is the heading's own, a word of reporting, or carries no content. A list of whole phrases
# missed each new wording, and "None specified" was kept as what the user prefers.
_ABSENCE = frozenset({"none", "nothing", "nil", "no", "not", "n", "na"})
_REPORTING = frozenset({
    "specified", "identified", "expressed", "made", "mentioned", "stated", "noted", "given", "provided",
    "discussed", "recorded", "known", "found", "raised", "shared", "applicable", "available", "there",
    "significant", "particular", "specific", "explicit", "clear", "new", "current", "this", "that",
    "session", "conversation", "chat", "here", "yet", "so", "far", "during", "identifiable",
})


def _heading_words() -> frozenset:
    """The words of the summary's own section names, singular and plural (_SUMMARY_SECTION_RE)."""
    words = set()
    for name in re.findall(r"[A-Z]+(?:\\s\+[A-Z]+)?", _SUMMARY_SECTION_RE.pattern.split(")")[0]):
        for word in re.findall(r"[A-Z]+", name.replace("\\s", " ")):
            word = word.lower()
            words.update({word, word.rstrip("s"), word.rstrip("s") + "s"})
    return frozenset(words)


def _summary_section_meaningful(s: str) -> bool:
    from eli.cognition.scoring import STOPWORDS
    words = re.findall(r"[a-z]+", (s or "").lower())
    if not words:
        return False
    if words[0] not in _ABSENCE:
        return True
    empty = _ABSENCE | _REPORTING | STOPWORDS | _heading_words()
    return any(w not in empty for w in words[1:])


def _route_summary_to_profile(cur: "sqlite3.Cursor", llm_summary: str, user_text: str = "",
                              user_id: Any = None) -> None:
    """Route the DYNAMIC 'CURRENT WORK' / 'USER PREFERENCES' the model inferred from the
    REAL conversation into fresh user_patterns — the replacement for the removed hard-coded
    project facts. Re-derived every session, so the proactive 'active_project' signal tracks
    what the user is ACTUALLY working on now (not a frozen 'developing ELI' literal)."""
    if not llm_summary:
        return
    # the readings kept so far, weighed against what the user said before new ones join them
    retire_unfounded_readings(cur, reweigh=True)
    sections = {m.group(1).upper().replace(" ", "_"): m.group(2).strip()
                for m in _SUMMARY_SECTION_RE.finditer(llm_summary)}
    work = _clean(sections.get("CURRENT_WORK", ""), 300)
    prefs = _clean(sections.get("USER_PREFERENCES", ""), 300)
    # A session spent troubleshooting ELI is not the user's work. Stored as their project, ELI's
    # own invented diagnosis ("vector search drift ... embedding model decay") came back in every
    # prompt as a recalled topic and was repeated as fact.
    if _ABOUT_THIS_SOFTWARE.search(work) or (user_text and not _said_by_user(work, user_text)):
        log.debug("profile_extractor: current work is about ELI or not in the user's words, not stored: %s",
                  work[:80])
        work = ""
    if _summary_section_meaningful(work):
        # Keep only the LATEST dynamic project signal — purge the frozen canned ones
        # (incl. legacy 'project.eli*' rows) so the daemon never reads a stale fact.
        who = pattern_owner(user_id)
        try:
            cur.execute(
                "DELETE FROM user_patterns "
                "WHERE (pattern_type = 'project.current' OR pattern_type LIKE 'project.eli%') "
                "AND COALESCE(user_id, '') = ?", (who,)
            )
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        _close_claims(cur, "project.current", who)
        _insert_user_pattern(cur, "project.current", work, provenance="inferred", user_id=who)
    if _summary_section_meaningful(prefs):
        who = pattern_owner(user_id)
        try:
            cur.execute("DELETE FROM user_patterns WHERE pattern_type = 'preference.session' "
                        "AND COALESCE(user_id, '') = ?", (who,))
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        _close_claims(cur, "preference.session", who)
        _insert_user_pattern(cur, "preference.session", prefs, provenance="inferred", user_id=who)

    # Durable facts accumulate. CURRENT_WORK and USER_PREFERENCES above are both
    # single-slot by design — they answer "what now?" and are rewritten each
    # session — which is exactly why they could never widen the funnel.
    facts = sections.get("USER_FACTS", "")
    if facts:
        n = _route_facts_to_patterns(cur, facts, user_id=user_id, user_text=user_text)
        if n:
            log.debug("profile_extractor: %d durable user fact(s) captured", n)


def _close_claims(cur: "sqlite3.Cursor", relation: str, who: str) -> None:
    """A single-slot row the profile replaces closes its claim with it: the session's preference was
    deleted and written again each session while every one of its claims went on standing."""
    try:
        from eli.memory import claims as _claims
        _claims.retire(cur, relation, subject=_claims.user_subject(who))
    except (ImportError, sqlite3.Error) as failure:
        log.debug("profile_extractor: %s claims left standing: %s", relation, failure)


def _build_transcript(rows: list[Any], max_chars: int = 6000) -> str:
    """Render conversation_turns rows (chronological) into a compact transcript.
    Keeps the most recent tail when over budget — the end of a session carries
    the most continuity value."""
    lines: list[str] = []
    for r in rows:
        role = "User" if str(r["role"]).lower() == "user" else "ELI"
        txt = _clean(r["content"], 400)
        if txt:
            lines.append(f"{role}: {txt}")
    transcript = "\n".join(lines)
    if len(transcript) > max_chars:
        transcript = "…\n" + transcript[-max_chars:]
    return transcript


def _sync_summary_to_memory_store(headline: str, source: str) -> None:
    """Mirror a session summary headline into memories (FTS + vector index)."""
    text = _clean(headline, 600)
    if len(text) < 20:
        return
    try:
        from eli.memory.memory import get_memory
        mem = get_memory()
        if mem is None:
            return
        mem.store_memory(
            text,
            tags=["session_summary", "continuity", source],
            source="session_consolidation",
            kind="reflection",
            importance=0.72,
        )
    except Exception:
        log.debug("profile_extractor: memory sync for session summary failed", exc_info=True)


def _llm_summarise_session(
    transcript: str,
    broker: Any = None,
    *,
    background: bool = False,
) -> str:
    """In-depth, 100%-local session summary via the already-loaded GGUF (no
    network). Returns "" on ANY failure so the caller falls back to the
    heuristic topic summary — this must never block or break shutdown."""
    if not transcript.strip():
        return ""
    try:
        # Never COLD-LOAD a model just to summarise (e.g. closing the GUI without
        # ever loading one) — only summarise with an already-resident model.
        if broker is None:
            try:
                import eli.cognition.gguf_inference as _gi
                if not getattr(_gi, "is_loaded", lambda: False)():
                    return ""
            except Exception:
                return ""
            from eli.cognition.inference_broker import get_inference_broker
            broker = get_inference_broker()
        if broker is None or not broker.gguf_ready:
            return ""
        system = (
            "You are writing a concise hand-off note about a FINISHED conversation "
            "between the user and ELI, for ELI to read at the start of the next "
            "session. Be concrete and factual. Do NOT invent anything that is not "
            "in the transcript. No preamble, no sign-off."
        )
        prompt = (
            "Summarise this conversation for continuity. Use exactly these "
            "sections; omit a section if it is empty. Keep each to 1-4 short "
            "bullets:\n"
            "SUMMARY: 2-3 sentences on what happened and what matters next. Something ELI said "
            "about the user is not a fact unless the user said it too.\n"
            "DECISIONS: concrete decisions that were made.\n"
            "OPEN THREADS: unfinished work or agreed next steps.\n"
            "USER PREFERENCES: how the user wants things done.\n"
            "CURRENT WORK: what the user is working on in their own life or projects. "
            "Troubleshooting or criticising ELI is not their work; write 'none' for it.\n"
            "USER FACTS: durable facts about the USER that would still be true "
            "next month — who they are, what they work on, what they care about. "
            "One per line, in your own words, drawn ONLY from what the USER said "
            "about themselves. Omit anything about ELI, this software, or this "
            "session's mechanics. If the user stated nothing durable, write "
            "'none'.\n\n"
            f"TRANSCRIPT:\n{transcript}"
        )
        # Shutdown may already be signalled — this deliberate call is exempt.
        import eli.cognition.gguf_inference as _gi_allow
        with _gi_allow.allow_one_call_during_shutdown():
            out = (broker.infer(
                prompt,
                system=system,
                max_tokens=420,
                temperature=0.3,
                background=background,
            ) or "").strip()
        # Reject degenerate output (a lone '-', whitespace, no letters).
        if len(out) < 20 or not re.search(r"[A-Za-z]", out):
            return ""
        return out
    except Exception:
        return ""


def write_llm_session_summary(
    db_path: Path | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    max_turns: int = 60,
    broker: Any = None,
    *,
    background: bool = False,
    checkpoint: bool = False,
) -> dict[str, Any]:
    """SESSION-END hand-off: generate an in-depth summary of the FULL session and
    UPSERT it into session_summaries (source='session_end'). 100% local — uses
    the loaded GGUF via the broker. Falls back to a heuristic topic summary when
    the broker isn't ready/offline or returns nothing usable. Idempotent: a
    second call for the same session replaces the prior end-of-session row.

    Called at shutdown, so it sees the whole conversation."""
    ensure_profile_tables(db_path)
    db = db_path or _user_db()
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    try:
        if not _table_exists(cur, "conversation_turns"):
            return {"inserted": False, "reason": "conversation_turns missing"}

        where, params = "", []
        if session_id:
            where = "WHERE session_id = ?"
            params.append(session_id)
        rows = cur.execute(
            f"""
            SELECT session_id, user_id, role, content, ts, timestamp
            FROM conversation_turns
            {where}
            ORDER BY COALESCE(ts, timestamp, 0) DESC
            LIMIT ?
            """,
            (*params, int(max_turns)),
        ).fetchall()
        if not rows:
            return {"inserted": False, "reason": "no turns"}

        rows = list(reversed(rows))
        sid = str(session_id or rows[-1]["session_id"] or "unknown")
        uid = str(user_id or rows[-1]["user_id"] or "unknown")
        started = min(float(r["ts"] or r["timestamp"] or time.time()) for r in rows)
        ended = max(float(r["ts"] or r["timestamp"] or time.time()) for r in rows)
        now = time.time()

        transcript = _build_transcript(rows)
        llm_summary = _llm_summarise_session(transcript, broker, background=background)
        if llm_summary:
            # First line (the SUMMARY:) is the short headline; full sectioned
            # text goes in `content` for deep recall.
            _head = llm_summary.splitlines()[0]
            _head = re.sub(r"^\s*SUMMARY:\s*", "", _head, flags=re.I).strip()
            summary = _clean(_head or llm_summary, 600)
            content = llm_summary
            source = "session_checkpoint" if checkpoint else "session_end"
            # Route the dynamically-inferred CURRENT WORK / USER PREFERENCES into fresh
            # user_patterns so the proactive 'active_project' signal is live, not canned.
            try:
                _route_summary_to_profile(
                    cur, llm_summary,
                    user_text="\n".join(str(r["content"] or "") for r in rows if str(r["role"]).lower() == "user"),
                    user_id=uid if uid != "unknown" else None)
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        else:
            user_msgs = [_clean(r["content"], 220) for r in rows
                         if str(r["role"]).lower() == "user"]
            pattern_counts: dict[str, int] = {}
            for msg in user_msgs:
                for ptype, _pd in extract_patterns_from_text(msg):
                    pattern_counts[ptype] = pattern_counts.get(ptype, 0) + 1
            topics = sorted(pattern_counts, key=pattern_counts.get, reverse=True)[:8]
            if topics:
                summary = f"Session {sid}: {len(rows)} turns. Topics: {', '.join(topics)}."
            else:
                summary = (f"Session {sid}: {len(rows)} turns. "
                           f"Recent: {'; '.join(user_msgs[:4])}")
            content = summary
            source = (
                "session_checkpoint_heuristic" if checkpoint else "session_end_heuristic"
            )

        # UPSERT — replace prior row for this session + source class.
        if checkpoint:
            cur.execute(
                "DELETE FROM session_summaries WHERE session_id = ? "
                "AND source IN ('session_checkpoint', 'session_checkpoint_heuristic')",
                (sid,),
            )
        else:
            cur.execute(
                "DELETE FROM session_summaries WHERE session_id = ? "
                "AND source IN ('session_end', 'session_end_heuristic')",
                (sid,),
            )
        cur.execute(
            """
            INSERT INTO session_summaries(
                session_id, user_id, summary, content, turns_count,
                started_at, ended_at, source, timestamp, ts
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (sid, uid, summary, content, len(rows), started, ended, source, now, now),
        )
        con.commit()
        # Consolidate the freshly-routed user_patterns + this session summary into the
        # continuous User Model (one row per user_id). Reuses the resident GGUF broker and
        # degrades to a heuristic dossier on any failure — never blocks the summary write.
        try:
            from eli.runtime.user_model import synthesize_user_model
            synthesize_user_model(user_id=uid, session_summary=str(content or ""),
                                  db_path=db, broker=broker)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        if llm_summary or source.endswith("_heuristic"):
            _sync_summary_to_memory_store(summary, source)
        return {
            "inserted": True,
            "session_id": sid,
            "source": source,
            "turns_count": len(rows),
            "llm": bool(llm_summary),
            "summary": summary,
        }
    finally:
        con.close()


def maybe_rolling_session_consolidation(
    session_id: str | None = None,
    user_id: str | None = None,
    *,
    turn_count: int = 0,
    session_depth: float = 0.0,
    min_depth: float = 0.25,
    min_turns: int = 4,
    every_n_turns: int = 4,
) -> dict[str, Any]:
    """Mid-session rolling hand-off when engagement depth crosses the casual threshold.

    Runs in the background so chat stays responsive. Routes USER FACTS / CURRENT WORK /
    preferences into user_patterns + semantic tier; mirrors headline to memories FTS."""
    if session_depth < min_depth or turn_count < min_turns:
        return {"inserted": False, "reason": "below_depth_threshold"}
    if turn_count % every_n_turns != 0:
        return {"inserted": False, "reason": "not_checkpoint_turn"}
    return write_llm_session_summary(
        session_id=session_id,
        user_id=user_id,
        background=True,
        checkpoint=True,
    )


def _mark_backfilled(db: Path, session_id: str) -> None:
    """Record that a session has been mined, so a resumed run skips it."""
    try:
        con = sqlite3.connect(str(db))
        try:
            con.execute(
                "INSERT OR REPLACE INTO fact_backfill_log (session_id, ts) VALUES (?, ?)",
                (str(session_id), time.time()),
            )
            con.commit()
        finally:
            con.close()
    except Exception:
        log.debug("could not mark session %s backfilled", session_id, exc_info=True)


def backfill_facts_from_sessions(
    db_path: Path | None = None,
    limit: int | None = None,
    broker: Any = None,
    progress: Any = None,
) -> dict[str, Any]:
    """Mine durable user facts out of conversations that predate fact extraction.

    The live path only runs at session end, so everything said before it existed
    stays unmined. On a real install that is 64 sessions and 353 user turns
    producing **10** `user_patterns` — and because promotion fans out from that
    table, the semantic tier (6 rows) and the knowledge graph inherit the same
    starvation.

    The 59 summaries already on disk cannot simply be re-routed: they were
    written by the older prompt and contain no USER FACTS section at all. The
    sessions have to be re-read.

    So this walks them through the SAME path that works live —
    `write_llm_session_summary`, which builds the transcript, asks the local
    model, and routes the result through `_route_summary_to_profile`. No second
    extractor to keep in step with the first.

    Four properties matter more here than the extraction does:

      * **Never cold-loads.** Same rule `_llm_summarise_session` enforces: if no
        model is resident this returns immediately rather than pulling gigabytes
        off disk to mine history. Mining is never worth a cold load.
      * **Resumable.** Measured against a real 64-session store, the summariser
        makes TWO inference calls per session, so a full history is closer to an
        hour than the half hour a single-call estimate suggests. That cannot be a
        startup task or a modal dialog. Every session considered is written to
        `fact_backfill_log`, so an interrupted run continues instead of
        restarting.
      * **Idempotent.** `_insert_user_pattern` dedupes and refreshes recency on
        reaffirmation, so a repeat pass costs time and changes nothing. That is
        what makes interrupting it safe.
      * **Oldest first.** If it is stopped halfway you have mined the history you
        would otherwise never revisit; recent sessions are covered by the live
        path anyway.

    `progress` is an optional callable(done, total, session_id) for a UI.
    Returns a summary; `reason` explains any early return.
    """
    ensure_profile_tables(db_path)
    db = db_path or _user_db()
    out: dict[str, Any] = {
        "sessions_seen": 0, "sessions_processed": 0, "skipped_done": 0,
        "skipped_thin": 0, "failed": 0, "patterns_before": 0,
        "patterns_after": 0, "reason": "",
    }

    # Gate BEFORE touching anything: a cold load to mine history is never worth it.
    if broker is None:
        try:
            import eli.cognition.gguf_inference as _gi
            if not getattr(_gi, "is_loaded", lambda: False)():
                out["reason"] = "no model resident — load one, then run this again"
                return out
        except Exception:
            out["reason"] = "inference unavailable"
            return out

    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    try:
        if not _table_exists(cur, "conversation_turns"):
            out["reason"] = "conversation_turns missing"
            return out
        out["patterns_before"] = cur.execute(
            "SELECT COUNT(*) FROM user_patterns").fetchone()[0]

        # Oldest first, and only sessions with enough turns to carry a fact.
        rows = cur.execute(
            """
            SELECT session_id, COUNT(*) AS n, MIN(COALESCE(timestamp, ts, 0)) AS first_ts
              FROM conversation_turns
             WHERE COALESCE(session_id, '') <> ''
             GROUP BY session_id
             ORDER BY first_ts ASC
            """
        ).fetchall()

        # An explicit log, not an inference. "Does the stored summary contain a USER FACTS section"
        # doesn't work: write_llm_session_summary persists a processed summary, not the raw
        # sectioned output, so the test never matched and every run re-summarised the whole history.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS fact_backfill_log (
                session_id TEXT PRIMARY KEY,
                ts REAL
            )""")
        con.commit()
        done: set = set()
        try:
            done = {str(r[0]) for r in
                    cur.execute("SELECT session_id FROM fact_backfill_log").fetchall()
                    if r[0]}
        except Exception:
            log.debug("could not read the backfill log", exc_info=True)
    finally:
        con.close()

    out["sessions_seen"] = len(rows)
    todo = [r for r in rows if str(r["session_id"]) not in done]
    out["skipped_done"] = len(rows) - len(todo)
    if limit:
        todo = todo[: int(limit)]

    for i, r in enumerate(todo, 1):
        sid = str(r["session_id"])
        thin = int(r["n"] or 0) < 4        # too short to carry a durable fact
        if not thin:
            try:
                write_llm_session_summary(db_path=db, session_id=sid, broker=broker,
                                          max_turns=60)
                out["sessions_processed"] += 1
            except Exception:
                out["failed"] += 1
                log.debug("backfill: session %s failed", sid, exc_info=True)
        else:
            out["skipped_thin"] += 1

        # Mark it either way: a session too thin to carry a fact is still one
        # this pass has considered, and re-reading it on every future run costs
        # time to reach the same conclusion.
        _mark_backfilled(db, sid)

        # Reported for EVERY session, skips included — a progress bar that never
        # reaches its total because thin sessions were passed over silently reads
        # as a hung job.
        if callable(progress):
            try:
                progress(i, len(todo), sid)
            except Exception:
                log.debug("suppressed exception", exc_info=True)

    con = sqlite3.connect(str(db))
    try:
        out["patterns_after"] = con.execute(
            "SELECT COUNT(*) FROM user_patterns").fetchone()[0]
    finally:
        con.close()
    out["patterns_added"] = out["patterns_after"] - out["patterns_before"]
    log.info("backfill: %d/%d sessions mined, %d new user_patterns",
             out["sessions_processed"], out["sessions_seen"], out["patterns_added"])
    return out


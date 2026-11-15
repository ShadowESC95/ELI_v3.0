"""
eli/world/books.py
──────────────────
ELI's records as books, for the rooms of its house to keep on their shelves.

A body (ELI VR) shelves books in three rooms. The Memory Archive keeps ELI's conversations, the
summaries of its sessions, the stretches of its log where something it did failed and what it did
next, its weeks, and the stories it wrote from them (eli/world/stories.py). ELI's own room keeps
what it likes and dislikes, what it believes and how that changed, the things it made of its own
accord, how it looks back, its goals with how far they have come, and its diary. The Evidence Wall
keeps a case file for every lesson it learned. Every word in a book is the record's own; a book
only gathers a record under a title and cuts it into sections. The few words a book adds of its own
(its headings, "turns", "failed") are in WORDS.

    listed()          every book of the user's, as a header: id, room, of, title, spine, about, from,
                      to, shelf, length (the characters of its text), importance, made_by, and for a
                      story by, fiction and drawn_from, for a lesson applies_to, its record and
                      change (what it says to do, whole)
    sections(ids)     the sections [{heading, when, text}] of those books, read together
    read(id)          one book whole (its header and its sections); None for a book that is not there
                      or not this user's

A book's id is book_<of>_<key>: the key is the record's own (a lesson's row, a stance's row), or a
digest of it where the record's id is not one to send (a session id). Nothing here is cached; the
world feed reads it as often as it reads the rest of ELI's records (eli/world/shown.py).
"""
from __future__ import annotations

import hashlib
import math
import time
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from eli.utils.log import get_logger

log = get_logger(__name__)

MEMORY_ARCHIVE, OWN_ROOM, EVIDENCE_WALL = "memory_archive", "own_room", "evidence_wall"
# Where each kind of book stands, and who made the record it is of.
ROOM_OF = {"conversation": MEMORY_ARCHIVE, "session": MEMORY_ARCHIVE, "log": MEMORY_ARCHIVE,
           "period": MEMORY_ARCHIVE, "story": MEMORY_ARCHIVE,
           "likes": OWN_ROOM, "dislikes": OWN_ROOM, "beliefs": OWN_ROOM, "thoughts": OWN_ROOM,
           "reflection": OWN_ROOM, "goals": OWN_ROOM, "diary": OWN_ROOM,
           "lesson": EVIDENCE_WALL}
MADE_BY = {MEMORY_ARCHIVE: "memory", OWN_ROOM: "eli", EVIDENCE_WALL: "lessons"}
# The kinds of ELI's own room, in the order its shelves stand (ELI VR's own_room.gd keeps the same).
OWN_SHELVES = ("likes", "dislikes", "beliefs", "thoughts", "reflection", "goals", "diary")
# A book's title, its cover line and its spine are as long as the feed's own names and meanings
# (building.NAME_MAX, MEANING_MAX) allow; the spine is what a shelf can carry down a book's back.
TITLE_MAX, SPINE_MAX = 60, 24
# The thoughts that are ELI's own doing, kept in its room (a note goes in its diary).
OWN_THOUGHTS = ("act",)
# The words a book adds of its own: headings and the lines that say what a book is.
WORDS = {"you": "You", "eli": "ELI", "turn": "turn", "failed": "failed", "tried_instead": "tried instead of",
         "action": "action", "conversation_n": "conversation", "week": "The week of", "held": "held since",
         "changed": "changed", "because": "because", "argued": "argued", "sure": "sure",
         "what_for": "What it is for", "done_when": "Done when", "steps": "Steps", "decided": "Decided",
         "done": "Done", "open": "Still open", "when": "When", "seen": "What was seen", "why": "Why",
         "change": "What to do", "expect": "What should follow", "record": "How it has gone", "checks": "checks",
         "helped": "helped", "harmed": "harmed", "retired": "retired", "note": "A note",
         "journal": "the journal entry", "conversation": "the conversation of", "insight": "An insight"}


# ── small pieces ─────────────────────────────────────────────────────────────────────────────────

def _plain(text: Any) -> str:
    return " ".join(str(text or "").split())


def _cut(text: Any, limit: int) -> str:
    """Text up to `limit` characters, cut at a word where it is longer, with "…" to say so."""
    text = _plain(text)
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0] or text[:limit - 1]
    return cut.rstrip(" ,;:.") + "…"


def _first_sentence(text: Any) -> str:
    text = _plain(text)
    for end in (". ", "? ", "! ", "\n"):
        at = text.find(end)
        if 0 < at:
            text = text[:at + 1]
    return text


def _count(n: int, word: str) -> str:
    """"1 turn", "2 turns"."""
    return f"{n} {word}{'' if n == 1 else 's'}"


def _digest(value: Any) -> str:
    return hashlib.sha1(str(value).encode("utf-8")).hexdigest()[:12]


def _clock(ts: Any) -> str:
    try:
        return time.strftime("%H:%M", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _day(ts: Any) -> str:
    try:
        return time.strftime("%a %d %b %Y", time.localtime(float(ts)))
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _span_words(start: Any, end: Any) -> str:
    """"Mon 09 Nov 2026 14:02–15:10": when a stretch of time was, in the words a heading carries."""
    day, first, last = _day(start), _clock(start), _clock(end)
    if not day:
        return ""
    if _day(end) and _day(end) != day:
        return f"{day} {first} – {_day(end)} {last}"
    return f"{day} {first}" + (f"–{last}" if last and last != first else "")


def _owner() -> str:
    try:
        from eli.kernel.state import get_active_user_id
        return str(get_active_user_id() or "")
    except Exception:  # noqa: BLE001 - no active user: the owner's records
        return ""


def _book(of: str, key: Any, *, title: Any, about: Any = "", start: Any = None, end: Any = None,
          spine: Any = "", importance: float = 0.5, shelf: str = "", sections: Optional[List[Dict[str, Any]]] = None,
          length: Optional[int] = None, **extra: Any) -> Dict[str, Any]:
    """One book's header, with its sections when the record already holds them (else read() reads them)."""
    title = _cut(title, TITLE_MAX) or _cut(about, TITLE_MAX)
    start = float(start) if start is not None else None
    end = float(end) if end is not None else start
    out: Dict[str, Any] = {
        "id": f"book_{of}_{key}", "of": of, "room": ROOM_OF[of], "made_by": MADE_BY[ROOM_OF[of]],
        "title": title, "spine": _cut(spine or title, SPINE_MAX), "about": _cut(about, 200),
        "from": start, "to": end, "importance": max(0.0, min(1.0, float(importance))),
    }
    if shelf:
        out["shelf"] = shelf
    out.update(extra)
    if sections is not None:
        out["sections"] = [s for s in sections if _plain(s.get("text"))]
        out["length"] = sum(len(str(s.get("text") or "")) + len(str(s.get("heading") or "")) for s in out["sections"])
    else:
        out["length"] = int(length or 0)
    return out


def _section(heading: Any, when: Any, text: Any) -> Dict[str, Any]:
    try:
        when = float(when) if when not in (None, "") else None
    except (TypeError, ValueError):
        when = None
    lines = [" ".join(line.split()) for line in str(text or "").splitlines()]
    return {"heading": _plain(heading), "when": when, "text": "\n".join(line for line in lines if line)}


# ── the Memory Archive ───────────────────────────────────────────────────────────────────────────

def _memory():
    from eli.memory.memory import get_memory
    return get_memory()


def _summaries() -> Dict[str, Dict[str, Any]]:
    """The session summaries of the user's sessions, by session id."""
    from eli.memory import policy
    rows = _memory().get_session_summaries(user_id=policy.owner_key() or None, limit=-1) or []
    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        sid = str(row.get("session_id") or "")
        if sid and _plain(row.get("summary")) and sid not in out:
            out[sid] = row
    return out


def _conversations(summaries: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for s in _memory().conversation_sessions() or []:
        summary = summaries.get(s["session_id"], {})
        title = _first_sentence(summary.get("summary")) if summary else ""
        out.append(_book("conversation", _digest(s["session_id"]), title=title or _span_words(s["started"], s["ended"]),
                         start=s["started"],
                         end=s["ended"], about=_count(s["turns"], WORDS["turn"]), length=s["chars"],
                         session=s["session_id"], turns=s["turns"]))
    return out


def _turn_sections(turns: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [_section(f"{WORDS['you'] if str(t.get('role')) == 'user' else WORDS['eli']}, {_clock(t.get('timestamp'))}",
                     t.get("timestamp"), t.get("content")) for t in turns]


def _conversation_sections(books: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """The turns of these conversations, read in one pass over the stretch of time they span, each
    kept only when it is the user's (policy.owner_key)."""
    wanted = {b["session"]: b for b in books if b.get("session")}
    if not wanted:
        return {}
    from eli.memory import policy
    key = policy.owner_key()
    since = min(float(b.get("from") or 0.0) for b in wanted.values())
    until = max(float(b.get("to") or 0.0) for b in wanted.values())
    out: Dict[str, List[Dict[str, Any]]] = {b["id"]: [] for b in wanted.values()}
    by_session: Dict[str, List[Dict[str, Any]]] = {}
    # limit -1: every turn of the stretch (SQLite's "no limit"), oldest first; `until` is exclusive
    for turn in _memory().get_recent_conversation(limit=-1, since=since, until=math.nextafter(until, math.inf)) or []:
        sid = str(turn.get("session_id") or "")
        if sid in wanted and policy.owner_key(turn.get("user_id")) == key:
            by_session.setdefault(sid, []).append(turn)
    for sid, turns in by_session.items():
        out[wanted[sid]["id"]] = _turn_sections(turns)
    return out


_runs_kept: Dict[str, Any] = {"newest": None, "owner": None, "runs": []}


def _runs() -> List[Dict[str, Any]]:
    """Every action ELI ran for the user that its ledger holds, oldest first, with how it came out and why
    it failed (action_outcomes.cause_of), each in the session it ran in. Read again only when the ledger
    has a newer execution than the last read saw."""
    from eli.runtime import action_outcomes
    from eli.runtime.evidence_ledger import executor_runs, recent_events
    from eli.runtime.evidence_ledger import _default_db_path as ledger_path
    owner = _owner()
    newest = [str(ledger_path())] + [r.get("id") for r in recent_events(event_type="executor_action", limit=1) or []]
    if _runs_kept["newest"] == newest and _runs_kept["owner"] == owner:
        return _runs_kept["runs"]
    out = []
    for row in executor_runs(since=0.0, limit=-1) or []:
        if str(row.get("user_id") or "") not in ("", owner) or row.get("outcome") not in ("ok", "failed", "no_effect"):
            continue
        payload = row.get("payload") or {}
        args = payload.get("args") if isinstance(payload.get("args"), dict) else {}
        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
        worked = row["outcome"] == "ok"
        cause = "" if worked else str(payload.get("cause") or action_outcomes.cause_of(result, row["action"], args) or "")
        out.append({"ts": float(row["ts"]), "action": row["action"], "worked": worked, "session": row["session_id"],
                    "said": _plain(row.get("content")), "cause": cause,
                    "instead_of": str(args.get("_alternative_of") or "")})
    _runs_kept.update({"newest": newest, "owner": owner, "runs": out})
    return out


def _logs(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A book for each session in which something ELI did failed: every action of that session in
    order, how each came out, why one failed and what ELI tried instead."""
    from eli.runtime.action_outcomes import cause_words, plain
    by_session: Dict[str, List[Dict[str, Any]]] = {}
    for run in runs:
        if run["session"]:
            by_session.setdefault(run["session"], []).append(run)
    out = []
    for sid, rows in by_session.items():
        failed = [r for r in rows if not r["worked"]]
        if not failed:
            continue
        sections = []
        for r in rows:
            text = [r["said"] or plain(r["action"])]
            if not r["worked"]:
                text.append(f"{WORDS['failed']}: {cause_words(r['cause'])}")
            if r["instead_of"]:
                text.append(f"{WORDS['tried_instead']} {plain(r['instead_of'])}")
            sections.append(_section(f"{plain(r['action'])}, {_clock(r['ts'])}", r["ts"], "\n".join(text)))
        out.append(_book("log", _digest(sid), title=failed[0]["said"] or plain(failed[0]["action"]),
                         about=f"{len(failed)} of {_count(len(rows), WORDS['action'])} {WORDS['failed']}",
                         start=rows[0]["ts"], end=rows[-1]["ts"], sections=sections,
                         importance=len(failed) / len(rows)))
    return out


def _periods(conversations: List[Dict[str, Any]], runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A book for each calendar week ELI talked or acted (epistemics.week_start), a section for each day
    of it: the conversations that day, and what ELI ran with how it came out."""
    from eli.cognition.epistemics import week_start
    from eli.runtime.action_outcomes import plain
    days: Dict[str, Dict[str, Any]] = {}
    for book in conversations:
        day = days.setdefault(time.strftime("%Y-%m-%d", time.localtime(float(book["from"] or 0.0))),
                              {"ts": float(book["from"] or 0.0), "lines": [], "ran": {}})
        titled = book["title"] and book["title"] != _span_words(book["from"], book["to"])
        day["lines"].append(f"{_clock(book['from'])}–{_clock(book['to'])} {book['about']}"
                            + (f": {book['title']}" if titled else ""))
    for run in runs:
        day = days.setdefault(time.strftime("%Y-%m-%d", time.localtime(run["ts"])),
                              {"ts": run["ts"], "lines": [], "ran": {}})
        tally = day["ran"].setdefault(run["action"], [0, 0])
        tally[0] += 1
        tally[1] += 0 if run["worked"] else 1
    weeks: Dict[float, List[Dict[str, Any]]] = {}
    for _key, day in sorted(days.items()):
        ran = [f"{plain(a)} ×{n}" + (f" ({bad} {WORDS['failed']})" if bad else "") for a, (n, bad) in day["ran"].items()]
        day["text"] = "\n".join(day["lines"] + ran)
        weeks.setdefault(week_start(day["ts"]), []).append(day)
    out = []
    for begun, in_week in weeks.items():
        talks = sum(len(d["lines"]) for d in in_week)
        acts = sum(n for d in in_week for n, _bad in d["ran"].values())
        out.append(_book("period", int(begun), title=f"{WORDS['week']} {time.strftime('%d %b %Y', time.localtime(begun))}",
                         about=f"{_count(talks, WORDS['conversation_n'])}, {_count(acts, WORDS['action'])}",
                         start=min(d["ts"] for d in in_week), end=max(d["ts"] for d in in_week),
                         sections=[_section(_day(d["ts"]), d["ts"], d["text"]) for d in in_week]))
    return out


def _sessions(summaries: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for sid, row in summaries.items():
        start, end = row.get("started_at") or row.get("ended_at"), row.get("ended_at") or row.get("started_at")
        turns = int(row.get("turns_count") or 0)
        out.append(_book("session", _digest(sid), title=_first_sentence(row.get("summary")),
                         about=_count(turns, WORDS["turn"]) if turns else "", start=start, end=end,
                         sections=[_section("", end, row.get("summary"))]))
    return out


def _stories() -> List[Dict[str, Any]]:
    from eli.world import stories
    out = []
    for story in stories.kept():
        out.append(_book("story", story["id"], title=story["title"], about=_first_sentence(story["text"]),
                         start=story.get("about_from") or story["written_at"],
                         end=story.get("about_to") or story["written_at"],
                         by=stories.by(story), fiction=bool(story.get("fiction")),
                         drawn_from=[str(d.get("words") or "") for d in story.get("drawn_from") or [] if d.get("words")],
                         sections=[_section("", story["written_at"], story["text"])]))
    return out


# ── ELI's own room ───────────────────────────────────────────────────────────────────────────────

def _shelf(of: str) -> str:
    """ELI's own name for one of its shelves, when it has given one (eli/world/stories.shelf_names)."""
    try:
        from eli.world import stories
        return str(stories.shelf_names().get(of) or "")
    except Exception:  # noqa: BLE001 - no name given: the shelf has no plate
        return ""


def _stances() -> List[Dict[str, Any]]:
    """A book for each position ELI holds now: a liking, a disliking (as its model judged the position,
    stance_store's valence) or a belief, with what it held before and why it changed."""
    out = []
    for row in _memory().stances() or []:
        if not row.get("current"):
            continue
        of = {1: "likes", -1: "dislikes"}.get(row.get("valence"), "beliefs")
        sections = [_section(f"{WORDS['held']} {_day(row.get('first_held'))}", row.get("first_held"), row["position"])]
        for rev in reversed(row.get("revisions") or []):
            text = f"{rev.get('old') or ''} → {rev.get('new') or ''}"
            if _plain(rev.get("reason")):
                text += f"\n{WORDS['because']} {_plain(rev['reason'])}"
            sections.append(_section(f"{WORDS['changed']} {_day(rev.get('ts'))}", rev.get("ts"), text))
        argued = int(row.get("corroboration") or 1)
        out.append(_book(of, int(row["id"]), title=row["position"], spine=row["topic"],
                         about=f"{WORDS['argued']} ×{argued}, {WORDS['sure']} {float(row.get('confidence') or 0.0):.0%}",
                         start=row.get("first_held"), end=row.get("last_held"), shelf=_shelf(of),
                         importance=float(row.get("confidence") or 0.0), sections=sections))
    return out


def _thought_books(kinds: Iterable[str], of: str) -> List[Dict[str, Any]]:
    from eli.cognition.thoughts import recent
    from eli.world.shown import thought_lines
    out = []
    for t in recent(-1, kinds=list(kinds), user_id=_owner()):
        chance = t.get("chance")
        out.append(_book(of, int(t["id"]), title=t.get("what"), about=t.get("decision") or t.get("kind"),
                         start=t.get("ts"), shelf=_shelf(of), importance=float(chance) if chance is not None else 0.5,
                         sections=[_section("", t.get("ts"), "\n".join(thought_lines(t)))]))
    return out


def _reflections() -> List[Dict[str, Any]]:
    out = []
    for row in _memory().get_recent_reflections(limit=-1) or []:
        out.append(_book("reflection", int(row["id"]), title=_first_sentence(row["text"]), start=row["timestamp"],
                         shelf=_shelf("reflection"), sections=[_section("", row["timestamp"], row["text"])]))
    try:
        from eli.planning.insight_synthesis import cached_insight_record
        insight = cached_insight_record(max_age_s=0)
    except Exception:  # noqa: BLE001 - no insight kept: no book of it
        insight = {}
    if _plain(insight.get("insight")):
        out.append(_book("reflection", f"insight{int(insight['ts'])}", title=_first_sentence(insight["insight"]),
                         about=WORDS["insight"], start=insight["ts"], shelf=_shelf("reflection"),
                         sections=[_section("", insight["ts"], insight["insight"])]))
    return out


def _goals() -> List[Dict[str, Any]]:
    """A book for each goal and task ELI holds or held: what it is for, what would finish it, the steps
    taken with what came of each, and for a task what was decided, done and is still open."""
    from eli.planning.goal_store import load_goals
    out = []
    for goal in load_goals():
        meta = dict(goal.metadata or {})
        work = dict(meta.get("work") or {})
        steps = [s for s in work.get("steps") or [] if isinstance(s, dict)]
        sections = []
        if _plain(goal.objective):
            sections.append(_section(WORDS["what_for"], goal.created_at, goal.objective))
        if goal.success_criteria:
            sections.append(_section(WORDS["done_when"], None, "\n".join(str(c) for c in goal.success_criteria)))
        if steps:
            sections.append(_section(WORDS["steps"], steps[-1].get("at"), "\n".join(
                f"{_clock(s.get('at'))} {s.get('action') or ''}: {s.get('outcome') or ''}"
                + (f" — {_plain(s.get('detail'))}" if _plain(s.get("detail")) else "") for s in steps)))
        for heading, key in (("decided", "decisions"), ("done", "steps_done"), ("open", "open_questions")):
            if meta.get(key):
                sections.append(_section(WORDS[heading], None, "\n".join(str(x) for x in meta[key])))
        done = sum(1 for s in steps if s.get("outcome") == "done")
        status = str(work.get("status") or goal.status or "")
        about = status + (f", {done} of {len(steps)} {WORDS['steps'].lower()} {WORDS['done'].lower()}" if steps else "")
        out.append(_book("goals", goal.goal_id.replace("_", "-"), title=goal.title, about=about, start=goal.created_at,
                         end=goal.updated_at, shelf=_shelf("goals"), importance=float(goal.priority or 0.0),
                         sections=sections, status=status, progress=(done / len(steps)) if steps else 0.0))
    return out


def _diary() -> List[Dict[str, Any]]:
    """A book for each day of ELI's diary: its world journal's entries that day, and the notes it wrote."""
    from eli.cognition.thoughts import recent
    from eli.world.persistence.journal import entries
    days: Dict[str, List[Tuple[float, Dict[str, Any]]]] = {}
    for entry in entries():
        days.setdefault(time.strftime("%Y-%m-%d", time.localtime(entry["when"])), []).append(
            (entry["when"], _section(entry["title"], entry["when"], entry["body"])))
    for note in recent(-1, kinds=["note"], user_id=_owner()):
        when = float(note.get("ts") or 0.0)
        days.setdefault(time.strftime("%Y-%m-%d", time.localtime(when)), []).append(
            (when, _section(WORDS["note"], when, note.get("what"))))
    out = []
    for key, items in days.items():
        items.sort(key=lambda pair: pair[0])
        out.append(_book("diary", key, title=_day(items[0][0]), spine=_day(items[0][0]),
                         about=items[0][1]["heading"], start=items[0][0], end=items[-1][0], shelf=_shelf("diary"),
                         sections=[section for _when, section in items]))
    return out


# ── the Evidence Wall ────────────────────────────────────────────────────────────────────────────

def _lessons() -> List[Dict[str, Any]]:
    """A case file for every lesson ELI learned (eli/runtime/lessons.py), with its whole record."""
    from eli.runtime import lessons
    from eli.runtime.action_outcomes import plain
    out = []
    for row in lessons.listed():
        checks, helped, harmed = int(row["checks"] or 0), int(row["helped"] or 0), int(row["harmed"] or 0)
        record = (f"{row['status']}, {checks} {WORDS['checks']}: {WORDS['helped']} {helped}, {WORDS['harmed']} {harmed}"
                  + (f"\n{WORDS['retired']}: {row['retired_reason']}" if _plain(row.get("retired_reason")) else ""))
        sections = [_section(WORDS["when"], row["created_at"], row["trigger_text"]),
                    _section(WORDS["seen"], None, "\n".join(row["evidence"])),
                    _section(WORDS["why"], None, row.get("hypothesis")),
                    _section(WORDS["change"], None, row["proposed_change"]),
                    _section(WORDS["expect"], None, row.get("predicted_outcome")),
                    _section(WORDS["record"], row.get("last_checked"), record)]
        out.append(_book("lesson", int(row["id"]), title=f"{plain(row['applies_to'])}: {row['proposed_change']}",
                         spine=plain(row["applies_to"]), about=record.split("\n")[0], start=row["created_at"],
                         end=row.get("last_checked") or row["created_at"], importance=(helped + 1.0) / (checks + 2.0),
                         sections=sections, applies_to=str(row["applies_to"]),
                         # what the lesson says to do, whole: the title is cut to a cover
                         change=_plain(row["proposed_change"]),
                         record={"status": row["status"], "checks": checks, "helped": helped, "harmed": harmed,
                                 "retired_reason": str(row.get("retired_reason") or "")}))
    return out


# ── the books, together ──────────────────────────────────────────────────────────────────────────

def _sources() -> List[Tuple[str, Callable[[], List[Dict[str, Any]]]]]:
    """Each source of books, by name; those that share a read (the summaries, the runs) share it."""
    shared: Dict[str, Any] = {}

    def summaries() -> Dict[str, Dict[str, Any]]:
        if "summaries" not in shared:
            shared["summaries"] = _summaries()
        return shared["summaries"]

    def conversations() -> List[Dict[str, Any]]:
        if "conversations" not in shared:
            shared["conversations"] = _conversations(summaries())
        return shared["conversations"]

    def runs() -> List[Dict[str, Any]]:
        if "runs" not in shared:
            shared["runs"] = _runs()
        return shared["runs"]

    return [("conversation", conversations), ("session", lambda: _sessions(summaries())),
            ("log", lambda: _logs(runs())), ("period", lambda: _periods(_safe(conversations), _safe(runs))),
            ("story", _stories), ("stances", _stances), ("thoughts", lambda: _thought_books(OWN_THOUGHTS, "thoughts")),
            ("reflection", _reflections), ("goals", _goals), ("diary", _diary), ("lesson", _lessons)]


def _safe(source: Callable[[], List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    try:
        return source()
    except Exception:  # noqa: BLE001 - a record that cannot be read gives no books of its own
        log.debug("a source of books was not read", exc_info=True)
        return []


def listed() -> List[Dict[str, Any]]:
    """Every book of the user's. A source that cannot be read gives none of its own, and says why in the log."""
    out: List[Dict[str, Any]] = []
    for name, source in _sources():
        try:
            out.extend(source())
        except Exception:  # noqa: BLE001
            log.debug("no %s books: the record was not read", name, exc_info=True)
    return out


def sections(books: Iterable[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """The sections of these books, by id: those a header already holds, and the turns of the
    conversations, read together."""
    books = list(books)
    out = {b["id"]: list(b["sections"]) for b in books if "sections" in b}
    talks = [b for b in books if "sections" not in b and b.get("of") == "conversation"]
    if talks:
        try:
            out.update(_conversation_sections(talks))
        except Exception:  # noqa: BLE001 - the turns unreadable: those books are read without them
            log.debug("the turns of the conversations were not read", exc_info=True)
    return out


def header(book: Dict[str, Any]) -> Dict[str, Any]:
    """A book without its sections or what only reading it needs: what goes on its cover and spine."""
    return {k: v for k, v in book.items() if k not in ("sections", "session", "turns")}


def read(book_id: Any) -> Optional[Dict[str, Any]]:
    """One book of the user's, whole: its header and its sections. None for a book that is not there."""
    ident = str(book_id or "")
    if not ident.startswith("book_"):
        return None
    of = ident[len("book_"):].split("_", 1)[0]
    if of not in ROOM_OF:
        return None
    names = {"likes": "stances", "dislikes": "stances", "beliefs": "stances"}
    for name, source in _sources():
        if name != names.get(of, of):
            continue
        for book in _safe(source):
            if book["id"] == ident:
                got = sections([book]).get(ident, [])
                return {**header(book), "sections": got}
    return None


__all__ = ["MEMORY_ARCHIVE", "OWN_ROOM", "EVIDENCE_WALL", "OWN_SHELVES", "ROOM_OF", "WORDS", "header", "listed",
           "read", "sections"]

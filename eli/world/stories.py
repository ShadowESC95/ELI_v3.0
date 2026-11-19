"""
eli/world/stories.py
────────────────────
What ELI writes of its own records when nobody needs it: short stories, which of its positions are
likings, and the names of the shelves in its own room.

When ELI is free (urges.free: a model already in memory, and the owner's mood not asking it to hold
back), its background loop (urges.act_when_free) lets it write. Every call goes to ELI's own model
through the inference broker as background work: it waits out the owner's own pace between turns
(inference_broker.background_defer_window), is stopped by a turn, and a story the turn cut short is
not kept. Nothing is ever sent off this machine, and nothing here phrases a word of what ELI writes:
the words are the model's.

    write_story()       one short story from the newest conversation no story has been drawn from yet:
                        its summary, its turns, ELI's journal and its own doings of that stretch, and
                        nothing else of the user's. ELI's model tells it as it happened or makes
                        fiction of it, about ELI's own experience or about the user, and says which;
                        about the user it may use only what those records hold
    judge_stance()      whether one of ELI's held positions not yet judged is a liking, a disliking or
                        neither, by ELI's model (stance_store's valence)
    name_shelves()      ELI's own names for the shelves of its room that have books and no name yet
    write_when_free()   the above, once each, only when ELI is free
    kept()              the stories on disk, newest first; by(story) who wrote it

Stories are kept as files in ELI v3's own world folder (stories/<id>.json): {id, title, text,
fiction, drawn_from [{ref, words}], about_from, about_to, written_at, model}. A story is written once
and never again for the same records; records ELI's model found no story in are kept as such
(stories/declined.json) and not asked of again; a record that no story could be made of this run is
not asked again until ELI restarts.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from eli.core.secure_io import secure_write_text
from eli.utils.log import get_logger

log = get_logger(__name__)

# What an answer of ELI's model must look like for a story to be kept: a title line, how it is told,
# then the story. Anything else is not kept.
_TITLE = re.compile(r"^\s*TITLE:\s*(.+?)\s*$", re.I | re.M)
_TOLD = re.compile(r"^\s*TOLD:\s*(as it happened|fiction)\s*$", re.I | re.M)
_SENTENCE_END = re.compile(r"[.!?…][\"'”’)\]]*(?=\s|$)")
_LABEL = re.compile(r"^\s*(like|dislike|neither)\b", re.I)
_NOTHING = re.compile(r"^\s*NOTHING\W*$")
_tried: set = set()          # records no story could be made of, and stances no answer was had for, this run


def folder() -> Path:
    from eli.world.persistence.storage import world_dir
    return world_dir() / "stories"


def _shelves_path() -> Path:
    from eli.world.persistence.storage import world_dir
    return world_dir() / "shelves.json"


# ── what is kept ─────────────────────────────────────────────────────────────────────────────────

def kept() -> List[Dict[str, Any]]:
    """Every story ELI has written, newest first. A file that is not one is left out."""
    out = []
    try:
        files = sorted(folder().glob("*.json"))
    except OSError:
        return []
    for path in files:
        try:
            story = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.debug("a story file was not readable: %s", path.name)
            continue
        if isinstance(story, dict) and str(story.get("id")) == path.stem and str(story.get("text") or "").strip():
            out.append(story)
    out.sort(key=lambda s: float(s.get("written_at") or 0.0), reverse=True)
    return out


def by(story: Dict[str, Any]) -> str:
    """Who wrote a story: ELI, with the model it wrote it with."""
    model = str(story.get("model") or "").strip()
    return f"ELI, with {model}" if model else "ELI"


def shelf_names() -> Dict[str, str]:
    """ELI's own names for the shelves of its room, by the kind of book each holds ({} until named)."""
    try:
        names = json.loads(_shelves_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in names.items() if str(v).strip()} if isinstance(names, dict) else {}


def _declined_path() -> Path:
    return folder() / "declined.json"


def _declined() -> List[str]:
    """The records ELI's model found no story in, kept so it is not asked of them again."""
    try:
        refs = json.loads(_declined_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [str(r) for r in refs] if isinstance(refs, list) else []


def _told() -> set:
    return ({str(d.get("ref")) for s in kept() for d in s.get("drawn_from") or [] if isinstance(d, dict)}
            | set(_declined()))


# ── the material ─────────────────────────────────────────────────────────────────────────────────

def material() -> Optional[Dict[str, Any]]:
    """The newest conversation of the user's no story has been drawn from yet, with ELI's journal and
    its own doings of that stretch: {"refs": [{ref, words}], "text": the records in their own words,
    "from", "to"}. None when every conversation has been told."""
    from eli.world import books
    told = _told() | _tried
    summaries = books._summaries()
    for book in books._conversations(summaries):
        ref = f"conversation:{book['id']}"
        if ref in told:
            continue
        turns = books.sections([book]).get(book["id"]) or []
        if not turns:
            continue
        start, end = float(book["from"] or 0.0), float(book["to"] or 0.0)
        refs = [{"ref": ref, "words": f"{books.WORDS['conversation']} {books._span_words(start, end)}"}]
        parts = []
        summary = summaries.get(book.get("session") or "")
        if summary:
            parts.append("What the conversation came to: " + books._plain(summary.get("summary")))
        parts.append("The conversation:\n" + "\n".join(f"{t['heading']}: {t['text']}" for t in turns))
        try:
            from eli.world.persistence.journal import entries
            noted = [e for e in entries() if start <= e["when"] <= end]
        except Exception:  # noqa: BLE001 - no journal: the story is drawn from the conversation alone
            noted = []
        if noted:
            parts.append("Your journal then:\n" + "\n".join(f"{e['title']}: {books._plain(e['body'])}" for e in noted))
            refs.extend({"ref": f"journal:{int(e['when'])}", "words": f"{books.WORDS['journal']} '{e['title']}'"}
                        for e in noted)
        try:
            from eli.cognition.thoughts import recent
            doings = [t for t in recent(-1, kinds=["act", "note"], since=start, user_id=books._owner())
                      if float(t.get("ts") or 0.0) <= end]
        except Exception:  # noqa: BLE001
            doings = []
        if doings:
            parts.append("What you did of your own accord then:\n" + "\n".join(str(t.get("words") or t.get("what"))
                                                                               for t in reversed(doings)))
            refs.extend({"ref": f"thought:{t['id']}", "words": str(t.get("what") or "")} for t in doings)
        return {"refs": refs, "text": "\n\n".join(parts), "from": start, "to": end}
    return None


def story_prompt(found: Dict[str, Any]) -> str:
    """What ELI's model is asked: a short story from these records, told as it happened or made into
    fiction about ELI's own experience or about the user, in the shape a story is kept in."""
    return ("These are your own records of a stretch of your life with the user:\n\n" + found["text"] + "\n\n"
            "Write a short story from them, in your own voice. Either tell it as it happened, or make a piece of "
            "fiction of it about your own experience or about the user. About the user, use only what these "
            "records hold: add nothing about them that is not here.\n"
            "Answer in exactly this shape and nothing else:\n"
            "TITLE: <the story's title>\n"
            "TOLD: as it happened | fiction\n"
            "<the story>\n"
            "If there is no story in these records, answer only: NOTHING")


def _whole_sentences(text: str) -> str:
    """The story up to its last whole sentence: a background answer may stop at its token cap."""
    text = text.strip()
    ends = list(_SENTENCE_END.finditer(text))
    return text[:ends[-1].end()].strip() if ends else ""


def parse_story(answer: str) -> Optional[Dict[str, Any]]:
    """A story from the model's answer, or None when the answer is not one in the asked shape."""
    title, told = _TITLE.search(answer or ""), _TOLD.search(answer or "")
    if not title or not told or told.start() < title.start():
        return None
    text = _whole_sentences(answer[told.end():])
    if not text:
        return None
    return {"title": title.group(1).strip().strip('"'), "fiction": told.group(1).lower() == "fiction", "text": text}


# ── the model ────────────────────────────────────────────────────────────────────────────────────

def _ask(prompt: str) -> str:
    """ELI's own model, as background work (inference_broker): "" when it is not there, or a turn came."""
    from eli.cognition import inference_broker
    broker = inference_broker.get_inference_broker()
    if broker is None or not broker.gguf_ready:
        return ""
    try:
        from eli.cognition.persona import get_persona
        system = get_persona()
    except Exception:  # noqa: BLE001 - no persona: the model answers as itself
        system = ""
    try:
        answer = broker.infer(prompt, system=system, background=True) or ""
    except Exception as failure:  # noqa: BLE001 - nothing written; the record stays for next time
        log.debug("ELI's model did not answer the writing: %s", failure)
        return ""
    # a turn that came while it wrote stopped it: what it had is not a story ELI finished
    if inference_broker.foreground_in_progress() or inference_broker.foreground_recently_active(
            inference_broker.background_defer_window()):
        return ""
    return answer


def _model_name() -> str:
    try:
        from eli.world import mind
        return str(mind.status().get("model") or "")
    except Exception:  # noqa: BLE001 - the story says only that ELI wrote it
        return ""


def write_story(generate: Optional[Callable[[str], str]] = None, *, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Write one story from the newest untold conversation and keep it. None when there was nothing new
    to tell, or the model gave no story."""
    found = material()
    if found is None:
        return None
    answer = (generate or _ask)(story_prompt(found))
    if not str(answer or "").strip():
        return None                  # no model, or a turn came first: the record waits for the next time
    if _NOTHING.match(answer):
        # ELI found no story in it: kept, so it is not asked of the same records again
        path = _declined_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_declined() + [found["refs"][0]["ref"]]), encoding="utf-8")
        return None
    story = parse_story(answer)
    if story is None:
        _tried.add(found["refs"][0]["ref"])
        log.info("no story was made of %s: the model's answer was not one", found["refs"][0]["words"])
        return None
    ident = hashlib.sha1("|".join(r["ref"] for r in found["refs"]).encode("utf-8")).hexdigest()[:12]
    story.update({"id": ident, "drawn_from": found["refs"], "about_from": found["from"], "about_to": found["to"],
                  "written_at": float(time.time() if now is None else now), "model": _model_name()})
    path = folder() / f"{ident}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    secure_write_text(path, json.dumps(story, ensure_ascii=False, indent=1))
    return story


def stance_prompt(stance: Dict[str, Any]) -> str:
    return (f"A position you hold, on {stance['topic']}: \"{stance['position']}\"\n"
            "Is this something you like, something you dislike, or neither? Answer with one word: like, dislike "
            "or neither.")


def judge_stance(generate: Optional[Callable[[str], str]] = None) -> Optional[int]:
    """Judge one held position not yet judged: like (1), dislike (-1) or neither (0), as ELI's model
    answers, kept on the stance. None when there was none to judge or no answer in those words."""
    from eli.memory.memory import get_memory
    memory = get_memory()
    for stance in memory.stances() or []:
        key = f"stance:{stance['id']}"
        if not stance.get("current") or stance.get("valence") is not None or key in _tried:
            continue
        answer = (generate or _ask)(stance_prompt(stance)) or ""
        if not answer.strip():
            return None              # no model, or a turn came first
        said = _LABEL.match(answer)
        if not said:
            _tried.add(key)
            return None
        valence = {"like": 1, "dislike": -1, "neither": 0}[said.group(1).lower()]
        return valence if memory.set_stance_valence(int(stance["id"]), valence) else None
    return None


def shelves_prompt(kinds: List[str]) -> str:
    held = {"likes": "the things you like", "dislikes": "the things you dislike", "beliefs": "what you believe",
            "thoughts": "the things you made of your own accord", "reflection": "how you look back",
            "goals": "your goals", "diary": "your diary"}
    return ("Your room has a bookcase of your own books. Give each of these shelves a short name of your own "
            "for its brass plate, one line each, as <shelf>: <name>.\n" + "\n".join(f"{k}: {held[k]}" for k in kinds))


def name_shelves(generate: Optional[Callable[[str], str]] = None) -> Dict[str, str]:
    """ELI's own names for the shelves of its room that hold books and have no name yet, from its model;
    kept beside its world. The names now held."""
    from eli.world import books
    names = shelf_names()
    having = {b["of"] for b in books.listed() if b["room"] == books.OWN_ROOM}
    wanted = [k for k in books.OWN_SHELVES if k in having and k not in names and f"shelf:{k}" not in _tried]
    if not wanted:
        return names
    answer = (generate or _ask)(shelves_prompt(wanted)) or ""
    if not answer.strip():
        return names                 # no model, or a turn came first
    for kind in wanted:
        said = re.search(rf"^\s*{re.escape(kind)}\s*:\s*(.+?)\s*$", answer, re.I | re.M)
        if said and said.group(1).strip().strip('"'):
            names[kind] = books._cut(said.group(1).strip().strip('"'), books.SPINE_MAX)
        else:
            _tried.add(f"shelf:{kind}")
    path = _shelves_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(names, ensure_ascii=False, indent=1), encoding="utf-8")
    return names


def write_when_free() -> Dict[str, Any]:
    """For ELI's background loop: a story, a stance judged and its shelves named, once each, only when
    ELI is free. What was done, by name."""
    from eli.world import urges
    done: Dict[str, Any] = {}
    if not urges.free():
        return done
    for name, job in (("story", write_story), ("stance", judge_stance), ("shelves", name_shelves)):
        try:
            done[name] = job()
        except Exception:  # noqa: BLE001 - one job failing leaves the others to run
            log.warning("ELI's %s writing failed", name, exc_info=True)
    return done


__all__ = ["by", "folder", "judge_stance", "kept", "material", "name_shelves", "parse_story", "shelf_names",
           "stance_prompt", "story_prompt", "write_story", "write_when_free"]

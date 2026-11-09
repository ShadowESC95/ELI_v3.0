"""Notes effector — LIST_NOTES, NEW_NOTE, SEARCH_NOTES, and the folder WRITE_NOTE writes into.

One place holds the person's notes: core.paths.notes_dir() (or ELI_NOTES_DIR when it is set), the
folder the document index also reads, so a note written one way is found every other way.
"""
from __future__ import annotations

import os
from pathlib import Path

from eli.utils.log import get_logger

log = get_logger(__name__)


def notes_folder() -> Path:
    """The folder the person's notes live in: ELI_NOTES_DIR when it is set, else core.paths.notes_dir().
    An unset variable read as Path("") is the current directory, and that was where notes went:
    README.md and every other markdown file there was listed and searched as a note."""
    override = (os.environ.get("ELI_NOTES_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    from eli.core.paths import notes_dir
    return Path(notes_dir())


def _h_list_notes(a, args):
    try:
        notes_dir = notes_folder()
        notes_dir.mkdir(parents=True, exist_ok=True)

        if a == "NEW_NOTE":
            import time as _nt
            title = (args.get("title") or args.get("text") or "").strip()
            content = (args.get("content") or args.get("text") or "").strip()
            fname = (title[:40].replace(" ", "_").replace("/", "_") or
                     _nt.strftime("note_%Y%m%d_%H%M%S")) + ".md"
            note_path = notes_dir / fname
            if note_path.exists():
                # A note of that name is there already: what is new goes under it, nothing is lost.
                with note_path.open("a", encoding="utf-8") as fh:
                    fh.write(f"\n{content}\n")
                msg = f"Added to the note {note_path}"
            else:
                note_path.write_text(f"# {title or fname}\n\n{content}\n", encoding="utf-8")
                msg = f"Note saved: {note_path}"
            return {"ok": True, "action": a, "path": str(note_path), "content": msg, "response": msg}

        if a == "LIST_NOTES":
            notes = sorted(notes_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not notes:
                msg = f"No notes found in {notes_dir}"
                return {"ok": True, "action": a, "notes": [], "content": msg, "response": msg}
            lines = [f"Notes ({len(notes)}):"]
            for n in notes[:30]:
                lines.append(f"  {n.name}")
            msg = "\n".join(lines)
            # Kept open, so "the second one" or "read the shopping one" opens that note.
            return {"ok": True, "action": a, "notes": [n.name for n in notes],
                    "content": msg, "response": msg,
                    "listed": [{"title": n.stem.replace("_", " "), "ref": str(n)} for n in notes[:30]],
                    "follow_up": {"action": "READ_FILE", "arg": "path", "field": "ref"}}

        if a == "SEARCH_NOTES":
            query = (args.get("query") or args.get("text") or "").strip().lower()
            if not query:
                return {"ok": False, "action": a, "error": "No query",
                        "content": "Provide a search query.", "response": "Provide a search query."}
            hits = []
            for note in notes_dir.glob("*.md"):
                text = note.read_text(encoding="utf-8", errors="replace").lower()
                if query in text:
                    hits.append(note.name)
            if not hits:
                msg = f"No notes matched '{query}'"
            else:
                msg = f"Found {len(hits)} note(s) matching '{query}':\n" + "\n".join(f"  {h}" for h in hits[:20])
            return {"ok": True, "action": a, "hits": hits, "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


def _h_write_note(a, args):
    """WRITE_NOTE ("write a note saying ..."): a line in today's notes file, in the same folder as every
    other note, so LIST_NOTES and SEARCH_NOTES find it. It wrote to a folder named relative to
    wherever ELI was started, shared with ELI v2, which nothing ever read back."""
    from datetime import datetime
    txt = str((args or {}).get("text") or "").rstrip()
    if not txt:
        return {"ok": False, "action": "WRITE_NOTE", "content": "Missing note text."}
    folder = notes_folder()
    folder.mkdir(parents=True, exist_ok=True)
    daily = folder / f"{datetime.now().strftime('%Y-%m-%d')}.md"
    first = not daily.exists() or not daily.read_text(encoding="utf-8", errors="ignore").strip()
    with daily.open("a", encoding="utf-8") as fh:
        fh.write(f"{'' if first else chr(10)}- [{datetime.now().strftime('%H:%M:%S')}] {txt}\n")
    return {"ok": True, "action": "WRITE_NOTE", "path": str(daily), "content": f"Wrote note to {daily}"}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "LIST_NOTES": _h_list_notes,
    "NEW_NOTE": _h_list_notes,
    "SEARCH_NOTES": _h_list_notes,
    "WRITE_NOTE": _h_write_note,
}.items():
    _register(_action, _fn)

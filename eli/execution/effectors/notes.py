"""Notes effector — LIST_NOTES, NEW_NOTE, SEARCH_NOTES (config-dir note files)."""
from __future__ import annotations

import os

from eli.utils.log import get_logger

log = get_logger(__name__)


def _h_list_notes(a, args):
    try:
        from pathlib import Path as _P
        from eli.core.paths import config_dir as _cd
        notes_dir = _P(os.environ.get("ELI_NOTES_DIR", "")) or (_cd() / "notes")
        notes_dir.mkdir(parents=True, exist_ok=True)

        if a == "NEW_NOTE":
            import time as _nt
            title = (args.get("title") or args.get("text") or "").strip()
            content = (args.get("content") or args.get("text") or "").strip()
            fname = (title[:40].replace(" ", "_").replace("/", "_") or
                     _nt.strftime("note_%Y%m%d_%H%M%S")) + ".md"
            note_path = notes_dir / fname
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
            return {"ok": True, "action": a, "notes": [n.name for n in notes],
                    "content": msg, "response": msg}

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


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "LIST_NOTES": _h_list_notes,
    "NEW_NOTE": _h_list_notes,
    "SEARCH_NOTES": _h_list_notes,
}.items():
    _register(_action, _fn)

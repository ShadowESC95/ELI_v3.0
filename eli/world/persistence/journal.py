from __future__ import annotations
import re
import time
from pathlib import Path
from time import strftime
from typing import Any, Dict, List

# How an entry begins in the journal file (append_journal_entry writes it): "## <stamp> — <title>".
STAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_HEAD = re.compile(r"^## (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) — (.*)$", re.M)
_SOURCE = re.compile(r"^Source: `([^`]*)`\s*$", re.M)


def journal_path() -> Path:
    """Absolute path to the world journal.

    Was a module-level Path("artifacts/world/journal/...") — relative to the
    CURRENT WORKING DIRECTORY, so the journal landed in a different place
    depending on where ELI was launched from, and in a packaged build tried to
    write to /artifacts and failed. storage.world_dir() already resolves this
    correctly for every install layout.
    """
    from eli.world.persistence.storage import world_dir
    return world_dir() / "journal" / "eli_world_journal.md"


def append_journal_entry(title: str, body: str, source: str = "eli_world") -> None:
    JOURNAL_PATH = journal_path()
    JOURNAL_PATH.parent.mkdir(parents=True, exist_ok=True)
    stamp = strftime(STAMP_FORMAT)
    with JOURNAL_PATH.open("a", encoding="utf-8") as f:
        f.write(f"\n\n## {stamp} — {title}\n\n")
        f.write(f"Source: `{source}`\n\n")
        f.write(body.strip() + "\n")


def entries() -> List[Dict[str, Any]]:
    """Every entry in the journal, oldest first, as append_journal_entry wrote it: {"when" (unix time,
    local), "title", "source", "body"}. A journal that is not there has none."""
    try:
        text = journal_path().read_text(encoding="utf-8")
    except OSError:
        return []
    heads = list(_HEAD.finditer(text))
    out: List[Dict[str, Any]] = []
    for i, head in enumerate(heads):
        rest = text[head.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        source = _SOURCE.search(rest)
        body = (rest[source.end():] if source else rest).strip()
        try:
            when = time.mktime(time.strptime(head.group(1), STAMP_FORMAT))
        except ValueError:
            continue
        out.append({"when": when, "title": head.group(2).strip(), "source": source.group(1) if source else "",
                    "body": body})
    return out

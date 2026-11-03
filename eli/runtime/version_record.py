"""Which ELI this is, and which it was. Asked "notice any updates to your code?", ELI made up a
"status diff from the last 24 hours" with paths that do not exist. What it can truthfully say: the
version running now (pyproject.toml beside the package), when this install was first seen here,
the versions seen before it, and what its own last code examination found. Nothing else about its
code is known to it unless something examined it.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)
SEEN_FILE = "versions_seen.json"


def current_version() -> str:
    """The version in pyproject.toml beside the package; else the package's metadata; else ""."""
    root = Path(__file__).resolve().parents[2]
    for candidate in (root / "pyproject.toml", Path.cwd() / "pyproject.toml"):
        try:
            m = re.search(r'^version\s*=\s*"([^"]+)"', candidate.read_text(encoding="utf-8"), re.M)
            if m:
                return m.group(1)
        except OSError:
            continue
    try:
        from importlib.metadata import version
        return version("eli")
    except Exception:
        return os.environ.get("ELI_VERSION", "")


def _seen_path() -> Path:
    from eli.core.paths import data_dir
    return data_dir() / "runtime" / SEEN_FILE


def _read_seen() -> Dict[str, float]:
    try:
        data = json.loads(_seen_path().read_text(encoding="utf-8"))
        return {str(k): float(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def note_seen(version: Optional[str] = None, now: Optional[float] = None) -> Dict[str, float]:
    """Record that this version is running here, the first time it is."""
    version = version or current_version()
    seen = _read_seen()
    if version and version not in seen:
        seen[version] = float(now if now is not None else time.time())
        try:
            path = _seen_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(seen, indent=1), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            log.debug("the versions seen could not be written", exc_info=True)
    return seen


def history() -> List[Dict[str, Any]]:
    """Versions seen here, newest first: [{"version", "first_seen"}]."""
    seen = note_seen()
    return [{"version": v, "first_seen": t} for v, t in sorted(seen.items(), key=lambda kv: kv[1], reverse=True)]


def _last_examination() -> str:
    """What ELI's own last code examination found, in a line; "" when there was none."""
    try:
        from eli.core.paths import data_dir
        data = json.loads((data_dir() / "code_exam_last.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    when = data.get("ts") or data.get("timestamp") or data.get("at")
    stamp = time.strftime("%a %d %b %Y at %H:%M", time.localtime(float(when))) if when else "at some point"
    found = data.get("summary") or data.get("result") or data.get("verdict") or ""
    files = data.get("files") or data.get("count")
    bits = [f"my last look at my own code was {stamp}"]
    if files:
        bits.append(f"{files} files")
    if found:
        bits.append(str(found)[:160])
    return ", ".join(bits)


def told(now: Optional[float] = None) -> str:
    """The answer, in words, held to what is recorded."""
    now = float(now if now is not None else time.time())
    seen = history()
    version = current_version() or "an unknown version"
    if not seen:
        head = f"I'm ELI v3 {version}; I have no record of when this version first ran here."
    else:
        first = seen[0]
        since = time.strftime("%a %d %b %Y at %H:%M", time.localtime(first["first_seen"]))
        days = (now - first["first_seen"]) / 86400.0
        ago = "today" if days < 1 else f"{int(days)} day{'s' if int(days) != 1 else ''} ago"
        head = f"I'm ELI v3 {first['version']}, first seen here on {since} ({ago})."
        if len(seen) > 1:
            before = ", ".join(f"{s['version']} (from {time.strftime('%d %b', time.localtime(s['first_seen']))})"
                               for s in seen[1:4])
            head += f" Before that: {before}."
    exam = _last_examination()
    tail = (" I keep no diff of my own code between versions, so I can't say line by line what changed; "
            "the release notes on GitHub say what each version brought.")
    return head + (f" {exam[0].upper() + exam[1:]}." if exam else "") + tail

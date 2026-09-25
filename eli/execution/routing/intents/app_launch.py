"""`open`/`run`/`launch`/`start <thing>` — apps, sites and folders.

The single busiest guard in the tail: resolves a bare name against known
website aliases, known directories, then the desktop app list.

Extracted verbatim from ``router_enhanced.route`` (v3 modularisation). This is an
ordered stage: it returns a route dict when one of its guards matches, or None to
let the next stage try. Order within the file is the legacy order and is load
bearing — an earlier guard deliberately shadows later ones.
"""
from __future__ import annotations

import re

from eli.execution.routing._helpers import (
    WEBSITE_ALIASES,
    _known_dir_path,
    _mk,
)


def route_app_launch(text: str, raw: str, low: str, text_l: str):
    _open_m = re.match(r"^\s*(open|run|launch|start)\s+(.+?)\s*$", raw, re.I)
    if _open_m:
        target = re.sub(r"\s+app$", "", _open_m.group(2).strip(), flags=re.I)
        # An article is never part of the target. Without this, "open the
        # trash" missed the filesystem branch (which knows "trash") and fell
        # all the way through to OPEN_APP with the literal name "the trash".
        target = re.sub(r"^(?:a|an|the)\s+", "", target, flags=re.I).strip() or target
        target_low = target.lower()

        generic_ide = {
            "ide", "the ide", "editor", "the editor",
            "built in ide", "built-in ide", "gui ide", "eli ide",
            "internal ide", "ide tab", "the ide tab",
        }

        # Canonical table (eli/execution/app_aliases.py). This inline copy also mapped "chrome" and
        # "google chrome" to `chromium`, a different browser; the canonical table resolves against
        # what is installed.
        from eli.execution.app_aliases import APP_ALIASES as app_aliases

        def _fs_target(tok: str) -> str:
            """Path for one open-target when it denotes a location, else ""."""
            tok = tok.strip()
            if not tok:
                return ""
            if tok.startswith(("/", "~")):
                return tok
            if tok.lower() in {"trash", "/trash"}:
                return "trash"
            return _known_dir_path(tok)

        # "open home directory and the downloads directory": one verb, two locations. The old
        # single-path routes dropped everything after the first target and reported success. Split
        # only when every part is a real location, so "open steam and chill" is untouched.
        _parts = [p for p in re.split(r"\s+and\s+(?:then\s+)?", target, flags=re.I) if p.strip()]
        if len(_parts) > 1:
            _resolved = [_fs_target(p) for p in _parts]
            if all(_resolved):
                return _mk(
                    "SEQUENCE",
                    {"steps": [{"action": "OPEN_FILE_SYSTEM", "args": {"path": p}}
                               for p in _resolved]},
                    0.98,
                    matched_by="open.filesystem.multi_target",
                )

        _single = _fs_target(target)
        if (
            _single
            or re.search(r"\b(folder|directory|path)\b", target, re.I)
            or target_low in {"trash", "home", "home directory"}
        ):
            return _mk("OPEN_FILE_SYSTEM", {"path": _single or target}, 0.99,
                       matched_by="open.filesystem.literal_preempt")

        if target_low in generic_ide:
            return _mk("OPEN_IDE", {"name": "ide"}, 0.99, matched_by="open.ide.literal_preempt")

        if target_low in WEBSITE_ALIASES:
            return _mk(
                "OPEN_URL",
                {"url": WEBSITE_ALIASES[target_low]},
                0.99,
                matched_by="open.website.literal_preempt",
            )

        # "open a web browser on quantum field theory" was taken whole as an app name and offered
        # for install. It is a browser request with a search query (OPEN_BROWSER accepts one). The
        # detector is shared with portable_intent_contract so the two matchers can't drift apart.
        from eli.execution.portable_intent_contract import _browser_open_target
        _bq = _browser_open_target(target)
        if _bq is not None:
            return _mk("OPEN_BROWSER", ({"query": _bq} if _bq else {}), 0.98,
                       matched_by="open.browser.literal_preempt")

        canonical = app_aliases.get(target_low, target)
        return _mk("OPEN_APP", {"name": canonical, "target": canonical}, 0.99,
                   matched_by="open.app.literal_preempt")
    return None

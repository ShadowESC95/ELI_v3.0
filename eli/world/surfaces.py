"""Where a turn was said: the surface its session id names.

One map for every reader. ELI VR opens its sessions as "eli-vr-<time>" (ELI VR scripts/eli/eli_link.gd)
and the web page as "web-<id>"; the desktop app's own sessions name no surface. The period log
(eli/memory/unified_retrieval.format_period_log) said "You:" for a turn spoken in the headset, so a
question about a VR evening read like one at the desk.
"""
from __future__ import annotations

SURFACES = (("eli-vr-", "in VR"), ("web-", "on the web page"))


def where(session_id) -> str:
    """"in VR", "on the web page", or "" for the desktop app and a session that names no surface."""
    session = str(session_id or "")
    return next((name for prefix, name in SURFACES if session.startswith(prefix)), "")

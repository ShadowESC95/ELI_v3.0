"""Models for ELI's rooms: "import the model at ~/things/lamp.glb" (IMPORT_MODEL) and "fetch a model
of a pine tree" (FETCH_MODEL). A path ending .gltf or .glb is an import; "a model of X", "an X
model" or "get/fetch/download/bring in X for your room" is a fetch."""
from __future__ import annotations

import os
import re

from eli.execution.routing._helpers import _mk

_IMPORT_RX = re.compile(
    r"^\s*(?:please\s+)?(?:import|load|add|bring\s+in|use)\s+(?:the\s+|this\s+|that\s+)?(?:3d\s+)?(?:model|object|thing|file)?"
    r"\s*(?:at|from|in)?\s*[\"']?(?P<path>[^\"']+?\.(?:gltf|glb))[\"']?\s*(?:as\s+(?:a\s+|an\s+)?(?P<name>[\w ]{1,40}))?\s*$",
    re.I)
_FETCH_RX = re.compile(
    r"^\s*(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?(?:fetch|get|find|download|bring\s+in|grab)\s+(?:me\s+|us\s+)?"
    r"(?:a\s+|an\s+|the\s+|some\s+)?(?:(?:3d\s+)?models?\s+of\s+(?:a\s+|an\s+|the\s+|some\s+)?(?P<of>.+?)"
    r"|(?P<thing>.+?)\s+(?:3d\s+)?models?)"
    r"(?:\s+(?:for|into|in)\s+(?:your|my|the|eli'?s)\s+(?:room|rooms|world|house|vr|space))?\s*[.!]?\s*$",
    re.I)


def route_world_models(text: str, raw: str, low: str, text_l: str):
    m = _IMPORT_RX.match(raw or "")
    if m:
        path = os.path.abspath(os.path.expanduser(m.group("path").strip()))
        args = {"path": path}
        if m.group("name"):
            args["name"] = m.group("name").strip()
        return _mk("IMPORT_MODEL", args, 0.95, matched_by="world.import_model", entities={"path": path})
    m = _FETCH_RX.match(raw or "")
    if m:
        want = (m.group("of") or m.group("thing") or "").strip()
        if want and len(want.split()) <= 6:
            return _mk("FETCH_MODEL", {"query": want}, 0.9, matched_by="world.fetch_model", entities={"query": want})
    return None

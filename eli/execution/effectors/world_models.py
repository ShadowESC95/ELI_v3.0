"""Real things for ELI's rooms: a model imported from a file on this computer, or fetched by what
it is from Poly Haven's CC0 library (eli/world/models.py). ELI places them in its rooms as it does
the things its body came with."""
from __future__ import annotations

from eli.execution.effectors.registry import register as _register


def _h_import_model(a, args):
    """IMPORT_MODEL {path, name?}: a .gltf or .glb on this computer, with the files it names."""
    from eli.world import models
    path = str(args.get("path") or "").strip()
    if not path:
        msg = "Which file? Give me the path to a .gltf or .glb."
        return {"ok": False, "action": a, "error": "no path", "content": msg, "response": msg}
    try:
        got = models.import_file(path, name=str(args.get("name") or ""))
    except (OSError, ValueError) as exc:
        msg = f"I couldn't import {path}: {exc}"
        return {"ok": False, "action": a, "path": path, "error": str(exc), "content": msg, "response": msg}
    msg = f"Imported {got['name']} ({got['id']}). I can put it in my rooms now."
    return {"ok": True, "action": a, "path": path, "model": got["id"], "name": got["name"], "content": msg,
            "response": msg}


def _h_fetch_model(a, args):
    """FETCH_MODEL {query}: the library's best match for what is wanted, brought into the store."""
    from eli.world import models
    want = str(args.get("query") or args.get("want") or "").strip()
    if not want:
        msg = "What should I fetch a model of?"
        return {"ok": False, "action": a, "error": "no query", "content": msg, "response": msg}
    got = models.fetch(want)
    if not got.get("ok"):
        if got.get("offline"):
            msg = (f"I can't fetch a model of {want} right now: network access is off. Turn on the Net toggle "
                   "and ask again.")
            return {"ok": False, "action": a, "query": want, "offline": True, "content": msg, "response": msg}
        msg = f"I couldn't fetch a model of {want}: {got.get('reason')}"
        return {"ok": False, "action": a, "query": want, "error": str(got.get("reason")), "content": msg,
                "response": msg}
    had = "I already had" if got.get("had") else "Fetched"
    msg = (f"{had} {got['name']} ({got['id']}, {got['size'][1]} m tall, {models.LIBRARY_LICENCE} from "
           f"{models.LIBRARY_NAME}). I can put it in my rooms now.")
    return {"ok": True, "action": a, "query": want, "model": got["id"], "name": got["name"], "size": got["size"],
            "content": msg, "response": msg}


for _action, _fn in {"IMPORT_MODEL": _h_import_model, "FETCH_MODEL": _h_fetch_model}.items():
    _register(_action, _fn)

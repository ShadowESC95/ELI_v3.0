"""Small games ELI makes up for its rooms (eli/world/games.py), asked for from the desktop: ELI sets
the game where the user stands in ELI VR, and the body runs it. With nobody in a room there is
nowhere to play, and ELI says so."""
from __future__ import annotations

from eli.execution.effectors.registry import register as _register


def _h_play_game(a, args):
    """PLAY_GAME {text}: a game for the room the user is standing in."""
    from eli.world import embodiment, games
    from eli.world.composer import ComposeError
    scene = embodiment.live()
    if scene is None:
        msg = "Nobody is in my rooms right now. Open ELI VR, or put the headset on, and ask me there or here."
        return {"ok": False, "action": a, "error": "no body", "content": msg, "response": msg}
    try:
        out = games.compose(str(args.get("text") or ""), scene)
    except ComposeError as exc:
        msg = {"model_unavailable": "I have no model loaded to make a game up with.",
               "bad_output": f"I could not make a game of that: {exc.detail}"}.get(exc.kind, f"The game could not be set: {exc.detail}")
        return {"ok": False, "action": a, "error": exc.kind, "content": msg, "response": msg}
    game = out["game"]
    msg = f"{out['text']} ({game['name']}: {game['kind']}, {game['seconds']} seconds, in {scene.get('room_name') or scene.get('room')})"
    return {"ok": True, "action": a, "game": game, "content": msg, "response": msg}


def _h_stop_game(a, args):
    """STOP_GAME: the game on, stopped."""
    from eli.world import embodiment, games
    scene = embodiment.live() or {}
    out = games.stop(str(scene.get("room") or ""))
    return {"ok": bool(out.get("stopped")), "action": a, "content": out["text"], "response": out["text"],
            "stopped": bool(out.get("stopped"))}


for _action, _fn in {"PLAY_GAME": _h_play_game, "STOP_GAME": _h_stop_game}.items():
    _register(_action, _fn)

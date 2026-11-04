"""Games in ELI's rooms (eli/world/games.py): "let's play a game in VR" (PLAY_GAME) and "stop the
game" (STOP_GAME). A game is for the rooms, so the sentence must say so, or someone must be in them
(a body present, eli/world/embodiment.py); "let's play a game" with nobody in a room is conversation."""
from __future__ import annotations

import re

from eli.execution.routing._helpers import _mk

_IN_THE_ROOMS = re.compile(
    r"\b(?:in|inside|into)\s+(?:vr|the\s+headset|eli\s+vr|your\s+rooms?|the\s+rooms?|your\s+world|your\s+house|"
    r"your\s+room|the\s+room)\b|\bvr\s+game\b", re.I)


def _someone_there() -> bool:
    from eli.world import embodiment
    return bool(embodiment.present())


def route_world_games(text: str, raw: str, low: str, text_l: str):
    from eli.world import games
    said = raw or ""
    if games.wants_stop(said) and (_someone_there() or games.current()):
        return _mk("STOP_GAME", {}, 0.9, matched_by="world.stop_game", meta_extra={"spoken_as_remark": True})
    if games.wants_game(said) and (_IN_THE_ROOMS.search(said) or _someone_there()):
        return _mk("PLAY_GAME", {"text": said.strip(), "kind": games.kind_named(said)}, 0.9, matched_by="world.play_game",
                   meta_extra={"spoken_as_remark": True})
    return None

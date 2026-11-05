"""A record of ELI's in the user's hand (eli/world/embodiment.py, `held`): in ELI's rooms the user
can pick up a sheet of ELI's (a thought, a belief, a memory, a finding) and ask about it. While they
hold one, a question about it ("why did you think that", "how sure were you", "is this still right",
"why did you drop it") is conversation answered from that record, which the prompt carries: not a
self-report, a failure log or a memory command."""
from __future__ import annotations

import re

from eli.execution.routing._helpers import _mk

# What refers to the thing in hand, and what asks about it.
_IT = re.compile(r"\b(?:this|that|it|these|the\s+(?:note|sheet|card|page|paper|thought|belief|claim|memory|record|"
                 r"finding|one))\b", re.I)
_ASKS = re.compile(r"\b(?:why|how\s+sure|how\s+confident|how\s+certain|when|what\s+(?:became|happened|came)|"
                   r"is\s+(?:this|that|it)\s+(?:still|right|true|wrong)|was\s+(?:this|that|it)\s+right|did\s+(?:this|that|it)|"
                   r"explain|tell\s+me\s+(?:about|more)|what\s+is\s+(?:this|that)|what'?s\s+(?:this|that)|"
                   r"where\s+did\s+(?:this|that|it)\s+come|drop|let\s+(?:this|that|it)\s+go|still\s+(?:believe|think|hold))\b", re.I)


def route_world_held(text: str, raw: str, low: str, text_l: str):
    from eli.world import embodiment
    held = embodiment.held()
    if not held:
        return None
    said = raw or ""
    if not (_IT.search(said) and _ASKS.search(said)):
        return None
    return _mk("CHAT", {"message": said}, 0.95, matched_by="router.held_note_chat", allow_chat_without_evidence=True,
               meta_extra={"spoken_as_remark": True, "held": str(held.get("id") or "")})

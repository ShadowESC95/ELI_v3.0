"""
eli/world/spatial_feed.py
─────────────────────────
ELI's world as a spatial client (ELI VR) needs it.

The world state on disk carries everything: every event, action, goal and habit.
A headset wants one small message: which room ELI is in, how he is, and what
stands in the rooms. ``spatial_state()`` builds that message, and
``build_router()`` serves it on the local API server three ways:

    GET  /v1/world/state    one snapshot
    WS   /v1/world/ws       the snapshot again whenever it changes
    POST /v1/world/event    the client reporting back (the user entered a room)

The feed only reads. It goes to storage directly, not through the autonomy
engine, so a client polling it never decays a goal or rewrites the state file.
What a client may report is a short fixed list, and it goes through the same
fire-and-forget event bus as every other runtime signal.
"""
# No `from __future__ import annotations` here: FastAPI resolves a route's
# annotations in this module's globals, and the route types below are local.
import asyncio
import hashlib
import json
from typing import Any, Callable, Dict, Optional

FEED_VERSION = 1
MESSAGE_TYPE = "eli_world_state"

# How often the WebSocket looks for a change. The state is a file other parts
# of ELI rewrite on their own schedule; nothing notifies us.
POLL_SECONDS = 2.0

_AWARENESS = (
    "focus", "uncertainty", "cognitive_load", "curiosity", "repair_pressure",
    "memory_confidence", "evidence_confidence", "tool_activity",
    "reflection_depth", "autonomy_pressure", "social_context_pressure",
)
_AVATAR = (
    "name", "room", "posture", "expression", "activity", "attention_target",
    "movement_intent", "persona_tint",
)

# Everything a spatial client is allowed to tell the world.
CLIENT_EVENTS = frozenset({
    "vr_session_started",
    "vr_session_ended",
    "vr_room_entered",
    "vr_user_spoke",
})
_SUMMARY_MAX = 200
_PAYLOAD_MAX = 2000


def _load_world() -> Dict[str, Any]:
    from eli.world.persistence.storage import EliWorldStorage
    return EliWorldStorage().load().to_dict()


def spatial_state(state: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The world reduced to what a headset draws. Pass a state dict to convert
    it; with none, the current world is read from storage."""
    if state is None:
        state = _load_world()
    avatar = state.get("avatar") or {}
    awareness = state.get("awareness") or {}

    objects = []
    for obj in (state.get("objects") or {}).values():
        if not isinstance(obj, dict) or obj.get("retired"):
            continue
        objects.append({
            "id": str(obj.get("object_id", "")),
            "name": str(obj.get("name", "")),
            "kind": str(obj.get("object_type", "")),
            "room": str(obj.get("room", "")),
            "x": float(obj.get("x", 0.0) or 0.0),
            "y": float(obj.get("y", 0.0) or 0.0),
            "importance": float(obj.get("importance", 0.5) or 0.0),
            "meaning": str(obj.get("symbolic_meaning", "")),
        })

    events = state.get("events") or []
    last_event = events[-1] if events and isinstance(events[-1], dict) else {}

    return {
        "type": MESSAGE_TYPE,
        "version": FEED_VERSION,
        "room": str(avatar.get("room") or "core_room"),
        "avatar": {k: avatar[k] for k in _AVATAR if k in avatar},
        "awareness": {k: float(awareness[k]) for k in _AWARENESS if k in awareness},
        "objects": objects,
        "widgets": {
            "activity": str(avatar.get("activity", "")),
            "confidence": awareness.get("evidence_confidence"),
            "last_event": str(last_event.get("summary", "")),
        },
        "timestamp": state.get("timestamp"),
    }


def _digest(message: Dict[str, Any]) -> str:
    """Identity of a message, ignoring when it was produced."""
    body = {k: v for k, v in message.items() if k != "timestamp"}
    return hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def record_client_event(event_type: str, summary: str = "",
                        payload: Optional[Dict[str, Any]] = None) -> None:
    """Hand a client's report to the world. Raises ValueError for anything not
    on the list, or too large to be a status line."""
    if event_type not in CLIENT_EVENTS:
        raise ValueError(f"unknown spatial event {event_type!r}")
    payload = payload or {}
    if len(json.dumps(payload, default=str)) > _PAYLOAD_MAX:
        raise ValueError("spatial event payload is too large")
    from eli.world.world_event_bus import fire_world_event
    fire_world_event(event_type, "spatial_client", str(summary)[:_SUMMARY_MAX], payload)


def build_router(require_read: Callable[..., Any], require_act: Callable[..., Any],
                 ws_allowed: Callable[[Any], bool]):
    """The three routes, gated by the server's own checks. ``ws_allowed`` gets
    the WebSocket and answers whether its handshake may proceed."""
    from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
    from pydantic import BaseModel

    router = APIRouter(tags=["World"])

    class SpatialEvent(BaseModel):
        event_type: str
        summary: str = ""
        payload: dict = {}

    @router.get("/v1/world/state", dependencies=[Depends(require_read)])
    def world_state():
        """ELI's room, avatar, awareness and world objects, for a spatial client."""
        return spatial_state()

    @router.post("/v1/world/event", dependencies=[Depends(require_act)])
    def world_event(event: SpatialEvent):
        """A spatial client reporting something that happened in its world."""
        try:
            record_client_event(event.event_type, event.summary, event.payload)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True}

    @router.websocket("/v1/world/ws")
    async def world_ws(ws: WebSocket):
        """The spatial state, sent on connect and again whenever it changes.
        The client may send {"type": "vr_event", "event_type": …, "summary": …,
        "payload": …}; anything else is ignored."""
        if not ws_allowed(ws):
            await ws.close(code=4401)
            return
        await ws.accept()
        sent = ""
        try:
            while True:
                message = await asyncio.to_thread(spatial_state)
                digest = _digest(message)
                if digest != sent:
                    await ws.send_text(json.dumps(message, default=str))
                    sent = digest
                try:
                    raw = await asyncio.wait_for(ws.receive_text(), timeout=POLL_SECONDS)
                except asyncio.TimeoutError:
                    continue
                try:
                    incoming = json.loads(raw)
                    if isinstance(incoming, dict) and incoming.get("type") == "vr_event":
                        record_client_event(
                            str(incoming.get("event_type", "")),
                            str(incoming.get("summary", "")),
                            incoming.get("payload") if isinstance(incoming.get("payload"), dict) else {},
                        )
                except (ValueError, TypeError):
                    pass  # a malformed or unlisted report is dropped, never fatal
        except WebSocketDisconnect:
            pass

    return router

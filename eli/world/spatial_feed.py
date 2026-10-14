"""
eli/world/spatial_feed.py
─────────────────────────
ELI's world as a body for ELI needs it: the embodiment protocol.

The world state on disk carries everything: every event, action, goal and habit.
A client that gives ELI a body (ELI VR now; a desktop avatar, glasses or a robot
later) wants a small message and a few ways to answer. This module is that
contract, served on the local API server:

    GET  /v1/world/protocol   which versions and capabilities this ELI speaks
    GET  /v1/world/state      one snapshot
    WS   /v1/world/ws         the snapshot again whenever it changes
    POST /v1/world/event      the client reporting back (the user entered a room)
    POST /v1/world/scene      what the user and ELI can see now       (version 2)
    POST /v1/world/say        a sentence to ELI from inside the world  (version 2)
    POST /v1/world/edit       the user moving or changing a thing      (version 2)
    POST /v1/world/compose    ELI making something of its own accord   (version 2)
    POST /v1/world/undo       taking back the last change              (version 2)
    GET  /v1/world/mind       which model is ELI's mind, and which could be (version 2)
    POST /v1/world/mind       choosing one                              (version 2)

A body consumes ELI's state; it is never ELI's thinking. The same ELI can have
several bodies without a second mind.

Versions are agreed, not assumed. A client says which it speaks; the answer is
the one both have, or a plain refusal with the reason. A client that says
nothing is a version 1 client and gets exactly what version 1 always gave.

The feed only reads. It goes to storage directly, not through the autonomy
engine, so a client polling it never decays a goal or rewrites the state file.
Everything that changes the world goes through eli/world/building.py and so
through the world's policy and its ledger.
"""
# No `from __future__ import annotations` here: FastAPI resolves a route's
# annotations in this module's globals, and the route types below are local.
import asyncio
import hashlib
import json
from typing import Any, Callable, Dict, List, Optional

FEED_VERSION = 1                       # what a client that never said hello is given
PROTOCOL_VERSIONS = (1, 2)
MESSAGE_TYPE = "eli_world_state"
CAPABILITIES = {
    1: ("world-state", "room-events"),
    2: ("world-state", "room-events", "scene", "say", "building", "undo", "own-room", "mind"),
}
# Close codes a client can tell apart.
CLOSE_UNAUTHORISED = 4401
CLOSE_INCOMPATIBLE = 4406

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
_SAY_MAX = 2000


def _load_world() -> Dict[str, Any]:
    from eli.world.persistence.storage import EliWorldStorage
    return EliWorldStorage().load().to_dict()


def _server_name() -> str:
    try:
        from importlib.metadata import version
        return f"ELI v3 {version('eli-v3')}"
    except Exception:
        return "ELI v3"


# ── versions ─────────────────────────────────────────────────────────────────
def protocol() -> Dict[str, Any]:
    """What this ELI speaks."""
    return {
        "type": "eli_world_protocol",
        "versions": list(PROTOCOL_VERSIONS),
        "capabilities": {str(v): list(c) for v, c in CAPABILITIES.items()},
        "server": _server_name(),
    }


def negotiate(hello: Dict[str, Any]) -> Dict[str, Any]:
    """Answer a client's hello: a welcome naming the version both speak and what
    comes with it, or a refusal that says exactly why."""
    offered = hello.get("protocol_versions")
    theirs = sorted({int(v) for v in offered if isinstance(v, (int, float))}) if isinstance(offered, list) else []
    common = [v for v in theirs if v in PROTOCOL_VERSIONS]
    if not common:
        said = ", ".join(str(v) for v in theirs) or "none"
        return {
            "type": "incompatible",
            "reason": (f"This ELI speaks world protocol {', '.join(str(v) for v in PROTOCOL_VERSIONS)}; "
                       f"the client offered {said}."),
            "server_versions": list(PROTOCOL_VERSIONS),
            "client_versions": theirs,
            "server": _server_name(),
        }
    version = max(common)
    wanted = hello.get("capabilities") if isinstance(hello.get("capabilities"), list) else None
    have = list(CAPABILITIES[version])
    return {
        "type": "welcome",
        "version": version,
        "capabilities": have,
        # what the client asked for and this ELI cannot give, so it can say so and not guess
        "missing": sorted(set(map(str, wanted)) - set(have)) if wanted else [],
        "server": _server_name(),
    }


# ── the message ──────────────────────────────────────────────────────────────
def spatial_state(state: Optional[Dict[str, Any]] = None, version: int = FEED_VERSION) -> Dict[str, Any]:
    """The world reduced to what a body draws. Pass a state dict to convert
    it; with none, the current world is read from storage."""
    if state is None:
        state = _load_world()
    avatar = state.get("avatar") or {}
    awareness = state.get("awareness") or {}
    full = version >= 2

    objects = []
    for obj in (state.get("objects") or {}).values():
        if not isinstance(obj, dict) or obj.get("retired"):
            continue
        item = {
            "id": str(obj.get("object_id", "")),
            "name": str(obj.get("name", "")),
            "kind": str(obj.get("object_type", "")),
            "room": str(obj.get("room", "")),
            "x": float(obj.get("x", 0.0) or 0.0),
            "y": float(obj.get("y", 0.0) or 0.0),
            "importance": float(obj.get("importance", 0.5) or 0.0),
            "meaning": str(obj.get("symbolic_meaning", "")),
        }
        if full:
            from eli.world import building
            item["form"] = building.form_of(obj)
            item["made_by"] = str(obj.get("created_by", ""))
        objects.append(item)

    events = state.get("events") or []
    last_event = events[-1] if events and isinstance(events[-1], dict) else {}

    message = {
        "type": MESSAGE_TYPE,
        "version": version if full else FEED_VERSION,
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
    if full:
        from eli.world import building
        spaces = state.get("spaces") or {}
        own = spaces.get(building.OWN_ROOM) if isinstance(spaces.get(building.OWN_ROOM), dict) else {}
        message["own_room"] = building.OWN_ROOM
        message["spaces"] = {building.OWN_ROOM: {
            "name": str(own.get("name") or building.OWN_ROOM_INFO["name"]),
            "purpose": str(own.get("purpose") or building.OWN_ROOM_INFO["purpose"]),
            "mood": building.mood_of(spaces),
        }}
        message["recent"] = _recent(state)
        try:
            # ELI's real memories and goals, standing in the rooms named for them
            from eli.world import shown
            message["objects"] = objects + shown.items()
        except Exception:
            pass
    return message


def _recent(state: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
    """The last few things ELI did to its world and why, newest last. ELI's
    walking from room to room is left out: the avatar already says where ELI is."""
    out = []
    for action in reversed(state.get("actions") or []):
        if not isinstance(action, dict) or action.get("action_type") == "avatar_move":
            continue
        out.append({
            "what": str(action.get("action_type", "")),
            "who": str(action.get("actor", "")),
            "room": str(action.get("room", "")),
            "why": str(action.get("reason", ""))[:_SUMMARY_MAX],
            "allowed": action.get("permission_class") == "world_safe",
        })
        if len(out) >= limit:
            break
    return list(reversed(out))


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
    if event_type == "vr_session_ended":
        from eli.world import embodiment
        embodiment.forget()
    from eli.world.world_event_bus import fire_world_event
    fire_world_event(event_type, "spatial_client", str(summary)[:_SUMMARY_MAX], payload)


def say(text: str, chat: Optional[Callable[[str], str]] = None) -> Dict[str, Any]:
    """One sentence to ELI from inside the world. If it asks for the place to
    be changed, ELI changes it and says so; otherwise it is an ordinary turn of
    conversation, with where ELI is in mind (see eli/world/embodiment.py)."""
    from eli.world import composer, embodiment
    text = str(text or "").strip()[:_SAY_MAX]
    if not text:
        raise ValueError("nothing was said")
    if composer.wants_building(text):
        return composer.compose(text, embodiment.live())
    if chat is None:
        raise RuntimeError("conversation is not available on this server")
    return {"kind": "chat", "text": str(chat(text) or "").strip(), "results": [], "refused": []}


def build_router(require_read: Callable[..., Any], require_act: Callable[..., Any],
                 ws_allowed: Callable[[Any], bool],
                 chat: Optional[Callable[[str, Any, str], str]] = None,
                 require_admin: Optional[Callable[..., Any]] = None):
    """The routes, gated by the server's own checks. ``ws_allowed`` gets the
    WebSocket and answers whether its handshake may proceed. ``chat`` is the
    server's own conversation: (text, principal, session) -> ELI's reply.
    ``require_admin`` gates choosing the model, as it does in the server."""
    from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
    from pydantic import BaseModel

    router = APIRouter(tags=["World"])

    class SpatialEvent(BaseModel):
        event_type: str
        summary: str = ""
        payload: dict = {}

    class Scene(BaseModel):
        scene: dict

    class Said(BaseModel):
        text: str
        session_id: str = ""

    class Edits(BaseModel):
        edits: list
        room: str = ""
        summary: str = ""

    class Mind(BaseModel):
        model: str

    def _problem(failure: Exception) -> HTTPException:
        """A failure a client can tell from the others: a kind and a sentence."""
        from eli.world.composer import ComposeError
        from eli.world.mind import MindError
        if isinstance(failure, ComposeError):
            status = 503 if failure.kind == "model_unavailable" else 502
            return HTTPException(status_code=status, detail={"kind": failure.kind, "detail": failure.detail})
        if isinstance(failure, MindError):
            status = 400 if failure.kind == "unknown_model" else 503
            return HTTPException(status_code=status, detail={"kind": failure.kind, "detail": failure.detail})
        if isinstance(failure, ValueError):
            return HTTPException(status_code=400, detail={"kind": "bad_request", "detail": str(failure)})
        return HTTPException(status_code=500, detail={"kind": "failed", "detail": str(failure)})

    @router.get("/v1/world/protocol", dependencies=[Depends(require_read)])
    def world_protocol():
        """The protocol versions and capabilities this ELI speaks."""
        return protocol()

    @router.get("/v1/world/state", dependencies=[Depends(require_read)])
    def world_state(version: int = FEED_VERSION):
        """ELI's room, avatar, awareness and world objects, for a spatial client."""
        if version not in PROTOCOL_VERSIONS:
            raise HTTPException(status_code=400, detail={
                "kind": "incompatible", "detail": negotiate({"protocol_versions": [version]})["reason"]})
        return spatial_state(version=version)

    @router.post("/v1/world/event", dependencies=[Depends(require_act)])
    def world_event(event: SpatialEvent):
        """A spatial client reporting something that happened in its world."""
        try:
            record_client_event(event.event_type, event.summary, event.payload)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"ok": True}

    @router.post("/v1/world/scene", dependencies=[Depends(require_act)])
    def world_scene(body: Scene):
        """What the user and ELI can see now. Kept in memory while it is fresh."""
        from eli.world import embodiment
        try:
            embodiment.report(body.scene)
        except ValueError as e:
            raise _problem(e)
        return {"ok": True}

    @router.post("/v1/world/say")
    def world_say(body: Said, principal: Any = Depends(require_act)):
        """A sentence to ELI from inside the world: ELI answers, or builds."""
        try:
            return say(body.text, (lambda text: chat(text, principal, body.session_id)) if chat else None)
        except Exception as e:  # noqa: BLE001 - every failure leaves as a typed problem
            raise _problem(e)

    @router.post("/v1/world/edit", dependencies=[Depends(require_act)])
    def world_edit(body: Edits):
        """The user moving or changing a thing with their own hands."""
        from eli.world import building
        return building.apply(body.edits, actor="user", asked=True, source="spatial_client",
                              default_room=body.room or building.OWN_ROOM,
                              summary=body.summary or "The user changed something by hand")

    @router.post("/v1/world/compose", dependencies=[Depends(require_act)])
    def world_compose():
        """ELI making or changing one thing in its own room, unasked."""
        from eli.world import composer, embodiment, urges
        try:
            # a moment of ELI's own that is waiting comes first; with none, what is on ELI's mind
            return urges.act(embodiment.live()) or composer.compose(None, embodiment.live())
        except Exception as e:  # noqa: BLE001
            raise _problem(e)

    @router.post("/v1/world/undo", dependencies=[Depends(require_act)])
    def world_undo():
        """Take back the last change made to the world by building."""
        from eli.world import building
        return building.apply([{"op": "undo"}], actor="user", asked=True, source="spatial_client",
                              summary="The user asked for the last change to be taken back")

    @router.get("/v1/world/mind", dependencies=[Depends(require_read)])
    def world_mind():
        """Which model is ELI's mind now, and which ones ELI could use. Names, never paths."""
        from eli.world import mind
        return mind.status()

    @router.post("/v1/world/mind", dependencies=[Depends(require_admin or require_act)])
    def world_mind_choose(body: Mind):
        """Choose ELI's model from inside the world. It is loaded before this answers."""
        from eli.world import mind
        try:
            return mind.choose(body.model)
        except Exception as e:  # noqa: BLE001
            raise _problem(e)

    @router.websocket("/v1/world/ws")
    async def world_ws(ws: WebSocket):
        """The spatial state, sent on connect and again whenever it changes.

        A client may say {"type": "hello", "protocol_versions": [...],
        "capabilities": [...]}. It is answered with a welcome, and the state
        follows in the version agreed; or with {"type": "incompatible", ...}
        and the socket is closed. A client that never says hello is version 1.

        The client may also send {"type": "vr_event", "event_type": …,
        "summary": …, "payload": …}; anything else is ignored."""
        if not ws_allowed(ws):
            await ws.close(code=CLOSE_UNAUTHORISED)
            return
        await ws.accept()
        version = FEED_VERSION
        sent = ""
        try:
            while True:
                message = await asyncio.to_thread(spatial_state, None, version)
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
                except (ValueError, TypeError):
                    continue
                if not isinstance(incoming, dict):
                    continue
                if incoming.get("type") == "hello":
                    answer = negotiate(incoming)
                    await ws.send_text(json.dumps(answer))
                    if answer["type"] == "incompatible":
                        await ws.close(code=CLOSE_INCOMPATIBLE)
                        return
                    version, sent = answer["version"], ""   # the state again, as agreed
                elif incoming.get("type") == "vr_event":
                    try:
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

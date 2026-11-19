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
    WS   /v1/world/ws         the snapshot again whenever it changes; a client that keeps the records it
                              was sent is sent each whole once, then by its id (version 2, kept-records)
    POST /v1/world/event      the client reporting back (the user entered a room)
    POST /v1/world/scene      what the user and ELI can see now       (version 2)
    POST /v1/world/body       the real things the body can draw, for ELI to place (version 2, props)
    GET  /v1/world/picture/N  a picture ELI took or looked at for the user, by its ledger row (version 2, pictures)
    GET  /v1/world/model/ID/F a file of a model ELI brought in, as the message's `models` lists it (version 2, models)
    POST /v1/world/say        a sentence to ELI from inside the world  (version 2)
    POST /v1/world/edit       the user moving or changing a thing      (version 2)
    POST /v1/world/compose    ELI making something of its own accord   (version 2)
    POST /v1/world/undo       taking back the last change              (version 2)
    GET  /v1/world/thoughts   ELI's thoughts lately: why, how sure, what it decided, what became of them (version 2, thoughts)
    GET  /v1/world/book/ID    one of ELI's books whole, with its sections (version 2, books)
    GET  /v1/world/mind       which model is ELI's mind, and which could be (version 2)
    POST /v1/world/mind       choosing one                              (version 2)
    POST /v1/world/played     the body saying how a game ELI set went   (version 2, games)

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

from eli.utils.log import get_logger

log = get_logger(__name__)

FEED_VERSION = 1                       # what a client that never said hello is given
PROTOCOL_VERSIONS = (1, 2)
MESSAGE_TYPE = "eli_world_state"
CAPABILITIES = {
    1: ("world-state", "room-events"),
    2: ("world-state", "room-events", "scene", "say", "building", "undo", "own-room", "mind", "props", "pictures",
        "models", "games", "thoughts", "books", "evidence", "kept-records"),
}
# A client that asks for this keeps the records it was sent: one the same as when it was last sent
# over this link goes as its id alone (lighten).
KEPT_RECORDS = "kept-records"
# The most one message to a body may take, in bytes, unless its hello says otherwise ("max_message"):
# what ELI VR's world link takes in (eli_link.gd, inbound_buffer_size: 1 MiB). ELI's books fill what
# the rest of the message leaves of it (shown.books).
MESSAGE_BYTES = 1 << 20
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
        # the most one message to it will take, as it said (or MESSAGE_BYTES)
        "max_message": message_bytes(hello),
        "server": _server_name(),
    }


def message_bytes(hello: Optional[Dict[str, Any]] = None) -> int:
    """The most one message to a client may take: what its hello said ("max_message", bytes), else
    MESSAGE_BYTES."""
    said = (hello or {}).get("max_message")
    if isinstance(said, (int, float)) and not isinstance(said, bool) and said > 0:
        return int(said)
    return MESSAGE_BYTES


# ── the message ──────────────────────────────────────────────────────────────
def spatial_state(state: Optional[Dict[str, Any]] = None, version: int = FEED_VERSION,
                  budget: Optional[int] = None, *, reader: Optional[str] = None) -> Dict[str, Any]:
    """The world reduced to what a body draws. Pass a state dict to convert
    it; with none, the current world is read from storage. A version 2 message is held to `budget`
    bytes (MESSAGE_BYTES): ELI's books fill what the rest leaves of it. `reader` is whose thoughts it
    carries (shown.kept's user_id: a member's own and ELI's shared ones; None the owner's), as for
    /v1/world/thoughts: the message read every caller the owner's."""
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
            meta = obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
            if isinstance(meta.get("piece"), dict):
                item["piece"] = dict(meta["piece"])        # its part in a game (eli/world/games.py)
            if obj.get("object_type") == building.GAME and isinstance(meta.get("game"), dict):
                item["game"] = dict(meta["game"])          # the rules, for the body to run
                item["status"] = str(meta.get("status") or "set")
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
        # each room of the house ELI has given a light or a mist of its own
        rooms = state.get("rooms") or {}
        for room_id, info in rooms.items():
            look = building.look_of(rooms, room_id)
            if look and room_id != building.OWN_ROOM:
                message["spaces"][room_id] = {"name": str(info.get("name") or room_id),
                                              "purpose": str(info.get("purpose") or ""), "mood": look}
        message["recent"] = _recent(state)
        message["log"] = _log(state)
        try:
            # ELI's real memories and goals, standing in the rooms named for them
            from eli.world import shown
            message["objects"] = objects + shown.items()
            message["mind"] = shown.thoughts()
            message["coming_up"] = shown.coming_up()
            # ELI's own models, for a body to fetch and draw
            from eli.world import models
            message["models"] = models.listed()
            # moments of ELI's own waiting to be made into something, and whether ELI is free to:
            # a body may ask ELI to make something of one while someone is there to see it
            message["waiting"] = len([u for u in own.get("urges") or [] if isinstance(u, dict)])
            message["free"] = shown.free()
            # ELI's thoughts lately, each with why it had it, how sure it was, what it decided and what
            # became of it, by the room they belong in (eli/cognition/thoughts.py)
            message["thoughts"] = shown.kept(user_id=reader)
            # what ELI's actions did and how often its figures proved right (the Evidence Wall)
            message["evidence"] = shown.evidence()
        except Exception:
            log.debug("ELI's records were not all read for the world message", exc_info=True)
        _add_books(message, budget if budget is not None else MESSAGE_BYTES)
    return message


def _add_books(message: Dict[str, Any], budget: int) -> None:
    """ELI's books in the room the rest of the message leaves of `budget` (shown.books), the message
    measured as it is sent; a book that would take it past the budget is not sent."""
    try:
        from eli.world import shown
        found = shown.books(budget - len(json.dumps(message, default=str)))
    except Exception:
        log.debug("ELI's books were not read for the world message", exc_info=True)
        return
    message["objects"] = list(message.get("objects") or []) + found
    while found and len(json.dumps(message, default=str)) > budget:
        found.pop()
        message["objects"].pop()


# A screenful of ELI's own log for a body to show where it keeps such things (the Debug Basement's
# terminals): newest last.
LOG_LINES = 24


def _log(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What has been happening in ELI, as its world recorded it: reasoning stages, recalls, weak
    evidence, tools that worked or failed and why, goals taken on, reflections. Newest last. What a
    spatial client reported (the user coming into a room) is the room talking, not ELI, and is left out."""
    own = [e for e in state.get("events") or []
           if isinstance(e, dict) and e.get("source") != "spatial_client" and str(e.get("summary") or "").strip()]
    out = []
    for event in own[-LOG_LINES:]:
        out.append({"type": str(event.get("event_type", "")), "source": str(event.get("source", "")),
                    "text": " ".join(str(event.get("summary")).split())[:_SUMMARY_MAX],
                    "ts": event.get("timestamp")})
    return out


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


def lighten(message: Dict[str, Any], held: Dict[str, str]) -> Dict[str, Any]:
    """The message for a client that keeps the records it was sent ("kept-records"): a record ("objects")
    the same as when it was last sent over this link goes as {"id", "kept": true}, and the client shows
    the one it holds. `held` is what this link has been sent ({id: the record's digest}), brought up to
    this message: a record that changed or is new goes whole, one no longer in the message is forgotten,
    so one that comes back is sent whole again. ELI's books and what it holds about the user are most
    of a message and seldom change, and every change anywhere used to send all of them again."""
    records = message.get("objects")
    if not isinstance(records, list):
        return message
    sent, out = {}, []
    for record in records:
        ident = str(record.get("id") or "") if isinstance(record, dict) else ""
        if not ident or ident in sent:          # nothing to keep it by, or an id twice: sent as it is
            out.append(record)
            continue
        sent[ident] = hashlib.sha1(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()
        out.append({"id": ident, "kept": True} if held.get(ident) == sent[ident] else record)
    held.clear()
    held.update(sent)
    return {**message, "objects": out}


def _digest(message: Dict[str, Any]) -> str:
    """Identity of a message, ignoring when it was produced."""
    body = {k: v for k, v in message.items() if k != "timestamp"}
    return hashlib.sha1(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def record_client_event(event_type: str, summary: str = "",
                        payload: Optional[Dict[str, Any]] = None,
                        who: Optional[Dict[str, str]] = None) -> None:
    """Hand a client's report to the world. Raises ValueError for anything not
    on the list, or too large to be a status line. `who`: the surface and session the client named
    in its hello, when it came over the world link."""
    if event_type not in CLIENT_EVENTS:
        raise ValueError(f"unknown spatial event {event_type!r}")
    payload = payload or {}
    if len(json.dumps(payload, default=str)) > _PAYLOAD_MAX:
        raise ValueError("spatial event payload is too large")
    if event_type == "vr_session_ended":
        from eli.world import embodiment
        if who and who.get("session"):
            embodiment.end(who.get("surface") or None, who["session"], why="its client ended the session")
        else:
            embodiment.forget()
    from eli.world.world_event_bus import fire_world_event
    fire_world_event(event_type, "spatial_client", str(summary)[:_SUMMARY_MAX], payload)


def say(text: str, chat: Optional[Callable[[str], str]] = None, *, session_id: str = "",
        remember: Optional[Callable[[str, str], Any]] = None) -> Dict[str, Any]:
    """One sentence to ELI from inside the world. If it asks for a game, ELI sets one; if it asks
    for the place to be changed, ELI changes it and says so; otherwise it is an ordinary turn of
    conversation, with where ELI is in mind (see eli/world/embodiment.py).

    A sentence the game or the building answered is kept as the chat path keeps its turns: what
    was done (or refused) is an action row in the evidence ledger with this VR session's id, which
    the period log reads, and the sentence and ELI's answer go to `remember(said, answer)`, the
    server's own turn store, when it gives one. Only chat used to leave a trace."""
    from eli.world import composer, embodiment, games
    text = str(text or "").strip()[:_SAY_MAX]
    if not text:
        raise ValueError("nothing was said")
    scene = embodiment.live()
    if games.wants_stop(text) and games.current((scene or {}).get("room", "")):
        return _kept(games.stop((scene or {}).get("room", ""), said=text), "STOP_GAME", text, session_id, remember)
    if games.wants_game(text):
        return _kept(games.compose(text, scene), "PLAY_GAME", text, session_id, remember)
    if composer.wants_building(text, scene):
        return _kept(composer.compose(text, scene), "WORLD_EDIT", text, session_id, remember)
    if chat is None:
        raise RuntimeError("conversation is not available on this server")
    return {"kind": "chat", "text": str(chat(text) or "").strip(), "results": [], "refused": []}


def _kept(result: Dict[str, Any], action: str, said: str, session_id: str,
          remember: Optional[Callable[[str, str], Any]]) -> Dict[str, Any]:
    """Record a sentence the world answered without the chat path (see say) and give back its result."""
    results = [r for r in result.get("results") or [] if isinstance(r, dict)]
    worked = any(r.get("ok") for r in results) or bool(result.get("stopped") or result.get("ok"))
    done = "; ".join(f"{r.get('op', '')} {r.get('name', '')}".strip() + ("" if r.get("ok") else
                     f" (not done: {r.get('reason', 'refused')})") for r in results)
    try:
        from eli.runtime.evidence_ledger import record_event
        record_event("executor_action", source="world.say", action=action, subject=str(result.get("room") or ""),
                     content=(str(result.get("text") or "") + (f" [{done}]" if done else "")).strip(),
                     payload={"said": said, "results": results, "refused": result.get("refused") or []},
                     severity="info" if worked else "warning", outcome="ok" if worked else "failed",
                     session_id=str(session_id or ""))
    except Exception:  # noqa: BLE001 - the answer still goes back; the record is said to be missing
        log.warning("what the world did for %r was not recorded", said[:80], exc_info=True)
    if remember is not None:
        try:
            remember(said, str(result.get("text") or ""))
        except Exception:  # noqa: BLE001
            log.warning("the world's turn was not kept in the conversation", exc_info=True)
    return result


def build_router(require_read: Callable[..., Any], require_act: Callable[..., Any],
                 ws_allowed: Callable[[Any], bool],
                 chat: Optional[Callable[[str, Any, str], str]] = None,
                 require_admin: Optional[Callable[..., Any]] = None,
                 remember: Optional[Callable[[str, str, Any, str], Any]] = None,
                 reader: Optional[Callable[[Any], Optional[str]]] = None):
    """The routes, gated by the server's own checks. ``ws_allowed`` gets the
    WebSocket and answers whether its handshake may proceed. ``chat`` is the
    server's own conversation: (text, principal, session) -> ELI's reply.
    ``require_admin`` gates choosing the model, as it does in the server.
    ``remember`` is the server's turn store: (said, answer, principal, session), for a sentence the
    game or the building answered. ``reader`` is whose records a caller reads: (principal, as
    require_read gives it) -> a member's user id, or None for the owner's. ``ws_allowed`` may give
    the caller (a principal, as require_read gives it) for a handshake that may proceed, so the
    socket's messages are that caller's; a bare True is the owner."""
    from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
    from pydantic import BaseModel

    router = APIRouter(tags=["World"])

    class SpatialEvent(BaseModel):
        event_type: str
        summary: str = ""
        payload: dict = {}

    class Scene(BaseModel):
        scene: dict

    class Body(BaseModel):
        surface: str = "spatial_client"
        props: list = []

    class Said(BaseModel):
        text: str
        session_id: str = ""

    class Edits(BaseModel):
        edits: list
        room: str = ""
        summary: str = ""

    class Mind(BaseModel):
        model: str

    class Played(BaseModel):
        game: str
        result: dict = {}

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

    def _reads(principal: Any) -> Optional[str]:
        """Whose records this caller reads (``reader``); None, the owner's, with no reader or caller."""
        return reader(principal) if reader and principal is not None and principal is not True else None

    @router.get("/v1/world/state")
    def world_state(version: int = FEED_VERSION, principal: Any = Depends(require_read)):
        """ELI's room, avatar, awareness and world objects, for a spatial client: the caller's own."""
        if version not in PROTOCOL_VERSIONS:
            raise HTTPException(status_code=400, detail={
                "kind": "incompatible", "detail": negotiate({"protocol_versions": [version]})["reason"]})
        return spatial_state(version=version, reader=_reads(principal))

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

    @router.post("/v1/world/body", dependencies=[Depends(require_act)])
    def world_body(body: Body):
        """What a body can draw: the real things (furniture, lamps, books) ELI may place in its rooms."""
        from eli.world import embodiment
        try:
            return embodiment.report_body(body.model_dump())
        except ValueError as e:
            raise _problem(e)

    @router.get("/v1/world/picture/{row_id}", dependencies=[Depends(require_read)])
    def world_picture(row_id: int):
        """A picture ELI took or looked at for the user: only one its evidence ledger records as such,
        still on disk, and a picture by its own first bytes. Anything else is not found."""
        from fastapi.responses import FileResponse
        from eli.world import shown
        path = shown.picture(row_id)
        if not path:
            raise HTTPException(status_code=404, detail="no such picture")
        with open(path, "rb") as f:
            kind = shown.picture_type(f.read(12))
        return FileResponse(path, media_type=kind, headers={"Cache-Control": "no-store"})

    @router.get("/v1/world/model/{ident}/{rel:path}", dependencies=[Depends(require_read)])
    def world_model(ident: str, rel: str):
        """A file of a model ELI brought in: only one the manifest lists, from the model's own folder."""
        from fastapi.responses import FileResponse
        from eli.world import models
        path = models.file_path(ident, rel)
        if path is None:
            raise HTTPException(status_code=404, detail="no such model file")
        kind = "model/gltf+json" if path.suffix == ".gltf" else ("model/gltf-binary" if path.suffix == ".glb"
                                                                 else "application/octet-stream")
        return FileResponse(str(path), media_type=kind, headers={"Cache-Control": "no-store"})

    @router.post("/v1/world/say")
    def world_say(body: Said, principal: Any = Depends(require_act)):
        """A sentence to ELI from inside the world: ELI answers, or builds."""
        try:
            return say(body.text, (lambda text: chat(text, principal, body.session_id)) if chat else None,
                       session_id=body.session_id,
                       remember=(lambda said, answer: remember(said, answer, principal, body.session_id))
                       if remember else None)
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
        """ELI making something unasked: of a moment of its own, in any room it chooses; with none, in its own room."""
        from eli.world import composer, embodiment, urges
        try:
            # a moment of ELI's own that is waiting comes first; with none, what is on ELI's mind
            return urges.act(embodiment.live()) or composer.compose(None, embodiment.live())
        except Exception as e:  # noqa: BLE001
            raise _problem(e)

    @router.post("/v1/world/played", dependencies=[Depends(require_act)])
    def world_played(body: Played):
        """The body saying how a game ELI set went: the score is kept, the pieces leave, ELI remembers."""
        from eli.world import games
        try:
            return games.played(body.game, body.result)
        except ValueError as e:
            raise _problem(e)

    @router.post("/v1/world/undo", dependencies=[Depends(require_act)])
    def world_undo():
        """Take back the last change made to the world by building."""
        from eli.world import building
        return building.apply([{"op": "undo"}], actor="user", asked=True, source="spatial_client",
                              summary="The user asked for the last change to be taken back")

    @router.get("/v1/world/thoughts")
    def world_thoughts(room: str = "", kinds: str = "", limit: int = 40, principal: Any = Depends(require_read)):
        """ELI's thoughts lately: what it recalled, believed, read a sentence as, made, tried and proposed,
        each with why, how sure, what it decided and what became of it. `room` keeps it to one room of
        the house, `kinds` to a comma-separated list of kinds. The caller's own (``reader``): a member
        never reads the owner's thoughts about the owner, nor the owner a member's."""
        from eli.world import shown
        who = _reads(principal)
        wanted = [k.strip() for k in str(kinds or "").split(",") if k.strip()]
        size = max(1, min(int(limit), shown.THOUGHTS))
        return {"thoughts": shown.kept(room=room, kinds=wanted or None, limit=size, user_id=who)
                if (room or wanted) else shown.kept(user_id=who)[:size]}

    @router.get("/v1/world/book/{book_id}", dependencies=[Depends(require_read)])
    def world_book(book_id: str):
        """One of ELI's books whole: what it is, and its sections [{heading, when, text}]. Only a book of
        this user's records (eli/world/books.py); anything else is not found."""
        from eli.world import books
        book = books.read(book_id)
        if book is None:
            raise HTTPException(status_code=404, detail="no such book")
        return {"id": book["id"], "book": {k: v for k, v in book.items() if k not in ("id", "sections")},
                "sections": book["sections"]}

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
        "capabilities": [...], "max_message": bytes}. It is answered with a
        welcome, and the state follows in the version agreed, each message no
        larger than it said it takes (MESSAGE_BYTES when it said nothing); or
        with {"type": "incompatible", ...} and the socket is closed. A client
        that never says hello is version 1.

        The client may also send {"type": "vr_event", "event_type": …,
        "summary": …, "payload": …}; anything else is ignored."""
        caller = ws_allowed(ws)
        if not caller:
            await ws.close(code=CLOSE_UNAUTHORISED)
            return
        await ws.accept()
        reads = _reads(caller)         # whose thoughts the messages carry: the caller's own
        version = FEED_VERSION
        budget = MESSAGE_BYTES         # the most one message to it takes, as its hello says
        sent = ""
        keeps: Optional[Dict[str, str]] = None    # the records a client that keeps them holds (lighten)
        who: Dict[str, str] = {}       # the surface and session the client named in its hello
        try:
            while True:
                message = await asyncio.to_thread(spatial_state, None, version, budget, reader=reads)
                digest = _digest(message)
                if digest != sent:
                    if keeps is not None:
                        message = lighten(message, keeps)
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
                    if str(incoming.get("session") or "").strip():
                        who = {"surface": str(incoming.get("surface") or "spatial_client")[:32],
                               "session": str(incoming["session"]).strip()[:64]}
                    answer = negotiate(incoming)
                    await ws.send_text(json.dumps(answer))
                    if answer["type"] == "incompatible":
                        await ws.close(code=CLOSE_INCOMPATIBLE)
                        return
                    version, sent = answer["version"], ""   # the state again, as agreed
                    asked = incoming.get("capabilities") if isinstance(incoming.get("capabilities"), list) else []
                    # from here the client holds what it is sent, if it said it keeps records: all of it first
                    keeps = {} if KEPT_RECORDS in asked and KEPT_RECORDS in answer["capabilities"] else None
                    budget = int(answer.get("max_message") or MESSAGE_BYTES)
                elif incoming.get("type") == "vr_event":
                    try:
                        record_client_event(
                            str(incoming.get("event_type", "")),
                            str(incoming.get("summary", "")),
                            incoming.get("payload") if isinstance(incoming.get("payload"), dict) else {},
                            who=who or None,
                        )
                    except (ValueError, TypeError):
                        pass  # a malformed or unlisted report is dropped, never fatal
        except WebSocketDisconnect:
            pass
        finally:
            # The link is the body's: once it closes, the session it named is over (ELI VR closed by
            # its window, stopped by its launcher or crashed sends nothing else).
            if who:
                from eli.world import embodiment
                await asyncio.to_thread(embodiment.end, who["surface"], who["session"], "its world link closed")

    return router

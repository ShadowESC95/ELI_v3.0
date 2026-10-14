# Embodiment: the world protocol

ELI has a world: nine rooms, an avatar, awareness, objects, a journal, a policy and a ledger
(`eli/world/`). A client that gives ELI a body draws that world and reports back from it. ELI VR is
the first. The contract between the two is the *world protocol*, served by ELI's local server from
`eli/world/spatial_feed.py`.

A body consumes ELI's state. It is never ELI's thinking: the same ELI can have several bodies (a
headset, a desktop avatar, a display in a room) without a second mind, and without any of them
importing ELI's runtime.

## Versions

Versions are agreed, not assumed. `GET /v1/world/protocol` says what this ELI speaks. On the socket
a client says hello:

```json
{"type": "hello", "protocol_versions": [1, 2], "capabilities": ["say", "building"], "surface": "eli_vr"}
```

and is answered with the newest version both have, what comes with it, and what it asked for that
this ELI cannot give:

```json
{"type": "welcome", "version": 2, "capabilities": ["world-state", "room-events", "scene", "say", "building", "undo", "own-room"], "missing": []}
```

or, with no version in common, a refusal that says why, and the socket closes with code 4406:

```json
{"type": "incompatible", "reason": "This ELI speaks world protocol 1, 2; the client offered 9.", "server_versions": [1, 2], "client_versions": [9]}
```

A client that never says hello is a version 1 client and gets exactly what version 1 always gave.

| Version | Carries |
|---|---|
| 1 | Room, avatar, awareness, objects; the client's room events |
| 2 | All of 1, and how each object looks and where it stands, ELI's own room, what ELI last did and why; the scene; sentences; building; undo |

## Routes

All on the local server, behind its own token and role checks. Loopback callers are the owner.

| Route | What | Version |
|---|---|---|
| `GET /v1/world/protocol` | Versions and capabilities | any |
| `GET /v1/world/state?version=N` | One snapshot | 1 |
| `WS /v1/world/ws` | The snapshot again whenever it changes; `hello`, `vr_event` from the client | 1 |
| `POST /v1/world/event` | A listed client event (a session started, a room entered) | 1 |
| `POST /v1/world/scene` | What the user and ELI can see now | 2 |
| `POST /v1/world/say` | A sentence from inside the world: a turn of conversation, or building | 2 |
| `POST /v1/world/edit` | The user moving or changing a thing by hand | 2 |
| `POST /v1/world/compose` | ELI making one thing in its own room, unasked | 2 |
| `POST /v1/world/undo` | Take back the last change made by building | 2 |
| `GET /v1/world/mind` | Which model is loaded, which would be used, which ones ELI has | 2 |
| `POST /v1/world/mind` | Choose ELI's model (admin) | 2 |

A failure on the version 2 routes carries a kind a client can act on:
`{"detail": {"kind": "model_unavailable", "detail": "..."}}`. Kinds: `bad_request` (400),
`incompatible` (400), `unknown_model` (400), `model_unavailable` (503), `bad_output` (502),
`failed` (500 or 502).

A sentence on `/v1/world/say` is the owner's turn, attributed the way `/v1/chat` attributes it. The
client names its surface and session; it is never a user of its own.

## Knowing where ELI is (`eli/world/embodiment.py`)

A client reports the scene every few seconds: the room and what the place is, where the user stands
and what they look at, whether ELI is in the room and how far away, which of ELI's things are near. The scene is kept
in memory, per surface, for 45 seconds, and never written down. While a scene is fresh, the prompt
carries a short block of plain lines headed `[WHERE YOU ARE: live, from ELI VR]`, added in
`eli/kernel/stages/prompt_assembly.py` beside the ambient-vision block. With nobody there it adds
nothing.

Plain text on purpose: it reads the same to any model.

## Building (`eli/world/building.py`, `eli/world/composer.py`)

ELI can put things in its rooms, move and change them, take them away, and, in the one room that is
its own, set the sky, the ground, the light and the mist.

- **The vocabulary is small and closed.** Ops: create, move, change, remove, room, note, undo.
  Sixteen shapes, five skies, eight grounds, named colours or `#rrggbb`. Positions are metres from
  the middle of the room's floor, `[right, up, forward]`. What comes in loosely worded is put right
  where it can be, and refused with the reason where it cannot.
- **Every edit is a world action.** It goes through `EliWorldAutonomyEngine.ingest_event` as a
  `world_edit` event, is classified by `EliWorldPolicy`, written to the provenance ledger with who
  asked, and applied to the world state. `alter_object` is a new free action beside `alter_room`.
- **A name is not an action.** What the policy reads is what an action does. What a thing is called
  and what it means ride in the event, so a thing may be called "Cloud" though the policy blocks the
  word in an action. The ledger keeps both.
- **ELI's own room is beside the house.** It lives in `EliWorldState.spaces`, not among the nine
  rooms, so the house view and the nine-room prompt text are unchanged.
- **Undo.** Each batch records how to take it back. `undo` takes back the last batch not yet taken
  back; a thing removed comes back where it stood.
- **Limits.** Twelve edits a batch, sixty things a room, nine metres from the middle.

### From words, with any GGUF

`composer.compose(text, scene)` makes one call to the loaded model through `generate_json`, with a
grammar for exactly the answer wanted: what ELI says, then a list of edits. The model cannot leave a
field out, invent an op, name a shape that does not exist or change a thing that is not there. No
model-specific tool format is used.

A small model still mishears, so the plain cases are not left to it:

- "undo" is done without asking a model;
- which ops are possible is decided from the sentence (a sentence about a thing that is there
  cannot make a new one; "a blue orb" makes one);
- a colour, shape or size named outright is what the thing gets;
- "behind me", "to my left", "next to the lantern", "in the middle" are computed from the scene;
- "the orb" with no orb there is answered without a model;
- a new thing is never put where the user stands or inside another thing;
- if nothing changed, ELI says so, whatever the model claimed.

Unasked (`compose(None, ...)`), ELI does exactly one thing in its own room. The op, the shape and
the place are chosen in code so that a small model does not repeat itself; the name, the colour and
the meaning are ELI's.

`generate_json` takes an optional `grammar` for this, and parses with `strict=False`.

## ELI's mind (`eli/world/mind.py`)

No model is assumed. `status()` says whether a model is in memory, which one (or which would be
used if none is), whether anybody chose it or it is only the one used last, and which models ELI
has, by `hardware_profile.discover_models` over ELI's models folders. `choose(name)` loads one the
way ELI's own settings do (`runtime_settings.apply_runtime_settings`), so it is the same choice ELI's
window makes. Names go over the wire, never paths.

Opened from ELI's window, a bundle that needs the server is served by that window's own process
(`eli.bundles.spawn` starts the server there, on loopback, and stops it when the bundle closes). So
the model already loaded in the window answers and nothing is loaded twice.

## Anything at all

A thing's form is a `shape` by name, or `parts`: up to twenty-four of box, ball, cylinder, cone,
torus, capsule, prism, lathe (a `profile` of `[radius, height]`), extrude (an `outline` of
`[right, forward]`), tube (a `path` of `[right, up, forward]` and a `radius`), text. Each part has
`pos`, `rot`, `size`, `colour` and may have `metal`, `rough`, `glow`, `clear`, `spin`, `bob`,
`pulse`. A room's mood may be painted: `sky_top`, `sky_horizon`, `stars`, `sun` (height, bearing,
colour, size), `ground_colour`, `terrain`, `mist_colour`.

`composer.capable()` decides whether the loaded model is offered this: three billion parameters and
up, by the GGUF's own metadata, or `ELI_WORLD_FREEFORM=1|0`. A smaller model is held to the named
shapes by the grammar.

## Of ELI's own accord (`eli/world/urges.py`)

Nothing here has a clock of its own, and nothing chooses for ELI.

- `urges.consider(state, event)` runs for every event entering ELI's world. An event that is a moment
  of ELI's own and has words (a `reflection` such as the synthesised insight, `task_opened`,
  `task_closed`, `improvement_proposal`) is kept in `spaces.own_room.urges`, twelve at most.
- `urges.act_when_free()` is called from the proactive loop after the insight step. It does nothing
  unless a model is already in memory and `affect_policy.should_defer_proactive` does not ask ELI to
  hold back; the loop has already checked that nobody is mid-request.
- `composer.compose(None, scene, about=urge)` gives ELI `mindset()`: its persona text, cached
  insight, goal titles, recent semantic memories, tone and awareness values, each line present only
  if the record is. Then the moment. All ops are open and the edits may be empty.
- What ELI makes carries `links` to the moment, a `reason` that begins "Because:", and a journal
  entry. With no meaning of ELI's own, the moment's words are the meaning.

`POST /v1/world/compose` answers the oldest urge, or with none, asks ELI from its mindset alone.

## ELI's records in the rooms (`eli/world/shown.py`)

Version 2 of the feed adds, to the objects, things that are ELI's records and not things ELI built:
`kind: "memory"` in `memory_archive` (its recent semantic memories, each with its text) and
`kind: "goal"` in `upgrade_bay` (the goal titles ELI holds). Read from `get_memory()` and
`goal_store.summarize_goals()`, cached for thirty seconds.

## Not there yet

- Only ELI's own room can be given a new sky; the nine rooms are as they are.
- No model large enough to be offered free parts has been tried; they have been drawn from edits
  sent by hand.
- Claims and open conflicts (`eli/memory/claims.py`) are not yet shown on the Evidence Wall or in
  the Anomaly Room, and a conceded belief does not yet raise an urge.
- Pictures of ELI's own (a sky or a surface from the image engine) are not wired in.
- The scene is held in the server process. A turn taken in another process does not see it.
- One world, one user: a second person's client would report into the same scene.

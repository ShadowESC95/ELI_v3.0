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
| 2 | All of 1, and how each object looks and where it stands, ELI's own room and every room of the house ELI has relit, what ELI last did and why; the scene; sentences; building; undo; the real things the body draws and ELI's own models; games; ELI's thoughts, books and evidence; pictures; kept records |

Version 2's capabilities, as `GET /v1/world/protocol` lists them: `world-state`, `room-events`,
`scene`, `say`, `building`, `undo`, `own-room`, `mind`, `props`, `pictures`, `models`, `games`,
`thoughts`, `books`, `evidence`, `kept-records`.

## Routes

All on the local server, behind its own token and role checks. Loopback callers are the owner.

**What a caller who is not the owner gets.** The message is made for its caller. The owner's
carries everything made of the owner's records: memories, claims, goals, results, the agenda, the
evidence, the books, what happened lately and the log. A member (the server's roles,
`docs/SERVER_AND_WEB_APP.md`) gets the house, the models and their own thoughts; those lists are
empty in a member's message, and the owner's pictures and books are not found for them.

| Route | What | Version |
|---|---|---|
| `GET /v1/world/protocol` | Versions and capabilities | any |
| `GET /v1/world/state?version=N` | One snapshot | 1 |
| `WS /v1/world/ws` | The snapshot again whenever it changes; `hello`, `vr_event` from the client | 1 |
| `POST /v1/world/event` | A listed client event (a session started, a room entered) | 1 |
| `POST /v1/world/scene` | What the user and ELI can see now | 2 |
| `POST /v1/world/say` | A sentence from inside the world: a turn of conversation, or building | 2 |
| `POST /v1/world/edit` | The user moving or changing a thing by hand | 2 |
| `POST /v1/world/compose` | ELI making something unasked: of a moment of its own, in any room it chooses | 2 |
| `POST /v1/world/undo` | Take back the last change made by building | 2 |
| `GET /v1/world/mind` | Which model is loaded, which would be used, which ones ELI has | 2 |
| `POST /v1/world/mind` | Choose ELI's model (admin) | 2 |
| `POST /v1/world/played` | The body saying how a game ELI set went (capability `games`) | 2 |
| `POST /v1/world/body` | The real things the body can draw, for ELI to place (capability `props`) | 2 |
| `GET /v1/world/picture/N` | A picture ELI took or looked at for the user, by its ledger row (capability `pictures`); owner only | 2 |
| `GET /v1/world/model/ID/F` | A file of a model ELI brought in, as the message's `models` lists it (capability `models`) | 2 |
| `GET /v1/world/thoughts?room=&kinds=` | ELI's thoughts lately, the caller's own (capability `thoughts`) | 2 |
| `GET /v1/world/book/ID` | One of ELI's books whole, with its sections (capability `books`); owner only | 2 |

**Kept records.** A client that asks for the capability `kept-records` in its hello keeps the
records it is sent. From then on a record (`objects`) that is the same as when it was last sent
over that link goes as `{"id": ..., "kept": true}` and the client shows the one it holds; a new or
changed record goes whole (`spatial_feed.lighten`). The first message after the hello carries
every record whole. A client that does not ask gets every record whole every time. A hello may
also say the most one message may take (`max_message`, bytes); ELI's books fill what the rest of
the message leaves of it. `tests/fixtures/world_feed_v2_contract.json` holds the shape of the
version 2 message a client can rely on.

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

### The headset and the cards

ELI VR and the runtime driving the headset draw on the same card ELI loads its model onto, and take
more as they go (a room's textures, the desktop's layer, the compositor's swapchains). A plan made
from what was free before they started left them nothing: the first reply failed inside CUDA and
took ELI with it. So, while a body is in the rooms, what its programs hold on each card is measured
(`eli/core/load_plan.py`, `note_xr_footprint`, no more often than a scene stays fresh) and the most
seen on each card is kept in `runtime/gpu_xr.json`; a plan made while a body is here or on its way
(`eli/bundles.body_expected`) leaves that much free on the card, less what they hold already, and
says so in its notes. Started as a bundle from ELI v3, the chat model is loaded only once ELI VR is
in the room, so the plan sees what the headset holds; and right after any load one full batch is
run through the model, so its own working buffers are at their size before anyone else takes the
card. Nothing about the headset's size is assumed: until it has been measured on a machine, nothing
is kept, and the warm-up alone protects ELI's own buffers.

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
- **The rooms of the house take ELI's light.** A room of the house keeps its own sky, ground and air;
  ELI may give it a light (`building.HOUSE_LOOK`), which its glowing fixtures wear in ELI VR. It is
  kept in `rooms[room].mood`, read with `building.look_of`, and sent in the feed's `spaces` beside
  ELI's own room (a room still as it was built is not sent). Anything else asked of one is refused
  with the reason.
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

Unasked (`compose(None, ...)`), every op is open, nothing is chosen for ELI, and the edits may be
empty. Without a moment of its own (the BUILD button), what ELI makes is made in its own room. With
one, the prompt names every room of the house with its purpose, and a new thing or a change of light
may carry `"room"`: the grammar holds a room of the house to a light.

`generate_json` takes an optional `grammar` for this, and parses with `strict=False`.

## ELI's mind (`eli/world/mind.py`)

No model is assumed. `status()` says whether a model is in memory, which one (or which would be
used if none is), whether anybody chose it or it is only the one used last, and which models ELI
has, by `hardware_profile.discover_models` over ELI's models folders, the folders of the models its
settings name (a person who keeps their models together is offered all of them once one is chosen)
and any folder named in `ELI_MODEL_DIRS` or the `model_dirs` setting (separated as PATH is).
`choose(name)` loads one the way ELI's own settings do (`runtime_settings.apply_runtime_settings`),
so it is the same choice ELI's window makes. Names go over the wire, never paths.

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
  `task_closed`, `improvement_proposal`, and `error_detected`: a failure the executor logged, in the
  words of what went wrong) is kept in `spaces.own_room.urges`, twelve at most. The World panel's
  buttons for trying events out are not moments.
- `urges.act_when_free()` is called from the proactive loop after the insight step, at most once a
  pass (each act is a model call, and a reply sent meanwhile would wait for it). It does nothing
  unless a model is already in memory and `affect_policy.should_defer_proactive` does not ask ELI to
  hold back; the loop has already checked that nobody is mid-request.
- `composer.compose(None, scene, about=urge)` gives ELI `mindset()`: its persona text, cached
  insight, goal titles, recent semantic memories, tone and awareness values (evidence confidence and
  uncertainty are the running means of the sitting's calibrated chances that ELI's replies were right,
  and their complements: `eli/cognition/epistemics.py`), each line present only
  if the record is. Then the moment. All ops are open and the edits may be empty.
- What ELI makes carries `links` to the moment, a `reason` that begins "Because:", and a journal
  entry. With no meaning of ELI's own, the moment's words are the meaning. Where it goes is ELI's
  choice (see above).

Every action the executor runs reaches the world through `fire_tool_result_event`: a tool that worked
as `task_completed` (which eases repair pressure and doubt), a failure as `error_detected` with what
went wrong. A chat reply is not a tool.

`POST /v1/world/compose` answers the oldest urge, or with none, asks ELI from its mindset alone.

## Games (`eli/world/games.py`)

A game is things ELI made with rules attached. "Let's play a game", "set up a catch game", "hide
something for me to find" on `/v1/world/say` (or `PLAY_GAME` from the desktop, when the sentence
names the rooms or a body is present) has `games.compose(text, scene)` ask the model once, held by a
grammar to one game: a name, a kind, a goal, one to four kinds of piece (a shape or a real thing,
colour, size, glow, tag), for sorting a mat per tag, a count, seconds, a pace, for targets a motion,
and the lines ELI says at the start, a hit, a miss and the end. The prompt carries the room, where
the user stands, what is on ELI's mind (`composer.mindset`) and how the last five games went. A kind
the user named holds the grammar to it.

- **Five mechanics**: `targets` (pieces come up one after another; hit them), `catch` (they come
  down; catch them), `sort` (pieces of two or more tags onto the mat with their tag), `order` (the
  lights flash in a sequence; repeat it), `hide` (one piece hidden each round; find it).
- **Set in the room as things.** `games.layout` puts each piece where it starts (targets and catches
  round the user inside the room, a sorting row before the user with the mats to either side, an
  arc of lights, a hidden piece away from the user), and the game goes through `building.apply` as
  creates: the game itself as an object of type `game` with the rules in its metadata (`game`,
  `status`), each piece a built thing with `piece` (game, role, tag, index) in its metadata. Every
  one is a world action judged by the policy. The batches are `final`: an undo passes over them.
  The feed carries `game` and `status` on the game object and `piece` on each piece.
- **The body runs it** (ELI VR's `eli_play.gd`): motion, hits, catches, drops, the sequence, the
  gaze; how many are up at once, how fast they move and how long each stays follow from count,
  seconds and pace and the room, not from a table.
- **How it went** comes back on `POST /v1/world/played` (score, misses, rounds, seconds,
  abandoned). `games.played` keeps the result on the game object (status `done`), retires the
  pieces and the game through the policy and then drops the pieces from the state (a game's pieces
  are not kept; the last forty finished games are), writes the game in the journal, stores a
  memory, and fires `game_played`, which `urges` keeps as a moment of kind `play`. `games.record`
  lists the finished games newest first, and `best_so_far` says whether a score beat every earlier
  one of its kind; the answer tells the body so.
- **Stopping.** "Stop the game" with a body present marks the game `stopping`, and the body posts
  how far it got; with none, it is finished here as abandoned. A new game set in a room gives the
  old one up the same way.
- **Limits of the protocol**: four kinds of piece, twenty-four pieces, ten seconds to ten minutes,
  as many pieces as the room has room for.

## ELI's records in the rooms (`eli/world/shown.py`)

Version 2 of the feed adds, to the objects, things that are ELI's records and not things ELI built:
`kind: "memory"` in `memory_archive` (its recent semantic memories, each with its text),
`kind: "goal"` in `upgrade_bay` (the goal titles ELI holds), `kind: "claim"` on `evidence_wall`
(each belief ELI holds about the user now, as `claims.describe` words it, amber when contested,
brighter the surer ELI is) and `kind: "conflict"` in `anomaly_room` (each open disagreement, with
`claims.conflict_question` as its meaning). Read from `get_memory()` and
`goal_store.summarize_goals()`, cached for thirty seconds; claims and conflicts show as many as the
room holds (`building.MAX_PER_ROOM`), newest and surest first.

Version 2 also carries what is on ELI's mind (`mind`, from `shown.thoughts()`: its latest insight,
the moments waiting in `urges`, the goal it holds first and its tone, each only if the record is
there), how many moments are waiting (`waiting`), and whether ELI is free to make something of one
(`free`: `urges.free()`, a model already in memory and nobody's mood asking ELI to hold back, the
same test ELI's own background loop uses, read as often as the records). A body may let the thoughts
rise from ELI, and may ask `POST /v1/world/compose` while someone is there to see what ELI makes.

## Not there yet

- The nine rooms keep their own sky and ground. ELI relights them and puts things in them, but cannot
  move or change what they were built with.
- No model large enough to be offered free parts has been tried; they have been drawn from edits
  sent by hand.
- A conceded belief does not yet raise an urge, and a conflict cannot yet be settled from inside the
  world (the user answers it in conversation).
- Pictures of ELI's own (a sky or a surface from the image engine) are not wired in.
- The scene is held in the server process. A turn taken in another process does not see it.
- The first session on a machine has no measurement of the headset's footprint yet: ELI's own
  buffers hold, but the headset can still run short on the card until the next start.
- One world, one user: a second person's client would report into the same scene.

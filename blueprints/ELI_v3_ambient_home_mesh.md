# ELI v3 — The Ambient Home Mesh

*Proposals for turning ELI's home system from a single-brain, room-blind, single-point-of-
failure design into a **sovereign, distributed, self-healing, room-aware home intelligence** —
where the house stays smart even if the main PC dies, media and control are per-room, and one
continuous ELI follows you through the house. Frontier-grade; most of it doesn't exist yet.*

Written July 2026, grounded in v2's own-MQTT home stack + the current edge-AI/Matter/Thread
landscape.

---

## 1. The gap (be honest about v2)

v2's home system is real — self-hosted MQTT, rooms, scenes, time/sunset/device automations,
voice control. But it has three structural limits:

1. **One brain, one point of failure.** All intelligence lives on the PC. PC down = the home
   goes dumb. No graceful degradation.
2. **Room-blind.** "Turn on the light" has no spatial model — no notion of *which* room you're
   in, no per-room speaker targeting, no follow-you.
3. **Centralised, not a mesh.** Devices talk to one broker on one machine. No redundancy, no
   edge reflexes, no local survival.

v3 fixes all three by making ELI a **tiered, distributed mesh** — and in doing so opens
capabilities nobody ships.

---

## 2. The vision

**A sovereign home nervous system.** Cheap always-on nodes in each room handle *reflexes*
(local device control, wake-word, timers, cached scenes, presence) on a tiny edge model. The PC
is the *cognition* (the 35B, memory, autonomy, deep reasoning). They form an encrypted local
mesh with **no cloud and no single point of failure** — if the PC drops, the house keeps
running at reflex level and gracefully re-acquires deep cognition when the PC returns. Media and
control are **per-room and presence-aware**, and one **continuous ELI** follows you from room to
room, mid-conversation, mid-memory.

Cloud assistants can't do this (they're centralised + cloud-dependent). Home Assistant can't do
this (one hub, no cognitive tiering, no continuous personal AI). This is a category ELI can
define.

---

## 3. Architecture — the tiered brain

Three tiers of intelligence, degrading gracefully downward:

**Tier 0 — Device firmware (survives everything).**
Matter + Thread devices (lights, sensors, locks, plugs) keep their last state and run *device-
local* fallbacks even if the whole mesh is down. Thread mesh gives **sub-20ms** command latency
and battery-device support. Matter works offline by default. *This is the floor: the lights
never stop working.*

**Tier 1 — Room nodes / "reflexes" (survive PC-down).**
Cheap always-on satellites in each room — **ESP32-S3** class for mic+speaker+presence, or a Pi
for a beefier room hub. Each runs a **tiny edge model** (Qwen-0.5B-class NLU + SenseVoice/faster-
whisper STT + Piper TTS, the proven 2026 offline voice stack) and a local device-control agent.
They handle: wake-word, local commands ("lights off"), timers, cached scenes, presence sensing,
and multi-room audio endpoints. *They keep the home smart-enough without the PC.*

**Tier 2 — The cognition brain (the PC).**
The 35B, full memory, autonomy, deep reasoning, the learned user-model, the world-model. Room
nodes escalate hard queries up to it ("plan my evening"; "what did I say about the boiler?").
*The deep mind, when it's available.*

**Tier 3 — Backup brain (optional resilience).**
A second capable machine (mini-PC, or the beefiest node) that can be **promoted to acting-brain**
with a smaller model if the PC is down for a while — so even deep-ish cognition survives a
primary outage.

**The broker moves off the single PC.** Run the MQTT broker (and a Thread border router) on an
always-on node, not the PC — so the *nervous system* survives even when the *brain* sleeps.

---

## 4. Failover & resilience — "what if the main PC goes down?" [Pioneer]

This is the headline idea, and nobody ships it. **Graceful cognitive degradation with brain
handoff:**

- **Heartbeats + health.** Every node heartbeats on the mesh. The brain (PC) publishes "I'm
  alive + capable." Nodes watch it.
- **On PC-down:** nodes detect the missing heartbeat and **fail over to reflex mode** — local
  device control, timers, scenes, and basic voice keep working on the edge models. The home
  *degrades, it doesn't die.* ELI on the phone shows "running in reflex mode — deep reasoning
  offline."
- **Brain election.** If a Tier-3 backup exists, the mesh **elects an acting-brain** (a simple
  priority + capability vote) and it loads a smaller model to cover deep-ish requests until the
  PC returns.
- **State sync + handoff.** All state (device states, pending timers, the day's episodic memory)
  is CRDT-synced across nodes, encrypted, peer-to-peer. When the PC returns, cognition **hands
  back** cleanly and memory reconciles — no lost timers, no forgotten context.
- **Sovereign, no cloud.** The entire failover is local. There is *no* single point of failure
  and *no* external dependency. Your home is yours even when your main machine is off.

*Why it's frontier:* HA is one hub (hub down = home down at the smart layer); Alexa/Google are
cloud (internet down = dumb). A **self-healing, tiered, cloud-free personal-AI home mesh with
cognitive degradation and brain election** is genuinely un-built.

---

## 5. Room-aware audio & media — "play X on the kitchen speaker" [Adopt→Pioneer]

The concrete answer to your Spotify/room-speaker question:

- **Per-room targeting.** Each room node is a synced audio endpoint via **Snapcast** (whole-home
  sync) + **Spotify Connect** / DLNA / Chromecast / AirPlay per room. "Play *Juicy* on the
  kitchen speaker" resolves the room → routes to that endpoint. "Play everywhere" → synced Snap
  group. Per-room volume, grouping, and hand-off are first-class.
- **Follow-me audio [Pioneer].** ELI knows which room you're in (presence sensors + phone BLE +
  node mics). Say "follow me" and the music **hands off room-to-room as you walk** — the kitchen
  fades as the hall picks up. Presence-aware audio handoff is a genuinely new, delightful
  behaviour almost nobody does locally.
- **Spatial intent.** "Turn it down in here" / "make it cozy here" resolve *"here"* from your
  location — the room graph + presence, not a fixed device name.
- **Voice from any speaker.** Any room node can *speak* — briefings, timers, replies come out of
  the room you're actually in.

## 6. Spatial device control — bedroom light, sitting-room light, per room [Adopt]

- **The home graph.** ELI holds a model of the house: rooms, devices, adjacency, and typical
  occupancy. "Sitting-room light off," "bedroom lamp to 30%," "kitchen fan on" all resolve
  through the graph — and Matter/Thread deliver them in sub-20ms.
- **Room + presence resolution.** Ambiguous commands ("lights off") resolve to *your current
  room*; explicit ones ("bedroom light") to the named room. Scenes become room-scoped ("movie
  mode" adapts to the room you're in).
- **Verifiable physical grounding [Pioneer].** Because ELI has sensor feedback, it *confirms* the
  light actually changed (power/state sensor) rather than assuming — the same verifiable-grounding
  moat as the desktop, now physical. "I turned it on" becomes *checkable*, not hopeful.

---

## 7. The frontier moonshots (doesn't exist yet)

Where ELI's mesh could plant flags first:

1. **Continuous cross-room ELI [Pioneer].** One conversation, one memory, one persona that
   *follows you through the house.* Start a thought at the kitchen node, finish it in the sitting
   room — same ELI, mid-sentence, no re-waking, no context loss. Nobody has a single continuous
   personal AI that spans rooms and devices as one mind.
2. **A learned world-model of your home [Pioneer].** Not rules — a predictive model over
   (room, time, occupancy, device-state, your actions). It *anticipates*: "you dim the sitting
   room ~22:40 and start a podcast — do it?"; and it *detects anomalies*: "the garage has been
   open two hours, that's unusual." The physical-world twin of the desktop world-model.
3. **Ambient sovereign autonomy [Pioneer].** The proposal-only autonomy loop (approval-gated,
   from the main roadmap) applied to the *home*: ELI proposes automations grounded in real
   patterns, learns from your accept/reject, and gets better at running your house — privately,
   with nothing leaving the mesh.
4. **Presence + activity fusion [Bet].** Fuse node mics, cameras (local vision — InternVL-class),
   presence sensors, phone BLE, and calendar into a live occupancy/activity model that drives
   everything above — who's home, in which room, doing what, so ELI acts *appropriately per room*.
5. **Whole-home "moods" that reason [Bet].** "Wind-down mode" isn't a static scene — ELI reasons
   it per room, per person, per time (lights warm + volume down + notifications held + doors
   checked), and adapts as the evening progresses.
6. **Guest/multi-person awareness [Bet].** RBAC (already in v2) extended physically: ELI knows
   whose voice/presence it is and personalises + gates per person and per room.

---

## 8. Hardware (what it actually takes)

Deliberately cheap and off-the-shelf — this is *feasible*, not sci-fi:

- **Room nodes:** ESP32-S3 satellites (mic + small speaker + presence) — a few € each; or a
  Raspberry Pi 5 for a beefier room hub / Snapcast server.
- **Mesh spine:** a Thread border router + Matter controller on an always-on node (Pi-class).
- **Devices:** any Matter/Thread lights, plugs, sensors, locks (vendor-agnostic, offline-capable).
- **Audio:** Snapcast clients on nodes + existing speakers; Spotify Connect / DLNA endpoints.
- **Brains:** the existing PC (35B) as cognition; optionally a mini-PC as Tier-3 backup brain.

No cloud account anywhere. Everything above runs on your LAN.

---

## 9. Phasing (don't boil the ocean)

| Phase | Ships | Value |
|-------|-------|-------|
| **H0** | Matter/Thread adoption + the **home graph** (rooms/devices/adjacency) | vendor-agnostic devices + spatial resolution ("bedroom light") |
| **H1** | **Snapcast multi-room audio** + per-room targeting (Spotify/DLNA) | "play X on the kitchen speaker" — the thing you asked for |
| **H2** | **First room node** (ESP32-S3): local wake-word + reflex device control + presence | the edge tier begins; a room survives PC-down for basics |
| **H3** | **Failover mesh** — broker on a node, heartbeats, reflex-mode degradation | the house stays smart when the PC sleeps |
| **H4** | **Follow-me audio + continuous cross-room ELI** | the delightful, nobody-else-has-it layer |
| **H5** | **Home world-model** — anticipation + anomaly detection + brain election | the frontier: a home that predicts, heals, and reasons |

Start H0/H1 (spatial + multi-room audio) — it's the immediate felt win and needs no new brains.
The mesh/failover (H2–H3) is the resilience leap. H4–H5 are the moonshots.

---

## The one-line thesis

Everyone else's smart home is either **one fragile hub** (Home Assistant) or **the cloud**
(Alexa/Google). ELI v3's is a **sovereign, self-healing mesh** — smart in every room, alive even
when the main brain sleeps, media and control that know *where you are*, and one continuous
private mind that follows you through the house. That's not a better smart home. It's the first
**ambient personal AI that owns your home without owning your data** — and it's exactly the kind
of thing only a local-first system can be.

---

### Sources
- [M5Stack CES 2026 — offline edge-AI smart home (Qwen-0.5B + SenseVoice + InternVL3)](https://community.m5stack.com/topic/7996/voice-controlled-smart-home-with-edge-ai-no-cloud-needed-m5stack-ces-2026)
- [Home Assistant AI Voice with a Local LLM — 2026 stack (faster-whisper + Piper + Qwen3)](https://botmonster.com/smart-home/build-private-local-ai-voice-assistant-2026/)
- [Matter and Thread Explained — 2026](https://datawiresolutions.com/blog/matter-thread-explained-2026)
- [Edge-AI-Driven Matter Controller for Next-Gen Smart Homes (MosChip)](https://moschip.com/blog/device-software-engineering/edge-ai-driven-matter-controller-for-next-generation-smart-homes/)
- [Matter 1.3 & Thread interoperability in 2026 (Beyond Tomorrow)](https://beyondtmrw.org/article/matter-13-and-thread-smart-home-interoperability-in-2026)
- [Matter innovations at CES 2026](https://matter-smarthome.de/en/products/the-matter-innovations-at-ces-2026/)

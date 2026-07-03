# ELI v3 — Master Roadmap

*The single source of truth for ELI v3. Consolidates the vision, the frontier upgrade plan,
and the ambient-home-mesh proposal into one coherent document: where ELI is, where the 2026
frontier is, the concrete proposals, the moonshots nobody has built, and a unified delivery
plan. v2 is the proven baseline; v3 is where it becomes an architecture.*

Compiled July 2026 from the v2 capability inventory + the current research landscape.
Tags: **[Adopt]** proven · **[Bet]** emerging, worth the risk · **[Pioneer]** not built
anywhere — ELI's unique shot.

### Contents
- **Part I — Foundations:** the thesis, the moat, the current inventory, the architectural spine
- **Part II — Frontier upgrades (16 subsystems):** current → frontier → proposal → mechanism → example
- **Part III — New organs:** capabilities v2 lacks entirely
- **Part IV — The ambient home mesh:** distributed, self-healing, room-aware home intelligence
- **Part V — The moonshots:** the not-yet-built category ELI can define
- **Part VI — Delivery:** unified sequencing, metrics, de-risking, and "a day with v3"

---

# Part I — Foundations

## 1. The thesis: occupy the local frontier

Every cloud lab is racing toward "an agent that knows you and acts for you." They hit a wall
ELI doesn't: **they can't have your data, your machine, or your continuity.** Privacy-
preserving *per-person* learning, sovereign memory, and true desktop + home embodiment are
things OpenAI/Google can only offer diluted, aggregated, and rented.

ELI is the inverse — it already lives on your machine, offline by default, with your entire
context local. So v3's strategy is not "catch the frontier." It's: **claim the slice of the
frontier that structurally requires being local, private, and embodied.** Several directions
below are things ELI can do that the cloud *fundamentally cannot do per person.* That is the
lane, and it is defensible because being in it is expensive and un-monetisable for a cloud
giant.

The frame for every proposal: *what does being local let ELI do here that a cloud agent can't?*
"Nothing" → an Adopt (table stakes). "A lot" → a Pioneer (the moat).

## 1.5 The moat, concretely — v3-local vs the cloud

| Capability | What the cloud offers | What ELI-local can do | Why the cloud structurally can't |
|-----------|----------------------|----------------------|----------------------------------|
| **Personalisation** | aggregated, opt-in, rented | a model of *you*, learned from your corrections, kept forever | per-user continual learning is a privacy + cost nightmare at scale |
| **Memory** | your chats on their servers | your whole autobiographical corpus, local, yours to delete | they can't hold everyone's life privately; you can hold one |
| **Embodiment** | screenshots you upload | live gaze + screen + OS + *home* control loop | no persistent, trusted access to your machine or house |
| **Grounding** | plausible guesses | *verifiable* checks against real system + device state | they don't have the machine/home to verify against |
| **Self-improvement** | one model for everyone | an adapter that learns *your* preferences privately | can't fine-tune per user, per correction, at scale |
| **Sovereignty** | "trust our policy" | a *provable* "nothing left the machine" | their business model is holding your data |
| **The home** | one cloud, one outage from dumb | a self-healing local mesh, smart even when the PC sleeps | centralised + cloud-dependent by design |

Where the right-hand column is full, that's where v3 pushes hardest — it's defensible *by
construction*.

## 2. Current capability inventory (what v3 inherits)

v2 already ships, end-to-end, a team's worth of surface — v3 upgrades all of it on a clean spine:
conversation + 5 reasoning modes + a 14-agent DAG bus; computer control (apps/windows/media/
**gaze-click**); files/docs/code (Report Builder, coding agent, IDE, examine-and-fix, test-gen);
perception (screen/OCR/VLM/PDF/CSV); memory (4 stores + FAISS + KG + profile); habits +
proposal-only **autonomy tick**; voice (whisper/piper/wake-word); self-improvement + eval
harness; image generation (procedural + SSD-1B); information (news/weather/web/telemetry);
scheduling + durable overnight jobs; **own-MQTT smart home** (rooms/scenes/automations); the
FastAPI web/phone server + PWA; a model-agnostic GGUF loader + VRAM smart-loader + LoRA; plugins
+ build-your-own-agent; and a security spine (offline failsafe, fail-closed gate, approval
engine, audit ledger, RBAC).

## 3. The architectural spine (build this first — everything rests on it)

**3.1 Planner / Effector split. [Adopt]** The 2026 consensus frontier-agent shape is a *Policy
Core* where every proposed action passes a *validation layer before execution*, producing an
audit trail — precisely the split. **Planner** (pure): `(intent, args, state) → Plan | Refusal`,
no side-effects, testable to 90%+. **Effector** (thin): `Plan → one side-effect → result`. A
`Plan` is a typed object (action, args, preconditions, expected effect, risk class, undo) and the
universal unit: planned → validated → approved → logged → replayed. Strangle the 14k-line
executor god-file one action family at a time. *Example:* "delete build/" → Plan{risk:
destructive, undo: trash} → the gate asks → the effector runs → the ledger records. Testable,
auditable, reversible.

**3.2 MCP-style declarative tools. [Adopt]** Capabilities become typed schemas + policy, not
hand-wired handlers; the 208 aliased actions collapse to a clean registry (~110 distinct). Adopt
an MCP/A2A-style protocol so ELI can expose *and* consume tools under one approval gate. The
sprawl becomes data, and an ecosystem becomes possible.

**3.3 The DAG as the universal substrate. [Adopt]** v2 already runs 14 agents on `dag.py`; v3 runs
*everything* on it — declarative, replayable, observable pipelines. Debuggable-by-construction at
2× scale.

**3.4 The model-router. [Adopt]** Route by task: a ~1–3B model for intent/routing, the 35B for
reasoning, the VLM on demand, an embedder always warm. Honours "keep the 35B" by spending its
latency only where it earns it. **The single biggest felt-speed win.**

---

# Part II — Frontier upgrades, subsystem by subsystem

*Each: today → frontier (2026) → proposal → mechanism → example → effort/risk.*

**4. Conversation & Reasoning.** Today: 5 heuristic modes, single model. Frontier: test-time
compute + **RLVR** + reflective self-checking. **[Adopt/Bet]** confidence-driven *verifiable*
self-checking — the planner proposes, ELI generates a check and **verifies it against real
machine state** before committing. *Example:* asked which config sets the port, ELI greps the
running process's actual env before answering, not guessing. **Verifiable local grounding is the
moat.** Effort med · risk low.

**5. Memory.** Today: strong substrate, present-tense, un-tuned fusion, no consolidation.
Frontier: benchmarked field — Mem0 (personalisation), Zep/Graphiti (temporal), Cognee (local
graph). **[Adopt]** temporal KG (bi-temporal edges — reason over *how you changed*); tune the
three fusion weights. **[Pioneer]** consolidation ("sleep"): nightly promote recurring episodics
to semantic memory, decay noise, distil a personal narrative. *Nobody ships an autobiographical
assistant memory — nobody else has all your data locally.* Effort med–high · risk med.

**6. Persona & Personalisation.** Today: emergent persona fed by a **regex→canned-phrase** profile
(hardcodes `active_project = "developing ELI"`). **[Pioneer]** a genuinely **learned user-model**
— a background LLM pass building an evolving model of you (interests/projects/tone/routines, each
with confidence + decay) feeding the emergent voice. Nothing about the *character* is scripted;
the *inputs* get deep. **The biggest jump in felt quality.** Effort med · risk low–med.

**7. Proactivity & Autonomy.** Today: proposal-only autonomy tick — right safety model, open loop
(habits detected but never proposed; FrontierAgent dormant). Frontier: self-improvers learning
from usage (Meta REA, Beam Tool Tuner). **[Bet]** close the loop: detect → propose → learn from
accept/reject → adjust. **[Pioneer]** **RLVR from your corrections** — every "no, not like that"
is a private preference pair training a small adapter; *unavailable to any cloud model.* **v3's
signature.** Effort high · risk med.

**8. Voice.** Today: turn-based whisper/piper/wake-word. Frontier: full-duplex, streaming,
prosody. **[Adopt]** streaming duplex (barge-in — talk over ELI and it stops), affect-aware TTS
(detect your stress, adapt pacing — ties to crisis-safety), streaming STT. Effort med · risk low.

**9. Vision & Screen Understanding.** Today: reads pixels, doesn't model them. Frontier: world
models (V-JEPA 2, Genie 3, Marble). **[Adopt]** continuous structured screen model (not glances).
**[Pioneer]** a **world model of your desktop** — a learned predictor over (screen, gaze, action)
that forecasts your next state ("build fails → you open the terminal → rerun"). *Nobody's built
it — nobody has ELI's gaze+screen+action loop.* Effort high · risk med–high.

**10. Embodiment: OS control & Gaze.** Today: reactive command→act + gaze-click. **[Pioneer]**
**anticipatory embodiment** — when the world model's confidence is high, pre-stage the predicted
action behind a light confirm; gaze rests on a failed build → "rerun?" appears where you're
looking. Acts *slightly ahead*, always reversible. A new interaction model. Effort high · risk med.

**11. Coding & Building.** Today: the strongest agent (plan→verify→repair), IDE, examine-fix,
test-gen. **[Adopt]** repo-scale context (codebase graph) + long-horizon execution on the DAG
("build feature X" → checkpointed overnight → reviewable diff + tests). **This agent is the
template every other agent should become.** Effort med · risk low.

**12. Self-Improvement.** Today: shallow scaffold. **[Bet]** real **RLVR self-tuning** — propose
a change, **run the eval harness** (verifiable reward), keep it only if metrics improve, log the
delta. **[Bet]** **continual personal adapter** — 35B frozen; a small always-updating LoRA (v2
trains LoRA), fed by episodic memory + preference pairs, refreshed nightly. Effort high · risk
med–high.

**13. Image & Media Generation.** Today: procedural + SSD-1B. **[Adopt]** newer local diffusion
(turbo/control) + image *editing* (inpaint/instruct); **[Bet]** local short-video as models
mature — same VRAM-swap. Effort low–med · risk low.

**14. Information & Web Research.** Today: synthesised news, gated search. **[Adopt]** a
**deep-research agent** on the DAG — plan → fan-out → cross-check → cited synthesis, honest about
going online. Effort med · risk low.

**15. Documents & Report Builder.** Today: evidence→outline→draft→revise. **[Adopt]** fuse with
the deep-research agent (§14) + temporal memory (§5) — reports grounded in *your* corpus + fresh
research + your history, with provenance. Effort low–med · risk low.

**16. Smart Home.** (Expanded fully in Part IV.) **[Bet]** a **predictive home** — learn routines
and *propose* automations grounded in real patterns, proposal-only + approval-gated. Effort med.

**17. Plugins, Agents & Extensibility.** Today: retrieval-level bus agents, bespoke protocol.
**[Adopt]** raise every agent to the coding-agent bar; agent *composition* on the DAG (research →
writer → file); an MCP tool marketplace under the approval gate. Effort med–high · risk low–med.

**18. Models & Inference.** Today: model-agnostic loader, smart-loader, LoRA, tier-scaling.
Frontier: **Mamba/SSMs** (linear-time), hybrids, **reasoning distillation**. **[Adopt,
opportunistic]** ELI's loader is built for this — slot a strong Mamba-hybrid or distilled small
model into the router as the fast brain. Near-zero integration cost. Effort low · risk low.

**19. Security, Privacy & Sovereignty.** Today: offline failsafe, fail-closed gate, approval
engine, audit ledger, RBAC — above the norm. **[Adopt]** encrypt memory + adapter at rest.
**[Pioneer]** **sovereign-agent guarantees** — formalise + *prove* the learned model of you never
leaves the machine and is yours to delete. Make sovereignty a verifiable property and the brand.

---

# Part III — Genuinely new capabilities (v2 has none)

Not upgrades — *new organs*, each strongly enabled by being local:
- **[Pioneer] Meeting & ambient-audio intelligence.** Private local transcription + summary +
  action-items of your calls — the cloud's killer app, but private because audio never leaves.
- **[Bet] "Ask my computer anything" / second brain.** Local RAG over your entire filesystem —
  code, docs, notes — queryable in plain English with citations to the real file.
- **[Bet] Multi-device continuity.** Your sovereign model syncs encrypted, peer-to-peer, between
  *your* machines — never a server.
- **[Bet] Life-admin agent.** Local parsing of statements/bills/subscriptions — the sensitive data
  people won't put in the cloud is ELI's home turf.
- **[Pioneer] Explainable action replay.** Every action is a logged Plan (§3.1), so ELI can replay
  and *explain* any past action — an auditable agent.
- **[Pioneer] Accessibility / assistive mode.** Gaze + duplex voice + anticipatory embodiment into
  a first-class hands-free computer for motor-impaired users — socially valuable, right in ELI's
  wheelhouse, rarely done locally.
- **[Bet] Portable "you" (digital-twin export).** Your sovereign model as an encrypted, exportable
  bundle — continuity + ownership, the opposite of cloud lock-in.

---

# Part IV — The ambient home mesh

*Turning ELI's home system from a single-brain, room-blind, single-point-of-failure design into a
**sovereign, distributed, self-healing, room-aware home intelligence.***

## 20. The gap
v2's home is real (own-MQTT, rooms, scenes, voice) but: **one brain** (PC down = dumb home);
**room-blind** (no spatial model, no per-room audio); **centralised** (no edge reflexes, no
redundancy). v3 fixes all three.

## 21. The tiered brain (architecture)
- **Tier 0 — devices (survive everything).** Matter + Thread lights/sensors/locks keep last state
  + device-local fallbacks even if the mesh is down. Thread gives **sub-20ms** latency; Matter
  works offline. *The lights never stop working.*
- **Tier 1 — room nodes / reflexes (survive PC-down).** Cheap always-on **ESP32-S3** satellites (or
  a Pi) per room running a **tiny edge model** (Qwen-0.5B-class NLU + faster-whisper + Piper — the
  proven 2026 offline stack) + local device control, wake-word, timers, cached scenes, presence,
  and audio endpoints. *Keep the home smart-enough without the PC.*
- **Tier 2 — cognition (the PC).** The 35B, full memory, autonomy, world-models. Nodes escalate
  hard queries up.
- **Tier 3 — backup brain (optional).** A mini-PC that can be **elected acting-brain** with a
  smaller model if the PC's out a while.
- **Move the broker off the PC** onto an always-on node — so the *nervous system* survives when the
  *brain* sleeps.

## 22. Failover & resilience — "what if the PC goes down?" [Pioneer]
Graceful cognitive degradation with brain handoff, and nobody ships it: heartbeats on the mesh →
on PC-down, nodes **fail over to reflex mode** (local control/timers/scenes/basic voice keep
working) → if a Tier-3 backup exists, the mesh **elects an acting-brain** → all state is CRDT-
synced encrypted peer-to-peer, so when the PC returns cognition **hands back** cleanly with no
lost timers or context. Entirely local — *no single point of failure, no cloud.* HA is one hub;
Alexa/Google are cloud; a self-healing tiered personal-AI home mesh with cognitive degradation is
un-built.

## 23. Room-aware audio & media — "play X on the kitchen speaker" [Adopt→Pioneer]
- **Per-room targeting:** **Snapcast** (whole-home sync) + Spotify Connect / DLNA / AirPlay per
  room; "play Juicy on the kitchen speaker," "play everywhere," per-room volume + grouping.
- **[Pioneer] Follow-me audio:** ELI knows your room (presence + phone BLE + node mics) and hands
  the music **room-to-room as you walk** — kitchen fades, hall picks up.
- **Spatial intent:** "turn it down in *here*" resolves "here" from your location.
- **Voice from any node:** briefings/replies come from the room you're in.

## 24. Spatial device control — bedroom light, sitting-room light [Adopt]
- **The home graph:** rooms + devices + adjacency + occupancy; "sitting-room light off," "bedroom
  lamp 30%" resolve through it, delivered in sub-20ms over Thread.
- **Room + presence resolution:** ambiguous "lights off" → your current room; explicit → named
  room; scenes become room-scoped.
- **[Pioneer] Verifiable physical grounding:** ELI *confirms* the light actually changed via sensor
  feedback — the verifiable-grounding moat, now physical.

## 25. Home hardware (cheap, off-the-shelf)
ESP32-S3 satellites (€ each) or Pi 5 room hubs; a Thread border router + Matter controller on an
always-on node; any Matter/Thread devices; Snapcast clients + existing speakers; the PC as
cognition + optional mini-PC as Tier-3. **No cloud account anywhere.**

---

# Part V — The moonshots (not built anywhere yet)

Where ELI can plant flags first, because it's local + private + embodied:
1. **The sovereign personal agent** — an autonomous assistant whose entire model of you never
   leaves your machine and is yours to delete. A **category**, not a feature.
2. **Autobiographical intelligence** — a coherent, evolving narrative of your life ELI *reasons
   over*: "you tried this in March and it stalled; here's why now differs."
3. **Private continual personalisation** — the first assistant that measurably gets better *at
   being yours* the longer you use it, from your corrections, on device, forever.
4. **Predictive embodiment** — desktop *and* home that anticipate: pre-stage the next app, surface
   the file, catch the error, dim the sitting room at 22:40.
5. **Continuous cross-room ELI** — one mind, one memory, one persona that follows you through the
   house, mid-sentence, no context loss.
6. **A learned world-model of your home** — anticipation + anomaly detection ("garage open 2
   hours") — the physical twin of the desktop world-model.
7. **Verifiable local grounding as a moat** — because ELI *has the machine and the home*, its
   answers and actions can be *checked against reality*. Lean all the way in.

---

# Part VI — Delivery

## 26. Unified sequencing

**Software track:**
| Phase | Ships | Why |
|-------|-------|-----|
| **v3.0 Foundation** | planner/effector, MCP tools, model-router | unblocks everything; biggest speed win |
| **v3.1 Knows me** | learned user-model, temporal memory + consolidation | the felt-quality leap |
| **v3.2 Acts well** | verify-then-act, repo-scale coding, deep-research + Report Builder | grounded, high value |
| **v3.3 Gets better** | closed autonomy + RLVR-from-corrections, self-tuning + adapter | the signature private work |
| **v3.4 Sees ahead** | continuous screen → desktop world model, anticipatory embodiment | pioneering; uses the gaze loop |

**Home track (parallel, hardware-paced):**
| Phase | Ships | Why |
|-------|-------|-----|
| **H0** | Matter/Thread + the home graph | vendor-agnostic devices + spatial resolution |
| **H1** | Snapcast multi-room audio + per-room targeting | "play X on the kitchen speaker" |
| **H2** | first ESP32-S3 room node (reflex control + wake-word + presence) | the edge tier begins |
| **H3** | failover mesh (broker on a node, heartbeats, reflex degradation) | the house stays smart when the PC sleeps |
| **H4** | follow-me audio + continuous cross-room ELI | the delightful, nobody-else-has-it layer |
| **H5** | home world-model + brain election | the frontier: predicts, heals, reasons |

Start **v3.0** (software foundation) and **H0/H1** (home spatial + multi-room audio) — the
immediate felt wins that need no new brains. Everything else earns its place after.

## 27. Success metrics
Median simple-turn latency ↓ 3–5× (router); a blind A/B where you can tell v3's persona from v2's
(learned model); self-check catches ≥X% of would-be-wrong answers (verifiable reward); proposal
acceptance rate *rises* over weeks (closed loop); planner surface ≥85% coverage + zero unlogged
actions; a proven "nothing left the machine" property; the home survives a PC-down at reflex level.

## 28. De-risking (honest failure modes)
- **Scope creep (the v2 wound, doubled at 2× size).** *Mitigation:* ship each phase before the
  next; a frozen feature line you refuse to cross. **Saying no is the feature.**
- **Continual-learning instability.** *Mitigation:* adapter-only (frozen base), eval-gated nightly
  refresh, one-click rollback via the ledger.
- **Autonomy / anticipation over-reach.** *Mitigation:* proposal-only + approval-gated + instant
  undo; confidence-gated, light-touch, always dismissable; start with *offer*, never *act*.
- **Maintainability at 2× LOC, solo.** *Mitigation:* the spine (§3) exists precisely so one person
  can carry it; everything eval-gated so regressions surface automatically.

## 29. A day with ELI v3 (the vignette)
Morning: the briefing is sharper — it *knows* you shifted to the v3 architecture (learned model).
You start the IDE; ELI warmed the coding model (router) and, from last week's pattern, pre-opened
the terminal (predictive embodiment). A build fails; before you look, "rerun?" appears where your
gaze lands. Midday: "research the best 8GB VLMs and write it up" — the deep-research agent + Report
Builder return a cited brief grounded in your hardware notes. You say "keep it terser" — that
correction becomes a preference pair. Evening: you walk to the kitchen and say "play some lo-fi
here" — it plays on the kitchen speaker and follows you to the sitting room; at 22:40 ELI offers to
dim the lights and hold notifications. Overnight: it runs the feature-build you queued, consolidates
the day into your narrative, and refreshes your adapter on your corrections. The PC reboots at 3am;
the house never noticed — reflex mode held the fort. Nothing left the machine. It's measurably more
*yours* than yesterday.

## 30. The one-line thesis
The cloud builds assistants that *rent* you intelligence and *borrow* your data. v3's opportunity is
the opposite corner of the frontier: an assistant that **owns nothing of yours, learns only for you,
runs entirely on your machine and your home, and gets better at being *yours* the longer you use it**
— with grounding, memory, reasoning, self-improvement, and a self-healing home that are frontier-grade
*because* they're local, not in spite of it. Most of it isn't built anywhere, and it requires being
exactly what ELI already is. That's a lane the giants can't drive in — and v3 takes it, in fewer,
sharper strokes, and out the door.

---

### Sources
- [Best AI Agent Memory Frameworks in 2026 (Atlan)](https://atlan.com/know/best-ai-agent-memory-frameworks-2026/) · [State of AI Agent Memory 2026 (Mem0)](https://mem0.ai/blog/state-of-ai-agent-memory-2026) · [AI Agent Memory Architectures (Zylos)](https://zylos.ai/research/2026-04-05-ai-agent-memory-architectures-persistent-knowledge/)
- [On-Device Agents / Adaptive Context (arXiv 2511.03728)](https://arxiv.org/pdf/2511.03728) · [World Models & Continual Learning 2026 (NextBigFuture)](https://www.nextbigfuture.com/2026/04/2026-is-breakthrough-year-for-reliable-ai-world-models-and-continual-learning-prototypes.html) · [Test-Time Reasoning & Reflective Agents (Hugging Face)](https://huggingface.co/blog/aufklarer/ai-trends-2026-test-time-reasoning-reflective-agen)
- [Self-Improving AI Agents 2026 (o-mega)](https://o-mega.ai/articles/self-improving-ai-agents-the-2026-guide) · [7 AI Breakthroughs 2026 (Adaline)](https://labs.adaline.ai/p/the-ai-research-landscape-in-2026) · [6 AI breakthroughs 2026 (InfoWorld)](https://www.infoworld.com/article/4108092/6-ai-breakthroughs-that-will-define-2026.html)
- [M5Stack CES 2026 offline edge-AI home](https://community.m5stack.com/topic/7996/voice-controlled-smart-home-with-edge-ai-no-cloud-needed-m5stack-ces-2026) · [Local AI Voice with HA 2026 (Botmonster)](https://botmonster.com/smart-home/build-private-local-ai-voice-assistant-2026/) · [Matter & Thread 2026 (Data Wire)](https://datawiresolutions.com/blog/matter-thread-explained-2026) · [Edge-AI Matter Controller (MosChip)](https://moschip.com/blog/device-software-engineering/edge-ai-driven-matter-controller-for-next-generation-smart-homes/)

---

*This master consolidates: `ELI_v3_vision_and_roadmap`, `ELI_v3_frontier_roadmap`, and
`ELI_v3_ambient_home_mesh`. Those remain as the detailed source docs; this is the single
authoritative overview.*

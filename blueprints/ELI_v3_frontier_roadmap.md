# ELI v3 — Frontier Roadmap & Proposals

*A full-system upgrade plan. For each of ELI's current capabilities (per the User Manual)
this maps: **where it is today → where the 2026 frontier is → the concrete v3 proposal →
effort/risk**. It closes with the moonshots nobody has built and a phased delivery plan.*

Written July 2026, grounded in the v2 capability inventory + the current research landscape.
Tags per proposal: **[Adopt]** proven, bring it in · **[Bet]** emerging, worth the risk ·
**[Pioneer]** not built anywhere — ELI's unique shot.

---

# Part I — Foundations

## 1. The thesis: occupy the local frontier

Every cloud lab is racing toward "an agent that knows you and acts for you." They hit a wall
ELI doesn't: **they can't have your data, your machine, or your continuity.** Privacy-
preserving *per-person* learning, sovereign memory, and true desktop embodiment are things
OpenAI/Google can only offer diluted, aggregated, and rented.

ELI is the inverse — it already lives on your machine, offline by default, with your entire
context local. So v3's strategy is not "catch the frontier." It's: **claim the slice of the
frontier that structurally requires being local, private, and embodied.** Several directions
below are things ELI can do that the cloud *fundamentally cannot do per person.* That's the
lane.

## 2. Current capability inventory (what v3 inherits)

From the manual, v2 already ships — genuinely — the following. v3 upgrades *all* of it:

- **Conversation & reasoning** — text/voice chat, 5 reasoning modes (quick→expert), a 14-agent
  bus on a DAG, escalation, grounded synthesis, anti-confabulation.
- **Computer control** — apps, windows, workspaces, volume, keyboard, mouse, media (Spotify/
  YouTube), **gaze/eye-control clicking**.
- **Files, docs & code** — file ops, notes, clipboard, doc conversion, the **Report Builder**
  (evidence→outline→draft→revise), a coding agent (plan→verify→repair), an IDE, code
  examination + repair, test generation, synthetic-data fabrication.
- **Perception** — screen reading/OCR, ambient screen watching, image description, PDF/CSV
  analysis, local vision (VLM).
- **Memory** — 4 SQLite stores + FAISS + knowledge-graph, an evolving user profile, session
  summaries, sourced recall.
- **Habits & proactivity** — learned routines, morning briefings, suggestions, a proposal-only
  **autonomy tick** (code-monitor + self-model refresh + goal autogenesis).
- **Voice** — STT (whisper), TTS (piper), wake-word, dictation, voice diagnostics.
- **Self-improvement** — improve/patch/upgrade/self-test cycles, approval-gated.
- **Image generation** — procedural + SSD-1B diffusion with VRAM hot-swap.
- **Information** — time/date/weather, synthesised news, web search, system telemetry.
- **Scheduling** — alarms, timers, calendar, pomodoro, durable overnight background jobs.
- **Smart home** — self-hosted MQTT device control, rooms, scenes, automations.
- **Server & phone** — FastAPI web app + PWA, LAN/HTTPS, model hot-swap dropdown.
- **Models** — model-agnostic GGUF loader, VRAM smart-loader, LoRA training, tier-scaling.
- **Extensibility** — plugins, **build-your-own-agent** flow, workspaces/projects.
- **Security** — offline-by-default socket failsafe, fail-closed command gate, approval engine,
  tamper-evident audit ledger, RBAC.

That's a team's worth of surface. The v3 job is to make each of these *frontier-grade* — and
to do it on an architecture that can carry it.

## 3. The architectural spine (build this first — everything rests on it)

Four foundations, in order. Nothing downstream is clean without them.

**3.1 Planner / Effector split. [Adopt]**
The 2026 consensus frontier-agent shape is a *Policy Core* where every proposed action passes
a *validation layer before execution*, producing an audit trail. That is precisely the split:
- **Planner** (pure) — intent + args + state → a *plan* (or refusal). No side-effects.
  Unit-testable to 90%+.
- **Effector** (thin) — plan → performs the one side-effect → structured result.
The plan becomes the universal unit: planned → validated → approved → logged → replayed.
Retire the 14k-line executor god-file behind it, one action family at a time.

**3.2 MCP-style declarative tools. [Adopt]**
Capabilities become **typed schemas + policy**, not hand-wired handlers. This kills the sprawl
(208 aliased actions collapse to a clean registry), makes the approval gate a per-schema
policy, and lets third-party tools drop in. Adopt an MCP/A2A-style protocol so ELI can both
*expose* its tools and *consume* others'.

**3.3 The DAG as the universal substrate. [Adopt]**
v2 already elevated `dag.py` to an orchestrator running 14 agents. v3 runs *everything* on it —
declarative, replayable, observable pipelines. Debuggable-by-construction at 2× scale.

**3.4 The model-router. [Adopt]**
Stop running one model for everything. Route by task: a tiny fast model for intent/routing,
the 35B for reasoning, the VLM on demand, an embedder always warm. Honours "keep the 35B" by
spending its latency *only* where it earns it. **Biggest felt-speed win in the whole plan.**

---

# Part II — Frontier upgrades, subsystem by subsystem

## 4. Conversation & Reasoning

**Today:** 5 heuristic reasoning modes; escalation on low grounding; single model.
**Frontier (2026):** *test-time compute* — inference-time scaling + **RL from Verifiable
Rewards (RLVR)**; models self-check and fix their own mistakes; "reflective agents."
**v3 proposal [Adopt/Bet]:** replace the fixed modes with **confidence-driven, verifiable
self-checking**. The planner proposes → ELI *self-critiques against real state* → repairs →
answers. Crucially, ELI has a reward the cloud often can't get: it **has the machine**, so it
can *verify* ("did the file open? did the build pass? is that the real GPU count?") instead of
guessing. Verifiable local grounding is a moat, not a mode.
**Effort:** medium. **Risk:** low — generalises the coding agent's loop you already ship.

## 5. Memory

**Today:** 4 stores + FAISS + KG, evolving profile, session summaries — strong substrate, but
present-tense, un-tuned fusion weights, no consolidation.
**Frontier (2026):** memory is now a *benchmarked field* — **Mem0** (personalisation),
**Zep/Graphiti** (temporal knowledge graphs), **Cognee** (local-first privacy graph reasoning,
closest in spirit to ELI).
**v3 proposals:**
- **[Adopt] Temporal knowledge graph** — bi-temporal edges (valid-time + ingest-time) so ELI
  reasons over *how you and your world changed*, not just what's true now. "You switched from
  approach A to B in March; here's why C is different."
- **[Adopt] Tune the fusion.** Three retrieval-fusion weights are still defaults. Measure on a
  recall benchmark and tune.
- **[Pioneer] Consolidation ("sleep").** A nightly pass: promote recurring facts to semantic
  memory, decay noise, distil episodes into a personal narrative. Nobody ships a genuinely
  *autobiographical* assistant memory — because nobody else has all your data locally.
**Effort:** medium–high. **Risk:** medium (retrieval quality regressions — gate with a memory
eval).

## 6. Persona & Personalisation

**Today:** emergent persona (great philosophy) fed by a **regex→canned-phrase** profile
extractor that literally hardcodes `active_project = "developing ELI"`. The character emerges
from a shallow input.
**Frontier (2026):** personalisation memory (Mem0) + continual per-user modelling.
**v3 proposal [Pioneer]:** a **genuinely learned user-model** — a background LLM pass that
extracts an evolving model of *you* (interests, projects, tone, relationships, routines), each
fact with a confidence and a decay, feeding the emergent voice. The persona gets dramatically
richer because it draws from real understanding — with nothing about the *character* scripted.
This is the single biggest jump in *felt* quality.
**Effort:** medium. **Risk:** low–medium. Pairs with §5 and §9.

## 7. Proactivity & Autonomy

**Today:** proposal-only autonomy tick (code-monitor + self-model + goal autogenesis) — the
safety model is *right*, but the loop doesn't close: frequency-habits are detected yet never
become rules, so they're never proposed; the FrontierAgent is dormant.
**Frontier (2026):** self-improving agents that learn *from production usage* (Meta's **Ranking
Engineer Agent**; Beam's **Tool Tuner** — RL from real use); reflective, goal-persistent agents.
**v3 proposals:**
- **[Bet] Close the loop:** detect a pattern → propose it → **learn from whether you accept** →
  adjust future proposals. Autonomy stays proposal-only + approval-gated, but it *improves* at
  anticipating you instead of re-detecting forever.
- **[Pioneer] RLVR from *your corrections*.** Every "no, not like that" / accept / reject is a
  verifiable reward signal — *private, personal, unavailable to any cloud model.* ELI learns
  your preferences on-device, from you, with nothing leaving. A self-improving agent the cloud
  literally cannot replicate per-person. **This is v3's signature.**
**Effort:** high. **Risk:** medium (needs guardrails — but the approval gate already provides
them).

## 8. Voice & Conversation UX

**Today:** whisper STT + piper TTS + wake-word — turn-based, functional.
**Frontier (2026):** low-latency **full-duplex** voice (barge-in/interruption), streaming
recognition, emotion/prosody, real-time conversation (the "voice-first agent" pattern).
**v3 proposals [Adopt]:**
- **Streaming duplex** — VAD-gated listening + interruptible TTS so you can talk over ELI and
  it stops, like a real conversation.
- **Prosody & affect** — emotion-aware TTS; detect *your* tone (stressed, rushed) and adapt
  pacing — ties into the crisis-safety guard.
- **On-device streaming STT** (faster-whisper streaming) for sub-second response.
**Effort:** medium. **Risk:** low.

## 9. Vision & Screen Understanding

**Today:** VLM description, OCR, ambient screen glances, PDF/CSV analysis — *reads* pixels,
doesn't *model* them.
**Frontier (2026):** stronger open VLMs; **world models** (V-JEPA 2, Genie 3, Marble) that
predict *environment state* rather than tokens; continuous scene understanding.
**v3 proposals:**
- **[Adopt] Continuous screen understanding** — not glances, a rolling structured model of
  what's on screen (windows, state, focus) the agent can query.
- **[Pioneer] A world model of *your desktop*** — a learned predictive model of your screen +
  app relationships + task flows. "You open the terminal after the IDE"; "this dialog = build
  failed." Gaze + screen + action history are the training signal, all local. **Predictive
  embodiment** for the desktop is a frontier nobody's built — because nobody else has ELI's
  gaze+screen+action loop.
**Effort:** high (the world model). **Risk:** medium–high — but the continuous-understanding
half is a clean, high-value first step.

## 10. Embodiment: OS control & Gaze

**Today:** apps/windows/mouse/keyboard/volume + **gaze-click** — reactive; you command, it acts.
**Frontier (2026):** vision-based computer-use agents (Self-Operating Computer lineage);
predictive/anticipatory interfaces.
**v3 proposal [Pioneer]:** couple the desktop world model (§9) to action: **anticipatory
embodiment** — pre-stage the app you're about to open, surface the file you're about to need,
catch the error before you see it, from a learned model of *your* machine. Gaze already tells
ELI where your attention is; v3 lets it act *slightly ahead* of you, with a light-touch
confirm. This is a genuinely new interaction model.
**Effort:** high. **Risk:** medium (over-eagerness — gate with confidence + easy undo).

## 11. Coding & Building

**Today:** coding agent (plan→verify→repair), IDE, code-examine-and-fix, test generation,
project scaffolding — already the *strongest, most frontier-shaped* agent in ELI.
**Frontier (2026):** repo-scale agentic coding, long-horizon multi-file tasks, verified patches,
agentic test-and-fix loops.
**v3 proposals [Adopt]:**
- **Repo-scale context** — codebase-graph indexing (symbols, imports, call-graph) so the agent
  reasons over the *whole* project, not a file.
- **Long-horizon task execution** on the DAG — decompose "build feature X" into a verified plan
  with checkpoints, run it overnight as a background job, report a reviewable diff.
- **This agent is the template** — its plan→verify→repair loop is what every other agent should
  become (§17).
**Effort:** medium. **Risk:** low — you're extending your best-built component.

## 12. Self-Improvement (ELI improving itself)

**Today:** improve/patch/upgrade/self-test scaffold, approval-gated — shallow, doesn't learn.
**Frontier (2026):** agents that autonomously run their own improvement lifecycle with
verifiable rewards (Meta REA, Beam Tool Tuner).
**v3 proposals:**
- **[Bet] Real RLVR self-tuning** — ELI proposes a change, **runs its own eval harness** (a
  verifiable reward), keeps the change only if metrics improve, and logs the delta. The eval
  harness (carried from v2) becomes the reward signal. Approval-gated for anything risky.
- **[Bet] Continual personal adapter** — you won't retrain 35B locally, but you *can* keep a
  small always-updating **LoRA adapter** (v2 already trains LoRA) that captures your patterns,
  fed by episodic memory, refreshed nightly. Big model frozen; the *adapter* learns you.
**Effort:** high. **Risk:** medium–high — strong guardrails + the audit ledger are mandatory.

## 13. Image & Media Generation

**Today:** procedural + SSD-1B diffusion with VRAM hot-swap.
**Frontier (2026):** faster/controllable diffusion (SDXL-Turbo/Lightning class), ControlNet-
style conditioning, local image *editing*, early local video.
**v3 proposals [Adopt]:**
- **Newer local diffusion** in the model-agnostic slot (turbo/lightning for speed, control for
  precision) — same VRAM-swap machinery.
- **Image editing** (inpaint/instruct) not just generation.
- **[Bet] Local short-video** as the open models mature — opportunistic.
**Effort:** low–medium. **Risk:** low.

## 14. Information & Web Research

**Today:** synthesised news, web search (gated + disclosed), weather, telemetry.
**Frontier (2026):** deep-research agents — multi-step, source-tracked, verified synthesis.
**v3 proposal [Adopt]:** a **deep-research agent** on the DAG — plan a research question,
fan out queries, read + cross-check sources, synthesise with citations and confidence, all
still honest about going online. Merge with the Report Builder (§15) so "research X and write
me a report" is one grounded pipeline.
**Effort:** medium. **Risk:** low (netguard already governs the online step).

## 15. Documents & Report Builder

**Today:** Report Builder (evidence→outline→draft→review→revise) with quality profiles —
already frontier-shaped.
**Frontier (2026):** long-form agentic writing with verification and revision loops.
**v3 proposal [Adopt]:** fuse Report Builder with the deep-research agent (§14) and the
temporal memory (§5) — reports grounded in *your* corpus + fresh research + your history, with
a verify-revise loop and source provenance. This is a genuinely strong, shippable capability.
**Effort:** low–medium (mostly wiring existing parts). **Risk:** low.

## 16. Smart Home & Physical World

**Today:** self-hosted MQTT control, rooms, scenes, time/sunset/device automations, suggested
automations.
**Frontier (2026):** predictive automation from behavioural + sensor patterns; local sensor
fusion.
**v3 proposal [Bet]:** **predictive home** — learn your routines (memory §5 + habits §7) and
*propose* automations grounded in real patterns ("you dim the lights at 22:40 most nights —
automate it?"), plus richer sensor fusion (presence, light, time, calendar). Proposal-only,
approval-gated — the same safety model.
**Effort:** medium. **Risk:** low–medium.

## 17. Plugins, Agents & Extensibility

**Today:** plugins, build-your-own-agent, workspaces — but bus agents are *retrieval-level*
(fetch context, hand to the model) and the protocol is bespoke.
**Frontier (2026):** multi-agent orchestration as *standard infrastructure* with standardised
protocols and agent *composition*.
**v3 proposals [Adopt]:**
- **Raise every agent to the coding-agent bar** (plan→verify→repair). Research, memory, file
  agents become *capable*, not just retrieval.
- **Agent composition on the DAG** — the research agent hands a verified brief to the writer to
  the file agent, declaratively.
- **MCP tool marketplace** — declarative tools (§3.2) mean third-party capabilities drop in
  safely under the approval gate.
**Effort:** medium–high. **Risk:** low–medium.

## 18. Models & Inference

**Today:** model-agnostic GGUF loader, VRAM smart-loader, LoRA, tier-scaling — already a
strength.
**Frontier (2026):** **State-Space Models (Mamba)** — linear-time, higher throughput, smaller;
**hybrid** attention+Mamba+MoE; **reasoning distillation** (o3-class thinking into small fast
models).
**v3 proposal [Adopt, opportunistic]:** ELI's loader is *built* for this. When a strong
Mamba-hybrid or distilled-reasoning small model ships in GGUF, slot it into the router (§3.4)
as the fast/routing brain — better on-device reasoning per watt, near-zero integration cost.
**Effort:** low (existing loader). **Risk:** low.

## 19. Security, Privacy & Sovereignty

**Today:** offline-by-default failsafe, fail-closed gate, approval engine, audit ledger, RBAC —
above the norm.
**Frontier (2026):** the whole industry is discovering that *agent security* (validation before
action, audit trails) is the hard part — exactly what ELI already does.
**v3 proposals [Adopt/Pioneer]:**
- **[Adopt] Encrypt memory at rest** — the user DBs + adapter are your whole self; encrypt them,
  keyed to the user.
- **[Pioneer] Sovereign-agent guarantees** — formalise and *prove* that the learned model of you
  (memory + adapter + world model) never leaves the machine and is yours to delete. Make
  sovereignty a stated, verifiable property. The cloud can't match it; make it the brand.
**Effort:** medium. **Risk:** low.

---

# Part III — The moonshots (not built anywhere yet)

Where ELI could plant a flag *first*, precisely because it's local + private + embodied:

1. **The sovereign personal agent.** An autonomous assistant whose entire model of you never
   leaves your machine and is yours to delete. The cloud rents you back your own data; ELI
   *owns nothing of yours.* This is a **category**, not a feature.
2. **Autobiographical intelligence.** The consolidation "sleep" pass taken to its end — a
   coherent, evolving narrative of your life and projects that ELI *reasons over*: "you tried
   this in March and it stalled; here's why now is different."
3. **Private continual personalisation.** The first assistant that measurably gets better *at
   being yours* the longer you use it — learning from your corrections, on device, forever,
   forgetting nothing that matters and nothing you didn't consent to keep.
4. **Predictive embodiment.** The desktop that anticipates — pre-stages your next app, surfaces
   the file you're about to need, catches the error before you see it, from a learned model of
   *your* machine.
5. **Verifiable local grounding as a moat.** Because ELI *has the machine*, its answers can be
   *checked against reality* (file opened? build passed?) — a verifiable reward cloud agents can
   only guess at. Lean all the way in.

---

# Part IV — Sequencing & delivery

## 20. Prioritisation (value × feasibility, honest)

| Tier | Items | Rationale |
|------|-------|-----------|
| **v3.0 — Foundation** | Planner/effector (§3.1), MCP tools (§3.2), model-router (§3.4) | unblocks everything; you're already on-thesis; biggest speed win |
| **v3.1 — Knows me** | Learned user-model (§6), temporal memory + consolidation (§5) | the "felt-quality" leap; substrate exists |
| **v3.2 — Acts well** | Verify-then-act reasoning (§4), repo-scale coding (§11), deep-research + Report Builder (§14–15) | grounded, high-value, low novelty risk |
| **v3.3 — Gets better** | Closed autonomy loop + RLVR-from-corrections (§7), self-tuning + continual adapter (§12) | the signature, private, cloud-can't-copy work |
| **v3.4 — Sees ahead** | Continuous screen understanding → desktop world model (§9), anticipatory embodiment (§10) | pioneering; uses the gaze loop you already own |
| **Opportunistic** | Mamba/distilled models (§18), newer diffusion (§13), voice duplex (§8) | near-free when the open models land |

## 21. The delivery discipline (the v2 lesson)

- **Ship v3.0 before touching v3.1.** The v2 wound was breadth outrunning depth — every "also
  add…" is a tax you pay alone, forever. At 2× scale it's fatal. **Saying no is the feature.**
- **Everything eval-gated.** No capability lands without a behavioural eval; the harness (from
  v2) is the regression gate from day one. RLVR self-tuning (§12) *reuses* it as the reward.
- **Release early.** The honest gap between ELI and the leaders was *adoption, not merit.* A
  narrower, deeper, **shipped** v3 outranks a broader unreleased v4 every day. Getting *anyone*
  using it changes the trajectory more than any architecture.

## 22. One-paragraph thesis

The cloud builds assistants that *rent* you intelligence and *borrow* your data. v3's
opportunity is the opposite corner of the frontier: an assistant that **owns nothing of yours,
learns only for you, runs entirely on your machine, and gets better at being *yours* the longer
you use it** — with grounding, memory, reasoning, and self-improvement that are frontier-grade
*because* they're local, not in spite of it. Most of that isn't built anywhere, and it requires
being exactly what ELI already is. That's not catching up. That's a lane the giants can't drive
in — and v3 is how you take it, in fewer, sharper strokes, and out the door.

---

### Sources
- [Best AI Agent Memory Frameworks in 2026 (Atlan)](https://atlan.com/know/best-ai-agent-memory-frameworks-2026/)
- [State of AI Agent Memory 2026 (Mem0)](https://mem0.ai/blog/state-of-ai-agent-memory-2026)
- [AI Agent Memory Architectures (Zylos Research)](https://zylos.ai/research/2026-04-05-ai-agent-memory-architectures-persistent-knowledge/)
- [Efficient On-Device Agents via Adaptive Context Management (arXiv 2511.03728)](https://arxiv.org/pdf/2511.03728)
- [2026 Breakthrough Year for World Models & Continual Learning (NextBigFuture)](https://www.nextbigfuture.com/2026/04/2026-is-breakthrough-year-for-reliable-ai-world-models-and-continual-learning-prototypes.html)
- [AI Trends 2026: Test-Time Reasoning and Reflective Agents (Hugging Face)](https://huggingface.co/blog/aufklarer/ai-trends-2026-test-time-reasoning-reflective-agen)
- [Self-Improving AI Agents: The 2026 Guide (o-mega)](https://o-mega.ai/articles/self-improving-ai-agents-the-2026-guide)
- [7 AI Breakthroughs Reshaping Production in 2026 (Adaline Labs)](https://labs.adaline.ai/p/the-ai-research-landscape-in-2026)
- [6 AI breakthroughs that will define 2026 (InfoWorld)](https://www.infoworld.com/article/4108092/6-ai-breakthroughs-that-will-define-2026.html)

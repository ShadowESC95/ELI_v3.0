# ELI v3 — Frontier Roadmap & Proposals

*A full-system upgrade plan. For each of ELI's current capabilities (per the User Manual) this
maps **where it is today → where the 2026 frontier is → the concrete v3 proposal → the
mechanism → a worked example → effort/risk**. It then adds genuinely-new capabilities v2
lacks, the moonshots nobody has built, success metrics, and a phased delivery plan.*

Written July 2026, grounded in the v2 capability inventory + the current research landscape.
Tags: **[Adopt]** proven · **[Bet]** emerging, worth the risk · **[Pioneer]** not built
anywhere — ELI's unique shot.

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
below are things ELI can do that the cloud *fundamentally cannot do per person.* That is the
lane, and it is defensible precisely because being in it is expensive and un-monetisable for a
cloud giant.

The frame that follows every proposal: *what does being local let ELI do here that a cloud
agent can't?* If the answer is "nothing," it's an Adopt (table stakes). If the answer is "a
lot," it's a Pioneer (the moat).

## 2. Current capability inventory (what v3 inherits)

From the manual, v2 already ships — genuinely, end-to-end — all of the following. v3 does not
*replace* these; it upgrades every one of them and re-homes them on a clean spine.

- **Conversation & reasoning** — text/voice chat; 5 reasoning modes (quick→expert); a 14-agent
  bus on a DAG orchestrator; low-grounding escalation; grounded synthesis; anti-confabulation.
- **Computer control** — apps, windows, workspaces, volume, keyboard, mouse, media (Spotify/
  YouTube), and **gaze/eye-control clicking**.
- **Files, docs & code** — file ops, notes, clipboard, doc conversion; the **Report Builder**
  (evidence→outline→draft→revise); a coding agent (plan→verify→repair); a built-in IDE; code
  examination + repair; test generation; synthetic-data fabrication.
- **Perception** — screen reading/OCR; ambient screen watching; image description; PDF/CSV
  analysis; local vision (VLM); gaze tracking.
- **Memory** — 4 SQLite stores + FAISS vector index + a knowledge graph; an evolving user
  profile; session summaries; sourced recall.
- **Habits & proactivity** — learned routines; morning briefings; suggestions; a proposal-only
  **autonomy tick** (code-monitor + self-model refresh + goal autogenesis), approval-gated.
- **Voice** — whisper STT; piper TTS; wake-word; dictation; voice diagnostics.
- **Self-improvement** — improve/patch/upgrade/self-test cycles, approval-gated; an eval harness.
- **Image generation** — procedural + SSD-1B diffusion with VRAM hot-swap.
- **Information** — time/date/weather; *synthesised* news; web search (gated + disclosed);
  system telemetry.
- **Scheduling** — alarms, timers, calendar, pomodoro; durable overnight background jobs.
- **Smart home** — self-hosted MQTT device control; rooms; scenes; time/sunset/device
  automations; suggested automations.
- **Server & phone** — FastAPI web app + installable PWA; LAN/HTTPS; model hot-swap dropdown.
- **Models** — model-agnostic GGUF loader; VRAM smart-loader; LoRA training; capability-tier
  scaling.
- **Extensibility** — plugins; **build-your-own-agent** flow; workspaces/projects.
- **Security** — offline-by-default socket failsafe; fail-closed command gate; approval engine;
  tamper-evident audit ledger; RBAC.

That is a team's worth of surface, built solo. The v3 job is to make each of these
*frontier-grade* — and to carry it on an architecture that can bear 2× the size without
collapsing.

## 3. The architectural spine (build this first — everything rests on it)

Four foundations, in order. Nothing downstream is clean without them, and skipping them is how
v2 got its god-files.

**3.1 Planner / Effector split. [Adopt]**
The 2026 consensus frontier-agent shape is a *Policy Core* where every proposed action passes a
*validation layer before execution*, producing an audit trail. That is precisely the split:
- **Planner** (pure) — `(intent, args, world_state) → Plan | Refusal`. No side-effects.
  Unit-testable to 90%+.
- **Effector** (thin) — `Plan → performs the one side-effect → StructuredResult`.
- *Mechanism:* a `Plan` is a typed object — action, args, preconditions, expected effect, risk
  class, undo. It's the universal unit: planned → validated → approved → logged → replayed.
- *Migration:* strangle the 14k-line executor one action family at a time; each family that
  moves gets a planner test-suite and drops out of the god-file.
- *Example:* "delete build/" → planner returns a Plan{action: rm, path: build/, risk: destructive,
  undo: trash-not-delete}; the approval gate sees `risk: destructive` and asks; the effector runs
  it; the ledger records the plan and result. Every step testable, auditable, reversible.

**3.2 MCP-style declarative tools. [Adopt]**
Capabilities become **typed schemas + policy**, not hand-wired handlers. The 208 aliased v2
actions collapse to a clean registry (~110 distinct). *Mechanism:* each tool is a JSON schema +
a policy predicate (who may call it, what risk class, what confirmation). Adopt an MCP/A2A-style
protocol so ELI can both *expose* its tools to other agents and *consume* third-party tools
under the same approval gate. *Payoff:* the sprawl becomes data, not code, and an ecosystem
becomes possible.

**3.3 The DAG as the universal substrate. [Adopt]**
v2 already elevated `dag.py` to an orchestrator running 14 agents. v3 runs *everything* on it —
declarative, replayable, observable pipelines with retries, fallbacks, caching, and telemetry.
*Payoff:* debuggable-by-construction at 2× scale; every multi-step task is a graph you can
inspect and replay, not a call-stack you have to reverse-engineer.

**3.4 The model-router. [Adopt]**
Stop running one model for everything. Route by task: a ~1–3B model for intent/routing and JSON,
the 35B for reasoning, the VLM on demand, an embedder always warm. *Mechanism:* a fast classifier
(rules + tiny model) tags each turn (route / chat / reason / see / code) and the DAG dispatches to
the right warm model; VRAM budget is managed by the existing smart-loader. *Payoff:* the single
biggest *felt-speed* win in the plan — and it honours "keep the 35B" by spending its latency only
where it earns it.

---

# Part II — Frontier upgrades, subsystem by subsystem

## 4. Conversation & Reasoning
**Today:** 5 heuristic reasoning modes; escalation on low grounding; single model.
**Frontier (2026):** *test-time compute* — inference-time scaling + **RL from Verifiable Rewards
(RLVR)**; models self-check and fix their own mistakes; "reflective agents."
**Proposal [Adopt/Bet]:** replace fixed modes with **confidence-driven, verifiable self-checking.**
*Mechanism:* the planner proposes an answer/plan, ELI generates a *check* ("what would make this
wrong?"), verifies it **against real machine state**, and repairs before committing. The reward
is *verifiable* because ELI has the machine.
*Example:* asked "which config sets the port?", ELI proposes `settings.json`, then *greps the
running process's actual env + config load order* to confirm before answering — instead of
guessing plausibly. **Verifiable local grounding is the moat, not a mode.**
*Effort:* medium · *Risk:* low (generalises the coding agent's loop you already ship).

## 5. Memory
**Today:** 4 stores + FAISS + KG; evolving profile; session summaries — strong substrate, but
present-tense, un-tuned fusion weights, no consolidation.
**Frontier (2026):** memory is a *benchmarked field* — **Mem0** (personalisation), **Zep/Graphiti**
(temporal graphs), **Cognee** (local-first privacy graph reasoning — closest in spirit).
**Proposals:**
- **[Adopt] Temporal knowledge graph.** *Mechanism:* bi-temporal edges (valid-time + ingest-time)
  so facts carry *when they were true* and *when ELI learned them*; queries can time-travel. *Example:*
  "what was I working on in spring?" resolves against valid-time, not just latest.
- **[Adopt] Tune the fusion.** Three retrieval-fusion weights are still defaults — measure on a
  recall benchmark and tune; gate regressions.
- **[Pioneer] Consolidation ("sleep").** *Mechanism:* a nightly pass promotes recurring episodics
  to semantic memory, decays noise, and distils episodes into a running personal narrative.
  *Example:* after a week, "you keep hitting VRAM limits on the 35B" becomes a stable known-fact,
  not 6 scattered conversation fragments. **Nobody ships an autobiographical assistant memory —
  because nobody else has all your data locally.**
*Effort:* medium–high · *Risk:* medium (retrieval regressions — gate with a memory eval).

## 6. Persona & Personalisation
**Today:** emergent persona (great philosophy) fed by a **regex→canned-phrase** profile extractor
that literally hardcodes `active_project = "developing ELI"`. The character emerges from a shallow
input.
**Frontier (2026):** personalisation memory (Mem0) + continual per-user modelling.
**Proposal [Pioneer]:** a **genuinely learned user-model.** *Mechanism:* a low-priority background
LLM pass reads recent episodes and updates a structured model of *you* — interests, projects, tone,
relationships, routines — each with a confidence + decay; the persona brief is assembled from the
*high-confidence* slice. *Example:* instead of "active_project = developing ELI" (hardcoded), ELI
actually infers "shifted focus to the v3 architecture this week; prefers blunt, technical,
no-preamble replies" — and the emergent voice sharpens accordingly. Nothing about the *character*
is scripted; only the *inputs* get deeper. **The single biggest jump in *felt* quality.**
*Effort:* medium · *Risk:* low–medium.

## 7. Proactivity & Autonomy
**Today:** proposal-only autonomy tick (code-monitor + self-model + goal autogenesis) — the safety
model is *right*, but the loop doesn't close: frequency-habits are detected yet never become rules,
so they're never proposed; the FrontierAgent is dormant.
**Frontier (2026):** self-improving agents that learn *from production usage* (Meta's **Ranking
Engineer Agent**; Beam's **Tool Tuner** — RL from real use).
**Proposals:**
- **[Bet] Close the loop.** *Mechanism:* detect a behavioural pattern → propose a rule → record
  accept/reject → update a per-user acceptance model that gates future proposals. *Example:* ELI
  notices you regenerate the test report every night, proposes automating it *once*, and — if you
  decline — learns not to nag about that class again.
- **[Pioneer] RLVR from *your corrections*.** *Mechanism:* every "no, not like that" / accept /
  reject is a labelled preference pair; a nightly pass fine-tunes a small preference adapter on
  them. *This signal is private, personal, and unavailable to any cloud model.* **This is v3's
  signature idea** — a self-improving agent the cloud literally cannot replicate per person.
*Effort:* high · *Risk:* medium (guardrails — but the approval gate already provides them).

## 8. Voice & Conversation UX
**Today:** whisper STT + piper TTS + wake-word — turn-based, functional.
**Frontier (2026):** low-latency **full-duplex** voice (barge-in), streaming recognition,
emotion/prosody, real-time conversation.
**Proposals [Adopt]:** **streaming duplex** (VAD-gated listening + interruptible TTS so you can
talk over ELI and it stops); **prosody & affect** (emotion-aware TTS; detect *your* stress/rush and
adapt pacing — ties into the crisis-safety guard); **streaming STT** (faster-whisper streaming) for
sub-second response. *Example:* mid-briefing you say "skip that" and ELI cuts off cleanly and moves
on — like a person, not a walkie-talkie. *Effort:* medium · *Risk:* low.

## 9. Vision & Screen Understanding
**Today:** VLM description, OCR, ambient glances, PDF/CSV analysis — *reads* pixels, doesn't
*model* them.
**Frontier (2026):** stronger open VLMs; **world models** (V-JEPA 2, Genie 3, Marble) that predict
*environment state* rather than tokens.
**Proposals:**
- **[Adopt] Continuous screen understanding.** *Mechanism:* a rolling structured model of screen
  state (windows, focus, app, salient text) the agent can query — not one-shot glances.
- **[Pioneer] A world model of *your desktop*.** *Mechanism:* a small learned predictor over
  (screen-state, gaze, action) sequences that forecasts your next state. *Example:* it learns "IDE
  build fails → you open the terminal → you rerun the last command," so when the build fails ELI can
  *offer* the rerun. **Predictive embodiment for the desktop is a frontier nobody's built — because
  nobody else has ELI's gaze+screen+action loop.**
*Effort:* high (world model) · *Risk:* medium–high — but continuous-understanding alone is a clean,
high-value first step.

## 10. Embodiment: OS control & Gaze
**Today:** apps/windows/mouse/keyboard/volume + **gaze-click** — reactive; you command, it acts.
**Frontier (2026):** vision-based computer-use agents; anticipatory interfaces.
**Proposal [Pioneer]:** couple the desktop world model (§9) to action — **anticipatory
embodiment.** *Mechanism:* when the world model's next-state confidence is high, ELI pre-stages the
predicted action behind a light-touch confirm (a hint, a hovered button, a warmed app). *Example:*
gaze rests on a failed build; ELI surfaces "rerun?" exactly where you're looking. It acts *slightly
ahead* of you, always reversible. A genuinely new interaction model. *Effort:* high · *Risk:*
medium (over-eagerness — gate on confidence + instant undo).

## 11. Coding & Building
**Today:** coding agent (plan→verify→repair), IDE, examine-and-fix, test generation, project
scaffolding — already the *strongest, most frontier-shaped* agent in ELI.
**Frontier (2026):** repo-scale agentic coding; long-horizon multi-file tasks; verified patches.
**Proposals [Adopt]:** **repo-scale context** (a codebase graph — symbols, imports, call-graph — so
the agent reasons over the *project*, not a file); **long-horizon execution on the DAG** (decompose
"build feature X" into a checkpointed plan, run it overnight as a background job, return a reviewable
diff). *Example:* "add rate-limiting to the API" becomes a plan across router + config + tests, run
overnight, presented as one diff + passing tests to approve. **This agent is the template every
other agent should become (§17).** *Effort:* medium · *Risk:* low.

## 12. Self-Improvement (ELI improving itself)
**Today:** improve/patch/upgrade/self-test scaffold, approval-gated — shallow, doesn't *learn*.
**Frontier (2026):** agents that run their own improvement lifecycle with verifiable rewards.
**Proposals:**
- **[Bet] Real RLVR self-tuning.** *Mechanism:* ELI proposes a change, **runs its own eval harness**
  (the verifiable reward), keeps it only if metrics improve, logs the delta to the ledger. *Example:*
  it tweaks a retrieval prompt, the memory-recall eval rises 3 points, it keeps it — and can explain
  why.
- **[Bet] Continual personal adapter.** *Mechanism:* the 35B stays frozen; a small always-updating
  **LoRA adapter** (v2 already trains LoRA), fed by episodic memory + preference pairs (§7),
  refreshes nightly. The *adapter* is the part that learns you.
*Effort:* high · *Risk:* medium–high — strong guardrails + the audit ledger are mandatory.

## 13. Image & Media Generation
**Today:** procedural + SSD-1B diffusion with VRAM hot-swap.
**Frontier (2026):** fast/controllable diffusion (Turbo/Lightning class), ControlNet-style
conditioning, local image *editing*, early local video.
**Proposals [Adopt]:** newer local diffusion in the model-agnostic slot (turbo for speed, control
for precision); **image editing** (inpaint/instruct) not just generation; **[Bet]** local
short-video as open models mature — opportunistic, same VRAM-swap machinery. *Effort:* low–medium ·
*Risk:* low.

## 14. Information & Web Research
**Today:** synthesised news; web search (gated + disclosed); weather; telemetry.
**Frontier (2026):** deep-research agents — multi-step, source-tracked, verified synthesis.
**Proposal [Adopt]:** a **deep-research agent** on the DAG. *Mechanism:* plan a question → fan out
queries → read + cross-check sources → synthesise with citations + confidence → stay honest about
going online. *Example:* "research the best local VLMs for 8GB and summarise" returns a sourced,
dated brief, not a vibe. Merge with §15 so "research X and write me a report" is one pipeline.
*Effort:* medium · *Risk:* low (netguard governs the online step).

## 15. Documents & Report Builder
**Today:** Report Builder (evidence→outline→draft→review→revise) with quality profiles — already
frontier-shaped.
**Proposal [Adopt]:** fuse Report Builder with the deep-research agent (§14) and temporal memory
(§5) — reports grounded in *your* corpus + fresh research + your history, with a verify-revise loop
and full source provenance. *Example:* "write up the QMSH grant progress" pulls your project memory,
adds current figures, and drafts a cited report you edit, not rewrite. *Effort:* low–medium (mostly
wiring existing parts) · *Risk:* low.

## 16. Smart Home & Physical World
**Today:** self-hosted MQTT control; rooms; scenes; time/sunset/device automations; suggested
automations.
**Frontier (2026):** predictive automation from behavioural + sensor patterns; local sensor fusion.
**Proposal [Bet]:** a **predictive home** — learn routines (memory §5 + habits §7) and *propose*
automations grounded in real patterns, plus richer sensor fusion (presence + light + time +
calendar). *Example:* "you dim the lights ~22:40 most nights — automate it?" Proposal-only,
approval-gated — the same safety model. *Effort:* medium · *Risk:* low–medium.

## 17. Plugins, Agents & Extensibility
**Today:** plugins; build-your-own-agent; workspaces — but bus agents are *retrieval-level* and the
protocol is bespoke.
**Frontier (2026):** multi-agent orchestration as *standard infrastructure*, with standardised
protocols and agent *composition*.
**Proposals [Adopt]:** **raise every agent to the coding-agent bar** (plan→verify→repair — research,
memory, file agents become *capable*, not retrieval); **agent composition on the DAG** (research →
writer → file, declaratively); an **MCP tool marketplace** (declarative tools from §3.2 drop in under
the approval gate). *Effort:* medium–high · *Risk:* low–medium.

## 18. Models & Inference
**Today:** model-agnostic GGUF loader; VRAM smart-loader; LoRA; tier-scaling — already a strength.
**Frontier (2026):** **State-Space Models (Mamba)** — linear-time, higher throughput, smaller;
**hybrid** attention+Mamba+MoE; **reasoning distillation** (o3-class thinking into small fast models).
**Proposal [Adopt, opportunistic]:** ELI's loader is *built* for this — when a strong Mamba-hybrid or
distilled-reasoning small model ships in GGUF, slot it into the router (§3.4) as the fast/routing
brain. Better on-device reasoning per watt at near-zero integration cost. *Effort:* low · *Risk:* low.

## 19. Security, Privacy & Sovereignty
**Today:** offline failsafe; fail-closed gate; approval engine; audit ledger; RBAC — above the norm.
**Frontier (2026):** the industry is discovering that *agent security* (validate before act, audit)
is the hard part — exactly what ELI already does.
**Proposals:** **[Adopt] encrypt memory at rest** (the user DBs + adapter are your whole self — key
them to the user); **[Pioneer] sovereign-agent guarantees** — formalise and *prove* that the learned
model of you (memory + adapter + world model) never leaves the machine and is yours to delete; make
sovereignty a *stated, verifiable* property and the brand. *Effort:* medium · *Risk:* low.

---

# Part II.5 — Genuinely new capabilities (v2 has none of these)

Not upgrades — *new organs*, each strongly enabled by being local:

- **[Pioneer] Meeting & ambient-audio intelligence.** Local, private transcription + summarisation
  + action-item extraction of *your* calls/meetings — the killer app of cloud assistants, but
  **private** because the audio never leaves. Reuses whisper + memory + Report Builder.
- **[Bet] Personal knowledge base / "second brain."** Point ELI at your notes, PDFs, code, and
  chats; it builds a private, queryable, cited knowledge graph over *your* corpus (Khoj territory,
  but fully local + embodied). The temporal memory (§5) is already the substrate.
- **[Bet] Multi-device continuity.** Your sovereign model (memory + adapter) syncs *encrypted,
  peer-to-peer* between your own machines — never a server. Continuity without the cloud.
- **[Bet] Financial / life admin agent.** Local parsing of statements, bills, subscriptions →
  private summaries + reminders, with nothing uploaded. Sensitive data is exactly what people won't
  put in the cloud — ELI's home turf.
- **[Pioneer] Explainable action replay.** Because every action is a logged Plan (§3.1), ELI can
  *replay and explain* any past action ("why did you delete that?") from the ledger — an auditable
  agent, which is a genuine trust differentiator.

---

# Part III — The moonshots (not built anywhere yet)

Where ELI could plant a flag *first*, precisely because it's local + private + embodied:

1. **The sovereign personal agent.** An autonomous assistant whose entire model of you never leaves
   your machine and is yours to delete. The cloud rents you back your own data; ELI *owns nothing of
   yours.* A **category**, not a feature.
2. **Autobiographical intelligence.** The consolidation "sleep" pass taken to its end — a coherent,
   evolving narrative of your life and projects that ELI *reasons over*: "you tried this in March and
   it stalled; here's why now is different."
3. **Private continual personalisation.** The first assistant that measurably gets better *at being
   yours* the longer you use it — from your corrections, on device, forever, forgetting nothing that
   matters and nothing you didn't consent to keep.
4. **Predictive embodiment.** The desktop that anticipates — pre-stages your next app, surfaces the
   file you're about to need, catches the error before you see it, from a learned model of *your*
   machine.
5. **Verifiable local grounding as a moat.** Because ELI *has the machine*, its answers can be
   *checked against reality* (file opened? build passed?) — a verifiable reward cloud agents can only
   guess at. Lean all the way in.

---

# Part IV — Delivery

## 20. What "a day with ELI v3" feels like (the vignette)

Morning: ELI's briefing is sharper — it *knows* you shifted to the v3 architecture and leads with
that, not generic news (learned user-model §6). You start the IDE; ELI has already warmed the coding
model (router §3.4) and, from last week's pattern, pre-opened the terminal (predictive embodiment
§10). A build fails; before you look, ELI hovers "rerun the last command?" right where your gaze
lands (§9–10). Midday you say "research the best 8GB VLMs and write it up" — the deep-research agent
fans out, the Report Builder drafts a cited brief grounded in your hardware notes (§14–15). You edit
one line and say "no, keep it terser" — that correction becomes a preference pair (§7). Overnight,
ELI runs the feature-build you queued (§11), consolidates the day's episodes into your narrative
(§5), and refreshes the personal adapter on your corrections (§12). Nothing left the machine. It's
measurably more *yours* than it was yesterday.

## 21. Success metrics (how you know v3 is working)

- **Felt latency:** median simple-turn response ↓ 3–5× (router).
- **"Knows me":** blind A/B — you can tell v3's persona/briefing from v2's (learned model).
- **Grounding:** self-check catches ≥X% of would-be-wrong answers pre-commit (verifiable reward).
- **Autonomy quality:** proposal acceptance rate *rises* over weeks (closed loop learning).
- **Coverage & safety:** the planner surface hits ≥85% coverage; zero unlogged actions.
- **Sovereignty:** a proven, auditable "nothing left the machine" property.

## 22. Prioritisation (value × feasibility)

| Tier | Items | Rationale |
|------|-------|-----------|
| **v3.0 — Foundation** | Planner/effector (§3.1), MCP tools (§3.2), model-router (§3.4) | unblocks everything; on-thesis; biggest speed win |
| **v3.1 — Knows me** | Learned user-model (§6), temporal memory + consolidation (§5) | the felt-quality leap; substrate exists |
| **v3.2 — Acts well** | Verify-then-act (§4), repo-scale coding (§11), deep-research + Report Builder (§14–15) | grounded, high value, low novelty risk |
| **v3.3 — Gets better** | Closed autonomy + RLVR-from-corrections (§7), self-tuning + adapter (§12) | the signature, private, cloud-can't-copy work |
| **v3.4 — Sees ahead** | Continuous screen → desktop world model (§9), anticipatory embodiment (§10) | pioneering; uses the gaze loop you already own |
| **New organs** | Meeting intelligence, second-brain (§II.5) | high pull, mostly reuse existing parts |
| **Opportunistic** | Mamba/distilled models (§18), diffusion (§13), voice duplex (§8) | near-free when the open models land |

## 23. De-risking (the honest failure modes)

- **Scope creep (the v2 wound, doubled at 2× size).** *Mitigation:* ship v3.0 before touching v3.1;
  a frozen v3.0 feature line you refuse to cross. **Saying no is the feature.**
- **Continual-learning instability / catastrophic forgetting.** *Mitigation:* adapter-only (frozen
  base), eval-gated nightly refresh, one-click rollback via the ledger.
- **Autonomy over-reach.** *Mitigation:* everything stays proposal-only + approval-gated + instant
  undo; the acceptance model *reduces* nagging over time.
- **World-model wrongness (bad predictions annoy).** *Mitigation:* confidence-gated, light-touch,
  always dismissable; start with *offer*, never *act*.
- **Maintainability at 2× LOC, solo.** *Mitigation:* the spine (§3) exists precisely so one person
  can carry it; everything eval-gated so regressions surface automatically.

## 24. One-paragraph thesis

The cloud builds assistants that *rent* you intelligence and *borrow* your data. v3's opportunity is
the opposite corner of the frontier: an assistant that **owns nothing of yours, learns only for you,
runs entirely on your machine, and gets better at being *yours* the longer you use it** — with
grounding, memory, reasoning, and self-improvement that are frontier-grade *because* they're local,
not in spite of it. Most of it isn't built anywhere, and it requires being exactly what ELI already
is. That's not catching up. That's a lane the giants can't drive in — and v3 takes it in fewer,
sharper strokes, and out the door.

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

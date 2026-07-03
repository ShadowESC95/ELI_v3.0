# ELI v3 — Frontier Roadmap (2026 and beyond)

A companion to `ELI_v3_vision_and_roadmap.md`. That one is the *architecture*; this one is
the *ambition* — a map of what's genuinely frontier in AI right now (mid-2026), what ELI v3
should adopt, and the edges **nobody has built yet** where ELI's local-private-embodied
position lets it pioneer things the cloud giants structurally *can't*.

Written July 2026, from the current research landscape + ELI's v2 baseline. Every item is
tagged: **Adopt** (proven, bring it in), **Bet** (emerging, worth the risk), or
**Pioneer** (not yet built — ELI's unique shot).

---

## 0. ELI's structural advantage (read this first)

The whole cloud industry is racing toward "agents that know you and act for you." But they
have a wall ELI doesn't: **they can't have your data.** Privacy-preserving *per-person*
learning, sovereign memory, and true embodiment on *your* machine are things OpenAI/Google
can offer only in a diluted, aggregated, rented form.

ELI is the inverse: it already lives on your machine, offline by default, with your whole
context local. That means the frontier directions below aren't just "catch up to the cloud"
— several of them are things **ELI can do that the cloud fundamentally cannot.** That's the
thesis of v3: don't chase the frontier, *occupy the part of it that requires being local.*

---

## 1. Agentic core: policy + validation (Adopt — you're already ahead)

The 2026 consensus architecture for a frontier agent is a **Policy Core** (the reasoning
LLM) where *every proposed action passes a validation layer before execution*, producing an
audit trail. That is **exactly** the planner/effector split + approval engine + audit ledger
in the v3 vision. ELI isn't behind here — it's on-thesis.

**v3 move:** make it literal. Formalize the *plan* as the unit of everything — planned,
validated, approved, logged, replayed. Adopt an **MCP-style tool protocol** so capabilities
are declarative schemas, not hand-wired handlers (this also kills the god-file).

---

## 2. Memory as a first-class system (Adopt + Pioneer)

Memory is now a *benchmarked research field*, not a feature. The leaders each own a slice:
- **Mem0** — personalization memory.
- **Zep / Graphiti** — *temporal* knowledge graphs (facts with time, change, causality).
- **Cognee** — **local-first, privacy-critical graph reasoning** (the closest in spirit to ELI).

ELI v2 already has 4 stores + FAISS + KG — more raw substrate than most. What it lacks is
what these frameworks add: **temporal reasoning** (what was true *when*, how your life
changed), tuned fusion, and **consolidation**.

**v3 Adopt:** a temporal knowledge graph (Zep/Graphiti-style) — ELI should reason over *how
you and your world changed*, not just what's true now. Tune the three fusion weights that
are still defaults.

**v3 Pioneer:** **lifelong episodic memory + a nightly consolidation pass** ("sleep"):
promote recurring facts to semantic memory, decay noise, distil episodes into a personal
narrative. Nobody ships a genuinely *autobiographical* assistant memory because nobody else
has all your data locally. ELI can.

---

## 3. Model routing: frontier + open orchestra (Adopt)

The 2026 best-practice: **combine frontier and open models behind a router** that classifies
the task and picks the right model — small/fast for routing, big for reasoning, specialist
for vision. ELI v2 runs one model for everything.

**v3 move:** a real **model-router on the DAG** — a tiny model for intent/routing, the 35B
for reasoning, the VLM on demand, an embedder always-warm. Honours "keep the 35B" by
spending its latency *only* where it earns it. This is the single biggest *felt-speed* win.

---

## 4. Test-time compute + reflective reasoning (Adopt/Bet)

The most visible 2026 shift: **inference-time scaling** and **RL from Verifiable Rewards
(RLVR)** — models spend more compute at runtime and **self-check before answering**, fixing
their own mistakes without step-by-step supervision. "Reflective agents" are the pattern.

ELI's reasoning modes (quick→expert) are a crude version of this already. v3 makes them
*real*: **confidence-driven, verifiable self-checking.** For a grounded action ("is this the
right file to edit?"), ELI can *verify against the actual system state* before acting — a
verifiable reward the cloud often can't get because it lacks the machine.

**v3 move:** wire a verify-then-act loop into the planner: propose → self-check against real
state → repair → act. The coding agent already does plan→verify→repair; generalise it.

---

## 5. Self-improving agents (Bet — with ELI's twist)

2026 shipped real self-improvers: **Meta's Ranking Engineer Agent** (autonomous ML
lifecycle) and **Beam AI's Tool Tuner** (continuous improvement via RL *from production
usage*). ELI v2 has a self-improvement scaffold but it's shallow and its autonomy loop
doesn't close.

**v3 Pioneer:** **RLVR from *your* corrections.** Every time you say "no, not like that" or
accept/reject a proposal, that's a verifiable reward signal — *private, personal, and
unavailable to any cloud model.* ELI can learn your preferences on-device, from you,
without a single byte leaving. That's a self-improving agent the cloud literally cannot
replicate per-person. This is arguably v3's signature idea.

---

## 6. Continual learning (Bet — the paradigm shift)

**The** most-discussed 2026 paradigm: **continual learning** — Google Research's **Nested
Learning** frames a model as interconnected optimization problems at different speeds, with
temporally-scaled memory modules that **avoid catastrophic forgetting.** This is the holy
grail for a *personal* assistant: a system that genuinely *keeps learning you* over years
without retraining or forgetting.

**v3 Bet:** you won't retrain a 35B locally — but you *can* run a **continual-learning
adapter layer**: a small, always-updating personal model (LoRA/adapter, which v2 already
trains) that captures your patterns, fed by the episodic memory, refreshed nightly. The big
model stays frozen; the *adapter* is the part that learns you. Pair with Nested-Learning-
style multi-speed memory: fast (this conversation) → medium (this week's habits) → slow
(who you are).

**Pioneer angle:** cloud assistants can't do per-user continual learning at scale — it's a
privacy and cost nightmare for them. For a *single local user*, it's tractable. ELI's market
of one is its advantage.

---

## 7. World models (Bet/Pioneer — the embodied edge)

2026 is the breakout year for **world models** — **V-JEPA 2**, **Genie 3** (real-time 3D),
**World Labs' Marble** — systems that predict *physical/environment state* rather than next
tokens, enabling spatial reasoning for embodied agents and robotics.

ELI is *already embodied* (screen, gaze, OS control) but blind to structure — it sees pixels
and OCR, not a *model* of your environment.

**v3 Pioneer:** a **world model of *your* desktop/workflow** — not physics, but a learned
predictive model of your screen state, app relationships, and task flows. "You always open
the terminal after the IDE"; "this dialog means a build failed." Gaze + screen + action
history are the training signal, all local. An embodied assistant that *predicts your next
move* and pre-stages it is a genuine frontier nobody's built for the desktop, because nobody
else has the gaze+screen+action loop ELI already has.

---

## 8. Efficient architectures (Adopt as they land)

The efficiency frontier that makes bigger-brains-locally feasible:
- **State Space Models (Mamba)** — linear-time (vs quadratic attention), higher throughput,
  smaller params. Hybrid **attention+Mamba+MoE** models are the efficiency sweet spot.
- **Reasoning distillation** — transfer o3-class thinking into small fast models.

**v3 Adopt (opportunistic):** ELI is model-agnostic by design (v2's GGUF template detection,
tier-scaling). When a strong Mamba-hybrid or a distilled-reasoning small model ships in GGUF,
ELI should slot it into the router as the fast/routing brain — better on-device reasoning per
watt. Low effort (it's the existing loader), high payoff.

---

## 9. Multi-agent orchestration + protocols (Adopt)

Multi-agent orchestration became *standard infrastructure* in 2026, with standardized
protocols for agents coordinating across domains with persistent memory. ELI's 14-agent bus
on a DAG is ahead of most — but the agents are retrieval-level and the protocol is bespoke.

**v3 move:** standardize the inter-agent contract (MCP/A2A-style), raise every agent to the
coding-agent's plan→verify→repair bar, and let agents *compose* (research agent hands a
verified brief to the writer agent to the file agent) on the DAG.

---

## 10. The moonshots (Pioneer — not built anywhere yet)

Where ELI could plant a flag *first*, precisely because it's local + private + embodied:

- **A sovereign personal agent.** A genuinely autonomous assistant whose entire model of you
  — memory, preferences, learned adapter, world model — **never leaves your machine and is
  yours to delete.** The cloud can't offer sovereignty; it's renting you back your own data.
  This is a *category*, not a feature, and it's ELI's to define.
- **Autobiographical intelligence.** The consolidation "sleep" pass (§2) taken to its end: an
  assistant that holds a coherent, evolving narrative of your life and projects and *reasons
  over your own history* — "you tried this approach in March and it stalled; here's why this
  time differs."
- **Private continual personalization** (§5–6 combined): the first assistant that measurably
  gets better *at being yours* the longer you use it, learning from your corrections, on
  device, forever, forgetting nothing that matters and nothing you didn't consent to keep.
- **Predictive embodiment** (§7): the desktop that anticipates. Pre-stages your next app,
  surfaces the file you're about to need, catches the error before you see it — from a
  learned model of *your* machine.
- **Verifiable local grounding as a moat:** because ELI *has the machine*, it can get
  verifiable rewards (did the file open? did the build pass?) that cloud agents can only
  guess at. Lean into it: ELI's answers can be *checked against reality*, not just plausible.

---

## Prioritisation — what actually goes in v3

Ranked by (value × feasibility), honestly:

| Tier | Item | Why |
|------|------|-----|
| **Now** | Planner/effector + MCP-style tools (§1) | foundation; unblocks everything; you're already on-thesis |
| **Now** | Model-router (§3) | biggest felt-speed win, low novelty risk |
| **Now** | Temporal memory + consolidation (§2) | the "knows me" leap; substrate exists |
| **Next** | Verify-then-act reflective loop (§4) | generalises the coding agent; grounded by the real machine |
| **Next** | Closed autonomy loop + RLVR-from-corrections (§5) | the signature, private, cloud-can't-copy idea |
| **Bet** | Continual personal adapter (§6) | high payoff, real ML effort; adapter-only keeps it tractable |
| **Bet** | Desktop world model (§7) | pioneering, uses the gaze+screen loop you already have |
| **Opportunistic** | Mamba/distilled small models (§8) | free when they ship; it's the existing loader |

**Do NOT** try all of these in one release. The v2 lesson is that breadth kills. Pick the
**Now** tier, ship it, then earn the **Next** tier. The moonshots (§10) are the north star,
not the sprint.

---

## The one-paragraph thesis

The cloud is building assistants that *rent* you intelligence and *borrow* your data. v3's
opportunity is the opposite corner of the frontier: an assistant that **owns nothing of
yours, learns only for you, runs entirely on your machine, and gets better at being *yours*
the longer you use it** — with grounding, memory, and improvement that are frontier-grade
*because* they're local, not in spite of it. Most of that isn't built anywhere. It requires
being exactly what ELI already is. That's not catching up. That's a lane the giants can't
drive in.

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

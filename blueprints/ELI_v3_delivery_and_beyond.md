# ELI v3 — Delivery Report: Bringing ELI to v3 and Beyond

*A single, detailed plan — technical and non-technical — for taking ELI from its current
v2-grade codebase to the intended v3 architecture, a family of specialised editions, and the
frontier past it. Written July 2026, grounded in a full read of the shipping code.*

---

## How to read this document

It is written for two readers at once.

- **If you are non-technical** (or reading fast): read Part I (Executive Summary), Part III
  (The Thesis), Part VII (Roadmap), and Appendix B (Glossary). Skip anything in a `code font`
  block. Every technical part opens with a plain-language paragraph.
- **If you are the engineer** (you, or whoever inherits this): the whole thing. The technical
  parts carry the module maps, migration mechanics, verification discipline, and risks.

Section signposts: **[plain]** = written for anyone. **[tech]** = implementation detail.

---

# Part I — Executive summary  [plain]

ELI is a complete, private AI assistant that runs entirely on your own machine — the model,
your data, all of it — with nothing sent to any server. Today it already does a great deal:
it holds conversations, controls your computer, reads your screen and documents, writes and
fixes code, remembers you across sessions, speaks and listens, and even runs its own smart
home. It ships as a paid desktop app for Windows, macOS and Linux.

That is the good news and, oddly, also the problem. ELI has *more* capability than most
products in its lane — but it grew fast, and a handful of enormous source files now carry too
much of it. Five files hold roughly a third of the whole system. That makes every change slow
and risky, and it blocks the two things the next phase needs:

1. **v3 as a real architecture** — not just more features, but a clean internal shape that one
   person can maintain and that the frontier ideas (a model that learns *you*, verifiable
   grounding, anticipation) can actually be built on.
2. **Specialised editions** — turning ELI into a *family* of products (ELI Research, ELI
   Healthcare, ELI Legal, ELI Coding, ELI Content, plus General and Enterprise), each sold as
   its own tier, without maintaining seven separate copies of the code.

The core insight of this report: **those two goals are the same job.** You cannot cleanly
split ELI into editions until the giant files are broken into tidy, well-labelled pieces. And
once they are, the editions almost fall out for free — because it turns out about 90% of ELI is
shared "core" that every edition needs, and only a thin, clearly-identified slice is
sector-specific.

So the plan is, in order:

1. **Break up the five monoliths** into small, cohesive modules (a safe, reversible,
   one-piece-at-a-time method that keeps the app working the whole way — already started and
   proven this session).
2. **Stand up the v3 spine** (the planner/effector split, typed tools, a model-router) *as* you
   break up the files — the executor split literally *is* the planner/effector work.
3. **Lift the thin sector slice into edition packs** that overlay the shared core and switch on
   by licence — so integrating a new sector is dynamic, not a fork.
4. **Then** climb the frontier: a learned model of the user, temporal memory, verify-then-act,
   private self-improvement, and predictive help.

None of this is a rewrite. It is a disciplined re-shaping of what already works, shipped in
small verified steps.

---

# Part II — Where ELI actually is today  [plain + tech]

**[plain]** Be honest about the starting line, because the plan depends on it.

The project folder is called `ELI_v3.0` and the app version is `3.0.24`, but the *code inside*
is still essentially v2 carried forward. The genuinely-new v3 architecture described in your
own roadmaps (the "spine" below) is written down in the blueprints but **not yet built in the
code**. That is not a failure — it is exactly the work this report sequences. Naming it plainly
means we measure progress against reality, not against the folder name.

**[tech]** Concrete state, from a direct read of the tree:

- **~151,000 lines** of Python under `eli/`, across ~30 subsystems.
- **Test surface:** 225 test files, ~24k lines; the suite runs green on the import/kernel/
  integration lanes and an offscreen GUI lane. This is the safety net the whole plan leans on.
- **The five monoliths** (a third of the codebase):

| File | Lines | Shape | Imported by | Risk to touch |
|:-----|:------|:------|:------------|:--------------|
| `execution/executor_enhanced.py` | 14.6k | 1 `Executor` class + 185 module functions | 51 files | High |
| `kernel/engine.py` | 13.5k → 6.977k | 1 `CognitiveEngine` decomposed into 6 stage mixins under `kernel/stages/` (13 files); `process()`+`__init__` stay | done | High |
| `gui/gui_eli_original.py` | 11.6k | 13 classes (`EliMainWindow` ~9k) | 2 files | Low |
| `execution/router_enhanced.py` | 7.3k | 93 functions, regex to intent | 39 files | High |
| `gui/labs_tab.py` | 5.7k (done) | 14 tab classes | 3 files | Low |

- **The v3 spine is absent in code:** a search finds no `Planner`/`Effector` split, no typed
  `Plan` object, no `ModelRouter`, no MCP-style tool registry. The DAG substrate (`core/dag.py`)
  and the 14-agent bus (`cognition/agent_bus.py`) *do* exist and are the seeds to build on.
- **Productization machinery already exists:** signed licences with tiers
  (`trial/personal/pro/enterprise`), per-tier EULAs, a plugin registry, and a 208-entry
  capability manifest (though the manifest has no `category` field yet — sector tagging is
  greenfield).

**The strategic reading:** ELI's gap to "great v3 product" is *not* features. It is structure,
latency, and the ability to specialise. Fix structure first; everything else unblocks.

---

# Part III — The thesis: why local, why editions  [plain]

**Why local is the moat.** Every cloud lab is racing toward "an agent that knows you and acts
for you." They hit a wall ELI does not: they cannot have your data, your machine, or your
continuity — not per person, not at scale, not without it being a privacy and cost nightmare.
ELI is the inverse: it already lives on your machine, offline by default, with your whole
context local. So the strategy is not "catch the frontier." It is **claim the slice of the
frontier that structurally requires being local** — private per-person learning, sovereign
memory, verifiable grounding against the real machine, true desktop and home embodiment. Those
are defensible *by construction* because they are expensive and un-monetisable for a cloud
giant.

**Why editions.** A single "does-everything" assistant is hard to sell and hard to trust,
because a stranger cannot tell what it is *for*. A **research** tool that cites everything, a
**clinical** scribe that never gives medical advice, a **legal** drafter with jurisdiction
disclaimers, a **coding** agent that ships verified diffs, a **content** studio with brand
voice — these are legible products with clear buyers and clear promises. Same core engine,
seven honest faces.

**Why "shared core + packs" and not seven forks.** For a solo maintainer, seven copies of a
150k-line codebase is a slow death — every fix merged seven times. The same *product* outcome
(seven diverging paid editions) is achieved far more cheaply by keeping **one** maintained core
and layering small **edition packs** on top. The analysis in Part V shows this is not wishful:
~90% of the code is genuinely shared, and the sector-specific part is thin and localised.

---

# Part IV — The v3 architectural spine  [tech]

**[plain]** These four foundations are what turn "a big pile of working code" into "an
architecture one person can carry." Build them first; everything downstream gets cleaner.

### 4.1 Planner / Effector split

The 2026 consensus shape for a safe agent: a pure **Planner** that decides, and a thin
**Effector** that acts, with a validation gate between them and an audit trail after.

```
intent + args + world_state
        │
        ▼
   ┌─────────┐        Plan{action, args, preconditions,
   │ Planner │ ─────▶ expected_effect, risk_class, undo}   (pure, no side-effects, 90%+ testable)
   └─────────┘
        │  Plan | Refusal
        ▼
   [approval gate] ── risk? ──▶ ask user
        │
        ▼
   ┌──────────┐
   │ Effector │ ─────▶ one side-effect ─────▶ StructuredResult ─────▶ audit ledger
   └──────────┘
```

- **Migration = the executor decomposition.** Splitting `executor_enhanced.py` into
  `effectors/*` (Part V) *is* this work: each effector becomes the thin actor; the typed `Plan`
  and the `dispatch.py` façade become the planner side. You do not do this as a separate
  project — it is the same strangler passes.
- **Payoff:** every action becomes planned → validated → approved → logged → replayable.
  Testable, auditable, reversible. This is also what makes "explain why you did X" possible.

### 4.2 MCP-style declarative tools

Capabilities become **typed schemas + a policy predicate**, not hand-wired handlers. The 208
aliased actions collapse to a clean registry (~110 distinct). Each tool: a JSON schema (args,
types) + who may call it + risk class + confirmation rule. Adopt an MCP/A2A-style protocol so
ELI can both *expose* its tools and *consume* third-party tools under one approval gate. The
sprawl becomes data; an ecosystem (and per-edition tool sets) becomes possible.

### 4.3 The DAG as the universal substrate

`core/dag.py` already runs the 14-agent bus. v3 runs *everything* multi-step on it —
declarative, replayable, observable pipelines with retries, fallbacks, caching, telemetry. A
multi-step task becomes a graph you can inspect and replay, not a call-stack to reverse-
engineer. This is the substrate the reasoning modes, deep-research, and overnight builds all sit
on.

### 4.4 The model-router

Stop running one model for everything. Route by task: a small 1–3B model for intent/routing and
JSON, the big model for reasoning, the vision model on demand, an embedder always warm. A fast
classifier tags each turn (route / chat / reason / see / code); the DAG dispatches to the right
warm model; the existing VRAM smart-loader manages the budget. **This is the single biggest
felt-speed win** — and it honours "keep the big model" by spending its latency only where it
earns it.

---

# Part V — The decomposition program  [tech]

**[plain]** This is the heart of the near-term work: breaking the five giant files into many
small, sensibly-named ones — safely, without ever breaking the running app. The method below is
not theoretical; it has already been run twice this session and verified green.

### 5.1 The strangler method (the safe recipe)

For each cohesive group of functions:

1. **Move it verbatim** into a new, well-named module. No behaviour change.
2. **Leave a re-export shim** at the old file path, so every existing `import` keeps working
   untouched — including the frozen PyInstaller build spec.
3. **Run the tests** for that surface. Green or revert.
4. **Commit** that one slice with a message that records what moved and what verified it.
5. Repeat. Dead code is dropped as each group moves; comments are trimmed to what earns its
   place.

Why it is safe: nothing observable changes per step, the blast radius is one group, and any
slice is revertible on its own. Why it is honest: the shim means "done" is provable by the test
suite, not by hope.

### 5.2 Proven this session

- **`labs_tab.py` (5,694 lines) → `eli.gui.labs`** — 16 focused modules (one per sub-tab) + a
  shared preamble + a 30-line shim. Verified: 14/14 offscreen widget tests on the labs surface,
  7/7 report-builder + frontier contracts, 206/206 import lane.
- **Phase 45 fast-path → `eli.kernel.stages.fast_actions`** — the direct-action sets and helpers
  lifted out of `engine.py` (−137 lines), imported back so callers are unchanged. Verified 215
  pass.

### 5.3 The grouping (evidence-based, from an AST + name-prefix analysis)

Each family below maps to a target module and is tagged for its **edition affinity** — **Core**
(every edition), **Core+** (core but per-edition tunable), or a sector name (candidate to lift
into an edition pack). Full tables live in `v3_monolith_decomposition.md`; the shape:

**`executor_enhanced.py` (185 fns) → `eli/execution/effectors/`**

```
media.py           _spotify_* _mpv_* _yt_* _playerctl_* _duck_* play_*      ~640   Core
apps_windows.py    _eli_tile_* _open_app_* open_browser _normalize_app       ~340   Core
files_notes.py     _eli_safe_* _open_file _append_note _save_* _scan_*       ~450   Core
tts_speech.py      _speak* _tts_*                                            ~200   Core
memory_ops.py      memory_* _format_memory_* search_memory* _convlog_*       ~450   Core
system_intel.py    _runtime_* _format_* _explain_* _*_audit_report _gpu_*    ~800   Core
inference_backends _ollama_*                                                 ~75    Core
self_maintenance   self_test proactive_* persona_lock_* _verify_persona      ~250   Core+
codegen.py         _maybe_background_codegen _maybe_background_file_analysis  ~140   CODING
dispatch.py        execute _execute_impl _action_pre/post Executor           ~6k    Core (façade)
```

**`router_enhanced.py` (93 fns) → `eli/execution/routing/`** — `route` stays as the matcher
chain; domain matchers split into `intents/` (contracts, memory, voice, media, self_ops,
web_info→Research, documents→Research/Content). Precedence order is load-bearing — preserve it.

**`engine.py` (94 methods) → `eli/kernel/stages/`** — `process` stays as the thin orchestrator;
stages split into `generation.py`, `prompt_assembly.py` (Core+), `reasoning_modes.py` (Core+),
`retrieval.py`, `memory_commit.py`, `lifecycle.py`, plus the done `fast_actions.py`.

**`gui_eli_original.py` (13 classes) → `eli/gui/main_window/`** — already class-structured;
split by class into `window.py` / `adapters/` / `models/` / `widgets/`. Low risk (2 importers).

**`agent_bus.py` (16 agent classes) → `eli/cognition/agents/`** — `_BaseAgent` + one file per
agent (`memory`, `system`, `habit`, `self_improvement`, `proactive`, `frontier`, `plugin`,
`capability`, `voice`, `orchestrator`, `filecode`, `introspection`, `reflection`,
`knowledge_graph`). Clean seams; each agent is a self-contained `run()`.

### 5.4 Verification & rollback discipline

- Every slice: `py_compile` → targeted pytest lane → commit. High-risk files (executor, router,
  engine) additionally get a symbol-identity check (the shim re-exports the *same objects*) and
  a behaviour spot-check.
- Every slice is a standalone commit on the `v3-modularisation` branch, so any one is
  `git revert`-able without unwinding the rest.
- The offscreen GUI lane has one **pre-existing** crash unrelated to this work
  (`FirstBootWizard` makes a network probe in its constructor and aborts in a sandbox) — flagged
  for a later guard, not a blocker.

---

# Part VI — The editions / productization layer  [plain + tech]

**[plain]** This is how ELI becomes a *family* of products without becoming seven codebases.

### 6.1 Two independent dials

- **Licence tier** — `trial / personal / pro / enterprise`. Governs seats, commercial rights,
  support, updates. Already built in `eli/licensing`.
- **Edition** — `general / research / healthcare / legal / coding / content / enterprise`.
  Governs which domain capability pack(s) switch on. New, scaffolded this session in
  `eli/editions`.

A licence carries a tier **and** entitles one or more editions. "ELI Research, Pro" = the
research pack + a 3-seat commercial licence. The two dials are orthogonal, which keeps pricing
and packaging flexible.

### 6.2 Anatomy of an edition pack  [tech]

Every pack is the same shape (copy `base/` to start a new one):

```
eli/editions/<sector>/
  manifest.json     name, label, personas, capability categories, templates, suggested tiers
  policy.py         hooks the core calls: system_prompt_addendum(), output_disclaimer(),
                    blocked_actions()
  capabilities/     sector capabilities that compose onto the core registry
  personas/         sector personas that feed the emergent persona brief
  templates/        report/doc templates the sector adds
  tools/            sector-specific declarative tools (under the approval gate)
```

The core stays **domain-blind**: it reads the manifest and calls the policy hooks; the pack
supplies all divergence. That is precisely why one core can serve seven products.

### 6.3 Dynamic composition — how a sector "plugs in"  [tech]

1. **Core always loads** — every Core / Core+ module from Part V is in every build.
2. **Editions overlay, never fork.** A pack contributes capabilities, a persona, templates, and
   policy hooks. The core asks the registry what is active; it does not branch on sector.
3. **Core+ is the tuning surface.** Personas, reasoning-mode selection, and prompt assembly read
   the active edition's manifest — so the *same* engine behaves like a research analyst or a
   staff engineer with no forked code.
4. **Licence entitlements pick the packs.** `editions.active_editions(entitled)` resolves which
   overlays compose. Wire `entitled` to a new `editions` field on the signed licence payload;
   enterprise tier implies all editions.

**[plain]** The upshot: adding a new sector later (say, "ELI Education") is writing one small
pack, not forking the app. That is the "dynamic integration of sectors" this is built for.

### 6.4 What each launch edition specialises in

| Edition | Specialises in | Sector-specific slice it lifts from core |
|:--------|:---------------|:-----------------------------------------|
| General | the full local assistant, no specialisation | none (the baseline) |
| Research | deep-research, citations, literature review | web_info + documents routing; academic report templates |
| Healthcare | clinical documentation (never medical advice) | clinical note templates; PHI-redaction; disclaimer policy |
| Legal | contract/clause drafting (never legal advice) | legal templates; statute-citation; disclaimer policy |
| Coding | repo-scale agent, test-gen, verified diffs | codegen effectors; coding tab; the strongest existing surface |
| Content | long/short-form, brand voice, image-gen | copy/social templates; brand-voice persona; diffusion |
| Enterprise | all editions + governance | RBAC, audit export, fleet config; bundles the rest |

---

# Part VII — The v3 roadmap  [plain + tech]

**[plain]** Two tracks run in parallel: the **software** track (the assistant itself) and the
**home** track (the smart-home mesh, paced by cheap hardware). Each phase ships something usable
and has an **exit gate** — you do not start the next until the last is green. That gate is the
single most important discipline in the whole plan; it is what stops scope creep sinking a solo
project.

### 7.1 Software track

**Phase 0 — Structure (in progress now).** *The prerequisite.* Decompose the five monoliths;
stand up `editions/`; extract Phase 45. **Exit:** all five monoliths under ~2k lines each behind
shims; test lanes green; editions registry resolves packs. *(Roughly: labs and Phase 45 done;
executor, router, engine, gui remaining.)*

**Phase v3.0 — Foundation.** Planner/effector for the first action family; the typed `Plan`;
the MCP tool registry with ~10 migrated tools; the model-router live with 2+ warm models.
**Exit:** a plain command round-trips plan → approve → effect → ledger; median simple-turn
latency down measurably; zero unlogged actions. *Then ship it to one real user before anything
else — that single act moves the project further than any feature.*

**Phase v3.1 — Knows me.** A background pass that builds a genuine learned model of the user
(interests, projects, tone, routines — each with confidence + decay) feeding the persona;
temporal memory (bi-temporal edges — reason over how you *changed*); nightly consolidation
("sleep") that promotes recurring episodes to stable facts. **Exit:** a blind A/B where you can
tell v3's briefing from v2's; the recall benchmark holds or improves.

**Phase v3.2 — Acts well.** Verify-then-act on the top grounded actions (propose, generate a
check, verify against real machine state, then commit); repo-scale coding context (a codebase
graph); the fused deep-research + Report Builder pipeline. **Exit:** self-check catches a
measurable share of would-be-wrong answers; a real overnight feature-build lands as a reviewable
diff with passing tests.

**Phase v3.3 — Gets better.** Close the autonomy loop (detect → propose → learn from
accept/reject); RLVR-from-corrections (every "no, not like that" becomes a private preference
pair training a small adapter); eval-gated self-tuning (keep a change only if the harness score
rises, log the delta, one-click rollback). **Exit:** proposal-acceptance rate trends up over
weeks; a kept self-tuning change with a logged metric delta and a rollback. *This is the
signature, cloud-can't-copy work.*

**Phase v3.4 — Sees ahead.** Continuous structured screen understanding (a rolling model, not
one-shot glances); a first desktop world-model prediction behind a light confirm (gaze rests on
a failed build → "rerun?" appears where you are looking). **Exit:** one genuinely useful
anticipatory offer fires correctly more often than it annoys (measured accept-vs-dismiss).

### 7.2 Home track (parallel, hardware-paced)

| Phase | Ships | Why |
|:------|:------|:----|
| H0 | Matter/Thread + the home graph (rooms, devices, adjacency, occupancy) | vendor-agnostic devices + spatial "the bedroom light" resolution |
| H1 | Snapcast multi-room audio + per-room targeting | "play X on the kitchen speaker" |
| H2 | first ESP32-S3 room node (reflex control + wake-word + presence) | the edge tier begins |
| H3 | failover mesh (broker off the PC, heartbeats, reflex degradation) | the house stays smart when the PC sleeps |
| H4 | follow-me audio + continuous cross-room ELI | the delightful, nobody-else-has-it layer |
| H5 | home world-model + backup-brain election | predicts, heals, reasons |

**[plain]** Start v3.0 (software foundation) and H0/H1 (spatial + multi-room audio) — the
immediate wins that need no new "brains." Everything else earns its place after.

---

# Part VIII — Beyond v3: the frontier and the moonshots  [plain]

Where ELI can plant flags first, because it is local + private + embodied:

1. **The sovereign personal agent** — an assistant whose entire model of you never leaves your
   machine and is yours to delete. A *category*, not a feature.
2. **Autobiographical intelligence** — a coherent, evolving narrative of your life and projects
   ELI reasons over: "you tried this in March and it stalled; here is why now differs."
3. **Private continual personalisation** — the first assistant that measurably gets better *at
   being yours* the longer you use it, from your corrections, on device, forever.
4. **Predictive embodiment** — a desktop *and* home that anticipate: pre-stage the next app,
   surface the file, catch the error, dim the sitting room at 22:40.
5. **Verifiable local grounding as a moat** — because ELI *has* the machine and the home, its
   answers and actions can be checked against reality. Lean all the way in.
6. **New organs** enabled by being local: private meeting/ambient-audio intelligence; "ask my
   computer anything" (local RAG over your whole filesystem, cited to the real file);
   multi-device continuity (encrypted peer-to-peer between *your* machines); a life-admin agent
   for the sensitive data people will never upload; and an accessibility mode (gaze + duplex
   voice + anticipation into a first-class hands-free computer).

**Editions as the beyond-v3 growth engine.** Once the pack mechanism is solid, new sectors are
new products at low marginal cost — Education, Finance, Field-Service, Accessibility-first — each
a manifest, a persona, some templates, and a policy. The core keeps improving; every edition
inherits it for free.

---

# Part IX — Execution discipline for a solo maintainer  [plain]

The failure mode here is not incapability — it is scope creep at 2× the size, alone. The
antidotes are structural, and most are already in force:

- **Saying no is the feature.** Freeze each phase before starting the next. A phase line you
  refuse to cross.
- **A commit per verified slice.** Small, reversible, test-backed. Never a giant unreviewable
  diff. (This session's cadence.)
- **The tests are the contract.** "Done" means the lane is green, not that it looked right.
- **Everything eval-gated.** Self-tuning, memory changes, and autonomy only land if a measurable
  score holds or rises — so regressions surface automatically instead of being discovered by a
  user.
- **Proposal-only, approval-gated, instant-undo** for anything that acts or learns on its own.
  Start with *offer*, never *act*.
- **Dead code out, comments lean.** Each file that moves gets tidied on the way through.
- **The spine exists so one person can carry it.** That is the entire point of Part IV — it is
  not architecture for its own sake.

Suggested working order (each is a self-contained, shippable unit):

1. Finish the monolith decomposition: executor effectors → router intents → engine stages →
   agent-bus agents → gui main-window. (Structure.)
2. Turn the executor split into the real planner/effector + typed `Plan` + ledger. (v3.0 spine.)
3. Model-router + MCP tool registry. (v3.0 speed + ecosystem.)
4. Wire editions to licence entitlements; lift the first sector slice (coding). (Productization.)
5. Learned user-model + temporal memory. (v3.1, the felt-quality leap.)
6. Everything after, phase-gated.

---

# Part X — Metrics, risks, de-risking  [plain + tech]

### 10.1 The metrics that define "working" (measure on weak hardware, not the dev box)

1. **Cold-start → first useful turn** (seconds) — the biggest first-impression number.
2. **% of turns under the latency target** — the model-router's job.
3. **"Did-what-it-said" rate** for core actions (executed == claimed) — target ~100%.
4. **Confabulation rate** on grounded/introspection questions — target ~0.
5. **"Knows me"** — a blind A/B where v3's persona/briefing is distinguishable from v2's.
6. **Proposal-acceptance rate** trending up over weeks (the closed loop is learning).
7. **Planner coverage ≥ 85%** and **zero unlogged actions** (auditability).
8. **A proven "nothing left the machine"** property (sovereignty as a verifiable claim).

### 10.2 Honest failure modes and their mitigations

| Risk | Mitigation |
|:-----|:-----------|
| Scope creep (the v2 wound, doubled at 2× size, solo) | phase gates; a frozen feature line; ship v3.0 to a real user before v3.1 |
| Refactor breaks the shipping app | strangler + shims + tests per slice; per-slice revert; frozen-build spec untouched |
| Continual-learning instability / forgetting | adapter-only (frozen base); eval-gated nightly refresh; one-click rollback via the ledger |
| Autonomy / anticipation over-reach | proposal-only + approval-gated + instant undo; confidence-gated; offer, never act |
| Editions become seven forks by accident | one core, packs overlay; the registry, not build flags; enforce "core is domain-blind" |
| Maintainability at 2× LOC, solo | the spine (Part IV) exists precisely so one person can carry it; everything eval-gated |
| Support does not scale to one person | excellent first-run autodetect; in-app self-diagnose; docs over hand-holding |

---

# Appendix A — Target module map  [tech]

```
eli/
  execution/
    effectors/        media apps_windows files_notes tts_speech memory_ops
                      system_intel inference_backends self_maintenance codegen
    routing/          router.py  intents/{contracts,memory,voice,media,self_ops,web_info,documents}
    dispatch.py       thin Executor façade + typed Plan (v3.0 spine)
  kernel/
    engine.py         thin CognitiveEngine orchestrator (process)
    stages/           generation prompt_assembly reasoning_modes retrieval
                      memory_commit lifecycle fast_actions(done) guards/
  cognition/
    agents/           base + one file per bus agent
    orchestrator.py   DAG-based agent composition
  gui/
    labs/             (done) one module per sub-tab
    main_window/      window.py adapters/ models/ widgets/ tabs/
  editions/           __init__ (registry) + base research healthcare legal
                      coding content enterprise   (manifest + policy + capabilities/personas/templates/tools)
  licensing/          tiers, activation, signing (edition entitlements added)
  core/ memory/ perception/ planning/ world/ runtime/ ...   (shared core, unchanged)
```

# Appendix B — Glossary  [plain]

- **Monolith / god-file** — one source file that has grown far too large and holds many
  unrelated jobs. Hard and risky to change.
- **Strangler refactor** — replacing a big thing piece by piece while it keeps running, instead
  of a risky big-bang rewrite.
- **Shim** — a tiny stand-in left at an old location that forwards to the new one, so nothing
  that relied on the old location breaks.
- **Planner / Effector** — the "decide" half and the "act" half of an agent, kept separate so
  decisions can be checked and logged before anything happens.
- **DAG** — a flowchart of steps with no loops; ELI uses one to run multi-step tasks in a way
  you can inspect and replay.
- **Model-router** — a traffic controller that sends each request to the smallest model that can
  handle it, so simple things feel instant.
- **RLVR (reinforcement learning from verifiable rewards)** — the system improves by checking its
  work against something *real* (did the build pass? did the file open?) rather than a guess.
- **LoRA adapter** — a small, cheap add-on to a big model that can be trained on *your*
  corrections without retraining the whole model.
- **Edition / edition pack** — a small overlay that specialises ELI for one sector (research,
  legal, …) without copying the whole codebase.
- **Licence tier** — the commercial level of a licence (seats, commercial use, support);
  separate from which edition you have.
- **Sovereign memory** — the idea that ELI's model of you lives only on your machine and is yours
  to delete.

# Appendix C — Progress log (this session)  [tech]

Branch `v3-modularisation`, all steps test-verified:

1. `labs_tab.py` (5.7k) → `eli.gui.labs` (16 modules + shim). 14/14 offscreen, 7/7 contracts.
2. `eli/editions/` scaffolded — working registry + 7 packs; reserved `effectors/`, `routing/`,
   `stages/` targets with the move-map.
3. Phase 45 → `eli.kernel.stages.fast_actions` (engine −137 lines). 215 pass.
4. `v3_monolith_decomposition.md` — the evidence-based grouping + edition-affinity mapping.
5. This report.

*Next: executor effectors, beginning with `media.py` — the cleanest family, same verified
pattern, one commit.*

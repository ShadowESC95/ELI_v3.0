# ELI Blueprints

Grounded architecture documentation for ELI v3. Each doc reads the real code,
states what's there, and gives an honest strength/weakness assessment. Written
from deep reads of the core + a full structural/code-health sweep — covering every
package's structure + the algorithm-bearing bodies; claims are grounded in what was
inspected.

> **Updated for 3.0.172.** Cognition unified with the v2 line (gradient orchestrator, shared
> retrieval, S01–S12 tracing, memory storage policy). **233,521 lines across 608 `.py` files** in `eli/`,
> **242** capabilities (**200** routable, **224** in `SUPPORTED_ACTIONS`, **230** in either). See
> **[docs/ARCHITECTURE_MAP.md](../docs/ARCHITECTURE_MAP.md)**.
>
> **Also:** two-front local-first system — PySide6 desktop **and** FastAPI web app
> (`api/server.py`) with MQTT smart-home, RBAC, tamper-evident audit, browser voice.
> **[state_snapshot.md](state_snapshot.md)** holds the last full measured sweep.

## Index

- **[V2_VS_V3_COMPARISON.md](V2_VS_V3_COMPARISON.md)** — v2 (shipping) vs v3
  (in development): modularisation, editions/licensing, what's identical.
- **[MONOLITH_DECOMPOSITION_MAP.md](MONOLITH_DECOMPOSITION_MAP.md)** — exactly how
  the five god-files broke down, where every function family now resides, and why
  it is more customisable + easier to maintain (measured).
- **[GODFILE_SESSION_DIFF.md](GODFILE_SESSION_DIFF.md)** — the five god-files'
  change over the refactor session: start→now tables + the executor's
  commit-by-commit strangle (14,592 → 6,442, −56% at the time; the executor is 15.9k lines again today, see `architecture.md`).
- **[EXTENDING_ELI.md](EXTENDING_ELI.md)** — hands-on guide: step-by-step
  procedure, commands, and files to add your own feature/capability (effector +
  registry), plus the plugin and edition extension surfaces.
- **[REFACTOR_LOG_V3.md](REFACTOR_LOG_V3.md)** — measured before/after of the
  refactor: monolith LOC, new packages, the effector→action map.
- **[executor_decomposition.md](executor_decomposition.md)** — the effector
  registry + god-file strangler (in progress on `v3-modularisation`).
- **[what_eli_can_do.md](what_eli_can_do.md)** — the capability showcase: every
  thing ELI can actually do (conversation, OS/window control, gaze, voice, vision,
  image generation, media, web/news, files, Report Builder, File Chat, coding,
  memory, self-improvement, proactivity), the 13 tabs, and full customisability.
  Strengths only, grounded, no embellishment. The "what you get" doc.
- **[what_eli_is.md](what_eli_is.md)** — the human-first portrait: what ELI is and
  what it does for your actual day (layman + tech head), with the genuine selling
  points foregrounded — ownership, model-agnosticism, self-honesty,
  self-improvement, embodiment. Read this for the "why it matters."
- **[complete_findings.md](complete_findings.md)** — the definitive deep-read
  record (2026-06-07): corrected numbers, every correction to earlier statements,
  verified subsystem facts, the fixes shipped this session, and the honest limits.
  Unbiased, just facts.
- **[capability_catalogue.md](capability_catalogue.md)** — exhaustive, ground-truth
  catalogue of all actions (242) + every package's modules.
- **[project_overview.md](project_overview.md)** — start here for the engineering
  view. What ELI is, scale (~234k LOC / 608 files + web app), architecture by layer, the
  honest verdict on "frontier", and the highest-leverage work.
- **[orchestration_and_agents.md](orchestration_and_agents.md)** — the real
  topology: gradient `AgentOrchestrator` (all CHAT modes) composing the 15-agent `AgentBus` after shared retrieval; see `orchestration_and_agents.md`.
  the ReAct loop, the typed `ExecutionPlan`, selection, timeouts, trust.
- **[memory.md](memory.md)** — SQLite/FTS5 + FAISS + knowledge graph, the
  `recall_memory` hybrid retriever, weight decay, the `Memory` god-class.
- **[grounding_and_evidence.md](grounding_and_evidence.md)** — the
  anti-confabulation layer: deterministic grounding gate, evidence ledger/
  arbitration, control contracts, output governor, grounded remediation.
- **[security.md](security.md)** — fail-closed shell/path/app gates, prompt-
  injection guard, SQL identifier validation, agent trust registry, vetted-script
  approach.
- **[perception.md](perception.md)** — local vision (hot-swap + co-resident),
  STT (+ output ducking), TTS (Piper/espeak), cross-platform OS control.
- **[inference_and_hardware.md](inference_and_hardware.md)** — model-agnostic
  GGUF inference + broker + lock, free-VRAM-aware hardware profiling, adaptive
  boot, settings, paths.
- **[learning.md](learning.md)** — the LoRA self-training pipeline (human-gated,
  PII-redacted, operator-invoked; trains a separate Phi-3 base).
- **[gui.md](gui.md)** — the PySide6 desktop app, launcher/first-boot, Labs
  scientific workspace.
- **[runtime_planning_world.md](runtime_planning_world.md)** — runtime response/
  introspection surfaces, the proactive planning layer, the world/autonomy model,
  tools, and the plugin system.
- **[coding_agent.md](coding_agent.md)** — the frontier coding agent (`eli/coding/`):
  planner/implementer, mandatory execution feedback, UCB tree search, verification
  gating, test synthesis, semantic bug classification, long-term bug/fix memory,
  patch-based refinement. Wired as the `CODE_SOLVE` action.
- **[coding_agent_test_prompts.md](coding_agent_test_prompts.md)** — runnable prompt
  suite for exercising the coding agent against the live model.
- **[dag.md](dag.md)** — the project-wide DAG engine (`eli/core/dag.py`) and how the
  **agent bus** and the **coding engine** both run on it (topological layers,
  upstream→downstream, subtask graphs).
- **[background_tasks.md](background_tasks.md)** — unified code generation (GENERATE_SCRIPT
  + self-upgrade route through the coding agent) and the in-process background task
  manager (heavy work runs on threads; `CHECK_JOB`/`BACKGROUND_JOBS`).
- **[state_snapshot.md](state_snapshot.md)** — the full measured sweep of 2026-07-01:
  scale, capabilities, tests, GPU, and the changes up to then. For current figures see
  `architecture.md` and `docs/ARCHITECTURE_MAP.md`.
- **[dag_orchestrator.md](dag_orchestrator.md)** — the DAG elevated to a full execution
  orchestrator (parallel/retries/fallback/cache/telemetry), wired through agents +
  evidence gather; Test & Review; SELF_IMPROVE/GENERATE_PROJECT on the coding/planner DAG;
  multi-command + timed commands.
- **[lora_pipeline.md](lora_pipeline.md)** — LoRA audit + the wired, model-agnostic
  training pipeline (preflight→build→train→eval) + `LORA_STATUS`/`LORA_TRAIN`.
- **[installation.md](installation.md)** — one-click cross-platform installers
  (Linux/macOS/Windows/Android), frozen lock, GPU verify, DB init, CUDA-toolkit option.
- **[decomposition_plan.md](decomposition_plan.md)** — full code-review + god-file
  decomposition plan (keep good code; fix real problems) — **plan only**.
- **[proposal_total_awareness.md](proposal_total_awareness.md)** — dated proposal
  (test→report→ELI loop, total-awareness roadmap, GPU erratum).

### Web app, server & operations
- **[server_frontier_roadmap.md](server_frontier_roadmap.md)** — the self-hosted web
  app (`api/server.py`): chat, live dashboard, ELI's own MQTT smart-home (rooms, scenes,
  real automations), RBAC, the tamper-evident audit trail, research collaboration, browser
  voice, the monitored Internet toggle — what shipped and what's next.
- **[operations.md](operations.md)** — running ELI day-to-day: processes, scheduled/
  overnight tasks, the proactive daemon, artifacts, logs.
- **[running_eli_at_scale.md](running_eli_at_scale.md)** — multi-user / server-mode
  deployment considerations.
- **[ELI_USER_MANUAL.md](ELI_USER_MANUAL.md)** — the end-user manual (desktop + web).
- **[productization_path.md](productization_path.md)** — the path from project to product.

### Architecture & algorithms (reference)
- **[architecture.md](architecture.md)** / **[architecture_ascii.md](architecture_ascii.md)**
  — the layered architecture (prose + ASCII topology).
- **[agent_algorithms.md](agent_algorithms.md)** — the algorithm-bearing bodies of the
  agents and the coding engine.
- **[code_mode_execution_layer.md](code_mode_execution_layer.md)** — the code-mode
  execution layer.
- **[adaptive_inference_governor.md](adaptive_inference_governor.md)** — design notes for
  hardware-/latency-aware inference governance (reference design; the resident model is a
  deliberate user choice and is not auto-swapped).
- **[diagrams.md](diagrams.md)** — system diagrams.
- **[eval_harness.md](eval_harness.md)** — the evaluation harness (router + engine cases).

### Status, audit & history
- **[project_audit.md](project_audit.md)** — full project audit (numbers, claims, health).
- **[executor_decomposition.md](executor_decomposition.md)** — the god-file decomposition
  plan for the executor (plan only).
- **[session_2026-06-28_web_app_home_security_and_routing.md](session_2026-06-28_web_app_home_security_and_routing.md)**
  — this week's work (web app, smart-home, security, routing).
- **[session_2026-06-18_hardening_scaling_and_release.md](session_2026-06-18_hardening_scaling_and_release.md)**
  — the prior session (hardening, scaling, release).

### Models & fine-tuning (reference)
- **[finetuning_guide.md](finetuning_guide.md)** — the fine-tuning guide.
- **[model_bakeoff_dossier.md](model_bakeoff_dossier.md)** /
  **[local_model_bakeoff_2026-06-17.md](local_model_bakeoff_2026-06-17.md)** /
  **[top2_head2head.md](top2_head2head.md)** — local-model evaluation dossiers.
- **[eli_marketing_and_outreach_playbook.md](eli_marketing_and_outreach_playbook.md)** —
  marketing & outreach playbook.

## Cross-cutting themes (recurring across docs)

1. **Strong ideas, several genuinely frontier subsystems** — the grounding/
   evidence layer, fully-local model-agnostic inference, fail-closed security,
   integrated multimodality.
2. **The dominant weakness is engineering discipline, not vision:**
   - **God-files (largely dismantled in v3)** — the two GUI monoliths are gone
     (`gui/main_window/`, `gui/labs/`), `engine.py` is 8,521 LOC with 9,245 more in the
     `kernel/stages/` mixins, `router_enhanced.py` 7,196 (intent families in
     `routing/intents/`), `executor_enhanced.py` 7,588 (registered actions run in
     `effectors/`). See `MONOLITH_DECOMPOSITION_MAP.md` for how they were taken apart.
   - **Over-fragmentation / "added beside, not folded in"** — ~15 overlapping
     `runtime/` response surfaces, two image engines, seven stacked
     `render_action` overrides in the grounding gate, multiple plan/queue
     representations; and the routing+verbatim logic decided across
     router/engine/GUI (the source of by-path inconsistencies).
   - **~4,560 `except Exception`** handlers (192 of them a silent `pass`, held under a
     ceiling by `tests/claims/test_no_silent_swallow.py`) — failures turned into
     fallbacks, the reason some bugs only surface via runtime logs.
   - **Repo hygiene** — root junk, committed binaries/diag outputs.
3. **The gap to "ground-breaking" is subtraction + observability**, in order:
   tame error-swallowing → split god-files → delete duplication/clutter →
   consolidate the `runtime/` surfaces. *(The last full measured sweep written up is
   `state_snapshot.md`, 2026-07-01.)*

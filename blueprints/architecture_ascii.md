# Blueprint — ELI Full Architecture (ASCII)

> **Updated for 3.0.169.** All CHAT modes → gradient orchestrator; bus composed at S06.

The entire system in one drawing, plus the module tree and data layout. Grounded
in the real source (see `architecture.md` for prose, `diagrams.md` for the
pipeline/memory/gating close-ups). Every layer and box maps to a real path.

---

## A. Full system — layered block diagram

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║                                ELI — FULL ARCHITECTURE                             ║
║                100% local  ·  offline-by-default  ·  model-agnostic                ║
║               233,521 LOC · 608 files · desktop GUI + web app server               ║
╚══════════════════════════════════════════════════════════════════════════════════╝

┌─ PRESENTATION ────────────────────────────────────────────────────────────────────┐
│  GUI (PySide6)                  CLI headless              Voice I/O                 │
│  gui/main_window/window.py       cli/headless.py           perception/audio_stt (STT)│
│  gui/app.py · gui/panels/       (python -m eli -H)        perception/tts_router(TTS)│
│  gui/labs/ · EliWorld tab       ./eli.sh                  wake-word "computer"       │
└────────────────────────────────────────┬───────────────────────────────────────────┘
                                          │  text / command
                                          ▼
┌─ ROUTING ─────────────────────────────────────────────────────────────────────────┐
│  execution/router_enhanced.py :: route()      regex-first PRIORITY PIPELINE         │
│  + cognition/llm_intent.py (fallback)   route_contracts · command_parser ·          │
│    execution_planner · portable_intent_contract                                     │
└────────────────────────────────────────┬───────────────────────────────────────────┘
                                          ▼  {action, args, confidence, matched_by}
┌─ KERNEL ──────────────────────────────────────────────────────────────────────────┐
│  kernel/engine.py :: CognitiveEngine.process()   ← the orchestrating core (8.5k LOC, plus 9.2k in kernel/stages) │
│  scheduler · pipeline · state · world_model · self_upgrade                          │
└──────┬──────────────────────────────┬───────────────────────────────┬───────────────┘
       ▼                              ▼
 ╔═════════════╗            ╔════════════════════════════════════════╗
 ║[A] FAST-PATH ║            ║[B] CHAT + NON-CHAT (gradient orchestrator)║
 ║ PHASE45      ║            ║ all modes · retrieve_for_turn → bus     ║
 ║ verbatim,    ║            ║ cognition/orchestrator.py · pipeline_trace║
 ║ NO LLM       ║            ╚═════════════════╤══════════════════════╝
 ╚══════╤══════╝                              │
        │              ┌─ COGNITION ──────────┴──────────────────────────┐
        │              │  AgentBus (15 agents, composed at S06) + CodeAgent │
        │              │  ─────────────────────────────────────────────────────────────  │
        │              │  inference_broker ─► gguf_inference  (MODEL-AGNOSTIC GGUF)        │
        │              │  reasoning_modes: quick · CoT · self-consistency · ToT · const.  │
        │              │  hyde · reranker · context_synthesiser · working_memory          │
        │              │  persona.py/_updater/_values/_hygiene  (+ persona.txt overlay)    │
        │              └────────────────────────────┬─────────────────────────────────────┘
        │                                           ▼
        │              ┌─ GROUNDING SPINE (runtime/) ── the anti-confabulation core ──────┐
        │              │  netguard ░ persistence_gate ░ deterministic_grounding_gate(2.6k) │
        │              │  ░ grounding_escalation (low-conf → deeper agent tiers + retry)    │
        │              │  ░ evidence_planner (plan→gather→consume: code/web/memory/runtime) │
        │              │  ░ report_pipeline (multi-stage docs: outline→sections→review)     │
        │              │  ░ evidence_ledger · evidence_arbitration                          │
        │              │  ░ response_contracts ░ final_response_provider                    │
        │              │  ░ user_visible_response_surface ░ truth_report ░ output_governor  │
        │              └────────────────────────────┬─────────────────────────────────────┘
        ▼                                           ▼
┌─ EXECUTION ───────────────────────────────────────────────────────────────────────┐
│  execution/executor_enhanced.py  224 supported actions · 242 capabilities (200 routable)  │
│  effectors/ (199 registered actions) · media_runtime · operator_actions             │
│  PLUGINS(10): calendar document_reader media notes pomodoro weather                 │
│              system_stats tts web web_automation        eli/coding :: CodeAgent     │
└──────┬───────────────────┬─────────────────────┬───────────────────┬────────────────┘
       │ read / write       │                     │                   │
       ▼                    ▼                     ▼                   ▼
┌─ MEMORY ───────────┐ ┌─ PERCEPTION ────────┐ ┌─ LEARNING ──────┐ ┌─ WORLD ──────────┐
│ SQLite + FTS5      │ │ vision (VL hot-swap:│ │ LoRA self-train │ │ EliWorld         │
│ FAISS vectors      │ │   Moondream/Qwen-VL)│ │ (Phi-3 base)    │ │ world_event_bus  │
│ Knowledge Graph    │ │ STT(whisper)·TTS    │ │ dataset_builder │ │ local_world_     │
│ working memory     │ │ os_controller·screen│ │ lora_trainer/   │ │   bridge         │
│ user.db + agent.db │ │ gaze·ambient_vision │ │   eval/guard    │ │ kernel/world_    │
└────────────────────┘ └─────────────────────┘ └─────────────────┘ │   model          │
                                                                    └──────────────────┘

┌─ CORE INFRA (cross-cutting — used by every layer) ────────────────────────────────┐
│  netguard (process-wide offline failsafe)   paths/portable_paths/legacy_paths      │
│  runtime_settings · config   hardware_profile · startup_hardware_optimizer ·       │
│  load_plan                model_download (curated GGUF)   setup/wizard              │
└────────────────────────────────────────────────────────────────────────────────────┘

┌─ BACKGROUND DAEMONS (continuous, started at boot) ────────────────────────────────┐
│  proactive_daemon ─ pattern signals      self_improvement ─ learns from failures   │
│   └─ autonomy tick (30-min, governed): code_monitor + self-model overlay refresh   │
│      + goal/scheduler ticks → proposals (observe-only / memory-write; need approval)│
│  habits_scheduler/habits ─ routines      scheduler ─ jobs                          │
│  background_tasks ─ async heavy work     reflection loop   ambient_vision loop      │
│  scheduled_tasks (durable overnight/timed)   world_event_bus ◄─ confidence events   │
└────────────────────────────────────────────────────────────────────────────────────┘

           OUTPUT  ◄── output_governor / sanitiser ◄── inference ◄── (any path)
                         │
                         └─► TTS (Piper)  /  GUI render
```

---

## B. The 15 agents

```
 AGENT BUS  (cognition/agent_bus.py :: AgentBus.dispatch → DispatchResult)
 selects a set → runs on a dependency DAG → aggregates grounding_confidence
 ┌────────────────────┬──────────────────────────────────────────────────────┐
 │ BusMemoryAgent      │ recall: SQLite/FTS5/FAISS → context                   │
 │ KnowledgeGraphAgent │ entities/relations from the KG                        │
 │ SystemAgent         │ runs executor actions (WEB_SEARCH, RUNTIME_AUDIT, …)  │
 │ OrchestratorAgent   │ plan / coordinate grounded synthesis                  │
 │ FileCodeAgent       │ searches the codebase for code-grounded answers       │
 │ IntrospectionBus…   │ live runtime + gathers identity/awareness audits as    │
 │                     │ evidence (persona summarises — never a data dump)      │
 │ CapabilityAgent     │ capability manifest (what ELI can do)                 │
 │ ReflectionAgent     │ session reflection / insights                         │
 │ ProactiveAgent      │ proactive patterns / signals                          │
 │ HabitAgent          │ learned habits                                        │
 │ SelfImprovementAgent│ self-improvement state                                │
 │ FrontierAgent       │ frontier reasoning hooks                              │
 │ PluginAgent         │ dispatch to registered plugins                        │
 │ VoiceAgent          │ STT/TTS engine state                                  │
 │ CriticAgent         │ second-tier verifier: checks retriever agents' output │
 ├────────────────────┴──────────────────────────────────────────────────────┤
 │ + CodeAgent        │ SEPARATE pipeline: plan→search→verify→repair          │
 │   eli/coding/agent  │ (CODE_SOLVE / GENERATE_SCRIPT) — not a bus agent      │
 └─────────────────────┴─────────────────────────────────────────────────────┘
```

---

## C. Module tree (LOC · key files · role)

```
eli/  (233,521 LOC, 608 files)  ·  api/server.py  (FastAPI web app + dashboard)
│
├── __main__.py ················ entry dispatch (GUI | --headless)
│
├── kernel/            19.6k ─── the core
│   ├── engine.py      8.5k      CognitiveEngine.process() — the spine (+ stages/ 9.2k)
│   ├── scheduler.py             timed jobs
│   ├── pipeline.py · state.py · world_model.py · self_upgrade.py
│
├── execution/         31.3k ─── route → act
│   ├── executor_enhanced.py    7.6k   224 supported / 242 manifest
│   ├── router_enhanced.py      7.2k   priority pipeline
│   ├── effectors/ (one module per action family + registry.py: 199 registered actions)
│   ├── routing/ (command_parser.py · intents/)
│   ├── execution_planner.py · route_contracts.py · shell_gate.py
│   ├── operator_actions.py · operator_policy.py
│   ├── media_runtime.py
│   └── portable_intent_contract.py
│
├── cognition/         27.3k ─── think
│   ├── agent_bus.py    3.7k   15 agents + dispatch
│   ├── orchestrator.py        12-stage deep retrieval
│   ├── gguf_inference.py 3.0k · inference_broker.py   model-agnostic inference
│   ├── reasoning_modes.py · hyde.py · reranker.py · llm_intent.py
│   ├── persona.py/_updater/_values/_status/_hygiene  (+ persona.txt, persona.auto.txt)
│   ├── context_synthesiser.py · working_memory.py
│   ├── output_governor.py · self_claims.py · memory_diag.py
│   └── grounded_status.py · introspection_agent.py · tone_analyzer.py
│
├── runtime/           41.7k ─── grounding spine + daemons (104 files)
│   ├── deterministic_grounding_gate.py 2.6k · grounding_escalation.py
│   ├── evidence_ledger.py · evidence_arbitration.py · command_splitter.py
│   ├── persistence_gate.py · truth_report.py · control_contracts.py
│   ├── response_contracts.py · final_response_provider.py
│   ├── user_visible_response_surface.py · personal_memory_*.py · reflection.py
│   ├── background_tasks.py · self_improvement.py · code_monitor.py
│   ├── capability_sync.py · pending_proposal.py · runtime_policy.py
│   └── agenda.py (calendar + reminders)
│
├── memory/            11.6k ─── remember (13 files)
│   ├── memory.py       5.5k   Memory · SQLite + FTS5 · storage policy in policy.py
│   ├── claims.py · knowledge_graph.py · document_index.py · retrieval.py
│   └── vector_store.py        FAISS index
│
├── perception/        12.2k ─── sense (27 files)
│   ├── vision.py · analyze_image.py · analyze_csv.py · analyze_pdfs.py · ambient_vision.py
│   ├── audio_stt.py · local_whisper_stt.py · voice_worker_streaming.py
│   ├── tts_router.py · os_controller.py · screen_locator.py · gaze_engine.py
│
├── planning/           5.2k ─── proactivity (22 files)
│   ├── proactive_daemon.py · habits_scheduler.py · habits.py · jobqueue_cli.py
│
├── coding/             2.9k ─── CodeAgent (plan→search→verify→repair, 15 files)
│
├── learning/           4.2k ─── LoRA self-training (14 files)
│   ├── lora_trainer.py · lora_eval.py · lora_trainer_guard.py · dataset_builder.py · dataset_filters.py
│   ├── bootstrap_phi3_base.py · base_model_resolver.py · training_preflight.py
│
├── plugins/            6.6k ─── manager + bundled plugins (33 files)
│   └── calendar · document_reader · media · notes · pomodoro · weather ·
│       system_stats · tts · web · web_automation
│
├── world/              8.5k ─── EliWorld (world_event_bus, local_world_bridge, building,
│                                games, urges; spatial_feed + shown: the feed a body reads)
│
├── core/              13.3k ─── infra
│   ├── netguard.py            offline failsafe + allow_network()
│   ├── paths.py · portable_paths.py · legacy_paths.py · model_folders.py
│   ├── runtime_settings.py · config.py · grounding.py
│   ├── hardware_profile.py · startup_hardware_optimizer.py · load_plan.py
│   ├── model_download.py
│
├── gui/               28.2k ─── PySide6 desktop
│   ├── main_window/ (the window) · labs/ · app.py
│   └── panels/  (startup.py: model picker + FirstBootWizard, HardwareTuningDock)
│
├── tools/              6.1k ─── image_engine · news · document tools
├── integrations/ 4.2k · licensing/ 2.2k · bundles/ 0.8k · editions/ 0.1k
├── contracts/ 0.6k · cli/ 0.1k · system/ 1.8k · utils/ 2.1k · setup/ 1.9k · onboarding/ 0.8k
```

---

## D. On-disk data layout

```
artifacts/
├── db/
│   ├── user.sqlite3      memories · memories_archive · memory_meta · semantic ·
│   │                     conversation_turns · conversations · session_summaries ·
│   │                     memory_links · memory_lineage ·
│   │                     kg_entities · kg_relations · recall_log · runtime_events ·
│   │                     learning_replay · observations · habits/habit_events/habit_rules ·
│   │                     user_patterns · user_model · eli_stances · belief_revisions ·
│   │                     news_articles · news_reflections   (+ FTS5 indexes)
│   ├── agent.sqlite3     agent_dispatches · agent_metrics · improvements · failures ·
│   │                     corrections · capability_proposals
│   ├── system_index.sqlite3   OS index
│   └── coding_memory.sqlite3  coding_bug_fixes
├── vectors/index.faiss   semantic index   (embedder: nomic-embed-…Q4_K_M.gguf)
├── conversations/
├── runtime/ · runtime_snapshot.json        live model/runtime truth
├── world/{snapshots,ledger,journal}/
├── documents/ · scripts/ · analyze_image_*/
│
config/   settings.json (gitignored) · templates/settings.template.json ·
          settings.example.json · plugins_state.json
models/   <your>.gguf · embeddings/ · whisper/ · image/     tts_piper/  Piper voices
```

---

## E. The flow in one line

```
INPUT → ROUTER → ENGINE ─┬─ FAST-PATH ──────────────────────────► OUTPUT (verbatim)
                         └─ ORCHESTRATOR (all CHAT modes, gradient)
                                ├─ retrieve_for_turn() ──► MEMORY
                                ├─ dispatch_specialists() ──► 15-agent BUS
                                └─ broker.infer() → finalize_turn() (S12)
   GATES: netguard · persistence · grounding · escalation · governor
   INFERENCE (broker → gguf, model-agnostic) → OUTPUT → TTS/GUI
   BACKGROUND: proactive · self-improve · habits · learning(LoRA) · world · scheduler
```

> Companion docs: `architecture.md` (prose, every subsystem) ·
> `diagrams.md` (pipeline / memory / gating close-ups) ·
> `tools/eval/README.md` (the measurement layer).
```

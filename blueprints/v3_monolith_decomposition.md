# ELI v3 — Monolith decomposition & edition mapping

How the five god-files break into cohesive modules, and how each function family maps
to the **shared core** vs a **specialised edition** — so versions/sectors compose
dynamically. Grounded in an AST + name-prefix analysis of the actual code (Jul 2026).

## Method (proven, not theoretical)

Strangler, one family at a time: move a cohesive group verbatim into a new module,
leave a re-export shim at the old path, run the tests, commit. Nothing observable
changes; every step is reversible.

Done so far:
- `labs_tab.py` (5.7k) → `eli.gui.labs` — 16 modules + shim. Verified 14/14 offscreen.
- `gui_eli_original.py` (11.6k) → `eli.gui.main_window` — 16 files; monolith gutted to 7 lines.
- `executor_enhanced.py` (14.6k) → `eli.execution.effectors` — **34 action modules + registry**,
  **169 actions** registry-owned (of 185 `SUPPORTED_ACTIONS`). Executor **14,592 → 6,442 LOC
  (−56%)** and shrinking. One family per commit, tests between.
- `engine.py` (13.5k) → `eli.kernel.stages` — **six stage mixins** composed onto
  `CognitiveEngine` (retrieval, prompt_assembly, reasoning_modes, generation,
  memory_commit, lifecycle) + shared leaf modules. Engine **13,507 → 6,977 LOC
  (−48%)**; `process()` + `__init__` stay on the class. One stage per commit.
- Router intent families → `eli.execution.routing` (13 modules); router 7,267 → 5,315.

## The two axes (why grouping ≠ just tidiness)

- **Technical family** → which file/folder a function lives in (cohesion).
- **Edition affinity** → who uses it. Almost everything is **Core** (shared by all
  editions). A thin slice leans to a sector and can later lift into an edition pack
  under `eli/editions/<sector>/`. Editions never fork the core; they overlay it.

Affinity legend: **Core** (always loaded) · **Core+** (core but per-edition
tunable: personas, reasoning modes, prompts) · **Coding / Research / Content /
Clinical / Legal** (candidate to lift into that edition pack).

---

## 1. `executor_enhanced.py` (14.6k → 7.9k, IN PROGRESS) → `eli/execution/effectors/`

The dispatch switch (`execute`/`_execute_impl`) stays as the shrinking façade in
`executor_enhanced.py` itself; the registry is consulted before the legacy
if-chain and its per-action branches delegate to the effector modules. **As
built, the split is finer than this original plan** — 34 action modules, not the
~9 below (e.g. `files_notes` became `document_analysis`/`document_creation`/…,
`codegen` became `coding`, plus `window_control`, `gaze`, `input_control`,
`voice_input`, `plugins`, `timers`, `jobs`, `web_info`, and more). The
authoritative as-built effector→action map lives in `executor_decomposition.md`
§5 and `REFACTOR_LOG_V3.md` §3. The original target clusters (still accurate for
the families already moved):

| Target module | Function families (prefix clusters) | ~LOC | Affinity |
|---|---|---|---|
| `media.py` | `_spotify_*`(8) `_mpv_*`(4) `_yt_*`(2) `_playerctl_*`(3) `_duck_*`(3) `play_*`(2) `_resolve_media_target` | ~640 | Core |
| `apps_windows.py` | `_eli_tile_*`(7) `_open_app_*` `open_browser` `_normalize_app` `open_*_settings` | ~340 | Core |
| `files_notes.py` | `_eli_safe_*`(read/write/screenshot) `_open_file` `_append_note_builtin` `_save_*`(2) `_scan_*`(3) `_calendar_event_builtin` | ~450 | Core |
| `tts_speech.py` | `_speak*`(3) `_tts_*`(2) | ~200 | Core |
| `memory_ops.py` | `memory_*`(6) `_format_memory_*`(3) `search_memory*`(2) `_convlog_*`(2) `_state_*`(2) `_live_memory_audit` | ~450 | Core |
| `system_intel.py` | `_runtime_*`(4) `_format_runtime_*`(3) `_format_cognition_*`(2) `_explain_*`(3) `_resolve_runtime_paths*` `_gpu_status_report` `_*_audit_report` | ~800 | Core |
| `inference_backends.py` | `_ollama_*`(5) | ~75 | Core |
| `self_maintenance.py` | `self_test` `_self_improvement_log_report` `proactive_*`(4) `persona_lock_*`(3) `_verify_persona_lock` | ~250 | Core+ |
| `codegen.py` | `_maybe_background_codegen` `_maybe_background_file_analysis` `_run_argv` | ~140 | **Coding** |
| `dispatch.py` | `execute` `_execute_impl` `_action_pre/post_dispatch` `_normalize_result` `Executor` | ~6k | Core |

## 2. `router_enhanced.py` (7.3k, 93 fns) → `eli/execution/routing/`

`route` (the 3.3k-LOC matcher chain) stays in `router.py`; the domain matchers split
into `intents/`. Precedence is load-bearing — preserve current order.

| Target module | Families | ~LOC | Affinity |
|---|---|---|---|
| `router.py` | `route` `_route_*`(4) | ~3.3k | Core |
| `intents/contracts.py` | `_eli_phase38_*`(30) | ~870 | Core |
| `intents/memory.py` | `_eli_memory_*`(2) `_eli_pm_*`(4) `_eli_profile_*`(2) `_eli_is_*`(4) | ~180 | Core |
| `intents/voice.py` | `_eli_voice_*`(5) | ~114 | Core+ |
| `intents/media.py` | `_eli_media_contract_post` | ~176 | Core |
| `intents/self_ops.py` | `_eli_self_*`(2) `_eli_lrf_*`(3) | ~220 | Core+ |
| `intents/web_info.py` | `_eli_web_lookup_prepass` `_eli_weather_prepass` `_sanitise_news_topic` `_eli_schedule_prepass` | ~200 | **Research** (web/news) |
| `intents/documents.py` | `_extract_pdf_*`(3) `_eli_phase11_enrich_pdf_route` | ~120 | **Research/Content** |
| `_helpers.py` | `_extract_*` `_is_likely_*`(2) `_normalize_*`(2) `_mk` | ~120 | Core |

## 3. `engine.py` (13.5k, CognitiveEngine + helpers) → `eli/kernel/stages/` ✅

Decomposed with the **mixin pattern**: `class CognitiveEngine(LifecycleMixin,
GenerationMixin, PromptAssemblyMixin, ReasoningModesMixin, RetrievalMixin,
MemoryCommitMixin)`. Every method still reads engine state through `self`.
`process()` (~3k LOC, the orchestrator) and `__init__` (the constructor — **not**
lifecycle) stay on the class. Engine **13,507 → 6,977 LOC**.

**Stage mixins** (measured, not estimated):

| Module | Mixin | Methods | LOC | Affinity |
|---|---|---:|---:|---|
| `generation.py` | `GenerationMixin` — model call/stream, govern+normalise, rapport/semantic guards, finalise | 10 | 1,954 | Core |
| `prompt_assembly.py` | `PromptAssemblyMixin` — enhanced-system, evidence, grounded/precise context, persona hand-off | 11 | 1,531 | Core+ |
| `reasoning_modes.py` | `ReasoningModesMixin` — CoT/ToT/self-consistency/constitutional + synthesis | 7 | 872 | Core+ |
| `retrieval.py` | `RetrievalMixin` — relevant-memory recall, recent-topic summary, runtime snapshot | 3 | 605 | Core |
| `lifecycle.py` | `LifecycleMixin` — `_init_gguf`/`shutdown`/background daemons/session digest | 10 | 448 | Core |
| `memory_commit.py` | `MemoryCommitMixin` — decide/store turns, learn from result | 4 | 319 | Core |

**Shared leaf modules** (engine + mixins import them; they never import back into
engine → no cycle): `_common.py` (263, primitives), `guards.py` (289, failed-executor +
identity guards), `persona_text.py` (271, persona/identity text), `output_text.py`
(206, output normalisation), `model_runtime.py` (23, the model-access seam),
`fast_actions.py` (158, Phase-45 table).

## 4. `gui_eli_original.py` (11.6k, 13 classes) → `eli/gui/main_window/`

Already class-structured — split by class, low risk (only 2 importers):
`window.py` (`EliMainWindow`, still ~9k → further split by tab-builder into `tabs/`),
`adapters/` (`CentralMemoryAdapter`, `ExecutorBridge`, `_GUIEngineAdapter`),
`models/` (`LocalModelManager`, `OllamaModelManager`),
`widgets/` (`_FlowLayout`, `_QABoard`, `_Zoomable*`, `_MiniTelemetryGraph`, `_GlobalScrollZoom`).
Affinity: Core (chrome). The tab *set shown* becomes edition-driven.

## 5. `labs_tab.py` → `eli/gui/labs` ✅

Next sub-split target: `report_tab.py` (3.3k) → `report/{ui,sources,prompts,pdf}.py`;
report templates are **Research/Legal/Clinical/Content** edition-leaning.

---

## Dynamic composition — how sectors integrate

1. **Core always loads** — every Core / Core+ module above is present in every build.
2. **Editions overlay, never fork.** An edition pack (`eli/editions/<sector>/`)
   contributes: extra capability groups (the edition-leaning modules above, lifted in),
   a persona, templates, and `policy.py` hooks. The core calls those hooks; it stays
   domain-blind.
3. **Core+ is the tuning surface.** Personas, reasoning-mode selection, and prompt
   assembly read the active edition's manifest — so "the same core" behaves like a
   research analyst or a staff engineer without branching code.
4. **Licence entitlements pick the packs.** `editions.active_editions(entitled)`
   resolves which overlays compose; the rest of the system asks the registry, not a
   build flag. That is what makes integration of versions/sectors dynamic.

**The headline finding:** ~90% of the monolith mass is Core plumbing shared by every
edition. The sector divergence is a thin, well-identified slice (codegen, web/deep-
research, document templates, personas, domain policy). Decompose the core cleanly
first; lift the thin edition-leaning slices into packs second.

## Sequence

1. ✅ labs · ✅ Phase 45 · ✅ GUI main_window
2. ⏳ Executor effectors — 34 modules / 169 actions done; remaining façade families
   (CHAT + orchestration verbs, entangled OPEN cluster) legitimately stay in the façade.
3. ✅ Router intents lifted (contracts, memory, voice, media, self_ops, web_info, …) — 13 modules.
4. ✅ Engine stages — all six mixins (generation, prompt_assembly, reasoning_modes,
   retrieval, memory_commit, lifecycle) + shared leaves; `process()`/`__init__` stay.
5. ✅ GUI main_window split.
6. Lift edition-leaning slices (codegen→coding, web_info/documents→research) into
   `eli/editions/*` and wire the registry to licence entitlements.

Rules in force: dead code dropped as each family moves; comments concise; a commit per
verified slice.

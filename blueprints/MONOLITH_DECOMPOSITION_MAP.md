# ELI v3 — Monolith Decomposition Map

> **Historical record.** Line counts, test counts and file names below are as of when this was written. For current figures see `project_overview.md` and `architecture.md`.

*Exactly how the five god-files were broken down, where every function family now
resides, and why the result is more customisable and easier to maintain. All
counts are `wc -l` / file-count measured against the pre-refactor baseline (git
merge-base `dc5f04b`, the v2-lineage code) and the current `v3-modularisation`
tree. The refactor is **behaviour-preserving**: each move is verified
route-through-registry (or import shim) + a test sweep before commit.*

---

## 0. The five monoliths at a glance

| Monolith | Baseline | Current | Δ | Now lives in |
|---|---:|---:|---:|---|
| `gui_eli_original.py` (main window) | 11,573 | 7 | **−11,566 (gutted)** | `gui/main_window/` (16 files) |
| `executor_enhanced.py` | 14,592 | 6,442 | **−8,150 (−56%)** | `execution/effectors/` (39 files) |
| `engine.py` | 13,507 | 6,977 | **−6,530 (−48%)** | `kernel/stages/` (13 files) |
| `labs_tab.py` | 5,694 | 30 | **−5,664 (gutted)** | `gui/labs/` (17 files) |
| `router_enhanced.py` | 7,267 | 5,315 | **−1,952 (−27%)** | `execution/routing/` (13 files) |
| **Total** | **52,633** | **18,771** | **−33,862 (−64%)** | |

Two monoliths are **fully decomposed** (gutted to a shim); the **engine** is
decomposed into six stage mixins on `CognitiveEngine` (`process()`/`__init__`
stay); the executor is **56 % strangled and continuing**; the **router** lifted
its intent families to `execution/routing/`. Nothing observable changed — the
same actions, the same UI, the same outputs.

---

## 1. `executor_enhanced.py` → `eli/execution/effectors/`

**The problem.** One 14,592-line file, one class, ~185 functions, and a single
dispatch function (`_execute_impl`) that was a ~5,700-line `if action == "X": …`
chain over 145+ actions. Any one action was impossible to test in isolation, and
a failure inside a branch vanished into a silent `except`.

**The mechanism — a dispatch registry.** `effectors/registry.py` holds
`register(action, fn)` / `get(action)`. The executor's `execute()` consults the
registry **before** the legacy if-chain. Each effector module is a set of plain
`handler(a, args) -> dict` functions that self-register on import. Adding a
capability is now a new module + a `register()` call — never a god-file edit.

**Where the functions now reside — 34 action modules (169 actions):**

| Effector module | LOC | Actions owned |
|---|---:|---|
| `introspection.py` | 175 | COGNITION_STATUS, DIAGNOSE_WRAPPERS, ELI_IDENTITY_AUDIT, EXPLAIN_ALL_REASONING_MODES, EXPLAIN_COGNITION_RUNTIME, EXPLAIN_MEMORY_RUNTIME, GPU_STATUS, GUI_RUNTIME_AUDIT, IMAGE_STATUS, IMPORT_AUDIT, MEMORY_STATUS, ORCHESTRATION_STATUS, RESOLVE_RUNTIME_PATHS, RUNTIME_AUDIT, RUNTIME_STATUS (15) |
| `window_control.py` | 210 | CLOSE_APP, FOCUS_APP, HIDE_APP, MAXIMISE_WINDOW, MINIMISE_ALL, MINIMISE_APP, MINIMISE_WINDOW, MINIMIZE_APP, MINIMIZE_WINDOW, NEXT_WINDOW, OPEN_IN_IDE, PREVIOUS_WINDOW, RESTORE_WINDOWS, SWITCH_WORKSPACE, TILE_WINDOWS (15) |
| `coding.py` | 1179 | CANCEL_CODE_FIX, CODE_CHANGES, CODE_SOLVE, CONFIRM_CODE_FIX, EXAMINE_CODE, FILE_AUDIT, FIX_FILE, GENERATE_PROJECT, GENERATE_SCRIPT, GENERATE_TESTS, LORA_TRAIN, RUN_TESTS, TEST_REVIEW (13) |
| `media.py` | 839 | MEDIA_CONTROL, NEXT_MEDIA, NOW_PLAYING, PAUSE_MEDIA, PLAY_MEDIA, PREVIOUS_MEDIA, REPEAT_MEDIA, SHUFFLE_MEDIA, SKIP_YOUTUBE_AD, STOP_MEDIA, VOLUME (11) |
| `status_reports.py` | 297 | AWARENESS_STATUS, CPU_USAGE, FRONTIER_STATUS, GET_STATUS, HABIT_STATUS, HARDWARE_PROFILE, LORA_STATUS, MEMORY_STATS, RAM_USAGE, SYSTEM_STATS (10) |
| `memory_ops.py` | 576 | CLEAR_CHAT_HISTORY, MEMORY_RECALL, MEMORY_STORE, MESSAGE_TIME_QUERY, REFRESH_USER_INFO, SET_USER_NAME, USER_IDENTITY_SUMMARY, USER_INFO_REPORT (8) |
| `plugins.py` | 206 | PLUGIN_DISABLE, PLUGIN_ENABLE, PLUGIN_INSTALL, PLUGIN_LIST, PLUGIN_SEARCH, PLUGIN_STATUS, PLUGIN_UNINSTALL (7) |
| `self_improve.py` | 398 | SELF_ANALYZE, SELF_IMPROVE, SELF_IMPROVEMENT_LOG, SELF_PATCH, SELF_REPORT, SELF_TEST, SELF_UPGRADE (7) |
| `datetime_calendar.py` | 48 | ADD_EVENT, DATE, GET_DATE, GET_TIME, LIST_EVENTS, TIME (6) |
| `system_open.py` | 290 | OPEN_AUDIO_SETTINGS, OPEN_BROWSER, OPEN_FILE_SYSTEM, OPEN_MEDIA_HUB, OPEN_POWER_SETTINGS, OPEN_SYSTEM_SETTINGS (6) |
| `file_ops.py` | 332 | CONVERT_DOCUMENT, CREATE_FILE, CREATE_FOLDER, LIST_DIR, SUMMARIZE_FILE (5) |
| `gaze.py` | 126 | GAZE_CALIBRATE, GAZE_CLICK, GAZE_DISABLE, GAZE_ENABLE, GAZE_STATUS (5) |
| `voice_input.py` | 135 | DICTATE, LISTEN_FOR_COMMAND, STT_DIAGNOSTICS, TRANSCRIBE, VOICE_DIAGNOSTICS (5) |
| `input_control.py` | 109 | GET_CLIPBOARD, KEYBOARD, MOUSE_CONTROL, SET_CLIPBOARD (4) |
| `persona_lock.py` | 54 | PERSONA_LOCK_CLEAR, PERSONA_LOCK_SET, PERSONA_LOCK_STATUS, PERSONA_REFRESH (4) |
| `screen.py` | 135 | AMBIENT_VISION, SCREENSHOT, SCREEN_LOCATE, SCREEN_READ_ANALYZE (4) |
| `wake_word.py` | 111 | TRAIN_VOICE, WAKE_ENROLL, WAKE_SET, WAKE_TRAIN (4) |
| `web_info.py` | 279 | GET_WEATHER, MORNING_REPORT, NEWS_FETCH, WEB_SEARCH (4) |
| `apps_windows.py` | 493 | OPEN_COMMUNICATION_HUB, OPEN_NETWORK_BROWSER, OPEN_URL (3) |
| `content_analysis.py` | 201 | ANALYZE_CSV, ANALYZE_IMAGE, OCR_IMAGE (3) |
| `document_creation.py` | 320 | CREATE_DOCUMENT, DOC_GENERATE, GENERATE_DOCUMENT (3) |
| `misc.py` | 98 | CHECK_CHRONAL_ALIGNMENT, DATA_FABRICATOR, SHOW_DIFF (3) |
| `notes.py` | 68 | LIST_NOTES, NEW_NOTE, SEARCH_NOTES (3) |
| `pomodoro.py` | 64 | POMODORO_START, POMODORO_STATUS, POMODORO_STOP (3) |
| `self_maintenance.py` | 194 | PROACTIVE_START, PROACTIVE_STATUS, PROACTIVE_STOP (3) |
| `document_analysis.py` | 416 | ANALYZE_PDF, ANALYZE_PDF_FOLDER (2) |
| `files_notes.py` | 245 | READ_FILE, WRITE_NOTE (2) |
| `habits.py` | 84 | CONFIRM_HABIT, DECLINE_HABIT (2) |
| `jobs.py` | 63 | BACKGROUND_JOBS, CHECK_JOB (2) |
| `shell.py` | 94 | RUN_CMD, SHELL_EXEC (2) |
| `timers.py` | 109 | SET_ALARM, SET_TIMER (2) |
| `ai_mode.py` | 40 | SET_AI_MODE (1) |
| `smart_home.py` | 95 | SMART_HOME (1) |
| `tts_speech.py` | 226 | SPEAK (1) |

**Five further files carry no actions** — the dispatch plumbing and the shared
primitives the handlers reach via lazy import:

| Support module | LOC | Role |
|---|---:|---|
| `system_intel.py` | 931 | runtime/GPU/cognition report + format helpers |
| `_base.py` | 354 | `_run`, `_run_ok`, `_save_artifact`, `_artifacts_dir`, fail-closed security gate |
| `inference_backends.py` | 128 | optional Ollama backend helpers |
| `registry.py` | 43 | `register` / `get` / `registered_actions` |
| `__init__.py` | 25 | package init |

**Still in the shrinking façade** (`executor_enhanced.py`, 6,442 LOC): `CHAT`,
the orchestration verbs (`MULTI_COMMAND`, `SEQUENCE`, `SCHEDULE_TASK`,
`GET_PROPOSALS`), and a handful of entangled families (memory ops, screen/vision,
file ops, self-upgrade) being extracted next. Of the executor's **185**
`SUPPORTED_ACTIONS`, **169** are already registry-owned.

---

## 2. `gui_eli_original.py` → `eli/gui/main_window/`

**The problem.** An 11,573-line file, 13 classes, one `EliMainWindow` doing
everything: chrome, chat, settings, model management, tabs, image, screen
capture. Only two importers, but every UI change meant editing the same giant
class.

**The mechanism — a mixin-composed window.** `window.py` defines the
`EliMainWindow` shell that assembles focused **mixins**, each owning one slice of
UI behaviour. Decomposition-orphaned window-globals (`model_manager`,
`memory_system`) are now `self.`-bound in `__init__`. The old path keeps a
7-line re-export shim so external importers never break.

**Where the functions now reside:**

| File | LOC | Holds |
|---|---:|---|
| `_mixins/settings_model.py` | 2,398 | Settings tab + local/Ollama model management |
| `_mixins/chrome_io.py` | 1,492 | window chrome, layout, input/output plumbing |
| `_mixins/tabs.py` | 1,247 | tab construction / tab-set assembly |
| `window.py` | 1,248 | `EliMainWindow` shell — composes the mixins |
| `_mixins/conversation.py` | 1,050 | chat / conversation panel |
| `_mixins/features.py` | 889 | feature toggles + feature panels |
| `models.py` | 627 | `LocalModelManager`, `OllamaModelManager` |
| `adapters.py` | 616 | `CentralMemoryAdapter`, `ExecutorBridge`, `_GUIEngineAdapter` |
| `_common.py` | 554 | shared GUI helpers |
| `_mixins/image.py` | 495 | image tab (analysis + diffusion swap) |
| `widgets.py` | 493 | custom widgets (`_FlowLayout`, `_QABoard`, zoomables, telemetry graph) |
| `_mixins/screen_capture.py` | 215 | screen capture panel |
| `_mixins/quick_actions.py` | 178 | quick-action bar |
| `_mixins/self_improve.py` | 129 | self-improvement panel |

**Which tab set is shown becomes edition-driven** — the shell composes only the
mixins the active edition entitles.

---

## 3. `labs_tab.py` → `eli/gui/labs/`

**The problem.** A 5,694-line file holding every Labs sub-tab in one place.

**The mechanism — one file per sub-tab**, assembled by `container.py`; the old
path keeps a 30-line shim.

**Where the functions now reside:**

| File | LOC | Holds |
|---|---:|---|
| `report_tab.py` | 3,327 | Report Builder (next sub-split target → `report/{ui,sources,prompts,pdf}.py`) |
| `workspaces_tab.py` | 440 | Projects / Workspaces |
| `sim_ide_tab.py` | 355 | native code editor / sim IDE |
| `memory_conversations_tab.py` | 282 | memory + conversations browser |
| `_common.py` | 236 | shared Labs helpers |
| `test_review_tab.py` | 224 | test review |
| `calculator_tab.py` | 171 | calculator |
| `file_chat_tab.py` | 134 | file chat |
| `container.py` | 129 | tab container / assembly |
| `jupyter_tab.py` / `notebook_tab.py` | 129 / 121 | Jupyter + notebook |
| `orchestration_tab.py` | 112 | DAG / orchestration view |
| `highlighter.py` / `physics_tab.py` / `code_runner.py` / `bg_worker.py` | 70 / 67 / 35 / 23 | syntax highlighter, physics tab, code runner, background worker |

---

## 4. `router_enhanced.py` → `eli/execution/routing/`

`route` (the ~3.3k-LOC matcher chain) stays in `router_enhanced.py` — its
**precedence is load-bearing** and must not be reordered. The order-independent
helpers and whole **intent families** have been lifted into `eli/execution/routing/`
(13 modules, 2,325 LOC): `_helpers`, and per-family matchers (contracts, memory,
personal_memory, memory_runtime, voice, profile, self_ops, web_info,
content_guards, weather, schedule, lrf). Router **7,267 → 5,315 LOC**; the lifted
families re-import so `route()`'s `globals()` lookups keep resolving, and
`CapabilitySync.discover()` scans `routing/*.py` so lifted intents stay routable.

## 5. `engine.py` → `eli/kernel/stages/`

`engine.py` is decomposed with the **mixin pattern**: `class CognitiveEngine(
LifecycleMixin, GenerationMixin, PromptAssemblyMixin, ReasoningModesMixin,
RetrievalMixin, MemoryCommitMixin)`. Every method still reads engine state through
`self`. `process()` (~3k LOC, the orchestrator) and `__init__` (the constructor —
**not** lifecycle) stay on the class. Engine **13,507 → 6,977 LOC**.

Six **stage mixins** (methods · LOC): `generation.py` `GenerationMixin` (10 · 1,954),
`prompt_assembly.py` `PromptAssemblyMixin` (11 · 1,531), `reasoning_modes.py`
`ReasoningModesMixin` (7 · 872), `retrieval.py` `RetrievalMixin` (3 · 605),
`lifecycle.py` `LifecycleMixin` (10 · 448), `memory_commit.py` `MemoryCommitMixin`
(4 · 319).

Six **shared leaf modules** the engine and mixins both import (they never import
back into engine, so no cycle): `_common.py` (263), `guards.py` (289),
`persona_text.py` (271), `output_text.py` (206), `model_runtime.py` (23, the model-
access seam), `fast_actions.py` (158, the Phase-45 deterministic fast-path).

The seam rule: a helper used **only** by one stage moves into that stage's file; a
helper **shared** with the engine or another stage moves to a leaf module — never a
back-import into `engine.py`.

---

## 6. Conservation of code — where every line went

Code was **relocated, not deleted** — a function-inventory diff confirms **0 of the
310 baseline executor functions are missing** (162 stayed in the executor, 272
moved into effectors, 13 into routing; the union *grew* to 433), and every
`CognitiveEngine` method is present across the six stage mixins + the class. The
five monoliths shed **33,862 LOC**; that behaviour landed in new packages:

```
  MONOLITHS LOSE                          NEW PACKAGES GAIN
  gui_eli_original.py    -11,566   -->    gui/main_window/       11,640   (16 files)
  executor_enhanced.py    -8,150   -->    execution/effectors/    9,751   (39 files)
  engine.py               -6,530   -->    kernel/stages/          7,023   (13 files)
  labs_tab.py             -5,664   -->    gui/labs/               5,891   (17 files)
  router_enhanced.py      -1,952   -->    execution/routing/      2,325   (13 files)
  ------------------------------          ------------------------------
  removed from monoliths -33,862          relocated               36,630
```

**-33,862 removed -> +36,630 relocated** — about **2,800 lines *more***, because 98
separate files need structure a single god-file did not: per-module docstrings,
`__init__` wiring, the registry, the shared `_base`/leaf primitives, per-mixin
import headers, and the re-export
shims. Plus two **genuinely new** packages (no monolith source) — the
productization layer:

| New package | Files | LOC |
|---|---:|---:|
| `eli/editions/` | 29 | 343 |
| `eli/licensing/` | 9 | 795 |

**New-package grand total: 37,768 LOC.**

### The whole tree — nothing else moved

| Layer | Status | LOC |
|---|---|---:|
| The 5 monoliths | shrunk (-64%) | 18,771 |
| Relocation packages (main_window, labs, effectors, routing, stages) | code moved here | 36,630 |
| New productization (editions, licensing) | brand new | 1,138 |
| Everything else (`runtime`, `cognition`, `memory`, `perception`, `core`, `tools`, …) | **unchanged** | ~98,500 |
| **Whole `eli/` package** | **519 files** | **155,039** |

The codebase *grew* ~12k LOC (v2 ≈ 370 files / 143k -> v3 519 / 155k) because
decomposition trades concentration for structure — and that structure is what
buys the testability and drop-in extensibility. **No functionality was removed;
it was given an address.**

## 7. Why this is more customisable

- **Drop-in capabilities.** A new action = a new effector module + one
  `register()` line. No merge-conflict-prone edit to a 14k-line dispatcher; the
  registry makes the action set *enumerable* (`registered_actions()` can be
  diffed against the capability manifest in a test).
- **Editions overlay, never fork.** Because behaviour now lives in cohesive,
  independently-importable modules, an edition pack (`eli/editions/<sector>/`)
  can lift a thin slice (e.g. `coding`, `web_info`, report templates) into a
  sector build without touching the shared core. A signed licence tier
  (`eli/licensing/`) picks which packs compose at runtime.
- **UI by composition.** The window is assembled from mixins and the Labs view
  from per-tab files, so the *shown* feature/tab set is edition-driven — the
  same core presents as a research analyst or a staff engineer without branching
  code.
- **Swap one part in isolation.** A single effector, tab, or stage can be
  replaced or A/B-tested behind the same registry key / shim without disturbing
  its neighbours.

## 8. Why this is easier to maintain

- **Single responsibility.** Each file is one family — `timers.py` is alarms,
  `media.py` is playback, `report_tab.py` is the report builder. You read one
  ~100–800-line file, not a 14k-line switch.
- **Independently testable.** A handler is `handler(a, args) -> dict`, importable
  and callable with a dict — the isolation that was impossible inside the
  god-function. The full suite is **7,943 tests**; each extraction runs the
  relevant slice + contract/surface tests before commit.
- **Failures stop hiding.** As families move, silent `except: pass` swallows
  convert to `log.debug(..., exc_info=True)` — the swallow ceiling drops, not
  rises.
- **Behaviour is pinned.** Every move is verified against the registry
  (route-through), `pyflakes` (no dropped imports / undefined names), and the
  test sweep. A dropped module-level import, a bare executor callable, or a stray
  function-body constant is caught at extraction time — not in the field.
- **Reversible, one commit per family.** Each extraction is an isolated,
  revertible commit; the legacy branch is deleted only once the registry owns the
  action, so rollback restores prior behaviour exactly.

**The one-liner:** *the same brain, re-shelved — every capability now has an
address, a test, and a price tag, instead of living in one unsearchable file.*

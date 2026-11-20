# ELI v3 Refactor Log — Before & After

> **Historical record.** Line counts, test counts and file names below are as of when this was written. For current figures see `project_overview.md` and `architecture.md`.
>
> **Gone since (noted 2026-11-20, at v3.0.167).** The `gui_eli_original.py` and `labs_tab.py` shims under `eli/gui/` were removed in v3.0.167; the window is `eli/gui/main_window/`, the Labs tabs `eli/gui/labs/`. `effectors/inference_backends.py` was removed in v3.0.166 (nothing imported it; the Ollama client is `eli/integrations/ollama/client.py`).

> **Update 2026-09-25.** The engine decomposition described below was silently undone on 2026-09-08
> (v3.0.99, "port v2.3.92 …"), which reinstated the v2 monolith as `engine.py` and left `kernel/stages/` unwired
> while later fixes landed in both places. It has been restored: the production code was moved verbatim into the
> stage modules and `CognitiveEngine` is again composed from the six mixins (`engine.py` is now about 8k lines).
> Two v3-only behaviours missing from production in between (28 read-and-describe actions exempt from the
> failed-executor guard, and the conditional system-prompt rules in `kernel/prompt_rules.py`) were restored.
> The pre-restoration tree is recoverable at the git tag `v3-kernel-stages-archive`.


*Complete, measured record of the v3 structural refactor (branch
`v3-modularisation`) versus the pre-refactor baseline (git merge-base
`dc5f04b`, the v2-lineage code). Every number is `wc -l` / file-count measured
against that baseline, not estimated. Behaviour is preserved: every extraction
is verified route-through-registry + a full test sweep before commit.*

---

## 1. The god-files — before → after

| File | Before (LOC) | After (LOC) | Δ | Where it went |
|---|---:|---:|---:|---|
| `executor_enhanced.py` | 14,592 | 6,442 | **−8,150 (−56%)** | `execution/effectors/` |
| `gui_eli_original.py` (main window) | 11,573 | 7 | **−11,566 (gutted)** | `gui/main_window/` |
| `engine.py` | 13,507 | 6,977 | **−6,530 (−48%)** | `kernel/stages/` |
| `labs_tab.py` | 5,694 | 30 | **−5,664 (gutted)** | `gui/labs/` |
| `router_enhanced.py` | 7,267 | 5,315 | **−1,952 (−27%)** | `execution/routing/` |
| **Total** | **52,633** | **18,771** | **−33,862 (−64%)** | |

The two GUI monoliths are **fully decomposed**. The **engine is decomposed** into
six stage mixins composed onto `CognitiveEngine` (see §6). The executor is **56%
strangled and continuing**; the router lifted its intent families to
`execution/routing/`. What remains in `engine.py` is `process()` (the ~3k-line
orchestrator that drives the stages) and `__init__` — both deliberately kept on
the class.

## 2. New packages created (did not exist before)

| Package | Files | LOC | Purpose |
|---|---:|---:|---|
| `eli/gui/main_window/` | 16 | 11,640 | decomposed main window (`window.py` + `_mixins/` + adapters/models/widgets) |
| `eli/execution/effectors/` | 39 | 9,751 | the executor's action layer + dispatch registry + `_base` primitives |
| `eli/kernel/stages/` | 13 | 7,023 | decomposed engine — 6 stage mixins + shared leaf modules |
| `eli/gui/labs/` | 17 | 5,891 | decomposed Labs tab |
| `eli/execution/routing/` | 13 | 2,325 | lifted router intent families |
| `eli/licensing/` | 9 | 795 | commercial tier gate (trial/personal/pro/enterprise) |
| `eli/editions/` | 29 | 343 | shared-core + edition packs (base/coding/content/enterprise/healthcare/legal/research) |

## 3. The effector layer — dispatch registry

`execution/effectors/registry.py` is consulted **before** the legacy if-chain.
**169 actions** are registry-owned across **34 effector modules**; the total
executor action surface is **185** `SUPPORTED_ACTIONS` (the remainder — CHAT,
orchestration verbs like MULTI_COMMAND/SEQUENCE, and a few entangled handlers —
still live in the shrinking façade). `_base.py` holds the shared primitives
(`_run`, `_run_ok`, `_save_artifact`, `_artifacts_dir`, the fail-closed
security gate); `registry.py`, `_base.py`, and the primitive helpers
(`inference_backends`, `memory_ops`, `system_intel`, `tts_speech`) make up the
non-action files.

| Effector module | Actions owned |
|---|---|
| `introspection` (15) | COGNITION_STATUS, DIAGNOSE_WRAPPERS, ELI_IDENTITY_AUDIT, EXPLAIN_ALL_REASONING_MODES, EXPLAIN_COGNITION_RUNTIME, EXPLAIN_MEMORY_RUNTIME, GPU_STATUS, GUI_RUNTIME_AUDIT, IMAGE_STATUS, IMPORT_AUDIT, MEMORY_STATUS, ORCHESTRATION_STATUS, RESOLVE_RUNTIME_PATHS, RUNTIME_AUDIT, RUNTIME_STATUS |
| `window_control` (15) | CLOSE_APP, FOCUS_APP, HIDE_APP, MAXIMISE_WINDOW, MINIMISE_ALL, MINIMISE_APP, MINIMISE_WINDOW, MINIMIZE_APP, MINIMIZE_WINDOW, NEXT_WINDOW, OPEN_IN_IDE, PREVIOUS_WINDOW, RESTORE_WINDOWS, SWITCH_WORKSPACE, TILE_WINDOWS |
| `coding` (13) | CANCEL_CODE_FIX, CODE_CHANGES, CODE_SOLVE, CONFIRM_CODE_FIX, EXAMINE_CODE, FILE_AUDIT, FIX_FILE, GENERATE_PROJECT, GENERATE_SCRIPT, GENERATE_TESTS, LORA_TRAIN, RUN_TESTS, TEST_REVIEW |
| `media` (11) | MEDIA_CONTROL, NEXT_MEDIA, NOW_PLAYING, PAUSE_MEDIA, PLAY_MEDIA, PREVIOUS_MEDIA, REPEAT_MEDIA, SHUFFLE_MEDIA, SKIP_YOUTUBE_AD, STOP_MEDIA, VOLUME |
| `status_reports` (10) | AWARENESS_STATUS, CPU_USAGE, FRONTIER_STATUS, GET_STATUS, HABIT_STATUS, HARDWARE_PROFILE, LORA_STATUS, MEMORY_STATS, RAM_USAGE, SYSTEM_STATS |
| `memory_ops` (8) | CLEAR_CHAT_HISTORY, MEMORY_RECALL, MEMORY_STORE, MESSAGE_TIME_QUERY, REFRESH_USER_INFO, SET_USER_NAME, USER_IDENTITY_SUMMARY, USER_INFO_REPORT |
| `plugins` (7) | PLUGIN_DISABLE, PLUGIN_ENABLE, PLUGIN_INSTALL, PLUGIN_LIST, PLUGIN_SEARCH, PLUGIN_STATUS, PLUGIN_UNINSTALL |
| `self_improve` (7) | SELF_ANALYZE, SELF_IMPROVE, SELF_IMPROVEMENT_LOG, SELF_PATCH, SELF_REPORT, SELF_TEST, SELF_UPGRADE |
| `datetime_calendar` (6) | ADD_EVENT, DATE, GET_DATE, GET_TIME, LIST_EVENTS, TIME |
| `system_open` (6) | OPEN_AUDIO_SETTINGS, OPEN_BROWSER, OPEN_FILE_SYSTEM, OPEN_MEDIA_HUB, OPEN_POWER_SETTINGS, OPEN_SYSTEM_SETTINGS |
| `file_ops` (5) | CONVERT_DOCUMENT, CREATE_FILE, CREATE_FOLDER, LIST_DIR, SUMMARIZE_FILE |
| `gaze` (5) | GAZE_CALIBRATE, GAZE_CLICK, GAZE_DISABLE, GAZE_ENABLE, GAZE_STATUS |
| `voice_input` (5) | DICTATE, LISTEN_FOR_COMMAND, STT_DIAGNOSTICS, TRANSCRIBE, VOICE_DIAGNOSTICS |
| `input_control` (4) | GET_CLIPBOARD, KEYBOARD, MOUSE_CONTROL, SET_CLIPBOARD |
| `persona_lock` (4) | PERSONA_LOCK_CLEAR, PERSONA_LOCK_SET, PERSONA_LOCK_STATUS, PERSONA_REFRESH |
| `screen` (4) | AMBIENT_VISION, SCREENSHOT, SCREEN_LOCATE, SCREEN_READ_ANALYZE |
| `wake_word` (4) | TRAIN_VOICE, WAKE_ENROLL, WAKE_SET, WAKE_TRAIN |
| `web_info` (4) | GET_WEATHER, MORNING_REPORT, NEWS_FETCH, WEB_SEARCH |
| `apps_windows` (3) | OPEN_COMMUNICATION_HUB, OPEN_NETWORK_BROWSER, OPEN_URL |
| `content_analysis` (3) | ANALYZE_CSV, ANALYZE_IMAGE, OCR_IMAGE |
| `document_creation` (3) | CREATE_DOCUMENT, DOC_GENERATE, GENERATE_DOCUMENT |
| `misc` (3) | CHECK_CHRONAL_ALIGNMENT, DATA_FABRICATOR, SHOW_DIFF |
| `notes` (3) | LIST_NOTES, NEW_NOTE, SEARCH_NOTES |
| `pomodoro` (3) | POMODORO_START, POMODORO_STATUS, POMODORO_STOP |
| `self_maintenance` (3) | PROACTIVE_START, PROACTIVE_STATUS, PROACTIVE_STOP |
| `document_analysis` (2) | ANALYZE_PDF, ANALYZE_PDF_FOLDER |
| `files_notes` (2) | READ_FILE, WRITE_NOTE |
| `habits` (2) | CONFIRM_HABIT, DECLINE_HABIT |
| `jobs` (2) | BACKGROUND_JOBS, CHECK_JOB |
| `shell` (2) | RUN_CMD, SHELL_EXEC |
| `timers` (2) | SET_ALARM, SET_TIMER |
| `ai_mode` (1) | SET_AI_MODE |
| `smart_home` (1) | SMART_HOME |
| `tts_speech` (1) | SPEAK |

## 4. Method (how behaviour was preserved)

1. Read the if-branch(es) for a cohesive family (single `if a == …` or a
   combined `if a in (…)` group).
2. Extract the body **verbatim** into an effector module (indentation-terminated
   so interstitial code is never absorbed); register the actions.
3. Shared helpers stay put — reached via a lazy import (executor namespace) or
   promoted to `_base` when general (e.g. `_run_ok`, `_save_artifact`).
4. Import the effector into the executor; delete the now-dead if-branches
   (registry-first dispatch makes them unreachable).
5. Verify: the action routes through the registry, stays in `SUPPORTED_ACTIONS`,
   the effector passes `pyflakes`, and the relevant + contract test suites stay
   green. Then commit — one family per commit.

Regressions caught and fixed during the run (the value of step 5): dropped
module-level imports the handler bodies relied on (`json`, `datetime`, `re`,
`os`, `threading`, `time`), bare executor callables needing a lazy shim
(`chat`, `notify`, `play_sound`, `resolve_user_repo_path`), and a function-body
local constant (`_LOC_PATS`) that lived outside its branch — now audited with an
AST scan + pyflakes per extraction.

## 5. Net effect — conservation of code

Code was **relocated, not deleted** — a function-inventory diff confirms **0 of the
310 baseline executor functions are missing** (162 stayed, 272 → effectors, 13 →
routing), and every `CognitiveEngine` method is present across the six stage
mixins + the class itself. The five monoliths shed 33,862 LOC; it landed in new
packages:

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

- **−33,862 LOC (−64%)** off the five monoliths → **+36,630 LOC** relocated into
  cohesive, single-responsibility packages (~2,800 more than removed — the
  structure 98 files need: docstrings, `__init__` wiring, registry, `_base`,
  per-mixin import headers).
- Plus **+1,138 LOC genuinely new** (no monolith source): `eli/editions/` (343) +
  `eli/licensing/` (795). New-package grand total **37,768 LOC**.
- Whole `eli/` package **519 files / 155,039 LOC** — grew ~12k over v2 (≈370 / 143k):
  structure has a cost that buys testability + extensibility.
- **169 actions** now drop-in modules on a registry instead of god-file surgery
  (of 185 total `SUPPORTED_ACTIONS`; 205 manifest capabilities).
- **Zero behavioural change** — every step verified against the full suite
  (7,943 tests collected). **No functionality was removed; it was given an address.**
- **New capability the refactor enables**: the editions + licensing layer
  (a productized, sellable core) that has no equivalent in v2.

## 6. The engine — `CognitiveEngine` composed from stage mixins

`engine.py` was decomposed with the **mixin pattern** (the same shape the main
window uses in `gui/main_window/_mixins/`): each pipeline stage becomes a mixin in
`eli/kernel/stages/`, and `CognitiveEngine` composes them:

```python
class CognitiveEngine(LifecycleMixin, GenerationMixin, PromptAssemblyMixin,
                      ReasoningModesMixin, RetrievalMixin, MemoryCommitMixin):
    def __init__(self, ...):   # stays — the constructor + the state stages read
    def process(self, ...):    # stays — the orchestrator that drives the stages
```

Every method still reads engine state through `self`; nothing changed
behaviourally. `process()` (~3,000 lines) and `__init__` are deliberately kept on
the class — `process()` is the orchestrator, `__init__` is the constructor, and
`__init__` is **not** lifecycle.

**Stage mixins** (one cohesive slice of the pipeline each):

| Module | Mixin | Methods | LOC | Owns |
|---|---|---:|---:|---|
| `retrieval.py` | `RetrievalMixin` | 3 | 605 | tiered memory fetch (relevant-memory recall, recent-topic summary, runtime snapshot) |
| `prompt_assembly.py` | `PromptAssemblyMixin` | 11 | 1,531 | enhanced-system prompt, executor evidence, grounded + precise context, persona hand-off |
| `reasoning_modes.py` | `ReasoningModesMixin` | 7 | 872 | CoT / ToT / self-consistency / constitutional + answer synthesis |
| `generation.py` | `GenerationMixin` | 10 | 1,954 | model call/stream, govern + normalise, rapport/semantic guards, finalise chat result |
| `memory_commit.py` | `MemoryCommitMixin` | 4 | 319 | decide what to store, store the turns, learn from the result |
| `lifecycle.py` | `LifecycleMixin` | 10 | 448 | `_init_gguf`, `shutdown`, background daemons, session digest |

**Shared leaf modules** (imported by the engine *and* the mixins; they never
import back into engine, so there is no cycle):

| Module | LOC | Holds |
|---|---:|---|
| `_common.py` | 263 | primitives: governance soft-import, path/phatic/test-mode helpers, bus-result helper |
| `guards.py` | 289 | failed-executor detection + honest surfacing, identity self-report guards, scaffold strip |
| `persona_text.py` | 271 | persona load + identity-context sanitise |
| `output_text.py` | 206 | assistant-output text normalisation |
| `model_runtime.py` | 23 | model-access seam — soft-imported inference module + broker, one binding both sides resolve to |
| `fast_actions.py` | 158 | Phase-45 direct fast-action table (module-level, not a mixin) |

The seam that made this safe: any helper used **only** by a stage moves into that
stage's file; any helper **shared** with the engine or another stage moves to a
leaf module both import — never a back-import into `engine.py`, which would create
a cycle. That is why `persona_text` / `output_text` / `model_runtime` exist.

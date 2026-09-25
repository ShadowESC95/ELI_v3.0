# Blueprint — Decomposing `_execute_impl` (the executor god-function)

> **Historical record.** Line counts, test counts and file names below are as of when this was written. For current figures see `project_overview.md` and `architecture.md`.

*Status: **IN PROGRESS** on `v3-modularisation`. Design dated 2026-06-26; build ongoing.*

**Progress (measured):** the handler registry (§3) is built and load-bearing —
`execution/effectors/registry.py`, consulted before the if-chain. **169 actions**
(of 185 total `SUPPORTED_ACTIONS`) across **34 effector modules** are
registry-owned — window control, introspection, coding, media, status reports,
plugins, system-open, gaze, self-improve, input control, voice input,
datetime/calendar, persona-lock, wake-word, web/info, apps/windows, content
analysis, pomodoro, self-maintenance, document analysis/creation, jobs, timers
(the full as-built map is in §5). `executor_enhanced.py` is down from
**14,592 → 6,442 LOC (−56%)**. Shared primitives (`_run`, `_run_ok`,
`_save_artifact`, `_artifacts_dir`, the security gate) live in
`effectors/_base.py`. Remaining in the façade: CHAT, the orchestration verbs
(MULTI_COMMAND, SEQUENCE, SCHEDULE_TASK, GET_PROPOSALS), and a handful of
entangled handlers (memory ops, screen/vision, file ops, self-upgrade) — being
extracted until the executor is a thin façade. Zero behavioural change verified
per family (route + pyflakes + relevant/contract test sweep).

*Framing: GAP ANALYSIS, not greenfield. Most of the surrounding machinery (middleware
chain, result contract, security gate, ~300 helper functions) already exists and is
sound. The single genuine defect was the **shape of the dispatch core**: one large
function. This document is the plan to change that shape without changing behaviour.*

---

## 1. The problem (measured)

- `eli/execution/executor_enhanced.py` is **14,370 lines**, one class, **312 functions**.
- The dispatch core, **`_execute_impl(action, args)` at `executor_enhanced.py:4742`, is a single 5,705-line function** — an `if a == "ACTION": …` chain over **145 distinct actions**.
- It is the largest single unit of behaviour in the entire 139 K-LOC codebase and concentrates **102 of the 950 silent `except: pass` swallows**.
- Consequences observed this cycle: the actions are not independently testable, a change to one branch risks the whole dispatcher, and failures inside a branch vanish silently (the same class of invisibility that hid the self-status truncation in `engine.py`).

**This is a maintainability/structure problem, not a correctness problem.** The actions work. The goal is to make them *individually* legible, testable, and safe to change — with **zero behavioural change** as the hard success criterion.

---

## 2. What already exists (do NOT rebuild)

The decomposition must preserve, untouched, the machinery wrapped *around* the dispatch:

| Piece | Where | Keep as-is |
|---|---|---|
| Public entry / alias | `execute`, `execute_action` (aliased to `execute`) | Yes — external callers (engine, agent_bus, `eli.tools.api`) call these. |
| **Canonical middleware table** | `_ELI_EXECUTOR_MIDDLEWARE_TABLE`, installed at import (`executor_enhanced.py:~772`) | Yes — ordered, named middleware chain (generated-script safety wrapper, `GUI_RUNTIME_AUDIT` visible-result contract, rebind chain). The dispatcher is what it wraps. |
| **Result contract** | dicts shaped `{ok, action, content, response, [error], [report]}` (541 `ok:`, 481 `content:`, 460 `action:`, 459 `response:`) | Yes — every handler must return this exact shape. |
| **Security gate** | `_get_security_manager()` → `eli.runtime.security.SecurityManager` (`executor_enhanced.py:66-77`, used at `:331`); `_BLOCKED_PATTERNS`; approval engine | Yes — gating stays in front of/inside the relevant handlers. |
| **Self-delegation** | branches call the dispatcher re-entrantly, e.g. `return _execute_impl("TIME", args)` (`:4909`), `_execute_impl("OPEN_APP", …)` (`:5040`), `_execute_impl("READ_FILE", …)` (`:6946`) | Yes — preserved via `ctx.dispatch(...)` (see §4). |
| **~300 helper functions** | report builders, `_eli_v3_generate_script`, `_explain_memory_runtime_report`, etc. | Yes — they move with their handler or into a shared module; they are not the problem. |

> The only thing being changed is the `if/elif` chain itself. Everything above is the contract the change is held to.

---

## 3. Target architecture — a handler registry

Replace the monolithic `if/elif` with a **dispatch table**: `action → handler(ctx, args) → result`.

```
execute() / execute_action()            # unchanged public entry
  → _ELI_EXECUTOR_MIDDLEWARE_TABLE      # unchanged middleware chain
      → dispatch(action, args)          # NEW: thin router (dict lookup, not if/elif)
          → HANDLERS[action](ctx, args) # one function per action, in a domain module
              → {ok, action, content, response}   # unchanged contract
```

- **`HANDLERS: dict[str, Callable[[Ctx, dict], dict]]`** — populated by a `@handler("ACTION")` decorator (or explicit registration) in each domain module.
- **`dispatch(action, args)`** — the only thing that replaces `_execute_impl`: normalises `action`/`args`, looks up `HANDLERS`, calls it, and on `KeyError` falls back to the legacy `_execute_impl` (see migration, §6). ~40 lines, not 5,705.
- Handlers are **plain module-level functions**, grouped by domain (§5), each independently importable and testable.

### Why a registry, not just "split the function"
A dict lookup makes the action set **enumerable** (`HANDLERS.keys()` ≡ the capability surface — can be diffed against `capability_manifest.json` in a test), removes the linear `if/elif` scan, and lets each action be unit-tested by importing one function. Splitting into a few medium functions would not buy any of that.

---

## 4. The dispatch context (`Ctx`)

Handlers need the shared services the god-function currently reaches via closure/module scope. Pass them explicitly via a small frozen context object so handlers stay pure-ish and testable:

```python
@dataclass(frozen=True)
class Ctx:
    dispatch: Callable[[str, dict], dict]   # re-entrant call → replaces _execute_impl("X", …)
    security: SecurityManager | None        # _get_security_manager()
    settings: dict                          # runtime settings snapshot
    log: logging.Logger
```

- Self-delegation `return _execute_impl("READ_FILE", {...})` becomes `return ctx.dispatch("READ_FILE", {...})` — **routes back through the middleware chain**, identical semantics.
- In tests, `Ctx` is trivially constructed with a fake `dispatch` and a null security manager — the thing that is impossible today.

---

## 5. Domain grouping of the actions

**As built (measured):** the actual package is `eli/execution/effectors/` (not
`handlers/`), and the grouping came out **finer** than the coarse proposal
below — 34 action modules rather than ~14, each a tight family. Handlers are
plain `handler(a, args) -> dict` functions (the `Ctx` object in §4 was not
needed — self-delegation reaches the executor via a lazy import instead). The
authoritative as-built map:

| Effector module (count) | Actions owned |
|---|---|
| `window_control` (13) | CLOSE/FOCUS/HIDE_APP, MAXIMISE/MINIMISE/MINIMIZE_WINDOW, MINIMISE_ALL, MINIMISE/MINIMIZE_APP, NEXT/PREVIOUS_WINDOW, RESTORE_WINDOWS, SWITCH_WORKSPACE |
| `introspection` (12) | RUNTIME/COGNITION/GPU/MEMORY/IMAGE_STATUS, RUNTIME/IMPORT_AUDIT, DIAGNOSE_WRAPPERS, RESOLVE_RUNTIME_PATHS, EXPLAIN_* |
| `coding` (10) | CODE_SOLVE, CODE_CHANGES, GENERATE_SCRIPT/PROJECT/TESTS, EXAMINE_CODE, FIX_FILE, FILE_AUDIT, CONFIRM/CANCEL_CODE_FIX |
| `media` (8) | PLAY/PAUSE/STOP/NEXT/PREVIOUS/SHUFFLE/REPEAT_MEDIA, NOW_PLAYING |
| `status_reports` (8) | GET_STATUS, AWARENESS/FRONTIER/HABIT/LORA_STATUS, CPU_USAGE, HARDWARE_PROFILE, MEMORY_STATS |
| `plugins` (7) | PLUGIN_INSTALL/UNINSTALL/ENABLE/DISABLE/LIST/SEARCH/STATUS |
| `system_open` (6) | OPEN_AUDIO/POWER/SYSTEM_SETTINGS, OPEN_BROWSER/MEDIA_HUB/FILE_SYSTEM |
| `gaze` (5), `self_improve` (5) | GAZE_*; SELF_ANALYZE/IMPROVE/PATCH/REPORT/TEST |
| `input_control`, `voice_input`, `datetime_calendar`, `persona_lock`, `wake_word`, `web_info` (4 each) | clipboard/keyboard/mouse; dictation/STT; date/calendar; persona-lock; wake-word; web/news/weather/morning-report |
| `apps_windows`, `content_analysis`, `pomodoro`, `self_maintenance` (3 each) | open-url/hub; OCR/image/CSV; pomodoro; proactive daemon |
| `document_analysis`, `document_creation`, `jobs`, `timers` (2 each) | PDF analysis; doc creation; background jobs; alarms/timers |

Six further files carry no actions — `registry.py`, `_base.py`, and the shared
primitive helpers `inference_backends`, `memory_ops`, `system_intel`,
`tts_speech`.

The original coarse proposal (kept for the design rationale) was one module per
domain under `eli/execution/handlers/`, each ~8–20 handlers:

| Module | Actions (representative) |
|---|---|
| `system_control.py` | OPEN_APP, CLOSE_APP, FOCUS_APP, OPEN_BROWSER, OPEN_IDE, OPEN_URL, OPEN_*_SETTINGS, OPEN_FILE_SYSTEM, *_WINDOW(S), TILE/MINIMISE/MAXIMISE, SWITCH_WORKSPACE, KEYBOARD, MOUSE_CONTROL, VOLUME, SMART_HOME, GET/SET_CLIPBOARD |
| `shell.py` | RUN_CMD, SHELL_EXEC (security-gated) |
| `hardware_status.py` | CPU_USAGE, RAM_USAGE, SYSTEM_STATS, HARDWARE_PROFILE, GET_STATUS, AWARENESS_STATUS, ORCHESTRATION_STATUS (GPU_STATUS path included) |
| `files_docs.py` | READ_FILE, CREATE_FILE, CREATE_FOLDER, LIST_DIR, CREATE/GENERATE/CONVERT_DOCUMENT, SUMMARIZE_FILE, FILE_AUDIT, FIX_FILE, ANALYZE_CSV/PDF/PDF_FOLDER, DATA_FABRICATOR |
| `vision_screen.py` | ANALYZE_IMAGE, OCR_IMAGE, SCREENSHOT, SCREEN_LOCATE, SCREEN_READ_ANALYZE, AMBIENT_VISION, GAZE_* |
| `media.py` | PLAY/PAUSE/STOP/NEXT/PREVIOUS/REPEAT/SHUFFLE_MEDIA, NOW_PLAYING, MEDIA_CONTROL, SKIP_YOUTUBE_AD |
| `voice.py` | SPEAK, DICTATE, TRANSCRIBE, LISTEN_FOR_COMMAND, TRAIN_VOICE, WAKE_*, VOICE/STT_DIAGNOSTICS |
| `coding.py` | CODE_SOLVE, CODE_CHANGES, GENERATE_SCRIPT/PROJECT/TESTS, RUN_TESTS, TEST_REVIEW, SHOW_DIFF, SELF_ANALYZE/IMPROVE/PATCH/TEST, LORA_TRAIN/STATUS |
| `memory_notes.py` | MEMORY_RECALL/STORE/STATS, NEW_NOTE, WRITE_NOTE, LIST_NOTES, SEARCH_NOTES, CLEAR_CHAT_HISTORY |
| `time_info.py` | TIME, DATE, GET_TIME, GET_DATE, MESSAGE_TIME_QUERY, ADD_EVENT, LIST_EVENTS, GET_WEATHER, WEB_SEARCH, HELP |
| `persona_identity.py` | PERSONA_LOCK_*, PERSONA_REFRESH, ELI_IDENTITY_AUDIT, USER_INFO_REPORT, REFRESH_USER_INFO, SET_USER_NAME, SET_AI_MODE, MORNING_REPORT |
| `proactive_schedule.py` | PROACTIVE_*, HABIT_STATUS, GET_PROPOSALS, SCHEDULE_TASK, BACKGROUND_JOBS, POMODORO_* |
| `plugins.py` | PLUGIN_* |
| `meta.py` | CHAT, MULTI_COMMAND, SEQUENCE, FRONTIER_STATUS, CHECK_CHRONAL_ALIGNMENT |

The package `__init__.py` (under `eli/execution/handlers/`) imports every module (registering handlers as a side effect) and exposes `HANDLERS`.

---

## 6. Migration — strangler-fig, one domain at a time

Never a big-bang rewrite. The legacy `_execute_impl` stays as the fallback until empty.

**Phase 0 — scaffolding (no behaviour change).**
1. Add `eli/execution/handlers/` with the registry, the `@handler` decorator, and `Ctx`.
2. Add `dispatch(action, args)`: look up `HANDLERS`; on miss, **call the existing `_execute_impl`**. Wire `dispatch` as what the middleware chain calls (today it calls `_execute_impl` — point it at `dispatch`).
3. At this point `HANDLERS` is empty → every call falls through to `_execute_impl` → byte-identical behaviour. Ship it.

**Phase 1..N — migrate one domain per PR.**
For each domain module (start with the smallest/purest — `time_info`, `hardware_status`):
1. Move each action's branch body into `handle_<action>(ctx, args)`, swapping module-scope calls for `ctx.*` and `_execute_impl("X", …)` for `ctx.dispatch("X", …)`.
2. Register them. They now win the lookup; `_execute_impl` no longer sees those actions.
3. Delete the migrated branches from `_execute_impl`.
4. Run the action's existing tests + a **golden contract test** (§7). PR is behaviour-preserving by construction.

**Phase final — retire the monolith.**
When `_execute_impl` has no branches left, delete it and the fallback arm of `dispatch`. A test asserts `set(HANDLERS) == ` the documented action set so nothing was dropped.

Ordering by risk (low→high): `time_info`, `hardware_status`, `memory_notes`, `plugins`, `proactive_schedule`, `persona_identity`, `files_docs`, `system_control`, `media`, `voice`, `vision_screen`, `coding`, `shell` (gated, last), `meta` (CHAT/SEQUENCE — most entangled, last).

---

## 7. Test strategy (the safety net)

The change is only safe because behaviour is pinned externally:

1. **Contract test** — for every action in `HANDLERS`, assert the result is a dict with `{ok, action}` and (when `ok`) `content`/`response` present; `action` echoes the request. One parametrised test over all 145.
2. **Capability-surface test** — `set(HANDLERS.keys())` must equal the action set in `capability_manifest.json` / `capability_inventory.generated.json`. Catches a dropped or renamed action immediately.
3. **Golden per-action tests** — the existing suite (7,943 tests collected, incl. `tests/claims/test_supported_actions.py`, router/executor suites) already exercises most actions; run the relevant slice after each domain PR. Add golden-output tests for any action lacking one *before* moving it.
4. **Differential check during migration** — a temporary test can call both `dispatch("X", args)` and the legacy `_execute_impl("X", args)` for pure/read-only actions and assert equal results, proving the move is faithful before the old branch is deleted.
5. **Ratchet** — the 950 `except: pass` ceiling must not rise; ideally each migrated handler converts its swallows to `ctx.log.debug(..., exc_info=True)`, *lowering* the ceiling as a side benefit.

---

## 8. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Hidden coupling between branches (shared locals, fallthrough) | Strangler keeps `_execute_impl` live as fallback; differential check (§7.4) before deleting any branch. |
| Self-delegation semantics drift (middleware re-entry) | `ctx.dispatch` routes through the same chain `_execute_impl` recursion did — proven equal by the differential check. |
| A handler import error silently disables an action | `handlers/__init__` import failure is loud (not swallowed); capability-surface test (§7.2) fails CI if an action vanishes. |
| Security gate bypass during a move | `shell`/`RUN_CMD`/`SHELL_EXEC` migrated **last**, with an explicit test that the blocked-pattern path still returns the block result. |
| Scope creep into behaviour "improvements" | Hard rule: decomposition PRs change *structure only*. Any behaviour fix is a separate PR with its own test. |

**Rollback:** each phase is an independent PR; reverting one restores its actions to `_execute_impl` (still present until the final phase). No phase is load-bearing for the next beyond the empty-registry scaffold.

---

## 9. Explicit non-goals

- **No behaviour change.** Same actions, same outputs, same side effects, same contract.
- **No middleware/contract/gate redesign.** Those are sound; they are preserved verbatim.
- **No new actions or capabilities.** Pure restructuring of existing 145.
- **Not coupled to the `engine.process` god-function.** That 2,946-line function is the *same class* of debt and a candidate for the identical treatment (pipeline-stage registry), but it is **out of scope here** and gets its own blueprint.

---

## 10. Effort & sequencing

- Phase 0 scaffold: ~half a day, fully behaviour-preserving, immediately shippable.
- ~13 domain PRs, each small and independently reviewable/revertible.
- The win compounds: after the first 2–3 domains the pattern is mechanical, and every migrated action gains real unit tests it never had.

Success = `_execute_impl` deleted, `dispatch` is ~40 lines, the actions live in legible per-family effector modules (23 so far, 169 actions), the full suite stays green, and the swallow ceiling has *dropped*. Current: 56% strangled, executor 14,592 → 6,442 LOC.

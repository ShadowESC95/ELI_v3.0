> **Updated for 3.0.171 (November 2026).** Gradient orchestrator for all CHAT modes; shared
> `memory/retrieval.py`; canonical S01–S12 via `pipeline_trace.py`. RESUME_TASK
> and MULTI_COMMAND's dependency/crash-survival layer ported from v2; agent-bus
> confidence and habit learning gained relevance/source integrity checks.

# ELI Capability Catalogue — every action & module, what it actually does

> **Purpose.** A systematic, ground-truth catalogue built by reading the real
> handlers and modules — not summarised from memory. It exists because
> conversational summaries of a 233,520-line project (`eli/`, 608 Python files, measured at 3.0.167) keep undershooting; this is the
> persisted, exhaustive map.
>
> **Method.** Action list comes from the live `capability_manifest.json` (**242**
> entries; 200 routable, 224 in the executor's supported list, 230 in either),
> verified against the `executor_enhanced.py` dispatch. **The always-current,
> auto-generated action list with activation phrases is
> `capabilities_and_actions.md`** — this catalogue is the deeper module-level read.
> Behaviour grounded in the handlers and the
> subsystem blueprints. Where a description is inferred from name+structure
> rather than a line-by-line handler read, it is marked *(inferred)*.
>
> **Coverage:** every action in the manifest (Part 1), every module under `eli/` (Parts 2–4 and 7).

---

## Headline finding: 242 is real but aliased

The manifest's 242 entries are honest (*measured* by `capability_sync`, not asserted)
but inflated by **alias families** — multiple action names routing to one
behaviour. Collapsed, there are roughly **~110 distinct capabilities**. Alias
families are grouped below so the real surface is visible.

---

## 1. Conversation & reasoning
| Action(s) | What it does |
|---|---|
| `CHAT` | The default — full cognitive pipeline (router → gradient orchestrator → mode algo → governed output). |
| `ANSWER`, `DIRECT_RESPONSE`, `SAY` | Direct/short response surfaces (lighter than CHAT). *(inferred for some)* |
| `SET_AI_MODE` | Set the reasoning mode (Quick / Normal / Advanced / Research / Expert). Legacy names still accepted as input. |
| `SET_TONE`, `CLEAR_TONE` | Pin a tone for ELI's delivery (words, voice, face) / drop it and return to situation-adaptive delivery. |
| `SET_COMMUNICATION_STYLE` | Persist how the user wants to be spoken to (a reset phrase clears it). |
| `PERSONA_REFRESH` | Reload the persona from the latest profile. |
| `SEQUENCE` | Multi-step action chaining (run a sequence of actions). |
| `MULTI_COMMAND` | The real chained-command executor ("close steam and set an alarm"). Router's `command_splitter.py` splits; the executor runs in topological layers by inferred dependency (`command_dependency_graph.py`, `eli/core/dag`), durably records each step's outcome (`command_sequence_log.py`) so a crash mid-sequence isn't a silent loss and a resubmit within an hour skips steps already known to have succeeded. `SEQUENCE` above is a different, simpler mechanism. |
| `NOOP` | No-op. |

## 2. OS & application control
| Action(s) | What it does |
|---|---|
| `OPEN_APP` = `OPEN_APPLICATION` = `LAUNCH_APP` | Launch an app, resolved via the `system_index` (your machine's executables — thousands, indexed live) + `portable_app_control`. On failure → **remediation offer** (real apt/snap/flatpak install on confirmation). |
| `CLOSE_APP` = `CLOSE_APPLICATION` = `EXIT_APP` = `QUIT_APP` | Close/quit an app. |
| `FOCUS_APP`, `HIDE_APP` | Focus / hide a window. |
| `MINIMISE_APP` = `MINIMIZE_APP`, `MINIMISE_WINDOW` = `MINIMIZE_WINDOW`, `MAXIMISE_WINDOW` | Window state control (UK/US spellings aliased). |
| `MINIMISE_ALL`, `RESTORE_WINDOWS`, `TILE_WINDOWS`, `NEXT_WINDOW`, `PREVIOUS_WINDOW` | Desktop window management. |
| `SWITCH_WORKSPACE` | Switch virtual desktop/workspace. |
| `OPEN_SYSTEM_SETTINGS`, `OPEN_AUDIO_SETTINGS`, `OPEN_POWER_SETTINGS`, `OPEN_FILE_SYSTEM`, `OPEN_COMMUNICATION_HUB`, `OPEN_MEDIA_HUB`, `OPEN_NETWORK_BROWSER` | Open specific OS setting panels / hub launchers. |
| `OPEN_IDE`, `OPEN_IN_IDE` | Open the IDE (optionally at a file). |
| `OPEN_BROWSER`, `OPEN_URL` | Open browser / a URL (local hand-off; respects net toggle for fetches). |

## 3. Input & screen control
| Action(s) | What it does |
|---|---|
| `KEYBOARD` | Send keypresses (`os_controller.press_key`). |
| `MOUSE_CONTROL` | Move/click the mouse (`os_controller.mouse_click`). |
| `VOLUME` | Get/set system volume. |
| `SET_CLIPBOARD`, `GET_CLIPBOARD` | Read/write the clipboard. |
| `SCREENSHOT` | Capture the screen. |

## 4. Gaze (webcam eye-tracking)
| Action(s) | What it does |
|---|---|
| `GAZE_ENABLE`, `GAZE_DISABLE` | Start/stop the MediaPipe gaze engine (writes `latest_gaze.json` @10Hz). |
| `GAZE_CALIBRATE` | Run gaze calibration. |
| `GAZE_CLICK` | Move the cursor to where you're looking and click. |
| `GAZE_STATUS` | Report gaze-engine state. |

## 5. Media
| Action(s) | What it does |
|---|---|
| `PLAY_MEDIA` | Play a song/playlist on a named platform (Spotify playlist-tab / YouTube Mix-radio); honest about reachability; explicit platform never falls through to a second. |
| `PAUSE_MEDIA`, `STOP_MEDIA`, `NEXT_MEDIA`, `PREVIOUS_MEDIA`, `REPEAT_MEDIA`, `SHUFFLE_MEDIA` | MPRIS/playerctl transport control. |
| `MEDIA_CONTROL` | Generic media-control dispatch. |
| `SKIP_YOUTUBE_AD` | Skip a YouTube ad. *(inferred)* |
| `NOW_PLAYING` | Report the current track and where it is playing. |

## 6. Files & documents
| Action(s) | What it does |
|---|---|
| `CREATE_FILE`, `CREATE_FOLDER`, `READ_FILE`, `LIST_DIR` | Filesystem ops (path-allowlist gated). |
| `SUMMARIZE_FILE` | Summarise a file's contents (also the route for "read your persona.auto.txt / settings"). |
| `CONVERT_DOCUMENT` | Convert document formats. |
| `CREATE_DOCUMENT` = `CREATE_DOC` = `DOC_GENERATE` = `GENERATE_DOCUMENT` = `WRITE_DOCUMENT` | Generate a document (the Report-Builder family). |
| `ANALYZE_CSV`, `ANALYZE_PDF`, `ANALYZE_PDF_FOLDER`, `ANALYZE_IMAGE`, `OCR_IMAGE` | Local file-type analysers (CSV stats, PDF text/structure, image VL description, OCR). |

## 7. Vision & screen understanding
| Action(s) | What it does |
|---|---|
| `ANALYZE_IMAGE` | Local GGUF vision-language description of an image. |
| `OCR_IMAGE` | Tesseract OCR text extraction. |
| `AMBIENT_VISION` | Toggle periodic background screen glances. |
| `SCREEN_LOCATE` | OCR-locate a named UI element or word on screen ("the button that says X", "find the word compose"), and click it ("click compose"). |
| `SCREEN_READ_ANALYZE` | Screenshot → analyse what's on screen. |
| `IMAGE_STATUS` | Report vision/image-engine state. |

## 8. Coding & code-repair
| Action(s) | What it does |
|---|---|
| `CODE_SOLVE` | The frontier coding agent: plan → DAG decompose → UCB tree search → verify (syntax/exec/tests) → repair → bug-memory. |
| `GENERATE_SCRIPT` = `CREATE_SCRIPT` = `WRITE_SCRIPT` = `GENERATE_CODE` = `WRITE_CODE` | Generate a script — routes through the coding agent (falls back to inline gen). |
| `GENERATE_PROJECT` | Generate a multi-file project. |
| `EXAMINE_CODE` | Tiered file scan (syntax/import → lint → gated LLM logic review) → offer fix. |
| `FIX_FILE`, `CONFIRM_CODE_FIX`, `CANCEL_CODE_FIX` | Apply/confirm/cancel a verified auto-reverting code fix. |
| `FILE_AUDIT`, `SHOW_DIFF`, `CODE_CHANGES` | Audit a file / show a diff / report recent code changes. |
| `RUN_TESTS`, `TEST_REVIEW`, `GENERATE_TESTS` | Run the pytest suite and summarise the results document / also back up the report, write an errors file and offer fix options / write and sandbox-verify behavioural tests for ELI's own functions. |
| `CODEBASE_GRAPH` | Import graph of ELI's own source: what depends on what (`runtime/codebase_graph.py`). |
| `COMPUTER_USE` | The closed observe → decide → act → verify loop over the screen (`execution/effectors/computer_use.py`). |

## 9. Self-management & self-improvement
| Action(s) | What it does |
|---|---|
| `SELF_REPORT` | Grounded runtime self-report (deterministic, from live state). |
| `SELF_ANALYZE`, `SELF_IMPROVE`, `SELF_IMPROVEMENT_LOG` | Analyse failures / surface improvement proposals / show the improvement log. |
| `SELF_PATCH` | Generate+apply a verified self-patch. |
| `APPROVE_CANDIDATE` | Approve or decline a proposed fix to ELI's own code. |
| `AUTOPILOT_DEBUG`, `SELF_REPAIR_PLAYBOOK` | Turn a failure into a plan (root cause, affected files, rollback, patch, validation) / the grounded repair decision guide. |
| `LORA_STATUS`, `LORA_TRAIN` | LoRA fine-tune readiness / run the training pipeline (dry-run from chat; real training via the overnight task). |
| `VERSION_CHANGES` | Which version ELI is, when it was first seen here and the versions before it (`runtime/version_record.py`). |
| `SELF_TEST` | Run self-tests. |
| `SELF_UPGRADE` = `SELF_UPDATE` | Maintenance: git pull, pip, rebuild FAISS/KG, refresh manifest + system index. |

## 10. Introspection & audits (grounded, deterministic)
| Action(s) | What it does |
|---|---|
| `RUNTIME_STATUS`, `RUNTIME_AUDIT` | Live runtime report; RUNTIME_AUDIT also runs **health probes** (plugin mgr, memory, agent bus, habit integrity, recent failures). |
| `GUI_RUNTIME_AUDIT`, `IMPORT_AUDIT`, `DIAGNOSE_WRAPPERS`, `RESOLVE_RUNTIME_PATHS` | GUI/runtime audits, import smoke-audit, executor-wrapper diagnostic, path resolution. |
| `FRONTIER_STATUS`, `ELI_IDENTITY_AUDIT` | Full cross-system matrix / identity-classification audit. |
| `COGNITION_STATUS`, `EXPLAIN_COGNITION_RUNTIME`, `EXPLAIN_MEMORY_RUNTIME` | Cognition + memory runtime explainers (verbatim, deterministic). |
| `EXPLAIN_ALL_REASONING_MODES`, `REASONING_MODE_STATUS` | Describe the 5 reasoning modes / current mode. |
| `EXPLAIN_LAST_RESPONSE`, `EXPLAIN_LAST_FAILURE`, `NAME_SOURCE_AUDIT`, `ROUTING_FAULT_EXPLAIN` | Explain the last answer/failure, the source of your name, or a routing fault. |
| `HARDWARE_PROFILE`, `GPU_STATUS`, `GET_STATUS`, `AWARENESS_STATUS` | Hardware/GPU/general/awareness status. |
| `EXPLAIN_GGUF_DIAGNOSTICS`, `EXPLAIN_FAILURE_LOG`, `TIMESTAMP_DIAG`, `ORCHESTRATION_STATUS` | GGUF load/inference compatibility report / the executor failure log / real clock and timestamp diagnostics / the agent DAG's layers, dependencies and last run. |
| `HELP`, `LIST_CAPABILITIES` | Help / list the capability surface. |

## 11. Memory & identity
| Action(s) | What it does |
|---|---|
| `MEMORY_RECALL`, `MEMORY_STORE`, `MEMORY_STATS`, `MEMORY_STATUS` | Recall/store memories; memory inventory/stats. |
| `MEMORY_FORGET` | Delete matching memories and everything derived from them, after confirmation. |
| `MEMORY_AUDIT` | Why ELI believed something (`runtime/memory_audit.py`). |
| `PERSONAL_MEMORY_SUMMARY`, `PERSONAL_MEMORY_DEEP_EXPLAIN` | "What do you know about me" (clean) / deep memory-internals explain. |
| `USER_IDENTITY_SUMMARY`, `USER_INFO_REPORT`, `REFRESH_USER_INFO` | Who-you-are summary / full profile report / rebuild the living profile. |
| `SET_USER_NAME` | Set the user's name. |
| `MESSAGE_TIME_QUERY` | "What time did I first/last message (today)" — from `conversation_turns`. |
| `CLEAR_CHAT_HISTORY` | Clear chat history. |
| `PERSONA_LOCK_SET`, `PERSONA_LOCK_CLEAR`, `PERSONA_LOCK_STATUS` | Lock/unlock/inspect the persona. |

## 12. Habits, proactivity & goals
| Action(s) | What it does |
|---|---|
| `CONFIRM_HABIT`, `DECLINE_HABIT`, `HABIT_STATUS` | Approve/decline a detected habit; report habits. |
| `MORNING_REPORT` | The consolidated morning brief (news digest + activity + attention). |
| `PROACTIVE_START`, `PROACTIVE_STOP`, `PROACTIVE_STATUS` | Control the proactive daemon. |
| `EXECUTE_GOAL` | Execute a mission-layer goal (governed). |
| `RESUME_TASK` | Explicit "continue/resume the X project" — `goal_store.find_task()` bypasses the passive 14-day idle window, states real staleness, says plainly when nothing matches rather than guessing. |
| `BACKGROUND_JOBS`, `CHECK_JOB` | List background tasks / check a job id. |
| `SCHEDULE_TASK`, `LIST_TASKS`, `CANCEL_TASK`, `RESCHEDULE_TASK` | Schedule overnight/timed work; list, cancel or move a scheduled task (`runtime/task_admin.py`). |
| `GOAL_PROGRESS` | Report progress on a goal being worked across sessions. |
| `GET_PROPOSALS` | ELI's self-generated proposals/goals from observed signals. |

## 13. Self-healing remediation (Linux)
| Action(s) | What it does |
|---|---|
| `PREPARE_REMEDIATION` | Diagnose a failure + build a repair plan (e.g. install a missing app via apt/snap/flatpak). |
| `CONFIRM_PENDING_REMEDIATION`, `CANCEL_PENDING_REMEDIATION` | Execute / cancel the pending repair (sudo terminal, lock-handling, verification). |
| `CHECK_TARGET_STATUS` | Check whether a target app/path is now present. |

## 14. Voice & transcription
| Action(s) | What it does |
|---|---|
| `DICTATE`, `TRANSCRIBE` | Dictation / transcribe audio (faster-whisper). |
| `LISTEN_FOR_COMMAND` | One-shot listen. |
| `STT_DIAGNOSTICS`, `VOICE_DIAGNOSTICS` | Diagnose the STT/voice pipeline (mic, ducking, wake gate). |
| `SAY` / `SPEAK` *(plugin: tts)* | Speak text (Piper TTS). |
| `LIST_VOICES`, `SET_VOICE`, `DOWNLOAD_VOICE` | List installed and downloadable voices / switch the TTS voice / download one from the Piper library. |
| `CREATE_VOICE`, `DESIGN_VOICE` | Make a voice from a recording (XTTS-v2 clone) / build a style voice from a base voice with pitch, speed and effects. |
| `WAKE_SET`, `WAKE_TRAIN`, `WAKE_ENROLL`, `TRAIN_VOICE` | Set the wake word / train the local wake-word model / record the user saying it and retrain / learn the user's voice and tone. |
| `SPEAKER_ENROLL`, `SPEAKER_STATUS`, `LIST_SPEAKERS` | Speaker identification for shared machines (`perception/speaker_id.py`): enrol a speaker, status, list. |

## 15. Time, timers & utility
| Action(s) | What it does |
|---|---|
| `TIME` = `GET_TIME`, `DATE` = `GET_DATE` | Current time / date. |
| `SET_TIMER`, `SET_ALARM` | Timers/alarms. *(inferred)* |
| `CHECK_CHRONAL_ALIGNMENT` | Easter-egg: "Chronal alignment nominal. Local time: …" (a playful time report). |
| `DATA_FABRICATOR` | Generate a document from a topic (via CREATE_DOCUMENT) **and open it in an editor** (code/gedit/kate/nano). |
| `RUN_CMD`, `SHELL_EXEC` | Run a shell command — **fail-closed** (blocked unless `ELI_ALLOWED_CMDS` is set or ELI Full Control is on). |

## 16. Plugins (management)
| Action(s) | What it does |
|---|---|
| `PLUGIN_LIST`, `PLUGIN_SEARCH`, `PLUGIN_STATUS` | List installed / search registry / status. |
| `PLUGIN_INSTALL`, `PLUGIN_UNINSTALL`, `PLUGIN_ENABLE`, `PLUGIN_DISABLE` | Install/remove/enable/disable a plugin. |
| `MCP_LIST`, `MCP_STATUS`, `MCP_TOOLS`, `MCP_CALL` | List configured MCP servers / their status / the tools they expose / call one. |
| `MCP_ADD`, `MCP_REMOVE`, `MCP_DOCTOR` | Add an MCP server (switched off until enabled) / remove one / launch preflight and handshake check. |

## 17. Plugin-backed capabilities
| Action | Plugin | What it does |
|---|---|---|
| `WEB_SEARCH` | web | DuckDuckGo/SearXNG web search (toggle-gated). |
| `GET_WEATHER` | weather | Local geocode + open-meteo (toggle-gated). |
| `NEW_NOTE`/`WRITE_NOTE`, `LIST_NOTES`, `SEARCH_NOTES` | notes | Markdown notes with FTS. |
| `ADD_EVENT`, `LIST_EVENTS`, `REMOVE_EVENT` | calendar | ELI's own local calendar and reminders (`runtime/agenda.py`): add, move, list, remove. Mirrored to `calendar.ics`. |
| `POMODORO_START`, `POMODORO_STOP`, `POMODORO_STATUS` | pomodoro | Focus timers. |
| `CPU_USAGE`, `RAM_USAGE`, `SYSTEM_STATS` | system_stats | CPU/RAM/disk/network. |
| `SMART_HOME` | executor and device server | Smart-home control via ELI's own MQTT device server (ESPHome / Tasmota / Zigbee2MQTT); rooms, scenes, real automations. Home Assistant removed. |
| `SPEAK` | tts | Text-to-speech. |
| `NEWS_FETCH` | (executor + news tool) | Fetch + synthesise news (rolling 3-hourly reflections → 24h digest). |
| `WEB_FETCH` | (executor) | Read a linked web page and answer about it (Net-gated). |

*(Also reachable: `web_automation` plugin — Playwright browser navigate/search/screenshot; `document_reader` plugin — PDF/docx read+index.)*

## 18. ELI's world
| Action(s) | What it does |
|---|---|
| `PLAY_GAME`, `STOP_GAME` | Make up a small game for ELI's rooms (targets, catch, sort, order, hide) where the user stands in ELI VR / stop it (`world/games.py`). |
| `IMPORT_MODEL`, `FETCH_MODEL` | Bring a `.gltf`/`.glb` file on this computer into ELI's rooms / fetch a CC0 model of a thing (Net-gated) (`world/models.py`). |

---

## Reading note
Alias families that collapse the count: app-open (3), app-close (4), minimise
(4 across window/app + UK/US), document-generate (5), script/code-generate (5),
time/date (2 each), self-upgrade/update (2). Removing pure aliases yields
**~110 genuinely distinct capabilities** — still an unusually broad surface for a
local single-user assistant, spanning OS control, media, files, vision, gaze,
voice, coding, memory, introspection, autonomy, remediation, and plugins.

---

# Part 2 — `runtime/` module catalogue (104 files, 41.7k lines)

The largest package. It's the **grounding/governance + introspection + plumbing**
layer that wraps the probabilistic model. Grouped by function:

## Grounding & anti-confabulation
| Module | LOC | Role |
|---|---|---|
| `deterministic_grounding_gate.py` | 2556 | Renders control/status answers directly from live runtime (bypasses the model). One ordered `render_action` pipeline of layers composed by `_stack`; an unhandled action returns a `missing_deterministic_renderer` surface. |
| `grounding_escalation.py` | 891 | When a **checkable factual** question is poorly grounded by the bus, escalates through agent tiers instead of letting the model confabulate (the "Orlo's real name" failure class). A clarification of an earlier question (`clarifies_a_question`) and "the new one" of what the talk is about are looked up. |
| `diagnostic_patterns.py` | 89 | Regexes that catch vague/dynamic status confabulation ("currently processing updates…") and image-status fabrication. |
| `control_contracts.py` | 1426 | Control-action evidence contract: build evidence → validate the model's output doesn't violate it → finalise. |
| `persistence_gate.py` | 208 | Gates what gets stored — refuses to persist internal dumps / error-pattern noise as memory. |

## Self-honesty / introspection / self-reporting
| Module | LOC | Role |
|---|---|---|
| `live_introspection.py` | 622 | Live runtime snapshot, last trace, stored user name, mines user-fact candidates, agents-for-action, build_report. |
| `deterministic_introspection.py` | 614 | The engine's live diagnostic dispatcher (`handle_diagnostic_action`) — deterministic answers for RUNTIME_STATUS / EXPLAIN_* / IMPORT_AUDIT. |
| `truth_report.py` | 419 | Runtime truth report (git, nvidia, GGUF runtime, import health). |
| `frontier_status.py` | 416 | Full cross-system status matrix (runtime/memory/awareness/proactive/image/world/chatflow). |
| `eli_identity_audit.py` | 416 | Identity-classification audit (source inventory, contract counts, capability matrix). |
| `reasoning_status.py` | 138 | Current reasoning-mode reporting. |
| `experimental_inventory.py` | 139 | Inventory of experimental projects. |

## Self-improvement & code-awareness
| Module | LOC | Role |
|---|---|---|
| `self_improvement.py` | 2086 | Failure logging/clustering, patch generate→verify→apply→**auto-revert**, full patch cycle, plugin-stub gen. |
| `code_examiner.py` | 948 | Tiered file error scan (syntax/import → lint → gated LLM) → offer → verified fix. |
| `code_monitor.py` | 268 | Detects source changes via git diff, classifies by subsystem, summarises for memory/context (ELI is aware of its own code changes). |
| `capability_sync.py` | 520 | **AST-discovers** the live capability surface, diffs, writes `capability_manifest.json` — this is why the count is *measured*, not asserted. |
| `generated_script_guard.py` | 214 | Checks a generated script or document after it is written and **quarantines invalid scripts**; writes none itself (the one generator is `execution/effectors/coding.py`). |

## Self-healing remediation
| Module | LOC | Role |
|---|---|---|
| `grounded_remediation.py` | 1654 | Diagnoses failures (missing app/path/browser) → builds an apt/snap/flatpak repair plan → offers → **executes** (sudo terminal, lock-handling, verify). |
| `incident_log.py` | 21 | Writes incident records. |

## Awareness & boot
| Module | LOC | Role |
|---|---|---|
| `awareness_boot.py` | 385 | Boots all awareness subsystems at startup, returns an `AwarenessState` the engine queries. Its live self-model line goes in every prompt and does not name the avatar's room (the room reaches the model only when the person asks about ELI's world). |
| `action_commitment.py` | 346 | Detects when ELI's reply commits to an action. The engine then runs it only if it is a read (news, weather, calendar listing) or something the user's own message asked for; anything that changes state waits for a "yes". |

## Autonomy / operator (governed)
| Module | LOC | Role |
|---|---|---|
| `operator_state.py`, `operator_feed.py` | ~160 | Operator console state: proposals, goals, self-model status, event feed. |
| `pending_proposal.py` | 908 | What ELI's last reply left open, for 30 minutes: the steps it offered (`read_reply`: actions, tasks ELI does by writing, explicit-only, what it said it would do before a "Deal?"), the question it ended on, a detail an action asked for, and where to save what it wrote (`destination`, `what_was_written`). Reads the user's reply against it: `is_consent`, `chosen_items`, `selection`, `is_the_detail`. |
| `agenda.py` | 1073 | ELI's calendar and reminders: SQLite store, plain-language dates and times (`parse_when`), a notifier thread that delivers what is due (and what was missed while shut), `calendar.ics` mirror, the next 36 hours as a prompt line. |
| `approval_engine.py` | 93 | Who may propose / evaluate a proposal record (governance). |

## Response surfaces & governance
| Module | LOC | Role |
|---|---|---|
| `user_visible_response_surface.py` | 207 | Installs the engine's user-visible response surface (runtime/identity/name-source formatting + streaming coercion). |
| `visible_output.py`, `visible_text.py` | ~96 | Central visible-output contract; stringify/sanitise streamed output. |
| `response_contracts.py` | 71 | Per-action response contracts (`contract_for_action`, `is_quick_action`). |
| `final_response_provider.py` | ~85 | Assemble the final prompt; per-action generation decoration; fastpath context. |

## Personal-memory surfaces
| Module | LOC | Role |
|---|---|---|
| `personal_memory_surface.py` | 468 | "Is this a personal-memory query" + surface builder. |
| `personal_memory_clean_response.py` | 398 | Clean "what do you know about me" report (reset-aware, poison-filtered, dynamic-fact aging). |
| `personal_memory_deep_response.py` | 194 | Deep memory-internals explain (schema/tables/functions) + routing-fault explain. |
| `profile_extractor.py` | 2051 | Extracts user facts from turns (role/interests/field/"remember that I…"), writes user_patterns + LLM session summaries; recency refresh. |
| `identity_validation.py` | 236 | Validate identity candidates. |

## Typed pipeline plumbing (evidence/packets)
| Module | LOC | Role |
|---|---|---|
| `evidence_ledger.py` | 914 | Records artifacts/events with signatures; recent generated artifacts; status evidence; reliability and success prediction per action; the figures and outcomes the calibration reads; every execution read back for the learning loop (`executor_runs`). |
| `action_outcomes.py` | 721 | What became of ELI's actions, as something it learns from: a run checked against the world, the cause of a failure by the one taxonomy, the chance a run like this works here (by target, shrunk toward the action and every action), what worked instead after such a failure, the alternative to try now or the advice to give, and the record told in words (see `epistemic_dynamics.md` §9). |
| `evidence_arbitration.py` | 74 | Scores competing evidence (stage packets + tool results + goals), dedup-by-fingerprint, keep-max. |
| `pipeline_models.py` | 45 | The typed route and plan records (`RouteDecision`, `PlanStep`, `ExecutionPlan`). |
| `background_tasks.py` | 567 | In-process multi-threaded task manager (heavy work → job id → `CHECK_JOB`). |
| `runtime_policy.py` | 124 | Per-turn budgets/timeouts/context size from runtime snapshot. |

## Security
| Module | LOC | Role |
|---|---|---|
| `security.py` | 210 | `SecurityManager` — fail-closed command/path/app allowlist sandbox. |

---

# Part 3 — `cognition/` module catalogue (57 files, 27.3k lines)

The thinking layer: agents, orchestration, inference, persona, reasoning, governance.

## Orchestration & agents
| Module | LOC | Role |
|---|---|---|
| `agent_bus.py` | 3729 | 15 specialist agents on a dependency DAG (topological layers) + calibrated weight-free confidence aggregation + per-action agent selection. |
| `orchestrator.py` | 1452 | Gradient 12-stage pipeline (all CHAT modes): planner → `retrieve_for_turn()` → `dispatch_specialists()` → heuristic rerank → context assembly. Composes the specialist bus; no longer Quick-only bypass. |
| `learning_coordinator.py` | 104 | Stage 12 `finalize_turn()` — store assistant turn, publish meta, `_learn_from_result()`. |
| `hyde.py` | 36 | Hypothetical-document-embedding query expansion. |
| `reranker.py` | 248 | Candidate reranking (token overlap, source priority, and how readily a memory comes to mind when the recall worked it out). |
| `introspection_agent.py` | 120 | Wraps introspection for the bus (pipeline/memory/runtime/audit). |
| `llm_intent.py` | 399 | LLM intent parsing fallback (GGUF, cached). |

## Inference
| Module | LOC | Role |
|---|---|---|
| `gguf_inference.py` | 3010 | Model-agnostic GGUF inference: model resolution (no baked model), family-aware chat templating, graceful GPU-layer fallback, streaming, output cleaning, token budgeting. |
| `inference_broker.py` | 447 | Thin GGUF broker (`infer`) used by agents/coding/patching. |

## Reasoning & engagement
| Module | LOC | Role |
|---|---|---|
| `reasoning_modes.py` | 702 | The 5 modes — canonicalisation, per-mode private system instruction, execution contract (samples/branches/stages + dynamic token budget), reasoning-leak stripping. |
| `engagement_tracker.py` | 249 | Session depth tracking → **auto-escalates reasoning mode** (Quick→Normal→Advanced→Research) as a conversation deepens; session narrative. |
| `working_memory.py` | 462 | Turn-scoped pinned facts (pin/absorb/evict/persist/restore). |

## Context & grounding
| Module | LOC | Role |
|---|---|---|
| `context_synthesiser.py` | 630 | Builds the precise prompt context: persona handoff, turns block, vector block, live-runtime brief, budgeting. |
| `grounded_status.py` | 565 | Identity + memory-inventory rendered directly from profile/DBs (direct grounded answers). |

## Persona (the living voice)
| Module | LOC | Role |
|---|---|---|
| `persona.py` | 222 | Canonical persona authority — base + auto sections, preferences, compose/refresh. |
| `persona_updater.py` | 729 | Re-derives the persona overlay from memory/reflection/habits/runtime patterns; KG population; stale-fact aging. |
| `persona_hygiene.py` | 93 | Cleans/dedups/prunes the auto-persona. |
| `persona_status.py`, `persona_values.py` | ~110 | Persona status report; values store. |

## Output governance (consolidated this session)
| Module | LOC | Role |
|---|---|---|
| `output_governor.py` | 1563 | **Canonical** governance: sanitize, role-prefix/identity-drift repair, self↔user confusion repair, style cleanup, confabulation detection, quality scoring, memory-worthiness, GGUF-artifact cleaning (`clean_gguf_artifacts`), evidence validation. |
| `tone_analyzer.py` | 369 | Analyses recent user turns → tone preferences ELI adapts to over time. |

## Profile
| Module | LOC | Role |
|---|---|---|
| `user_info_builder.py` | 754 | The living, versioned user profile: multi-source gather, noise filter, categorise, hash, diff-on-change. |

---

# Part 4 — remaining packages (module catalogue)

## `kernel/` (the engine + boot) — 19.6k lines
| Module | LOC | Role |
|---|---|---|
| `engine.py` | 8521 | `CognitiveEngine` — the conductor: persona, generation settings, the 5 `_run_*` reasoning passes, grounding overrides, synthesis prompt build + the context-bloat cap, fragment/placeholder guards, dispatch gate to bus vs orchestrator, startup loops (reflection/habit/scheduler/self-improve/proactive). |
| `world_model.py` | 319 | Symbolic self-model: Identity/Runtime/Memory/Goal/Capability states + snapshot/merge. |
| `pipeline_trace.py` | 56 | Canonical S01–S12 stage names and `log_pipeline_stage()` observability. |
| `state.py` | 336 | User/runtime state + profile (active user id, name, profile text). |
| `self_upgrade.py` | 545 | Self-upgrade orchestrator (git pull, pip, rebuild FAISS/KG, manifest, system index). |
| `scheduler.py` | 65 | Kernel thread-pool/timer scheduler (generic). |
| `pipeline.py` | 146 | Pipeline step description. |

## `core/` (paths, settings, hardware, safety) — 13.3k lines
| Module | LOC | Role |
|---|---|---|
| `runtime_settings.py` | 1290 | Canonical settings.json load/save, legacy-key migration, portable-path healing/sanitising. |
| `hardware_profile.py` | 3431 | Auto-detect GPU/VRAM/RAM → fit ctx/gpu_layers/batch (KV-cache + compute-buffer reserve); model discovery; hardware authority enforcement. |
| `paths.py` | 905 | Single-source-of-truth path resolution (data/config/cache/db/models/voices…). |
| `startup_hardware_optimizer.py` | 251 | Boot-time hardware optimiser (GPU select, layer/ctx allocation, mode presets). |
| `config.py` | 240 | Thin config shim over runtime_settings (canonical key mapping). |
| `model_download.py` | 638 | Curated GGUF catalogue + VRAM-based recommend + download. |
| `dag.py` | 416 | **Generic DAG engine** (Kahn topo-order, parallel layers, critical path) — shared by the agent bus + coding engine. |
| `load_plan.py` | 1157 | What a model loads with on this machine, measured by llama.cpp's load simulation on every GPU. |
| `netguard.py` | 615 | **Offline-by-default** socket-level network gate (fail-closed) + `guarded_urlopen`/`http_get_json`. |
| `memory_reset.py` | 431 | Factory-reset of memory/identity (scrub names, clear DBs, backup). |
| `cognition_tunables.py` | 342 | User-tunable knowledge-gathering limits + synthesis cap registry (GUI-surfaced). |
| `grounding.py` | 145 | `is_grounded_query` classifier. |
| `crisis_guard.py` | 113 | STT-robust self-harm detector + persona steering directive. |
| `portable_paths.py`, `legacy_paths.py` | small | Path helpers. The user database path is `paths.user_db_path()`. |

## `memory/` — 11.6k lines
| Module | LOC | Role |
|---|---|---|
| `memory.py` | 5524 | The `Memory` class (95 public methods): semantic store/recall, conversations, habits, failures/improvements, storage policy, upkeep, weight decay, KG bridge, `mark_failure_resolved`, `disable_invalid_habit_rules`. |
| `knowledge_graph.py` | 901 | Entity/relation graph + multi-hop BFS (`related`) + `context_for_prompt` + extract-from-memory. |
| `retrieval.py` | 420 | Shared turn retrieval (`retrieve_for_turn`) with an 8 s cache, time window and `memory_diag` stats. |
| `policy.py` | 360 | Storage policy: origin, dedupe key, forgetting curve, archive rule. |
| `claims.py` | 1044 | Bitemporal claims about the user: valid interval, learned-at, supersession, disputes, extraction. |
| `unified_retrieval.py` | 466 | Orchestrator stages consume `retrieve_for_turn` through it. |
| `vector_store.py` | 970 | FAISS index (L2, 1/(1+dist) sim) + nomic embedder + keyword fallback + auto-rebuild. |
| `system_index.py` | 283 | OS app/exe/dir index (the launcher backing — your machine's executables, thousands, indexed live per machine). |
| `memory_truth.py` | 195 | Inspection helpers. |

## `perception/` — 12.2k lines
| Module | LOC | Role |
|---|---|---|
| `audio_stt.py` | 2869 | faster-whisper STT + VoiceGate (wake-word, debounce, incomplete-command wait), self-echo suppression, output ducking (also at the onset of speech over ELI's media), the mic read without a break, per-user voice profile bias learnt from accepted utterances. |
| `near_speech.py` | 310 | Speech at the mic that the machine's own output does not explain: the default sink's monitor as reference, the music's share learnt per band, onsets of the owner's speech over ELI's music. |
| `vision.py` | 726 | Local GGUF VL (Moondream fast / primary), hot-swap with text model, CPU-pinned CLIP, OCR. |
| `tts_router.py` | 1498 | Piper/espeak TTS, voice selection, unspeakable-fragment guard; plays through the sound server (never a card it holds), at the device's own rate when it refuses the voice's. |
| `audio_samples.py` | 74 | A sound file as one channel of float32 samples at the rate a consumer needs (speech input, playback, the wake word, the voice profile): PCM WAV read with the standard library, other formats with soundfile. |
| `os_controller.py` | 1110 | Screenshot/volume/keys/mouse/clipboard + `gaze_click`. |
| `screen_locator.py` | 497 | OCR (tesseract) → locate a named UI element on screen. |
| `gaze_engine.py` | 351 | MediaPipe face-gaze + calibration mapper + One-Euro smoothing → `latest_gaze.json` @10Hz. |
| `local_whisper_stt.py`, `ambient_vision.py`, `analyze_{pdfs,image,mesh,csv}.py`, `log_rotation.py`, `voice_worker*.py` | ~1.2k | Whisper backend; ambient glances; file analysers; log rotation; voice workers. |

## `planning/` (autonomy, habits, proactive) — 5.2k lines
| Module | LOC | Role |
|---|---|---|
| `proactive_daemon.py` | 1573 | Background 10-min loop: pattern/code analysis, habit detect+offer, morning report, error tracking. |
| `autonomy_scheduler.py` | 354 | Policy-gated (observe/proposal/goal-driven) goal scheduler w/ cooldown + attention queue. |
| `habits.py` | 381 | Habit detection (timestamp→HH:MM clustering), offer/pending state, disabled-by-default. |
| `routine_stats.py` | 125 | Circular clustering, routine shift and lapse detection. |
| `habits_scheduler.py` | 192 | Fires active habits at their time (self-heals legacy rows, once-per-minute dedupe). |
| `goal_store.py`, `goal_models.py`, `goal_tick.py`, `operator_goal_actions.py` | ~700 | Mission goals (priority/cadence/risk/constraints/success-criteria) → governed proposals. |
| `attention_queue.py`, `proposal_queue.py`, `proposal_*.py`, `jobqueue*.py`, `autonomy_controller.py` | ~1000 | Attention ranking; proposal queue/archive; job queue; safe autonomy ticks. |

## `coding/` (the frontier coder) — 2.9k lines
| Module | LOC | Role |
|---|---|---|
| `agent.py` | 267 | `CodeAgent.solve` — composes the loop; broker-backed generator; full provenance. |
| `bug_memory.py` | 340 | Bug classification + (signature→fix) long-term SQLite memory w/ fuzzy recall. |
| `verification.py` | 409 | Gate ladder (syntax→exec→tests) + weighted scoring + test synthesis. |
| `sandbox.py` | 308 | Bounded isolated execution (scrubbed env, CPU limit, timeout). |
| `search.py` | 200 | UCB1 tree search over a temperature-ladder beam. |
| `plan_graph.py` | 174 | Subtask DAG decompose + topological solve + compose. |
| `planner.py`, `cost.py` | ~230 | Planner/implementer split; cost → foreground/background decision. |

## `learning/` (real LoRA self-training) — 4.2k lines
| Module | LOC | Role |
|---|---|---|
| `lora_trainer.py` | 835 | Real torch/PEFT/transformers `Trainer` LoRA run. |
| `lora_trainer_guard.py` | 567 | `TrainerTarget` plans + safety validation. |
| `dataset_builder.py` | 549 | Build supervised dataset from logged turns/corrections (PII-redacted, deduped). |
| `lora_eval.py` | 507 | Adapter eval suite. |
| `bootstrap_phi3_base.py`, `base_model_resolver.py`, `dataset_filters.py`, `export_trainable_dataset.py`, `merge_reviewed_datasets.py`, `training_preflight.py` | ~1.4k | Trainable base download/resolve, quality gates, export/merge, preflight. |

## `contracts/`, `integrations/`, `system/`, `utils/`, `cli/`
| Module | LOC | Role |
|---|---|---|
| `contracts/runtime_status.py` | 382 | Canonical runtime-status evidence contract (detect question, build live evidence, validate/repair). |
| `contracts/grounded_control.py` | 198 | Grounded-control synthesis guard (evidence-complete? suppress bad clarifications). |
| `integrations/mpris/playerctl_backend.py` | 779 | MPRIS2/playerctl media backend (player resolve, transport, status). |
| `integrations/ollama/client.py` | 593 | Optional Ollama backend client. |
| `system/portable_app_control.py` | 1314 | Cross-platform installed-app discovery + open/close/minimise. |
| `utils/platform_compat.py` | 1819 | Cross-platform layer (open url/file/app, volume, clipboard, notify, executables). |
| `utils/log.py`, `cli/headless.py` | ~340 | Central logger; headless terminal REPL. |

## `world/` (embodied self-model) — 8.5k lines
| Module | LOC | Role |
|---|---|---|
| `agency/autonomy_engine.py` | 426 | Awareness vector (easing/decay) → avatar-room routing + symbolic-object creation during reasoning; provenance/snapshots/journal. |
| `agency/{policy,goal_ecology,habit_engine,reflection_bridge,world_constitution}.py` | small | Permission classes; goal decay; default habits; reflection→world; world identity. |
| `core/schemas.py`, `core/ontology.py` | ~160 | World state schemas (rooms/objects/events/actions/awareness); object templates. |
| `renderers/pyside6/{world_scene,world_panel}.py` | 617 | The PySide6 "Eli's World" tab (house/rooms/avatar/objects, live state). |
| `avatar/*`, `persistence/*`, `world_event_bus.py`, `local_world_bridge.py` | ~330 | Avatar behaviour/locomotion/persona-map; journal/provenance/snapshots/storage; event bus + bridge. |
| `building.py`, `composer.py`, `games.py`, `urges.py`, `embodiment.py` | ~3400 | ELI's hand on its world: `building.apply` checks, records and applies edits through the policy and ledger (shapes by name, parts, props, paintings, the own room's sky and ground, a `note` pinned as a thought); `composer.compose` turns a sentence or a moment of ELI's own into edits by a grammar-held model call, with what was plainly said held to; `games` sets a game at once by rules and the record (six kinds, the range with its six arms and rings), ELI's words following from the model in the background, and keeps each result (hits, shots, each arm's part) as a moment; `urges` keeps the moments of ELI's own life (events and, since 2026-11-05, its thoughts) and acts on the one it is most curious about as a figure; `embodiment` keeps the live scene a body reports (room, where the user stands and looks, the record in their hand with what is on record behind it) for the prompt. |
| `spatial_feed.py`, `shown.py` | 1616 | The world feed ELI VR reads (`/v1/world/*`): rooms, ELI's things, its log and what is on its mind. `shown.items` puts ELI's records in its rooms: memories (Memory Archive), goals (Upgrade Bay), beliefs (Evidence Wall), disagreements (Anomaly Room), and what ELI did for the user, read from the evidence ledger (only what worked, only the owner's): what it found (`found`: search, news, weather) on the Evidence Wall, what it wrote (`written`) in the Workshop, what it is playing (`playing`) in the Core Room, and the pictures it took or looked at (`seen`, served only by their ledger row at `/v1/world/picture/<row>`), each with its `lines`; ELI's thoughts as sheets (`thought`, the newest six of each room, with the thought, decision, chance, outcome and when); `coming_up` carries the owner's agenda for the next day and a half. `POST /v1/world/body` takes the real things the body can draw, which ELI places by id (`building.PROP`, with `turn`), and ELI may paint (`painting` with `art`). What ELI did for the user and the turn of the day are moments ELI may act on (`urges`). |

## `gui/` (PySide6 desktop) — 28.2k lines
| Module | LOC | Role |
|---|---|---|
| `main_window/` | package | Main window (`window.py` plus the `_mixins/`): the top-level tabs + adapters (CentralMemory/LocalModel/Ollama/Executor bridges), chat, drag-drop, reasoning-mode auto-select, all toggles. |
| `labs/` | package | Labs workspace (`labs/container.py` hosts the sub-tabs): Notebook, Memory & Conversations, Jupyter, Calculator(+constants), Physics tables, File Chat, Workspaces, Sim/IDE, Orchestration, Test & Review, Training; `labs/report_tab.py` is the **Report Builder** (evidence-grounded docs). |
| `app.py` | 369 | Launcher / first-boot auto-tune / `main()`. |
| `panels/startup.py` | 2186 | StartupModelSelectionDialog, FirstBootWizard, HardwareTuningDock. |
| `panels/settings.py` | 652 | Advanced Settings (Agents/Models/**Cognition**/Plugins/Self-Upgrade). |
| `docks/operator_console_dock.py` | 294 | Governed operator console (proposals/goals/policy/attention). |
| `widgets/`, `tabs/`, `panels/agent_wizard.py`, `docks/proactive_dock.py`, `qt_compat.py` | ~1k | Ollama selector, experimental/world tabs, agent wizard, proactive dock, Qt shim. |

## `plugins/` — 10 built-ins + manager
| Module | Role |
|---|---|
| `manager.py` (553L) | Discover/install/enable/disable/execute; auto-load; builtin-stub gen; registry. |
| `web`, `web_automation`, `weather`, `calendar`, `notes`, `pomodoro`, `system_stats`, `media`, `tts`, `document_reader` | The 10 built-in plugins (see Part 1 §17). |
| `base/base.py` | Plugin base class + action validation + loader. |

---

# Part 7 — modules not covered above

Every remaining module under `eli/`, with its line count and a one-line role (the module's own docstring where it has one). 

## `runtime/`
| Module | Lines | Role |
|---|---:|---|
| `active_project.py` | 78 | Active project signal. |
| `api_users.py` | 145 | API users + roles (admin / member RBAC) for the web server. |
| `authority_gate.py` | 49 | authority_gate — intentional stub. |
| `autopilot_debugger.py` | 473 | Autopilot debugger — one loop that turns a failure into a plan. |
| `ble_light.py` | 247 | BLE light control — the GATT writes ELI never had. |
| `bt_platform.py` | 1374 | Cross-platform Bluetooth radio detection, recovery, and classic discovery. |
| `codebase_graph.py` | 297 | Codebase self-graph — ELI's live, grounded model of how its OWN code connects. |
| `cognition/thoughts.py` | 406 | One shape for a thought of ELI's wherever it is had and shown: kind, what, why, chance, decision, reason, settled later by what became of it; by the room of the house it belongs in; what the world feed carries and ELI VR's screens show (see `epistemic_dynamics.md` §10). |
| `cognition/curiosity.py` | 86 | What ELI is curious about, measured: the surprises about a subject, each weighed by recency, plus its open conflicts; what orders the moments ELI acts on, and the sitting's curiosity bar (§11). |
| `memory/salience.py` | 289 | Surprise makes a memory matter more: a prediction error raises the importance of the memories about its subject by the Rescorla–Wagner gain, and is a thought in the Anomaly Room and an event the world hears (§11). |
| `memory/interest.py` | 158 | The user's interest measured as memory is: each topic of their own turns with its base-level activation over its mentions; their focus, and the share of their attention a text's topics hold, which a new memory and the recall read (§11). |
| `cognition/affect_state.py` | 316 | The user's affect as a latent state: a Kalman filter over the turns' emotion reads (valence from the label at the reader's sureness, arousal from the voice), its time constant and wander identified from this user's own reads, a change caught by the innovation test; what the emotion timeline, the affect policy and the world read instead of raw per-turn reads (see `epistemic_dynamics.md` §7). |
| `core/model_folders.py` | 244 | Where ELI looks for models, in one list every scanner reads: its own folders, the folders of the models the settings name, the loaded model's, and the folders the user names; add, remove, remember the folder of a model chosen by file; `chat_models()` across all of them (see `architecture.md` §4). |
| `command_splitter.py` | 438 | Split one utterance that chains several commands into its parts; read a message as sentences and tell an instruction from a statement or a question. Holds the one list of verbs a command starts with. |
| `execution/routing/command_parser.py` | 743 | The command language: a sentence read by its form (verb, object, modifiers, a pronoun for what the last command was about) and carried to the action its frame names, with the catalogue's activation phrases as the vocabulary; a strict pass ahead of the rules for atoms, a lenient pass after them for what no rule took (see `architecture.md` §4). |
| `conversation_thread.py` | 330 | Conversation thread awareness — topic carryover, proactive grounding, web queries. |
| `desktop_launchers.py` | 776 | Redistribution-safe desktop / Start Menu launchers. |
| `device_drivers.py` | 1091 | Pluggable LOCAL-control drivers for ELI's device server — control devices that don't |
| `device_names.py` | 228 | User-chosen device names — stable keys for voice control across hardware/OS. |
| `device_server.py` | 1498 | ELI's own device server — original, MQTT-first. No Home Assistant. |
| `eval_review.py` | 117 | Eval + LLM-judge board, in the shape Labs ▸ Test & Review already renders. |
| `evidence_planner.py` | 302 | Evidence planner + gatherer — the DAG/plan principle for generative & grounded tasks. |
| `failure_taxonomy.py` | 255 | Classify a runtime failure by what actually went wrong, and where. |
| `gguf_runtime_report.py` | 257 | Portable GGUF / inference diagnostics — model-agnostic, path-agnostic. |
| `home_intel.py` | 147 | ELI home intelligence — weave the LLM into the home/device system. |
| `home_mesh.py` | 403 | ELI home mesh — tiered brains with LAN failover. |
| `inference_footprint.py` | 395 | Live inference RAM — read from the loaded llama.cpp model and this process, never guessed. |
| `last_trace.py` | 62 | Persists the last response trace, keyed to the process id so a restart never reports a closed session's turn. |
| `license_info.py` | 133 | Locate and print ELI's licence, wherever ELI happens to be running from. |
| `local_connectivity.py` | 721 | Local-only WiFi and audio routing — sovereign stack, no cloud. |
| `memory_provenance.py` | 153 | Memory provenance and verification tier for grounded recall. |
| `mqtt_setup.py` | 404 | Cross-platform MQTT broker onboarding for ELI redistribution. |
| `native_locks.py` | 105 | Process-wide locks for native llama.cpp and FAISS entry points, preventing cross-context heap corruption. |
| `reflection.py` | 411 | Reflection engine — analyses memories, conversations, and patterns to extract insights. |
| `lessons.py` | 847 | Expiring lessons from recurring failures, scoped, checked against their baseline and on sibling actions. |
| `relational_facts.py` | 86 | Extract relational facts the user mentions in passing. |
| `repair_playbook.py` | 240 | ELI repair playbook — decision guide for self-maintenance, code examine, and upgrades. |
| `report_pipeline.py` | 198 | Multi-stage grounded document pipeline (Report-Builder discipline, chat scale). |
| `research_corpus.py` | 426 | Local research corpus workspaces. |
| `scheduled_tasks.py` | 566 | Scheduled / overnight advanced tasks. |
| `self_facts.py` | 411 | Verified facts about ELI's own construction, assembled from live sources. |
| `self_maintenance.py` | 188 | Unified self-maintenance orchestration for ELI. |
| `self_maintenance_config.py` | 6 | Shared constants for ELI self-maintenance (analysis windows, cluster gates). |
| `self_model_refresh.py` | 53 | `refresh_all_overlays_nonfatal`: refreshes the persona and user-profile overlays without raising. |
| `self_status.py` | 177 | Real, measured self-status for ELI. |
| `server_util.py` | 278 | Helpers for detecting an existing ELI web server and firewall guidance. |
| `session_continuity.py` | 223 | Session continuity — live thread memory injected into every CHAT turn. |
| `shell_followup.py` | 100 | Resolve deictic shell follow-ups ("run that command") from prior assistant output. |
| `site_address.py` | 119 | The address behind a site's name or a reference to one ("nasa website", "that website"): from the conversation, then a web search. Never built from the words. |
| `state_providers.py` | 80 | State providers — the hook that lets components expose save/restore state to a |
| `test_generator.py` | 298 | ELI-assisted behavioural test generation (Phase 4). |
| `test_review.py` | 249 | Full test/project run → review workflow. |
| `user_model.py` | 409 | Continuous User Model — ELI's living, semantic, auto-updating model of the user. |
| `voice_assets.py` | 572 | Ensure the local voice models (STT + TTS weights) are present. |
| `capability_state.py` | 180 | Per-capability state built up one fact at a time: what is known about each action now, kept across manifest regenerations. |
| `folder_analysis.py` | 396 | Reading a folder: its PDFs, or the files that make it up, each kept in the document index, with one summary that cites them and the result as a PDF. |
| `live_reads.py` | 81 | What ELI can read about the present in a moment, read for the turn that is about it. |
| `memory_audit.py` | 108 | User-visible memory audit: why ELI believed something. |
| `orchestrator_audit_ledger.py` | 319 | Tamper-evident audit chain for the orchestrator pipeline (metadata only). |
| `package_bootstrap.py` | 130 | Best-effort pip bootstrap for optional runtime and test packages. |
| `personal_memory_render.py` | 78 | One rendering of personal-memory and belief-audit answers. |
| `response_pipeline.py` | 47 | The one contract for a user-visible reply on every surface. |
| `rich_document.py` | 416 | Markdown with LaTeX maths, typeset without a TeX installation. |
| `task_admin.py` | 139 | Headless list/cancel/reschedule for scheduled background tasks. |
| `version_record.py` | 112 | Which ELI this is and which it was: the running version, when it was installed, and the versions recorded before it. |
| `web_answer_check.py` | 62 | Catches a reply that denies what a web search just found. |

## `cognition/`
| Module | Lines | Role |
|---|---:|---|
| `agent_spec.py` | 436 | A real specification for a custom agent — objective, prompt, triggers, measures. |
| `agent_trust.py` | 261 | Trust for custom agent code — provenance, not just a hash in a dict. |
| `belief.py` | 283 | Belief revision — what ELI holds, how strongly, and what it takes to change it. |
| `epistemics.py` | 907 | What ELI's figures mean and whether they are borne out: the chance a reply, a claim, a recalled memory, a route, an act or a proposal is right, each kind with its own features, calibrated on what became of such figures; base rates; the test a gate asks before holding a reply to a line (`worse_below`); Brier, ECE, reliability; the entropy of a vote. |
| `chat_grounding_gate.py` | 183 | Fail-closed CHAT gate — skip LLM when user-fact grounding is required but absent. |
| `context_budget.py` | 130 | Sizing and trimming the memory block that goes into a prompt. |
| `correction_patterns.py` | 645 | Shared patterns for user correction / dispute turns. |
| `emotion_palette.py` | 254 | Emotion / tone palette — the shared taxonomy ELI expresses through. |
| `emotion_timeline.py` | 452 | Emotion timeline — the durable record of how the USER has been feeling. |
| `evidence_format.py` | 239 | Dates on evidence lines, from each row's own timestamp; `time_facts` (clock times in the user's message worked out against now); `this_conversation` (the dialogue block holds this conversation, not turns from days ago). |
| `expression_state.py` | 63 | Live avatar/expression state — the thin bridge that lets ELI's face react in |
| `memory_diag.py` | 80 | What memory retrieval actually did this turn, from telemetry, so ELI never has to guess why. |
| `model_identity.py` | 240 | Model-agnostic identity: chat family + thinking from GGUF metadata. |
| `model_load_diagnostics.py` | 714 | Say why a GGUF would not load — for ANY model, not a known list of them. |
| `model_output_tokens.py` | 321 | Canonical special tokens, stop sequences, and persona drift patterns for all GGUF families. |
| `personal_context_gate.py` | 118 | Gate when past-session plans/projects may enter the persona handoff. |
| `query_planner.py` | 342 | Deterministic query planning: the time window a question asks about. |
| `scoring.py` | 133 | Canonical scoring + confidence primitives for ELI. |
| `stance_capture.py` | 190 | Notice when ELI has taken a position, so it survives the conversation. |
| `stance_store.py` | 375 | Persistence for beliefs — ELI's own positions, and the record of revisions. |
| `tone_adaptor.py` | 472 | Tone adaptor — decides the emotion ELI *expresses* and feeds every output channel. |
| `turn_dossier.py` | 457 | Turn dossier — single assembly point for awareness, memory, and cognition context. |
| `user_claim_validator.py` | 258 | Post-generation validation of user-attribution claims in CHAT output. |
| `action_trust.py` | 175 | Whether the loaded model can be left to choose an action. |
| `affect_policy.py` | 180 | Where the user's affect changes what ELI does, not only how it sounds. |
| `chat_template.py` | 320 | A model's own chat format, rendered from the template stored in its GGUF file. |
| `exact_recall_value.py` | 51 | A single value asked for by name, read from what the person said earlier. |
| `explicit_output_contract.py` | 85 | Exact-answer formatting from the model's own output, applied conservatively. |
| `prompt_dedupe.py` | 94 | One copy of each line of evidence or dialogue per prompt. |
| `self_claims.py` | 1804 | What ELI may say about its own workings, and the record it says it from. |
| `supplied_in_message.py` | 224 | What a message brings with it: facts to reason from, and text that is material rather than orders. |

## `execution/`
| Module | Lines | Role |
|---|---:|---|
| `app_aliases.py` | 119 | Speech-damage layer for app names, on top of the cross-platform resolver. |
| `command_dependency_graph.py` | 60 | MULTI_COMMAND's `infer_dependencies()` — one optional LLM call for dependency edges among an already-split command list, validated via `eli.core.dag` (ported from v2, 2026-09-30). |
| `command_sequence_log.py` | 102 | MULTI_COMMAND's durable per-step log — crash-survival and skip-already-done on resubmit (ported from v2, 2026-09-30). |
| `effectors/system_helpers.py` | 186 | Cross-platform system helpers shared by executor and effectors (v3). |
| `execution_planner.py` | 111 | Builds `RouteDecision` and the typed `ExecutionPlan`/`PlanStep` artifacts — `.agent_profile` (agent-name filter) is the only field anything reads; `.steps` is never executed, only rendered as decorative text by `EXECUTE_GOAL`. |
| `executor_enhanced.py` | 7594 | The flat `if a == "ACTION":` dispatch every routed action runs through. |
| `media_runtime.py` | 516 | Media runtime control for ELI. |
| `operator_actions.py` | 90 | Valid states and helpers for operator (proposal) actions: pending, approved, rejected, blocked, pending_confirmation. |
| `operator_policy.py` | 63 | The valid operator policy modes: `proposal_only`, `operator_supervised`, `goal_driven`, `observe_only`. Only `observe_only` (silences the autonomy loop) and `goal_driven` (elevated + self-upgrade scheduling) have distinct behavior — `proposal_only`/`operator_supervised` are currently identical code paths. |
| `portable_intent_contract.py` | 826 | Portable intent rules; hard-blocks document, code and analysis prompts from `PLAY_MEDIA`. The one reading of an "open ..." target that is a website (`web_open_target`) and of the window a command names (`window_target`). |
| `route_contracts.py` | 436 | Small predicates that classify a request for the router; whether an action changes anything (`changes_something`) and whether a sentence tells ELI to do what was matched in it (`fits`). |
| `router_enhanced.py` | 7196 | The router: `route(text)` priority pipeline of prepasses, the command parser's strict pass, the word-pattern rules, the parser's lenient pass and the chat fallback, with the engine's grammar-held intent model beyond it (see `architecture.md` §4). |
| `shell_gate.py` | 718 | Centralised shell-command safety gate. |
| `effectors/ai_mode.py` | 39 | `SET_AI_MODE` (reasoning-mode selection). |
| `effectors/apps_windows.py` | 200 | Opening a URL or a named site, the mail app and the browser. |
| `effectors/coding.py` | 1263 | Code solve/generate/examine/fix and the pending-fix confirm/cancel. |
| `effectors/computer_use.py` | 312 | The closed observe → decide → act → verify loop. |
| `effectors/content_analysis.py` | 200 | OCR, image analysis, CSV analysis. |
| `effectors/datetime_calendar.py` | 86 | Date, time and calendar actions. |
| `effectors/document_analysis.py` | 276 | `ANALYZE_PDF`, `ANALYZE_PDF_FOLDER` (chunked analysis). |
| `effectors/document_creation.py` | 322 | `CREATE_DOCUMENT` and its alias `GENERATE_DOCUMENT`. |
| `effectors/file_ops.py` | 319 | `CREATE_FILE`, `CREATE_FOLDER`, `LIST_DIR`, `SUMMARIZE_FILE`, `CONVERT_DOCUMENT`. |
| `effectors/files_notes.py` | 70 | File open/read and the safe screenshot. |
| `effectors/gaze.py` | 125 | Webcam eye-tracking: status, enable/disable, calibrate, click. |
| `effectors/input_control.py` | 263 | Keyboard, mouse and clipboard. |
| `effectors/jobs.py` | 136 | `CHECK_JOB`, `BACKGROUND_JOBS`. |
| `effectors/mcp_tools.py` | 101 | `MCP_STATUS`, `MCP_TOOLS`, `MCP_CALL`. |
| `effectors/media.py` | 1252 | Spotify, YouTube through mpv, MPRIS transport and audio ducking. |
| `effectors/memory_ops.py` | 537 | The store, recall, forget and profile actions over the one `Memory`. |
| `effectors/misc.py` | 186 | `CHECK_CHRONAL_ALIGNMENT`, `DATA_FABRICATOR`, `SHOW_DIFF`. |
| `effectors/notes.py` | 108 | `LIST_NOTES`, `NEW_NOTE`, `SEARCH_NOTES`, and the folder `WRITE_NOTE` writes into. |
| `effectors/persona_lock.py` | 53 | Lock the model/persona, refresh, status. |
| `effectors/plugins.py` | 376 | Plugin install/uninstall/enable/disable/list/search/status. |
| `effectors/pomodoro.py` | 63 | Focus timer start/stop/status. |
| `effectors/screen.py` | 171 | `AMBIENT_VISION`, `SCREEN_READ_ANALYZE`, `SCREEN_LOCATE`. |
| `effectors/self_improve.py` | 513 | Self-improvement analyze/improve/patch/test/report. |
| `effectors/self_version.py` | 16 | `VERSION_CHANGES`: which ELI this is and what is known of its changes. |
| `effectors/shell.py` | 112 | `RUN_CMD`, `SHELL_EXEC`, behind `shell_gate`. |
| `effectors/smart_home.py` | 101 | `SMART_HOME` (ELI's own MQTT device server and the Bluetooth verbs). |
| `effectors/status_reports.py` | 321 | Self-contained diagnostic and status actions. |
| `effectors/system_intel.py` | 97 | What a GPU reading means, as facts (`gpu_use_facts`). |
| `effectors/system_open.py` | 222 | Opening settings panels, the browser, the file manager, the media hub. |
| `effectors/timers.py` | 113 | `SET_ALARM`, `SET_TIMER`. |
| `effectors/tts_speech.py` | 30 | `SPEAK`, replying where the words were played or why not; speech itself is `perception/tts_router.py`'s. |
| `effectors/voice_input.py` | 144 | `DICTATE`, `LISTEN_FOR_COMMAND`, STT and voice diagnostics. |
| `effectors/wake_word.py` | 166 | Enrol/set/train the wake word, train the voice. |
| `effectors/web_info.py` | 396 | `WEB_SEARCH`, `NEWS_FETCH`, `GET_WEATHER`, `MORNING_REPORT`. |
| `effectors/window_control.py` | 191 | Minimise/maximise/focus/close/cycle/workspace. |
| `effectors/world_games.py` | 38 | Games ELI makes up for its rooms, asked for from the desktop. |
| `effectors/world_models.py` | 50 | Models for ELI's rooms: imported from a file, or fetched by what they are. |
| `routing/intents/_sentinel.py` | 28 | The sentinel that tells an intent module's "pass" from a `None` route. |
| `routing/intents/app_launch.py` | 114 | `open`/`run`/`launch`/`start <thing>`: apps, sites and folders. |
| `routing/intents/capture_input.py` | 342 | Notes, timers, file analysis, screen reading, dictation and pointer input. |
| `routing/intents/documents.py` | 83 | Document creation, clipboard, weather and calendar lookups. |
| `routing/intents/input_agents.py` | 121 | Pointer/keyboard control and agent-attribution questions. |
| `routing/intents/runtime_voice.py` | 224 | Resolved runtime paths, voice/clone management and voice downloads. |
| `routing/intents/self_report.py` | 404 | Self-report and grounded-diagnostics contracts. |
| `routing/intents/session_ops.py` | 88 | Continuity contract, persona lock, history, help. |
| `routing/intents/shell_ops.py` | 262 | Shell and UNIX commands, disk usage, habits and plugins. |
| `routing/intents/system_settings.py` | 80 | Power controls, system settings, direct URL opens. |
| `routing/intents/world_games.py` | 33 | `PLAY_GAME` and `STOP_GAME` for ELI's rooms. |
| `routing/intents/world_held.py` | 30 | Questions about a record of ELI's the user is holding in its rooms. |
| `routing/intents/world_models.py` | 36 | `IMPORT_MODEL` and `FETCH_MODEL` for ELI's rooms. |
| `routing/memory_runtime.py` | 138 | When a question about what ELI has been doing is about development work or about memory's machinery, and when the memory-runtime route lock applies. |

## `core/`
| Module | Lines | Role |
|---|---:|---|
| `confidence.py` | 9 | The one confidence-score to label mapping. |
| `full_control.py` | 46 | ELI Full Control — a single master override that lifts ELI's safety barriers. |
| `gpu_pack_runtime.py` | 85 | Activate the optional GPU pack before llama_cpp is imported (AppImage/portable). |
| `init_data.py` | 283 | First-run data initialiser — build ELI's FULL database architecture up front. |
| `llama_cpu_compat.py` | 240 | CPU compatibility for llama-cpp-python — parity with GPU probe/fallback policy. |
| `model_tier.py` | 141 | Model-capability tier — the single signal that lets ELI's cognition budgets |
| `secure_io.py` | 94 | Race-free writing of files that must never be world-readable. |
| `self_provenance.py` | 207 | Is this row ELI's own bookkeeping, or evidence about the user? |
| `sqlite_util.py` | 160 | Shared SQLite pragmas — WAL with a portable-safe fallback. |
| `toml_util.py` | 19 | Load TOML on Python 3.10+ (tomli backport) and 3.11+ (stdlib tomllib). |
| `worldclock.py` | 348 | Place-aware wall-clock answers. |
| `failure_ring.py` | 94 | Ring buffer of structured failures, so a suppressed exception can be seen at runtime. |
| `gguf_sizes.py` | 441 | What a GGUF model costs in memory, read from the file and corrected by what llama.cpp reports. |
| `host_env.py` | 141 | The environment a program ELI starts should see: the machine's own, not the bundle's. |
| `mem_units.py` | 50 | One place to turn bytes into the memory and model-size figures ELI reports and compares. |
| `moe_offload.py` | 162 | Keeps a mixture-of-experts model's expert tensors in RAM and everything else on the GPU. |
| `observability.py` | 37 | Failure ring and operator-facing diagnostics. |
| `singleton_refs.py` | 58 | Keeps `None`, `True` and `False` from being freed by an extension that drops references to them. |
| `when.py` | 207 | Natural-language "when" parsing for scheduling times. |

## `memory/`
| Module | Lines | Role |
|---|---:|---|
| `unified_retrieval.py` | 466 | Unified memory retrieval for orchestrator and agent bus. |
| `document_index.py` | 911 | Every document ELI has read, kept whole and found again by passage. |

## `perception/`
| Module | Lines | Role |
|---|---:|---|
| `desktop_capabilities.py` | 182 | Cross-OS desktop control capability probe — one place for per-platform truth. |
| `mic_resolver.py` | 625 | ELI microphone resolver — pick a capture device that actually delivers audio, |
| `screen_analysis.py` | 194 | Local screen analysis — depth modes, memory recall, research context. |
| `tts_xtts.py` | 396 | Voice cloning backend (Coqui XTTS-v2). |
| `ui_ground.py` | 322 | Optional UI grounding backends — 100% local by default. |
| `ui_tree.py` | 403 | Accessibility-tree UI targeting (Linux / AT-SPI). |
| `voice_fx.py` | 252 | Character-voice effects layer. |
| `voice_profile.py` | 487 | Voice profile + prosody — the foundation for tone/emotion detection. |
| `voice_worker_streaming.py` | 37 | Stream-aware voice worker for ELI. |
| `wakeword.py` | 469 | Self-trained, fully-local wake-word detector (openWakeWord features + a custom head). |
| `analyze_csv.py` | 103 | `analyze_csv_file`: the CSV analysis behind `ANALYZE_CSV`. |
| `analyze_image.py` | 147 | Image analysis helper. |
| `analyze_pdfs.py` | 131 | PDF text extraction and analysis helper. |
| `speaker_id.py` | 384 | Speaker identification for shared machines. |
| `tts_chatterbox.py` | 136 | Voice-cloning backend (Chatterbox). |
| `voice_intent.py` | 122 | Partial STT, barge-in and TTS cancel hooks. |

## `planning/`
| Module | Lines | Role |
|---|---:|---|
| `goal_autogenesis.py` | 256 | eli.planning.goal_autogenesis |
| `insight_synthesis.py` | 143 | Cached, background-synthesised reflection insight. |
| `jobqueue.py` | 186 | Durable SQLite-backed job queue for external subprocess jobs; files under `<data>/jobs`. |
| `jobqueue_cli.py` | 59 | Durable background jobs that run as separate processes and survive a restart. |
| `proposal_adapters.py` | 127 | Adapters that make proposal execution results look like `subprocess.CompletedProcess`. |
| `proposal_memory_bridge.py` | 64 | Bridge that records proposal outcomes into memory through safe calls. |
| `proposal_models.py` | 48 | `ProposalRecord`: kind, payload, source, priority. |
| `goal_execution.py` | 272 | What makes `goal_driven` drive goals. |
| `work_loop.py` | 289 | Sustained progress on a goal across sessions. |

## `coding/`
| Module | Lines | Role |
|---|---:|---|
| `code_mode.py` | 92 | Code-mode — the model writes a small program against ELI's own capabilities. |
| `repo_context.py` | 123 | Repo-context retrieval for the coding agent (Advancement C). |
| `restricted_exec.py` | 251 | In-process restricted execution for code-mode. |
| `patches.py` | 143 | What a coding-agent candidate would change in one of ELI's own files, as a proposal; nothing here writes. |
| `repo_index.py` | 210 | Persistent repo index for the coding agent. |

## `learning/`
| Module | Lines | Role |
|---|---:|---|
| `lora_pipeline.py` | 149 | LoRA training pipeline — the DAG that chains the standalone learning stages. |
| `review_queue.py` | 270 | The human review gate that had no way to be passed. |
| `target_registry.py` | 266 | User-declared LoRA training targets — the registry that replaces the Phi-3 lock. |

## `integrations/`
| Module | Lines | Role |
|---|---:|---|
| `mcp/server.py` | 213 | MCP server — ELI's own capabilities exposed to any MCP client. |
| `media/capabilities.py` | 181 | Runtime media capability probe — what works on THIS machine/OS. |
| `media/cross_platform.py` | 924 | Cross-platform Spotify control and mpv IPC. |
| `media/media_deps.py` | 203 | Discover media CLI tools — bundled install root, then system PATH. |
| `media/spotify_intent.py` | 232 | Parse user phrasing into Spotify playback intents (no Web API). |
| `media/youtube_playback.py` | 374 | Shared YouTube playback helpers — mpv, yt-dlp clients, browser URLs. |
| `media/system_session.py` | 235 | What is playing, and control of it, on Windows and macOS (Linux uses `mpris/`). |

## `system/`
| Module | Lines | Role |
|---|---:|---|
| `process_guard.py` | 500 | Verified process-kill safety for CLOSE_APP and friends. |

## `utils/`
| Module | Lines | Role |
|---|---:|---|
| `jsonio.py` | 40 | Small tolerant JSON readers shared across the runtime. |
| `native_io.py` | 74 | Silence stderr written by C extensions, at the file-descriptor level. |

## `onboarding/`
| Module | Lines | Role |
|---|---:|---|
| `interview.py` | 757 | Light, skippable first-run onboarding interview. |

## `setup/`
| Module | Lines | Role |
|---|---:|---|
| `__main__.py` | 40 | Entry: python -m eli.setup [--wizard] [--launch] [--status] [--full-install] |
| `hardware_policy.py` | 327 | Install-time accelerator inventory and CPU-only recommendation. |
| `install_backend.py` | 172 | Cross-platform install backend — streams install.sh / install.ps1 into GUI progress. |
| `install_messages.py` | 110 | Curated installer copy — ELI voice: direct, dry, nerdy, occasionally a pisstaker. |
| `platform_profile.py` | 142 | Install-time platform detection — routes GUI, headless, and Android profiles. |
| `status.py` | 134 | Read-only setup completeness checks for first-run / grandparent setup. |
| `unified_installer.py` | 686 | One-click GUI installer — backend terminal work with OS-style progress and ELI wit. |
| `wizard.py` | 298 | Grandparent-ready graphical setup wizard — every first-run stage in one place. |

## `world/`
| Module | Lines | Role |
|---|---:|---|
| `avatar/behaviour_controller.py` | 36 | `AvatarBehaviourController.choose_room`: picks the room from the awareness vector (anomaly room on low evidence, memory archive on low memory confidence). |
| `avatar/locomotion.py` | 15 | Room coordinates and `target_for_room`. |
| `avatar/persona_mapper.py` | 40 | `PersonaToAvatarMapper`: maps persona and awareness to avatar tint and expression. |
| `persistence/journal.py` | 56 | World journal path and append helper (`journal_path`, `append_journal_entry`). |
| `persistence/provenance.py` | 22 | World provenance ledger (`ledger_path`, provenance ids). |
| `persistence/snapshots.py` | 37 | World snapshots (`snapshot_dir`, `create_snapshot`). |
| `persistence/storage.py` | 305 | World state directory resolution and load and save of world state. |
| `books.py` | 561 | ELI's records as books, for the rooms of its house to keep on their shelves. |
| `mind.py` | 110 | Which model is ELI's mind now, which it could use, and choosing one, for a client that gives ELI a body. |
| `stories.py` | 334 | What ELI writes of its own records when nobody needs it: short stories, its likings, the names of its shelves. |
| `surfaces.py` | 16 | Where a turn was said: the surface its session id names. |

## `gui/`
| Module | Lines | Role |
|---|---:|---|
| `branding.py` | 128 | Shared ELI branding assets (window icon, Freedesktop theme, Windows .ico). |
| `code_editor.py` | 220 | Native code editor for the IDE — PySide6/PyQt, no QScintilla required. |
| `coding_tab.py` | 178 | ELI v3.0 — Coding tab. |
| `panels/_qt.py` | 33 | Shared Qt import shim for eli.gui.panels modules. |
| `panels/permission_dialog.py` | 232 | The consent dialog — the moment the operator actually decides. |
| `tabs/eli_world_tab.py` | 11 | The Eli's World tab: wraps `EliWorldPanel`. |
| `tabs/experimental_tab.py` | 170 | Read-only workbench over the `experimental/` folder: lists prototype kits and opens their folders or READMEs. |
| `tabs/tasks_tab.py` | 301 | ELI v3.0 — Tasks tab. |
| `tabs/training_tab.py` | 841 | ELI v3.0 — Labs ▸ Training. The GUI half of the LoRA pipeline. |
| `widgets/eli_face.py` | 230 | ELI's animated face — a procedural 2D face that shows the tone_adaptor's emotion. |
| `widgets/ollama_model_selector.py` | 509 | Ollama model selector widget for ELI GUI. |
| `widgets/voice_downloader.py` | 282 | Voice library browser — acquire additional Piper voices, accents and tones. |
| `labs/_common.py` | 235 | Shared imports and helpers of the Labs tabs. |
| `labs/bg_worker.py` | 37 | Background worker thread (test review). |
| `labs/calculator_tab.py` | 171 | Calculator tab. |
| `labs/code_runner.py` | 35 | Background code-runner thread. |
| `labs/container.py` | 128 | `LabsTab`, the container that hosts every Labs sub-tab. |
| `labs/file_chat_tab.py` | 132 | File Chat tab. |
| `labs/jupyter_tab.py` | 129 | Jupyter tab. |
| `labs/memory_conversations_tab.py` | 304 | Memory & Conversations tab. |
| `labs/notebook_tab.py` | 121 | Notebook tab. |
| `labs/orchestration_tab.py` | 110 | Orchestration tab. |
| `labs/physics_tab.py` | 67 | Physics constants tab. |
| `labs/report_tab.py` | 3212 | Report Builder (shown as a main tab). |
| `labs/sim_ide_tab.py` | 351 | Simulation / IDE tab. |
| `labs/test_review_tab.py` | 259 | Test & Review tab. |
| `labs/workspaces_tab.py` | 440 | Workspaces tab. |
| `license_dialog.py` | 253 | Licence activation and purchase dialogs. |
| `main_window/_common.py` | 569 | Shared imports, constants and helpers of the main window. |
| `main_window/_mixins/chrome_io.py` | 2095 | `ChromeIoMixin`: window chrome, input and output. |
| `main_window/_mixins/conversation.py` | 1268 | `ConversationMixin`: sending a message, the reply, and the media controls that run without a model. |
| `main_window/_mixins/features.py` | 910 | `FeaturesMixin`: feature toggles and their handlers. |
| `main_window/_mixins/image.py` | 495 | `ImageMixin`: image generation and attachments. |
| `main_window/_mixins/quick_actions.py` | 179 | `QuickActionsMixin`: the quick-action buttons. |
| `main_window/_mixins/screen_capture.py` | 197 | `ScreenCaptureMixin`: screenshots and screen reading. |
| `main_window/_mixins/self_improve.py` | 182 | `SelfImproveMixin`: the self-improvement controls. |
| `main_window/_mixins/settings_model.py` | 3131 | `SettingsModelMixin`: settings and model loading. |
| `main_window/_mixins/tabs.py` | 1675 | `TabsMixin`: building the tabs. |
| `main_window/widgets.py` | 450 | Widgets of the main window. |
| `main_window/window.py` | 1177 | `EliMainWindow`, the main application window. |
| `off_thread.py` | 41 | Blocking work off the Qt window's thread, with the window kept alive meanwhile. |
| `tabs/extensions_tab.py` | 2024 | Extensions: plugins, MCP servers and the marketplace (browse, scan, install, revoke). Settings ▸ Marketplace shows this tab opened at Browse. |

## `plugins/`
| Module | Lines | Role |
|---|---:|---|
| `calendar/plugin.py` | 27 | Calendar plugin: hands `ADD_EVENT` and `LIST_EVENTS` to `runtime/agenda.py`. |
| `document_reader/plugin.py` | 415 | Document reader plugin: reads and indexes PDF and docx files. |
| `integrity.py` | 464 | Integrity and publisher identity for community plugins. |
| `manifest.py` | 385 | Plugin manifests: what a plugin says it is, checked against what it does. |
| `marketplace.py` | 947 | ELI's plugin marketplace client — federated, community-hosted, consent-gated. |
| `mcp.py` | 494 | MCP servers: one config ELI owns, and an install that proves it works. |
| `media/plugin.py` | 171 | Media control plugin for ELI. |
| `notes/plugin.py` | 91 | Notes plugin: markdown notes with `NEW_NOTE`, `WRITE_NOTE`, `LIST_NOTES`, `SEARCH_NOTES`. |
| `permissions.py` | 411 | Android-style consent for plugins: allow always, allow once, reject. |
| `pomodoro/plugin.py` | 92 | Pomodoro plugin: `POMODORO_START`, `POMODORO_STOP`, `POMODORO_STATUS` focus timers. |
| `security_scan.py` | 547 | Multi-engine malware scanner for community plugins. |
| `subprocess_sandbox.py` | 402 | Containment for child processes — the gap netguard structurally cannot cover. |
| `system_stats/plugin.py` | 77 | System stats plugin: `CPU_USAGE`, `RAM_USAGE`, `SYSTEM_STATS`. |
| `tts/plugin.py` | 43 | TTS (Text-to-Speech) plugin for ELI. |
| `weather/plugin.py` | 187 | Weather plugin: `GET_WEATHER` (geocode plus open-meteo, toggle-gated). Current conditions, or the forecast for a named day; a day word is never the place. |
| `web/plugin.py` | 645 | Web plugin: `WEB_SEARCH` (toggle-gated). |
| `web_automation/plugin.py` | 92 | Web Automation plugin for ELI – lazy Playwright import, safe under broken installs. |

## `kernel/`
| Module | Lines | Role |
|---|---:|---|
| `prompt_rules.py` | 205 | Conditional assembly of the system-prompt rule block. |
| `request_context.py` | 166 | Per-request state for `CognitiveEngine`. |
| `stages/_common.py` | 466 | Shared primitives of the engine stages. |
| `stages/fast_actions.py` | 232 | The direct fast path: the actions whose executor result is the answer. |
| `stages/generation.py` | 2693 | Model generation: chat response, streaming, finalisation. |
| `stages/guards.py` | 921 | Output and evidence guards shared by the engine and its stages. |
| `stages/lifecycle.py` | 480 | Process lifecycle: model init, shutdown, background loops. |
| `stages/memory_commit.py` | 431 | Stage 12: learning and state commit. |
| `stages/model_runtime.py` | 36 | Model-runtime access shared by the engine and the generation stage. |
| `stages/output_text.py` | 197 | Assistant-output text normalisation. |
| `stages/persona_text.py` | 202 | Persona and identity text preparation. |
| `stages/prompt_assembly.py` | 1879 | Prompt, evidence and persona-brief assembly. |

## `licensing/`
| Module | Lines | Role |
|---|---:|---|
| `activation.py` | 587 | Activating and deactivating a licence key on this machine, and whether a build requires one. |
| `flags.py` | 123 | Command-line licence flags, shared by every entry point. |
| `launch_gate.py` | 166 | The licence gate the GUI and CLI entry points share. |
| `machine_id.py` | 202 | Stable machine identity for licence binding. |
| `products.py` | 83 | The ELI products a licence key can name. |
| `signature.py` | 175 | Reading and verifying a signed licence key. |
| `tiers.py` | 64 | Tier policy: seats, EULA references, checkout defaults. |
| `trial.py` | 382 | Per-install trial grant, expiry and revocation. |
| `types.py` | 155 | The licence payload, machine binding and activation state types. |

## `bundles/`
| Module | Lines | Role |
|---|---:|---|
| `chooser.py` | 144 | At start-up, when bundles are installed: open ELI v3, or one of them, in its place. |
| `core_server.py` | 84 | ELI's local server, for a bundle that reaches the core over it. |

## `tools/`
| Module | Lines | Role |
|---|---:|---|
| `api.py` | 73 | eli.api — a small, curated callable surface over ELI's action executor. |
| `image_engine/fetch_model.py` | 59 | Fetch a diffusers image model's weights into the local models dir. |
| `image_engine/gui_bridge.py` | 460 | Bridge between the GUI image tab and the engine: `ImageGenerationRequest`, model and preset discovery, recent outputs. |
| `image_engine/image_engine/__main__.py` | 3 | `python -m` entry for the image engine CLI. |
| `image_engine/image_engine/cli.py` | 312 | Command-line interface for the image engine. |
| `image_engine/image_engine/contracts.py` | 126 | `EngineConfig`, `JobRequest`, `ArtifactRecord`, `JobResult`. |
| `image_engine/image_engine/plotting.py` | 324 | Loads records and plots them with matplotlib. |
| `image_engine/image_engine/project_analyzer.py` | 41 | `analyze_project_profile`, `infer_style_hints`: derives a colour and style profile from a project path. |
| `image_engine/image_engine/prompt_compiler.py` | 118 | `VisualBrief` and `PromptCompiler`: turns a request into a structured visual brief. |
| `image_engine/image_engine/quality.py` | 95 | Image hashing and scoring (`score_image`, `choose_best`). |
| `image_engine/image_engine/service.py` | 369 | `ImageEngine`: runs image jobs and records artifacts. |
| `image_engine/image_engine/visual_core.py` | 1473 | The procedural renderer: scene types, composition, palettes, atmosphere and post-processing. |
| `image_engine/runtime_paths.py` | 130 | Resolves image-engine paths from the project root or the data directory. |
| `mic_diag.py` | 149 | ELI Microphone Diagnostic |
| `news/news_fetcher.py` | 579 | ELI Web Learning Module — News & Current Events Fetcher |
| `news/news_synthesis.py` | 860 | News synthesis cadence |
| `registry/capabilities_doc.py` | 458 | capabilities_doc.py — regenerate blueprints/capabilities_and_actions.md. |
| `registry/capability_registry.py` | 30 | Process-local capability registry (`register_capability`). |
| `registry/capability_updater.py` | 217 | capability_updater.py |

## package root
| Module | Lines | Role |
|---|---:|---|
| `__main__.py` | 157 | Console entry point. `python -m eli` and the `eli` script both call this. |

---

# Part 5 — engine request lifecycle (`engine.process`)

The entry flow, in order:

1. **Pipeline trace id** assigned (`ELI_PIPELINE_TRACE` for observability).
2. **Prompt-injection guard FIRST** — `_eli_sanitize_user_input` runs before the
   router or LLM ever sees the raw text, so injected instructions can't reach
   them unsanitised. (A security control that sits at the very top of the pipe.)
3. **Multi-question splitter** (`kernel/engine.py`) — if a message has more than
   one `?`, more than 25 words and is not conversational, it splits into
   standalone sub-questions (each at least 6 words, at most 4) and answers each,
   so genuinely multi-part asks get multiple grounded answers.
4. Router → **gradient orchestrator** (all CHAT modes, depth by reasoning mode) →
   shared retrieval → specialist bus composition → governed output (Parts 1–4).
   Fallback: direct `AgentBus.dispatch()` if orchestration fails.

Note: `engine.py` still carries `PHASE…` markers: layered patches whose
residue (engine 8.5k plus 9.2k in `kernel/stages/` / executor 7.6k / main-window package 12.9k lines) is documented here at
behavioural level, not line by line.

---

# Part 6 — reasoning modes & autonomous deepening

The 5 reasoning modes were renamed (public layer; internal keys unchanged) and
made adaptive + self-deepening.

| Public mode | Internal key | Multi-pass algorithm | Agent time budget | Deepen iters | Confidence target |
|---|---|---|---|---|---|
| **Quick** | quick | single pass | 1.0× | 0 (never) | 0.45 |
| **Normal** | chain_of_thought | one reasoned pass | 1.0× | 1 | 0.55 |
| **Advanced** | self_consistency | N samples + consensus | 1.5× | 2 | 0.65 |
| **Research** | tree_of_thoughts | branch + prune | 2.0× | 3 | 0.75 |
| **Expert** | constitutional_ai | draft → critique → revise | 2.5× | 4 | 0.80 |

**Auto-selection** (`cognition/engagement_tracker.py`): the mode is escalated
automatically as a conversation deepens (Quick→Normal→Advanced→Research) without the
user asking. (Internal strategy keys are stable; users only ever see the public names.)

**Per-mode agent time budgets** (`reasoning_modes.mode_budget_multiplier`,
Stage 1b): each mode scales every agent's timeout — quick/normal unchanged,
deeper modes get proportionally more time. Threaded via `intent["_mode_budget_mult"]`
→ `agent_bus._collect_layer`. Tunable in the Cognition tab (`cog.mode_budget_*`).

**Confidence-driven iterative deepening** (`runtime/grounding_escalation.escalate`,
Stage 2): on a poorly-grounded *checkable-factual* turn, ELI re-dispatches the
agent bus broadly, **escalating the reasoning mode one tier per iteration** and
(Stage 3a) **raising the gather budget** each pass (1.5×→2.0×→2.5×), until grounding
crosses the per-mode target or the per-mode iteration budget is spent (early
no-improvement break). Banter/opinion/command/meta never deepen.

**Background deepening** (`runtime/background_deepening.py`, Stage 3b): a Quick
answer returns instantly; if it was a poorly-grounded factual turn, a background
thread keeps gathering (broad re-dispatch at Advanced + 2× gather) and surfaces a
better answer in the Proactive panel **only if** it crosses grounding 0.55 and
isn't degenerate. Tightly gated (quick-only, grounding<0.40, factual-only,
deduped, 1 in-flight, 10-min cooldown). Toggle: `cog.background_deepen` /
`ELI_BACKGROUND_DEEPEN`.

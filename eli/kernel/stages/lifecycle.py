"""Stage: process lifecycle — model init, shutdown, background loops.

Owns _init_gguf (load/probe the local model), shutdown (idempotent native
teardown), the background daemons (reflection, habit detection + scheduling,
self-improvement, proactive listener), and the session-digest generator.

__init__ deliberately stays on CognitiveEngine — it is the class's own
constructor and builds the state every stage reads; only the lifecycle
*operations* live here. Composed onto CognitiveEngine as a mixin.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Dict

from eli.core import runtime_settings
from eli.kernel.stages._common import _eli_test_mode
# the engine's own module-level handle; _init_gguf read a name this module never defined
from eli.kernel.stages.model_runtime import gguf_inference
from eli.utils.log import get_logger
import logging

log = get_logger(__name__)

# Run the destructive teardown once per process. The close steps (memory/vector/GGUF) hit
# module-level singletons shared by every CognitiveEngine, so a second instance shutting down
# after the first freed the CUDA handles would double-free and segfault. This flag holds however
# many instances or atexit hooks fire.
_ELI_NATIVE_TEARDOWN_DONE = False


class LifecycleMixin:
    def cancel_generation(self) -> None:
        """Stop the in-flight chat generation at the next token (GUI Stop button)."""
        try:
            from eli.cognition import gguf_inference as _ggi
            _ggi.request_cancel_generation()
        except Exception:
            log.debug("[COGNITIVE] cancel_generation failed", exc_info=True)

    def shutdown(self) -> None:
        """
        Graceful session shutdown.

        Ordering matters — see numbered steps:
        1. Stop the proactive daemon before further state changes.
        2-3. Persist working memory + engagement narrative.
        3.3-3.4. Run the model readings waiting for the owner's pause, then the session summary,
            both before the inference abort, while the model is loaded.
        4-5. Reflection + self-improvement passes (read-only of memory).
        6.  Close the memory store (final WAL checkpoint, SQLite handles).
        7.  Close the vector-store embedder.
        8.  Explicit gguf_inference.unload_model() — prevents the Llama.__del__
            segfault on interpreter teardown by releasing the C handle while
            the Python GIL and llama_cpp module are still healthy.

        Called by GUI closeEvent, atexit, and SIGINT/SIGTERM handlers.
        Safe to call multiple times (idempotent).
        """
        if getattr(self, "_shutdown_called", False):
            return
        self._shutdown_called = True

        if _eli_test_mode():
            log.debug("[COGNITIVE] Shutdown: test mode, persistent flush skipped.")
            return

        log.debug("[COGNITIVE] Shutdown: flushing session state…")

        # 1. Stop the proactive daemon FIRST so no more writes happen
        # during the rest of teardown.
        try:
            daemon = getattr(self, "proactive_daemon", None)
            if daemon is not None and hasattr(daemon, "stop"):
                daemon.stop()
                log.debug("[COGNITIVE] Shutdown: proactive daemon stopped")
        except Exception as _pd_err:
            log.debug(f"[COGNITIVE] Shutdown: proactive daemon stop failed (non-fatal): {_pd_err}")

        # 2. Flush working memory → persistent store. Every conversation's pins are kept for that
        # user (persist); only the owner's go into long-term memory, which has no user of its own,
        # so an API member's pinned facts never become the owner's memories.
        try:
            _wm_db = str(getattr(self.memory, "db_path", "") or "")
            for _wm in self._all_working_memories():
                if _wm_db:
                    _wm.persist(_wm_db)
                if getattr(_wm, "user_id", ""):
                    continue
                saved = _wm.flush_to_memory(self.memory)
                if saved:
                    log.debug(f"[COGNITIVE] Shutdown: {saved} WorkingMemory fact(s) persisted")
        except Exception as _wm_err:
            log.debug(f"[COGNITIVE] Shutdown: WM flush failed (non-fatal): {_wm_err}")

        # 3. Log session narrative as a memory for next-session continuity
        try:
            if self._engagement:
                narrative = self._engagement.session_narrative()
                depth = self._engagement.session_depth()
                if narrative and self._engagement._turns and depth >= 0.25:
                    self.memory.store_memory(
                        narrative,
                        tags=["session_summary", "continuity"],
                        source="session_end",
                        kind="reflection",
                        importance=0.70,
                    )
                    log.debug(f"[COGNITIVE] Shutdown: session narrative stored (depth={depth:.2f})")
                elif narrative and self._engagement._turns:
                    log.debug(f"[COGNITIVE] Shutdown: session narrative skipped (depth={depth:.2f} < 0.25, casual session)")
        except Exception as _eng_err:
            log.debug(f"[COGNITIVE] Shutdown: engagement flush failed (non-fatal): {_eng_err}")

        # 3.3 What waits for the owner's pause (inference_broker.when_idle: ELI's model reading what
        # the last message said of the reply before it) runs now, on this thread, while the model is
        # still loaded: the owner is not waiting any more. The worker that would have run it dies with
        # the process, so a reading still waiting when the session ended was lost. With a reply still
        # being generated the model is not free (a background call gives way to it), so the word
        # cues' reading, settled at once, stands, and the log says so.
        try:
            from eli.cognition.inference_broker import foreground_in_progress, run_idle_work
            if foreground_in_progress():
                log.info("[COGNITIVE] Shutdown: a reply was still being generated, so the model's readings "
                         "waiting for the owner's pause were not run; the word cues' reading stands")
            else:
                run_idle_work()
                log.debug("[COGNITIVE] Shutdown: the readings waiting for the owner's pause are done")
        except Exception as _iw_err:
            log.warning(f"[COGNITIVE] Shutdown: the readings waiting for the owner's pause did not run: {_iw_err}")

        # 3.4 The in-depth LLM end-of-session summary (session_summaries, user_patterns, semantic
        # tier, memories FTS) runs before aborting inference, while shutdown isn't yet signalled;
        # otherwise _llm_summarise_session bails and every session logs llm=False.
        try:
            from eli.runtime.profile_extractor import write_llm_session_summary
            _ss = write_llm_session_summary(
                session_id=str(getattr(self, "session_id", "") or "") or None,
                user_id=str(getattr(self, "user_id", "") or "") or None,
            )
            if _ss.get("inserted"):
                log.debug(f"[COGNITIVE] Shutdown: session summary written "
                          f"(llm={_ss.get('llm')}, turns={_ss.get('turns_count')})")
        except Exception as _ss_err:
            log.debug(f"[COGNITIVE] Shutdown: session summary failed (non-fatal): {_ss_err}")

        # 3.5 Abort in-flight generation after the hand-off note. closeEvent can arrive
        # while a slow CPU/iGPU stream still holds the broker lock; the summary above
        # only runs when the model is idle (falls back to heuristic if not).
        try:
            from eli.cognition import gguf_inference as _ggi_sd
            _ggi_sd.signal_shutdown()
            log.debug("[COGNITIVE] Shutdown: inference abort signalled")
        except Exception as _sd_err:
            log.debug(f"[COGNITIVE] Shutdown: inference abort signal failed (non-fatal): {_sd_err}")

        # Steps 4-8 touch process-global singletons (memory store, vector
        # embedder, GGUF model). Run them AT MOST ONCE per process — a second
        # engine instance re-closing the already-freed CUDA handles segfaults.
        global _ELI_NATIVE_TEARDOWN_DONE
        if _ELI_NATIVE_TEARDOWN_DONE:
            log.debug("[COGNITIVE] Shutdown: native teardown already done by another instance; skipping shared steps.")
            log.debug("[COGNITIVE] Shutdown: complete.")
            return
        _ELI_NATIVE_TEARDOWN_DONE = True

        # 4. Final reflection pass — extract patterns from this session's memories
        try:
            from eli.runtime.reflection import reflect_on_memories
            reflect_on_memories(days=1)
        except Exception:
            logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

        # 5. Brief self-improvement analysis on exit (if there were failures). Analysis only: generating
        # fix proposals dispatches the coding agent over a thread pool and blocks on inference, which
        # hung window close until Ctrl-C. The daemon tick still proposes.
        try:
            from eli.runtime.self_improvement import get_self_improvement
            si = get_self_improvement()
            si.analyze_and_improve(propose=False)
        except Exception:
            logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

        # 6. Close the memory store — final WAL checkpoint, then SQLite handles.
        try:
            close_fn = getattr(self.memory, "close", None)
            if callable(close_fn):
                close_fn()
                log.debug("[COGNITIVE] Shutdown: memory store closed")
        except Exception as _mem_err:
            log.debug(f"[COGNITIVE] Shutdown: memory close failed (non-fatal): {_mem_err}")

        # 7. Close the vector-store embedder (also a llama_cpp Llama instance, so it needs the same
        # explicit unload as the main model). Read the module singleton directly — do NOT lazy-init
        # during shutdown.
        try:
            from eli.memory import vector_store as _vs_mod
            vs = getattr(_vs_mod, "_store", None)
            if vs is not None:
                vs.flush(pending_only=True)   # memories added since the last batched save
                vs.close()
                log.debug("[COGNITIVE] Shutdown: vector-store embedder closed")
        except Exception as _emb_err:
            log.debug(f"[COGNITIVE] Shutdown: embedder close failed (non-fatal): {_emb_err}")

        # 7b. Close the resident co-resident vision model (Moondream), if any —
        # a second resident CUDA context; release it before the text model.
        try:
            from eli.perception.vision import unload_resident_fast_model
            unload_resident_fast_model()
        except Exception as _rv_err:
            log.debug(f"[COGNITIVE] Shutdown: resident vision close failed (non-fatal): {_rv_err}")

        # 8. Explicit GGUF unload, to avoid a Llama.__del__ segfault on exit. Suppress native stderr
        # only around the unload: llama.cpp's destructor prints a benign "CUDA_Host compute buffer size
        # ... does not match expectation". fd-level redirect because it comes from C, restored in
        # finally.
        try:
            import os as _os_sd, sys as _sys_sd
            from eli.cognition.gguf_inference import unload_model
            _saved_fd = None
            _devnull = None
            try:
                _sys_sd.stderr.flush()
                _saved_fd = _os_sd.dup(2)
                _devnull = _os_sd.open(_os_sd.devnull, _os_sd.O_WRONLY)
                _os_sd.dup2(_devnull, 2)
            except Exception:
                _saved_fd = None
            try:
                unload_model()
            finally:
                try:
                    if _saved_fd is not None:
                        _os_sd.dup2(_saved_fd, 2)
                        _os_sd.close(_saved_fd)
                    if _devnull is not None:
                        _os_sd.close(_devnull)
                except Exception:
                    logging.getLogger(__name__).debug("suppressed exception", exc_info=True)
            log.debug("[COGNITIVE] Shutdown: GGUF model unloaded")
        except Exception as _gguf_err:
            log.debug(f"[COGNITIVE] Shutdown: GGUF unload failed (non-fatal): {_gguf_err}")

        log.debug("[COGNITIVE] Shutdown: complete.")

    def _init_gguf(self) -> None:
        global gguf_inference
        self._gguf_available = False
        self._gguf_load_error = None
        # Initialize runtime facts to defaults
        self._model_path = "unknown"
        self._ctx = 0
        self._gpu_layers = 0
        try:
            if gguf_inference is None:
                self._gguf_load_error = "GGUF module not available"
                log.debug(f"[COGNITIVE] {self._gguf_load_error}")
                return
            model_path = gguf_inference.get_model_path()
            if not model_path:
                self._gguf_load_error = "No GGUF model found"
                log.debug(f"[COGNITIVE] {self._gguf_load_error}")
                return
            model = gguf_inference.load_model()
            if model is not None:
                self._gguf_available = True
                self._gguf_load_error = None
                # Store runtime facts
                self._model_path = str(model_path)
                self._ctx = self._runtime_n_ctx()
                # GPU layers from settings
                try:
                    settings = runtime_settings.load_settings()
                    self._gpu_layers = int(settings.get("n_gpu_layers", 0))
                except Exception:
                    self._gpu_layers = 0
                log.debug("[COGNITIVE] GGUF model loaded successfully")
            else:
                self._gguf_available = False
                getter = getattr(gguf_inference, "get_last_error", None)
                self._gguf_load_error = getter() if callable(getter) else "GGUF load failed"
                log.debug(f"[COGNITIVE] GGUF model load failed: {self._gguf_load_error}")
        except Exception as e:
            self._gguf_available = False
            self._gguf_load_error = str(e)
            log.debug(f"[COGNITIVE] GGUF init failed: {e}")

    def _generate_session_digest(self) -> None:
        """Summarise the last 20 conversation turns into a searchable session digest memory."""
        try:
            recent = self.memory.get_recent_conversation(limit=20)
            if not recent or len(recent) < 6:
                return
            user_msgs = [t["content"] for t in recent if t.get("role") == "user" and t.get("content")]
            if not user_msgs:
                return
            # Extract key topics from user messages
            _stopwords = {"i", "me", "my", "the", "a", "an", "is", "was", "it", "to", "do",
                          "you", "your", "and", "or", "of", "in", "on", "for", "what", "how",
                          "can", "that", "this", "with", "not", "are", "have", "has", "be",
                          "just", "so", "ok", "yeah", "yes", "no", "hey", "hi"}
            _word_freq: Dict[str, int] = {}
            for msg in user_msgs:
                for w in msg.lower().split():
                    w = re.sub(r"[^a-z0-9]", "", w)
                    if len(w) >= 4 and w not in _stopwords:
                        _word_freq[w] = _word_freq.get(w, 0) + 1
            top_topics = [w for w, _ in sorted(_word_freq.items(), key=lambda x: x[1], reverse=True)[:6]]
            if not top_topics:
                return
            digest = f"Session digest ({len(recent)} turns): topics — {', '.join(top_topics)}. Last user message: {user_msgs[-1][:100]}"
            # Dedup: only store if meaningfully different from last digest
            try:
                existing = self.memory.recall_memory(top_topics[0], limit=2)
                if any("Session digest" in str(m.get("text", "")) for m in existing):
                    return
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            self.memory.store_memory(
                digest,
                tags=["session_digest", "auto"],
                kind="session_context",
                source="eli_session",
                importance=0.50,
            )
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def _start_reflection_loop(self) -> None:
        def loop() -> None:
            initial_delay = int(
    os.environ.get(
        "ELI_REFLECTION_START_DELAY_SEC",
         "300") or 300)
            time.sleep(max(0, initial_delay))
            last_reflection = 0.0
            _FLOOR = 30 * 60      # never reflect more than once per 30 min, even under a signal burst
            _CEILING = 24 * 3600  # still reflect at least once a day with zero signal
            _POLL = 30 * 60       # how often to check for an early-wake signal
            while self.running:
                self._reflection_wake.wait(timeout=_POLL)
                self._reflection_wake.clear()
                now = time.time()
                if now - last_reflection < _FLOOR:
                    continue
                if now - last_reflection >= _CEILING or self._significant_reflection_signal(last_reflection):
                    self._reflect()
                    last_reflection = time.time()
        threading.Thread(
    target=loop,
    daemon=True,
     name="eli-reflection").start()

    def _significant_reflection_signal(self, since: float) -> bool:
        """Cheap reuse of reflect_on_period's own significance checks, run early instead
        of only inside the 24h reflection: a fresh user challenge or an active failure
        cluster is worth reflecting on now, not up to a day later."""
        try:
            from eli.runtime.evidence_ledger import recent_events
            rows = recent_events(limit=1, event_type="user_challenge")
            if rows and float(rows[0].get("timestamp") or 0) > since:
                return True
        except Exception:
            log.debug("reflection wake: user_challenge check failed", exc_info=True)
        try:
            from eli.runtime import lessons as _lessons
            if _lessons.from_failure_clusters(days=1.0, min_count=3):
                return True
        except Exception:
            log.debug("reflection wake: failure cluster check failed", exc_info=True)
        return False

    def _reflect(self) -> None:
        _MAX_RETRIES = 4
        acquired = False
        try:
            for _ in range(_MAX_RETRIES):
                if self._gguf_lock.acquire(blocking=False):
                    acquired = True
                    break
                time.sleep(1)
            if not acquired:
                log.debug("[COGNITIVE] Reflection deferred (GGUF busy)")
                return
            try:
                from eli.runtime.reflection import run_reflection
                result = run_reflection(hours=24)
                insights = result.get("insights", [])
                if insights:
                    log.debug(f"[COGNITIVE] eli-reflection: {len(insights)} insights generated")
                    # Persisting them here was a double write: run_reflection -> reflect_on_period already stores
                    # each insight, and this loop stored the same rows again with no duplicate check (135 reflection
                    # rows on one machine, 34 exact duplicates). reflect_on_period owns it.
            except Exception as e:
                log.debug(f"[COGNITIVE] Reflection failed: {e}")
        finally:
            if acquired:
                self._gguf_lock.release()
        try:
            from eli.cognition.persona_updater import update_persona_overlay
            update_persona_overlay(memory=self.memory)
        except Exception as e:
            log.debug(f"[COGNITIVE] Persona overlay update failed: {e}")

    def _start_habit_loop(self) -> None:
        def loop() -> None:
            self._detect_habits()
            while self.running:
                time.sleep(12 * 3600)
                self._detect_habits()
        threading.Thread(target=loop, daemon=True, name="eli-habits").start()

    def _detect_habits(self) -> None:
        acquired = False
        try:
            _MAX_RETRIES = 4
            for _ in range(_MAX_RETRIES):
                if self._gguf_lock.acquire(blocking=False):
                    acquired = True
                    break
                time.sleep(1)
            if not acquired:
                # Fix 7b: log message
                log.debug("[COGNITIVE] Habit detection deferred")
                return
            try:
                from eli.planning.habits import detect_habits
                detect_habits(days=14, min_occurrences=3)
                log.debug("[COGNITIVE] eli-habits: detection complete")
            except Exception as e:
                log.debug(f"[COGNITIVE] Habit detection failed: {e}")
        finally:
            if acquired:
                self._gguf_lock.release()

    def _start_habit_scheduler(self) -> None:
        """Start the habit rule execution scheduler (fires detected habits on schedule)."""
        try:
            from eli.planning.habits_scheduler import get_scheduler
            get_scheduler()
            log.debug("[COGNITIVE] Habit scheduler started")
        except Exception as e:
            log.debug(f"[COGNITIVE] Habit scheduler failed to start: {e}")

    def _start_self_improvement_loop(self) -> None:
        """Start the background self-improvement analysis loop (24h interval)."""
        try:
            from eli.runtime.self_improvement import get_self_improvement
            get_self_improvement().start_self_improvement_loop(interval_hours=24)
            log.debug("[COGNITIVE] Self-improvement loop scheduled (analyses now, then every 24h)")
        except Exception as e:
            log.debug(f"[COGNITIVE] Self-improvement loop failed to start: {e}")

    def _start_proactive_listener(self) -> None:
        proactive_flag = str(
    os.environ.get(
        "ELI_PROACTIVE",
         "1")).strip().lower()
        if proactive_flag in ("0", "false", "no", "off"):
            log.debug("[COGNITIVE] Proactive daemon disabled by ELI_PROACTIVE=0")
            self.proactive_daemon = None
            return
        if os.environ.get("ELI_PROACTIVE_STARTED") == "1":
            log.debug(
                "[COGNITIVE] Proactive daemon already running - skipping duplicate start")
            self.proactive_daemon = None
            return
        try:
            from eli.planning.proactive_daemon import start_daemon
            self.proactive_daemon = start_daemon()
            os.environ["ELI_PROACTIVE_STARTED"] = "1"
            self.add_observation("system", "Proactive daemon started")
            log.debug("[COGNITIVE] Proactive daemon started – habit learning active")
        except Exception as e:
            log.debug(f"[COGNITIVE] Failed to start proactive daemon: {e}")
            self.proactive_daemon = None

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
from eli.kernel.stages.model_runtime import gguf_inference, gguf_import_error
from eli.utils.log import get_logger

log = get_logger(__name__)

# Idempotent native-teardown guard: shutdown() flips this once so a second call
# (e.g. atexit firing after an explicit shutdown) can't double-free the model.
_ELI_NATIVE_TEARDOWN_DONE = False


class LifecycleMixin:
    def shutdown(self) -> None:
        """
        Graceful session shutdown.

        Ordering matters — see numbered steps:
        1. Stop the proactive daemon before further state changes.
        2-3. Persist working memory + engagement narrative.
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

        # 0. Signal shutdown to the inference layer FIRST. A background self-improvement
        # /codegen call can be mid-flight in a single 10+ minute native llm() call holding
        # the shared lock; the OS can't kill it, so step 8 (unload_model) would block for
        # 20-30 minutes. This makes any in-flight generation yield at the next token and
        # short-circuits new background calls, so teardown proceeds immediately.
        try:
            from eli.cognition import gguf_inference as _ggi_sd
            _ggi_sd.signal_shutdown()
            log.debug("[COGNITIVE] Shutdown: inference abort signalled")
        except Exception as _sd_err:
            log.debug(f"[COGNITIVE] Shutdown: inference abort signal failed (non-fatal): {_sd_err}")

        # 1. Stop the proactive daemon FIRST so no more writes happen
        # during the rest of teardown.
        try:
            daemon = getattr(self, "proactive_daemon", None)
            if daemon is not None and hasattr(daemon, "stop"):
                daemon.stop()
                log.debug("[COGNITIVE] Shutdown: proactive daemon stopped")
        except Exception as _pd_err:
            log.debug(f"[COGNITIVE] Shutdown: proactive daemon stop failed (non-fatal): {_pd_err}")

        # 2. Flush working memory → persistent store
        try:
            if self._working_memory:
                saved = self._working_memory.flush_to_memory(self.memory)
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

        # 3.5 In-depth, LLM-generated end-of-session summary → session_summaries.
        # Runs while the GGUF is still loaded (unload is step 8). 100% local; on
        # any failure it falls back to a heuristic summary internally, and the
        # whole step is guarded so it can never block or break shutdown.
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
            pass

        # 5. Brief self-improvement analysis on exit (if there were failures)
        try:
            from eli.runtime.self_improvement import get_self_improvement
            si = get_self_improvement()
            si.analyze_and_improve()
        except Exception:
            pass

        # 6. Close the memory store — final WAL checkpoint, then SQLite handles.
        try:
            close_fn = getattr(self.memory, "close", None)
            if callable(close_fn):
                close_fn()
                log.debug("[COGNITIVE] Shutdown: memory store closed")
        except Exception as _mem_err:
            log.debug(f"[COGNITIVE] Shutdown: memory close failed (non-fatal): {_mem_err}")

        # 7. Close the vector-store embedder (also a llama_cpp Llama instance,
        # so it needs the same explicit unload treatment as the main model).
        # Read the module singleton directly — do NOT lazy-init during shutdown.
        try:
            from eli.memory import vector_store as _vs_mod
            vs = getattr(_vs_mod, "_store", None)
            embedder = getattr(vs, "_embedder", None) if vs is not None else None
            if embedder is not None:
                _emb_close = getattr(embedder, "close", None)
                if callable(_emb_close):
                    _emb_close()
                else:
                    _underlying = getattr(embedder, "_llm", None) or getattr(embedder, "llm", None)
                    _u_close = getattr(_underlying, "close", None) if _underlying is not None else None
                    if callable(_u_close):
                        _u_close()
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

        # 8. Explicit GGUF unload — prevents Llama.__del__ segfault on exit.
        # Suppress NATIVE (C-level) stderr only around the unload: llama.cpp's
        # destructor prints a benign, non-actionable warning ("CUDA_Host compute
        # buffer size … does not match expectation"). fd-level redirect (not
        # contextlib) because the message comes from C, not Python. Scoped +
        # restored in finally so ELI's own logging is unaffected.
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
                    pass
            log.debug("[COGNITIVE] Shutdown: GGUF model unloaded")
        except Exception as _gguf_err:
            log.debug(f"[COGNITIVE] Shutdown: GGUF unload failed (non-fatal): {_gguf_err}")

        log.debug("[COGNITIVE] Shutdown: complete.")

    def _init_gguf(self) -> None:
        self._gguf_available = False
        self._gguf_load_error = None
        # Initialize runtime facts to defaults
        self._model_path = "unknown"
        self._ctx = runtime_settings.DEFAULT_N_CTX
        self._gpu_layers = 0
        try:
            if gguf_inference is None:
                # The inference module wouldn't import. gguf_import_error carries
                # the root cause (e.g. a llama-cpp GPU backend .so that won't load)
                # so a broken CUDA/ROCm/Metal build is diagnosable, not just "off".
                self._gguf_load_error = (
                    f"GGUF module not available — {gguf_import_error}"
                    if gguf_import_error else "GGUF module not available")
                if _eli_test_mode():
                    log.debug(f"[COGNITIVE] {self._gguf_load_error}")
                else:
                    log.warning(f"[COGNITIVE] local inference unavailable: "
                                f"{self._gguf_load_error}")
                return
            model_path = gguf_inference.get_model_path()
            if not model_path:
                # Normal on a fresh install with no model downloaded yet — not an error.
                self._gguf_load_error = "No GGUF model found"
                log.debug(f"[COGNITIVE] {self._gguf_load_error}")
                return
            model = gguf_inference.load_model()
            if model is not None:
                self._gguf_available = True
                self._gguf_load_error = None
                # Store runtime facts
                self._model_path = str(model_path)
                # Try to get context size from loaded model or settings
                try:
                    # Some gguf_inference modules expose get_n_ctx or
                    # get_context_size
                    if hasattr(gguf_inference, 'get_n_ctx'):
                        self._ctx = int(gguf_inference.get_n_ctx())
                    elif hasattr(gguf_inference, 'get_context_size'):
                        self._ctx = int(gguf_inference.get_context_size())
                    else:
                        settings = runtime_settings.load_settings()
                        self._ctx = int((settings or {}).get("n_ctx", runtime_settings.DEFAULT_N_CTX))
                except Exception:
                    self._ctx = runtime_settings.DEFAULT_N_CTX
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
                # A model IS present but wouldn't load (VRAM, corrupt GGUF, GPU
                # backend fault) — the user expects inference, so make it visible.
                if _eli_test_mode():
                    log.debug(f"[COGNITIVE] GGUF model load failed: {self._gguf_load_error}")
                else:
                    log.warning(f"[COGNITIVE] GGUF model load failed: {self._gguf_load_error}")
        except Exception as e:
            self._gguf_available = False
            self._gguf_load_error = str(e)
            if _eli_test_mode():
                log.debug(f"[COGNITIVE] GGUF init failed: {e}")
            else:
                log.warning(f"[COGNITIVE] GGUF init failed: {e}", exc_info=True)

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
            _MAX_RETRIES = 4  # Fix 7: retry instead of skipping
            initial_delay = int(
    os.environ.get(
        "ELI_REFLECTION_START_DELAY_SEC",
         "300") or 300)
            time.sleep(max(0, initial_delay))
            while self.running:
                self._reflect()
                time.sleep(24 * 3600)
        threading.Thread(
    target=loop,
    daemon=True,
     name="eli-reflection").start()

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
                    # Store individual insights as searchable "insight" memories.
                    # NOT tagged "reflection" — that tag is noise-filtered in recall.
                    for _ins in insights[:5]:
                        _ins_text = str(_ins or "").strip()
                        if not _ins_text or len(_ins_text) < 15:
                            continue
                        try:
                            self.memory.store_memory(
                                _ins_text,
                                tags=["eli_insight", "auto"],
                                kind="insight",
                                source="eli_reflection",
                                importance=0.68,
                            )
                        except Exception:
                            log.debug("suppressed exception", exc_info=True)
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
            log.debug("[COGNITIVE] Self-improvement loop started")
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

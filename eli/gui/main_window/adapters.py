"""adapters — extracted from gui_eli_original.py (v3 modularisation)."""
from __future__ import annotations
from eli.gui.main_window._common import *  # noqa: F401,F403


class CentralMemoryAdapter:
    def __init__(self, mem_instance):
        self._mem = mem_instance

    def store(self, text: str, tags: List[str] = None, kind: str = "note",
              source: str = "user", confidence: float = 0.8) -> bool:
        try:
            result = self._mem.store_memory(
                text=text,
                tags=tags or [],
                source=source,
                kind=kind,
                confidence=confidence
            )
            return result.get("ok", False)
        except Exception as e:
            print(f"Memory store error: {e}")
            return False

    def search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        try:
            results = self._mem.recall_memory(query, limit=limit)
            out = []
            for r in results:
                out.append({
                    "timestamp": r.get("ts") or r.get("timestamp") or "",
                    "kind": r.get("kind", "note"),
                    "text": r.get("text", ""),
                    "tags": r.get("tags", ""),
                })
            return out
        except Exception as e:
            print(f"Memory search error: {e}")
            return []

    def get_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        try:
            if hasattr(self._mem, "get_recent_memories"):
                results = self._mem.get_recent_memories(limit=limit)
            else:
                results = self._mem.recall_memory("", limit=limit)
            out = []
            for r in results:
                out.append({
                    "timestamp": r.get("ts") or r.get("timestamp") or "",
                    "kind": r.get("kind", "note"),
                    "text": r.get("text", ""),
                    "tags": r.get("tags", ""),
                })
            return out
        except Exception as e:
            print(f"Memory get_recent error: {e}")
            return []

    def get_stats(self) -> Dict[str, Any]:
        try:
            if hasattr(self._mem, "get_stats"):
                return self._mem.get_stats()
            else:
                return {"total": 0, "by_kind": {}}
        except Exception as e:
            print(f"Memory stats error: {e}")
            return {"total": 0, "by_kind": {}}

    def get_recent_memories(self, limit: int = 10) -> list:
        try:
            return self._mem.get_recent_memories(limit=limit)
        except Exception:
            return []

    def log_event(self, event_type: str, description: str, metadata: Dict = None):
        try:
            self._mem.log_habit_event(event_type, {"description": description, **(metadata or {})})
        except Exception as e:
            print(f"Event log error: {e}")

    # --- Habit methods (direct pass-through) ---
    def get_habit_rules(self, enabled_only: bool = True) -> List[Dict[str, Any]]:
        return self._mem.get_habit_rules(enabled_only)

    def add_habit_rule(self, name: str, command: str, hour: int, minute: int,
                       days: list = None, enabled: bool = True) -> int:
        return self._mem.add_habit_rule(name, command, hour, minute, days, enabled=enabled)

    def update_habit_rule(self, rule_id: int, **fields) -> bool:
        return self._mem.update_habit_rule(rule_id, **fields)

    def delete_habit_rule(self, rule_id: int) -> None:
        conn = self._mem._get_connection()
        try:
            conn.execute("DELETE FROM habit_rules WHERE id = ?", (rule_id,))
            conn.commit()
        finally:
            conn.close()

    def toggle_habit_rule(self, rule_id: int, enabled: bool) -> None:
        conn = self._mem._get_connection()
        try:
            conn.execute("UPDATE habit_rules SET enabled = ? WHERE id = ?", (1 if enabled else 0, rule_id))
            conn.commit()
        finally:
            conn.close()


class ExecutorBridge:
    def __init__(self):
        self.executor_module = None
        self.router_module = None
        self.import_notes: List[str] = []
        self._load_modules()
    def _candidate_roots(self) -> List[Path]:
        here = Path(__file__).resolve()
        candidates = [
            here.parent,
            here.parent.parent,
            here.parent.parent.parent,
            here.parent.parent.parent.parent,
            PROJECT_ROOT,
            Path.cwd(),
            Path(__file__).resolve().parents[2],
        ]
        seen = set()
        out: List[Path] = []
        for c in candidates:
            try:
                r = c.resolve()
            except Exception:
                r = c
            key = str(r)
            if key not in seen:
                seen.add(key)
                out.append(r)
        return out
    def _prepare_sys_path(self) -> None:
        import sys as _sys
        for root in self._candidate_roots():
            root_str = str(root)
            if root_str not in _sys.path:
                _sys.path.insert(0, root_str)
    def _import_first(self, module_names: List[str]):
        self._prepare_sys_path()
        last_err = None
        for name in module_names:
            try:
                module = __import__(name, fromlist=['*'])
                self.import_notes.append(f"loaded:{name}")
                return module
            except Exception as e:
                last_err = e
                self.import_notes.append(f"failed:{name}:{e}")
        if last_err:
            raise last_err
        raise ImportError('No module names supplied')
    def _load_modules(self):
        executor_candidates = [
            'eli.execution.executor_enhanced',
            'eli.tools.executor_enhanced',
            'eli.execution.executor_enhanced',
        ]
        router_candidates = [
            'eli.execution.router_enhanced',
            'eli.tools.router_enhanced',
            'eli.execution.router_enhanced',
        ]
        try:
            self.executor_module = self._import_first(executor_candidates)
            print(f"✅ Executor module loaded: {getattr(self.executor_module, '__name__', self.executor_module)}")
        except Exception as e:
            print(f"⚠️  Executor module not found: {e}")
        try:
            self.router_module = self._import_first(router_candidates)
            print(f"✅ Router module loaded: {getattr(self.router_module, '__name__', self.router_module)}")
        except Exception as e:
            print(f"⚠️  Router module not found: {e}")
    def route_command(self, text: str) -> Dict[str, Any]:
        if self.router_module:
            try:
                if hasattr(self.router_module, 'route'):
                    return self.router_module.route(text)
                if hasattr(self.router_module, 'route_command'):
                    return self.router_module.route_command(text)
            except Exception as e:
                print(f"⚠️  Router error: {e}")
        return self._simple_route(text)
    def _simple_route(self, text: str) -> Dict[str, Any]:
        text_lower = text.lower().strip()
        if 'time' in text_lower or 'what time' in text_lower:
            return {'action': 'TIME', 'args': {}}
        if (
            text_lower == 'date'
            or 'what date' in text_lower
            or 'current date' in text_lower
            or 'what is the date' in text_lower
            or "what's the date" in text_lower
            or text_lower == 'today'
            or 'what day' in text_lower
        ):
            return {'action': 'DATE', 'args': {}}
        if 'remember' in text_lower or 'store memory' in text_lower:
            return {'action': 'MEMORY_STORE', 'args': {'text': text}}
        if 'recall' in text_lower or 'search memory' in text_lower:
            return {'action': 'MEMORY_RECALL', 'args': {'query': text, 'limit': 10}}
        return {'action': 'CHAT', 'args': {'message': text}}
    def execute_action(self, action: str, args: Dict[str, Any]) -> str:
        if self.executor_module:
            try:
                if hasattr(self.executor_module, 'execute'):
                    result = self.executor_module.execute(action, args)
                    return self._coerce_result(result)
                if hasattr(self.executor_module, 'execute_action'):
                    result = self.executor_module.execute_action(action, args)
                    return self._coerce_result(result)
            except Exception as e:
                return f"❌ Execution error: {str(e)}"
        return self._simple_execute(action, args)
    def _coerce_result(self, result: Any) -> str:
        if isinstance(result, dict):
            return str(result.get('content') or result.get('response') or result.get('message') or result)
        return str(result)
    def _simple_execute(self, action: str, args: Dict[str, Any]) -> str:
        if action == 'TIME':
            return f"Current time: {now_timestamp()}"
        if action == 'DATE':
            return datetime.now().strftime("%A, %Y-%m-%d")
        if action == 'MEMORY_STORE':
            text = args.get('text', '')
            if memory_system.store(text):
                return '✅ Memory stored'
            return '❌ Failed to store memory'
        if action == 'MEMORY_RECALL':
            query = args.get('query', '')
            limit = args.get('limit', 10)
            results = memory_system.search(query, limit)
            if results:
                return f"Found {len(results)} memories:\n" + "\n".join(
                    f"- {r['text']}" for r in results[:5]
                )
            return 'No memories found'
        try:
            from eli.execution.executor_enhanced import execute as execute_enhanced
            result = execute_enhanced(action, args or {})
            if isinstance(result, dict):
                return result.get("response") or result.get("content") or str(result)
            return str(result)
        except Exception:
            return f"Action {action} not implemented in fallback mode"


class _GUIEngineAdapter:
    """
    Thin adapter that satisfies the AgentOrchestrator's engine interface
    using the GUI's already-loaded GGUF backend.

    Every method the orchestrator calls goes through this adapter so the
    full 11-stage blueprint pipeline runs with zero extra model loads.
    """

    def __init__(self, backend, memory, max_tokens: int,
                 temperature: float, n_ctx: int, inference_lock,
                 cognitive_engine=None):
        self._backend       = backend
        self.memory         = memory          # raw Memory instance
        self._max_tokens    = max_tokens
        self._temperature   = temperature
        self._n_ctx         = n_ctx
        self._lock          = inference_lock  # class-level GUI lock (thread-safe)
        self._ce            = cognitive_engine
        self._in_orchestrator = False
        self.document_rag   = None            # optional RAG — not wired yet
        self.session_id     = f"gui-{int(time.time())}"
        self.user_id        = self._load_user_id()

    # ── helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _load_user_id() -> str:
        try:
            from eli.core.paths import get_paths as _gp
            uid_file = _gp().config_dir / "user_id"
            if uid_file.exists():
                return uid_file.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            pass
        return "local-user"

    def _compact_persona(self) -> str:
        """Return persona trimmed to ≤900 chars for small context windows."""
        p = ELI_SYSTEM_PROMPT or ""
        return p[:900].rstrip() if len(p) > 900 else p

    def _build_persona_handoff_once(self, user_input: str, memory_context: str = "",
                                    bus_result=None, recent_turns=None, working_memory=None) -> str:
        if self._ce is None or not hasattr(self._ce, "_build_persona_handoff_once"):
            return ""
        return self._ce._build_persona_handoff_once(
            user_input=user_input,
            memory_context=memory_context,
            bus_result=bus_result,
            recent_turns=recent_turns,
            working_memory=working_memory,
        )

    # ── AgentOrchestrator interface ───────────────────────────────────────
    # ---- PATH2 COMPATIBILITY SURFACE ----
    # These methods are intentionally retained because the AgentOrchestrator
    # fallback path still calls them through the GUI adapter.
    # Main runtime path is CognitiveEngine-first.
    # Do NOT rename/remove these until orchestrator.py is migrated off them.
    def parse_intent(self, user_input: str, context: list) -> dict:
        try:
            if self._ce is not None and hasattr(self._ce, "parse_intent"):
                return self._ce.parse_intent(user_input, context)
        except Exception as _ce_delegate_err:
            log.debug(f"[GUI] parse_intent CE delegation failed: {_ce_delegate_err}")

        try:
            from eli.execution.router_enhanced import route
            return route(user_input)
        except Exception as e:
            log.debug(f"[ENGINE-ADAPTER] parse_intent fallback: {e}")
            return {"action": "CHAT", "args": {"message": user_input},
                    "confidence": 0.7, "meta": {}}

    def verify_persona_lock(self) -> bool:
        """Delegate to CognitiveEngine when available; fall back to True."""
        try:
            if self._ce is not None and hasattr(self._ce, "verify_persona_lock"):
                return self._ce.verify_persona_lock()
        except Exception as _vpl_err:
            log.debug(f"[GUI] verify_persona_lock CE delegation failed: {_vpl_err}")
        return True

    def repair_persona_lock(self):
        """Delegate to CognitiveEngine when available."""
        try:
            if self._ce is not None and hasattr(self._ce, "repair_persona_lock"):
                self._ce.repair_persona_lock()
        except Exception as _rpl_err:
            log.debug(f"[GUI] repair_persona_lock CE delegation failed: {_rpl_err}")

    def recall_memory_query(self, query: str, limit: int = 12) -> list:
        if self.memory is None:
            return []
        try:
            return self.memory.recall_memory(query, limit=limit) or []
        except Exception:
            return []

    def _dispatch_agent_bus(self, user_input: str, intent: dict) -> str:
        """
        Dispatch the 13-agent AgentBus concurrently.  All agents are pure
        SQLite/filesystem evidence-gatherers (no LLM calls) so this is safe
        to run outside the inference lock.  Returns a compact context block.
        """
        try:
            from eli.cognition.agent_bus import get_bus
            dr = get_bus().dispatch(
                user_input, intent,
                session_id=self.session_id,
                user_id=self.user_id,
            )
            ctx = (dr.memory_context or "").strip()
            log.debug(f"[ENGINE-ADAPTER] AgentBus: agents_used={dr.agents_used} "
                  f"conf={dr.aggregated_confidence:.2f} ctx_chars={len(ctx)}")
            return ctx
        except Exception as e:
            log.debug(f"[ENGINE-ADAPTER] AgentBus dispatch failed: {e}")
            return ""

    def assemble_precise_context(self, user_input: str, working_memory,
                                 short_term_memory, intent: dict,
                                 reasoning_mode: str = None) -> tuple:
        """
        Build (assembled_context, final_prompt) from the orchestrator's
        populated WorkingMemory.  assembled_context becomes the system prompt;
        final_prompt is the raw user query.
        """
        try:
            if self._ce is not None and hasattr(self._ce, "assemble_precise_context"):
                return self._ce.assemble_precise_context(
                    user_input,
                    working_memory=working_memory,
                    short_term_memory=short_term_memory,
                    intent=intent,
                    reasoning_mode=reasoning_mode,
                )
        except Exception as _ce_delegate_err:
            log.debug(f"[GUI] assemble_precise_context CE delegation failed: {_ce_delegate_err}")

        import time as _t

        # Token budget: leave enough room for response + safety margin
        _char_budget = max(3000, self._n_ctx * 3 - self._max_tokens * 5)

        # 1. Persona — compact for small models, full for 7B+
        _use_compact = self._n_ctx <= 8192
        persona = self._compact_persona() if _use_compact else (ELI_SYSTEM_PROMPT or "")

        # 2. User profile
        _user_block = ""
        try:
            from eli.kernel.state import get_user_profile_text as _gup
            _user_block = _gup().strip()
        except Exception:
            pass
        if not _user_block:
            try:
                from eli.kernel.state import get_user_name as _gun
                _n = _gun().strip()
                if _n:
                    _user_block = f"Name: {_n}"
            except Exception:
                pass

        # 3. Retrieved + reranked memory hits (most valuable context)
        hit_lines = []
        for hit in (working_memory.reranked_hits or [])[:8]:
            txt = (hit.get("text") or "").strip()[:260]
            if txt:
                src = hit.get("source", "memory")
                hit_lines.append(f"[{src}] {txt}")

        # 4. Recent conversation turns (short-term memory)
        conv_lines = []
        for turn in (short_term_memory.recent_turns or [])[-8:]:
            role    = turn.get("role", "user")
            content = (turn.get("content") or "").strip()[:280]
            if content:
                conv_lines.append(f"[{role.upper()}]: {content}")

        # 5. Reasoning-mode instruction — private strategy, final answer only.
        try:
            from eli.cognition.reasoning_modes import gui_prompt_prefix_for_mode as _rm_gui_prefix
            _mode_instr = _rm_gui_prefix(reasoning_mode)
        except Exception:
            _mode_instr = ""

        _now  = _t.strftime("%H:%M:%S %Z", _t.localtime())
        _date = _t.strftime("%A %d %B %Y", _t.localtime())

        # ── AgentBus — 13 specialist agents in parallel (no LLM, pure SQLite) ──
        _bus_ctx = ""  # orchestrator already gathered evidence; avoid duplicate AgentBus dispatch

        # Assemble — persona first, then user profile, history, context, rules
        parts = [persona]
        if _user_block:
            parts.append(f"\nUSER PROFILE (personalise every response using this):\n{_user_block}")
        if _mode_instr:
            parts.append(_mode_instr)
        if conv_lines:
            parts.append(
                "\n--- CONVERSATION HISTORY ---\n"
                + "\n".join(conv_lines)
                + "\n--- END HISTORY ---"
            )
        if hit_lines:
            parts.append(
                "\n--- RETRIEVED CONTEXT (memory + knowledge base) ---\n"
                + "\n".join(hit_lines)
                + "\n--- END CONTEXT ---"
            )
        if _bus_ctx:
            # Trim bus context to stay within budget (agents can return verbose data)
            _bus_trimmed = _bus_ctx[:1200] if len(_bus_ctx) > 1200 else _bus_ctx
            parts.append(
                "\n--- AGENT INTELLIGENCE (system state + habits + knowledge graph) ---\n"
                + _bus_trimmed
                + "\n--- END AGENT INTELLIGENCE ---"
            )
        parts.append(
            f"\nCURRENT TIME: {_now}  DATE: {_date}."
            "\n\nRESPONSE DISCIPLINE — obey every reply:"
            "\n- Answer ONLY what was asked. Nothing more."
            "\n- NEVER echo or paraphrase the user's message as your opening."
            "\n- NEVER include unsolicited Memory System / Cognition Pipeline sections."
            "\n- NEVER pad with system internals, file paths, or architecture unless asked."
            "\n- Personal/philosophical questions: answer directly as ELI."
            "\n- Begin your reply with ELI's own voice. Direct. Dry. Accurate."
        )

        assembled = "\n".join(parts)

        # Hard budget guard — truncate oldest history if over budget
        if len(assembled) > _char_budget:
            overshoot = len(assembled) - _char_budget
            if conv_lines:
                conv_lines = conv_lines[1:]     # drop oldest turn
                parts_rebuild = [p for p in parts
                                 if "CONVERSATION HISTORY" not in p]
                if conv_lines:
                    parts_rebuild.insert(-1,
                        "\n--- CONVERSATION HISTORY ---\n"
                        + "\n".join(conv_lines)
                        + "\n--- END HISTORY ---"
                    )
                assembled = "\n".join(parts_rebuild)

        return assembled, user_input

    def generate_from_assembled_prompt(self, prompt: str,
                                       working_memory=None,
                                       reasoning_mode: str = None,
                                       raw_direct: bool = False) -> str:
        """
        Non-streaming inference.  Used by HyDE (raw_direct=True, tiny budget)
        and occasionally for grounded action results.
        """
        try:
            if self._ce is not None and hasattr(self._ce, "generate_from_assembled_prompt"):
                return self._ce.generate_from_assembled_prompt(
                    prompt,
                    working_memory=working_memory,
                    reasoning_mode=reasoning_mode,
                )
        except Exception as _ce_delegate_err:
            log.debug(f"[GUI] generate_from_assembled_prompt CE delegation failed: {_ce_delegate_err}")

        if raw_direct:
            # HyDE hypothetical-doc generation — minimal system, tiny token budget
            system = (
                "You are a knowledge assistant. Write a short factual answer "
                "(2-3 sentences, no roleplay, no filler)."
            )
            max_tok = 72
        else:
            system   = (working_memory.assembled_context
                        if working_memory and working_memory.assembled_context
                        else ELI_SYSTEM_PROMPT)
            max_tok  = self._max_tokens

        messages = [{"role": "system", "content": system},
                    {"role": "user",   "content": prompt}]
        try:
            with self._lock:
                if hasattr(self._backend, "chat"):
                    return self._backend.chat(
                        messages=messages,
                        max_tokens=max_tok,
                        temperature=self._temperature,
                    )
        except Exception as e:
            log.debug(f"[ENGINE-ADAPTER] generate_from_assembled_prompt: {e}")
        return ""

    def generate_stream_from_assembled_prompt(self, prompt: str,
                                              working_memory=None,
                                              reasoning_mode: str = None):
        """
        True streaming inference — yields tokens one at a time.
        Stage 11 of the blueprint pipeline.
        """
        try:
            if self._ce is not None and hasattr(self._ce, "generate_stream_from_assembled_prompt"):
                yield from self._ce.generate_stream_from_assembled_prompt(
                    prompt,
                    working_memory=working_memory,
                    reasoning_mode=reasoning_mode,
                )
                return
        except Exception as _ce_delegate_err:
            log.debug(f"[GUI] generate_stream_from_assembled_prompt CE delegation failed: {_ce_delegate_err}")

        system = (working_memory.assembled_context
                  if working_memory and working_memory.assembled_context
                  else ELI_SYSTEM_PROMPT)
        messages = [{"role": "system", "content": system},
                    {"role": "user",   "content": prompt}]
        try:
            with self._lock:
                if hasattr(self._backend, "chat_stream"):
                    for token in self._backend.chat_stream(
                        messages,
                        max_tokens=self._max_tokens,
                        temperature=self._temperature,
                    ):
                        yield token
                    return
                # Fallback: non-streaming, chunk for display
                response = self._backend.chat(
                    messages=messages,
                    max_tokens=self._max_tokens,
                    temperature=self._temperature,
                )
                for i in range(0, len(response), 8):
                    yield response[i:i + 8]
        except Exception as e:
            log.debug(f"[ENGINE-ADAPTER] generate_stream_from_assembled_prompt: {e}")
            yield f"[Error: {e}]"

    def enqueue_post_response_storage(self, user_input: str, response: str,
                                      intent: dict, command: bool = False,
                                      working_memory=None):
        """
        Blueprint Post-Response: store turns + occasional weight decay.
        """
        if not response or self.memory is None:
            return
        try:
            # Canonical persistence lives in CognitiveEngine.
            # GUI layer must not duplicate chat-turn writes.
            pass
        except Exception as e:
            log.debug(f"[ENGINE-ADAPTER] post_storage failed: {e}")
        # Weight decay — 1 % of responses (amortised cost)
        try:
            import random as _rnd
            if _rnd.random() < 0.01 and hasattr(self.memory, "apply_weight_decay"):
                decayed = self.memory.apply_weight_decay()
                if decayed:
                    log.debug(f"[MEMORY] Weight decay: {decayed} entries aged")
        except Exception:
            pass

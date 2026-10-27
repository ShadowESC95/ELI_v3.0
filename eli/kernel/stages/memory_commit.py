"""Engine stage — memory commit (Stage 12: learning + state commit).

Owns the *write* side of a turn: deciding what is worth remembering, storing the
user/assistant turns, and learning from the executed result. Mixed into
CognitiveEngine; `process()` stays the orchestrator that calls these.
"""
from __future__ import annotations

import re
from typing import Any, Dict

from eli.cognition.output_governor import govern_output
from eli.kernel import request_context as _request_context
from eli.kernel.stages._common import _HAS_GOVERNANCE, should_store_as_memory, _is_autonomous_source  # noqa: F401
from eli.utils.log import get_logger

log = get_logger(__name__)


# Replies that are a report or a recall list rather than ELI talking: what they quote is not ELI's
# offer or question (pending_proposal.read_reply is not run on them).
_REPORT_REPLY_ACTIONS = frozenset({
    "MEMORY_RECALL", "PERSONAL_MEMORY_DEEP_EXPLAIN", "PERSONAL_MEMORY_SUMMARY", "ROUTING_FAULT_EXPLAIN",
    "DETERMINISTIC_INTROSPECTION", "USER_INFO_REPORT", "USER_IDENTITY_SUMMARY", "MEMORY_STATS",
    "LIST_CAPABILITIES", "META_DIAGNOSTIC",
})


def _reply_is_a_report(engine: Any) -> bool:
    act = str(getattr(engine, "_last_response_action", "") or "").upper()
    if not act:
        trace = getattr(engine, "_last_trace", None) or {}
        act = str(trace.get("action") or (trace.get("intent") or {}).get("action") or "").upper()
    return act in _REPORT_REPLY_ACTIONS or act.startswith("EXPLAIN_") or act.endswith(("_STATUS", "_AUDIT"))


class MemoryCommitMixin:
    """Turn-commit stage: store memory / turns, learn from the executed result."""

    def _maybe_store_memory(self, text: str, role: str = "user") -> None:
        if self._in_nested_turn():
            return
        _request_context.note_turn_fact(f"memory_{role}", True)
        if role == "user":
            try:
                from eli.planning.goal_store import capture_task_events
                capture_task_events(text)
            except Exception:
                log.debug("task events not captured", exc_info=True)
        # Never store error strings as memories
        _err_patterns = (
            "gguf streaming failed", "gguf error", "model not ready",
            "requested tokens", "exceed context window", "inference failed",
            "context window", "broker unavailable",
        )
        # FIX: use 'text' instead of undefined 'response'
        _resp_low = (text or "").lower().strip()
        if any(p in _resp_low for p in _err_patterns) and len(_resp_low) < 300:
            return
        # Refuse ELI-authored patterns in user_input
        # These get stored if ELI's response leaked into user_input at any
        # point
        _eli_self_patterns = (
            "i am eli", "i'm eli", "my current reasoning mode",
            "### memory system", "current time (authoritative",
            "good afternoon", "good morning", "haha, glad",
            "sure, let's", "understood, ", "gguf streaming",
            "provider:", "model:", "context size:", "gpu layers:",
            "threads:", "batch:", "gguf loaded:", "confidence:", "agents:",
            "capabilities:", "runtime snapshot failed:"
        )
        # Governance filter: skip storing junk
        if _HAS_GOVERNANCE and not should_store_as_memory(text, role):
            return
        _user_low = (text or "").lower().strip()
        if any(p in _user_low for p in _eli_self_patterns):
            return
        text = (text or "").strip()
        if not text:
            return
        if role == "assistant":
            if len(text.split()) < 4 or text.lower() in {
                "i'm here.", "i'm here", "got it.", "got it", "ok.", "ok"
            }:
                return
            # ELI talking about itself is not knowledge. Stored as a memory it comes back in a later
            # session under "Stored knowledge:" and is recited as fact ("I'm standing by, still glitchy..."
            # answered "morning"). Conversation history already keeps what was said, so only substantive
            # content earns a memory.
            _t_low = text.lower()
            _self_status = (
                # first person + a state word ("I'm good", "I am ready")
                (re.search(r"\b(i'?m|i am|i've|i have been|my)\b", _t_low)
                 and re.search(r"\b(glitch\w*|standing by|running on|same old code|"
                               r"still here|buggy|broken|fine|okay|ok|good|alright|well|"
                               r"ready|online|operational|functioning|state|status|mood|"
                               r"feeling)\b", _t_low))
                # …or an unmistakable self-status phrase even without the pronoun
                # ("Sahns. Still glitchy, still running on the same old code.")
                or re.search(r"\b(still glitch\w*|same old code|standing by|"
                             r"running on the same)\b", _t_low)
            )
            if _self_status:
                log.debug("[MEMORY] not storing ELI's own self-status remark as knowledge")
                return
            # Runtime state is measured live and goes stale; a stored statement of it
            # (or an invented one) comes back later as fact.
            if (re.search(r"\b(n_ctx|context (?:length|window|size)|ctx|gpu|vram|layers?|"
                          r"batch|threads?|ram)\b", _t_low)
                    and re.search(r"\d{2,}", _t_low)):
                log.debug("[MEMORY] not storing ELI's own runtime-state statement as knowledge")
                return
            kind = "assistant_insight"
            source = "assistant"
        else:
            kind = "memory"
            source = "user"

        if role == "user":
            # the user's own words: ChatGPT's pasted "I am making it more precise" was stored as an
            # identity fact about the user
            from eli.cognition.correction_patterns import split_pasted
            user_text = split_pasted(text)[0].lower()
            stripped = user_text.strip().rstrip("?!.")
            if stripped.endswith("?") or user_text.strip().endswith("?"):
                return
            if len(user_text.split()) < 4:
                return
            _question_starts = ("who ", "what ", "where ", "when ", "why ", "how ", "which ",
                                "can you", "could you", "would you", "do you", "does ",
                                "is there", "are there", "tell me", "give me", "show me",
                                "list ", "explain ", "describe ")
            if any(stripped.startswith(q) for q in _question_starts):
                return

            facts = []
            pref_tests = [
                # Explicit preferences
                ("preference",
     r"my (?:favourite|favorite|preferred|fav) (?:colour|color|language|editor|music|food|drink|tool|os|distro|framework|stack|setup|genre|show|film|game|sport) is .{2,60}"),
                ("preference",
     r"i (?:prefer|like|love|use|always use|usually use|tend to use|enjoy) .{3,60}"),
                ("preference",
     r"i(?:'m| am) (?:a big fan of|really into|obsessed with) .{3,60}"),
                # Identity
                ("identity", r"(?:^|[.!] )i am (?:a |an )?(?!not |asking |saying |telling |wondering )[a-z].{3,60}"),
                ("identity", r"my name is (?!not\b|never\b|unknown\b|actually\b|just\b)[a-zA-Z]{2,30}"),
                ("identity", r"call me (?!later|back|when)[a-zA-Z]{2,25}"),
                ("identity", r"i(?:'m| am) (?:a |an )?(?:developer|engineer|designer|student|researcher|teacher|writer|artist|musician|gamer|trader|analyst)\b.{0,60}"),
                # Work / project context
                ("context", r"i(?:'m| am) (?:working|building|developing|making|creating) (?:on |a |an )?(?:project |app |tool |script |system |bot |game )?.{3,60}"),
                ("context", r"i work (?:on|with|in|at|for) .{3,60}"),
                ("context", r"i(?:'ve| have) been (?:working|building|using|running|studying|learning) .{3,60}"),
                ("context", r"my (?:project|app|tool|system|game|bot|script) (?:is|uses|runs|does|needs) .{3,60}"),
                # Hardware / environment
                ("technical", r"i(?:'m| am) (?:using|running|on) .{3,60}"),
                ("technical", r"i have (?:a |an )?(?:\d+\s*gb\b|nvidia|amd|intel|rtx|gtx|rx\s*\d|core\s*i|ryzen|arm).{2,60}"),
                ("technical", r"my (?:gpu|cpu|ram|machine|pc|laptop|server|setup|rig|distro|os|kernel) (?:is|has|runs|uses) .{3,60}"),
                # Software / tech stack
                ("technical", r"i(?:'m| am) using (?:python|javascript|typescript|rust|go|java|c\+\+|kotlin|swift|ruby|php|elixir|haskell|lua).{0,60}"),
                ("technical", r"(?:the project|this project|my project|the app|my app) (?:uses|runs on|is built with|is written in) .{3,60}"),
            ]
            for tag, pattern in pref_tests:
                for m in re.finditer(pattern, user_text):
                    fact = m.group(0).strip()[:200]
                    if len(fact) > 8 and len(fact.split()) >= 3:
                        facts.append((fact, tag))
            tech_tests = [
                ("correction",
     r"(?:actually|correction|to clarify|wait|no)[,:] (?:my |the |it |that |this )\w.{8,100}"),
                ("technical", r"(?:the|that|this) \w+ is (?:approximately|exactly|about|around) [\d\.]+ .{2,40}"),
                ("context",
     r"(?:i just|i recently|i finally|i always|i usually|i never|i sometimes) (?:finished|completed|shipped|deployed|broke|fixed|deleted|migrated|updated).{3,80}"),
            ]
            for tag, pattern in tech_tests:
                for m in re.finditer(pattern, user_text):
                    fact = m.group(0).strip()[:200]
                    if len(fact) > 12 and len(fact.split()) >= 4:
                        facts.append((fact, tag))

            # importance mapping: identity/preference facts are high-salience
            _tag_importance = {
                "identity": 0.88,
                "preference": 0.82,
                "correction": 0.78,
                "context": 0.72,
                "technical": 0.68,
            }

            for fact, tag in facts[:8]:  # raised cap: extract more facts per turn
                try:
                    existing = self.memory.recall_memory(fact[:50], limit=3)
                    already = any(fact[:40].lower() in str(
                        m.get("text", "")).lower() for m in existing)
                    if not already:
                        _imp = _tag_importance.get(tag, 0.65)
                        self.memory.store_memory(
                            fact,
                            tags=[tag, "auto_extracted"],
                            kind=kind,
                            source=source,
                            importance=_imp,
                        )
                        log.debug(f"[MEMORY] Stored {tag} (importance={_imp}): {fact[:60]}")
                        # Pin directly into working memory if importance is high enough
                        try:
                            if self._working_memory and _imp >= 0.65:
                                self._working_memory.pin(
                                    fact, source=f"auto_{tag}", importance=_imp)
                        except Exception:
                            log.debug("suppressed exception", exc_info=True)
                        # Post-response: also index in vector store
                        try:
                            from eli.memory.vector_store import get_vector_store
                            _vs = get_vector_store()
                            if _vs is not None:
                                _vs.add(fact, metadata={"tags": tag, "source": source,
                                                        "importance": _imp})
                        except Exception:
                            log.debug("suppressed exception", exc_info=True)
                except Exception as mem_e:
                    log.debug(f"[MEMORY] store failed: {mem_e}")
        else:
            # Do NOT store ELI's responses in the FTS5-indexed memories table.
            # They are already in conversation_turns and would pollute recall
            # with old/bad answers, causing ELI to repeat or reinforce errors.
            pass

        # --- Periodic working memory persistence (every 10 turns) ---
        try:
            self._wm_turn_counter = getattr(self, "_wm_turn_counter", 0) + 1
            if self._working_memory and self._wm_turn_counter % 3 == 0:
                _wm_db = str(getattr(self.memory, "db_path", "") or "")
                if _wm_db:
                    self._working_memory.persist(_wm_db)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # --- Periodic session digest (every 20 turns) ---
        try:
            if getattr(self, "_wm_turn_counter", 0) % 20 == 0:
                self._generate_session_digest()
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    @staticmethod
    def _in_nested_turn() -> bool:
        """A turn run inside another (multi-question split, followthrough re-run).
        Its parent stores the conversation: the user's real message once, and the
        reply they actually saw. Children storing their own made a sub-question,
        or ELI's own "fetch the latest news" clause, look like something the user
        typed."""
        facts = _request_context.turn_facts_var.get()
        return bool(facts and facts.get("parent_request_id"))

    def _store_user_turn(self, text: str) -> None:
        if not text or self._in_nested_turn():
            return
        try:
            self.memory.add_conversation_turn("user", text, self.session_id, self.user_id)
            _request_context.note_turn_fact("user_stored", True)
        except Exception as e:
            log.debug(f"[COGNITIVE] User turn store failed: {e}")
        try:
            from eli.runtime.diagnostic_patterns import is_user_challenge
            if is_user_challenge(text):
                self._reflection_wake.set()
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    def _store_assistant_turn(self, text: str) -> None:
        if not text or self._in_nested_turn():
            return
        _shown = str(text)      # what the user saw; the stored copy below has closers tidied away
        # Govern here so every storage path (canonical + fastpath bypasses) is covered.
        try:
            text = govern_output(str(text), is_grounded=False)
        except Exception as _gov_err:
            log.debug(f"[COGNITIVE] store-time govern failed: {_gov_err}")
        if not text:
            return
        try:
            from eli.runtime.diagnostic_patterns import should_exclude_turn_from_prompt
            if should_exclude_turn_from_prompt("assistant", text):
                # Benign and by design: an image/status-loop frame is deliberately not stored in
                # conversation context. It isn't a failure; logging it as one polluted the failure
                # log and drove the self-heal loop to "fix" a non-bug. Debug-log only.
                log.debug("[COGNITIVE] skipped storing unsupported assistant image/status frame")
                return
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        try:
            self._last_response = str(text)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        # Capture any follow-up offer ELI just made ("Want me to update the profile?") so a later
        # "yes" re-routes and runs it. Lives at the one store chokepoint every reply path uses. It was
        # gated to WEB_SEARCH only, so conversational offers were never captured and the "yes" was
        # swallowed as chat.
        # Everything the reply leaves open is kept (eli.runtime.pending_proposal.read_reply): the
        # steps it offered, numbered, so "yes" or "do 1-3" carries them out whether a step is an
        # action or something ELI writes, and the question it ended on, so a short reply is read
        # as its answer. An action that asked for confirmation itself (forgetting memories) or
        # for a missing detail keeps its own. What an action's result listed this turn replaces
        # the open listing; a reply that lists nothing leaves the open one as it is.
        try:
            from eli.runtime.pending_proposal import (
                clear_pending_proposal, get_follow_up, listing, read_reply, set_pending_proposal,
            )
            _facts = _request_context.turn_facts_var.get() or {}
            _listed = listing(_facts.get("listed"))
            if _reply_is_a_report(self):
                # A report or a recall list quotes other text; a question in it is not ELI asking
                # (a recalled "what did I ask you to start yesterday?" was kept as ELI's own
                # question). What the report listed stays open to pick from.
                if _listed:
                    set_pending_proposal("", listed=_listed, from_reply=True)
                else:
                    clear_pending_proposal()
            elif not _facts.get("proposal_set_by_action"):
                _open = read_reply(_shown, offers_only=bool(_facts.get("executed_actions")))
                if _open["items"] or _open["question"] or _listed:
                    set_pending_proposal("", items=_open["items"], question=_open["question"],
                                         listed=_listed, from_reply=True)
                else:
                    clear_pending_proposal()
            elif _listed:
                # The action left its own offer open; add what it listed beside it.
                _kept = get_follow_up() or {}
                set_pending_proposal(str(_kept.get("command") or ""), str(_kept.get("summary") or ""),
                                     items=_kept.get("items"), question=str(_kept.get("question") or ""),
                                     awaiting=_kept.get("awaiting"), listed=_listed, from_reply=True)
        except Exception as _prop_err:
            log.debug(f"[COGNITIVE] offer-capture skipped: {_prop_err}")
        try:
            self.memory.add_conversation_turn("assistant", text, self.session_id, self.user_id)
            _request_context.note_turn_fact("assistant_stored", True)
        except Exception as e:
            log.debug(f"[COGNITIVE] Assistant turn store failed: {e}")

    def _learn_from_result(
        self, intent: Dict[str, Any], result: Dict[str, Any], source: str = "user") -> None:
        action = str(intent.get("action") or "").upper()
        args = intent.get("args", {}) or {}
        ok = bool((result or {}).get("ok", True))
        meta = intent.get("meta", {}) or {}
        # ELI's own automation (habit firing, scheduled tasks, ...) re-running an action must
        # not feed habit_events — detect_habits() mines that table for recurring USER routines,
        # and a self-triggered run would let ELI's automation reinforce itself as if it were
        # fresh, independent evidence of a pattern.
        autonomous = _is_autonomous_source(source)

        try:
            self.memory.log_learning_event(
                "command_result",
                input_text=str(intent.get("user_input") or args.get("message") or args.get("query") or ""),
                output_text=str((result or {}).get("content") or (result or {}).get("response") or ""),
                action=action,
                outcome="ok" if ok else "failed",
                reward=1.0 if ok else -1.0,
                metadata={
                    "args": args,
                    "matched_by": meta.get("matched_by"),
                    "result_error": (result or {}).get("error"),
                    "source": source,
                },
            )
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        if not autonomous:
            try:
                self.memory.log_habit_event(
                    "command_result",
                    {
                        "action": action,
                        "args": args,
                        "ok": ok,
                        "matched_by": meta.get("matched_by"),
                        "source": "cognitive_engine",
                    },
                )
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        try:
            from eli.runtime.evidence_ledger import record_event as _eli_record_event
            _eli_record_event(
                "command_result",
                source=f"cognitive_engine.learn_from_result.{source}",
                action=action,
                subject=str(args.get("path") or args.get("target") or args.get("name") or args.get("topic") or ""),
                content=str((result or {}).get("content") or (result or {}).get("response") or (result or {}).get("error") or ""),
                payload={
                    "args": args,
                    "matched_by": meta.get("matched_by"),
                    "result": result or {},
                },
                severity="info" if ok else "error",
                outcome="ok" if ok else "failed",
                confidence=float(intent.get("confidence") or 0.0) if isinstance(intent, dict) else None,
                reusable=True,
                session_id=str(getattr(self, "session_id", "") or ""),
                user_id=str(getattr(self, "user_id", "") or ""),
            )
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        if autonomous:
            return   # below this point is app/file usage — real routine-clustering input

        if action == "OPEN_APP" and ok:
            name = args.get("name") or args.get("target") or args.get("app")
            cmd = (result or {}).get("cmd") or (result or {}).get("command") or args.get("cmd") or name
            method = (result or {}).get("method") or meta.get("matched_by") or "cognitive_engine"
            if name:
                self.memory.store_app_cmd(name, cmd or name, method)
                self.memory.log_habit_event(
                    "app_launch",
                    {"app": name, "cmd": cmd or name, "method": method, "success": True},
                )
        elif action in {"OPEN_FILE_SYSTEM", "LIST_DIR", "READ_FILE"} and ok:
            path = args.get("path")
            if path:
                self.memory.log_habit_event(action.lower(), {"path": path, "success": True})

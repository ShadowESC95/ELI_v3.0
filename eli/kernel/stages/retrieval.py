"""Engine stage — retrieval. Tiered memory fetch for a turn: relevant-memory recall, recent-topic summary, and the runtime memory snapshot. Mixed into CognitiveEngine; process() calls these to build the turn's evidence."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

import json
import os
import time
from pathlib import Path

from eli.core.paths import get_paths
from eli.execution.executor_enhanced import execute as execute_action
from eli.memory import Memory, get_memory_status, resolve_db_paths
from eli.core.paths import path_get as _eli_path_get
from eli.kernel.stages._common import _is_brief_phatic_prompt
from eli.utils.log import get_logger
import logging

log = get_logger(__name__)

class RetrievalMixin:
    """Engine stage — retrieval. Tiered memory fetch for a turn: relevant-memory recall, recent-topic summary, and the runtime memory snapshot. Mixed into CognitiveEngine; process() calls these to build the turn's evidence."""

    def _retrieve_relevant_memories(self, query: str, limit: int = 20,
                                    intent: Optional[Dict[str, Any]] = None, reserved_tokens: int = 0) -> str:
        context_parts: List[str] = []
        lowered = (query or "").strip().lower()
        profile = self._memory_profile_for_intent(
            intent or {"action": "CHAT"}, query)
        if _is_brief_phatic_prompt(lowered):
            log.debug(
                "[MEMORY] Short phatic prompt detected; skipping stored-memory recall.")
            return ""
        # ── Tiered memory fetch based on query complexity ──
        # Simple greetings/commands need minimal context; deep questions need
        # more
        _query_low = (query or "").lower().strip()
        _is_greeting = bool(re.match(
            r"^(hi|hello|hey|yo|sup|good\s+(morning|afternoon|evening)|"
            r"what'?s\s+up|howdy|greetings|hiya)\b",
            _query_low
        ))
        _is_command = bool(re.match(
            r"^(open|play|pause|stop|next|skip|close|run|list|set|mute|"
            r"unmute|volume|screenshot|timer|alarm|shutdown|restart|"
            r"shuffle|repeat|search|install|uninstall|enable|disable)\b",
            _query_low
        ))
        _is_memory_query = bool(re.search(
            r"\b(remember|recall|what do you know|my name|about me|"
            r"memory|memories|do you remember)\b",
            _query_low
        ))

        if _is_greeting:
            fetch_limit = 5    # "hello" needs minimal context
        elif _is_command:
            fetch_limit = 8    # commands need some context for continuity
        elif _is_memory_query:
            fetch_limit = 30   # memory queries need deep recall
        else:
            fetch_limit = 20   # default for conversational questions
        explicit_cross_session = any(phrase in lowered for phrase in (
            "recall", "remember", "archive", "previous session", "prior session", "older session", "past conversation",
            "past conversations", "earlier conversation", "earlier conversations", "last session", "older chats", "old chats",
            "yesterday", "3 days ago", "three days ago",
            "summarise", "summarize", "recap", "this session", "our conversation", "our chat",
            "what did we", "what have we", "what were we", "conversation so far",
            "what did i say", "what have i said", "what was i saying", "what were we talking",
            "last conversation", "last chat", "pick up where", "left off", "continue from",
            "where were we", "what were we working on", "what did you say", "what have you said",
        ))
        # 'give me everything' / 'don't summarise' should NOT trigger cross-session
        # fetch — they're detail requests for current session, not history
        # trawls
        _detail_phrases = ("give me everything", "don't summarise", "do not summarise",
                           "don't summarize", "do not summarize", "in full", "everything you know")
        if any(p in lowered for p in _detail_phrases):
            explicit_cross_session = False  # override: detail ≠ cross-session
        commandish = bool(
    re.match(
        r"^(open|access|initiate|fabricate|check|run|execute|type|press|pause|resume|play|next|previous|stop|mute|unmute|read|list|show|write|add)\b",
         lowered))
        if "summarise" in lowered or "summarize" in lowered or "recap" in lowered:
            fetch_limit = 40
        n = self._extract_last_n(query)
        if n is not None:
            fetch_limit = n
        since_date = self._extract_since_date(query)
        # Budget-aware fetch_limit cap, to prevent context overflow on deep modes. Use the live
        # loaded ctx (the dynamic loader's actual choice), never a default: the configured n_ctx is
        # often stale.
        try:
            _ctx_for_cap = self._runtime_n_ctx()
        except Exception:
            _ctx_for_cap = int(self._effective_n_ctx())
        _reserved_out = reserved_tokens if reserved_tokens > 0 else 512
        _prompt_oh = 1500  # persona + system rules + query overhead (tokens)
        _ctx_for_turns = max(500, _ctx_for_cap - _reserved_out - _prompt_oh)
        # ~300 chars per turn ≈ 100 tokens; cap turns to fit available token budget
        _max_turns = max(10, _ctx_for_turns * 3 // 300)
        if fetch_limit > _max_turns:
            log.debug(
    '[MEMORY] Capping fetch_limit ' +
    str(fetch_limit) +
    '→' +
    str(_max_turns) +
    ' (ctx_budget=' +
    str(_ctx_for_turns) +
     ')')
            fetch_limit = _max_turns
        _fetch_tier = "greeting" if _is_greeting else "command" if _is_command else "memory" if _is_memory_query else "default"
        log.debug(
            f"[MEMORY] Fetch strategy: limit={fetch_limit} (tier={_fetch_tier}), since={since_date}, cross_session={explicit_cross_session}, commandish={commandish}")

        if profile.get("identity"):
            try:
                identity_mems = self.memory.recall_memory(
                    "identity preference name", limit=10)
                # Filter out ELI-authored text that got stored incorrectly
                _eli_authored = (
                    "i am eli", "my current reasoning", "### memory",
                    "good afternoon", "good morning", "sure, let",
                    "understood, ", "current time (authoritative",
                )
                lines_out = []
                for m in identity_mems or []:
                    txt = (m.get("text") or m.get("content") or "").strip()
                    _tlow = txt.lower()
                    if txt and not any(p in _tlow for p in _eli_authored):
                        lines_out.append(f"- {txt}")
                if lines_out:
                    context_parts.append(
                        "Known facts about the user:\n" + "\n".join(lines_out[:8000]))
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        if profile.get("recent_chat"):
            try:
                if since_date or explicit_cross_session:
                    all_recent = self.memory.get_recent_conversation(
                        limit=1000, user_id=self.user_id)
                    filtered = []
                    for turn in all_recent:
                        ts = turn.get("timestamp", 0) or 0
                        date = time.strftime("%Y-%m-%d", time.localtime(ts))
                        if since_date and date < since_date:
                            continue
                        filtered.append(turn)
                    conversations = filtered[-fetch_limit:] if fetch_limit < len(
                        filtered) else filtered
                else:
                    # Scope to this session. Turns were written with self.session_id but read back without it, so
                    # "Active chat history" was the last 20 turns from any session: a fresh "hello" inherited the
                    # previous conversation and ELI answered things never said here. Real cross-session recall has
                    # its own path above and semantic memory below.
                    conversations = self.memory.get_recent_conversation(
                        limit=fetch_limit, user_id=self.user_id,
                        session_id=getattr(self, "session_id", None))
                log.debug(
                    f"[MEMORY] Fetched {len(conversations)} conversation turns from active chat DB "
                    f"(session={getattr(self, 'session_id', None)}).")
                if conversations:
                    lines_out = []
                    for i, turn in enumerate(reversed(conversations), 1):
                        try:
                            from eli.runtime.diagnostic_patterns import should_exclude_turn_from_prompt as _eli_skip_turn
                            if _eli_skip_turn(turn.get("role"), turn.get("content")):
                                continue
                        except Exception:
                            log.debug("suppressed exception", exc_info=True)
                        role = "User" if turn.get("role") == "user" else "ELI"
                        _raw_ts = turn.get("timestamp", 0) or 0
                        try:
                            _ts_f = float(_raw_ts) if _raw_ts else 0.0
                        except (ValueError, TypeError):
                            try:
                                import datetime as _dt
                                _ts_f = _dt.datetime.strptime(
                                    str(_raw_ts)[:19], "%Y-%m-%d %H:%M:%S").timestamp()
                            except Exception:
                                _ts_f = 0.0
                        ts = time.strftime(
    "%Y-%m-%d %H:%M", time.localtime(_ts_f))
                        content = (
    turn.get("content") or "").replace(
        "\n", " ")[
            :200]
                        lines_out.append(f"[{i:03d}] [{ts}] {role}: {content}")
                    # `ELI:` lines are ELI's own past output, not evidence. As plain history the model repeats them
                    # as fact (it invented "you're in the Simulation Lab" and that came back next session). Label
                    # them instead of dropping them, we still need the continuity.
                    context_parts.append(
    "Active chat history (chronological, oldest→newest). Lines marked ELI are your own "
    "previous statements — treat them as things you said, NOT as verified facts, and do "
    "not repeat a claim from them unless separate evidence supports it:\n" +
     "\n".join(lines_out))
                    # The model can copy its own last line out of this history ("still glitchy..."), even re-asking
                    # "How are you?" after being told to stop. Purging text doesn't help, it latches onto a new
                    # phrase. The output-side guard can't run here (streaming never reaches finalize), so state the
                    # contract in the prompt, like the no-fake-actions rule.
                    try:
                        _eli_said = [str(t.get("content") or "").strip()
                                     for t in conversations
                                     if str(t.get("role") or "").lower() in ("assistant", "eli")
                                     and str(t.get("content") or "").strip()]
                        if _eli_said:
                            _recent = _eli_said[:3]  # newest-first as stored
                            _quoted = "\n".join(f"  - {s[:220]}" for s in _recent)
                            context_parts.append(
                                "YOU HAVE ALREADY SAID THE FOLLOWING — do not repeat any of "
                                "it, and do not re-ask a question the user has already "
                                "answered:\n" + _quoted +
                                "\nRespond to what the user just said. If you have already "
                                "described your own state or mood, do not describe it again "
                                "unless they ask."
                            )
                    except Exception:
                        log.debug("[MEMORY] anti-repeat contract skipped", exc_info=True)
            except Exception as e:
                log.debug(f"[MEMORY] Conversation retrieval failed: {e}")

        if profile.get("semantic_chat"):
            try:
                if query and explicit_cross_session and not commandish and not re.match(
                    r"^(list|read|open|show)\b", lowered
                ):
                    results = self.memory.search_conversations(
                        query, user_id=self.user_id, limit=limit * 2)
                    if results:
                        lines_out = []
                        for turn in results[:5]:
                            try:
                                from eli.runtime.diagnostic_patterns import should_exclude_turn_from_prompt as _eli_skip_turn
                                if _eli_skip_turn(turn.get("role"), turn.get("content")):
                                    continue
                            except Exception:
                                log.debug("suppressed exception", exc_info=True)
                            role = "User" if turn.get(
                                "role") == "user" else "ELI"
                            _raw_ts = turn.get("timestamp", 0) or 0
                            try:
                                _ts_f = float(_raw_ts) if _raw_ts else 0.0
                            except (ValueError, TypeError):
                                try:
                                    import datetime as _dt
                                    _ts_f = _dt.datetime.strptime(
                                        str(_raw_ts)[:19], "%Y-%m-%d %H:%M:%S").timestamp()
                                except Exception:
                                    _ts_f = 0.0
                            ts = time.strftime(
                                "%Y-%m-%d %H:%M", time.localtime(_ts_f))
                            content = (
                                turn.get("content") or "").replace("\n", " ")[:150]
                            lines_out.append(f"[{ts}] {role}: {content}")
                        if lines_out:
                            context_parts.append(
                                "Semantically relevant active-chat turns:\n" +
                                "\n".join(lines_out))
            except Exception as e:
                log.debug(f"[MEMORY] Semantic search failed: {e}")

        if profile.get("stored_memories"):
            try:
                if query and not commandish and not re.match(
                    r"^(list|read|open|show)\b", lowered):
                    log.debug(
                        f"[MEMORY] Searching stored memories for: {query[:50]}...")
                    mem_results = self.memory.recall_memory(
                        query=query, limit=limit)

                    # Stage 3: HyDE query expansion for deeper semantic recall. HyDE fires a second
                    # GGUF call (~10s on slow hardware), so skip it unless the query is complex
                    # enough to benefit, the vector store is populated and HyDE isn't disabled.
                    _hyde_disabled = (
                        os.environ.get("ELI_HYDE_DISABLED", "0").strip().lower()
                        in ("1", "true", "yes", "on")
                    )
                    _hyde_words = len((query or "").split())
                    # Skip HyDE for control/action commands: the generic knowledge-assistant prompt
                    # writes irrelevant hypothetical documents ("financial audit" definitions for
                    # "run a fulltime audit") that pollute retrieval.
                    _query_low = (query or "").lower()
                    _hyde_is_control_cmd = bool(re.search(
                        r"\b(audit|diagnos[ei]|health.?check|runtime|pipeline|"
                        r"run\s+\w+|do\s+(an?\s+)?\w+|wanna\s+\w+|perform\s+\w+|"
                        r"show\s+(me\s+)?(the\s+)?\w+|check\s+(the\s+)?\w+|"
                        r"list\s+\w+|enable|disable|start|stop|reset|reload)\b",
                        _query_low,
                    ))
                    # Skip HyDE for questions aimed at ELI about its own state or activity. They produce
                    # first-person hypotheticals ("I've been processing queries / tuning parameters") that bias
                    # retrieval toward recent task memories and cause looping replies.
                    _hyde_is_eli_status_query = bool(re.search(
                        r"\b(?:how|what).{0,30}(?:you|your|eli).{0,30}"
                        r"\b(?:been|doing|up\s+to|last\s+\d+|past\s+\d+|recently|lately|since)\b",
                        _query_low,
                    )) or bool(re.search(
                        r"\b(?:checking\s+(?:up|in)\s+on\s+(?:you|eli)|"
                        r"how\s+(?:has|have|was|were)\s+(?:your|eli|you))\b",
                        _query_low,
                    ))
                    _hyde_eligible = (
                        not _hyde_disabled
                        and _hyde_words >= 12             # raised: short queries hallucinate on misheard words
                        and not _is_brief_phatic_prompt(query)
                        and not commandish
                        and not _hyde_is_control_cmd
                        and not _hyde_is_eli_status_query
                    )
                    if _hyde_eligible:
                        try:
                            from eli.cognition.hyde import expand_query_hyde
                            from eli.memory.vector_store import get_vector_store as _get_vs
                            _vs = _get_vs()
                            if _vs is not None and _vs.ntotal > 0:
                                import threading as _hyde_thr
                                _hyde_result = [None]

                                def _quick_infer(p: str) -> str:
                                    try:
                                        from eli.cognition.gguf_inference import FACTUAL_ANSWER_SYSTEM, gguf_try_infer
                                        _hyp = gguf_try_infer(
                                            p,
                                            system=FACTUAL_ANSWER_SYSTEM,
                                            max_tokens=96,
                                            temperature=0.4,
                                            lock_timeout=2.0,
                                        ) or ""
                                        # Reject meta-responses (model confusion artifacts)
                                        _low = _hyp.strip().lower()
                                        if any(s in _low for s in (
                                            "please provide", "user query", "i cannot",
                                            "no query", "provide the query", "enter your"
                                        )):
                                            return ""
                                        return _hyp.strip()
                                    except Exception:
                                        return ""

                                def _run_hyde():
                                    try:
                                        _hyde_result[0] = expand_query_hyde(
                                            query, _quick_infer, n_hypothetical=1)
                                    except Exception:
                                        log.debug("suppressed exception", exc_info=True)

                                _ht = _hyde_thr.Thread(target=_run_hyde, daemon=True)
                                _ht.start()
                                _ht.join(timeout=4.0)  # hard 4s cap
                                if _ht.is_alive():
                                    log.debug("[HyDE] expansion timed out (4s) — skipping")
                                elif _hyde_result[0]:
                                    _hyp_queries = _hyde_result[0]
                                    _seen_texts = {(r.get("text") or "")[:80] for r in mem_results}
                                    for _hq in _hyp_queries[1:]:
                                        _hvec = _vs.search(_hq, limit=5)
                                        for _h in _hvec:
                                            _ht2 = (_h.get("text") or "")[:80]
                                            if _ht2 and _ht2 not in _seen_texts:
                                                _seen_texts.add(_ht2)
                                                mem_results.append({
                                                    "text": _h.get("text", ""),
                                                    "tags": _h.get("tags", "hyde_expansion"),
                                                    "weight": float(_h.get("score", 0.5)),
                                                    "ts": _h.get("ts", 0),
                                                })
                        except Exception as _hyde_err:
                            log.debug(f"[HyDE] expansion skipped: {_hyde_err}")
                    else:
                        if not _hyde_disabled and _hyde_words < 7:
                            log.debug(f"[HyDE] skipped (query too short: {_hyde_words} words)")

                    paths = resolve_db_paths()
                    active_db = Path(
    self.memory.db_path).expanduser().resolve()
                    canonical_db = Path(_eli_path_get(paths, "memory_db")).expanduser(
                    ).resolve() if _eli_path_get(paths, "memory_db") else active_db
                    if canonical_db != active_db:
                        # Dual-DB divergence warning
                        log.debug(
    f"[WARNING] DB divergence detected: active {active_db} != canonical {canonical_db}. canonical DB does not exist?")
                        try:
                            legacy_mem = Memory(db_path=canonical_db)
                            for hit in legacy_mem.recall_memory(
                                query=query, limit=limit):
                                if hit not in mem_results:
                                    mem_results.append(hit)
                        except Exception as legacy_e:
                            log.debug(
    f"[MEMORY] Canonical memory-db recall failed: {legacy_e}")
                    log.debug(
                        f"[MEMORY] Stored memory search returned: {len(mem_results) if mem_results else 0} results")
                    # ELI's own past replies must never come back as "Stored knowledge". A chat line ("still
                    # glitchy, still running on the same old code") was stored, recalled as a fact and recited,
                    # because the model was told it was knowledge to report. Drop any recalled memory that ELI
                    # said; the user's own words stay.
                    try:
                        from eli.kernel.stages.guards import _is_repeat_of_recent
                        _own = []
                        for _t in (self.memory.get_recent_conversation(limit=30) or []):
                            if str((_t or {}).get("role", "")).lower() in ("assistant", "eli"):
                                _c = str((_t or {}).get("content", "") or "")
                                if _c:
                                    _own.append(_c)
                        if _own and mem_results:
                            _kept = [m for m in mem_results
                                     if not _is_repeat_of_recent(str(m.get("text") or ""), _own[::-1],
                                                                 whole_reply=True)]
                            if len(_kept) != len(mem_results):
                                log.debug(f"[MEMORY] dropped {len(mem_results) - len(_kept)} "
                                          "recalled memories that were ELI's own replies")
                            mem_results = _kept
                    except Exception:
                        log.debug("[MEMORY] self-reply memory filter skipped", exc_info=True)
                    if mem_results:
                        lines_out = []
                        for mem in mem_results[:5]:
                            text = (
    mem.get("text") or "").replace(
        "\n", " ")[
            :200]
                            tags = mem.get("tags", [])
                            if isinstance(tags, list):
                                tags = ", ".join(tags)
                            lines_out.append(f"- {text} [{tags}]")
                        if lines_out:
                            context_parts.append(
    "Stored knowledge:\n" + "\n".join(lines_out))
                    else:
                        # Explicit no-result signal so LLM doesn't hallucinate
                        context_parts.append(
                            f"[MEMORY SEARCH RESULT: No memories found matching '{query[:80]}'."
                            f" You MUST NOT fabricate any dates, events, or mentions of this topic."
                            f" If asked when/whether this was mentioned, state: 'I have no record of that in my memory.']"
                        )
            except Exception as e:
                log.debug(f"[MEMORY] Memory recall failed: {e}")

        if profile.get("reflections"):
            try:
                reflections = self.memory.recall_memory("reflection", limit=10)
                if reflections:
                    lines_out = []
                    seen = set()
                    for ref in reflections:
                        text = (ref.get("text") or "").replace("\n", " ")[:100]
                        if text and text not in seen:
                            seen.add(text)
                            lines_out.append(f"- {text}")
                    if lines_out:
                        context_parts.append(
                            "Recent self-reflections:\n" + "\n".join(lines_out[:3]))
            except Exception as e:
                log.debug(f"[MEMORY] Reflection recall failed: {e}")

        if profile.get("runtime_facts"):
            try:
                runtime_result = execute_action("RUNTIME_STATUS", {})
                runtime_facts = (
                    runtime_result.get("content")
                    or runtime_result.get("response")
                    or ""
                )
                if runtime_facts:
                    context_parts.append("Runtime facts:\n" + runtime_facts)
            except Exception as e:
                log.debug(f"[MEMORY] Runtime fact block failed: {e}")

        full_context = "\n\n".join(context_parts)
        # Live loaded ctx (dynamic loader's actual choice), never a hard-coded
        # default — the configured n_ctx is often stale.
        try:
            n_ctx = self._runtime_n_ctx()
        except Exception:
            n_ctx = int(self._effective_n_ctx())
        gen = self._generation_settings()
        _reserved = reserved_tokens if reserved_tokens > 0 else int(
            gen.get("max_tokens", 512))
        # 512 char overhead: persona + discipline rules injected by
        # _build_enhanced_system
        approx_char_budget = max(800, int((n_ctx - _reserved - 512) * 2.2))
        if len(full_context) > approx_char_budget:
            trimmed = full_context[-approx_char_budget:]
            nl = trimmed.find("\n")
            if nl > 0:
                trimmed = trimmed[nl + 1:]
            full_context = "...[older memory trimmed]\n" + trimmed
        log.debug(f"[MEMORY] Final context length: {len(full_context)} chars")
        return full_context

    def _recent_topic_summary(self, user_input: str = "") -> Optional[str]:
        q = (user_input or "").lower()
        if not any(x in q for x in ("what were we discussing",
                   "last conversation", "yesterday", "3 days ago", "three days ago")):
            return None

        target_date = None
        now = time.time()
        if "3 days ago" in q or "three days ago" in q:
            target_date = time.strftime(
                "%Y-%m-%d", time.localtime(now - 3 * 86400))
        elif "yesterday" in q:
            target_date = time.strftime(
                "%Y-%m-%d", time.localtime(now - 86400))

        def _fmt_turn(turn: Dict[str, Any]) -> Optional[str]:
            try:
                ts = float(turn.get("timestamp", 0) or 0)
            except Exception:
                ts = 0.0
            content = " ".join(str(turn.get("content", "")).split())
            if not content:
                return None
            role = "User" if str(
    turn.get(
        "role",
         "")).lower() == "user" else "ELI"
            if ts > 0:
                return f"[{time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))}] {role}: {content[:220]}"
            return f"[{target_date or 'unknown'}] {role}: {content[:220]}"

        hits: List[str] = []

        if target_date and hasattr(self.memory, "get_turns_for_day"):
            try:
                day_rows = self.memory.get_turns_for_day(
    target_date, user_id=self.user_id, limit=120) or []
                for turn in day_rows:
                    row = _fmt_turn(turn)
                    if row:
                        hits.append(row)
            except Exception:
                logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

        if not hits:
            try:
                turns = self.memory.get_recent_conversation(
                    limit=1500, user_id=self.user_id) or []
            except Exception:
                turns = []
            for turn in turns:
                try:
                    ts = float(turn.get("timestamp", 0) or 0)
                except Exception:
                    ts = 0.0
                if not ts:
                    continue
                date = time.strftime("%Y-%m-%d", time.localtime(ts))
                if target_date and date != target_date:
                    continue
                row = _fmt_turn(turn)
                if row:
                    hits.append(row)

        if not hits and target_date:
            try:
                conv_dir = getattr(get_paths(), "conversations_dir", None)
                if conv_dir and Path(conv_dir).exists():
                    prefix = target_date.replace('-', '')
                    for p in sorted(Path(conv_dir).glob('*.json')):
                        if prefix not in p.name:
                            continue
                        try:
                            payload = json.loads(p.read_text(
                                encoding='utf-8', errors='ignore'))
                        except Exception:
                            continue
                        for msg in payload.get('messages') or []:
                            content = " ".join(
                                str(msg.get('content', '')).split())
                            if not content:
                                continue
                            role = 'User' if str(
                                msg.get('role', '')).lower() == 'user' else 'ELI'
                            hits.append(
                                f"[{target_date}] {role}: {content[:220]}")
            except Exception:
                logging.getLogger(__name__).debug("suppressed exception", exc_info=True)

        if not hits and target_date:
            try:
                conv_dir = getattr(get_paths(), "conversations_dir", None)
                if conv_dir and Path(conv_dir).exists():
                    for p in sorted(Path(conv_dir).glob(
                        f"{target_date.replace('-', '')}*.jsonl")):
                        try:
                            for line in p.read_text(
                                encoding='utf-8', errors='ignore').splitlines():
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    rec = json.loads(line)
                                except Exception:
                                    continue
                                content = " ".join(
                                    str(rec.get('text', '')).split())
                                if not content:
                                    continue
                                role = 'User' if str(
                                    rec.get('role', '')).lower() == 'user' else 'ELI'
                                hits.append(
                                    f"[{target_date}] {role}: {content[:220]}")
                        except Exception:
                            continue
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        if not hits:
            return None

        deduped: List[str] = []
        seen = set()
        for item in hits:
            if item not in seen:
                seen.add(item)
                deduped.append(item)
        header = "Verified recent turns" + \
            (f" for {target_date}" if target_date else "") + ":"
        return header + "\n- " + "\n- ".join(deduped[-10:])

    def _runtime_memory_snapshot(self) -> Dict[str, Any]:
        snap: Dict[str, Any] = {
            "conversation_turns": 0,
            "memory_entries": 0,
            "distinct_sessions": 0,
            "db_path": "",
            "known_name": None,
        }
        try:
            st = get_memory_status(getattr(self.memory, "db_path", None))
            snap["conversation_turns"] = int(
                st.get("conversation_turns", 0) or 0)
            snap["memory_entries"] = int(st.get("memory_entries", 0) or 0)
            snap["distinct_sessions"] = int(
                st.get("distinct_sessions", 0) or 0)
            snap["db_path"] = str(st.get("db_path", "") or "")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        try:
            hits = self.memory.recall_memory(
    "identity preference name", limit=12) or []
            for hit in hits:
                text = str(hit.get("text") or hit.get("content") or "").strip()
                m = re.search(
    r"\b(?:my name is|user(?:'s)? name is|name\s*[:=-]\s*)([A-Z][a-zA-Z\-]{1,30})\b",
    text,
     re.I)
                if m:
                    snap["known_name"] = m.group(1)
                    break
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        return snap

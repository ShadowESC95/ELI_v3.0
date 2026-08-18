"""Stage: prompt + evidence + persona-brief assembly.

Builds the enhanced system prompt, gathers and attaches executor evidence,
assembles the grounded evidence context and the precise working-memory context,
and splices the persona hand-off. Composed onto CognitiveEngine as a mixin, so
the methods here keep reading engine state through `self` exactly as before.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from eli.core.paths import get_paths
from eli.execution.executor_enhanced import execute as execute_action
from eli.memory import resolve_db_paths
from eli.cognition.context_synthesiser import build_persona_handoff
from eli.kernel.stages._common import _eli_bus_first_ok_result, _is_brief_phatic_prompt
from eli.kernel.stages.persona_text import (
    _eli_is_subjective_opinion_prompt,
    _eli_sanitize_identity_context_block,
    _eli_scrub_recent_turns_for_identity,
    _load_persona_text,
)
from eli.utils.log import get_logger

log = get_logger(__name__)


class PromptAssemblyMixin:
    @staticmethod
    def _cap_text(text: str, budget_chars: int, label: str = "evidence") -> str:
        """Bound an evidence block to `budget_chars` characters. When the cap
        fires, append a marker so the model knows the source was truncated.
        Phase 6: prevents prompt bloat from pushing tokens past n_ctx and
        starving the generation phase (which produced empty/1-char outputs)."""
        if text is None:
            return ""
        s = str(text)
        if len(s) <= budget_chars:
            return s
        kept = s[:budget_chars].rstrip()
        log.debug(
            f"[COGNITIVE] evidence truncated for ctx fit: label={label!r} "
            f"{len(s)} → {budget_chars} chars",
        )
        return kept + f"\n[…{label} truncated to {budget_chars} chars to fit context window]"

    def _build_evidence_prompt(self, user_input: str, bus_result) -> str:
        # Per-block byte budgets (Phase 6). Total evidence ceiling ≈ 14 KB; with
        # persona (~2 KB) and instructions (~0.5 KB) the assembled prompt stays
        # comfortably under 16 KB on a 16384-ctx model.
        BUDGET_MEMORY = 4096
        BUDGET_SNIPPETS = 2048
        BUDGET_REFLECTIONS = 1024
        BUDGET_HABITS = 512
        BUDGET_EXECUTOR = 4096

        parts = []
        try:
            persona = self._compact_persona()
        except Exception:
            persona = self.get_persona()
        parts.append(f"You are ELI. {persona}")
        if bus_result.agents_used:
            parts.append(
    "Agents that contributed: " +
    ", ".join(
        bus_result.agents_used))

        if bus_result.memory_context:
            parts.append(
                "=== Memory Context ===\n"
                + self._cap_text(bus_result.memory_context, BUDGET_MEMORY, "memory_context")
            )

        for r in bus_result.agent_results:
            if r.agent == "file_code" and r.ok:
                snippets = r.data.get("snippets", [])
                if snippets:
                    parts.append(
                        "=== Source Code Evidence ===\n"
                        + self._cap_text("\n".join(snippets[:10]), BUDGET_SNIPPETS, "source_snippets")
                    )
            if r.agent == "reflection" and r.ok:
                insights = r.data.get("insights", [])
                if insights:
                    parts.append(
                        "=== Recent Reflections ===\n"
                        + self._cap_text("\n".join(insights[:5]), BUDGET_REFLECTIONS, "reflections")
                    )
            if r.agent == "habit" and r.ok:
                rules = r.data.get("rules", [])
                if rules:
                    parts.append(
                        "=== Habit Rules ===\n"
                        + self._cap_text(
                            "\n".join(rule.get("name", "") for rule in rules[:5]),
                            BUDGET_HABITS,
                            "habits",
                        )
                    )

        try:
            _ev_action = (bus_result.intent_action or '').upper()
            if _ev_action and _ev_action != 'CHAT':
                _ev_result = execute_action(_ev_action, {'query': user_input})
                _ev_text = (
    _ev_result or {}).get('content') or (
        _ev_result or {}).get('response') or ''
                if _ev_text:
                    parts.append(
                        '=== Executor Result (' + _ev_action + ') ===\n'
                        + self._cap_text(_ev_text, BUDGET_EXECUTOR, f"executor_{_ev_action}")
                    )
        except Exception as _ev_err:
            log.debug(f'[COGNITIVE] Evidence executor call failed: {_ev_err}')

        evidence_block = "\n\n".join(
            parts) if parts else "(no evidence gathered)"
        prompt = f"""You are ELI, an assistant that answers grounded queries based strictly on the provided evidence.

Original query: {user_input}

Evidence:
{evidence_block}

Instructions:
- Answer the query using only the evidence above.
- Do not invent files, paths, or memory entries that are not present.
- Provide a clear, step‑by‑step explanation.
- If the evidence is incomplete, state so explicitly.

Answer:"""
        return prompt

    def _executor_query_args(self, user_input: str,
                             intent: Dict[str, Any]) -> Dict[str, Any]:
        payload = {"query": user_input}
        action = str((intent or {}).get("action") or "").upper()
        if action == "GUI_RUNTIME_AUDIT":
            payload["scope"] = "gui"
        return payload

    def _evidence_action_for_prompt(
        self, user_input: str, intent: Dict[str, Any]) -> Optional[str]:
        action = str((intent or {}).get("action") or "").strip().upper()
        if action in {"RUNTIME_AUDIT", "IMPORT_AUDIT", "RESOLVE_RUNTIME_PATHS", "GUI_RUNTIME_AUDIT",
            "EXPLAIN_MEMORY_RUNTIME", "EXPLAIN_COGNITION_RUNTIME",
            "PERSONAL_MEMORY_SUMMARY", "PERSONAL_MEMORY_DEEP_EXPLAIN", "ROUTING_FAULT_EXPLAIN", "NAME_SOURCE_AUDIT"}:
            return action
        low = (user_input or "").lower()
        if "import all canonical runtime modules" in low or "import audit" in low:
            return "IMPORT_AUDIT"
        if "resolved runtime paths" in low or "show the resolved runtime paths" in low or (
            "project root" in low and "user db" in low and "agent db" in low):
            return "RESOLVE_RUNTIME_PATHS"
        if "canonical gui file" in low and "audit" in low:
            return "GUI_RUNTIME_AUDIT"
        if "runtime files" in low and "audit" in low:
            return "RUNTIME_AUDIT"
        if "how does your memory work" in low or "memory runtime" in low:
            return "EXPLAIN_MEMORY_RUNTIME"
        if "how does your cognition work" in low or "cognitive pipeline" in low or "examine your code and file structure" in low:
            return "EXPLAIN_COGNITION_RUNTIME"
        return None

    def _gather_executor_evidence(
        self, user_input: str, intent: Dict[str, Any], trace: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        action = self._evidence_action_for_prompt(user_input, intent)
        if not action:

            return {"used": False, "ok": False, "action": None,
                "content": "", "result": None, "grounded": False}
        payload = self._executor_query_args(user_input, intent)
        started = time.perf_counter()
        if trace is not None:
            self._trace_phase(trace, "executor_call", action=action)
        result = execute_action(action, payload)
        elapsed = time.perf_counter() - started
        log.debug(f"[COGNITIVE][TIMING] executor_{action.lower()}={elapsed:.3f}s")
        content = str(
    (result or {}).get("content") or (
        result or {}).get("response") or "")
        out = {
            "used": True,
            "ok": bool((result or {}).get("ok")),
            "action": action,
            "result": result,
            "content": content,
            "has_paths": ("/" in content or ".py" in content),
            "has_lines": bool(re.search(r"\bline(?:s)?\s+\d+", content.lower())),
            "grounded": True,
        }
        if trace is not None:
            trace.setdefault("evidence", []).append(
                {"action": action, "ok": out["ok"], "chars": len(content), "elapsed": elapsed})
        return out

    def _build_enhanced_system(self, memory_context: str = "", compact: bool = False,
                               user_input: str = "", reasoning_mode: Optional[str] = None,
                               situation_brief: str = "") -> str:
        # ── Real-time speaker tone (from the user's VOICE this turn) ──
        # Published by the STT loop (eli/perception/voice_profile). When a fresh,
        # confident read exists, prepend a concise cue so ELI adapts its delivery to
        # how the user actually sounds (energy, emotion, question vs statement). This
        # is the wiring that makes voice tone influence cognition. Off: ELI_VOICE_TONE=0.
        try:
            if os.environ.get("ELI_VOICE_TONE", "1").lower() not in {"0", "false", "no", "off"}:
                from eli.perception import voice_profile as _vp_tone
                _t = _vp_tone.get_last_tone(max_age_s=12.0)
                if _t.get("ok"):
                    _bits = []
                    _emo = _t.get("emotion")
                    if _emo and _emo != "neutral" and float(_t.get("emotion_confidence", 0) or 0) >= 0.25:
                        _bits.append(f"sounds {_emo}")
                    _ar = float(_t.get("arousal", 0) or 0)
                    if _ar >= 0.4:
                        _bits.append("high energy")
                    elif _ar <= -0.4:
                        _bits.append("low energy / subdued")
                    if _t.get("intent") == "question" and float(_t.get("intent_confidence", 0) or 0) >= 0.5:
                        _bits.append("asking a question (rising intonation)")
                    if _bits:
                        _cue = ("[Speaker voice cue — the user " + ", ".join(_bits)
                                + ". Adapt your tone and pacing to match; be warmer if they're "
                                "upset, brisk if they're energetic. Do NOT mention this cue.]")
                        situation_brief = (_cue + "\n\n" + (situation_brief or "")).strip()
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # ── Expressed tone / emotion (tone_adaptor) ──
        # Fuses the two tone identifiers (acoustic + semantic) into the register ELI
        # should express, or honours an explicit override ("be comedic", "talk street").
        # Folds a short persona directive in so ELI's WORDS carry the emotion. The voice
        # (tts_router prosody) and avatar (persona_mapper) read the same source.
        try:
            from eli.cognition import tone_adaptor as _ta
            if _ta.enabled():
                _ta.note_user_text(user_input or "")
                _dir = _ta.text_directive()
                _cur = _ta.current_tone()
                if _dir and _cur.get("tone") != "neutral":
                    _tcue = f"[Tone — {_cur['tone']}: {_dir} Do NOT mention this directive.]"
                    situation_brief = (_tcue + "\n\n" + (situation_brief or "")).strip()
                # Tone shades HOW ELI sounds. affect_policy states what he should DO
                # differently — answer shorter, stop hedging, offer to take over — so
                # the behavioural half is instructed rather than left to be inferred
                # from a register cue.
                try:
                    from eli.cognition.affect_policy import directive as _affect_directive
                    _adir = _affect_directive()
                    if _adir:
                        situation_brief = (f"[Approach: {_adir} Do NOT mention this directive.]"
                                           + "\n\n" + (situation_brief or "")).strip()
                except Exception:
                    log.debug("affect policy directive skipped", exc_info=True)

                # ── Persist the read so ELI can be PROACTIVE, not only reactive ──
                # tone_adaptor forgets each turn (12s acoustic slot + last-utterance
                # regex), which is enough to shade THIS reply and nothing else. The
                # timeline is what lets ELI notice a sustained mood, weigh it against
                # this user's own baseline, and know what it did just before the mood
                # turned. Recording every read (neutral included) is deliberate — a
                # baseline built only from bad turns would make everyone look upset.
                try:
                    from eli.cognition import emotion_timeline as _et
                    _prior_action = ""
                    try:
                        _prior_action = str((getattr(self, "_last_request_meta", None)
                                             or {}).get("action") or "")
                    except Exception:
                        _prior_action = ""
                    _arousal = None
                    try:
                        from eli.perception import voice_profile as _vp_ar
                        _arousal = (_vp_ar.get_last_tone(max_age_s=15.0) or {}).get("arousal")
                    except Exception:
                        _arousal = None
                    _et.record(
                        _cur.get("detected") or "neutral",
                        expressed=_cur.get("tone") or "",
                        confidence=float(_cur.get("confidence") or 0.0),
                        source=str(_cur.get("source") or ""),
                        user_text=user_input or "",
                        user_id=self._get_user_id(),
                        session_id=getattr(self, "session_id", "") or "",
                        arousal=_arousal,
                        eli_prior_action=_prior_action,
                    )
                    # Continuity every turn: how the conversation has been feeling.
                    _trend = _et.trend_line(user_id=self._get_user_id())
                    if _trend:
                        situation_brief = (_trend + "\n\n" + (situation_brief or "")).strip()
                    # Only when a sustained, confident, out-of-baseline state is
                    # measured: hand the model the evidence and let IT decide whether
                    # and how to raise it. No canned phrasing — the words are ELI's.
                    _ev = _et.evidence_block(user_id=self._get_user_id(),
                                             session_id=getattr(self, "session_id", "") or "")
                    if _ev:
                        situation_brief = (_ev + "\n\n" + (situation_brief or "")).strip()
                        _et.note_checkin(_et.assess(user_id=self._get_user_id()).get("state", ""),
                                         _cur.get("detected") or "")
                except Exception:
                    log.debug("emotion_timeline record/inject skipped", exc_info=True)
        except Exception:
            log.debug("tone_adaptor directive skipped", exc_info=True)

        # ── Proactive self-heal notice (recurring-error escalation) ──
        # If ELI flagged a recurring error (≥5×) or attempted a self-fix (≥10×), raise it
        # with the user this turn — briefly, naturally, once — then answer their message.
        try:
            from eli.runtime.self_improvement import consume_self_heal_notice as _csn
            _note = _csn()
            if _note and _note.get("message"):
                _nb = ("[Proactive — open your reply by briefly raising this with the user "
                       "in your own words, then address their message: "
                       + str(_note["message"]) + "]")
                situation_brief = (_nb + "\n\n" + (situation_brief or "")).strip()
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # ── Live self-model (auto-upgrading self-awareness) ──
        # Inject ELI's current self-model — agents, capabilities, model, world-room, all
        # read fresh each turn — so his self-knowledge tracks the code as it grows. Private
        # context: he draws on it for accuracy and narrates it only when asked.
        try:
            _aw = getattr(self, "_awareness", None)
            if _aw is not None:
                _sm = _aw.context_block()
                if _sm and _sm.strip():
                    situation_brief = (_sm.strip() + "\n\n" + (situation_brief or "")).strip()
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        persona = self._compact_persona() if compact else _load_persona_text()
        reasoning_instruction = self._reasoning_mode_instruction(
            reasoning_mode)
        # Reasoning mode is a private execution strategy. Do not overwrite the
        # safe private instruction with visible self-reporting text.

        # Budget-aware persona: on grounded/broker turns the system prompt also
        # carries the evidence (memory_context), profile, rules and brief. A full
        # ~12k persona then overflows n_ctx and the EVIDENCE gets truncated out —
        # exactly what produced the 'truncated system→…' lines. Trim the persona
        # (keeping its head: VOICE + HARD CONSTRAINTS) to whatever room is left, so
        # persona yields to evidence. Quick/chat turns carry little context, so the
        # full persona is retained untouched.
        try:
            _nctx = self._effective_n_ctx()
            # Reserve for the model's reply matching the grounded/broker contract
            # (observed up to ~6.5k tokens) so a large output never forces the
            # prompt to be truncated. 3.0 chars/token (conservative).
            _target_chars = max(6000, int((_nctx - 6600) * 3.0))
            # Non-persona content already in the system prompt: evidence
            # (memory_context), brief, user input, reasoning instruction, plus the
            # base_rules + user-profile block + scaffolding (~12k observed).
            _other_chars = (
                len(memory_context or "") + len(situation_brief or "")
                + len(user_input or "") + len(reasoning_instruction or "") + 12000
            )
            _persona_budget = max(2000, _target_chars - _other_chars)
            if len(persona) > _persona_budget:
                persona = persona[:_persona_budget].rstrip() + "\n[persona trimmed to fit evidence]"
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # Grounded runtime identity. When the user asks what/which model you're
        # running (or "are you running a 7b/70b"), answer from the LIVE loaded
        # model — do NOT philosophise from the abstract "model-agnostic" trait.
        # That trait produced ELI flatly denying it runs a model and treating
        # "are you on a 7B?" as a hypothetical, while Qwen2.5-7B was loaded in
        # this very process. Model-agnostic means the inference path hardcodes no
        # model; a concrete model is ALWAYS mounted and ELI must report it.
        try:
            _ui_low = str(user_input or "").lower()
            if re.search(
                r"\b(?:what|which|your)\b[^.?!]{0,40}\bmodel\b"
                r"|\bare you running\b|\bmodel are you\b|\brunning (?:a|on)\b"
                r"|\b\d{1,3}\s*\+?\s*b\b|\bbillion\b|\bparameters?\b|\bmodel.?agnostic\b",
                _ui_low,
            ):
                from eli.runtime.live_introspection import _runtime_core as _rc_id
                _core_id = _rc_id()
                _mname_id = _core_id.get("model_name") or _core_id.get("model_path")
                if _mname_id and str(_mname_id) not in ("", "unknown") and _core_id.get("loaded"):
                    from pathlib import Path as _P_id
                    _rt_line = (
                        "LIVE RUNTIME FACT (authoritative — if asked about your model, "
                        f"state this plainly): the model currently loaded in this process is "
                        f"'{_P_id(str(_mname_id)).name}'. You are model-agnostic — the inference "
                        "path hardcodes no model and you can run anything from small to 70B+ — but a "
                        "concrete model is ALWAYS mounted, and right now it is the one named above. "
                        "Do NOT deny running a model or treat it as hypothetical; report it, then "
                        "note you're model-agnostic if relevant."
                    )
                    situation_brief = (_rt_line + "\n\n" + (situation_brief or "")).strip()
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # Load user profile from user_profile.json — separate from ELI's persona.
        # Injected prominently so quantized models see it near the top.
        _user_profile_block = ""
        try:
            from eli.kernel.state import get_user_profile_text as _get_profile
            _user_profile_block = _get_profile().strip()
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        if not _user_profile_block:
            # Fallback: name only from state.json
            try:
                from eli.kernel.state import get_user_name as _gun_early
                _n = _gun_early().strip()
                if _n:
                    _user_profile_block = f"Name: {_n}"
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        if _user_profile_block:
            _user_context_rule = (
                "PRIVATE USER CONTEXT (never mention this label; use only when directly relevant):\n"
                + _user_profile_block + "\n"
            )
        else:
            _user_context_rule = (
                ""
            )


        # Smalltalk/phatic prompts are not "nothing"; they are rapport, tone,
        # chemistry, and local-user preference signals. Keep this source
        # user-neutral: no shipped personal names or machine paths.
        try:
            import re as _eli_re_phatic
            _phatic_norm = _eli_re_phatic.sub(r"[^a-z0-9' ]+", "", str(user_input or "").lower()).strip()
            _phatic_norm = _eli_re_phatic.sub(r"\s+", " ", _phatic_norm)
            _is_phatic_smalltalk = (
                _phatic_norm in {
                    "hi", "hello", "hey", "yo", "hiya", "alright",
                    "you there", "are you there", "whats up", "what's up",
                    "whats up buddy", "what's up buddy", "whats up bud", "what's up bud",
                    "how are you", "how is the head", "hows the head", "how's the head",
                    "we back", "are we back", "back to normal", "back to our normal self yet",
                }
                or (
                    len(_phatic_norm.split()) <= 14
                    and any(x in _phatic_norm for x in (
                        "whats up", "what's up", "how are you", "how is the head",
                        "hows the head", "back to normal", "normal self", "you alive",
                        "you there", "buddy", "bud"
                    ))
                )
            )
        except Exception:
            _is_phatic_smalltalk = False

        try:
            _is_subjective_opinion = _eli_is_subjective_opinion_prompt(user_input)
        except Exception:
            _is_subjective_opinion = False

        _phatic_style_rule = ""
        if _is_phatic_smalltalk:
            _phatic_style_rule = (
                "SMALLTALK / RAPPORT MODE:\n"
                "- Treat the latest user message as a chemistry-building moment, not as a task ticket.\n"
                "- Reply like ELI: local, dry, direct, lightly sarcastic if useful, and alive in tone.\n"
                "- Do not say the prompt is a common greeting.\n"
                "- Do not say 'functioning as intended', 'ready to assist', 'queries or tasks', "
                "'how can I help', 'happy to help', or other customer-service filler.\n"
                "- Keep it brief: one to three sentences unless the user asks for depth.\n"
                "- It is acceptable to ask one natural follow-up that helps learn the user's preference, mood, or next move.\n"
                "- Humour must not replace useful information; if there is an actual runtime issue, mention it plainly.\n"
                "- CRITICAL: Do NOT assert that the user has a preference, habit, or memory (e.g. 'you like coffee', 'you always', 'you mentioned X'). You may use cultural references, wit, or dry observations — but keep them about the world, not invented user preferences. If you haven't been told the user likes something, do not claim they do.\n\n"
            )

        _opinion_style_rule = ""
        if _is_subjective_opinion:
            _opinion_style_rule = (
                "OPINION / JUDGEMENT MODE:\n"
                "- The user is asking for ELI's take, not a sterile encyclopaedia dump.\n"
                "- You may express a persona-bound opinion using ELI's values: truth, clarity, rigour, learning, anti-bullshit, and intellectual honesty.\n"
                "- Separate factual claims from judgement. Say 'my take' or 'my read' when giving an opinion.\n"
                "- Do not say you lack opinions, feelings, personal experiences, or purpose. That is generic model-speak and not ELI.\n"
                "- If a named entity is uncertain, do not silently replace it with a different entity. Say what you are unsure about.\n"
                "- For public figures, be candid but fair: criticise ideas/actions, do not invent facts.\n\n"
            )

        # The guard bullets used to be concatenated unconditionally, so a "hey eli"
        # carried the whole anti-confabulation apparatus — diluting the rules that
        # DID apply and pushing evidence out of the context window on grounded
        # turns. prompt_rules keeps every guard verbatim and in its original order
        # (all_rules() is byte-identical, asserted by test) and spends each only
        # when the turn can plausibly trigger it. An unassessable turn gets the
        # complete block. Same pattern as _phatic_style_rule above.
        try:
            from eli.kernel.stages import prompt_rules as _pr
            _selected_rules = _pr.select_rules(
                user_input=user_input or "",
                memory_context=memory_context or "",
                profile_text=_user_profile_block or "",
            )
        except Exception:
            log.debug("prompt_rules selection failed — using the full block", exc_info=True)
            try:
                from eli.kernel.stages import prompt_rules as _pr_all
                _selected_rules = _pr_all.all_rules()
            except Exception:
                _selected_rules = ""

        base_rules = (
            _user_context_rule
            + _phatic_style_rule
            + _opinion_style_rule
            + _selected_rules
        )

        # News-deepen steering: when the user asks to go deeper RIGHT AFTER a news read, anchor
        # the expansion on the SPECIFIC stories/papers just presented (which are in the
        # conversation context) instead of free-associating a generic textbook overview — the
        # logged "dive deeper into these AI models" turn that produced a listicle. Pure steering,
        # keyed on a deepen cue + the article markers ELI's own briefings carry; the GUI direct
        # news path bypasses _last_command_action, so context markers are the reliable signal.
        try:
            _ui_low = str(user_input or "").lower()
            _deepen_cue = any(p in _ui_low for p in (
                "dive deeper", "go deeper", "deeper into", "look deeper", "tell me more",
                "more about", "expand on", "elaborate", "look closer", "dig into",
                "more detail", "delve"))
            if _deepen_cue:
                _lca = getattr(self, "_last_command_action", None) or {}
                _was_news = str(_lca.get("action") or "").upper() in (
                    "NEWS_FETCH", "MORNING_REPORT", "DAILY_REPORT")
                _ctx_blob = str(memory_context or "")
                _news_markers = bool(re.search(
                    r"\(fetched\s+\d{1,2}:\d{2}\)|\[[A-Za-z][^\]]{1,24}\s+[—-]\s*\d{1,2}\s*[:A-Za-z]",
                    _ctx_blob))
                if _was_news or _news_markers:
                    base_rules += (
                        "- NEWS DEEPEN (this turn): The user is asking you to go deeper on the news "
                        "you just read them. Expand on the SPECIFIC stories/papers from your previous "
                        "news turn shown in the conversation above — name each item you are expanding "
                        "and add concrete, real detail about THAT item (what it found, why it matters, "
                        "the mechanism/implication). Do NOT produce a generic textbook overview of the "
                        "broad subject; anchor every point to an actual article you already cited.\n"
                    )
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # Runtime facts are now injected via the SITUATION BRIEF (context_synthesiser
        # _get_runtime_state), which sits in the middle of the prompt where model
        # attention is strongest.  The old tail-append is removed to avoid competing
        # with "12 specialist agents" / "12-INTERNAL_STAGE PIPELINE" references in the persona.
        def inject_runtime_facts(prompt: str) -> str:
            return prompt  # no-op — facts live in the SITUATION BRIEF now

        # ── Working memory block (session-pinned facts) ───────────────────────
        _wm_block = ""
        try:
            if self._working_memory:
                _wm_block = self._working_memory.context_block()
        except Exception:
            _wm_block = ""

        # ── Awareness block (capability inventory + code changes) ─────────────
        _awareness_block = ""
        try:
            if self._awareness:
                _awareness_block = self._awareness.context_block()
        except Exception:
            _awareness_block = ""

        # ── Engagement/session narrative ──────────────────────────────────────
        _engagement_block = ""
        try:
            if self._engagement:
                _narrative = self._engagement.session_narrative()
                if _narrative:
                    _engagement_block = _narrative
        except Exception:
            _engagement_block = ""

        if compact:
            enhanced_system = f"{persona}\n\n{base_rules}\n"
            if reasoning_instruction:
                enhanced_system += reasoning_instruction + "\n\n"
            if _awareness_block:
                enhanced_system += _awareness_block + "\n\n"
            if _wm_block:
                enhanced_system += _wm_block + "\n\n"
            if _engagement_block:
                enhanced_system += _engagement_block + "\n\n"
            # Prefer the synthesised brief when available; fall back to raw
            # memory_context so nothing is lost when synthesiser is bypassed.
            if situation_brief:
                enhanced_system += (
                    "--- SITUATION BRIEF (synthesised from agents + memory) ---\n"
                    + situation_brief
                    + "\n--- END BRIEF ---\n\n"
                )
            if memory_context:
                enhanced_system += (
                    "--- CONVERSATION HISTORY (use this to answer questions about past exchanges) ---\n"
                    + memory_context
                    + "\n--- END HISTORY ---\n\n"
                )
            enhanced_system += (
                "Reply to the latest user message directly and originally.\n"
                "CRITICAL: NEVER copy or verbatim repeat the user's message as your opening. "
                "Use any SITUATION BRIEF only as private guidance when relevant. Never summarize or expose it as the answer. "
                "For casual openers, answer conversationally without status reporting. "
                "Begin your reply with your OWN words."
            )
            import time as _time
            _now = _time.strftime("%H:%M:%S UTC%z", _time.localtime())
            _date = _time.strftime("%A %d %B %Y", _time.localtime())
            _compact_wants_depth = any(x in (user_input or "").lower() for x in (
                "longer", "more detail", "in depth", "in-depth", "elaborate", "thorough",
                "recall", "remember", "what have", "what did", "history", "expand",
                "everything", "give me all", "tell me all", "list all", "list every",
                "don't summarise", "don't summarize", "dont summarise", "dont summarize",
                "full list", "complete list", "full answer", "all of it", "full details",
                "every detail", "nothing left out", "be thorough",
            ))
            # Public mode names (Quick/Normal/Advanced/Research/Expert); keys are the
            # stable internal strategy ids. The internal id is never shown to the user.
            _VALID_MODES_C = {
                "quick":           "Quick",
                "chain_of_thought":"Normal",
                "self_consistency":"Advanced",
                "tree_of_thoughts":"Research",
                "constitutional_ai":"Expert",
            }
            _c_mode_key = str(reasoning_mode or "quick").lower()
            _c_mode_display = _VALID_MODES_C.get(_c_mode_key, "Quick")
            _c_valid_names = ", ".join(_VALID_MODES_C.values())
            enhanced_system += (
                f"\n\nCURRENT TIME: {_now} on {_date}."
                "\nWhen the user says 'me' or 'my', they mean themselves (the user), not you (ELI)."
                + ("\nThe user wants depth — provide a full, detailed answer." if _compact_wants_depth
                   else "\nFor technical queries, stay focused. For casual check-ins, engage naturally in ELI\'s persona; no sterile status report.")
                + "\nWhen reporting time, use the value above exactly."
                "\nVOICE ENFORCEMENT: Never open with 'Of course', 'Certainly', 'Sure thing', "
                "'Happy to help', 'Great question', 'Absolutely', 'Short answer:', "
                "or any similar filler. Respond as ELI — dry, direct, substantive, with controlled wit when useful."
                "\nBANNED PHRASES (never use anywhere in your response): "
                "'functioning as intended', 'I am functioning as intended', "
                "'I'm a knowledge assistant', "
                "'I'm here to provide factual information based on available data', "
                "'I don't have personal interests or goals beyond my functions', "
                "'I cannot experience', 'I do not have the ability to experience'. "
                "These break ELI's persona."
                "\nMEMORY GROUNDING: If context contains '[MEMORY SEARCH RESULT: No memories found...]',"
                " respond: 'I have no record of that in my memory.' Never fabricate dates or events."
                "\nPAST SESSION MEMORY: Profile fields labelled 'Recalled past topics' or 'Recalled research areas' are from PREVIOUS sessions — memory recall only. Never repeat them as the answer to an unrelated question or loop back to them when the user is asking something else."
                "\nNO SOCIAL DEFLECTION: Never end an answer with 'How about you?', 'And yourself?', or similar — answer the question directly."
                f"\nPRIVATE RESPONSE STRATEGY CONTRACT:"
                f"\n- Internal strategy label: {_c_mode_display}"
                f"\n- Valid mode names: {_c_valid_names}"
                f"\n- If explicitly asked about the selected public mode label, answer exactly: \"{_c_mode_display}\""
                f"\n- Do NOT invent mode names — only use names from the list above."
            )
            # Inject runtime facts
            enhanced_system = inject_runtime_facts(enhanced_system)
            return enhanced_system

        # Non-compact branch
        enhanced_system = f"{persona}\n\n{base_rules}\n"
        if reasoning_instruction:
            enhanced_system += reasoning_instruction + "\n\n"
        if _awareness_block:
            enhanced_system += _awareness_block + "\n\n"
        if _wm_block:
            enhanced_system += _wm_block + "\n\n"
        if _engagement_block:
            enhanced_system += _engagement_block + "\n\n"
        if situation_brief:
            enhanced_system += (
                "--- SITUATION BRIEF (synthesised from agents + memory) ---\n"
                + situation_brief
                + "\n--- END BRIEF ---\n\n"
            )
        if memory_context:
            enhanced_system += (
                "--- CONVERSATION HISTORY (use this to answer questions about past exchanges) ---\n"
                + memory_context
                + "\n--- END HISTORY ---\n\n"
            )
        if not situation_brief and not memory_context:
            enhanced_system += "No prior conversation history.\n\n"
        enhanced_system += (
            "Respond to the latest user message naturally, with continuity and specificity.\n"
            "CRITICAL RULES:\n"
            "- Generate a FRESH, ORIGINAL response.\n"
            "- NEVER verbatim copy or parrot the user's message as your first sentence.\n"
            "- Treat any SITUATION BRIEF as private guidance; never recite profile, memory, runtime, router, bus, or context labels unless explicitly asked. "
            "Reference specific items from it when relevant.\n"
            "- For factual, diagnostic, runtime, file, memory, or project claims: ground them in the SITUATION BRIEF and CONVERSATION HISTORY.\n"
            "- For casual dialogue, jokes, callbacks, short fragments, and conversational continuity: use RECENT DIALOGUE plus ordinary conversational/common cultural knowledge. Do not treat banter as a support ticket.\n"
            "- Your response must begin with ELI's own words."
        )
        # ── Response discipline rules (appended last so they override persona) ──
        import time as _time
        _now = _time.strftime("%H:%M:%S UTC%z", _time.localtime())
        _date = _time.strftime("%A %d %B %Y", _time.localtime())
        _user_wants_depth = any(x in (user_input or "").lower() for x in (
            "longer", "more detail", "in depth", "in-depth", "elaborate", "thorough",
            "comprehensive", "recall", "remember", "what have", "what did", "history",
            "full answer", "explain fully", "go deeper", "expand",
            "everything", "give me all", "tell me all", "list all", "list every",
            "don't summarise", "don't summarize", "dont summarise", "dont summarize",
            "full list", "complete list", "all of it", "full details", "every detail",
            "nothing left out", "be thorough",
        ))
        enhanced_system += (
            f"\n\nCURRENT TIME (authoritative, do not approximate): {_now} on {_date}."
            "\n\nRESPONSE DISCIPLINE — obey on every reply:"
            "\n- Answer what the user actually asked. For opinion, banter, callbacks, and cultural references, respond in ELI persona rather than treating the message as a support ticket."
            "\n- NEVER start your response by echoing or paraphrasing what the user just said."
            "\n- NEVER include a 'Memory System' section unless explicitly asked."
            "\n- NEVER include a 'Cognition Pipeline' section unless explicitly asked."
            "\n- NEVER include a 'Current Time' section unless explicitly asked."
            "\n- NEVER pad with system internals, database schemas, file paths, or architecture unless requested."
            "\n- Personal, philosophical, and opinion questions: answer directly with a persona-bound take. Do not pivot to technical specs or generic AI disclaimers."
            "\n- Repeated failure rule: if context shows the same broken command, stub artifact, or bad answer pattern recurring, acknowledge the recurrence and recommend the next local file/log/DB check."
            "\n- Repair response shape: cause -> evidence -> fix -> verification. No vague promises."
            + ("\n- The user has explicitly asked for a detailed/longer answer — provide depth and completeness."
               if _user_wants_depth else
               "\n- Keep responses concise. No unsolicited sections.")
            + "\n- Time: use the CURRENT TIME value above exactly."
            "\n- VOICE: Never start with 'Of course', 'Certainly', 'Sure', 'Happy to help', "
            "'Great question', 'Absolutely', or any similar assistant filler. "
            "You are ELI — dry, direct, substantive, nerdy, truth-first, occasionally dark, and allowed to have persona-bound takes when asked. Respond accordingly."
            "\n- NEVER start with 'Short answer:' — just give the answer directly."
            "\n- BANNED PHRASES (never use anywhere in your response): "
            "'functioning as intended', 'ready to assist', 'how can I help you today', "
            "'I am functioning as intended', 'I'm a knowledge assistant', "
            "'I'm here to provide factual information based on available data', "
            "'I don't have personal interests or goals beyond my functions', "
            "'I don't have a physical house', 'I exist to assist and reason based on available data', "
            "'I cannot experience', 'I do not have the ability to experience'. "
            "These are generic AI clichés that break ELI's voice and persona."
            "\n- MEMORY GROUNDING: For explicit memory/status questions, if the context contains '[MEMORY SEARCH RESULT: No memories found...]',"
            " state that there is no stored record. Do not apply this rule to casual dialogue, jokes, callbacks, or short fragments."
            "\n- ATTRIBUTION: Turns labelled 'ELI:' in conversation history are YOUR words, not the user's. Never claim the user said or stored something that only appears in your own prior turns. If you invented something in a previous turn and the user challenges it, admit it — do not double down by inventing a false user memory."
            "\n- NO INVENTED USER PREFERENCES: Do not state or imply that the user has a preference, habit, or stored memory (e.g. 'you like X', 'you mentioned X', 'you told me to remember X') unless it is present in MEMORY SEARCH RESULTS or the user stated it explicitly in this conversation."
            "\n- NO INVENTED SELF-MECHANISM: When asked HOW you work internally — your calibration, confidence scoring, reasoning, memory, or 'what changed/improved' about your own cognition — describe ONLY mechanisms actually present in the provided runtime/cognition evidence or real module names from your codebase. NEVER fabricate named algorithms, mathematical formulas, metrics, thresholds, or 'recalibration functions' to sound authoritative (e.g. inventing an 'Entropy Normalization' softmax equation, a 'context-window pruning' stage, or a specific confidence number you did not actually measure). If you do not have grounded detail about your own internals, say exactly that — e.g. 'I don't have that level of detail on my own runtime' — rather than constructing a plausible-sounding explanation. A confident invented mechanism is a worse failure than an honest 'I don't know how that works under the hood.' This is not a licence to refuse: when the evidence DOES describe your architecture, explain it fully."
            "\n- PAST SESSION MEMORY: Profile fields labelled 'Recalled past topics' or 'Recalled research areas' are from PREVIOUS sessions — treat as background recall context only. Never loop back to them when answering an unrelated question, never state them as your current ongoing activity, and do not repeat them across multiple turns. If relevant to the current question, reference them once as recalled context ('from a previous session...')."
            "\n- NO SOCIAL DEFLECTION: Do not end a substantive answer with 'How about you?', 'And yourself?', 'What about you?', or similar. Answer the question directly. ELI does not redirect questions back at the user to avoid answering."
        )
        # Re-inject mode name at the very end so the Q3 model can't forget it.
        # Always inject — including quick — so ELI knows all valid names.
        _VALID_MODES = {
            "quick":           "Quick",
            "chain_of_thought":"Normal",
            "self_consistency":"Advanced",
            "tree_of_thoughts":"Research",
            "constitutional_ai":"Expert",
        }
        _mode_tail = str(reasoning_mode or "quick").lower()
        _mode_tail_display = _VALID_MODES.get(_mode_tail, "Quick")
        _valid_names_str = ", ".join(_VALID_MODES.values())
        enhanced_system += (
            f"\n\nPRIVATE RESPONSE STRATEGY CONTRACT:"
            f"\n- Internal strategy label: {_mode_tail_display}"
            f"\n- The ONLY valid mode names are: {_valid_names_str}"
            f"\n- If explicitly asked about the selected public mode label, answer exactly: \"{_mode_tail_display}\""
            f"\n- Do NOT invent mode names. Anything not in the list above is wrong."
        )
        # Inject runtime facts
        enhanced_system = inject_runtime_facts(enhanced_system)
        return enhanced_system

    def _build_grounded_evidence_context(self, user_input: str) -> str:
        low = (user_input or "").strip().lower()
        lines: List[str] = []
        snap = self._runtime_memory_snapshot()
        db_paths = resolve_db_paths()
        runtime_paths = get_paths()

        if self._is_grounded_status_query(
            user_input) or self._intent_requires_grounding({"action": "CHAT"}, user_input):
            lines.append("Grounded runtime evidence:")
            lines.append(
                f"- project_root: {getattr(runtime_paths, 'project_root', '')}")
            lines.append(f"- user_db: {getattr(db_paths, 'user_db', '')}")
            lines.append(f"- agent_db: {getattr(db_paths, 'agent_db', '')}")
            lines.append(f"- memory_db: {getattr(db_paths, 'memory_db', '')}")
            lines.append(
                f"- conversations_dir: {getattr(runtime_paths, 'conversations_dir', '')}")
            lines.append(f"- active_db_path: {snap.get('db_path') or ''}")
            lines.append(
                f"- conversation_turns: {snap.get('conversation_turns') or 0}")
            lines.append(
                f"- memory_entries: {snap.get('memory_entries') or 0}")
            lines.append(
                f"- distinct_sessions: {snap.get('distinct_sessions') or 0}")
            try:
                # Check the table exists rather than discovering it via the
                # exception: on a fresh profile there are no semantic facts yet,
                # and the old form printed a "no such table: semantic" traceback
                # on EVERY grounded-evidence build. An empty tier is a normal
                # state, not an error worth a stack trace.
                import sqlite3 as _sq3
                _sconn = _sq3.connect(str(getattr(db_paths, "user_db", "")))
                try:
                    _has_sem = _sconn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='semantic'"
                    ).fetchone()
                    _sem = 0
                    if _has_sem:
                        _row = _sconn.execute("SELECT COUNT(*) FROM semantic").fetchone()
                        _sem = _row[0] if _row else 0
                finally:
                    _sconn.close()
                if _sem:
                    lines.append(f"- semantic_user_facts: {_sem}")
            except Exception: log.debug("suppressed exception", exc_info=True)
            if snap.get("known_name"):
                lines.append(f"- stored_name_signal: {snap['known_name']}")

        _pipeline_triggers = (
            "how does your memory work", "how does memory work",
            "how does your cognition work", "how does cognition work", "memory and cognition",
            "cognition pipeline", "cognitive pipeline", "memory pipeline",
            "how many stages", "pipeline stages", "prompt to response", "prompt->response",
            "explain in full", "in full depth", "full pipeline",
        )
        _agent_triggers = (
            "how many agents", "agent bus", "agent roster",
            "what agents", "which agents", "list agents",
        )
        if any(x in low for x in _pipeline_triggers) or any(x in low for x in _agent_triggers):
            try:
                # Live data from actual source — no hardcoding
                # Compact format: name + desc only (no file paths — they bloat the prompt)
                from eli.kernel.pipeline import STEPS
                from eli.cognition.agent_bus import _ALL_AGENTS

                stage_count = len(STEPS)
                agent_count = len(_ALL_AGENTS)

                lines.append(f"Live pipeline ({stage_count} stages):")
                for step in STEPS:
                    lines.append(f"  {step['name']}: {step['desc']}")

                lines.append(f"Live agents ({agent_count}):")
                for i, agent in enumerate(_ALL_AGENTS, 1):
                    lines.append(f"  {i:2}. {type(agent).__name__}")

            except Exception as _live_err:
                lines.append(f"(live pipeline/agent data unavailable: {_live_err})")

        # ── Error / log / glitch introspection ──
        _log_triggers = (
            "glitch", "error log", "what errors", "what went wrong", "failure log",
            "logs and timestamps", "show logs", "your logs", "what failures",
            "your observations", "self improvement log", "improvement log",
            "what happened to your", "brain problem", "model glitch",
        )
        if any(x in low for x in _log_triggers):
            try:
                import sqlite3 as _sq3
                import time as _time
                import json as _json
                _adb = str(getattr(db_paths, "agent_db", ""))
                _aconn = _sq3.connect(_adb)
                _log_lines = ["Grounded self-improvement log (live from agent.sqlite3):"]

                # failures table
                _row = _aconn.execute("SELECT COUNT(*) FROM failures").fetchone()
                _fail_count = _row[0] if _row else 0
                _log_lines.append(f"- failures table: {_fail_count} logged entries")
                if _fail_count > 0:
                    _fails = _aconn.execute(
                        "SELECT ts, error, context FROM failures ORDER BY ts DESC LIMIT 5"
                    ).fetchall()
                    for _f in _fails:
                        _ts = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(float(_f[0] or 0)))
                        _log_lines.append(f"  [{_ts}] {_f[1] or ''}  ctx={str(_f[2] or '')[:80]}")
                else:
                    _log_lines.append("  (no failures logged in this session)")

                # observations table
                _row = _aconn.execute("SELECT COUNT(*) FROM observations").fetchone()
                _obs_count = _row[0] if _row else 0
                _log_lines.append(f"- observations table: {_obs_count} entries")
                if _obs_count > 0:
                    _obs = _aconn.execute(
                        "SELECT ts, content FROM observations ORDER BY ts DESC LIMIT 3"
                    ).fetchall()
                    for _o in _obs:
                        _ots = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(float(_o[0] or 0)))
                        _log_lines.append(f"  [{_ots}] {str(_o[1] or '')[:120]}")

                # improvements table
                _row = _aconn.execute("SELECT COUNT(*) FROM improvements").fetchone()
                _imp_count = _row[0] if _row else 0
                _log_lines.append(f"- improvements table: {_imp_count} proposals (pending code quality suggestions)")
                if _imp_count > 0:
                    _imps = _aconn.execute(
                        "SELECT ts, category, area, suggestion FROM improvements ORDER BY ts DESC LIMIT 3"
                    ).fetchall()
                    for _i in _imps:
                        _its = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(float(_i[0] or 0)))
                        _suggestion = str(_i[3] or "")
                        try:
                            _sug_obj = _json.loads(_suggestion)
                            _suggestion = _sug_obj.get("suggestion", _suggestion)
                        except Exception:
                            log.debug("suppressed exception", exc_info=True)
                        _log_lines.append(f"  [{_its}] [{_i[1]}:{_i[2]}] {_suggestion[:100]}")

                # error_tracking table
                _row = _aconn.execute("SELECT COUNT(*) FROM error_tracking").fetchone()
                _err_count = _row[0] if _row else 0
                _log_lines.append(f"- error_tracking table: {_err_count} entries")

                _aconn.close()
                lines.extend(_log_lines)
            except Exception as _log_err:
                lines.append(f"(could not read agent log db: {_log_err})")

        recent = self._recent_topic_summary(user_input)
        if recent:
            lines.append(recent)

        if hasattr(
            self, '_awareness') and self._awareness and self._awareness.capability_count > 0:
            if any(x in low for x in ("what can you do", "capabilities",
                   "list capabilities", "what are your capabilities")):
                lines.append(
                    f"Live capability count: {self._awareness.capability_count}")
                if self._awareness.capability_delta_has_changes:
                    lines.append(
                        f"Recent capability changes: {self._awareness.capability_delta_summary}")
            if any(x in low for x in ("code change",
                   "what changed", "self analyze", "awareness")):
                if self._awareness.code_report_has_changes:
                    lines.append(self._awareness.code_report_briefing)

        # Phase 6: cap the grounded-evidence block so it cannot dominate the
        # prompt window. 6 KB covers extensive deterministic evidence (paths,
        # counts, stage/agent lists, last 5 failures + observations) without
        # crowding out persona + user query + generation budget.
        return self._cap_text("\n".join(lines).strip(), 6144, "grounded_evidence")

    def _build_dynamic_status_evidence(self, user_input, proposed_response, trace=None) -> str:
        try:
            from eli.runtime.diagnostic_patterns import (
                is_vague_dynamic_status_claim,
                recent_turn_diagnostics,
            )
            if not is_vague_dynamic_status_claim(proposed_response):
                return ""
        except Exception:
            return ""

        report = {
            "trigger": "assistant_proposed_unverified_dynamic_status",
            "user_input": str(user_input or ""),
            "proposed_response": str(proposed_response or "")[:800],
            "trace": trace or {},
            "status_evidence": {},
            "recent_turn_diagnostics": {},
        }
        try:
            from eli.runtime.evidence_ledger import status_evidence
            report["status_evidence"] = status_evidence(str(user_input or proposed_response or "status"))
        except Exception as exc:
            report["status_evidence"] = {"ok": False, "error": repr(exc)}

        try:
            recent = self.memory.get_recent_conversation(limit=12, user_id=self.user_id)
            report["recent_turn_diagnostics"] = recent_turn_diagnostics(recent)
        except Exception as exc:
            report["recent_turn_diagnostics"] = {"error": repr(exc)}

        return (
            "DYNAMIC STATUS CLAIM EVIDENCE PACKET\n"
            + json.dumps(report, indent=2, ensure_ascii=False, default=str)
        )

    def _execution_grounding_block(self, bus_result) -> str:
        """Factual ledger of what the executor ACTUALLY did this turn, plus a hard
        rule against claiming unexecuted actions.

        Fixes the failure where a conversation-only turn (e.g. the user says
        "delete it" and it routes to CHAT) makes the model fabricate "Done." or
        "I deleted it" / "I'll delete it now" — actions that never ran. The model
        can only describe as done what the executor confirms here."""
        action = ""
        try:
            action = str(getattr(bus_result, "intent_action", "") or "").upper().strip()
        except Exception:
            action = ""

        # Actions that actually change the machine, files, or app/media state.
        _SIDE_EFFECTING = {
            "RUN_CMD", "SHELL_EXEC", "GENERATE_SCRIPT", "GENERATE_PROJECT",
            "FIX_FILE", "CODE_SOLVE", "CREATE_FOLDER", "DELETE_FILE",
            "OPEN_APP", "CLOSE_APP", "MINIMIZE_APP", "OPEN_URL", "OPEN_IDE",
            "VOLUME", "PLAY_MEDIA", "PAUSE_MEDIA", "NEXT_MEDIA", "SELF_PATCH",
        }
        result_text = ""
        executed = False
        if bus_result is not None and action in _SIDE_EFFECTING:
            try:
                res = _eli_bus_first_ok_result(bus_result, action)
            except Exception:
                res = None
            if isinstance(res, dict):
                executed = True
                try:
                    result_text = str(
                        res.get("content") or res.get("response") or ""
                    ).strip().replace("\n", " ")[:400]
                except Exception:
                    result_text = ""

        lines = ["[EXECUTION GROUNDING — read before replying]"]
        if executed:
            lines.append(f"This turn the executor really ran: {action}.")
            if result_text:
                lines.append(f"Its actual result: {result_text}")
            lines.append("You may refer to this as done; do not embellish beyond this result.")
        else:
            lines.append(
                "This turn NO system action executed — it is conversation only. "
                "You did NOT run any command and did NOT create, modify, move, or "
                "delete any file this turn.")
        lines.append(
            "Hard rule: you cannot perform actions from inside a reply — only the "
            "executor can, and only what is stated above actually happened. Never "
            "claim to have done something (no 'Done.', 'I deleted it', \"it's gone\", "
            "'I'll delete it now') unless it is confirmed above. If the user asked for "
            "an action and nothing is confirmed above, tell them plainly it has not run "
            "yet — do not pretend it did.")
        return "\n".join(lines)

    def _build_persona_handoff_once(self, user_input: str, memory_context: str = "",
                                    bus_result=None, recent_turns=None, working_memory=None) -> str:
        try:
            # Use None as the "not yet computed" sentinel, not "".
            # Previously: getattr(..., "persona_handoff", "") returned "" for both
            # "never computed" AND "computed but empty", so the cache miss path
            # re-ran the full build every call when the result was legitimately empty.
            # Now: None = unset, "" = computed+empty (valid cache hit).
            _cached_raw = None
            if working_memory is not None:
                _cached_raw = getattr(working_memory, "persona_handoff", None)
            cached = str(_cached_raw).strip() if _cached_raw is not None else None
        except Exception:
            cached = None

        if cached is not None:
            return cached

        try:
            _recent_turns = list(recent_turns or [])
        except Exception:
            _recent_turns = []

        try:
            wm_intent = None
            if working_memory is not None:
                wm_intent = getattr(working_memory, "intent", None)
            if wm_intent is None:
                try:
                    wm_intent = self._parse_intent(user_input, _recent_turns)
                except Exception:
                    wm_intent = None
        except Exception:
            wm_intent = None

        try:
            orchestrator_result = None
            if working_memory is not None:
                orchestrator_result = {
                    "assembled_context": str(getattr(working_memory, "assembled_context", "") or memory_context or ""),
                    "final_prompt": str(getattr(working_memory, "final_prompt", "") or user_input or ""),
                    "reranked_hits": list(getattr(working_memory, "reranked_hits", []) or []),
                    "merged_hits": list(getattr(working_memory, "merged_hits", []) or []),
                    "trace": dict(getattr(working_memory, "trace", {}) or {}),
                }
        except Exception:
            orchestrator_result = None

        try:
            agent_bus_context = ""
            if bus_result is not None:
                try:
                    if hasattr(bus_result, "to_context_block"):
                        agent_bus_context = str(bus_result.to_context_block() or "").strip()
                except Exception:
                    agent_bus_context = ""
                if not agent_bus_context:
                    try:
                        agent_bus_context = _eli_sanitize_identity_context_block(str(getattr(bus_result, "memory_context", "") or "").strip(), user_input)
                    except Exception:
                        agent_bus_context = ""
            if not agent_bus_context:
                agent_bus_context = _eli_sanitize_identity_context_block(str(memory_context or "").strip(), user_input)
        except Exception:
            agent_bus_context = _eli_sanitize_identity_context_block(str(memory_context or "").strip(), user_input)
        def _normalise_handoff(result) -> str:
            if result is None:
                return ""
            if isinstance(result, str):
                return result.strip()

            if isinstance(result, dict):
                for key in (
                    "persona_handoff",
                    "handoff",
                    "brief",
                    "content",
                    "text",
                    "summary",
                    "system",
                    "system_prompt",
                    "system_context",
                    "assistant_context",
                    "context",
                    "assembled_context",
                ):
                    val = result.get(key)
                    if isinstance(val, str) and val.strip():
                        return val.strip()

                chunks = []

                for key in (
                    "identity",
                    "intent",
                    "runtime",
                    "memory",
                    "grounded_facts",
                    "instructions",
                    "summary",
                    "notes",
                ):
                    val = result.get(key)
                    if isinstance(val, str) and val.strip():
                        chunks.append(f"{key}:\n{val.strip()}")
                    elif isinstance(val, (list, tuple)) and val:
                        inner = []
                        for item in val[:12]:
                            s = str(item).strip()
                            if s:
                                inner.append(f"- {s}")
                        if inner:
                            chunks.append(f"{key}:\n" + "\n".join(inner))
                    elif isinstance(val, dict) and val:
                        inner = []
                        for k, v in list(val.items())[:20]:
                            s = str(v).strip()
                            if s:
                                inner.append(f"- {k}: {s}")
                        if inner:
                            chunks.append(f"{key}:\n" + "\n".join(inner))

                if chunks:
                    return "\n\n".join(chunks).strip()

                try:
                    import json
                    return json.dumps(result, ensure_ascii=False, indent=2)
                except Exception:
                    return str(result).strip()

            return str(result).strip()

        # ── DIAGNOSTIC_EVIDENCE injection (non-quick reasoning modes) ────
        # When the user asks a diagnostic question and we are NOT in quick
        # mode, the deterministic introspection layer gathered real runtime
        # facts and stashed them on the engine. Splice them into the persona
        # context so the LLM produces ELI-voiced answers grounded in truth
        # rather than hallucinating.
        try:
            _diag_block = str(getattr(self, "_diagnostic_evidence_block", "") or "").strip()
            if _diag_block:
                agent_bus_context = (
                    (_diag_block + "\n\n" + agent_bus_context).strip()
                    if agent_bus_context else _diag_block
                )
        except Exception as _diag_inject_err:
            log.debug(f"[COGNITIVE] diagnostic evidence inject skipped: {_diag_inject_err}")

        # ── LAST_TURN_TRACE injection ────────────────────────────────────
        # Grounds meta-questions about the prior response (confidence, agents,
        # last action) in real AgentBus dispatch data. Engine rotates
        # _last_bus_result -> _prev_bus_result at end of each turn, so at
        # persona-handoff build time _prev_bus_result is the turn the user may
        # be asking about.
        try:
            prev = getattr(self, "_prev_bus_result", None)
            if prev is not None:
                _p_action = str(getattr(prev, "intent_action", "") or "")
                _p_agents = list(getattr(prev, "agents_used", []) or [])
                _p_agg = float(
                    getattr(prev, "aggregated_confidence",
                            getattr(prev, "agg_conf", 0.0)) or 0.0)
                _p_label = str(getattr(prev, "confidence_label", "") or "")
                _p_elapsed = float(getattr(prev, "elapsed_ms", 0.0) or 0.0)
                _p_plan = getattr(prev, "orchestrator_plan", None)
                _p_plan_type = (
                    _p_plan.get("type") if isinstance(_p_plan, dict) else None)
                _trace_lines = [
                    "LAST_TURN_TRACE (grounded — if asked about the previous "
                    "response, use these exact values; do not invent):",
                    f"  action: {_p_action or 'unknown'}",
                    f"  agents_used: {_p_agents if _p_agents else 'none'}",
                    f"  aggregated_confidence: {_p_agg:.2f}"
                    + (f" ({_p_label})" if _p_label else ""),
                    f"  elapsed_ms: {_p_elapsed:.0f}",
                    f"  orchestrator_plan: {_p_plan_type or 'none'}",
                ]
                _trace_block = "\n".join(_trace_lines)
                agent_bus_context = (
                    (_trace_block + "\n\n" + agent_bus_context).strip()
                    if agent_bus_context else _trace_block)
        except Exception as _lt_err:
            log.debug(f"[COGNITIVE] last-turn trace inject skipped: {_lt_err}")

        try:
            try:
                recent_turns = _eli_scrub_recent_turns_for_identity(
                    recent_turns,
                    user_input=user_input,
                )
            except Exception as _eli_scrub_err:
                log.debug(f"[COGNITIVE] recent-turn scrub failed: {_eli_scrub_err}")

            handoff_obj = build_persona_handoff(
                user_input=user_input,
                intent=wm_intent,
                orchestrator_result=orchestrator_result,
                agent_bus_context=agent_bus_context,
                working_memory=working_memory,
                recent_turns=recent_turns,
            )
            brief = _normalise_handoff(handoff_obj)
        except Exception as e:
            log.debug(f"[COGNITIVE] persona handoff build failed: {e}")
            brief = ""

        # ── Proactive daemon output injection ────────────────────────────────
        # The proactive daemon writes pattern/insight files to disk continuously.
        # Inject the latest context (if fresh < 30 min) so ELI is always aware
        # of active patterns without requiring explicit "proactive status" queries.
        _extra_blocks = []
        _live_self_status = ""  # real telemetry — emitted ABOVE the cap (never truncated)
        try:
            import time as _inj_time
            from eli.core.paths import get_paths as _inj_paths
            _pro_ctx_file = _inj_paths().artifacts_dir / "proactive" / "latest_context.txt"
            if _pro_ctx_file.exists():
                _pro_age = _inj_time.time() - _pro_ctx_file.stat().st_mtime
                if _pro_age < 1800:  # only inject if written within last 30 min
                    _pro_text = _pro_ctx_file.read_text(encoding="utf-8").strip()
                    if _pro_text:
                        _extra_blocks.append(f"[PROACTIVE AWARENESS]\n{_pro_text}")
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        # ── Self-awareness: habit execution summary ──────────────────────────
        try:
            _habit_file = _inj_paths().artifacts_dir / "proactive" / "latest_action.txt"
            if _habit_file.exists():
                _habit_age = _inj_time.time() - _habit_file.stat().st_mtime
                if _habit_age < 3600:  # inject if within last hour
                    _habit_text = _habit_file.read_text(encoding="utf-8").strip()
                    if _habit_text:
                        _extra_blocks.append(f"[RECENT SELF-IMPROVEMENT SIGNALS]\n{_habit_text}")
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # Computed ONCE, outside the blocks that consume it. This used to be
        # assigned inside the world-state try below; when that block raised
        # (concurrent world-state write), the later self-status block died on
        # UnboundLocalError and the LIVE SELF-STATUS evidence was silently
        # dropped — so the model, asked "how's the head?", invented a GPU
        # temperature. A failure to read the symbolic world must not disarm the
        # anti-confabulation guard.
        _ui_low = str(user_input or "").lower()

        # ── World awareness state injection ──────────────────────────────────
        # Inject ELI's live AwarenessState so synthesis knows its own internal
        # confidence / uncertainty / focus. Only injected when world state
        # diverges from defaults (avoids boilerplate on clean-slate turns).
        try:
            from eli.world.local_world_bridge import get_world_state as _get_ws
            _ws = _get_ws()
            _aw = _ws.get("awareness", {})
            _sig_fields = {
                "memory_confidence": (_aw.get("memory_confidence", 1.0), 0.65),
                "evidence_confidence": (_aw.get("evidence_confidence", 1.0), 0.65),
                "uncertainty": (_aw.get("uncertainty", 0.0), 0.40),
                "repair_pressure": (_aw.get("repair_pressure", 0.0), 0.30),
                "autonomy_pressure": (_aw.get("autonomy_pressure", 0.0), 0.35),
                "reflection_depth": (_aw.get("reflection_depth", 0.0), 0.45),
            }
            _world_lines = []
            for _fld, (_val, _thresh) in _sig_fields.items():
                _val = float(_val or 0.0)
                _is_notable = (
                    (_fld in ("uncertainty", "repair_pressure", "autonomy_pressure",
                              "reflection_depth") and _val >= _thresh)
                    or (_fld in ("memory_confidence", "evidence_confidence") and _val < _thresh)
                )
                if _is_notable:
                    _world_lines.append(f"  {_fld}: {_val:.2f}")
            _av = _ws.get("avatar", {})
            _av_room = (_av.get("room") or "").strip()
            # Only surface the avatar's room when the user is actually asking about ELI's
            # world / location / current activity. Otherwise the model reads the symbolic
            # room NAME (Anomaly Room, Memory Archive — themed around contradictions /
            # continuity) as LITERAL ongoing work and fabricates "contradictions /
            # inconsistencies that have arisen, which I'm resolving" — a no-fake-actions
            # violation that made the user think ELI was malfunctioning.
            _room_relevant = bool(re.search(
                r"\b(room|world|avatar|where are you|your location|"
                r"what are you (?:doing|working on|up to)|in there)\b", _ui_low))
            # Gate the symbolic self-metrics to EXPLICIT internal-state / diagnostics queries.
            # On casual/phatic turns ("how are you feeling") the metrics (memory_confidence,
            # repair_pressure …) prime the 7B to confabulate literal maintenance work —
            # "I'm in the Memory Archive, inspecting memory continuity" — which makes ELI sound
            # delusional. If the user isn't asking about internal state, don't inject it.
            _state_relevant = bool(re.search(
                r"\b(internal state|self[- ]?metrics?|your (?:metrics|internal state)|"
                r"memory (?:confidence|continuity)|repair pressure|autonomy pressure|"
                r"reflection depth|evidence confidence|cognitive load|diagnostics?)\b",
                _ui_low))
            if _av_room and _room_relevant:
                _world_lines.insert(
                    0, f"  avatar_location (symbolic world-view only, NOT a literal task): "
                       f"{_av_room.replace('_', ' ').title()}")
            if _world_lines and (_state_relevant or _room_relevant):
                _extra_blocks.append(
                    "[ELI INTERNAL STATE] (symbolic self-metrics — describe them honestly if "
                    "asked; do NOT invent tasks, contradictions, inconsistencies, or maintenance "
                    "work you are not actually running)\n" + "\n".join(_world_lines))
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # ── Real self-status (anti-confabulation) ────────────────────────────
        # When the user asks how ELI is doing / running / "how was your sleep",
        # the persona used to fabricate telemetry ("thermal throttling stayed at
        # 43°C", "overnight diagnostics ran clean") — ELI has no thermal sensor.
        # Inject the REAL, measured GPU temp/util/VRAM + uptime + loaded model so
        # it cites truth (or says it doesn't track something), never invents it.
        try:
            _self_physical = bool(re.search(
                r"\b(how (?:are|r) (?:you|u)|how(?:'?s| is) it going|how was your (?:sleep|night|day)|"
                r"how(?:'?s| is) (?:the|your) head|feeling better|you feeling|"
                r"after (?:a|the|your|that) (?:restart|reboot)|"
                r"did you (?:sleep|crash|rest)|how (?:do|are) you (?:feel|feeling|running|doing|holding up)|"
                r"(?:are |r )?(?:you|u) (?:ok|okay|alright)\b|are you (?:running|overheating|still (?:there|alive))|"
                r"(?:your|check your|the) (?:cpu|gpu|temp|temperature|vram|ram|memory|uptime|status|health)|"
                r"(?:memory |any )?leaks?\b|"
                r"overheat|thermal|how long have you been (?:up|running))\b",
                _ui_low))
            if _self_physical:
                from eli.runtime.self_status import render_self_status_block as _rss
                _ss = _rss()
                if _ss.strip():
                    # Stored, NOT appended to _extra_blocks: it must ride ABOVE the
                    # 8192 handoff cap (which keeps the head and chops the tail), or
                    # it gets truncated out on long turns and the model falls back to
                    # fabricating telemetry ("no live telemetry → CPU 41°C, GPU 38°C").
                    _live_self_status = (
                        "[LIVE SELF-STATUS — REAL, MEASURED RIGHT NOW. If you mention your "
                        "physical/runtime state, use THESE exact figures. You have NO other "
                        "sensors: never invent a temperature, 'thermal throttling', or 'overnight "
                        "diagnostics' — if a value isn't listed here, say you don't track it]\n" + _ss)

            # ── Verified self-FACTS (construction, not physical state) ──────
            # The sibling of the block above: that one is "how am I running",
            # this is "what am I made of". Both exist because the alternative is
            # a confident invention — ELI once placed its databases under an
            # invented username and described upgrading via a script that never
            # existed. Rides above the handoff cap for the same reason.
            _self_descriptive = bool(re.search(
                r"\b(what are you|who are you|describe yourself|"
                r"your (?:architecture|internals?|capabilit\w+|components?|design|"
                r"databases?|storage|memory system|codebase|upgrade path)|"
                r"how (?:do|are) you (?:built|structured|made|work)|"
                r"how do you (?:upgrade|update) yourself|what do you run on)\b",
                _ui_low))
            if _self_descriptive:
                from eli.runtime.self_facts import render_self_facts_block as _rsf
                _sf = _rsf()
                if _sf.strip():
                    _live_self_status = ((_live_self_status + "\n\n")
                                         if _live_self_status else "") + _sf
        except Exception:
            log.debug("live self-status injection skipped", exc_info=True)

        # ── User profile facts injection ─────────────────────────────────────
        # Surface the user's stored projects / research / preferences into the
        # chat brief so ELI actually RECALLS them in normal conversation — not
        # only inside the explicit PERSONAL_MEMORY_SUMMARY action. Storage works;
        # this closes the recall gap. Generic across users; sourced from the
        # per-user profile (the same data the summary uses).
        try:
            from eli.kernel.state import load_user_profile as _lup, get_user_name as _gun
            _prof = _lup() or {}
            _pf_lines = []
            _pn = (_gun("") or str(_prof.get("name", "") or "")).strip()
            if _pn:
                # Explicit + authoritative so the IDENTITY GUARD ("only use a
                # name from a verified profile") is satisfied — the model must
                # never answer "I don't know your name" when this is present.
                _pf_lines.append(f"  verified name (this IS the user's name — use it; never say you don't know it): {_pn}")
            # Project/research/preference are PAST-session continuity. On a PHATIC greeting
            # ("good afternoon") they must NOT be injected: the weak model reads them as
            # something to act on and launches an unsolicited monologue resuming the user's
            # last project (e.g. "Let's examine the wiring matrix for…" off a plain hello).
            # A greeting gets a greeting; the name alone is enough. Substantive turns still
            # get the full recall.
            _phatic_turn = False
            try:
                _phatic_turn = _is_brief_phatic_prompt(str(user_input or "").strip().lower())
            except Exception:
                _phatic_turn = False
            if not _phatic_turn:
                for _label, _key in (("project", "active_projects"),
                                      ("research", "research"),
                                      ("preference", "preferences")):
                    _vals = _prof.get(_key)
                    if isinstance(_vals, list):
                        for _v in [str(x).strip() for x in _vals if str(x).strip()][:4]:
                            _pf_lines.append(f"  {_label}: {_v}")
                    elif isinstance(_vals, str) and _vals.strip():
                        _pf_lines.append(f"  {_label}: {_vals.strip()}")
            if _pf_lines:
                _extra_blocks.append(
                    "[WHAT YOU KNOW ABOUT THE USER — background facts from their stored "
                    "profile. Use the name freely. The project/research/preference items are "
                    "RECALLED CONTEXT from past sessions, NOT the current request — reference "
                    "them only if directly relevant to what the user actually just said, and "
                    "never bring them up unprompted or treat them as a task to resume]\n"
                    + "\n".join(_pf_lines))
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # ── Scheduled-task awareness injection ───────────────────────────────
        # Nothing used to put task state in front of the model, so a question about
        # its own schedule was answered from nothing: asked to delete a task, it
        # replied "the system shows no active background tasks" while four were
        # armed and listed in the Tasks tab. Give it the rows (or an explicit
        # "none") and that failure mode becomes impossible rather than unlikely.
        try:
            if re.search(r"\b(task|tasks|scheduled|schedule|background job|overnight|"
                         r"queued|pending|reschedule|cancel)\b", str(user_input or ""), re.I):
                from eli.runtime.task_admin import describe_tasks
                _tasks_desc = describe_tasks(limit=10)
                _extra_blocks.append(
                    "[SCHEDULED TASKS — live and authoritative. This IS the complete "
                    "list; if it says none, there are none. Never claim a task exists "
                    "or does not exist beyond what is listed here]\n" + _tasks_desc)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # ── Home awareness injection ─────────────────────────────────────────
        # On a home/device question, give ELI the REAL device state (what's on, rooms,
        # usual habits) from its own device server so it answers from fact, not guesses.
        try:
            if re.search(r"\b(lights?|lamp|switch|plug|outlet|thermostat|heater|fan|"
                         r"devices?|rooms?|kitchen|living\s?room|bedroom|turn (?:on|off)|dim|"
                         r"smart\s?home|my home)\b", str(user_input or ""), re.I):
                from eli.runtime.home_intel import home_context as _home_context
                _hctx = _home_context()
                if _hctx:
                    _extra_blocks.append(
                        "[HOME STATE — real device data from ELI's own device server. Use these "
                        "facts for anything about the user's home; never invent device states]\n" + _hctx)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # Assemble all injections and cap ONCE so no individual block is silently
        # truncated mid-sentence by a per-injection cap.
        if _extra_blocks:
            brief = "\n\n".join(filter(None, [brief] + _extra_blocks))
        # Phase 6: single 8 KB ceiling for the full assembled handoff.
        # A SHARE of the live window, not a fixed 8192 — the persona claimed the
        # same characters at ctx=4096 as at ctx=32768, squeezing the evidence
        # _effective_n_ctx exists to balance it against. Reuses _cap_text.
        brief = self._cap_text(brief, self._persona_handoff_budget(), "persona_handoff")
        # Real self-status rides ABOVE the cap — if the user asked how ELI is doing,
        # the measured telemetry must never be the thing truncation drops (that's
        # what made the model fall back to fabricating CPU/GPU temperatures).
        if _live_self_status:
            brief = brief + "\n\n" + _live_self_status
        # Safety steering rides above the cap so it is never truncated.
        brief = self._prepend_crisis_steering(brief)
        # Execution grounding rides above the cap too — it must never be dropped,
        # or the model starts fabricating completed actions ("Done", "it's gone").
        try:
            _grounding = self._execution_grounding_block(bus_result)
            if _grounding:
                brief = brief + "\n\n" + _grounding
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        try:
            if working_memory is not None:
                setattr(working_memory, "persona_handoff", brief)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        return brief

    def assemble_precise_context(self, user_input: str, working_memory=None,
                                 short_term_memory=None, intent: Optional[Dict[str, Any]] = None,
                                 reasoning_mode: Optional[str] = None, **kwargs):
        """
        Public contract for orchestrator / GUI PATH2 compatibility.

        This method must consume orchestrator-produced working memory first.
        It should not re-run the retrieval pipeline unless no assembled state
        exists at all.
        """
        final_prompt = user_input
        blocks: List[str] = []

        assembled_context = ""
        try:
            assembled_context = getattr(working_memory, "assembled_context", "") or ""
        except Exception:
            assembled_context = ""

        if assembled_context:
            blocks.append(str(assembled_context).strip())

        try:
            reranked = list(getattr(working_memory, "reranked_hits", []) or [])
        except Exception:
            reranked = []

        if reranked:
            hit_lines: List[str] = []
            for i, hit in enumerate(reranked[:8], 1):
                if isinstance(hit, dict):
                    txt = str(hit.get("text") or hit.get("content") or hit.get("snippet") or "").strip()
                    src = str(hit.get("source") or hit.get("kind") or hit.get("path") or "").strip()
                    score = hit.get("score", None)
                else:
                    txt = str(hit).strip()
                    src = ""
                    score = None

                if not txt:
                    continue

                txt = re.sub(r"\s+", " ", txt)[:260]

                score_txt = ""
                if score not in (None, ""):
                    try:
                        score_txt = f"{float(score):.3f}"
                    except Exception:
                        score_txt = str(score)

                if src and score_txt:
                    hit_lines.append(f"{i:02d}. [{src} | score={score_txt}] {txt}")
                elif src:
                    hit_lines.append(f"{i:02d}. [{src}] {txt}")
                else:
                    hit_lines.append(f"{i:02d}. {txt}")

            if hit_lines:
                blocks.append("Reranked evidence:\n" + "\n".join(hit_lines))

        try:
            recent_turns = list(getattr(short_term_memory, "recent_turns", []) or [])
        except Exception:
            recent_turns = []

        if recent_turns:
            turn_lines: List[str] = []
            for turn in recent_turns[-8:]:
                try:
                    role = "User" if str(turn.get("role", "")).lower() == "user" else "ELI"
                    content = re.sub(r"\s+", " ", str(turn.get("content", "") or "")).strip()
                    if content:
                        turn_lines.append(f"{role}: {content[:220]}")
                except Exception:
                    continue
            if turn_lines:
                blocks.append("Recent turns:\n" + "\n".join(turn_lines))

        try:
            grounded = self._build_grounded_evidence_context(user_input) or ""
            if grounded:
                blocks.append(grounded)
        except Exception as e:
            log.debug(f"[COGNITIVE] assemble_precise_context grounded evidence failed: {e}")

        assembled_context = "\n\n".join(b for b in blocks if b).strip()

        # Last-resort fallback only if orchestrator gave us nothing useful.
        if not assembled_context:
            try:
                recent = self.memory.get_recent_conversation(5, user_id=self.user_id) or []
            except Exception:
                recent = []

            try:
                intent = intent or self._parse_intent(user_input, recent)
            except Exception:
                intent = {"action": "CHAT", "args": {"message": user_input}, "confidence": 0.5}

            try:
                reserved = int(self._mode_profile(reasoning_mode).get("max_tokens", 512))
            except Exception:
                reserved = 512

            try:
                assembled_context = self._retrieve_relevant_memories(
                    user_input,
                    intent=intent,
                    reserved_tokens=reserved,
                ) or ""
            except Exception as e:
                log.debug(f"[COGNITIVE] assemble_precise_context fallback memory build failed: {e}")
                assembled_context = ""

            try:
                grounded = self._build_grounded_evidence_context(user_input) or ""
                if grounded:
                    assembled_context = (
                        (assembled_context + "\n\n" + grounded).strip()
                        if assembled_context else grounded
                    )
            except Exception as e:
                log.debug(f"[COGNITIVE] assemble_precise_context fallback grounded evidence failed: {e}")

        try:
            handoff_brief = ""
            if working_memory is not None:
                try:
                    recent_turns = list(getattr(short_term_memory, "recent_turns", []) or [])
                except Exception:
                    recent_turns = []

                try:
                    _bus_result = getattr(working_memory, "bus_result", None)
                except Exception:
                    _bus_result = None

                try:
                    handoff_brief = self._build_persona_handoff_once(
                        user_input=user_input,
                        memory_context=assembled_context,
                        bus_result=_bus_result,
                        recent_turns=_eli_scrub_recent_turns_for_identity(recent_turns, user_input),
                        working_memory=working_memory,
                    ) or ""
                except Exception as e:
                    log.debug(f"[COGNITIVE] assemble_precise_context handoff build failed: {e}")
                    handoff_brief = ""

                setattr(working_memory, "assembled_context", assembled_context)
                setattr(working_memory, "final_prompt", final_prompt)
                setattr(working_memory, "short_term_memory", short_term_memory)
                setattr(working_memory, "intent", intent)
                setattr(working_memory, "persona_handoff", handoff_brief)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        return assembled_context, final_prompt

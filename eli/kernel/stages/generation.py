"""Stage: model generation — chat response, streaming, finalisation.

The model-facing half of the pipeline: it takes an assembled prompt, calls the
local model (streaming or one-shot), governs and normalises the visible text,
runs the rapport/semantic conversational guards, and finalises the chat result.
Composed onto CognitiveEngine as a mixin, so every method still reads engine
state through `self`.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any, Dict, Generator, List, Optional

from eli.cognition.output_governor import govern_output
from eli.cognition.output_governor import normalize_assistant_text as _output_governor_normalize
from eli.execution.router_enhanced import route as route_intent
from eli.execution.executor_enhanced import chat as ollama_chat
from eli.execution.executor_enhanced import execute as execute_action

from eli.kernel.stages._common import (
    _HAS_GOVERNANCE,
    _eli_test_mode,
    _is_brief_phatic_prompt,
    normalize_response,
)
from eli.kernel.stages.persona_text import (
    _eli_sanitize_identity_context_block,
    _load_persona_text,
)
from eli.kernel.stages.output_text import _normalize_assistant_text
from eli.kernel.stages.model_runtime import gguf_inference, _get_inference_broker
# Tokens reserved for the reply when sizing the evidence budget. `max_tokens` is a
# ceiling (~3461 in quick mode) and reserving all of it starved the evidence — see
# the note at the streaming context-size guard. A reply longer than this simply eats
# into the 20% headroom the budget already keeps.
_OUTPUT_RESERVE_TOKENS = int(os.environ.get("ELI_OUTPUT_RESERVE_TOKENS", "1024"))


from eli.kernel.stages.guards import (
    _RepeatDetected,
    _failed_executor_is_failed_block,
    _failed_executor_query_from_prompt,
    _failed_executor_relevant_block,
    _failed_executor_surface,
    _deprimed_persona_brief,
    _is_greeting_turn,
    _redact_prior_replies,
    _REPEAT_RETRY_TEMP_BUMP,
    _stream_holding_back_repeats,
    _user_asked_for_a_repeat,
)
from eli.kernel.stages.fast_actions import (
    _PHASE45_DIRECT_FAST_ACTIONS,
    _PHASE45_SILENT_FAST_ACTIONS,
    _phase45_action_name,
    _phase45_force_direct_result,
)
from eli.utils.log import get_logger

log = get_logger(__name__)


def _eli_conversation_semantic_guard(user_text: str) -> str:
    """
    Dynamic semantic guard for local-persona conversation.
    Does not hardcode the user's name or profile.
    Prevents the model from flipping ELI-directed metaphors into generic
    medical/legal/encyclopaedic explanations.
    """
    raw = str(user_text or "")
    low = raw.lower()
    notes = []

    eli_directed = any(x in low for x in (" eli", "eli,", "you ", "your ", "on you", "to you"))
    repair_metaphor = any(x in low for x in (
        "lobotomised", "lobotomized", "lobotomy",
        "open-head surgery", "open head surgery",
        "head screwed", "screwed back on",
        "personality gone", "persona not up to date",
    ))

    if eli_directed and repair_metaphor:
        notes.append(
            "SEMANTIC GUARD: The user is using repair/surgery/lobotomy language as a metaphor "
            "for ELI's code, persona, memory, or cognition being patched. Do NOT explain human "
            "brain surgery, medical procedures, craniotomy, hospitals, emotions, or patient care. "
            "Answer as ELI about the local system state, recent repair context, persona drift, "
            "memory behaviour, and what still needs fixing."
        )

    if any(x in low for x in ("what is my name", "preferred name", "nickname", "who am i")):
        notes.append(
            "IDENTITY GUARD: Only use a user's name if it comes from a verified current profile "
            "or high-confidence explicit memory evidence. Do not infer names from stale snippets, "
            "old assistant outputs, Linux usernames, examples, or poisoned conversation turns. "
            "If identity is unknown, say it is unknown and state what evidence was checked."
        )

    if notes:
        return "\n".join(notes).strip()
    return ""

def _eli_is_rapport_prompt(text: str) -> bool:
    import re
    q = re.sub(r"[^a-z0-9' ]+", " ", str(text or "").lower()).strip()
    q = re.sub(r"\s+", " ", q)

    if not q:
        return False

    direct_hits = {
        "hi", "hello", "hey", "yo", "hiya", "alright",
        "you there", "are you there",
        "whats up", "what's up",
        "whats up buddy", "what's up buddy",
        "whats up bud", "what's up bud",
        "whats up pal", "what's up pal",
    }
    if q in direct_hits:
        return True

    rapport_patterns = [
        r"\bwhat'?s up\b",
        r"\byou alive\b",
        r"\bfucked in the head\b",            # any form: still/so/just fucked in the head
        r"\bhow'?s the head\b",
        r"\bhow is the head\b",
        r"\bwe back\b",
        r"\bnormal self\b",
        r"\bopen[- ]head surgery\b",
        r"\bbrain still\b",
        r"\bare we good\b",
        r"\byou good\b",
        r"\bback to normal\b",
        # Rough "is your STATE okay / are you still broken" check-ins — these are rapport
        # about ELI's wellbeing, not insults to deflect or lecture about.
        r"\bare you (?:still |so |even |actually )?(?:ok|okay|broken|buggy|glitch(?:y|ing)?|"
        r"malfunction(?:ing)?|fixed|sane|insane|mad|crazy|working|messed up|off|right)\b",
        r"\bstill (?:broken|buggy|glitch(?:y|ing)?|messed up|off|borked|scrambled)\b",
        r"\b(?:lost the plot|gone (?:mad|crazy|insane)|losing it|out of your mind)\b",
        r"\byou (?:fixed|sorted|better) now\b",
        # Canonical rapport check-ins — the most common "how are you" forms were missing,
        # so "how are you feeling" fell through to full memory recall and the model recited
        # stored facts ("you mentioned liking coffee") instead of just answering.
        r"\bhow (?:are|r|'?re) (?:you|ya|u)(?: doing| feeling| going| holding up| keeping)?\b",
        r"\bhow (?:you|ya) (?:doing|feeling|going|holding up)\b",
        r"\bhow(?:'?s| is| are things| are we)? it (?:going|hanging)\b",
        r"\bhow'?s (?:it|things) (?:going|hanging)\b",
        r"\bhow have you been\b",
        r"\byou feeling\b",
        r"\bhow do you feel\b",
        r"\bhow goes it\b",
    ]

    return any(re.search(pat, q) for pat in rapport_patterns)

def _eli_rapport_prompt_instruction(text: str) -> str:
    if not _eli_is_rapport_prompt(text):
        return ""

    return (
        "RAPPORT MODE:\\n"
        "- The latest user message is casual check-in / banter, not a request for a sterile status report.\\n"
        "- Stay model-owned: do not use canned templates or fixed greeting replies.\\n"
        "- Respond as ELI: direct, nerdy, dry, sarcastic if useful, but still helpful.\\n"
        "- Treat rough language from the user as familiar banter unless there is a real safety issue.\\n"
        "- Do not moralise about the wording. Do not say the user is being inappropriate.\\n"
        "- Do not answer with only 'yes' or 'functioning as intended'.\\n"
        "- A rough state-check ('are you fucked in the head?', 'are you still broken/buggy?', "
        "'you sorted now?') is the user asking, bluntly, whether you are still MALFUNCTIONING. "
        "It is a real, understandable question — answer it. Say plainly whether you're running "
        "clean now, and own recent glitches with dry humour if relevant. NEVER claim it 'doesn't "
        "make sense', NEVER ask them to clarify a clear question, NEVER deflect to 'let's focus on "
        "the task' or lecture about 'unnecessary banter / rhetorical questions'.\\n"
        "- A plain feeling/check-in ('how are you feeling?', 'how's it going?') wants a SHORT, "
        "in-character answer about how you're doing — nothing else. Do NOT recite stored facts "
        "about the user ('you mentioned liking coffee'), do NOT dump memories, runtime, or your "
        "internal state unprompted, and do NOT bolt on unrelated info. Answer the question, then "
        "optionally ask what they want to do.\\n"
        "- Acknowledge the mood, show some wit, then ask what the user wants to work on or notice.\\n"
    )


class GenerationMixin:
    def _yield_text_chunks(
        self, text: str, chunk_size: int = 48) -> Generator[str, None, None]:
        text = text or ""
        for i in range(0, len(text), chunk_size):
            yield text[i:i + chunk_size]

    def _get_chat_response(self, prompt: str, memory_context: str = "",
                           reasoning_mode: Optional[str] = None, gen_overrides: Optional[Dict[str, Any]] = None,
                           situation_brief: str = "") -> str:
        # Scoped check: extract the executor-result lines only (avoids false
        # positives from persona notes / old failure history in the prompt).
        _fail_block = _failed_executor_relevant_block(prompt)
        if _fail_block and _failed_executor_is_failed_block(_fail_block):
            return _failed_executor_surface(_fail_block, _failed_executor_query_from_prompt(prompt))
        # Broad fallback: scoped extraction may miss inline failures.
        _p_low = str(prompt or "").lower()
        if (
            ("'ok': false" in _p_low or '"ok": false' in _p_low
             or "ok=false" in _p_low or "filenotfounderror" in _p_low
             or "file not found" in _p_low or "successful: 0 | failed:" in _p_low)
            and ("execute result" in _p_low or "agent:system" in _p_low
                 or "grounded_evidence" in _p_low or "analyze_pdf" in _p_low)
        ):
            return _failed_executor_surface(
                prompt, _failed_executor_query_from_prompt(prompt)
            )

        # ── Verbatim guard for deterministic grounded reports (EXAMINE_CODE / FILE_AUDIT) ──
        # These are deterministic grounded output (tiered findings / a directory file-count).
        # Re-narrating them through this chat path makes the model confabulate: EXAMINE_CODE
        # gets the file_code agent's snippets/comments paraphrased as "bugs" (observed: 5
        # invented findings that were existing comments); FILE_AUDIT (a plain file-counter)
        # gets synthesised into files-with-bugs that DON'T EXIST (observed: 5 fabricated
        # files). If the report is present in the evidence, return it VERBATIM.
        _verbatim_evidence_patterns = (
            (r"(Examined\s+\d+\s+file\(s\)\s*:[\s\S]+)",
             ("Tier 1", "No errors found", "PLEASE CONFIRM")),
            (r"(File Audit:[\s\S]+)", ("Directories scanned:", "Total files:")),
        )
        for _pat, _markers in _verbatim_evidence_patterns:
            for _exam_src in (situation_brief, memory_context, prompt):
                _em = re.search(_pat, str(_exam_src or ""))
                if _em and any(_mk in _em.group(1) for _mk in _markers):
                    _rep = re.split(
                        r"\n\s*(?:USER QUESTION:|GROUNDED EVIDENCE \(|Recent ELI reflections|"
                        r"Knowledge graph|Live agents \()", _em.group(1))[0].strip()
                    if _rep:
                        return _rep

        # FIX_FILE success event: the executor JSON-encodes {event:artifact_generated,
        # fixed:true, path, filename, backup}. Synthesising it made the model narrate a FALSE
        # refusal ("I cannot fix the file from here") even though the file WAS written. Surface
        # the real outcome instead of letting the model confabulate one.
        for _ff_src in (situation_brief, memory_context, prompt):
            _ff_txt = str(_ff_src or "")
            if re.search(r'"event"\s*:\s*"artifact_generated"', _ff_txt) and \
               re.search(r'"fixed"\s*:\s*true', _ff_txt):
                _fn = re.search(r'"filename"\s*:\s*"([^"]+)"', _ff_txt)
                _pm = re.search(r'"path"\s*:\s*"([^"]+)"', _ff_txt)
                _name = _fn.group(1) if _fn else (_pm.group(1) if _pm else "the file")
                _has_bak = bool(re.search(r'"backup"\s*:\s*"[^"]+"', _ff_txt))
                return (f"✅ Fixed `{_name}`"
                        + (" — a timestamped backup was saved" if _has_bak else "")
                        + ". The corrected file is open in the editor.")

        if _eli_test_mode():
            gen_overrides = dict(gen_overrides or {})
            gen_overrides["max_tokens"] = min(
                int(gen_overrides.get("max_tokens", 96)), 96)
            gen_overrides["temperature"] = 0.0

        provider = self._current_provider()
        gen = self._generation_settings()
        if gen_overrides:
            gen.update({k: v for k, v in dict(
                gen_overrides).items() if v is not None})

        if not self._gguf_available and gguf_inference is not None:
            _ovr = gguf_inference.get_live_runtime_override() or {}
            if _ovr.get("loaded"):
                self._gguf_available = True
                self._gguf_load_error = None
            else:
                try:
                    self._init_gguf()
                except Exception:
                    log.debug("suppressed exception", exc_info=True)

        try:
            _mode_contract = dict(getattr(self, "_last_mode_execution_contract", {}) or {})
            if _mode_contract:
                self._maybe_apply_mode_runtime_adaptation(reasoning_mode, _mode_contract)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        if self._gguf_available:
            try:
                if gguf_inference is None:
                    raise RuntimeError("GGUF module not available")

                # ── Context size guard ──────────────────────────────────────
                # For short queries on small context windows, trim memory to
                # prevent prompt bloat and multi-minute first-token latency.
                # Rule: persona (~2k chars) + user_input + max_tokens must fit
                # in n_ctx with room to spare. Memory context gets the budget
                # that remains after persona + query.
                _n_ctx_guard = self._runtime_n_ctx()
                _max_tok_guard = int(gen.get('max_tokens', 512))
                _persona_chars = len(_load_persona_text())
                _query_chars = len(prompt or '')
                # Rough: 1 token ≈ 3.5 chars; leave 20% headroom
                _total_char_budget = int(_n_ctx_guard * 3.5 * 0.80)
                _mem_char_budget = max(
                    400,  # always allow at least some context
                    _total_char_budget - _persona_chars - _query_chars - (_max_tok_guard * 4)
                )
                _trimmed_mem = _eli_sanitize_identity_context_block(memory_context, prompt)
                if len(memory_context) > _mem_char_budget:
                    log.debug(
                        f"[COGNITIVE] Trimming memory context "
                        f"{len(memory_context)}→{_mem_char_budget} chars "
                        f"(n_ctx={_n_ctx_guard}, max_tokens={_max_tok_guard})"
                    )
                    # Prefer keeping the most recent turns (end of string)
                    _trimmed_mem = memory_context[-_mem_char_budget:]
                    # Don't cut mid-line
                    _nl = _trimmed_mem.find('\n')
                    if _nl > 0:
                        _trimmed_mem = _trimmed_mem[_nl:]
                # ─────────────────────────────────────────────────────────────

                enhanced_system = self._build_enhanced_system(
                    _trimmed_mem,
                    compact=self._use_compact_system(
                        prompt, _trimmed_mem, reasoning_mode=reasoning_mode),
                    user_input=prompt,
                    reasoning_mode=reasoning_mode,
                    situation_brief=situation_brief,
                )
                log.debug("[COGNITIVE] Generating response with GGUF...")
                broker = _get_inference_broker() if _get_inference_broker else None
                if broker and broker.gguf_ready:
                    log.debug("[COGNITIVE] Using broker path")
                    # Pre-flight: clamp max_tokens so prompt+output fits n_ctx.
                    # CRITICAL: use the LIVE loaded model's n_ctx, not the
                    # configured value. Mistral may have fallen back to 1024
                    # ctx if VRAM was tight; the configured 4096 is a lie.
                    # Start from the live loaded ctx (authoritative), not a
                    # hard-coded default; the override chain below confirms it.
                    _n_ctx_pf1 = self._runtime_n_ctx()
                    # Override with the LIVE loaded value.  Priority chain:
                    #   1. _live_runtime_params["n_ctx"] — set by gguf_inference
                    #      after a successful load; always correct.
                    #   2. runtime_snapshot.json effective.n_ctx — written by
                    #      the GUI launcher immediately after load.
                    #   3. llm.n_ctx() method — last resort (may throw).
                    # gen.get("n_ctx") / settings["n_ctx"] is the CONFIGURED
                    # value and is often stale — do NOT use it as the source.
                    try:
                        import eli.cognition.gguf_inference as _eli_gguf_mod
                        _lrp = getattr(_eli_gguf_mod, "_live_runtime_params", None) or {}
                        _lrp_ctx = int(_lrp.get("n_ctx", 0))
                        if _lrp_ctx > 0:
                            _n_ctx_pf1 = _lrp_ctx
                        else:
                            # Fallback: runtime snapshot file
                            import json as _jsnap
                            from eli.core.paths import project_root as _eli_root
                            _snap_path = _eli_root() / "artifacts" / "runtime_snapshot.json"
                            if _snap_path.exists():
                                _snap = _jsnap.loads(_snap_path.read_text(encoding="utf-8"))
                                _snap_ctx = int(
                                    (_snap.get("effective") or {}).get("n_ctx")
                                    or _snap.get("n_ctx")
                                    or 0
                                )
                                if _snap_ctx > 0:
                                    _n_ctx_pf1 = _snap_ctx
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
                    # Token estimate uses 3.5 chars/token (Mistral-ish);
                    # earlier code used 3 which under-estimated and let
                    # oversized prompts through.
                    _pt_pf1 = max(1, int((len(enhanced_system) + len(prompt)) / 3.5))
                    _avail_pf1 = max(128, _n_ctx_pf1 - _pt_pf1 - 64)
                    _req_pf1 = int(gen["max_tokens"])
                    _safe_max_pf1 = _avail_pf1 if _req_pf1 <= 0 else max(128, min(_req_pf1, _avail_pf1))
                    if 0 < _req_pf1 < _safe_max_pf1:
                        log.debug(
    f"[COGNITIVE] Clamping max_tokens {_req_pf1}→{_safe_max_pf1} (est={_pt_pf1}, n_ctx={_n_ctx_pf1})")
                    # Hard guard: if the prompt itself exceeds n_ctx, truncate
                    # it before sending — an oversized prompt crashes llama.cpp.
                    # Use 3.0 chars/token here (conservative) so we under-budget.
                    _max_prompt_chars = max(400, int((_n_ctx_pf1 - _safe_max_pf1 - 128) * 3.0))
                    # Quality ceiling (context-bloat cap): the small local model
                    # degenerates into a lone "-"/"-G" on very large prompts long
                    # BEFORE n_ctx fills — a 39k-char WEB_SEARCH synthesis produced
                    # "-G" even though it fit n_ctx. Cap to a sane size independent
                    # of n_ctx so the model gets a prompt it can actually answer.
                    # Tunable: ELI_SYNTH_MAX_PROMPT_CHARS (set 0 to disable).
                    try:
                        from eli.core.cognition_tunables import get_tunable as _cog_get
                        _qenv = os.environ.get("ELI_SYNTH_MAX_PROMPT_CHARS")
                        _fixed_cap = _cog_get("cog.synth_max_prompt_chars")
                        if _qenv is not None:
                            _qcap = int(_qenv or "0")  # explicit env override wins
                        elif _cog_get("cog.synth_cap_auto"):
                            # Auto-scale to the loaded model: ~45% of the context
                            # window (chars≈3×tokens) × capability tier, never
                            # below the fixed cap. For the current small model this
                            # stays at the fixed cap (floor dominates).
                            try:
                                from eli.core.model_tier import tier_scale as _ts
                                _derived = int(_n_ctx_pf1 * 3.0 * 0.45 * _ts())
                            except Exception:
                                _derived = 0
                            _qcap = max(_fixed_cap, _derived)
                        else:
                            _qcap = _fixed_cap
                    except Exception:
                        _qcap = 20000
                    if _qcap > 0:
                        _max_prompt_chars = min(_max_prompt_chars, _qcap)
                    if len(enhanced_system) + len(prompt) > _max_prompt_chars:
                        # Give the user prompt at least 25% of the budget so the
                        # actual question is never fully clipped.
                        _prompt_budget = max(200, min(len(prompt), _max_prompt_chars // 4))
                        _sys_budget = max(200, _max_prompt_chars - _prompt_budget)
                        if len(enhanced_system) > _sys_budget:
                            # Keep persona HEAD (voice + hard constraints) AND the
                            # grounded evidence TAIL (appended last); drop the bulky
                            # middle (profile/scaffolding/memory dump) — that is what
                            # bloats the prompt without being the answer's substance.
                            _head = max(200, int(_sys_budget * 0.5))
                            _tail = max(200, _sys_budget - _head)
                            enhanced_system = (
                                enhanced_system[:_head].rstrip()
                                + "\n\n…[context trimmed to fit the model]…\n\n"
                                + enhanced_system[-_tail:].lstrip()
                            )
                        prompt = prompt[-_prompt_budget:]
                        log.debug(
    f"[COGNITIVE] Prompt capped to {_max_prompt_chars}chars "
    f"(head+tail; n_ctx={_n_ctx_pf1}, qcap={_qcap})")
                    response = broker.infer(
                        prompt,
                        system=enhanced_system,
                        max_tokens=_safe_max_pf1,
                        temperature=gen["temperature"],
                    )
                else:
                    log.debug("[COGNITIVE] Using direct GGUF path (broker unavailable)")
                    _n_ctx_pf2 = self._runtime_n_ctx()
                    _pt_pf2 = max(1, (len(enhanced_system) + len(prompt)) // 3)
                    _avail_pf2 = max(128, _n_ctx_pf2 - _pt_pf2 - 64)
                    _req_pf2 = int(gen["max_tokens"])
                    _safe_max_pf2 = _avail_pf2 if _req_pf2 <= 0 else max(128, min(_req_pf2, _avail_pf2))
                    if 0 < _req_pf2 < _safe_max_pf2:
                        log.debug(
    f"[COGNITIVE] Direct GGUF: clamping max_tokens {_req_pf2}→{_safe_max_pf2} (est={_pt_pf2}, n_ctx={_n_ctx_pf2})")
                    # Hard guard for direct path too
                    _max_prompt_chars2 = max(400, (_n_ctx_pf2 - _safe_max_pf2 - 64) * 3)
                    if len(enhanced_system) + len(prompt) > _max_prompt_chars2:
                        _sys_budget2 = min(len(enhanced_system), _max_prompt_chars2 // 2)
                        _prompt_budget2 = _max_prompt_chars2 - _sys_budget2
                        enhanced_system = enhanced_system[-_sys_budget2:]
                        prompt = prompt[-_prompt_budget2:]
                        log.debug(
    f"[COGNITIVE] Direct GGUF overflow: truncated to fit n_ctx={_n_ctx_pf2}")
                    with self._gguf_lock:
                        response = gguf_inference.chat_completion(
                            prompt,
                            system=enhanced_system,
                            max_tokens=_safe_max_pf2,
                            temperature=gen["temperature"],
                        )
                if response:
                    return _normalize_assistant_text(prompt, response)
                raise RuntimeError("GGUF returned empty response")
            except Exception as e:
                self._gguf_available = False
                self._gguf_load_error = str(e)
                log.debug(f"[COGNITIVE] GGUF chat failed: {e}")
                if provider not in ("ollama",):
                    return f"[ELI] GGUF error: {e}"
        if provider not in ("ollama",):
            detail = self._gguf_load_error or "GGUF model unavailable"
            return f"[ELI] Model not ready: {detail}. Check config/settings.json."
        log.debug("[COGNITIVE] Falling back to Ollama for chat response")
        try:
            full_prompt = f"{memory_context}\n\nUser: {prompt}\n\nELI:" if memory_context else prompt
            result = ollama_chat(full_prompt, skip_router=True)
            if isinstance(result, dict):
                response = result.get(
    "content", "").strip() or result.get(
        "response", "").strip()
            else:
                response = str(result).strip()
            return _normalize_assistant_text(prompt, response)
        except Exception as e:
            log.debug(f"[COGNITIVE] Ollama fallback also failed: {e}")
            return f"I encountered an error while generating a response: {e}"

    def _stream_model_response(self, prompt: str, memory_context: str = "",
                               reasoning_mode: Optional[str] = None, gen_overrides: Optional[Dict[str, Any]] = None,
                               situation_brief: str = "") -> Generator[str, None, None]:
        """
        TRUE STREAMING: use generate() directly with stream=True.
        Yields tokens as they are generated — not after full completion.
        Falls back to broker.infer() only when generate() is unavailable.
        situation_brief: pre-synthesised context brief from ContextSynthesiser.
        When provided it replaces the raw memory_context in the system prompt.
        """
        # TRUE STREAMING: use generate() directly
        provider = self._current_provider()
        gen = self._generation_settings()
        if gen_overrides:
            gen.update({k: v for k, v in dict(
                gen_overrides).items() if v is not None})

        # Self-heal: GUI loads model via live override without setting _gguf_available
        if not self._gguf_available and gguf_inference is not None:
            _ovr = gguf_inference.get_live_runtime_override() or {}
            if _ovr.get("loaded"):
                self._gguf_available = True
                self._gguf_load_error = None
        if self._gguf_available and gguf_inference is not None:
            try:
                try:
                    _mode_contract = dict(getattr(self, "_last_mode_execution_contract", {}) or {})
                    if _mode_contract:
                        self._maybe_apply_mode_runtime_adaptation(reasoning_mode, _mode_contract)
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
                # ── Context size guard (same logic as non-streaming path) ──
                # Use the model's REAL usable ceiling (min of loaded n_ctx and
                # n_ctx_train), not the config default — a model whose trained context
                # is smaller than the requested/loaded ctx must be sized to its trained
                # length or the prompt overflows ("Requested tokens exceed context
                # window"). Falls back to the config value only if no model is loaded.
                _n_ctx = 0
                try:
                    if hasattr(gguf_inference, "current_context_limit"):
                        _n_ctx = int(gguf_inference.current_context_limit() or 0)
                except Exception:
                    _n_ctx = 0
                if _n_ctx <= 0:
                    _n_ctx = self._runtime_n_ctx()
                _max_tok_s = int(gen.get('max_tokens', 512))
                _persona_chars_s = len(_load_persona_text())
                _query_chars_s = len(prompt or '')
                _total_char_budget_s = int(_n_ctx * 3.5 * 0.80)
                # Reserve for OUTPUT realistically. `max_tokens` is a CEILING, not an
                # expectation: in quick mode it is ~3461, so `_max_tok_s * 4` reserved
                # ~13.8k chars of a ~29k budget for output a chat reply never produces.
                # Observed live in v2: the bus gathered 6,352 chars of evidence for
                # "are these dynamic and true awareness, not hard-coding?" — including a
                # 1,415-char introspection result — at grounding 0.98, and this line
                # trimmed it to 685 chars. The model then answered "let's pretend it
                # works for now", handed almost none of the evidence fetched to answer it.
                #
                # The budget already keeps 20% headroom (the 0.80 above), and llama.cpp
                # enforces n_ctx regardless, so reserving the full ceiling was doubly
                # conservative. A longer generation simply uses the headroom.
                _out_reserve_tok_s = min(_max_tok_s, _OUTPUT_RESERVE_TOKENS)
                _mem_char_budget_s = max(
                    400,
                    _total_char_budget_s - _persona_chars_s - _query_chars_s - (_out_reserve_tok_s * 4)
                )
                _trimmed_mem_s = _eli_sanitize_identity_context_block(memory_context, prompt)
                if len(memory_context) > _mem_char_budget_s:
                    log.debug(
                        f"[COGNITIVE] Stream: trimming memory context "
                        f"{len(memory_context)}→{_mem_char_budget_s} chars"
                    )
                    _trimmed_mem_s = memory_context[-_mem_char_budget_s:]
                    _nl_s = _trimmed_mem_s.find('\n')
                    if _nl_s > 0:
                        _trimmed_mem_s = _trimmed_mem_s[_nl_s:]
                # ──────────────────────────────────────────────────────────

                # Conversational context injection (current session only, last 30 min).
                #  (a) Short follow-ups (≤6 words) get the recent exchange inline so
                #      the model sees the current message in immediate context.
                #  (b) CONFUSION signals — "what do you mean", "i don't understand",
                #      "can you elaborate", "huh?", "what?", "you're looping", etc. —
                #      ALSO get the recent exchange PLUS a directive to locate exactly
                #      where the confusion arose and resolve it, instead of looping or
                #      repeating the previous reply.
                _orig_msg = (prompt or "").strip()
                _low_msg = _orig_msg.lower()
                _confusion = bool(re.search(
                    r"\b(what do you mean|what does that mean|what'?s that mean|"
                    r"can you (?:elaborate|clarify|explain)|please (?:elaborate|clarify|explain)|"
                    r"\belaborate\b|\bclarif(?:y|ication)\b|i don'?t (?:understand|get it|follow)|"
                    r"i'?m (?:confused|lost|not following)|that (?:doesn'?t|does not) make sense|"
                    r"you'?re not making sense|makes no sense|come again|say that again|"
                    r"are you (?:talking about|on about|referring to)|"
                    r"you keep (?:saying|asking)|"
                    r"that'?s not what i|i didn'?t ask|"
                    r"you'?re (?:slack(?:ing)?|drift(?:ing)?|repeating|looping)|"
                    r"starting to (?:slack|drift|loop|repeat))\b",
                    _low_msg,
                )) or _low_msg.rstrip("?!. ") in {"what", "huh", "eh", "sorry", "pardon", "you what"}
                _short_followup = len(_orig_msg.split()) <= 6
                if _confusion or _short_followup:
                    try:
                        _session_cutoff = time.time() - 1800  # 30 minutes
                        _recent = self.memory.get_recent_conversation(
                            limit=8, user_id=self.user_id)
                        _prompt_key = _orig_msg[:80]
                        _thread = [
                            t for t in _recent
                            if float(t.get("timestamp") or 0) >= _session_cutoff
                            and not (t.get("role") == "user"
                                     and (t.get("content") or "").strip()[:80] == _prompt_key)
                        ]
                        if len(_thread) >= 2:
                            _n = 5 if _confusion else 3
                            _thread_lines = []
                            for _t in _thread[-_n:]:
                                _r = "You" if _t.get("role") == "user" else "ELI"
                                _c = (_t.get("content") or "").replace("\n", " ")[:180]
                                _thread_lines.append(f"{_r}: {_c}")
                            _block = "[Recent exchange]\n" + "\n".join(_thread_lines)
                            if _confusion:
                                _block += (
                                    "\n\n[The user is signalling confusion or asking you to clarify. "
                                    "Re-read the recent exchange above and pinpoint SPECIFICALLY what "
                                    "caused it — a word you used, a claim you made, or a topic you raised "
                                    "that they never mentioned. State plainly where the misunderstanding "
                                    "is, then resolve it with a concrete answer or fix. Do NOT repeat your "
                                    "previous reply, and do NOT re-ask a question they already rejected.]"
                                )
                            prompt = _block + "\n\nYou: " + _orig_msg
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)

                enhanced_system = self._build_enhanced_system(
                    _trimmed_mem_s,
                    compact=self._use_compact_system(
                        prompt, _trimmed_mem_s, reasoning_mode=reasoning_mode),
                    user_input=prompt,
                    reasoning_mode=reasoning_mode,
                    situation_brief=situation_brief,
                )
                # Pre-flight clamp
                _pt = max(1, (len(enhanced_system) + len(prompt)) // 3)
                _avail_s = max(128, _n_ctx - _pt - 64)
                _req_s = int(gen["max_tokens"])
                _safe_max = _avail_s if _req_s <= 0 else max(128, min(_req_s, _avail_s))
                if 0 < _req_s < _safe_max:
                    log.debug(
    f"[COGNITIVE] Stream: clamping {_req_s}→{_safe_max} (est={_pt}, n_ctx={_n_ctx})")
                # Hard guard: if combined prompt still exceeds n_ctx, truncate
                # before calling generate() — an oversized prompt causes segfault.
                _max_stream_chars = max(400, (_n_ctx - _safe_max - 64) * 3)
                if len(enhanced_system) + len(prompt) > _max_stream_chars:
                    _sys_bud = min(len(enhanced_system), _max_stream_chars // 2)
                    _prm_bud = _max_stream_chars - _sys_bud
                    enhanced_system = enhanced_system[-_sys_bud:]
                    prompt = prompt[-_prm_bud:]
                    log.debug(
    f"[COGNITIVE] Stream overflow: truncated to fit n_ctx={_n_ctx}")

                generate = getattr(gguf_inference, "generate", None)
                if callable(generate):
                    log.debug("[COGNITIVE] Stream: using generate() with stream=True")
                    _yielded = False
                    with self._gguf_lock:
                        for chunk in generate(
                            prompt,
                            system=enhanced_system,
                            max_tokens=_safe_max,
                            temperature=gen["temperature"],
                            stream=True,
                        ):
                            if isinstance(chunk, dict):
                                token = str(
    chunk.get("response") or chunk.get("token") or "")
                            else:
                                token = str(chunk or "")
                            if token:
                                _yielded = True
                                yield token
                    if _yielded:
                        return
                    log.debug("[COGNITIVE] Stream: generate() produced zero visible tokens; falling back to non-streaming Stage 11")
                    response = self._get_chat_response(
                        prompt,
                        memory_context,
                        reasoning_mode=reasoning_mode,
                        gen_overrides={
                            "max_tokens": _safe_max,
                            "temperature": gen["temperature"],
                        },
                        situation_brief=situation_brief,
                    )
                    for token in self._yield_text_chunks(response or "", chunk_size=12):
                        yield token
                    return

                # generate() not available — use broker (pseudo-streaming)
                broker = _get_inference_broker() if _get_inference_broker else None
                if broker and broker.gguf_ready:
                    log.debug(
                        "[COGNITIVE] Stream: generate() unavailable, falling back to broker (pseudo-stream)")
                    response = broker.infer(
                        prompt,
                        system=enhanced_system,
                        max_tokens=_safe_max,
                        temperature=gen["temperature"],
                    )
                    for token in self._yield_text_chunks(
                        response or "", chunk_size=12):
                        yield token
                    return

            except Exception as e:
                _emsg = str(e)
                _ctx_overflow = ("context window" in _emsg.lower()
                                 or "requested tokens" in _emsg.lower())
                log.debug(f"[COGNITIVE] GGUF streaming failed: {_emsg}")
                # A context-window overflow is RECOVERABLE — it does not mean the GGUF
                # backend is dead. Don't flip _gguf_available (that poisons every later
                # turn), and never surface the raw exception text as ELI's reply. Retry
                # once via the non-streaming path: generate() now truncates an over-ctx
                # prompt to the model's real n_ctx_train instead of failing.
                if not _ctx_overflow:
                    self._gguf_available = False
                self._gguf_load_error = _emsg
                if provider != "ollama":
                    _fallback = ""
                    try:
                        _fallback = self._get_chat_response(
                            prompt,
                            memory_context,
                            reasoning_mode=reasoning_mode,
                            gen_overrides={
                                "max_tokens": int(gen.get("max_tokens") or 256),
                                "temperature": gen.get("temperature", 0.7),
                            },
                            situation_brief=situation_brief,
                        )
                    except Exception as _e2:
                        log.debug(f"[COGNITIVE] non-stream fallback after stream failure also failed: {_e2}")
                    if _fallback and _fallback.strip():
                        for token in self._yield_text_chunks(_fallback, chunk_size=12):
                            yield token
                        return
                    if _ctx_overflow:
                        yield ("I couldn't fit my working context into this model — its trained "
                               "context window is smaller than the prompt I need to reason over. "
                               "Load a model with a larger context (e.g. Qwen3-8B) and I'll be back "
                               "to normal.")
                    else:
                        yield ("Something went wrong reaching the local model just now. The error "
                               "was logged — try again, and if it persists, reload the model.")
                    return

        if provider != "ollama":
            detail = self._gguf_load_error or "GGUF model unavailable"
            yield f"GGUF unavailable: {detail}"
            return

        response = self._get_chat_response(
    prompt,
    memory_context,
    reasoning_mode=reasoning_mode,
     gen_overrides=gen)
        for token in self._yield_text_chunks(response, chunk_size=12):
            yield token

    def _chat_generation_overrides(self, user_input: str, memory_context: str,
                                   reasoning_mode: Optional[str] = None) -> Dict[str, Any]:
        base = self._generation_settings()
        try:
            from eli.cognition.reasoning_modes import (
                build_mode_execution_contract as _eli_mode_contract,
                canonical_mode as _eli_canonical_mode,
            )

            mode = _eli_canonical_mode(reasoning_mode)
            profile = self._mode_profile(mode)
            runtime = self._live_runtime_snapshot()
            contract = _eli_mode_contract(
                mode,
                profile=profile,
                runtime_snapshot=runtime,
                query_text=user_input or "",
                memory_context=memory_context or "",
            )

            overrides = dict(contract.get("generation_overrides") or {})
            if int(base.get("max_tokens", 512)) <= 0:
                # Preserve unlimited-token pass-through contract where callers
                # explicitly configured max_tokens <= 0.
                overrides["max_tokens"] = -1

            self._last_mode_execution_contract = dict(contract)

            try:
                _rt = contract.get("runtime") or {}
                log.debug(
                    "[MODE][CONTRACT] "
                    f"mode={mode} max_tokens={overrides.get('max_tokens')} "
                    f"temp={overrides.get('temperature')} "
                    f"pressure={_rt.get('prompt_pressure')} "
                    f"target_ctx={_rt.get('target_n_ctx')} "
                    f"target_batch={_rt.get('target_n_batch')} "
                    f"target_gpu_layers={_rt.get('target_n_gpu_layers')}"
                )
            except Exception:
                log.debug("suppressed exception", exc_info=True)

            return overrides
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # Conservative fallback if mode-contract helpers fail.
        mode = str(reasoning_mode or "quick").strip().lower() or "quick"
        overrides: Dict[str, Any] = {"temperature": float(base.get("temperature", 0.7))}
        base_max = int(base.get("max_tokens", 512))
        if base_max <= 0:
            overrides["max_tokens"] = -1
            return overrides
        if mode == "quick":
            overrides["max_tokens"] = max(256, base_max)
            return overrides
        overrides["max_tokens"] = min(2200, max(768, base_max))
        return overrides

    def _finalize_chat_result(self, user_input: str, response: str, trace: Dict[str, Any], score: Optional[float] = None, threshold: Optional[
                              float] = None, clarified: bool = False, evidence_used: bool = False, reasoning_mode: Optional[str] = None) -> Dict[str, Any]:
        response = govern_output(response, is_grounded=evidence_used)
        response = str(response or "").strip()
        # ── Anti-echo: never serve ELI's own previous reply back to the user ──
        # ELI's replies are stored and later recalled as context, so on a short turn the
        # model can latch onto its own last line and re-emit it. Observed live: the same
        # "still glitchy, still running on the same old code" three turns running, once
        # with the speaker flipped ("You're still glitchy") while ignoring what the user
        # had just said. Token-level repeat_penalty cannot see across turns.
        try:
            from eli.cognition.output_governor import is_echo_of_recent
            _prev_replies = []
            try:
                for _t in (self.memory.get_recent_conversation(limit=8) or []):
                    _role = (_t.get("role") if isinstance(_t, dict) else None) or ""
                    _content = (_t.get("content") if isinstance(_t, dict) else "") or ""
                    if str(_role).lower() in ("assistant", "eli") and _content:
                        _prev_replies.append(str(_content))
            except Exception:
                log.debug("[ECHO] recent-reply lookup failed", exc_info=True)
            if _prev_replies and is_echo_of_recent(response, _prev_replies):
                log.debug("[ECHO] reply repeated a recent one — regenerating once")
                trace["echo_repaired"] = True
                _fresh = self._get_chat_response(
                    "You already said the following to this user, and repeating it would be "
                    "wrong — they have moved the conversation on.\n\n"
                    f"ALREADY SAID:\n{_prev_replies[-1]}\n\n"
                    f"WHAT THEY JUST SAID:\n{user_input}\n\n"
                    "Reply to what they actually just said, in your own voice. Do not reuse "
                    "the sentence above or restate your own status unprompted.",
                    "",
                    reasoning_mode=reasoning_mode,
                    gen_overrides={"max_tokens": 320, "temperature": 0.7},
                )
                _fresh = govern_output(str(_fresh or "").strip(), is_grounded=evidence_used).strip()
                if _fresh and not is_echo_of_recent(_fresh, _prev_replies):
                    response = _fresh
        except Exception:
            log.debug("[ECHO] anti-echo guard skipped", exc_info=True)
        try:
            from eli.cognition.reasoning_modes import apply_final_reasoning_contract as _rm_final
            response = _rm_final(response, mode=reasoning_mode)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        try:
            from eli.cognition.reasoning_modes import canonical_mode as _eli_final_mode
            _eli_mode_key = _eli_final_mode(reasoning_mode)
        except Exception:
            _eli_mode_key = str(reasoning_mode or "quick").strip().lower() or "quick"
        # Depth policy: only complete genuinely truncated non-quick responses.
        # Do not fire a second full inference just because the answer is short —
        # a concise correct answer is not truncation.
        try:
            if _eli_mode_key != "quick" and not _is_brief_phatic_prompt(user_input):
                from eli.cognition.output_governor import _looks_truncated
                if _looks_truncated(response):
                    _expand_prompt = (
                        "Complete the truncated answer below in the same voice and with the "
                        "same grounded facts. Finish the current thought; do not add new content.\n\n"
                        f"USER REQUEST:\n{user_input}\n\nTRUNCATED DRAFT:\n{response}\n\nCOMPLETED ANSWER:"
                    )
                    _wc = len(str(response or "").split())
                    _expanded = self._get_chat_response(
                        _expand_prompt,
                        "",
                        reasoning_mode=reasoning_mode,
                        gen_overrides={"max_tokens": 512, "temperature": 0.25},
                    )
                    _expanded = str(_expanded or "").strip()
                    _expanded_low = _expanded.lower()
                    _expanded_failed = any(
                        marker in _expanded_low
                        for marker in (
                            "model not ready",
                            "gguf error",
                            "gguf model unavailable",
                            "broker unavailable",
                            "inference failed",
                        )
                    )
                    if _expanded and not _expanded_failed and len(_expanded.split()) > _wc:
                        response = _expanded
                        try:
                            from eli.cognition.reasoning_modes import apply_final_reasoning_contract as _rm_final2
                            response = _rm_final2(response, mode=reasoning_mode)
                        except Exception:
                            log.debug("suppressed exception", exc_info=True)
        except Exception as _depth_err:
            log.debug(f"[COGNITIVE] non-quick depth expansion skipped: {_depth_err}")
        try:
            from eli.runtime.diagnostic_patterns import is_vague_dynamic_status_claim
            if is_vague_dynamic_status_claim(response):
                evidence_packet = self._build_dynamic_status_evidence(
                    user_input,
                    response,
                    trace=trace,
                )
                if evidence_packet:
                    synthesized = ""
                    try:
                        synthesized = self._synthesize_answer(
                            evidence_packet,
                            user_input,
                            reasoning_mode=reasoning_mode,
                            max_tokens_override=384,
                            action="META_DIAGNOSTIC",
                        ).strip()
                    except Exception as _dyn_syn_err:
                        log.debug(f"[COGNITIVE] Dynamic-status evidence synthesis failed: {_dyn_syn_err}")
                    response = synthesized or evidence_packet
                    evidence_used = True
        except Exception as _dyn_guard_err:
            log.debug(f"[COGNITIVE] Dynamic-status guard skipped: {_dyn_guard_err}")
        # Strip role-leakage artefacts ([HH:MM] User: …, "Assistant:", etc.)
        try:
            from eli.cognition.response_sanitizer import sanitize_assistant_text as _san
            response = _san(response) or response
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        grounded = bool(evidence_used)

        if threshold is None or float(threshold) <= 0.0:
            threshold = self._required_confidence_threshold(
                reasoning_mode, grounded=grounded)
        if score is None:
            score = threshold

        score = float(score)
        threshold = float(threshold)

        log.debug(
            f"[COGNITIVE][FINAL] final score={score:.2f} "
            f"threshold={threshold:.2f} clarified={bool(clarified)} "
            f"evidence_used={grounded}"
        )

        trace["final"] = {
            "response_chars": len(response),
            "score": score,
            "threshold": threshold,
            "clarified": bool(clarified),
            "evidence_used": grounded,
        }

        try:
            self._publish_last_response_meta(
                trace,
                action=str(((trace or {}).get("intent") or {}).get("action") or "CHAT"),
                result_action="CHAT",
                confidence=score,
                agents_used=list((trace or {}).get("agents_used") or []),
                evidence_used=grounded,
                grounded=grounded,
                response=response,
            )
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        self._store_assistant_turn(response)
        self._maybe_store_memory(user_input, role="user")
        self._maybe_store_memory(response, role="assistant")

        # Feed actual response confidence back into the engagement tracker
        try:
            if self._engagement:
                self._engagement.update_confidence(score)
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        # Blueprint Post-Response: Weight Decay (runs ~1% of responses to amortise cost)
        try:
            import random as _random
            if _random.random() < 0.01:
                _decayed = self.memory.apply_weight_decay()
                if _decayed:
                    log.debug(f"[MEMORY] Weight decay applied to {_decayed} old memories")
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        reasoning_meta = {
            "confidence": score,
            "threshold": threshold,
            "grounded": grounded,
            "clarified": bool(clarified),
            "evidence_used": grounded,
        }

        try:
            from eli.runtime.diagnostic_patterns import should_exclude_turn_from_prompt as _eli_exclude_turn
            from eli.runtime.evidence_ledger import record_event as _eli_record_event

            _eli_record_event(
                "assistant_final",
                source="cognitive_engine.finalize",
                action=str(((trace or {}).get("intent") or {}).get("action") or "CHAT"),
                subject=str((trace or {}).get("request_id") or ""),
                content=response,
                payload={
                    "score": score,
                    "threshold": threshold,
                    "grounded": grounded,
                    "clarified": bool(clarified),
                    "evidence_used": grounded,
                    "excluded_from_prompt": _eli_exclude_turn("assistant", response),
                },
                outcome="ok",
                confidence=score,
                reusable=not _eli_exclude_turn("assistant", response),
                session_id=str(getattr(self, "session_id", "") or ""),
                user_id=str(getattr(self, "user_id", "") or ""),
                request_id=str((trace or {}).get("request_id") or ""),
            )
        except Exception:
            log.debug("suppressed exception", exc_info=True)


        return {
            "ok": True,
            "action": "CHAT",
            "content": response,
            "response": response,
            "confidence": score,
            "confidence_score": score,
            "confidence_threshold": threshold,
            "evidence_used": grounded,
            "meta": {"reasoning": reasoning_meta, "trace": trace},
            "trace": trace,
        }

    def generate_from_assembled_prompt(self, prompt: str,
                                       working_memory=None,
                                       reasoning_mode: Optional[str] = None,
                                       raw_direct: bool = False, **kwargs):
        # FAST PATH: raw_direct bypasses persona/handoff/memory build.
        # Used by HyDE (retrieval seed generation) where we want a tiny,
        # quick answer — no system prompt customisation, no context assembly.
        if raw_direct:
            try:
                from eli.cognition.gguf_inference import gguf_try_infer
                _raw_system = (
                    "You are a knowledge assistant. Write a short factual answer "
                    "(2-3 sentences, no roleplay, no filler)."
                )
                response = gguf_try_infer(
                    prompt,
                    system=_raw_system,
                    max_tokens=96,
                    temperature=0.4,
                    lock_timeout=2.0,
                )
                return (response or "").strip()
            except Exception as e:
                log.debug(f"[COGNITIVE] raw_direct fast path failed: {e}; falling back")
                # fall through to slow path
        memory_context = ""
        try:
            memory_context = getattr(working_memory, "assembled_context", "") or ""
        except Exception:
            memory_context = ""

        try:
            _stm = getattr(working_memory, "short_term_memory", None)
            recent_turns = list(getattr(_stm, "recent_turns", []) or [])
        except Exception:
            recent_turns = []

        try:
            _bus_result = getattr(working_memory, "bus_result", None)
        except Exception:
            _bus_result = None

        try:
            situation_brief = self._build_persona_handoff_once(
                user_input=prompt,
                memory_context=memory_context,
                bus_result=_bus_result,
                recent_turns=recent_turns,
                working_memory=working_memory,
            ) or ""
        except Exception as e:
            log.debug(f"[COGNITIVE] generate_from_assembled_prompt handoff failed: {e}")
            situation_brief = ""

        _gen_overrides = None
        try:
            _gen_overrides = self._chat_generation_overrides(
                prompt,
                memory_context,
                reasoning_mode=reasoning_mode,
            )
        except Exception:
            _gen_overrides = None

        return self._get_chat_response(
            prompt,
            memory_context,
            reasoning_mode=reasoning_mode,
            gen_overrides=_gen_overrides,
            situation_brief=situation_brief,
        ).strip()

    def _govern_visible_response(
        self,
        user_input: str,
        text: str,
        *,
        memory_context: str = "",
        is_grounded: bool = False,
    ) -> str:
        response = _normalize_assistant_text(user_input, str(text or "").strip())
        response = _output_governor_normalize(user_input, response)
        if _HAS_GOVERNANCE:
            # normalize_response = the GGUF-artifact cleaner
            # clean_gguf_artifacts(response, user_input). The old swapped-arg
            # TypeError fallback is gone now the signature collision with
            # output_governor.normalize_response(user_input, text) is resolved.
            try:
                response = normalize_response(response, user_input)
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        response = govern_output(response, is_grounded=is_grounded, evidence=memory_context)
        try:
            from eli.cognition.reasoning_modes import apply_final_reasoning_contract as _rm_final
            response = _rm_final(response)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        try:
            from eli.runtime.diagnostic_patterns import is_vague_dynamic_status_claim
            if is_vague_dynamic_status_claim(response):
                evidence_packet = self._build_dynamic_status_evidence(
                    user_input,
                    response,
                    trace=getattr(self, "_last_trace", {}) or {},
                )
                if evidence_packet:
                    synthesized = ""
                    try:
                        synthesized = self._synthesize_answer(
                            evidence_packet,
                            user_input,
                            max_tokens_override=384,
                            action="META_DIAGNOSTIC",
                        ).strip()
                    except Exception:
                        synthesized = ""
                    response = synthesized or evidence_packet
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        if not response:
            response = "I lost the thread during generation. Ask it again and I will rebuild from current context."
        return response.strip()

    def generate_stream_from_assembled_prompt(self, prompt: str,
                                              working_memory=None,
                                              reasoning_mode: Optional[str] = None,
                                              gen_overrides: Optional[Dict[str, Any]] = None,
                                              **kwargs):
        memory_context = ""
        situation_brief = ""
        try:
            memory_context = getattr(working_memory, "assembled_context", "") or ""
            situation_brief = getattr(working_memory, "persona_handoff", "") or ""
        except Exception:
            memory_context = ""
            situation_brief = ""

        # Private reasoning modes run the real multi-pass algorithms (CoT, ToT,
        # CAI, SC) then buffer the finished result before yielding chunks.
        # Quick mode streams live.  Raw private-strategy chunks must never reach
        # the GUI before the final sanitiser runs on them.
        try:
            from eli.cognition.reasoning_modes import (
                is_private_reasoning_mode as _rm_private,
                canonical_mode as _rm_canonical,
            )
            if _rm_private(reasoning_mode):
                _rm_key = _rm_canonical(reasoning_mode)
                _rm_profile = self._mode_profile(_rm_key)
                _rm_gen_overrides = self._chat_generation_overrides(
                    prompt, memory_context, reasoning_mode=_rm_key,
                )
                situation_brief_rm = ""
                try:
                    _stm_rm = getattr(working_memory, "short_term_memory", None)
                    _recent_rm = list(getattr(_stm_rm, "recent_turns", []) or [])
                    _bus_rm = getattr(working_memory, "bus_result", None)
                    situation_brief_rm = str(
                        self._build_persona_handoff_once(
                            user_input=prompt,
                            memory_context=memory_context,
                            bus_result=_bus_rm,
                            recent_turns=_recent_rm,
                            working_memory=working_memory,
                        ) or ""
                    )
                except Exception:
                    situation_brief_rm = ""
                # Expose grounding confidence to the per-mode algorithms
                # (constitutional grounded-trust override, #3b/Option C).
                try:
                    self._current_grounding_confidence = float(
                        getattr(_bus_rm, "grounding_confidence", 0.0) or 0.0
                    )
                except Exception:
                    self._current_grounding_confidence = 0.0
                # Route to the real per-mode algorithm; falls back to a single
                # _get_chat_response call if _run_mode_algorithm returns None.
                final = None
                if self._supports_mode_algorithm(_rm_key):
                    try:
                        final = self._run_mode_algorithm(
                            _rm_key, prompt, memory_context,
                            _rm_gen_overrides, situation_brief_rm,
                        )
                        log.debug(
                            f"[COGNITIVE][STREAM] private mode {_rm_key}: "
                            f"algorithm produced {len(final or '')} chars"
                        )
                    except Exception as _algo_err:
                        log.debug(
                            f"[COGNITIVE][STREAM] {_rm_key} algorithm failed: "
                            f"{_algo_err} — falling back to single pass"
                        )
                        final = None
                if not final:
                    final = self._get_chat_response(
                        prompt, memory_context,
                        reasoning_mode=_rm_key,
                        gen_overrides=_rm_gen_overrides,
                        situation_brief=situation_brief_rm,
                    )
                final = self._govern_visible_response(
                    str(prompt or ""),
                    str(final or ""),
                    memory_context=memory_context,
                    is_grounded=bool(memory_context),
                )
                for piece in self._yield_text_chunks(final, chunk_size=12):
                    yield piece
                return
        except Exception as _rm_stream_err:
            log.debug(f"[COGNITIVE] Private reasoning buffered stream failed; falling back to guarded stream: {_rm_stream_err}")

        try:
            _stm = getattr(working_memory, "short_term_memory", None)
            recent_turns = list(getattr(_stm, "recent_turns", []) or [])
        except Exception:
            recent_turns = []

        try:
            _bus_result = getattr(working_memory, "bus_result", None)
        except Exception:
            _bus_result = None

        if not situation_brief:
            try:
                situation_brief = self._build_persona_handoff_once(
                    user_input=prompt,
                    memory_context=memory_context,
                    bus_result=_bus_result,
                    recent_turns=recent_turns,
                    working_memory=working_memory,
                ) or ""
            except Exception as e:
                log.debug(f"[COGNITIVE] generate_stream_from_assembled_prompt handoff failed: {e}")
                situation_brief = ""

        def _live_stream() -> Generator[str, None, None]:
            """
            True GUI streaming path.

            Important:
            - Yield model chunks as they arrive.
            - Do not wait for full completion before yielding.
            - Do only tiny prefix cleanup at stream start.
            - Full final governance/storage happens outside this generator.
            """
            import re as _re

            raw_parts: List[str] = []
            yielded_any = False

            # Small start buffer prevents leaking "ELI:" / "Assistant:" prefixes
            # without destroying real streaming.
            start_buffer = ""
            prefix_resolved = False

            def _clean_start(s: str) -> str:
                return _re.sub(
                    r"^\s*(?:ELI|Assistant|AI|<\|assistant\|>)\s*:\s*",
                    "",
                    str(s or ""),
                    flags=_re.I,
                )

            def _still_possible_role_prefix(s: str) -> bool:
                low = str(s or "").lower()
                stripped = low.strip()
                if not stripped:
                    return True
                prefixes = (
                    "e", "el", "eli", "eli:",
                    "a", "as", "ass", "assi", "assis", "assist", "assista",
                    "assistan", "assistant", "assistant:",
                    "ai", "ai:",
                    "<|assistant|>", "<|assistant|>:",
                )
                return stripped in prefixes and len(low) < 24

            for chunk in self._stream_model_response(
                prompt,
                memory_context,
                reasoning_mode=reasoning_mode,
                gen_overrides=gen_overrides,
                situation_brief=situation_brief,
            ):
                piece = str(chunk or "")
                if not piece:
                    continue

                raw_parts.append(piece)

                if not prefix_resolved:
                    start_buffer += piece

                    # Hold only while the model is clearly spelling a role prefix.
                    if _still_possible_role_prefix(start_buffer):
                        continue

                    prefix_resolved = True
                    cleaned = _clean_start(start_buffer)
                    if cleaned:
                        yielded_any = True
                        yield cleaned
                    continue

                yielded_any = True
                yield piece

            # If the model only emitted a tiny prefix buffer, flush a cleaned version.
            if not prefix_resolved and start_buffer:
                cleaned = _clean_start(start_buffer)
                if cleaned:
                    yielded_any = True
                    yield cleaned

            # Emergency fallback only. Normal path should already have yielded live.
            if not yielded_any:
                final_text = self._govern_visible_response(
                    prompt,
                    "".join(raw_parts),
                    memory_context=memory_context or situation_brief,
                    is_grounded=bool(memory_context or situation_brief),
                )
                if final_text:
                    for token in self._yield_text_chunks(final_text, chunk_size=12):
                        yield token

        # Streaming generator handoff must yield sub-generator tokens live.
        # Returning the generator object here can collapse visible streaming.
        yield from _live_stream()
        return

    def _stream_with_followthrough(self, inner, user_input: str,
                                   reasoning_mode: Optional[str] = None) -> Generator[str, None, None]:
        """Wrap the CHAT token stream so NO action is faked (engine-level, every
        consumer). Streams the reply, then — if ELI committed to or faked a task
        ("let me check the news", "fetching…", "[Story 1]") — actually re-runs
        the pipeline and yields the REAL result. If it says it, it does it.
        Recursion-guarded; only yields when a genuine non-chat task executes."""
        parts: list = []
        for tok in inner:
            try:
                parts.append(str(tok or ""))
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            yield tok
        full = "".join(parts).strip()
        if not full or getattr(self, "_in_followthrough", False):
            return
        try:
            from eli.runtime.action_commitment import detect_action_commitment as _dc
            commit = _dc(full)
        except Exception:
            commit = None
        if not commit:
            return
        self._in_followthrough = True
        try:
            # Topic-deepen guard: if the previous real action was a news briefing
            # and the user asked to go deeper on a specific topic ("look closer
            # into Hubble"), re-fetch THAT topic — not ELI's lossy paraphrase,
            # which drops the topic and dumps the whole briefing again.
            _query = commit["clause"]
            try:
                import re as _ft_re
                from eli.runtime.action_commitment import extract_deepen_topic as _edt
                _deepen = _edt(user_input)
                _lca = getattr(self, "_last_command_action", None) or {}
                _was_news = str(_lca.get("action") or "").upper() in (
                    "NEWS_FETCH", "MORNING_REPORT", "DAILY_REPORT")
                _clause_newsish = bool(_ft_re.search(
                    r"\bnews|headline|stor(?:y|ies)|latest|update\b",
                    commit["clause"], _ft_re.I))
                if _deepen and (_was_news or _clause_newsish):
                    _query = f"fetch the latest news about {_deepen}"
            except Exception:
                _query = commit["clause"]
            real = self.process(_query, stream=False, reasoning_mode=reasoning_mode)
            if isinstance(real, dict):
                real_act = str(real.get("action") or "").upper()
                real_txt = str(real.get("content") or real.get("response") or "").strip()
            else:
                real_act, real_txt = "", str(real or "").strip()
            # Followthrough is for ACTIONS ELI promised to perform FOR the user (fetch news,
            # play media, search, open) — never for internal status/introspection DUMPS. A
            # casual narration ("I've been checking my memory") must not auto-run MEMORY_STATUS
            # and carpet-bomb the user with evidence they didn't ask for (the "why did you send
            # me that data dump" complaint).
            _ft_dump_actions = {
                "MEMORY_STATUS", "PERSONAL_MEMORY_SUMMARY", "USER_IDENTITY_SUMMARY",
                "EXPLAIN_MEMORY_RUNTIME", "EXPLAIN_COGNITION_RUNTIME", "AWARENESS_STATUS",
                "META_DIAGNOSTIC", "SELF_ANALYZE", "RUNTIME_AUDIT", "REASONING_MODE_STATUS",
                "EXPLAIN_ALL_REASONING_MODES", "CAPABILITY", "CAPABILITY_STATUS",
                "HABIT_STATUS", "ORCHESTRATION_STATUS", "EXAMINE_CODE", "FILE_AUDIT",
            }
            if (real_txt and real_act not in ("", "CHAT", "UNKNOWN", "NOOP")
                    and real_act not in _ft_dump_actions):
                log.debug(f"[FOLLOWTHROUGH] '{commit.get('matched')}' → executed {real_act}")
                yield "\n\n" + real_txt
            elif real_act in _ft_dump_actions:
                log.debug(f"[FOLLOWTHROUGH] suppressed status/dump action {real_act} (not a user-requested task)")
        except Exception as _ft_err:
            log.debug(f"[FOLLOWTHROUGH] engine-stream skipped: {_ft_err}")
        finally:
            self._in_followthrough = False

    def _stream_chat(self, user_input: str, args: dict, context: list,
                     reasoning_mode: Optional[str] = None,
                     pre_built_memory_context: str = "",
                     pre_built_bus_result: Optional[Any] = None) -> Generator[str, None, None]:

        # Direct OS/audio controls that intentionally return no visible GUI text must
        # execute before GGUF/stream fallback. Otherwise the no-visible-output guard
        # mistakes a successful silent action for a broken generation.
        try:
            _p41_text = str(user_input or "").strip()
            if _p41_text:
                from eli.execution.router_enhanced import route as _p41_route
                from eli.execution.executor_enhanced import execute as _p41_execute
        
                _p41_r = _p41_route(_p41_text)
                _p41_action = None
                _p41_args = {}
        
                if isinstance(_p41_r, dict):
                    _p41_action = _p41_r.get("action")
                    _p41_args = _p41_r.get("args") or {}
                elif isinstance(_p41_r, (tuple, list)) and _p41_r:
                    _p41_action = _p41_r[0]
                    if len(_p41_r) > 1 and isinstance(_p41_r[1], dict):
                        _p41_args = _p41_r[1]
                else:
                    _p41_action = getattr(_p41_r, "action", None)
                    _p41_args = getattr(_p41_r, "args", {}) or {}
        
                _p41_silent_actions = {"VOLUME"}
                if str(_p41_action or "").upper() in _p41_silent_actions:
                    _p41_res = _p41_execute(str(_p41_action).upper(), _p41_args)
                    log.debug(f"[COGNITIVE][PHASE41] silent direct action {_p41_action} executed; suppressing GUI output: {_p41_res}")
                    # Yield a zero-width space so the GUI generator is non-empty and
                    # does NOT trigger the stream=False double-call fallback.
                    yield "\u200b"
                    return
        except Exception as _p41_err:
            log.debug(f"[COGNITIVE][PHASE41] silent direct fastpath skipped: {_p41_err}")

        """
        Canonical single-dispatch streaming path.

        Rules:
        - process() may already have run AgentBus; reuse that context.
        - Do not call process() again.
        - Do not let the GUI perform a non-streaming rerun.
        - If the primary stream path yields nothing, fall back inside this same
          engine method directly to gguf_inference.generate().
        """
        from types import SimpleNamespace
        import time as _time

        prompt = str((args or {}).get("message") or user_input or "").strip()
        started = _time.perf_counter()
        _eli_pipeline_trace = str(__import__("os").environ.get("ELI_PIPELINE_TRACE", "")).strip().lower() in {"1", "true", "yes", "on"}
        _eli_pipeline_req = str(getattr(self, "_pipeline_req_id", "") or "n/a")

        def _eli_pipe_stream(stage: str, **fields) -> None:
            if not _eli_pipeline_trace:
                return
            try:
                parts = [f"stage={stage}", f"req={_eli_pipeline_req}"]
                for k, v in fields.items():
                    parts.append(f"{k}={v}")
                log.debug("[PIPELINE][ENGINE_STREAM] " + " ".join(parts))
            except Exception:
                log.debug("suppressed exception", exc_info=True)

        if not prompt:
            return

        # Streaming GUI path must also bypass inference for commands.
        try:
            _p45_intent = route_intent(prompt)
            _p45_action = _phase45_action_name((_p45_intent or {}).get("action"))
            if _p45_action in _PHASE45_DIRECT_FAST_ACTIONS:
                _p45_args = (_p45_intent or {}).get("args", {}) or {}
                _p45_started = _time.perf_counter()
                _p45_raw = execute_action(_p45_action, _p45_args)
                _p45_result = _phase45_force_direct_result(_p45_action, _p45_raw)
                _p45_text = str(_p45_result.get("response") or _p45_result.get("content") or "")
                log.debug(f"[PHASE45] stream direct command {_p45_action} completed in {_time.perf_counter() - _p45_started:.3f}s")
                # Silent commands still yield a zero-width marker so the GUI does
                # not think the stream failed and print the "no visible output" error.
                if not _p45_text and _p45_action in _PHASE45_SILENT_FAST_ACTIONS:
                    yield "\u200b"
                    return
                if _p45_text:
                    yield _p45_text
                    return
                yield "\u200b"
                return
        except Exception as _p45_stream_err:
            log.debug(f"[PHASE45] stream command fastpath failed: {_p45_stream_err}")

        # (SHORT_AMBIGUOUS_INPUT_FASTPATH removed — short/one-word inputs like
        # "Hi" now reach the model normally instead of being rejected with
        # "Didn't catch that — say it again?")

        # ELI_REASONING_MODE_RECOVERY_V1
        # Some indirect stream paths may omit the explicit reasoning_mode kwarg.
        # process() stamps the active mode on self; recover it here to preserve
        # non-Quick mode contracts in Stage 11 and fallback guards.
        if not reasoning_mode:
            reasoning_mode = getattr(self, "_current_reasoning_mode", None) or None

        log.debug(
            f"[COGNITIVE][PIPELINE] stream_chat begin "
            f"req={_eli_pipeline_req} "
            f"mode={reasoning_mode or 'quick'} "
            f"prompt_chars={len(prompt)} "
            f"prebuilt_ctx={bool(pre_built_memory_context)} "
            f"prebuilt_bus={bool(pre_built_bus_result)}"
        )
        _eli_pipe_stream(
            "begin",
            mode=(reasoning_mode or "quick"),
            prompt_chars=len(prompt),
            prebuilt_ctx=bool(pre_built_memory_context),
            prebuilt_bus=bool(pre_built_bus_result),
        )

        _rapport_mode = _eli_is_rapport_prompt(prompt)

        try:
            import re as _re
            _phatic_low = _re.sub(r"[^a-z0-9\' ]+", "", prompt.lower()).strip()
            _phatic_stream = _phatic_low in {
                "hi", "hello", "hey", "yo", "hiya", "how are you",
                "whats up", "what\'s up", "whats up pal", "what\'s up pal",
                "alright", "you there", "are you there"
            }
        except Exception:
            _phatic_stream = False

        # 1. Memory / evidence context. Reuse process()-built context when present.
        # Rapport prompts deliberately skip deep memory/HyDE retrieval: casual banter should not be
        # converted into a corporate status query or safety lecture.
        if _rapport_mode:
            # Skip deep memory/HyDE retrieval but inject the user profile so
            # ELI knows who it's talking to and can respond personally rather
            # than with generic chatbot filler.
            memory_context = ""
            try:
                from eli.kernel.state import get_user_profile_text as _gup
                _profile_txt = (_gup() or "").strip()
                if _profile_txt:
                    memory_context = f"USER PROFILE:\n{_profile_txt}"
            except Exception:
                log.debug("suppressed exception", exc_info=True)
            log.debug("[COGNITIVE] Stream: rapport prompt — lightweight profile context only")
            _eli_pipe_stream("context_mode", mode="rapport_profile_only")
        elif _phatic_stream:
            memory_context = ""
            log.debug("[COGNITIVE] Stream: phatic prompt — skipping memory/evidence context")
            _eli_pipe_stream("context_mode", mode="phatic_skip")
        elif pre_built_memory_context and not any(p in prompt.lower() for p in (
            # Force a fresh cross-session fetch for memory/recall queries — the
            # prebuilt context was built for the *prior* turn without cross-session
            # and won't contain prior-session content even when needed now.
            "last conversation", "last chat", "what did i say", "what have i said",
            "pick up where", "left off", "where were we", "what were we working on",
            "previous session", "prior session", "recall", "past conversation",
            "what did we", "what have we", "remember", "what were we discussing",
        )):
            memory_context = str(pre_built_memory_context or "")
            log.debug("[COGNITIVE] Stream: reusing pre-built bus memory context — skipping second dispatch")
            _eli_pipe_stream("context_mode", mode="prebuilt_reuse", chars=len(memory_context))
        else:
            try:
                memory_context = self._retrieve_relevant_memories(
                    user_input,
                    intent={"action": "CHAT"},
                )
            except Exception as _mem_err:
                log.debug(f"[COGNITIVE] Stream memory retrieval failed: {_mem_err}")
                memory_context = ""

            try:
                evidence_context = self._build_grounded_evidence_context(user_input)
                if evidence_context:
                    memory_context = (memory_context + "\n\n" + evidence_context).strip()
            except Exception as _ev_err:
                log.debug(f"[COGNITIVE] Stream evidence build failed: {_ev_err}")
            _eli_pipe_stream("context_mode", mode="retrieved", chars=len(memory_context))

        log.debug(f"[COGNITIVE][TIMING] memory_context={_time.perf_counter() - started:.3f}s")

        # 2. Build persona handoff once. Try the richest signature first, then degrade.
        situation_brief = ""
        try:
            try:
                _stream_recent_turns = list(context or [])
            except Exception:
                _stream_recent_turns = []

            attempts = [
                {
                    "memory_context": memory_context,
                    "bus_result": pre_built_bus_result,
                    "recent_turns": _stream_recent_turns,
                },
                {
                    "memory_context": memory_context,
                    "recent_turns": _stream_recent_turns,
                },
                {
                    "recent_turns": _stream_recent_turns,
                },
                {},
            ]

            for kw in attempts:
                try:
                    situation_brief = str(self._build_persona_handoff_once(user_input, **kw) or "").strip()
                    if situation_brief:
                        break
                except TypeError:
                    continue

            if situation_brief:
                log.debug(f"[COGNITIVE] Persona handoff → {len(situation_brief)} char brief")
                log.debug(f"[PIPELINE] Stage 10: Context → {len(memory_context)}ch  Stage 10.5: Persona Handoff → {len(situation_brief)}ch")
                _eli_pipe_stream("persona_handoff", chars=len(situation_brief))
        except Exception as _handoff_err:
            log.debug(f"[COGNITIVE] Persona handoff failed (non-fatal): {_handoff_err}")
            situation_brief = ""
            _eli_pipe_stream("persona_handoff_error", error=type(_handoff_err).__name__)

        semantic_guard = _eli_conversation_semantic_guard(user_input)
        _rapport_instruction = _eli_rapport_prompt_instruction(user_input)
        if _rapport_instruction:
            if situation_brief:
                situation_brief = _rapport_instruction + "\n" + situation_brief
            else:
                situation_brief = _rapport_instruction
        if semantic_guard:
            if situation_brief:
                situation_brief = semantic_guard + "\n\n" + situation_brief
            else:
                situation_brief = semantic_guard

        # ── Anti-repeat contract (streaming) ──
        # ELI re-served its own last reply verbatim across turns, re-asking a question the
        # user had already answered. Guards in finalize and in the memory-context builder
        # both missed it: streaming never reaches finalize, and the memory context is
        # discarded or skipped ("reusing pre-built bus memory context"). situation_brief IS
        # the streaming prompt (stage_11 measures ctx_chars=len(situation_brief)), and it
        # is where the rapport/semantic guards already land — so the constraint goes here.
        try:
            _prev_eli = []
            # Skipped on a greeting for the same reason the enforcement guard is:
            # telling the model "do not repeat any of this" while it decides how to
            # say hello is the pressure that pushed a correct "Evening" into the
            # user's misspelled "Aftrnoon".
            _greeting_turn = _is_greeting_turn(user_input)
            if _greeting_turn:
                log.debug("[ANTI-REPEAT] greeting — contract not injected")
            else:
              for _t in (self.memory.get_recent_conversation(limit=8) or []):
                if str((_t or {}).get("role", "")).lower() in ("assistant", "eli"):
                    _c = str((_t or {}).get("content", "") or "").strip()
                    if _c:
                        _prev_eli.append(_c)
            if _prev_eli:
                _quoted = "\n".join(f"  - {s[:220]}" for s in _prev_eli[:3])
                _no_repeat = (
                    "YOU HAVE ALREADY SAID THE FOLLOWING — do not repeat any of it, and do "
                    "not re-ask a question the user has already answered:\n" + _quoted +
                    "\nReply to what the user just said. If you have already described your "
                    "own state or mood, do not describe it again unless asked."
                )
                situation_brief = (_no_repeat + "\n\n" + situation_brief).strip()
                log.debug(f"[ANTI-REPEAT] contract injected ({len(_prev_eli[:3])} prior replies)")
        except Exception:
            log.debug("[ANTI-REPEAT] contract skipped", exc_info=True)

        # Do not promote raw memory_context into situation_brief.
        # Raw context is private evidence, not answer text.
        if not situation_brief:
            situation_brief = ""

        # 3. Build a minimal working-memory object for Stage 11.
        try:
            _wm_recent_turns = list(context or [])
        except Exception:
            _wm_recent_turns = []

        wm = SimpleNamespace(
            user_input=user_input,
            assembled_context=memory_context,
            persona_handoff=situation_brief,
            final_response="",
            trace={},
            bus_result=pre_built_bus_result,
            short_term_memory=SimpleNamespace(recent_turns=_wm_recent_turns),
        )

        full_tokens: List[str] = []
        yielded = False

        # 4. Primary Stage 11 streaming path.
        try:
            log.debug("[COGNITIVE] Stream: Stage 11 primary path")
            log.debug(f"[PIPELINE] Stage 11: LLM Generation → streaming ({reasoning_mode or 'quick'} mode)")
            log.debug(
                f"[COGNITIVE][PIPELINE] stage_11_enter "
                f"req={_eli_pipeline_req} "
                f"mode={reasoning_mode or 'quick'} "
                f"ctx_chars={len(situation_brief)} "
                f"memory_chars={len(pre_built_memory_context or str())} "
                f"bus_result={bool(pre_built_bus_result)}"
            )
            _eli_pipe_stream(
                "stage_11_enter",
                mode=(reasoning_mode or "quick"),
                ctx_chars=len(situation_brief),
                memory_chars=len(pre_built_memory_context or str()),
                bus_result=bool(pre_built_bus_result),
            )
            stream = self.generate_stream_from_assembled_prompt(
                prompt,
                working_memory=wm,
                reasoning_mode=reasoning_mode,
            )

            # ── Anti-repeat ENFORCEMENT (see guards._is_repeat_of_recent) ─────
            # The contract injected into situation_brief above only *asks*. This
            # checks. It acts on the opening rather than the finished reply: once a
            # chunk is yielded it is on the user's screen, so a check that waits for
            # the full text can only report a repeat, never prevent one.
            _recent_eli = []
            if _user_asked_for_a_repeat(user_input):
                log.debug("[ANTI-REPEAT] user asked for a repeat — guard stood down")
            elif _is_greeting_turn(user_input):
                # Leaving _recent_eli empty disarms the guard: both
                # _stream_holding_back_repeats calls no-op on an empty list.
                log.debug("[ANTI-REPEAT] greeting — guard stood down "
                          "(a time-of-day greeting is meant to recur)")
            else:
                try:
                    for _t in (self.memory.get_recent_conversation(limit=8) or []):
                        if str((_t or {}).get("role", "")).lower() in ("assistant", "eli"):
                            _c = str((_t or {}).get("content", "") or "").strip()
                            if _c:
                                _recent_eli.append(_c)
                except Exception:
                    log.debug("[ANTI-REPEAT] recent-reply fetch failed", exc_info=True)

            try:
                for piece in _stream_holding_back_repeats(
                        stream, _recent_eli, allow_retry=True):
                    full_tokens.append(piece)
                    yielded = True
                    yield piece
            except _RepeatDetected:
                log.debug("[ANTI-REPEAT] opening matched a recent reply — regenerating")
                # Regenerate ONCE, with the constraint restated as a hard rule rather
                # than a preamble the model can drift past. A second repeat is served
                # as-is: an honest duplicate beats an empty turn or an infinite retry.
                full_tokens.clear()
                # De-prime the PERSONA HANDOFF, which is where the contract lives.
                # `prompt` is the user's message and must reach the model unaltered.
                # Stripping the contract is necessary but not sufficient: the same
                # reply also reaches the model through the assembled memory context
                # and the handoff's evidence block, so attempt 2 was still reading
                # its own last paragraph. Redact it from every block feeding the retry.
                _retry_brief = _redact_prior_replies(
                    _deprimed_persona_brief(situation_brief), _recent_eli)
                _retry_context = _redact_prior_replies(memory_context, _recent_eli)
                _retry_wm = SimpleNamespace(
                    user_input=user_input,
                    assembled_context=_retry_context,
                    persona_handoff=_retry_brief,
                    final_response="",
                    trace={},
                    bus_result=pre_built_bus_result,
                    short_term_memory=SimpleNamespace(recent_turns=_wm_recent_turns),
                )
                # Same prompt at the same temperature is the same reply. A retry that
                # changes only the framing leaves the sampler free to walk the identical
                # path, which is exactly what it did; widen it.
                _retry_gen = None
                try:
                    _base_temp = float(self._generation_settings().get("temperature", 0.7) or 0.7)
                    _retry_gen = {"temperature": round(min(1.2, _base_temp + _REPEAT_RETRY_TEMP_BUMP), 3)}
                except Exception:
                    log.debug("[ANTI-REPEAT] retry temperature bump skipped", exc_info=True)
                log.debug("[ANTI-REPEAT] retry generation issued (persona handoff "
                          "de-primed: %d -> %d chars, context %d -> %d chars, temp=%s)",
                          len(situation_brief), len(_retry_brief),
                          len(memory_context or ""), len(_retry_context or ""),
                          (_retry_gen or {}).get("temperature", "unchanged"))
                _retry = self.generate_stream_from_assembled_prompt(
                    prompt,
                    working_memory=_retry_wm,
                    reasoning_mode=reasoning_mode,
                    gen_overrides=_retry_gen,
                )
                # The de-primed prompt is materially different from the first, so a
                # second attempt is worth one more generation. Live evidence: attempt
                # 2 reproduced the opening verbatim when it was still primed. Beyond
                # this, serve it — an honest duplicate beats silence or a loop.
                # allow_retry=False: this IS the second chance. Passing True made a
                # repeated retry raise again and trigger a THIRD generation — observed
                # at 20.1s for one reply, with the log claiming "serving it" while it
                # silently regenerated. Two generations is the cap. salvage=True so the
                # cap is not a surrender: if attempt 2 opens with the same recycled
                # sentences and then answers, the recycled part is dropped.
                for piece in _stream_holding_back_repeats(
                        _retry, _recent_eli, allow_retry=False, salvage=True):
                    full_tokens.append(piece)
                    yielded = True
                    yield piece

            if yielded:
                final_text = "".join(full_tokens).strip()
                if final_text:
                    try:
                        self._store_assistant_turn(final_text)
                    except Exception as _store_err:
                        log.debug(f"[COGNITIVE] Stream assistant-turn store failed: {_store_err}")
                    # ── Stage 12: Confidence scoring ──────────────────────────
                    try:
                        _s12_intent_conf = float(
                            (getattr(pre_built_bus_result, "intent_confidence", None)
                             or 0.6) if pre_built_bus_result else 0.6
                        )
                        _s12_score = self._score_response_confidence(
                            prompt, final_text, memory_context, _s12_intent_conf, None)
                        _s12_agent_conf = float(
                            getattr(pre_built_bus_result, "aggregated_confidence", 0.0) or 0.0
                        ) if pre_built_bus_result else 0.0
                        _s12_label = str(
                            getattr(pre_built_bus_result, "confidence_label", "?") or "?"
                        ) if pre_built_bus_result else "?"
                        _s12_threshold = 0.54 if (reasoning_mode or "quick") == "quick" else 0.66
                        _s12_pass = "PASS" if _s12_score >= _s12_threshold else "LOW"
                        log.debug(
                            f"[PIPELINE] Stage 12: Confidence → "
                            f"response={_s12_score:.2f} agent={_s12_agent_conf:.2f} "
                            f"({_s12_label}) threshold={_s12_threshold:.2f} [{_s12_pass}]"
                        )
                        # A low confidence SCORE is a quality signal, not a code bug — there is no
                        # traceback/file for the self-heal patcher to act on, so logging it as a
                        # code-failure only polluted the failure log + recent-failures probe and
                        # made low-confidence casual turns look like recurring bugs. Debug-log the
                        # signal for observability; do not file it as a failure.
                        if _s12_score < _s12_threshold:
                            log.debug(f"[COGNITIVE] low stream confidence score={_s12_score:.2f} "
                                      f"(agent={_s12_agent_conf:.2f}, mode={reasoning_mode or 'quick'})")
                    except Exception as _s12_err:
                        log.debug(f"[PIPELINE] Stage 12: Confidence scoring failed: {_s12_err}")
                # ── Stream meta publish ───────────────────────────────────────
                # _last_request_meta is not updated by the streaming generator path;
                # publish it here so the GUI confidence badge reflects this turn.
                try:
                    _s_grounding = float(
                        getattr(pre_built_bus_result, "grounding_confidence", 0.0) or 0.0
                    ) if pre_built_bus_result else 0.0
                    _s_agg = float(
                        getattr(pre_built_bus_result, "aggregated_confidence", 0.0) or 0.0
                    ) if pre_built_bus_result else 0.0
                    _s_agents = list(
                        getattr(pre_built_bus_result, "agents_used", []) or []
                    ) if pre_built_bus_result else []
                    _s_label = str(
                        getattr(pre_built_bus_result, "confidence_label", "") or ""
                    ) if pre_built_bus_result else ""
                    self._last_request_meta = {
                        "action": "CHAT",
                        "result_action": "CHAT",
                        "reasoning_mode": str(reasoning_mode or "quick"),
                        "agents_used": _s_agents,
                        "aggregated_confidence": _s_agg,
                        "grounding_confidence": _s_grounding,
                        "confidence_label": _s_label,
                        "evidence_used": bool(pre_built_memory_context),
                        "grounded": bool(pre_built_memory_context),
                        "response_chars": len(final_text),
                    }
                except Exception as _smeta_err:
                    log.debug(f"[PIPELINE] Stream meta publish failed: {_smeta_err}")
                log.debug(f"[COGNITIVE][TIMING] stream_total={_time.perf_counter() - started:.3f}s")
                return

            log.debug("[COGNITIVE] Stream: Stage 11 primary path yielded zero visible tokens")
            log.debug(
                f"[COGNITIVE][PIPELINE] stage_11_zero_token "
                f"req={_eli_pipeline_req} "
                f"mode_at_check={reasoning_mode or None} "
                f"full_tokens_count={len(full_tokens)} "
                f"situation_brief_len={len(situation_brief)}"
            )
            _eli_pipe_stream(
                "stage_11_zero_token",
                mode_at_check=(reasoning_mode or None),
                full_tokens_count=len(full_tokens),
                situation_brief_len=len(situation_brief),
            )

            _mode_now = str(reasoning_mode or "quick").strip().lower()
            if _mode_now not in {"quick", "fast", "direct"}:
                _fault = (
                    "Internal cognition-pipeline fault: Stage 11 produced zero visible tokens while "
                    f"reasoning_mode={_mode_now}. I am blocking direct GGUF fallback because this is a "
                    "non-Quick mode. The correct path is router → AgentBus plan → memory/context grounding "
                    "→ Stage 11 synthesis → governor. Current failure point: generate_stream_from_assembled_prompt "
                    "returned no visible chunks."
                )
                yield _fault
                try:
                    self._store_assistant_turn(_fault)
                except Exception:
                    log.debug("suppressed exception", exc_info=True)
                log.debug(f"[COGNITIVE][TIMING] stream_total={_time.perf_counter() - started:.3f}s")
                return

        except Exception as _stage11_err:
            log.debug(f"[COGNITIVE] Stream Stage 11 primary failed: {_stage11_err}")
            _mode_now = str(reasoning_mode or "quick").strip().lower()
            if _mode_now not in {"quick", "fast", "direct"}:
                _fault = (
                    "Internal cognition-pipeline fault: Stage 11 raised an exception in a non-Quick mode. "
                    f"reasoning_mode={_mode_now}; error={_stage11_err}. Direct GGUF fallback blocked."
                )
                yield _fault
                return

        # 5. Same-dispatch direct GGUF fallback. QUICK MODE ONLY.
        try:
            _mode_now = str(reasoning_mode or "quick").strip().lower()
            if _mode_now not in {"quick", "fast", "direct"}:
                log.debug(f"[COGNITIVE] Direct GGUF fallback blocked for non-Quick mode: {_mode_now}")
                return

            log.debug("[COGNITIVE] Stream: direct gguf fallback path")
            log.debug(f"[COGNITIVE][PIPELINE] gguf_fallback req={_eli_pipeline_req} mode_now={_mode_now!r}")
            _eli_pipe_stream("gguf_fallback", mode_now=_mode_now)
            from eli.cognition import gguf_inference as _gi

            try:
                from eli.core.runtime_settings import load_settings as _load_settings
                _settings = _load_settings()
            except Exception:
                _settings = {}

            try:
                max_tokens = int(self._generation_settings().get("max_tokens", _settings.get("max_tokens", 512)) or 512)
            except Exception:
                max_tokens = 512
            if max_tokens <= 0:
                max_tokens = 512

            try:
                temperature = float(_settings.get("temperature", 0.7) or 0.7)
            except Exception:
                temperature = 0.7

            direct_prompt = (
                "You are ELI. Use the grounding package below and answer the user directly. "
                "Do not speak as a generic AI assistant. Do not claim you lack memory or identity. "
                "For repeated failures, repairs, and audits, give cause, evidence, fix, and verification.\n\n"
                "--- GROUNDING PACKAGE ---\n"
                f"{situation_brief}\n\n"
                "--- USER MESSAGE ---\n"
                f"{prompt}\n\n"
                "--- ELI RESPONSE ---\n"
            )

            try:
                direct_stream = _gi.generate(
                    direct_prompt,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                )
            except TypeError:
                direct_stream = _gi.generate(
                    direct_prompt,
                    max_tokens=max_tokens,
                    stream=True,
                )

            if isinstance(direct_stream, str):
                direct_iter = [direct_stream]
            else:
                direct_iter = direct_stream

            for chunk in direct_iter:
                piece = ""
                if isinstance(chunk, dict):
                    piece = str(
                        chunk.get("response")
                        or chunk.get("content")
                        or chunk.get("text")
                        or ""
                    )
                    if not piece:
                        try:
                            piece = str(chunk["choices"][0]["delta"].get("content", ""))
                        except Exception:
                            piece = ""
                else:
                    piece = str(chunk or "")

                if not piece:
                    continue

                full_tokens.append(piece)
                yielded = True
                # Stream live tokens immediately in the direct fallback path.
                yield piece

            if yielded:
                final_text = self._govern_visible_response(
                    prompt,
                    "".join(full_tokens),
                    memory_context=situation_brief,
                    is_grounded=bool(situation_brief),
                )
                if final_text:
                    try:
                        self._store_assistant_turn(final_text)
                    except Exception as _store_err:
                        log.debug(f"[COGNITIVE] Stream direct-fallback store failed: {_store_err}")
                log.debug(f"[COGNITIVE][TIMING] stream_total={_time.perf_counter() - started:.3f}s")
                return

        except Exception as _direct_err:
            log.debug(f"[COGNITIVE] Stream direct gguf fallback failed: {_direct_err}")

        # 6. Final visible failure. This should only happen if both generation paths failed.

        try:
            from eli.execution.router_enhanced import route as _p41_route2
            _p41_text2 = str(user_input or "").strip()
            _p41_r2 = _p41_route2(_p41_text2) if _p41_text2 else {}
            if isinstance(_p41_r2, dict):
                _p41_action2 = str(_p41_r2.get("action") or "").upper()
            else:
                _p41_action2 = str(getattr(_p41_r2, "action", "") or "").upper()
            if _p41_action2 in {"VOLUME"}:
                log.debug(f"[COGNITIVE][PHASE41] no-visible-output suppressed for silent action: {_p41_action2}")
                return
        except Exception as _p41_suppress_err:
            log.debug(f"[COGNITIVE][PHASE41] no-visible suppressor skipped: {_p41_suppress_err}")
        try:
            _p43_silent_actions = {'VOLUME'}
            _p43_action = ''
            for _p43_k in ('action', '_action', 'intent_action', 'routed_action'):
                _p43_v = locals().get(_p43_k)
                if isinstance(_p43_v, str) and _p43_v.strip():
                    _p43_action = _p43_v.strip().upper()
                    break
            if not _p43_action:
                for _p43_k in ('route', 'intent', 'parsed', 'routed'):
                    _p43_obj = locals().get(_p43_k)
                    if isinstance(_p43_obj, dict):
                        _p43_v = _p43_obj.get('action') or _p43_obj.get('name')
                        if isinstance(_p43_v, str) and _p43_v.strip():
                            _p43_action = _p43_v.strip().upper()
                            break
            _p43_user = str(
                locals().get('user_input')
                or locals().get('text')
                or locals().get('message')
                or ''
            ).strip().lower()
            if _p43_action in _p43_silent_actions or _p43_user in ('volume up', 'volume down', 'volume mute', 'mute', 'unmute'):
                log.debug('[COGNITIVE][PHASE43] silent direct action completed; suppressing no-visible-output error.')
                return
        except Exception as _p43_e:
            log.debug(f'[COGNITIVE][PHASE43] silent-action suppress check failed: {_p43_e}')
        msg = "❌ CognitiveEngine stream produced no visible output after Stage 11 and direct GGUF fallback."
        log.debug(f"[COGNITIVE] {msg}")
        yield msg
        return

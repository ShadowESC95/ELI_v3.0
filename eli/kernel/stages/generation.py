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
from eli.cognition import context_budget as _ctx_budget
from eli.kernel.stages._common import _phatic_generation_budget
# Tokens reserved for the reply when sizing the evidence budget. `max_tokens` is a
# ceiling (~3461 in quick mode) and reserving all of it starved the evidence — see
# the note at the streaming context-size guard. A reply longer than this simply eats
# into the 20% headroom the budget already keeps.
_OUTPUT_RESERVE_TOKENS = int(os.environ.get("ELI_OUTPUT_RESERVE_TOKENS", "1024"))


from eli.kernel.stages.guards import (
    _failed_executor_is_failed_block,
    _failed_executor_query_from_prompt,
    _failed_executor_relevant_block,
    _failed_executor_surface,
)
from eli.kernel.stages.fast_actions import (
    _PHASE45_DIRECT_FAST_ACTIONS,
    _PHASE45_SILENT_FAST_ACTIONS,
    _phase45_action_name,
    _phase45_force_direct_result,
)
from eli.utils.log import get_logger

log = get_logger(__name__)

# How much of a reply the last-response trace keeps. Enough to quote the message
# back when asked what it was; short enough that a file rewritten every turn does
# not grow into the largest thing under artifacts/.
_TRACE_TEXT_CHARS = 1200


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

def _ft_summarise_findings(report_text: str, *, max_lines: int = 6) -> str:
    """The FAILING lines of a status/audit dump, or "" when everything passed.

    A promise to "flag any hiccups" is a promise about problems, not an
    invitation to paste the whole report -- so PASS lines and healthy probes
    are dropped and only what actually failed survives. Returns "" when there
    is nothing to report, which keeps a clean run silent rather than
    manufacturing reassurance.
    """
    import re as _sf_re
    body = str(report_text or "")
    if not body.strip():
        return ""
    bad = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(("FAIL", "\u274c")) or _sf_re.match(r"^-\s+line\s+\d+", stripped):
            bad.append(stripped)
        elif stripped.startswith("\u2705") or stripped.startswith("PASS"):
            continue
    if not bad:
        return ""
    head = bad[:max_lines]
    more = len(bad) - len(head)
    out = "I said I'd flag anything — here's what came back:\n" + "\n".join(
        f"  {b}" for b in head)
    if more > 0:
        out += f"\n  (+{more} more)"
    return out


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
            # Structured markers only. "filenotfounderror" / "file not found" were dropped: they
            # match narrative (a commit message, a quoted log) and "grounded_evidence" is present on
            # every grounded turn, so any error name in the prompt passed the co-occurrence test.
            ("'ok': false" in _p_low or '"ok": false' in _p_low
             or "ok=false" in _p_low or "successful: 0 | failed:" in _p_low)
            and ("execute result" in _p_low or "agent:system" in _p_low
                 or "analyze_pdf" in _p_low)
        ):
            return _failed_executor_surface(
                prompt, _failed_executor_query_from_prompt(prompt)
            )

        # Verbatim guard for deterministic grounded reports (EXAMINE_CODE / FILE_AUDIT). Re-narrating
        # made the model confabulate: EXAMINE_CODE snippets became 5 invented "bugs" that were existing
        # comments, and FILE_AUDIT counts became 5 fabricated buggy files. If the report is in the
        # evidence, return it verbatim.
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

        # FIX_FILE success event: the executor JSON-encodes {event: artifact_generated, fixed: true,
        # path, filename, backup}. Synthesising it made the model narrate a false refusal ("I cannot
        # fix the file from here") though the file was written. Surface the real outcome.
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

        prompt = self._prepend_recent_exchange(prompt)

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

                # Context size guard. For short queries on small windows, trim memory to prevent prompt bloat
                # and multi-minute first-token latency: persona (~2k chars) + user_input + max_tokens must fit in
                # n_ctx with room to spare, and memory gets what's left.
                _n_ctx_guard = self._runtime_n_ctx()
                _max_tok_guard = int(gen.get('max_tokens', 512))
                _persona_chars = len(_load_persona_text())
                _query_chars = len(prompt or '')
                _trimmed_mem = _eli_sanitize_identity_context_block(memory_context, prompt)
                _mem_char_budget = _ctx_budget.memory_char_budget(
                    _n_ctx_guard, _persona_chars + _query_chars, _max_tok_guard,
                    wanted_chars=len(_trimmed_mem),
                    protect_memory=_ctx_budget.is_recall_question(prompt))
                if len(_trimmed_mem) > _mem_char_budget:
                    log.debug(
                        f"[COGNITIVE] Trimming memory context "
                        f"{len(_trimmed_mem)}→{_mem_char_budget} chars "
                        f"(n_ctx={_n_ctx_guard}, max_tokens={_max_tok_guard})"
                    )
                    _before = len(_trimmed_mem)
                    _trimmed_mem = _ctx_budget.trim_memory_context(_trimmed_mem, _mem_char_budget)
                    self._note_memory_trim(_before, len(_trimmed_mem))
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
                    # Pre-flight: clamp max_tokens so prompt+output fits n_ctx, using the live loaded model's
                    # n_ctx, not the configured one (a model may have fallen back to 1024 ctx when VRAM was tight and
                    # the configured 4096 is a lie). Start from the live ctx, not a default.
                    _n_ctx_pf1 = self._runtime_n_ctx()
                    # Use the live loaded value, in this order: _live_runtime_params["n_ctx"] (set after a
                    # successful load), runtime_snapshot.json effective.n_ctx, then llm.n_ctx() (may throw). The
                    # configured n_ctx is often stale, so never use it as the source.
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
                    # Quality ceiling (context-bloat cap): the small local model degenerates into a lone "-" on
                    # very large prompts long before n_ctx fills (a 39k-char WEB_SEARCH synthesis gave "-G" though it
                    # fit). Cap to a sane size independent of n_ctx. ELI_SYNTH_MAX_PROMPT_CHARS tunes it (0 disables).
                    try:
                        from eli.core.cognition_tunables import get_tunable as _cog_get
                        _qenv = os.environ.get("ELI_SYNTH_MAX_PROMPT_CHARS")
                        _fixed_cap = _cog_get("cog.synth_max_prompt_chars")
                        if _qenv is not None:
                            _qcap = int(_qenv or "0")  # explicit env override wins
                        elif _cog_get("cog.synth_cap_auto"):
                            # Auto-scale to the loaded model: ~45% of the window (chars ~ 3 x
                            # tokens) x capability tier, never below the fixed cap. For the current
                            # small model the floor dominates.
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
                            # Keep the persona head (voice + hard constraints) and the grounded-evidence tail (appended
                            # last), drop the bulky middle (profile, scaffolding, memory dump) that bloats the prompt
                            # without being the answer.
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

                    # Re-fit the answer budget to the prompt actually being sent. The earlier estimate floored at
                    # 128 and ran before truncation, so a big prompt got cut and the answer stayed capped at 128.
                    # Truncating frees room, so recompute after it.
                    _pt_final = max(1, int((len(enhanced_system) + len(prompt)) / 3.5))
                    _avail_final = max(128, _n_ctx_pf1 - _pt_final - 64)
                    if _avail_final > _safe_max_pf1:
                        _req_ok = _req_pf1 if _req_pf1 > 0 else _avail_final
                        _safe_max_pf1 = max(_safe_max_pf1, min(_req_ok, _avail_final))
                        log.debug(
    f"[COGNITIVE] Budget re-fitted after prompt truncation: "
    f"max_tokens→{_safe_max_pf1} (prompt≈{_pt_final} tok, n_ctx={_n_ctx_pf1})")

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
                # Empty after broker's own retry — usually a thinking model that burned
                # its budget inside a hidden reasoning block. One more pass with think
                # suppressed; do NOT mark the whole GGUF backend unavailable.
                log.debug("[COGNITIVE] GGUF empty response — retrying with force_no_think")
                _retry_tokens = max(256, min(int(locals().get("_safe_max_pf1", 512) or 512), 1024))
                try:
                    from eli.cognition.gguf_inference import force_no_think as _fnt
                    with _fnt():
                        if broker and broker.gguf_ready:
                            response = broker.infer(
                                prompt,
                                system=enhanced_system,
                                max_tokens=_retry_tokens,
                                temperature=gen["temperature"],
                                retry=False,
                            )
                        elif gguf_inference is not None:
                            with self._gguf_lock:
                                response = gguf_inference.chat_completion(
                                    prompt,
                                    system=enhanced_system,
                                    max_tokens=_retry_tokens,
                                    temperature=gen["temperature"],
                                )
                except Exception as _empty_retry_err:
                    log.debug(f"[COGNITIVE] force_no_think empty retry failed: {_empty_retry_err}")
                    response = ""
                if response:
                    return _normalize_assistant_text(prompt, response)
                raise RuntimeError("GGUF returned empty response after retry")
            except Exception as e:
                _emsg = str(e)
                _empty = "empty response" in _emsg.lower()
                _ctx_overflow = (
                    "context window" in _emsg.lower()
                    or "requested tokens" in _emsg.lower()
                )
                if not _empty and not _ctx_overflow:
                    self._gguf_available = False
                self._gguf_load_error = _emsg
                log.debug(f"[COGNITIVE] GGUF chat failed: {e}")
                if provider not in ("ollama",):
                    if _empty:
                        return (
                            "The local model returned an empty answer after retry. "
                            "Common causes: a thinking model spent its token budget in a "
                            "hidden reasoning block; generation was interrupted during shutdown; "
                            "or the prompt was too large for the effective GPU offload. "
                            "Try again, use quick mode, or ask for a GGUF diagnostics report."
                        )
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

        _is_phatic_stream = False
        try:
            _is_phatic_stream = _is_brief_phatic_prompt(str(prompt or "").strip().lower())
        except Exception:
            _is_phatic_stream = False
        if _is_phatic_stream:
            gen["max_tokens"] = min(int(gen.get("max_tokens") or 256), _phatic_generation_budget())
            gen["temperature"] = max(float(gen.get("temperature") or 0.7), 0.72)

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
                # Context size guard (same logic as the non-streaming path). Use the model's real usable
                # ceiling (min of loaded n_ctx and n_ctx_train), not the config default: a model trained on less
                # than the requested ctx must be sized to its trained length or the prompt overflows. Config
                # value only if no model is loaded.
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
                _trimmed_mem_s = _eli_sanitize_identity_context_block(memory_context, prompt)
                _mem_char_budget_s = _ctx_budget.memory_char_budget(
                    _n_ctx, _persona_chars_s + _query_chars_s, _max_tok_s,
                    wanted_chars=len(_trimmed_mem_s),
                    protect_memory=_ctx_budget.is_recall_question(prompt))
                if len(_trimmed_mem_s) > _mem_char_budget_s:
                    log.debug(
                        f"[COGNITIVE] Stream: trimming memory context "
                        f"{len(_trimmed_mem_s)}→{_mem_char_budget_s} chars"
                    )
                    _before = len(_trimmed_mem_s)
                    _trimmed_mem_s = _ctx_budget.trim_memory_context(_trimmed_mem_s, _mem_char_budget_s)
                    self._note_memory_trim(_before, len(_trimmed_mem_s))
                # ──────────────────────────────────────────────────────────

                # Conversational context injection (current session only, last 30 min). (a) Short follow-ups
                # (<=6 words) get the recent exchange inline. (b) Confusion signals ("what do you mean", "i don't
                # understand", "huh?", "you're looping") also get a directive to find where the confusion arose
                # and resolve it, not loop or repeat the last reply.
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
                    r"that was a question|that is a question|that'?s a question|"
                    r"you asked me\b|like what\b|"
                    r"you'?re (?:slack(?:ing)?|drift(?:ing)?|repeating|looping)|"
                    r"starting to (?:slack|drift|loop|repeat))\b",
                    _low_msg,
                )) or _low_msg.rstrip("?!. ") in {"what", "huh", "eh", "sorry", "pardon", "you what", "like what"}
                _inject_session = False
                if not _is_phatic_stream:
                    try:
                        from eli.runtime.session_continuity import session_has_prior_turns
                        _recent = self.memory.get_recent_conversation(
                            limit=14, user_id=getattr(self, "user_id", None)) or []
                        _inject_session = session_has_prior_turns(_recent, _orig_msg)
                    except Exception:
                        _inject_session = False
                if _confusion or _inject_session:
                    try:
                        from eli.runtime.session_continuity import build_inline_exchange_block
                        if not _inject_session:
                            _session_cutoff = time.time() - 1800  # 30 minutes
                            _recent = self.memory.get_recent_conversation(
                                limit=14, user_id=self.user_id)
                            _prompt_key = _orig_msg[:80]
                            _thread = [
                                t for t in _recent
                                if float(t.get("timestamp") or 0) >= _session_cutoff
                                and not (t.get("role") == "user"
                                         and (t.get("content") or "").strip()[:80] == _prompt_key)
                            ]
                        else:
                            _thread = list(_recent)
                        if len(_thread) >= 2:
                            _block = build_inline_exchange_block(_thread, user_input=_orig_msg)
                            if not _block:
                                _thread_lines = []
                                for _t in _thread[-12:]:
                                    _r = "You" if _t.get("role") == "user" else "ELI"
                                    _c = (_t.get("content") or "").replace("\n", " ")[:280]
                                    _thread_lines.append(f"{_r}: {_c}")
                                _block = "[Recent session]\n" + "\n".join(_thread_lines)
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
                    compact=(
                        _is_phatic_stream
                        or self._use_compact_system(
                            prompt, _trimmed_mem_s, reasoning_mode=reasoning_mode)
                    ),
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
                    # Keep persona HEAD + evidence TAIL (same as non-stream path).
                    # Tail-only truncation was dropping voice/constraints and leaving
                    # a raw memory dump the model could not finish within max_tokens.
                    _prompt_budget = max(200, min(len(prompt), _max_stream_chars // 4))
                    _sys_budget = max(200, _max_stream_chars - _prompt_budget)
                    if len(enhanced_system) > _sys_budget:
                        _head = max(200, int(_sys_budget * 0.5))
                        _tail = max(200, _sys_budget - _head)
                        enhanced_system = (
                            enhanced_system[:_head].rstrip()
                            + "\n\n…[context trimmed to fit the model]…\n\n"
                            + enhanced_system[-_tail:].lstrip()
                        )
                    prompt = prompt[-_prompt_budget:]
                    log.debug(
    f"[COGNITIVE] Stream overflow: truncated to fit n_ctx={_n_ctx}")
                    # Re-fit answer budget against the prompt actually sent (see non-stream
                    # path: estimate→clamp→truncate pins max_tokens=128 when the pre-cut
                    # prompt was huge, then truncation frees thousands of tokens).
                    _pt_final = max(1, int((len(enhanced_system) + len(prompt)) / 3.5))
                    _avail_final = max(128, _n_ctx - _pt_final - 64)
                    if _avail_final > _safe_max:
                        _req_ok = _req_s if _req_s > 0 else _avail_final
                        _safe_max = max(_safe_max, min(_req_ok, _avail_final))
                        log.debug(
    f"[COGNITIVE] Stream budget re-fitted after truncation: "
    f"max_tokens→{_safe_max} (prompt≈{_pt_final} tok, n_ctx={_n_ctx})")

                generate = getattr(gguf_inference, "generate", None)
                if callable(generate):
                    log.debug("[COGNITIVE] Stream: using generate() with stream=True")
                    _yielded = False
                    try:
                        from eli.cognition.gguf_inference import force_no_think as _fnt_stream
                        from contextlib import nullcontext as _nullctx_stream
                    except Exception:
                        from contextlib import nullcontext as _fnt_stream, nullcontext as _nullctx_stream  # type: ignore
                    _stream_think_ctx = _fnt_stream() if _is_phatic_stream else _nullctx_stream()
                    with self._gguf_lock, _stream_think_ctx:
                        for chunk in generate(
                            prompt,
                            system=enhanced_system,
                            max_tokens=_safe_max,
                            temperature=gen["temperature"],
                            stream=True,
                        ):
                            try:
                                from eli.cognition import gguf_inference as _ggi_abort
                                if _ggi_abort.is_cancel_requested():
                                    break
                            except Exception:
                                log.debug("suppressed exception", exc_info=True)
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
                    if _is_phatic_stream:
                        try:
                            from eli.cognition.gguf_inference import force_no_think as _fnt_ph_fallback
                            _phatic_sys = self._build_enhanced_system(
                                "",
                                compact=True,
                                user_input=prompt,
                                reasoning_mode=reasoning_mode or "quick",
                                situation_brief=self._build_phatic_handoff_brief(prompt),
                            )
                            with _fnt_ph_fallback():
                                with self._gguf_lock:
                                    _ph_raw = generate(
                                        prompt,
                                        system=_phatic_sys,
                                        max_tokens=min(_phatic_generation_budget(), _safe_max),
                                        temperature=gen["temperature"],
                                        stream=False,
                                    )
                            _ph_text = ""
                            if isinstance(_ph_raw, dict):
                                _ph_text = str(_ph_raw.get("response") or _ph_raw.get("content") or "").strip()
                            else:
                                _ph_text = str(_ph_raw or "").strip()
                            if _ph_text:
                                for token in self._yield_text_chunks(_ph_text, chunk_size=12):
                                    yield token
                                return
                        except Exception as _ph_fb_err:
                            log.debug(f"[COGNITIVE] phatic zero-token fallback failed: {_ph_fb_err}")
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
                _empty = "empty response" in _emsg.lower()
                log.debug(f"[COGNITIVE] GGUF streaming failed: {_emsg}")
                # A context-window overflow is recoverable and doesn't mean the GGUF backend is dead. Don't
                # flip _gguf_available (that poisons every later turn) and never show the raw exception as
                # ELI's reply. Retry once non-streaming; generate() now truncates an over-ctx prompt to the
                # model's n_ctx_train.
                if not _ctx_overflow and not _empty:
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
                    elif _empty:
                        yield ("The local model returned an empty answer after retry. "
                               "Common causes: a thinking model spent its token budget in a "
                               "hidden reasoning block; generation was interrupted during shutdown. "
                               "Try again, use quick mode, or ask for a GGUF diagnostics report.")
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
        # Recent turns let the governor drop an intent question the user has
        # already answered. Cheap (8 rows) and fully optional: any failure here
        # leaves the reply exactly as it was.
        _gov_hist = None
        try:
            from eli.memory.memory import get_memory as _gm
            _gov_hist = _gm().get_recent_conversation(limit=8)
        except Exception:
            log.debug("[GOVERNOR] recent-turn lookup skipped", exc_info=True)
        response = govern_output(response, is_grounded=evidence_used, history=_gov_hist,
                                 memory_diag=getattr(self, "_memory_diag", None))
        response = str(response or "").strip()
        # Anti-echo: never serve ELI's previous reply back to the user. Replies get stored and recalled,
        # so on a short turn the model latches onto its own last line. repeat_penalty can't see across
        # turns. Regenerate once with a do-not-repeat instruction; if it still echoes keep the original.
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
                    _expand_tokens = 512
                    try:
                        from eli.cognition.reasoning_modes import (
                            canonical_mode as _cm_exp,
                            _base_tokens_from_profile as _btfp,
                        )
                        _expand_tokens = max(
                            512,
                            min(
                                2048,
                                _btfp(
                                    _cm_exp(reasoning_mode),
                                    self._mode_profile(reasoning_mode),
                                )
                                // 2,
                            ),
                        )
                    except Exception:
                        log.debug("suppressed exception", exc_info=True)
                    _expanded = self._get_chat_response(
                        _expand_prompt,
                        "",
                        reasoning_mode=reasoning_mode,
                        gen_overrides={"max_tokens": _expand_tokens, "temperature": 0.25},
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

        try:
            self.memory.upkeep_async()
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

    def _maybe_fail_closed_chat(
        self,
        user_input: str,
        *,
        query_class: str = "",
        memory_context: str = "",
        bus_result: Any = None,
    ) -> Optional[str]:
        try:
            from eli.cognition.chat_grounding_gate import evaluate_chat_grounding_gate
            return evaluate_chat_grounding_gate(
                user_input,
                query_class=query_class,
                memory_context=memory_context,
                bus_result=bus_result,
            )
        except Exception as _fc_err:
            log.debug(f"[COGNITIVE] chat fail-closed gate skipped: {_fc_err}")
            return None

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
            # normalize_response is the GGUF-artifact cleaner clean_gguf_artifacts(response,
            # user_input). The swapped-arg TypeError fallback is gone now that the signature
            # collision with output_governor.normalize_response(user_input, text) is resolved.
            try:
                response = normalize_response(response, user_input)
            except Exception:
                log.debug("suppressed exception", exc_info=True)
        response = govern_output(response, is_grounded=is_grounded, evidence=memory_context)
        if not is_grounded:
            try:
                from eli.cognition.user_claim_validator import (
                    validate_user_claims_against_evidence,
                )
                _recent_user_turns: List[str] = []
                try:
                    for _t in (self.memory.get_recent_conversation(limit=8, user_id=self.user_id) or []):
                        if str((_t or {}).get("role", "")).lower() == "user":
                            _c = str((_t or {}).get("content", "") or "").strip()
                            if _c:
                                _recent_user_turns.append(_c)
                except Exception:
                    _recent_user_turns = []
                _claim_verdict = validate_user_claims_against_evidence(
                    response,
                    memory_context,
                    user_input=user_input,
                    recent_user_turns=_recent_user_turns,
                )
                if _claim_verdict.get("unsafe") or _claim_verdict.get("violations"):
                    _san = str(_claim_verdict.get("sanitized") or "").strip()
                    if _san:
                        response = _san
                    log.debug(
                        "[COGNITIVE] user-claim validator: violations=%s unsafe=%s",
                        len(_claim_verdict.get("violations") or []),
                        _claim_verdict.get("unsafe"),
                    )
            except Exception as _ucv_err:
                log.debug(f"[COGNITIVE] user-claim validator skipped: {_ucv_err}")
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

        # Private reasoning modes run the real multi-pass algorithms (CoT, ToT, CAI, SC) and buffer
        # the finished result before yielding chunks; quick mode streams live. Raw private-strategy
        # chunks must never reach the GUI before the final sanitiser.
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
                if gen_overrides:
                    # Caller-supplied sampling wins — this is how the anti-repeat
                    # retry raises temperature so attempt 2 is not attempt 1 again.
                    _rm_gen_overrides = dict(_rm_gen_overrides or {})
                    _rm_gen_overrides.update(
                        {k: v for k, v in dict(gen_overrides).items() if v is not None})
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

        situation_brief = self._inject_anti_repeat_contract(prompt, situation_brief)
        memory_context = self._redact_recent_eli_from_context(memory_context)
        try:
            if working_memory is not None:
                working_memory.persona_handoff = situation_brief
                working_memory.assembled_context = memory_context
        except Exception:
            log.debug("suppressed exception", exc_info=True)

        def _live_stream_factory(brief: str, ctx: str, overrides: Optional[Dict[str, Any]] = None):
            """True GUI streaming path with optional brief/context overrides for retry."""
            import re as _re

            raw_parts: List[str] = []
            yielded_any = False
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

            _merged_overrides = dict(gen_overrides or {})
            if overrides:
                _merged_overrides.update({k: v for k, v in dict(overrides).items() if v is not None})

            for chunk in self._stream_model_response(
                prompt,
                ctx,
                reasoning_mode=reasoning_mode,
                gen_overrides=_merged_overrides or None,
                situation_brief=brief,
            ):
                piece = str(chunk or "")
                if not piece:
                    continue

                raw_parts.append(piece)

                if not prefix_resolved:
                    start_buffer += piece

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

            if not prefix_resolved and start_buffer:
                cleaned = _clean_start(start_buffer)
                if cleaned:
                    yielded_any = True
                    yield cleaned

            if not yielded_any:
                final_text = self._govern_visible_response(
                    prompt,
                    "".join(raw_parts),
                    memory_context=ctx or brief,
                    is_grounded=bool(ctx or brief),
                )
                if final_text:
                    for token in self._yield_text_chunks(final_text, chunk_size=12):
                        yield token

        yield from self._yield_with_anti_repeat_guard(
            prompt,
            _live_stream_factory,
            memory_context=memory_context,
            situation_brief=situation_brief,
            reasoning_mode=reasoning_mode,
            working_memory=working_memory,
            bus_result=_bus_result,
            recent_turns=recent_turns,
        )
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
            # Topic-deepen guard: if the previous real action was a news briefing and the user asks
            # to go deeper on a specific topic ("look closer into Hubble"), re-fetch that topic, not
            # ELI's lossy paraphrase, which drops the topic and dumps the whole briefing again.
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
            # Followthrough is for actions ELI promised to perform for the user (fetch news, play media,
            # search, open), never for internal status dumps. A casual "I've been checking my memory" must
            # not auto-run MEMORY_STATUS and bury the user in evidence they didn't ask for.
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
                # Suppressing a dump nobody asked for is right. Suppressing one ELI just promised to report
                # breaks its word (it said "I'll flag any lingering hiccups", found a real defect and dropped
                # it). If the reply promised to report, surface the findings only, never the full PASS listing.
                _ft_promised = bool(_ft_re.search(
                    r"(?i)\bi(?:'ll| will| am going to| can)\s+(?:\w+\s+){0,3}?"
                    r"(?:flag|report|tell you|let you know|surface|call out|point out|"
                    r"highlight|come back to you)\b",
                    # `full` is the complete streamed reply -- the promise lives
                    # there, not necessarily in the matched clause alone.
                    str(commit.get("clause") or "") + " " + str(full or "")))
                _ft_findings = _ft_summarise_findings(real_txt) if _ft_promised else ""
                if _ft_findings:
                    log.debug(f"[FOLLOWTHROUGH] {real_act} was promised — surfacing findings only")
                    yield "\n\n" + _ft_findings
                else:
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

        # Reasoning-mode recovery (ELI_REASONING_MODE_RECOVERY_V1): some indirect stream paths omit
        # the reasoning_mode kwarg. process() stamps the active mode on self; recover it here to
        # keep the non-Quick contracts in Stage 11 and the fallback guards.
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

        if pre_built_bus_result is not None or pre_built_memory_context:
            _qclass = str(getattr(self, "_last_request_meta", {}).get("query_class") or "")
            _fc_stream = self._maybe_fail_closed_chat(
                prompt,
                query_class=_qclass,
                memory_context=pre_built_memory_context or "",
                bus_result=pre_built_bus_result,
            )
            if _fc_stream:
                yield _fc_stream
                return

        _eli_pipe_stream(
            "begin",
            mode=(reasoning_mode or "quick"),
            prompt_chars=len(prompt),
            prebuilt_ctx=bool(pre_built_memory_context),
            prebuilt_bus=bool(pre_built_bus_result),
        )

        _rapport_mode = _eli_is_rapport_prompt(prompt)

        try:
            _phatic_stream = _is_brief_phatic_prompt(str(user_input or prompt or "").strip().lower())
        except Exception:
            _phatic_stream = False
        if _phatic_stream:
            _rapport_mode = False

        # 1. Memory / evidence context. Reuse process()-built context when present.
        # Rapport prompts deliberately skip deep memory/HyDE retrieval: casual banter should not be
        # converted into a corporate status query or safety lecture.
        if _phatic_stream:
            memory_context = ""
            log.debug("[COGNITIVE] Stream: phatic prompt — skipping memory/evidence context")
            _eli_pipe_stream("context_mode", mode="phatic_skip")
        elif _rapport_mode:
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

        situation_brief = self._inject_anti_repeat_contract(user_input, situation_brief)

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
            for piece in self.generate_stream_from_assembled_prompt(
                prompt,
                working_memory=wm,
                reasoning_mode=reasoning_mode,
            ):
                full_tokens.append(piece)
                yielded = True
                yield piece

            if yielded:
                final_text = self._govern_visible_response(
                    prompt,
                    "".join(full_tokens).strip(),
                    memory_context=memory_context or situation_brief,
                    is_grounded=bool(memory_context or situation_brief),
                )
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
                        # A low confidence score is a quality signal, not a code bug: no traceback or file for the
                        # self-heal patcher, and filing it polluted the failure log and made casual low-confidence
                        # turns look like recurring bugs. Debug-log it, don't file it.
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
                        "route_action": "CHAT",
                        "request_id": str(_eli_pipeline_req or ""),
                        "reasoning_mode": str(reasoning_mode or "quick"),
                        "agents_used": _s_agents,
                        "aggregated_confidence": _s_agg,
                        "confidence": _s_agg,
                        "grounding_confidence": _s_grounding,
                        "confidence_label": _s_label,
                        "evidence_used": bool(pre_built_memory_context),
                        "grounded": bool(pre_built_memory_context),
                        "response_chars": len(final_text),
                        "response_text": final_text[:_TRACE_TEXT_CHARS],
                        "response_truncated": len(final_text) > _TRACE_TEXT_CHARS,
                        "user_input": str(user_input or "")[:400],
                    }
                    # Persist it, not just the in-memory badge. This block only set _last_request_meta, so a
                    # streamed CHAT (every ordinary GUI turn) never wrote last_trace.json, and "What was the last
                    # message you sent?" answered from whichever run last wrote the file (106 minutes and a restart
                    # earlier, with full confidence).
                    from eli.runtime.last_trace import save_last_trace as _save_lt
                    _save_lt(dict(self._last_request_meta))
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

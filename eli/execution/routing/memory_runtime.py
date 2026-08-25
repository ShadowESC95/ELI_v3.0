"""Router memory-runtime intents — cognition-failure guard, recent-memory-processing and self-report-recent-updates questions, and the memory-runtime route lock. Moved from router_enhanced.py."""
from __future__ import annotations

import re as _eli_recent_mem_re
import re



def _eli_outside_quotes(text: str) -> str:
    """The utterance with quoted passages removed — what the user actually asked.

    Quoting ELI's own words back is a conversational move, not a request to run
    whatever those words mention. Anything left empty (the message was ONLY a
    quote) falls back to the whole text so a bare quote still routes as before.
    """
    # DOUBLE quotes only. An apostrophe is not a quote mark in English, and
    # including it made "you're building..." look like a quoted span, eating half
    # the sentence including the words that mattered.
    stripped = re.sub(r"[\"\u201c\u201d][^\"\u201c\u201d]{12,}?[\"\u201c\u201d]",
                      " ", str(text or ""))
    return stripped if stripped.strip() else str(text or "")

def _eli_runtime_cognition_failure_guard(text):
    raw = str(text or "")
    low = raw.lower().strip()
    # Route on what the USER asked, not on words they are quoting back. The
    # _explain_prior_claim guard already states this principle — "even when it
    # quotes ELI's own words back, which may incidentally contain 'context
    # window', 'confidence', 'max_tokens'" — but it never reached this trigger.
    #
    # Live at 2.3.6, the user quoted ELI's own sentence:
    #   "...you might push toward more layers. But if it's about speed or
    #    resource usage, 26 is a smart middle ground." -- is that not
    #    counterintuitive?
    # "layers" + "you" inside the QUOTE fired GPU_STATUS at 0.995, and a
    # conversational challenge was answered with a VRAM dump.
    _asked = _eli_outside_quotes(low)

    # "how many layers are you using?" means GPU offload layers in ELI's own
    # runtime — n_gpu_layers is live in the GPU_STATUS snapshot. Left to CHAT it
    # confabulated (a real session answered with After Effects compositing
    # advice), and "are you using the gpu?" drew "I am running on CPU, not GPU"
    # while 11 layers were offloaded. Grounded report, not a guess.
    # It must actually ASK. The trigger was `layers` + any of you/your/gpu/…,
    # which is satisfied by ordinary conversation about layers and fired at
    # 0.995 — overriding everything. Live at 2.3.13 two consecutive turns were
    # answered with the same VRAM dump:
    #
    #   "yeah you aree right about the layers, we need to claw some back"
    #   "i don't care about that, I was talking about incrasing your layers"
    #
    # Neither requested a status report. The second says outright that the first
    # report was unwanted, and got it again verbatim. A previous fix taught this
    # guard to ignore words inside QUOTES; these were not quotes, they were the
    # user talking.
    # A trailing "?" made the WHOLE turn a request, however long it was and
    # however many subjects it had moved through. Live at 2.3.26 a fifty-word
    # turn that corrected ELI's layer count, changed subject to a film, and
    # ended "...so i cn get some weed, you?" was routed to GPU_STATUS at 0.995
    # and answered with a VRAM dump -- the "?" belonged to the weed, not the
    # GPU. The mark only counts when the question could plausibly BE about the
    # runtime: either the turn is short, or its last sentence mentions it.
    _last_sentence = (re.split(r"(?<=[.!?])\s+", _asked.strip()) or [""])[-1]
    _ends_in_relevant_question = bool(
        _asked.rstrip().endswith("?")
        and (len(_asked.split()) <= 25
             or re.search(r"\b(?:gpu|vram|cuda|nvidia|layers?|offload\w*|batch|"
                          r"context|ctx|memory|model)\b", _last_sentence))
    )
    # The user STATING ELI's runtime state already knows it: that is a
    # correction, not a request for a report. Answering it with the same report
    # is what made a challenge ("you are on 28 layers - why are you still
    # lying?") come back as a telemetry dump.
    _asserts_state = bool(re.search(
        r"\b(?:you(?:'re| are) (?:only )?(?:on|at|running|using)\s+\d+"
        r"|(?:the )?gpu is (?:not|n'?t)\b"
        r"|(?:is )?not back to full"
        r"|you(?:'re| are) (?:still )?(?:lying|wrong|mistaken|not right))\b",
        _asked))

    _is_request = bool(
        _ends_in_relevant_question
        or re.match(r"^\s*(?:how|what|which|why|is|are|am|do|does|did|can|could|"
                    r"should|show|tell|check|report|give|list|status)\b", _asked)
        or re.search(r"\b(?:show me|tell me|check|report on|how many|how much|"
                     r"what(?:'s| is| are)|status of)\b", _asked)
        # A bare noun-phrase command is still a request: "gpu status", "vram
        # usage", "gpu stats" are the documented activation phrases and carry no
        # interrogative at all. Length is what separates them from conversation —
        # nobody chats in four words about their offload layers.
        or (len(_asked.split()) <= 5
            and re.search(r"\b(?:status|stats|usage|diagnostics?|temp|temperature)\b", _asked))
    )
    # An explicit brush-off is not a request, whatever else the sentence contains.
    _dismissed = bool(re.search(
        r"\b(?:i don'?t care|don'?t care about|not asking about|forget (?:that|it)|"
        r"never ?mind|stop (?:that|it)|i know that|not what i (?:asked|meant|said))\b",
        _asked))

    _gpu_layer_question = (
        _is_request and not _dismissed and not _asserts_state
        and re.search(r"\blayers?\b", _asked)
        and re.search(r"\b(you|your|offload\w*|gpu|vram|cuda|model|running|loaded|use|using)\b", _asked)
        and not re.search(r"\b(image|photo|photoshop|after\s?effects|composit\w*|design|"
                          r"canvas|css|z-index|onion\s?skin)\b", _asked)
    )
    if re.search(r"\bnvidia-smi\b", _asked) or _gpu_layer_question or (
        _is_request and not _dismissed and not _asserts_state
        and re.search(r"\b(gpu|vram|cuda|nvidia)\b", _asked)
        and re.search(r"\b(stat|stats|status|diagnostic|diagnostics|usage|using|use|memory|"
                      r"performance|running on|running|offload\w*|accelerat\w*|"
                      # "used" was absent, so "is the gpu being used?" -- as
                      # plain a status question as exists -- fell through to
                      # CHAT and got answered from imagination.
                      "used|"
                      r"free|available|left|spare|"
                      r"tell me what it means|temp|temperature|how hot)\b", _asked)
    ):
        return {
            "action": "GPU_STATUS",
            "args": {"question": raw, "explain": True},
            "confidence": 0.995,
            "meta": {
                "matched_by": "eli.gpu_status_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_diagnostic",
            },
        }

    if re.search(r"\b(full\s+)?runtime\s+audit\b", low) or re.search(r"\bwhat'?s?\s+actually\s+(broken|missing)\b", low):
        return {
            "action": "RUNTIME_AUDIT",
            "args": {},
            "confidence": 0.99,
            "meta": {
                "matched_by": "eli.runtime_audit_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_audit",
            },
        }

    if re.search(r"\bcognition\s+pipeline\b", low) or re.search(r"\binput\s+to\s+output\b", low):
        return {
            "action": "EXPLAIN_COGNITION_RUNTIME",
            "args": {},
            "confidence": 0.99,
            "meta": {
                "matched_by": "eli.cognition_pipeline_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "grounded_audit",
            },
        }

    # SELF_ANALYZE inspects ELI's OWN failure log — so require a self-reference
    # ("your/you/eli"), not a bare "recent failures" / "root cause" which over-matched
    # questions about the user's own code/project.
    if (re.search(r"\b(your|you'?ve had|eli'?s)\s+(?:recent\s+)?failures?\b", low)
            or re.search(r"\bactual root cause of (?:your|eli'?s|the last)\b", low)
            or re.search(r"\banalyse?\s+(?:your|eli'?s)\s+(?:own\s+)?(?:recent\s+)?failures?\b", low)):
        return {
            "action": "SELF_ANALYZE",
            "args": {},
            "confidence": 0.98,
            "meta": {
                "matched_by": "eli.failure_analysis_guard",
                "need_grounding": True,
                "allow_chat_without_evidence": False,
                "task_family": "self_improvement",
            },
        }

    return None


def _eli_recent_memory_processing_question(text: object) -> bool:
    low = str(text or "").strip().lower()
    if not low:
        return False

    # Preserve existing count-only route.
    if _eli_recent_mem_re.search(r"\b(how many|count|number of)\b.{0,40}\b(memories|memory rows|memory entries)\b", low):
        return False

    # "processed"/"processing" are deliberately NOT memory terms. They also
    # appear in recent_terms below, so counting them here collapsed the two-signal
    # AND at the bottom of this function into a bare "processing" + "you" test —
    # which routed behavioural complaints ("you're not processing my questions
    # properly, are you okay?") to a SQLite memory inventory. The explicit
    # patterns below still catch "what memories have you been processing".
    memory_terms = _eli_recent_mem_re.search(
        r"\b(memories|memory|remembered|remembering|learning|stored|recalled|recall)\b",
        low,
    )
    recent_terms = _eli_recent_mem_re.search(
        r"\b(lately|recently|latest|last|currently|been|processing|working on|what .* processing)\b",
        low,
    )

    patterns = [
        r"\bwhat\s+memories\s+have\s+you\s+been\s+processing\b",
        r"\bwhat\s+memory\s+have\s+you\s+been\s+processing\b",
        r"\bwhat\s+have\s+you\s+been\s+remembering\b",
        r"\bwhat\s+have\s+you\s+remembered\s+(?:recently|lately)\b",
        r"\bwhat\s+have\s+you\s+been\s+learning\s+(?:recently|lately)\b",
        r"\bwhat\s+memories\s+(?:did|do)\s+you\s+(?:process|have)\s+(?:recently|lately)\b",
        r"\bwhat\s+recent\s+memories\b",
        r"\bshow\s+(?:me\s+)?recent\s+memories\b",
        r"\blatest\s+memory\s+(?:activity|processing|updates)\b",
        r"\brecent\s+memory\s+(?:activity|processing|updates)\b",
    ]
    if any(_eli_recent_mem_re.search(p, low) for p in patterns):
        return True

    return bool(memory_terms and recent_terms and "you" in low)


# "What have you been doing?" is two different questions wearing the same words.
# Asked after "what updates have you made to the code", it is the maintenance
# question this grounded contract exists for. Asked in the middle of a chat, it is
# small talk — and answering it with a git/runtime evidence dump is a worse failure
# than the invented maintenance activity the contract was built to prevent.
_BARE_ACTIVITY_PHRASES = (
    "what have you been doing",
    "what have you been processing",
    "what have you been working on",
    "what have you been up to",
    "what you been doing",
    "what you been up to",
)

_DEV_UPDATE_SUBJECT_RE = re.compile(
    r"\b(code|codebase|repo|repositor\w*|git|commit\w*|version|build|patch\w*|"
    r"capabilit\w*|module|test\w*|upgrade\w*|check(?:s|ed|ing)?|update(?:s|d)?|"
    r"maintenance|deploy\w*|repair\w*|refactor\w*|fix(?:es|ed|ing)?|"
    r"runtime|pipeline|system)\b",
    re.I,
)


def _eli_activity_question_is_about_dev_work(low: str) -> bool:
    """True when an activity question names development work as its subject."""
    text = str(low or "")
    if not any(p in text for p in _BARE_ACTIVITY_PHRASES):
        return True          # not a bare activity question; caller's match governs
    return bool(_DEV_UPDATE_SUBJECT_RE.search(text))


def _eli_self_report_recent_updates_question(text):
    low = str(text or "").strip().lower()
    if not low:
        return False

    self_ref = any(x in low for x in (
        "eli",
        "yourself",
        "about yourself",
        "your runtime",
        "your system",
    ))

    update_ref = any(x in low for x in (
        "what updates",
        "which updates",
        "updates and checks",
        "checks have been",
        "checks were",
        "performed as of late",
        # NOTE: bare "as of late" removed — it fires on casual idioms like
        # "are you saying i'm obsessive these days as of late".
        # "performed as of late" above is specific enough.
        "recent checks",
        "routine updates",
        "recent updates",
    )) or any(p in low for p in _BARE_ACTIVITY_PHRASES)

    if not _eli_activity_question_is_about_dev_work(low):
        return False

    return bool(update_ref and (self_ref or " you " in f" {low} " or " your " in f" {low} "))


def _eli_memory_runtime_route_lock_should_trigger(text):
    import re as _re

    raw = str(text or "").strip()
    low = raw.lower()

    if not low:
        return False

    # Literal control/action invocation.
    if _re.search(r"\b(?:run|execute|call|invoke)?\s*`?explain_memory_runtime`?\b", low):
        return True

    # Direct architecture/internal-memory requests.
    if _re.search(
        r"\b("
        r"explain exactly how your memory system works internally|"
        r"memory system works internally|"
        r"how (?:does|do) your memory system work|"
        r"how does your memory work internally|"
        r"which files.*which db tables.*which functions|"
        r"memory runtime(?: surface)?|"
        r"memory architecture|"
        r"memory internals"
        r")\b",
        low,
    ):
        return True

    # Broader DB/schema/function phrasing.
    if _re.search(r"\bmemor(?:y|ies)\b", low) and _re.search(
        r"\b("
        r"database files?|db files?|databases?|sqlite|tables?|schema|"
        r"functions?|internally|architecture|runtime|"
        r"faiss|fts5|vectors?|vectoring|recall_log|conversation_turns|"
        r"user\.sqlite3|agent\.sqlite3|memory\.sqlite3"
        r")\b",
        low,
    ):
        # Personal profile questions should stay personal-memory unless the
        # user explicitly asks architecture/schema/files/functions.
        asks_profile = _re.search(
            r"\b(what do you know about me|what do you remember about me|"
            r"my preferences|my profile|who am i|what is my name)\b",
            low,
        )
        asks_arch = _re.search(
            r"\b(files?|db|database|sqlite|tables?|functions?|schema|"
            r"internally|runtime|architecture)\b",
            low,
        )
        if asks_profile and not asks_arch:
            return False
        return True

    return False


def _eli_memory_runtime_route_lock_result(raw):
    return {
        "action": "EXPLAIN_MEMORY_RUNTIME",
        "args": {"question": str(raw or ""), "detail": "full"},
        "confidence": 0.995,
        "meta": {
            "matched_by": "eli.memory_runtime_route_lock_v1",
            "need_grounding": True,
            "allow_chat_without_evidence": False,
            "task_family": "memory_runtime",
            "response_contract": "canonical_grounded_memory_runtime_no_raw_gguf",
        },
    }

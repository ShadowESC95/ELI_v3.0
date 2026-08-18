"""Engine guards — output/evidence guards shared by the engine and its stages.

Four cohesive groups:
  * failed-executor: detect a failed executor result in the evidence and surface
    it honestly instead of letting the model narrate around it;
  * identity self-report: spot an identity self-report request and reject a bad
    self-report output;
  * scaffold strip: remove reasoning scaffolding from generated text;
  * anti-repeat: refuse to serve a reply ELI has already given.

Lifted from engine.py so the engine and the reasoning-mode stage share one
implementation rather than each reaching into the monolith.
"""
from __future__ import annotations

import difflib
import json
import re

# Actions whose evidence is a REPORT ABOUT the system. Their bundles quote git
# logs, failure counts and telemetry by design, so scanning that prose for error
# words cannot distinguish "this action failed" from "this action is describing a
# failure". The caller already knows whether these succeeded — SELF_REPORT came
# back ok=True with synthesis_validated=True while this guard was overwriting its
# content with "I did not successfully complete `ACTION`".
#
# The trigger was a git commit message quoted in the evidence:
#   "...stop printing a FileNotFoundError stack when clearing a pending fix"
# A commit about FIXING an error was read as an error. Which is why the test for
# this passed all morning and failed the same afternoon on an unchanged commit:
# the commit LOG had changed, not the code.
#
# Membership is decided by what the action DOES, never by how it is named. The
# self-* actions that APPLY changes — SELF_PATCH, SELF_IMPROVE, SELF_UPDATE,
# SELF_UPGRADE — are deliberately absent: a failed self-patch narrated as success
# is precisely the fabrication this guard exists to stop. Only actions that read
# and describe belong here.
_REPORTS_ABOUT_STATE = frozenset({
    # Reports carried over from v2.
    "SELF_REPORT", "RUNTIME_STATUS", "RUNTIME_AUDIT", "IMPORT_AUDIT",
    "GUI_RUNTIME_AUDIT", "MEMORY_STATUS", "COGNITION_STATUS", "META_DIAGNOSTIC",
    "SELF_ANALYZE", "SELF_IMPROVEMENT_LOG", "EXPLAIN_LAST_RESPONSE",
    "EXPLAIN_MEMORY_RUNTIME", "EXPLAIN_COGNITION_RUNTIME", "ROUTING_FAULT_EXPLAIN",
    "PERSONAL_MEMORY_DEEP_EXPLAIN", "PERSONAL_MEMORY_SUMMARY", "EXAMINE_CODE",
    # v3-only read-and-describe actions, vulnerable to the same confusion.
    # EXPLAIN_LAST_FAILURE is the clearest case in the tree: its evidence is a
    # failure by definition, so the unpatched guard could never let it answer.
    "EXPLAIN_LAST_FAILURE", "EXPLAIN_ALL_REASONING_MODES", "AWARENESS_STATUS",
    "CHECK_TARGET_STATUS", "DIAGNOSE_WRAPPERS", "ELI_IDENTITY_AUDIT",
    "FILE_AUDIT", "FRONTIER_STATUS", "GAZE_STATUS", "GET_STATUS", "GPU_STATUS",
    "HABIT_STATUS", "IMAGE_STATUS", "LORA_STATUS", "MCP_STATUS", "MORNING_REPORT",
    "NAME_SOURCE_AUDIT", "ORCHESTRATION_STATUS", "PERSONA_LOCK_STATUS",
    "PLUGIN_STATUS", "POMODORO_STATUS", "PROACTIVE_STATUS",
    "REASONING_MODE_STATUS", "SELF_TEST", "STT_DIAGNOSTICS",
    "USER_IDENTITY_SUMMARY", "USER_INFO_REPORT", "VOICE_DIAGNOSTICS",
})

# Actions that CHANGE something. Never exempt, however report-like the name
# looks — asserted by tests/test_failure_words_in_prose.py so a later edit
# cannot quietly move one into the set above.
_MUTATING_SELF_ACTIONS = frozenset({
    "SELF_PATCH", "SELF_IMPROVE", "SELF_UPDATE", "SELF_UPGRADE",
})


def _failed_executor_is_failed(evidence: str, action: str = "") -> bool:
    """Return True if evidence indicates an executor action failed."""
    import re as _re
    ev = str(evidence or "")
    low = ev.lower()
    act = str(action or "").upper().strip()

    # A report about failures is not a failed report — see the note above.
    if act in _REPORTS_ABOUT_STATE:
        return False

    actionish = bool(act and act not in {"CHAT", "NONE"})
    _inferred = False
    if not actionish:
        actionish = any(x in low for x in (
            "'action':", '"action":', "action=", "analyze_pdf",
            "runtime_audit", "execute result", "agent:system", "response_mode",
        ))
        _inferred = actionish
    if not actionish:
        return False

    # A REPORT ABOUT failures is not a failed report.
    #
    # When no action was passed, "this evidence is an executor result" is inferred
    # from markers like `action=` appearing anywhere — and a self-report bundles
    # agent-dispatch telemetry, whose every line looks exactly like that:
    #
    #     action=CHAT agents=[memory, orchestrator] confidence=0.84 ok=True
    #     action=MEMORY_RECALL ... ok=False
    #
    # So one failed dispatch recorded anywhere in the log made SELF_REPORT return
    # "I did not successfully complete `ACTION`" — with a placeholder name,
    # because there was no action to name — while the report itself carried
    # ok=True and synthesis_validated=True. It passed for months and started
    # failing the day a failure row existed.
    #
    # On the inferred path, require a marker that a specific EXECUTOR RESULT
    # failed, rather than any `ok=false` that might be a logged row about some
    # other turn. An explicitly-passed action keeps the original, broader check:
    # there the caller has told us which action's result this is.
    if _inferred:
        _dict_form = bool(
            _re.search(r'["\']ok["\']\s*:\s*false\b', low)
            or _re.search(r'["\']ok["\']\s*:\s*False\b', ev)
        )
        _explicit_failure = bool(
            "execute result" in low and _dict_form
            or "successful: 0 | failed:" in low
            or "filenotfounderror" in low
            or ("traceback" in low and "failed" in low)
        )
        return _explicit_failure

    return bool(
        _re.search(r'["\']ok["\']\s*:\s*false\b', low)
        or _re.search(r'["\']ok["\']\s*:\s*False\b', ev)
        or _re.search(r'\bok\s*=\s*false\b', low)
        or _re.search(r'\bok:\s*false\b', low)
        or "successful: 0 | failed:" in low
        or "file not found" in low
        or "filenotfounderror" in low
        or ("traceback" in low and "failed" in low)
    )


def _failed_executor_relevant_block(prompt: str) -> str:
    """Extract only the executor-result lines from a full prompt string.

    Avoids scanning persona notes, memory artefacts, or unrelated action names
    that can appear elsewhere in the prompt.
    """
    import re as _re
    text = str(prompt or "")
    lines = text.splitlines()
    selected = []

    for i, line in enumerate(lines):
        low = line.lower()
        if "execute result" in low and ("'ok': false" in low or '"ok": false' in low):
            selected.append(line.strip())
            for j in range(i + 1, min(len(lines), i + 8)):
                nxt = lines[j].strip()
                nl = nxt.lower()
                if not nxt:
                    continue
                if (
                    "filenotfounderror" in nl or "traceback" in nl
                    or "error" in nl or ".pdf" in nl
                    or ("attempt" in nl and "failure" in nl)
                ):
                    selected.append(nxt)

    if selected:
        return "\n".join(selected)

    # Fallback: grounded_evidence block only.
    m = _re.search(r"<grounded_evidence>\s*(.*?)\s*</grounded_evidence>", text, _re.I | _re.S)
    if m:
        block = m.group(1).strip()
        filtered = [
            s.strip() for s in block.splitlines()
            if any(x in s.lower() for x in (
                "'ok': false", '"ok": false', "execute result",
                "filenotfounderror", "file not found", "error", ".pdf", "analyze_pdf",
            ))
        ]
        return "\n".join(filtered) if filtered else block[:3000]

    m = _re.search(
        r"AGENT DATA:\s*(.*?)(?:\n\nUSER QUESTION:|\n\nYOUR ANSWER:|$)",
        text, _re.I | _re.S,
    )
    if m:
        return m.group(1).strip()[:3000]

    return ""


def _failed_executor_is_failed_block(block: str) -> bool:
    low = str(block or "").lower()
    # Structured evidence that a result failed — unambiguous.
    if ("'ok': false" in low or '"ok": false' in low
            or "ok=false" in low or "ok: false" in low
            or "successful: 0 | failed:" in low or "analyze_pdf failure" in low):
        return True
    # A bare error NAME in prose is not a failure. A self-report quotes git
    # commit messages, and one of them —
    #   "stop printing a FileNotFoundError stack when clearing a pending fix"
    # — was read as a live FileNotFoundError, so a successful report was
    # replaced with "I did not successfully complete `ACTION`". Require the name
    # to sit next to something that marks an actual result.
    return (("filenotfounderror" in low or "file not found" in low)
            and ("execute result" in low or "traceback" in low))


def _failed_executor_query_from_prompt(prompt: str) -> str:
    import re as _re
    text = str(prompt or "")
    for pat in (
        r"USER ASKED:\s*(.+?)(?:\n\n|$)",
        r"USER QUESTION:\s*(.+?)(?:\n\n|$)",
        r"USER:\s*(.+?)(?:\n\n|$)",
    ):
        m = _re.search(pat, text, _re.I | _re.S)
        if m:
            return m.group(1).strip()[:1200]
    return ""


def _failed_executor_action_name(block: str, query: str = "") -> str:
    import re as _re
    text = str(block or "")
    for pat in (
        r"'action'\s*:\s*'([^']+)'",
        r'"action"\s*:\s*"([^"]+)"',
        r"\baction\s*=\s*([A-Z_]+)",
        r"\baction:\s*([A-Z_]+)",
    ):
        m = _re.search(pat, text)
        if m:
            return m.group(1).upper().strip()
    if "analyze_pdf" in text.lower() or (".pdf" in query.lower() and any(
        x in query.lower() for x in ("read", "summari", "analyse", "analyze")
    )):
        return "ANALYZE_PDF"
    return "ACTION"


def _failed_executor_paths(block: str, query: str = "") -> list:
    import re as _re
    out: list = []
    for m in _re.finditer(r"(/[^,\n\r`\"']+?\.pdf)\b", f"{block}\n{query}", _re.I):
        p = m.group(1).strip().rstrip(" .;:)]}")
        if p not in out:
            out.append(p)
    return out[:8]


def _failed_executor_errors(block: str) -> list:
    import re as _re
    out: list = []
    for pat in (
        r"'error'\s*:\s*'([^']{1,300})'",
        r'"error"\s*:\s*"([^"]{1,300})"',
        r"(FileNotFoundError:\s*[^\n\r]{1,300})",
        r"(No such file or directory[^\n\r]{0,200})",
        r"(This is attempt\s+\d+\s+for the same\s+[A-Z_]+\s+failure\s+`[^`]+`)",
    ):
        for m in _re.finditer(pat, str(block or "")):
            s = m.group(1).strip()
            if s and s not in out:
                out.append(s)
    return out[:8]


def _failed_executor_surface(evidence: str, query: str = "", action: str = "") -> str:
    """Format a clean failure response without calling GGUF."""
    block = _failed_executor_relevant_block(str(evidence or ""))
    if not block:
        block = str(evidence or "")[:3000]
    act = _failed_executor_action_name(block, query)
    paths = _failed_executor_paths(block, query)
    errors = _failed_executor_errors(block)

    lines = (
        ["I did not successfully analyse the PDF request."]
        if act == "ANALYZE_PDF"
        else [f"I did not successfully complete `{act}`."]
    )
    if errors:
        lines += ["", "What failed:"] + [f"- {e}" for e in errors]
    if paths:
        lines += ["", "Path(s) involved:"] + [f"- `{p}`" for p in paths]
    lines += [
        "",
        "I am not going to claim the document was read or summarised, "
        "because the executor evidence says the action failed.",
    ]
    return "\n".join(lines).strip()


def _eli_identity_self_report_request(text: str) -> bool:
    low = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not low:
        return False
    return bool(re.search(
        r"\b("
        r"who are you"
        r"|who you are"
        r"|what are you(?:\s|$)"
        r"|do you know who you are"
        r"|tell me about yourself"
        r"|tell me .{0,40}who you are"
        r"|as (?:a |an )?(?:person|entity)"
        r"|your identity"
        r"|your persona"
        r"|persona evolved"
        r"|identity evolved"
        r"|defined with .{0,80}memories"
        r"|how .{0,80}(?:persona|identity).{0,80}(?:evolved|defined)"
        r")\b",
        low,
    ))


def _eli_bad_identity_self_report_output(user_input: str, response: str) -> bool:
    if not _eli_identity_self_report_request(user_input):
        return False
    text = str(response or "").strip()
    if not text:
        return True
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            surface = str(parsed.get("surface") or "").lower()
            ident = parsed.get("identity") if isinstance(parsed.get("identity"), dict) else {}
            sources = ident.get("grounding_sources") if isinstance(ident, dict) else []
            if (
                surface in {"identity_evidence", "identity_runtime_evidence", "self_report_evidence"}
                and str(ident.get("name") or "").lower() == "eli"
                and {"persona", "memory"} <= {str(x).lower() for x in (sources or [])}
            ):
                return False
    except Exception:
        pass
    low = text.lower()
    if re.search(r"\byour (?:identity|persona)\b", low):
        return True
    if text.endswith("?") and re.match(
        r"(?is)^\s*(who|what|when|where|why|how|do|does|did|is|are|can|could|should|would)\b",
        text,
    ):
        return True
    has_eli_first_person = bool(re.search(r"\b(i am|i'm)\s+eli\b|\bmy (?:identity|persona)\b", low))
    settings_heavy = bool(re.search(r"\b(model details|model path|context size|gpu layers|batch size|provider=|ctx=)\b", low))
    if not has_eli_first_person:
        return True
    if settings_heavy and not re.search(r"\bpersona|memory|reflection|identity|local files|runtime state\b", low):
        return True
    return False


def _strip_reasoning_scaffold(text: str) -> str:
    """Strip leftover internal-deliberation prefixes that small LLMs leak into the
    final user-visible answer. Covers role/mode/stage/approach labels at the very
    start of the response. Conservative — only removes the opening label line(s),
    leaves the answer body intact."""
    t = str(text or "").lstrip()
    if not t:
        return t
    # Forbidden opening forms — each pattern only strips when it matches the START
    # of the response, then re-strips leading whitespace and re-checks.
    _scaffold_re = re.compile(
        r"^(?:"
        # Speaker/role label: "As ELI:", "ELI:", "As an AI:", "As a/an X:"
        r"as\s+eli[:,]?\s*|"
        r"eli[:,]\s*|"
        r"as\s+an?\s+(?:ai|assistant|llm|agent)[:,]?\s*|"
        # Mode label: "Quick:", "CoT:", "Constitutional:", "Chain of Thought:"
        r"(?:quick|cot|chain\s+of\s+thought|self[-\s]?consistency|"
        r"tree\s+of\s+thoughts?|constitutional(?:\s+ai)?)[:,]?\s*|"
        # Approach/stage/step preamble: "Approach 3:", "Stage 1:", "Step 2:"
        r"(?:approach|stage|step|option|candidate|path|plan|strategy|selected)"
        r"\s*[0-9]*\s*[:.\-]\s*[^\n]*\n+|"
        # Generic single-line announcement followed by colon-list intro
        r"selected\s+approach[:,]?\s*[^\n]*\n+"
        r")",
        re.IGNORECASE,
    )
    # Apply iteratively until no more leading scaffold matches (handles stacked prefixes).
    for _ in range(4):
        new_t = _scaffold_re.sub("", t, count=1).lstrip()
        if new_t == t:
            break
        t = new_t
    return t


# ── Anti-repeat: verification, not just instruction ──────────────────────────
# The prompt-level contract in the generation stage ("YOU HAVE ALREADY SAID THE
# FOLLOWING — do not repeat any of it") is advisory. In one observed v2 session an
# 8B model ignored it four times, serving the same 40-word paragraph verbatim at
# 13:52, 13:52, 13:55 and 13:56 — including right after the user told it to stop and
# it answered "I'll stop". Asking a model not to repeat itself is not a control;
# checking whether it did is.
#
# Self-contained by necessity: engine.py imports the stages, so reaching back for
# engine._clarifier_norm would close an import cycle.
_REPEAT_HEAD_CHARS = 200      # buffered before first paint
_REPEAT_RATIO = 0.86          # difflib ratio over normalised text
# A stock opener repeated across replies reads as a tic even when the rest
# differs. Short openers ("Yes.", "Okay.") are exempt — not a stylistic rut.
_REPEAT_OPENING_MIN_CHARS = 12
_REPEAT_OPENING_RATIO = 0.92
_REPEAT_COMPARE_AGAINST = 4   # how many recent ELI turns to test
_REPEAT_RETRY_TEMP_BUMP = 0.35  # widen sampling on the retry — see the retry path


def _repeat_norm(text: str) -> str:
    """Punctuation- and case-insensitive form. Apostrophes are deleted rather than
    spaced so "what's" and "whats" collapse together instead of leaving "what s"."""
    lowered = str(text or "").lower().replace("'", "").replace("’", "")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", lowered)).strip()


_REPEAT_REQUESTED_RE = re.compile(
    r"\b(say (that|it) again|repeat (that|it|yourself)|come again|again please"
    r"|what did you (just )?say|read (that|it) back|one more time"
    r"|(say|tell) (me )?(that|it) (once )?more)\b",
    re.I,
)


def _user_asked_for_a_repeat(user_input: str) -> bool:
    """True when repeating IS the request.

    Without this the anti-repeat guard becomes its own bug: "say that again" would
    be answered, correctly, with the previous reply — which the guard would then
    reject as a repeat and regenerate into something the user did not ask for.
    """
    return bool(_REPEAT_REQUESTED_RE.search(str(user_input or "")))


# A greeting is the other case where recurring is correct. "Evening" at 23:00 is
# ELI reading the authoritative clock in its own system prompt, not recycling a
# paragraph — but to the guard it looked like the previous session's opener.
#
# Live at 2.1.83, 23:10: the first generation opened 'Even…' (right), the guard
# matched it against a recent reply and regenerated, and the retry — explicitly
# instructed to produce "different content" — echoed the user's own misspelling
# echoing the user's own typo and then telling him off for it. A correct answer
# was replaced by a wrong one because it resembled a previous hello.
#
# Trade-off, stated plainly: greeting turns lose anti-repeat protection, so ELI
# may open two sessions the same way. That is a far smaller failure than naming
# the wrong time of day, and it is the same call already made for "say that
# again" above — when repetition is the correct answer, the guard stands down.
_GREETING_STEMS = (
    "hello", "hi", "hey", "hiya", "howya", "howdy", "yo", "sup",
    "morning", "afternoon", "evening", "night", "nite", "greetings",
)
# Words that can precede the greeting itself ("good afternoon", "ah hello").
_GREETING_LEAD_SKIP = {"good", "a", "the", "well", "ah", "oh", "so", "hows", "how"}
_GREETING_MAX_WORDS = 6       # beyond this a request is attached, not just a hello
_GREETING_FUZZ = 0.8          # "aftrnoon" ≈ "afternoon" (0.94)
_GREETING_FUZZ_MIN_LEN = 4    # below this only exact matches — "his" scores 0.8 vs "hi"
# A time-of-day word can also open a real request — "night mode is broken please
# fix". That is not a hello, and it should keep its anti-repeat protection.
_GREETING_DISQUALIFIER_RE = re.compile(
    r"\b(fix|broken|error|bug|crash(?:ed|ing)?|fail(?:ed|ing|s)?|please|can you|"
    r"could you|why|how do|show me|open|run|write|check|make|set|change|update)\b",
    re.I,
)


def _is_greeting_turn(user_input: str) -> bool:
    """True when the user is saying hello.

    Typo-tolerant on purpose: the live failure arrived as "Good aftrnoon, Eli", and
    a check that only recognised correctly-spelled greetings would not have stood
    down for the very message that exposed the bug.
    """
    norm = _repeat_norm(user_input)
    if not norm:
        return False
    if _GREETING_DISQUALIFIER_RE.search(str(user_input or "")):
        return False
    words = norm.split()
    if len(words) > _GREETING_MAX_WORDS:
        return False
    for word in words[:2]:
        if word in _GREETING_LEAD_SKIP:
            continue
        if word in _GREETING_STEMS:
            return True
        if len(word) >= _GREETING_FUZZ_MIN_LEN:
            return any(
                len(stem) >= _GREETING_FUZZ_MIN_LEN
                and difflib.SequenceMatcher(None, word, stem).ratio() >= _GREETING_FUZZ
                for stem in _GREETING_STEMS
            )
        return False
    return False


# The contract block injected into situation_brief, matched so a retry can remove it.
_ANTI_REPEAT_BLOCK_RE = re.compile(
    r"YOU HAVE ALREADY SAID THE FOLLOWING.*?do not describe it again unless asked\.",
    re.S,
)


def _deprimed_persona_brief(situation_brief: str) -> str:
    """Strip the verbatim prior replies from the PERSONA HANDOFF before regenerating.

    Named for its argument on purpose. The first version took `prompt` and was a
    no-op: in the generation stage `prompt` is the user's own message
    (``prompt = str((args or {}).get("message") or user_input or "").strip()``),
    while the anti-repeat contract is injected into `situation_brief`, which reaches
    the model as ``wm.persona_handoff``. Stripping the user's message removed
    nothing and prepended an instruction to their words instead. The live v2 log
    said so plainly — "prior replies stripped from prompt: 26 -> 143 chars", and 26
    is the length of "fair point pal, fair point".

    Observed live (v2.1.60, 15:13): the guard caught a repeat, retried, and the
    retry produced the SAME opening — prompt_tokens barely moved, 4501 -> 4495,
    because the retry was `instruction + prompt` and `prompt` still carried the
    anti-repeat contract quoting three prior replies at up to 220 chars each.

    So the retry handed the model the exact text it must not produce and asked it
    not to produce it. For a small model that is priming, not prevention: the
    surest way to make it say something is to put it in the context window. The
    remedy is subtraction — remove the quotes, keep a constraint that names no
    content. (A turn earlier the same guard retried successfully, 'You' -> 'Hello';
    the failure is not universal, which is why it needed a live session to find.)
    """
    stripped = _ANTI_REPEAT_BLOCK_RE.sub(
        "Do not repeat anything you have already said in this conversation, and do "
        "not re-ask a question the user has already answered.",
        str(situation_brief or ""),
    )
    return (
        "Your previous attempt repeated an earlier reply. Respond only to the user's "
        "latest message, with different content.\n\n" + stripped
    ).strip()


def _repeat_ratio(a: str, b: str) -> float:
    na, nb = _repeat_norm(a), _repeat_norm(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


# ── Anti-echo: ELI must not speak the user's own sentence back as its own ────
# Observed live (v2 2.1.95). User: "Still on loop, seson 3 now. How is your
# memory after all the codebse changes?"  ELI: "I'm still on loop, season 3 now.
# Your memory's a bit fuzzy…" — the user's line returned in the first person,
# with the roles inverted, and RAW_HEAD confirms the model generated it rather
# than any display layer adding it.
#
# The repeat guard above cannot catch this. It compares head-to-head over equal
# lengths, which scores that pair at 0.667 against the 0.86 bar, because only
# the FIRST SENTENCE was copied and the rest diverged. Compared sentence to
# sentence the same pair scores 0.926. So this checks the opening sentence
# specifically, and is deliberately separate from _is_repeat_of_recent: that
# function means "ELI already said this", a different claim about a different
# corpus, and widening it would change every caller.
#
# A false positive costs one regeneration and the caller's salvage path still
# serves a sane reply, so the threshold favours catching the echo.
_ECHO_MIN_SENTENCE_CHARS = 18     # shorter than this, agreement is not plagiarism
_ECHO_RATIO = 0.80
# The corpus is the current message PLUS recent user turns, because the echo that
# prompted this widening came from a session three hours earlier, reintroduced
# through recalled memory. Comparing a single opening sentence against ~17 short
# strings is trivial next to the generation it guards.
_ECHO_MAX_SOURCES = 20
# Reproducing a whole message back is an echo however its first sentence
# tokenises. Long enough that a short shared phrase ("yeah, fair enough") is not
# mistaken for one.
_ECHO_MIN_HEAD_CHARS = 40


def _first_sentence(text: str) -> str:
    parts = _SENTENCE_SPLIT_RE.split(str(text or "").strip(), maxsplit=1)
    return (parts[0] if parts else "").strip()


def _opens_by_echoing(opening: str, sources) -> bool:
    """True when the reply's first sentence restates a sentence the USER wrote.

    Only the opening is judged: quoting the user mid-reply ("you said X, and
    that's why…") is legitimate and common. Leading with their sentence as your
    own is the failure.
    """
    # WHOLE-HEAD test first. Judging only the opening sentence meant a SHORT
    # opening sentence disabled the guard entirely — live at v2 2.3.0 ELI replied
    # with the user's own message reproduced verbatim (ratio 1.000), and this
    # returned False because its first sentence, "You're not wrong.", is 15
    # normalised characters against the 18-character minimum below.
    _head = _repeat_norm(opening)
    if len(_head) >= _ECHO_MIN_HEAD_CHARS:
        for src in list(sources or [])[:_ECHO_MAX_SOURCES]:
            _src = _repeat_norm(src)
            if len(_src) < _ECHO_MIN_HEAD_CHARS:
                continue
            n = min(len(_head), len(_src))
            if _repeat_ratio(_head[:n], _src[:n]) >= _ECHO_RATIO:
                return True

    first = _first_sentence(opening)
    if len(_repeat_norm(first)) < _ECHO_MIN_SENTENCE_CHARS:
        return False
    for src in list(sources or [])[:_ECHO_MAX_SOURCES]:
        for sentence in _SENTENCE_SPLIT_RE.split(str(src or "").strip()):
            sentence = sentence.strip()
            if len(_repeat_norm(sentence)) < _ECHO_MIN_SENTENCE_CHARS:
                continue
            if _repeat_ratio(first, sentence) >= _ECHO_RATIO:
                return True
    return False


def _is_repeat_of_recent(candidate: str, prior_replies, *,
                         ratio: float = _REPEAT_RATIO) -> bool:
    """True when `candidate` restates something ELI already said.

    Compared head-to-head over the same number of characters: a reply that opens
    identically and then continues is still a repeat to the reader, and comparing a
    200-char head against a 600-char prior reply would score low purely on the
    length difference and let it through.
    """
    cand = str(candidate or "").strip()
    if len(_repeat_norm(cand)) < 40:
        return False                      # too short to judge; "Okay." is not a repeat

    # A recycled OPENING SENTENCE is a repeat to the reader even when everything
    # after it is new — the head-to-head ratio below cannot see it, because the
    # differing remainder drags the score under the threshold. Live at v2 2.3.0
    # seven consecutive replies opened "You're not wrong." and the guard fired on
    # only two of them.
    _cand_open = _repeat_norm(_first_sentence(cand))
    if len(_cand_open) >= _REPEAT_OPENING_MIN_CHARS:
        for _prior in list(prior_replies or [])[:_REPEAT_COMPARE_AGAINST]:
            _prior_open = _repeat_norm(_first_sentence(_prior))
            if len(_prior_open) < _REPEAT_OPENING_MIN_CHARS:
                continue
            if _repeat_ratio(_cand_open, _prior_open) >= _REPEAT_OPENING_RATIO:
                return True

    for prior in list(prior_replies or [])[:_REPEAT_COMPARE_AGAINST]:
        p = str(prior or "").strip()
        if not p:
            continue
        n = min(len(cand), len(p))
        if n < 40:
            continue
        if _repeat_ratio(cand[:n], p[:n]) >= ratio:
            return True
    return False


class _RepeatDetected(Exception):
    """Raised inside the streaming guard when the opening restates a recent reply.

    An exception rather than a return value because the check lives inside a
    generator: it has to unwind the partially-consumed stream without having
    yielded any of it to the caller.
    """


def _stream_holding_back_repeats(stream, recent_replies, *, allow_retry: bool,
                                 echo_sources=None,
                                 salvage: bool = False):
    """Yield `stream`, withholding the opening until it is cleared as non-repeating.

    Module-level and dependency-free so the guard can be driven directly by tests;
    a closure inside the streaming method could only ever be tested by
    re-implementing it, which proves nothing.

    Once a chunk is yielded it is on the user's screen, so the check must happen
    before first paint: the first `_REPEAT_HEAD_CHARS` are buffered, tested, then
    released untouched in the common case.

    `allow_retry` raises `_RepeatDetected` so the caller can regenerate. `salvage`
    is for the final attempt, where raising is no longer useful: the recycled
    opening sentences are dropped and the remainder is served, buffering on until
    something novel arrives. A reply that is repeat all the way down still gets
    served whole — an honest duplicate beats an empty turn.
    """
    head, released, salvaging = [], False, False
    for chunk in stream:
        piece = str(chunk or "")
        if not piece:
            continue
        if released:
            yield piece
            continue
        head.append(piece)
        if sum(len(h) for h in head) < _REPEAT_HEAD_CHARS:
            continue
        opening = "".join(head)
        # The user's own sentence coming back in ELI's voice is caught here too:
        # same buffered-head mechanism, same retry, different corpus.
        if not salvaging and echo_sources and _opens_by_echoing(opening, echo_sources):
            if allow_retry:
                raise _RepeatDetected(opening)
            # No retry left: fall through to the repeat handling below, which
            # trims the offending opening sentences rather than serving them.
            recent_replies = list(recent_replies or []) + list(echo_sources or [])
        if not salvaging and recent_replies and _is_repeat_of_recent(opening, recent_replies):
            if allow_retry:
                raise _RepeatDetected(opening)
            if not salvage:
                released = True
                yield opening
                continue
            salvaging = True
        if salvaging:
            novel = _strip_repeated_opening(opening, recent_replies)
            if len(_repeat_norm(novel)) < _REPEAT_MIN_SENTENCE_CHARS:
                # Still inside the recycled preamble, or holding a fragment too
                # short to judge — keep buffering rather than paint it. Releasing
                # on a 3-character tail ("How") would let the rest of a sentence
                # ELI has already said stream out behind it.
                continue
            released = True
            yield novel
            continue
        released = True
        yield opening
    if not released:
        # The stream ended inside the buffer: a short reply, still worth checking.
        opening = "".join(head)
        if opening:
            # An echoed opening is usually SHORT — "I'm still on loop, season 3
            # now." never fills the 200-char buffer — so this path, not the one
            # above, is where the live case is actually caught.
            _echoed = bool(echo_sources) and _opens_by_echoing(opening, echo_sources)
            if _echoed and not salvaging:
                recent_replies = list(recent_replies or []) + list(echo_sources or [])
            if salvaging or _echoed or (recent_replies
                                        and _is_repeat_of_recent(opening, recent_replies)):
                if allow_retry and not salvaging:
                    raise _RepeatDetected(opening)
                if salvage or salvaging:
                    novel = _strip_repeated_opening(opening, recent_replies)
                    # Nothing but repeat, and no more stream to wait for: serve the
                    # duplicate rather than a fragment or an empty turn.
                    if len(_repeat_norm(novel)) >= _REPEAT_MIN_SENTENCE_CHARS:
                        yield novel
                    else:
                        yield opening
                    return
            yield opening


# ── Anti-repeat: salvage instead of serving the duplicate ────────────────────
# Live evidence (2.1.80): attempt 1 opened "You're not wrong — I'm a bit of a hot
# mess…", the guard caught it, the retry opened with the SAME two sentences, and
# the duplicate was served because the retry was the last chance. But the retry
# was not a total loss: after those two recycled sentences it went on to answer
# the question that had actually been asked. Serving or discarding the whole
# reply were both wrong — the repeat was a preamble, not the answer.
#
# So the last attempt trims rather than surrenders: leading sentences that
# restate a recent reply are dropped and the novel remainder is served. Only if
# nothing novel survives does the honest-duplicate rule still apply.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])[\"'’”)\]]*\s+")
_REPEAT_MIN_SENTENCE_CHARS = 12   # below this a sentence carries no content to match on
_REPEAT_MIN_REDACT_CHARS = 40     # shorter lines are too generic to redact on a match


def _split_sentences_with_gaps(text: str):
    """Sentences paired with the whitespace that followed them.

    Keeping the gaps means a trimmed remainder is the ORIGINAL substring, not a
    re-joined approximation — no lost line breaks, no invented spaces.
    """
    body = str(text or "")
    out, last = [], 0
    for m in _SENTENCE_SPLIT_RE.finditer(body):
        out.append((body[last:m.start()], m.group(0)))
        last = m.end()
    out.append((body[last:], ""))
    return [(s, g) for s, g in out if s or g]


def _prior_sentences(prior_replies) -> List[str]:
    sentences: List[str] = []
    for prior in list(prior_replies or [])[:_REPEAT_COMPARE_AGAINST]:
        for s, _gap in _split_sentences_with_gaps(str(prior or "").strip()):
            s = s.strip()
            if s:
                sentences.append(s)
    return sentences


def _sentence_seen_before(sentence: str, prior_sentences, *, partial: bool) -> bool:
    """True when this sentence restates one ELI already delivered.

    `partial` is for the trailing fragment of a still-streaming buffer: it is
    compared against the OPENING of each prior sentence, so a repeat that is only
    half-emitted is recognised as one instead of reading as novel text.
    """
    cand = _repeat_norm(sentence)
    if len(cand) < _REPEAT_MIN_SENTENCE_CHARS:
        return False
    for prior in prior_sentences:
        pn = _repeat_norm(prior)
        if len(pn) < _REPEAT_MIN_SENTENCE_CHARS:
            continue
        if partial:
            if len(pn) < len(cand):
                continue
            pn = pn[:len(cand)]
        if difflib.SequenceMatcher(None, cand, pn).ratio() >= _REPEAT_RATIO:
            return True
    return False


def _strip_repeated_opening(text: str, prior_replies) -> str:
    """Return `text` with its recycled opening sentences removed.

    Returns "" when every sentence is one ELI has already said — the caller
    decides what to do with a reply that is nothing but repeat.
    """
    body = str(text or "")
    priors = _prior_sentences(prior_replies)
    if not body.strip() or not priors:
        return body
    parts = _split_sentences_with_gaps(body)
    keep = 0
    for i, (sentence, _gap) in enumerate(parts):
        stripped = sentence.strip()
        if not stripped:
            keep = i + 1
            continue
        complete = bool(re.search(r"[.!?…][\"'’”)\]]*$", stripped))
        is_tail = (i == len(parts) - 1)
        if _sentence_seen_before(stripped, priors, partial=(is_tail and not complete)):
            keep = i + 1
            continue
        break
    if keep == 0:
        return body
    return "".join(s + g for s, g in parts[keep:]).lstrip()


def _redact_prior_replies(text: str, prior_replies) -> str:
    """Remove ELI's own recent replies from a prompt block before regenerating.

    De-priming the persona brief alone was not enough: the same paragraph also
    reaches the model through the assembled memory context and the handoff's
    evidence block, so the retry was still shown the exact text it had just been
    told not to produce. Matching is line-wise and ratio-based, so unrelated
    evidence on neighbouring lines survives intact.
    """
    body = str(text or "")
    priors = [str(p or "").strip() for p in list(prior_replies or [])[:_REPEAT_COMPARE_AGAINST]]
    priors = [p for p in priors if len(_repeat_norm(p)) >= _REPEAT_MIN_REDACT_CHARS]
    if not body or not priors:
        return body
    kept = []
    for line in body.splitlines():
        norm = _repeat_norm(line)
        if len(norm) >= _REPEAT_MIN_REDACT_CHARS and any(
                _repeat_ratio(line, p) >= _REPEAT_RATIO
                or _repeat_norm(p) in norm
                for p in priors):
            continue
        kept.append(line)
    return "\n".join(kept)



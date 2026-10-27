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
from typing import List
from eli.kernel.stages._common import _clarifier_norm

# Actions whose evidence is a report about the system. Their bundles quote git logs and failure
# counts by design, so scanning for error words can't tell "failed" from "describing a failure".
# The caller already knows whether they succeeded.
_REPORTS_ABOUT_STATE = frozenset({
    "SELF_REPORT", "RUNTIME_STATUS", "RUNTIME_AUDIT", "IMPORT_AUDIT",
    "GUI_RUNTIME_AUDIT", "MEMORY_STATUS", "COGNITION_STATUS", "META_DIAGNOSTIC",
    "SELF_ANALYZE", "SELF_IMPROVEMENT_LOG", "EXPLAIN_LAST_RESPONSE",
    "EXPLAIN_MEMORY_RUNTIME", "EXPLAIN_COGNITION_RUNTIME", "ROUTING_FAULT_EXPLAIN",
    "PERSONAL_MEMORY_DEEP_EXPLAIN", "PERSONAL_MEMORY_SUMMARY", "EXAMINE_CODE",
    "SELF_REPAIR_PLAYBOOK",
    # v3 read-and-describe actions. EXPLAIN_LAST_FAILURE's evidence is a failure by definition, so
    # without this it could never answer.
    "EXPLAIN_LAST_FAILURE", "EXPLAIN_ALL_REASONING_MODES", "AWARENESS_STATUS",
    "CHECK_TARGET_STATUS", "DIAGNOSE_WRAPPERS", "ELI_IDENTITY_AUDIT",
    "FILE_AUDIT", "FRONTIER_STATUS", "GAZE_STATUS", "GET_STATUS", "GPU_STATUS",
    "HABIT_STATUS", "IMAGE_STATUS", "LORA_STATUS", "MCP_STATUS", "MORNING_REPORT",
    "NAME_SOURCE_AUDIT", "ORCHESTRATION_STATUS", "PERSONA_LOCK_STATUS",
    "PLUGIN_STATUS", "POMODORO_STATUS", "PROACTIVE_STATUS",
    "REASONING_MODE_STATUS", "SELF_TEST", "STT_DIAGNOSTICS",
    "USER_IDENTITY_SUMMARY", "USER_INFO_REPORT", "VOICE_DIAGNOSTICS",
})

# Actions that change something are never exempt, however report-like the name looks.
_MUTATING_SELF_ACTIONS = frozenset({"SELF_PATCH", "SELF_IMPROVE", "SELF_UPDATE", "SELF_UPGRADE"})


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

    # A report about failures is not a failed report. With no action passed we infer "executor
    # result" from markers, and a self-report is full of them, so one failed dispatch anywhere made
    # it say "I did not successfully complete". On the inferred path require a marker for a specific
    # failed result. An explicit action keeps the broader check.
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
    # A bare error name in prose isn't a failure. A self-report quotes commit messages ("stop
    # printing a FileNotFoundError stack..."), which read as a live error and replaced a good report
    # with "I did not successfully complete `ACTION`". Require the name next to something that
    # marks an actual result.
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

    # "ACTION" is the sentinel _failed_executor_action_name() returns when it can't recover a name.
    # Interpolating it printed "I did not successfully complete `ACTION`", a placeholder presented
    # as fact. If we can't say what failed, say that.
    if act == "ANALYZE_PDF":
        lines = ["I did not successfully analyse the PDF request."]
    elif act and act != "ACTION":
        lines = [f"I did not successfully complete `{act}`."]
    else:
        lines = ["That did not complete successfully."]
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
    except (ValueError, TypeError):
        pass  # a plain-text reply isn't JSON — expected, fall through to the text checks
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


# Anti-repeat: verification, not just instruction. The prompt-level contract ("YOU HAVE ALREADY
# SAID THE FOLLOWING") is advisory, and an 8B model ignored it four times and served the same
# paragraph, even after the user said to stop. Asking a model not to repeat itself isn't a
# control, checking whether it did is.
_REPEAT_HEAD_CHARS = 200      # buffered before first paint — see the streaming guard
_REPEAT_RATIO = 0.86          # difflib ratio over normalised text
# A stock opener repeated across replies reads as a tic even when the rest differs.
# Short openers ("Yes.", "Okay.") are exempt — they are not a stylistic rut.
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


# A greeting is the one case where repeating is right: "Evening" at 23:00 is ELI reading the
# clock. Greeting turns skip anti-repeat, same as "say that again". Two sessions opening alike
# costs far less than naming the wrong time of day.
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
    norm = _clarifier_norm(user_input)
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

    Named for its argument on purpose. The first version of this took `prompt` and
    was a no-op: in stream_chat, `prompt` is the user's own message
    (``prompt = str((args or {}).get("message") or user_input or "").strip()``),
    while the anti-repeat contract is injected into `situation_brief`, which
    reaches the model as ``wm.persona_handoff``. Stripping the user's message
    removed nothing and prepended an instruction to their words instead. The live
    log said so plainly — "prior replies stripped from prompt: 26 -> 143 chars",
    and 26 is the length of "fair point pal, fair point".

    Why strip at all: the contract quotes up to three prior replies verbatim at 220
    chars each, so the retry handed the model the exact text it must not produce
    and asked it not to produce it. For a small model that is priming, not
    prevention. The remedy is subtraction — remove the quotes, keep a constraint
    that names no content.
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
    """Similarity of two replies, punctuation- and case-insensitive."""
    na, nb = _clarifier_norm(a), _clarifier_norm(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


# Anti-echo: ELI must not say the user's own sentence back as its own. The repeat guard compares
# whole replies and missed a copy of just the first sentence, so this checks the opening sentence
# on its own. A false positive costs one regeneration, so it leans towards catching the echo.
_ECHO_MIN_SENTENCE_CHARS = 18     # shorter than this, agreement is not plagiarism
_ECHO_RATIO = 0.80
# The corpus is the current message plus recent user turns: the echo that prompted this came from a
# session hours earlier, reintroduced through recalled memory. One opening sentence against ~17
# short strings is trivial next to the generation it guards.
_ECHO_MAX_SOURCES = 20
# Reproducing a whole message back is an echo however its first sentence
# tokenises. Long enough that a short shared phrase ("yeah, fair enough") is
# not mistaken for one.
_ECHO_MIN_HEAD_CHARS = 40


def _first_sentence(text) -> str:
    """The opening sentence of a reply, for the opener-repeat and echo tests.

    There were TWO module-level definitions of this name (ELI's own
    RUNTIME_AUDIT flagged it: "_first_sentence also defined at lines [3508,
    3561]"). Python keeps the last one, so the earlier definition -- the one
    that used _SENTENCE_SPLIT_RE and therefore understood ellipses and closing
    quotes -- was dead code, and _opens_by_echoing() silently got the weaker
    splitter instead. Merged: the better pattern with the safer fallback.
    """
    body = str(text or "").strip()
    if not body:
        return ""
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(body) if p.strip()]
    return parts[0] if parts else body


def _opens_by_echoing(opening: str, sources) -> bool:
    """True when the reply's first sentence restates a sentence the USER wrote.

    Only the opening is judged: quoting the user mid-reply ("you said X, and
    that's why…") is legitimate and common. Leading with their sentence as your
    own is the failure.
    """
    # Whole-head test first. Judging only the opening sentence let a short one disable the guard: a
    # reply that reproduced the user's message verbatim (ratio 1.000) returned False because its
    # first sentence ("You're not wrong.") was under the 18-character minimum.
    _head = _clarifier_norm(opening)
    if len(_head) >= _ECHO_MIN_HEAD_CHARS:
        for src in list(sources or [])[:_ECHO_MAX_SOURCES]:
            _src = _clarifier_norm(src)
            if len(_src) < _ECHO_MIN_HEAD_CHARS:
                continue
            n = min(len(_head), len(_src))
            if _repeat_ratio(_head[:n], _src[:n]) >= _ECHO_RATIO:
                return True

    first = _first_sentence(opening)
    if len(_clarifier_norm(first)) < _ECHO_MIN_SENTENCE_CHARS:
        return False
    for src in list(sources or [])[:_ECHO_MAX_SOURCES]:
        for sentence in _SENTENCE_SPLIT_RE.split(str(src or "").strip()):
            sentence = sentence.strip()
            if len(_clarifier_norm(sentence)) < _ECHO_MIN_SENTENCE_CHARS:
                continue
            if _repeat_ratio(first, sentence) >= _ECHO_RATIO:
                return True
    return False


# How many leading words make an opening recognisable as the SAME opening.
# "You're not wrong" is three; the fourth catches "you're not wrong but".
_REPEAT_STEM_WORDS = 4
_REPEAT_STEM_MIN_CHARS = 12


def _opening_stem(text: str) -> str:
    """The first few words of the opening, before any continuation clause.

    The 2.3.0 fix compared whole first sentences and worked while the tic WAS
    the whole sentence ("You're not wrong."). The model since learned to append
    an em-dash clause, and because the clause varies every time, first-sentence
    similarity drops to ~0.5 against a 0.92 threshold -- so four replies in one
    session opened "You're not wrong -" and the guard fired on none of them,
    while reading to the user as exactly the tic it was written to stop.

    Cutting at the first dash/comma leaves the stem, which is what repeats.
    """
    raw = _first_sentence(str(text or "")).strip()
    stem_raw = re.split(r"[-\u2013\u2014,;:]", raw, 1)[0].strip() or raw
    stem = _clarifier_norm(stem_raw)
    return " ".join(stem.split()[:_REPEAT_STEM_WORDS])


def _is_repeat_of_recent(candidate: str, prior_replies, *,
                         ratio: float = _REPEAT_RATIO) -> bool:
    """True when `candidate` restates something ELI already said.

    Compared head-to-head over the same number of characters: a reply that opens
    identically and then continues is still a repeat to the reader, and comparing a
    200-char head against a 600-char prior reply would score low purely on the length
    difference and let it through.
    """
    cand = str(candidate or "").strip()
    if len(_clarifier_norm(cand)) < 40:
        return False                      # too short to judge; "Okay." is not a repeat

    # A recycled opening sentence is a repeat to the reader even when the rest is new, and the
    # head-to-head ratio can't see it because the differing remainder drags the score down (seven
    # replies opened "You're not wrong." and the guard fired on two). Same opening stem is a repeat
    # however it continues (see _opening_stem). Checked before the whole-sentence test.
    _cand_stem = _opening_stem(cand)
    if len(_cand_stem) >= _REPEAT_STEM_MIN_CHARS and len(_cand_stem.split()) >= 3:
        for prior in list(prior_replies or [])[:_REPEAT_COMPARE_AGAINST]:
            if _opening_stem(prior) == _cand_stem:
                return True

    # Compare first sentence to first sentence, exactly, as its own test.
    _cand_open = _clarifier_norm(_first_sentence(cand))
    if len(_cand_open) >= _REPEAT_OPENING_MIN_CHARS:
        for prior in list(prior_replies or [])[:_REPEAT_COMPARE_AGAINST]:
            _prior_open = _clarifier_norm(_first_sentence(prior))
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

    # The same content re-presented: sentences ELI already delivered, reformatted (a paragraph turned
    # into a list under bold headings) or reordered, open differently and pass every test above. Live:
    # told the news was not what was asked for, ELI served the same stories again as a bulleted list
    # under a new opening line. A reply mostly made of sentences already said is a repeat.
    prior_sents = _prior_sentences(prior_replies)
    if prior_sents:
        lines = []
        for sentence, _gap in _split_sentences_with_gaps(cand):
            for line in _LIST_ITEM_SPLIT_RE.split(sentence):
                line = _LIST_LEAD_RE.sub("", line).strip()
                if len(_clarifier_norm(line)) >= _REPEAT_MIN_REDACT_CHARS:
                    lines.append(line)
        restated = sum(1 for line in lines if _sentence_seen_before(line, prior_sents, partial=False))
        if restated >= _REPEAT_MIN_RESTATED and 2 * restated >= len(lines):
            return True
    return False


# Where a list item starts: a new line, or a bullet inline once the reply's lines were joined.
_LIST_ITEM_SPLIT_RE = re.compile(r"\n+|\s+(?=[*•·-]\s+\S)")
# A list item's lead: the bullet or number, and a bold label ("*   **UK Aid Spending:** ...").
_LIST_LEAD_RE = re.compile(r"^\s*(?:(?:[-*•·]|\d+[.)])\s+)?(?:(?:\*\*|__)[^*_\n]{1,80}?(?:\*\*|__)\s*:?\s*)?")
_REPEAT_MIN_RESTATED = 2     # one sentence said again can be a quote; two of them is the reply again


class _RepeatDetected(Exception):
    """Raised inside the streaming guard when the opening restates a recent reply.

    An exception rather than a return value because the check lives inside a
    generator: it has to unwind the partially-consumed stream without having
    yielded any of it to the caller.
    """


_LEAK_MIN_SENTENCE_CHARS = 30


def _instruction_sentences(*texts) -> set:
    """Normalised sentences of the instruction prose given to the model, evidence lines
    (bullets, tagged and numbered rows) left out."""
    out = set()
    for text in texts:
        for line in str(text or "").splitlines():
            line = line.strip()
            if not line or line[0] in "-•*[" or re.match(r"\d{1,3}\.", line):
                continue
            for sentence in _SENTENCE_SPLIT_RE.split(line):
                norm = _clarifier_norm(sentence)
                if len(norm) >= _LEAK_MIN_SENTENCE_CHARS:
                    out.add(norm)
    return out


def _strip_prompt_leak(opening: str, leak: set):
    """(`opening` without a leading run that recites the instructions, still_inside_it).

    Live on a 35B model: a quick reply began "Friday 02 October 2026, 18:34 IST (evening).
    Night is 21:00-05:00, morning 05:00-12:00, ... never claim you don't track dates", the
    clock paragraph of its own prompt, before the answer. Everything up to the last recited
    sentence goes; regenerating would cost a second full generation."""
    if not leak:
        return opening, False
    parts = _split_sentences_with_gaps(str(opening or ""))
    last = -1
    for i, (sentence, _gap) in enumerate(parts[:8]):
        norm = _clarifier_norm(sentence)
        if len(norm) >= _LEAK_MIN_SENTENCE_CHARS and norm in leak:
            last = i
        elif i > last + 2:
            break
    if last < 0:
        # the stream may have stopped mid-way through the first recited sentence
        norm = _clarifier_norm(parts[-1][0]) if parts else ""
        inside = (len(parts) <= 2 and len(norm) >= 20 and any(l.startswith(norm) for l in leak))
        return opening, inside
    rest = "".join(sent + gap for sent, gap in parts[last + 1:]).lstrip()
    tail = _clarifier_norm(parts[-1][0]) if len(parts) > last + 1 else ""
    inside = (not rest) or (len(parts) == last + 2 and len(tail) >= 12
                            and any(l.startswith(tail) for l in leak))
    return rest, inside


def _stream_holding_back_repeats(stream, recent_replies, *, allow_retry: bool,
                                 salvage: bool = False, echo_sources=None, leak=None):
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
    head, released, salvaging, leaking = [], False, False, False
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
        if leak:
            # An opening that recites the instructions: hold on until it ends, then serve
            # what follows.
            _rest, _inside = _strip_prompt_leak(opening, leak)
            if _rest != opening or _inside:
                leaking = True
                if _inside or len(_clarifier_norm(_rest)) < _REPEAT_MIN_SENTENCE_CHARS:
                    continue
                released = True
                yield _rest
                continue
            if leaking:
                released = True
                yield opening
                continue
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
            if len(_clarifier_norm(novel)) < _REPEAT_MIN_SENTENCE_CHARS:
                # Still inside the recycled preamble, or holding a fragment too short to judge: keep
                # buffering. Releasing on a 3-character tail ("How") would let the rest of an
                # already-said sentence stream out behind it.
                continue
            released = True
            yield novel
            continue
        released = True
        yield opening
    if not released:
        # The stream ended inside the buffer: a short reply, still worth checking.
        opening = "".join(head)
        if opening and leak:
            _rest, _ = _strip_prompt_leak(opening, leak)
            if len(_clarifier_norm(_rest)) >= _REPEAT_MIN_SENTENCE_CHARS:
                opening = _rest
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
                    if len(_clarifier_norm(novel)) >= _REPEAT_MIN_SENTENCE_CHARS:
                        yield novel
                    else:
                        yield opening
                    return
            yield opening


# Anti-repeat: salvage instead of serving the duplicate. On the last attempt the retry repeated
# the same two sentences and then answered, so serving or discarding the reply were both wrong.
# Drop the leading sentences that restate a recent reply and serve the novel rest. Only if
# nothing novel survives does the honest-duplicate rule apply.
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
    cand = _clarifier_norm(sentence)
    if len(cand) < _REPEAT_MIN_SENTENCE_CHARS:
        return False
    for prior in prior_sentences:
        pn = _clarifier_norm(prior)
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
    priors = [p for p in priors if len(_clarifier_norm(p)) >= _REPEAT_MIN_REDACT_CHARS]
    if not body or not priors:
        return body
    kept = []
    for line in body.splitlines():
        norm = _clarifier_norm(line)
        if len(norm) >= _REPEAT_MIN_REDACT_CHARS and any(
                _repeat_ratio(line, p) >= _REPEAT_RATIO
                or _clarifier_norm(p) in norm
                for p in priors):
            continue
        kept.append(line)
    return "\n".join(kept)



"""Notes, timers, file analysis, screen reading, dictation and pointer input.

The capture-and-observe family: anything that records something, reads the
screen, or drives the mouse/keyboard.

Extracted verbatim from ``router_enhanced.route`` (v3 modularisation). This is an
ordered stage: it returns a route dict when one of its guards matches, or None to
let the next stage try. Order within the file is the legacy order and is load
bearing — an earlier guard deliberately shadows later ones.
"""
from __future__ import annotations

import os
import re

from eli.execution.routing.intents._sentinel import ROUTE_NONE
from eli.execution.routing._helpers import (
    _extract_path_from_text,
    _is_likely_path,
    _mk,
)



# Asking WHETHER ELI can see, rather than asking it to look. Subject-first
# ("you can see ...") and explicit confirmation openers; the modal-first request
# form ("can you see ...") is deliberately NOT matched so it still triggers a glance.
_SCREEN_CAPABILITY_QUESTION_RX = re.compile(
    r"\b(?:just\s+(?:confirming|checking|making\s+sure)|"
    r"(?<!can\s)you\s+can\s+(?:actually\s+|really\s+)?(?:see|read|view)\b|"
    r"(?:are|so)\s+you\s+saying|"
    r"you\s+(?:are|can)\s+able\s+to\s+see)\b",
    re.I,
)

def route_capture_input(text: str, raw: str, low: str, text_l: str):
    # mkdir X (bare)
    m = re.match(r"^mkdir\s+(.+)$", raw, re.I)
    if m:
        folder_name = m.group(1).strip()
        # os already imported at module level
        full_path = os.path.expanduser(
            f"~/{folder_name}") if not folder_name.startswith(
            ("/", "~")) else os.path.expanduser(folder_name)
        return _mk("CREATE_FOLDER", {"name": full_path}, 0.95, matched_by="fs.create_folder",
                   entities={"name": full_path})

    # note: X (colon format)
    m = re.match(r"^note:\s*(.+)$", raw, re.I)
    if m:
        return _mk("WRITE_NOTE", {"text": m.group(
            1).strip()}, 0.95, matched_by="notes.write")

    # Set a timer (bare, no duration) → prompt for duration
    if re.search(r"\bset\s+(?:a\s+)?timer\b",
                 low) and not re.search(r"\d", raw):
        return _mk("SET_TIMER", {"duration": None}, 0.7,
                   matched_by="timer.set_duration_loose")

    # Analyze X.csv / X.pdf (bare, without "summarize")
    m = re.match(r"^(?:analyze|analyse)\s+(.+\.csv)\s*$", raw, re.I)
    if m:
        # os already imported at module level
        return _mk("ANALYZE_CSV", {"path": os.path.abspath(os.path.expanduser(m.group(1).strip()))},
                   0.95, matched_by="analyze.csv")

    m = re.match(r"^(?:analyze|analyse)\s+(.+\.pdf)\s*$", raw, re.I)
    if m:
        # os already imported at module level
        return _mk("ANALYZE_PDF", {"path": os.path.abspath(os.path.expanduser(m.group(1).strip())),
                                   "instruction": raw},
                   0.95, matched_by="analyze.pdf")

    # Generate project with description
    if re.search(
            r"\bgenerate\s+(?:a\s+)?(?:new\s+)?(?:python\s+)?project\b", low):
        return _mk("GENERATE_PROJECT", {"description": raw, "use_gguf_only": True, "forbid_ollama": True},
                   0.95, matched_by="dev.generate_project")

    # Hardware: broader triggers
    if re.search(
            r"\b(?:hardware|gpu|cpu|ram|vram)\s+(?:info|specs?|status|check|details?)\b", low):
        return _mk("HARDWARE_PROFILE", {}, 0.90, matched_by="hardware.profile",
                   need_grounding=True, task_family="grounded_audit")

    if re.search(r"\boptimize\s+(?:my\s+)?hardware\b",
                 low) or re.search(r"\b(?:profile|scan)\s+hardware\b", low):
        return _mk("HARDWARE_PROFILE", {}, 0.90, matched_by="hardware.optimize",
                   need_grounding=True, task_family="grounded_audit")

    # Weather: umbrella / rain queries
    if re.search(r"\b(?:umbrella|rain(?:ing)?|snow(?:ing)?|storm)\b", low):
        location_match = re.search(
            r'\b(?:in|at|for)\b\s+([\w\s\-]+?)(?:\s*[,?!.]|\s+and\s+|\s+(?:provide|tell|show|give|also|then|next|tomorrow|today|forecast)|$)',
            raw,
            re.I)
        location = location_match.group(1).strip().rstrip("?.!, ") if location_match else None
        return _mk("GET_WEATHER", {"location": location, "_raw_user_text": raw}, 0.90, matched_by="info.weather",
                   entities={"location": location} if location else None)

    # "what is the time" / "what's the time" (expanded TIME patterns)
    if re.search(r"\bwhat\s+is\s+the\s+time\b",
                 low) or re.search(r"\bwhat's\s+the\s+time\b", low):
        return _mk("TIME", {}, 1.0, matched_by="system.time")
    # --- WRITE_NOTE with colon ---
    if re.match(r"^write note:", low):
        return _mk("WRITE_NOTE", {"text": re.sub(
            r"^write note:\s*", "", raw, flags=re.I).strip()}, 0.97, matched_by="notes.write_colon")
    # --- SCREENSHOT: final fallback catch ---
    if re.search(
            r"\b(take|capture)\s+(a\s+)?(screenshot|screen\s*shot|screen\s*capture|screen)\b", raw, re.I):
        region = "area" if re.search(
            r"\b(area|region|selection|part)\b", raw, re.I) else "full"
        return _mk("SCREENSHOT", {"region": region}, 0.98,
                   matched_by="io.screenshot", entities={"region": region})

    # Bare "screenshot" without "take a"
    if low.strip() == "screenshot":
        return _mk("SCREENSHOT", {}, 0.95, matched_by="io.screenshot_bare")

    # "write a report/guide/essay/document about X"
    _doc_m = re.match(
        r"^(?:write|create|generate|draft|produce)\s+(?:a\s+|an\s+)?"
        r"(report|guide|tutorial|essay|article|blog\s*post|document|manual|handbook|specification|proposal|plan|summary|brief)\s+"
        r"(?:about|on|for|regarding)\s+(.+)$", raw, re.I
    )
    if _doc_m:
        doc_type = _doc_m.group(1).strip()
        topic = _doc_m.group(2).strip()
        return _mk("GENERATE_DOCUMENT", {"topic": topic, "doc_type": doc_type, "format": "md"},
                   0.95, matched_by="doc.generate_typed", entities={"type": doc_type, "topic": topic})

    # ------------------------------------------------------------
    # OCR — extract text from image file
    # ------------------------------------------------------------
    m = re.match(
        r"^(?:ocr|read\s+(?:text\s+(?:from|in|on)|image)|extract\s+text\s+(?:from|in))\s+(.+)$",
        raw, re.I)
    if m:
        path = m.group(1).strip()
        return _mk("OCR_IMAGE", {"path": os.path.abspath(os.path.expanduser(path))},
                   0.95, matched_by="ocr.extract", entities={"path": path})

    # AMBIENT_VISION — continuous "watch my screen" toggle (distinct from a
    # one-shot "what's on my screen"). Check OFF before ON.
    if re.search(r"\b(?:stop|quit|disable|turn\s+off|cease)\b.{0,30}\b(?:watch|watching|"
                 r"look(?:ing)?\s+at|ambient\s+vision|keep\s+an\s+eye)\b", raw, re.I) or \
       re.search(r"\b(?:turn\s+off|disable)\s+ambient\s+vision\b", raw, re.I) or \
       re.search(r"\bstop\s+watching\b", raw, re.I):
        return _mk("AMBIENT_VISION", {"enabled": False, "text": raw}, 0.95,
                   matched_by="vision.ambient_off")
    if re.search(r"\b(?:watch|keep\s+watching|keep\s+an\s+eye\s+on|monitor)\s+(?:my|the)\s+screen\b",
                 raw, re.I) or \
       re.search(r"\b(?:enable|turn\s+on|start|activate)\s+ambient\s+vision\b", raw, re.I) or \
       re.search(r"\b(?:keep\s+)?(?:watching|looking\s+at)\s+what\s+i(?:'m|\s+am)\s+doing\b", raw, re.I):
        return _mk("AMBIENT_VISION", {"enabled": True, "text": raw}, 0.95,
                   matched_by="vision.ambient_on")

    # SCREEN_READ_ANALYZE — screenshot then vision (VL model) + OCR
    if re.search(
            r"\b(?:read|analyse?|analyze|describe|interpret|look\s+at|what(?:'s|\s+is)\s+on)\s+"
            r"(?:the|my)\s+(?:screen|display|monitor|desktop)\b", raw, re.I):
        _depth = "deep" if re.search(r"\b(in\s+depth|deep(?:ly)?|full(?:y)?|exact(?:ly)?|comprehensive)\b", raw, re.I) else ""
        return _mk("SCREEN_READ_ANALYZE", {"query": raw, **({"depth": _depth} if _depth else {})},
                   0.95, matched_by="screen.read_analyze")

    if re.search(
            r"\b(?:analyse?|analyze)\s+(?:my|the)\s+screen\s+(?:in\s+depth|deep(?:ly)?|fully|completely)\b",
            raw, re.I):
        return _mk("SCREEN_READ_ANALYZE", {"query": raw, "depth": "deep"},
                   0.96, matched_by="screen.read_deep")

    if re.search(r"\bexactly\s+what(?:'s|\s+is)\s+on\s+(?:my|the)\s+(?:screen|display)\b", raw, re.I):
        return _mk("SCREEN_READ_ANALYZE", {"query": raw, "depth": "deep"},
                   0.96, matched_by="screen.read_exact")

    if re.search(
            r"\b(?:what(?:'s|\s+is)\s+on\s+(?:my|the)\s+screen).*\b(research|relate|remember|previous\s+session)\b",
            raw, re.I) or re.search(
            r"\b(?:remember|recall).*\b(?:screen|seeing\s+this|saw\s+this)\b", raw, re.I):
        return _mk("SCREEN_READ_ANALYZE", {"query": raw, "depth": "deep"},
                   0.94, matched_by="screen.read_memory_research")

    if re.search(r"\bscreen\s*(?:read|analyse?|analyze|ocr)\b", raw, re.I):
        return _mk("SCREEN_READ_ANALYZE", {}, 0.93, matched_by="screen.read_ocr")

    # A question about the capability isn't a request to use it. "So, you can see my screen?" and
    # "just checking you can really see what i see" each fired another SCREEN_READ_ANALYZE
    # (~20s, new screenshot) and answered neither. The split is syntactic: a request puts the modal
    # first ("can you see my screen?"), a confirmation puts the subject first. Falling through lets
    # CHAT answer the yes/no.
    if _SCREEN_CAPABILITY_QUESTION_RX.search(raw):
        return ROUTE_NONE

    # Conversational vision: "what do you see", "can you see this/my screen",
    # "look at my screen", "what am I looking at", "see what I see".
    if re.search(
            r"\b(?:what\s+do\s+you\s+see|what\s+can\s+you\s+see|can\s+you\s+see\s+"
            r"(?:this|that|my\s+screen|what|the\s+screen)|see\s+what\s+i(?:'m|\s+am)?\s*"
            r"(?:see|seeing|looking\s+at)|what\s+am\s+i\s+looking\s+at|look\s+at\s+"
            r"(?:this|what\s+i'?m\s+doing)|see\s+my\s+screen)\b", raw, re.I):
        return _mk("SCREEN_READ_ANALYZE", {}, 0.9, matched_by="screen.vision_conversational")

    # Content questions about what's on screen ("what season is on the screen?", "which app is on
    # my display?") are visual: take a real glance and answer from pixels. Otherwise they fall to CHAT
    # and the model guesses (it invented a TV season). The vision model gets a hard "answer only
    # from what's visible; if you can't tell, say so".
    if re.search(r"\bon\s+(?:the\s+|my\s+)?(?:screen|display|monitor)\b", raw, re.I) and \
       re.search(r"\b(?:what|which|who|where|when|is|are|how\s+many|name|"
                 r"see|show|can\s+you|do\s+you|tell\s+me)\b", raw, re.I):
        _vq = raw.strip()
        _vp = (f'You are ELI looking at the user\'s screen right now. Answer this '
               f'question using ONLY what is actually visible on screen: "{_vq}". '
               f'Be specific about what you can see. If the answer is not visible, '
               f'say you cannot tell from the screen — never guess or invent.')
        return _mk("SCREEN_READ_ANALYZE", {"prompt": _vp}, 0.87,
                   matched_by="screen.content_question")

    # ------------------------------------------------------------
    # CONVERT_DOCUMENT — convert between document formats
    # ------------------------------------------------------------
    m = re.search(
        r"convert\s+(.+?)\s+to\s+(pdf|docx?|html?|markdown|md|txt|tex|latex|lualatex|odt|rtf|epub)",
        raw, re.I)
    if m:
        src = m.group(1).strip()
        fmt = m.group(2).strip().lower()
        src_path = os.path.abspath(os.path.expanduser(src)) if _is_likely_path(src) or "." in src else src
        return _mk("CONVERT_DOCUMENT", {"source": src_path, "format": fmt},
                   0.95, matched_by="doc.convert", entities={"source": src_path, "format": fmt})

    m = re.search(
        r"export\s+(?:as|to)\s+(pdf|docx?|html?|markdown|md|txt|tex|latex|lualatex|odt|rtf|epub)",
        raw, re.I)
    if m:
        fmt = m.group(1).strip().lower()
        path_m = re.search(r'(?:from|file|document)\s+([\w./~\- ]+\.\w+)', raw, re.I)
        src_path = os.path.abspath(os.path.expanduser(path_m.group(1).strip())) if path_m else ""
        return _mk("CONVERT_DOCUMENT", {"source": src_path, "format": fmt},
                   0.90, matched_by="doc.export", entities={"source": src_path, "format": fmt})

    if re.search(r'\blualatex\b', raw, re.I):
        src_path = _extract_path_from_text(raw) or ""
        return _mk("CONVERT_DOCUMENT", {"source": src_path, "format": "lualatex"},
                   0.90, matched_by="doc.lualatex")

    # ------------------------------------------------------------
    # DICTATE / TRANSCRIBE — voice dictation and audio transcription
    # ------------------------------------------------------------
    if re.search(r"\b(?:start|begin|enable)\s+dictati(?:on|ng)\b", raw, re.I):
        return _mk("DICTATE", {"action": "start"}, 0.97, matched_by="voice.dictate_start")

    if re.search(r"\b(?:stop|end|disable)\s+dictati(?:on|ng)\b", raw, re.I):
        return _mk("DICTATE", {"action": "stop"}, 0.97, matched_by="voice.dictate_stop")

    if re.match(r"^dictate\b", raw, re.I):
        return _mk("DICTATE", {"action": "start"}, 0.95, matched_by="voice.dictate_bare")

    m = re.search(
        r"\btranscri(?:be|pt)\s+(.+)$", raw, re.I)
    if m:
        src = m.group(1).strip()
        src_path = os.path.abspath(os.path.expanduser(src)) if _is_likely_path(src) or "." in src else src
        return _mk("TRANSCRIBE", {"source": src_path},
                   0.95, matched_by="voice.transcribe", entities={"source": src_path})

    if re.search(r"\btranscri(?:be|pt)\b", raw, re.I):
        return _mk("TRANSCRIBE", {}, 0.90, matched_by="voice.transcribe_bare")

    # ------------------------------------------------------------
    # MOUSE_CONTROL — move, click, scroll
    # ------------------------------------------------------------
    m = re.search(
        r"\b(?:mouse\s+)?(?:click|left.click|right.click|double.click)\s+"
        r"(?:at\s+|on\s+)?(?:(\d+)\s*[,x]\s*(\d+))?", raw, re.I)
    if m and m.group(1):
        x, y = int(m.group(1)), int(m.group(2))
        btn = "right" if "right" in raw.lower() else "left"
        dbl = "double" in raw.lower()
        return _mk("MOUSE_CONTROL", {"action": "click", "x": x, "y": y, "button": btn, "double": dbl},
                   0.95, matched_by="ui.mouse_click", entities={"x": x, "y": y})

    if re.search(r"\bmove\s+(?:the\s+)?(?:mouse|cursor)\s+to\s+(\d+)\s*[,x]\s*(\d+)", raw, re.I):
        mm = re.search(r"(\d+)\s*[,x]\s*(\d+)", raw)
        x, y = int(mm.group(1)), int(mm.group(2))
        return _mk("MOUSE_CONTROL", {"action": "move", "x": x, "y": y},
                   0.95, matched_by="ui.mouse_move", entities={"x": x, "y": y})

    m = re.search(
        r"\b(?:move|nudge|shift)\s+(?:the\s+)?(?:mouse|cursor|pointer)\s+"
        r"(left|right|up|down)(?:\s+(?:by\s+)?(\d+)\s*(?:pixels?|px)?)?\b",
        raw, re.I,
    )
    if m:
        direction = m.group(1).lower()
        amount = int(m.group(2) or 100)
        return _mk(
            "MOUSE_CONTROL",
            {"action": "move", "direction": direction, "amount": amount, "pixels": amount},
            0.94,
            matched_by="ui.mouse_move_relative",
            entities={"direction": direction, "amount": amount},
        )

    if re.search(r"\b(?:mouse|cursor|pointer)\b", low):
        m = re.search(
            r"\b(?:move|nudge|shift)\s+(left|right|up|down)"
            r"(?:\s+(?:by\s+)?(\d+)\s*(?:pixels?|px)?)?\b",
            raw, re.I,
        )
        if m:
            direction = m.group(1).lower()
            amount = int(m.group(2) or 100)
            return _mk(
                "MOUSE_CONTROL",
                {"action": "move", "direction": direction, "amount": amount, "pixels": amount},
                0.91,
                matched_by="ui.mouse_move_relative_implicit",
                entities={"direction": direction, "amount": amount},
            )

    if re.search(
        r"\b(?:click|left\s+click|right\s+click|double\s+click)\s+(?:here|at\s+(?:the\s+)?cursor)\b",
        low,
    ):
        btn = "right" if "right" in low else "left"
        dbl = "double" in low
        return _mk(
            "MOUSE_CONTROL",
            {"action": "click", "button": btn, "double": dbl},
            0.9,
            matched_by="ui.mouse_click_at_cursor",
        )

    if re.search(r"\bscroll\s+(up|down)(?:\s+(\d+))?\b", raw, re.I):
        sm = re.search(r"\bscroll\s+(up|down)(?:\s+(\d+))?\b", raw, re.I)
        direction = sm.group(1).lower()
        amount = int(sm.group(2) or 3)
        return _mk("MOUSE_CONTROL", {"action": "scroll", "direction": direction, "amount": amount},
                   0.93, matched_by="ui.mouse_scroll")
    return None

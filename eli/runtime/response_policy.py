from __future__ import annotations

from typing import Any, Dict

COGNITIVE_DEFAULT = "cognitive_response"
GROUNDED_REPORT = "grounded_report"
PLAN_AND_EXECUTE = "plan_and_execute"
RAW_MEMORY_DUMP = "raw_memory_dump"

GROUNDED_ACTIONS = {
    "RUNTIME_AUDIT",
    "RESOLVE_RUNTIME_PATHS",
    "IMPORT_SURFACE_AUDIT",
    "LAST_TRACE_REPORT",
    "REASONING_MODE_REPORT",
    "MEMORY_STATUS",
    "FIRST_INTERACTION_REPORT",
    "IDENTITY_MEMORY_REPORT",
    "USER_MEMORY_DUMP",
    "COGNITION_STATUS",
    "GUI_RUNTIME_AUDIT",
    "FRONTIER_STATUS",
    "ELI_IDENTITY_AUDIT",
}

EXECUTION_ACTION_HINTS = {
    "OPEN_APP",
    "OPEN_URL",
    "RUN_COMMAND",
    "SHELL",
    "FILE_WRITE",
    "FILE_READ",
    "SEARCH_WEB",
    "WEB_SEARCH",
    "WEB_FETCH",
}


def classify_response_mode(action: str, args: Dict[str, Any] | None = None, meta: Dict[str, Any] | None = None) -> str:
    action = str(action or "").strip().upper()
    args = dict(args or {})
    meta = dict(meta or {})
    if action in GROUNDED_ACTIONS:
        return GROUNDED_REPORT
    if action in EXECUTION_ACTION_HINTS:
        return PLAN_AND_EXECUTE
    if action == "MEMORY_RECALL":
        msg = str(args.get("query") or args.get("message") or "").lower()
        if any(p in msg for p in (
            "give me everything", "don't summarise", "do not summarise", "raw memory", "full dump", "verbatim",
        )):
            return RAW_MEMORY_DUMP
    return COGNITIVE_DEFAULT


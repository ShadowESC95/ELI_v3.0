"""MCP effector — MCP_STATUS, MCP_TOOLS, MCP_CALL.

MCP tools are discovered at runtime, so they deliberately do *not* each become an
entry in SUPPORTED_ACTIONS: the action surface would then differ between machines and
the capability manifest could never be checked against source. Instead one action,
MCP_CALL, dispatches by tool name, and MCP_TOOLS is how the model (and the user) sees
what is currently available.
"""
from __future__ import annotations

from typing import Any, Dict

from eli.utils.log import get_logger

log = get_logger(__name__)


def _client():
    from eli.integrations.mcp import client
    return client


def _h_mcp_status(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        st = _client().status()
    except Exception as e:
        msg = f"Couldn't read the MCP configuration: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}

    if not st["configured"]:
        msg = ("No MCP servers are configured yet, so I have no external tools beyond my own. "
               f"Add them to {st['config_path']} and I'll pick them up.")
        return {"ok": True, "action": a, "content": msg, "response": msg, **st}

    lines = [f"{st['configured']} MCP server(s) configured:"]
    for s in st["servers"]:
        if s["connected"]:
            tools = ", ".join(s["tools"]) or "no tools"
            lines.append(f"  {s['name']} — connected ({tools})")
        elif s["error"]:
            lines.append(f"  {s['name']} — not working: {s['error']}")
        else:
            lines.append(f"  {s['name']} — configured, not started yet")
    body = "\n".join(lines)
    return {"ok": True, "action": a, "content": body, "response": body, **st}


def _h_mcp_tools(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    try:
        tools = _client().list_tools(refresh=bool(args.get("refresh")))
    except Exception as e:
        msg = f"Couldn't list MCP tools: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}

    if not tools:
        msg = ("I can't see any MCP tools right now — either nothing is configured, "
               "or the configured servers didn't start. Ask me for MCP status for the reason.")
        return {"ok": True, "action": a, "tools": [], "content": msg, "response": msg}

    lines = [f"{len(tools)} MCP tool(s) available:"]
    for t in tools:
        desc = (t["description"] or "").strip().splitlines()[0][:110] if t["description"] else ""
        lines.append(f"  {t['qualified']}" + (f" — {desc}" if desc else ""))
    body = "\n".join(lines)
    return {"ok": True, "action": a, "tools": tools, "content": body, "response": body}


def _h_mcp_call(a: str, args: Dict[str, Any]) -> Dict[str, Any]:
    tool = str(args.get("tool") or args.get("name") or args.get("qualified") or "").strip()
    if not tool:
        msg = "Tell me which MCP tool to call — ask for MCP tools to see what's available."
        return {"ok": False, "action": a, "error": "missing tool", "content": msg, "response": msg}

    arguments = args.get("arguments") or args.get("args") or {}
    if not isinstance(arguments, dict):
        arguments = {"input": arguments}

    try:
        res = _client().call_tool(tool, arguments)
    except Exception as e:
        msg = f"MCP call failed: {e}"
        return {"ok": False, "action": a, "error": str(e), "content": msg, "response": msg}

    if not res.get("ok"):
        reason = str(res.get("error") or "the tool call failed")
        hint = res.get("hint")
        msg = f"{tool} didn't run: {reason}" + (f" ({hint})" if hint else "")
        return {"ok": False, "action": a, "error": reason, "content": msg, "response": msg}

    body = str(res.get("text") or "").strip() or f"{tool} ran and returned nothing to show."
    return {"ok": True, "action": a, "tool": tool, "content": body, "response": body}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "MCP_STATUS": _h_mcp_status,
    "MCP_TOOLS": _h_mcp_tools,
    "MCP_CALL": _h_mcp_call,
}.items():
    _register(_action, _fn)

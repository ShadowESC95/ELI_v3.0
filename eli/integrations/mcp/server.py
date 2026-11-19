"""MCP server — ELI's own capabilities exposed to any MCP client.

Run it as::

    python -m eli.integrations.mcp.server

and point an MCP client at that command. This is the half that makes ELI a platform:
its action surface becomes callable by anything speaking the protocol, without that
client needing to know anything about ELI's router, effectors or memory.

Two deliberate restrictions:

* **Actions are exposed, not the engine.** A caller gets the effector surface, which
  is already gated, rather than a free-text prompt into the assistant.
* **Only what reads ELI's state and the machine's is open by default.** The clock, the
  machine's load and ELI's own status (``_OPEN_ACTIONS``) — nothing that changes anything,
  and nothing that reads the user's files, screen, microphone, clipboard, memories or
  conversations. Every other action, the composite ones that run others (SEQUENCE,
  MULTI_COMMAND) included, is withheld unless ``ELI_MCP_ALLOW_CONTROL=1`` or the saved
  setting allows it. A list of what to withhold missed whatever its prefixes did not name
  (READ_FILE, PLUGIN_INSTALL, MEMORY_FORGET, and SEQUENCE running any of the rest).
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List

from eli.execution.effectors.registry import Declared
from eli.utils.log import get_logger

log = get_logger(__name__)

_PROTOCOL_VERSION = "2024-11-05"

# Offered to any MCP client by default. Each one only reads, whatever arguments it is given,
# and reads nothing of the user's own: the clock, the machine's load, and ELI's status. An
# action is declared open (open=True, eli/execution/effectors/registry.py) only after its handler
# has been read for that; anything not declared so needs the control opt-in.
_OPEN_ACTIONS = Declared("open")


def _allow_control() -> bool:
    """Env first, then the saved setting.

    The env var stays authoritative so a one-off launch can differ from the default,
    and an explicit `ELI_MCP_ALLOW_CONTROL=0` still means no. Without it the saved
    setting decides, because this server usually runs as its own process launched by
    someone else's MCP client — a GUI toggle that only set an env var in ELI's own
    interpreter would have had no effect on the process that actually serves.
    """
    env = os.environ.get("ELI_MCP_ALLOW_CONTROL")
    if env is not None and env != "":
        return env.strip().lower() in {"1", "true", "yes", "on"}
    try:
        from eli.core.runtime_settings import load_settings
        return bool((load_settings() or {}).get("mcp_expose_control", False))
    except Exception:
        log.debug("mcp.server: could not read mcp_expose_control", exc_info=True)
        return False


def _exposed_actions() -> List[str]:
    try:
        from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
    except Exception:
        log.debug("mcp.server: action surface unavailable", exc_info=True)
        return []
    actions = sorted(set(SUPPORTED_ACTIONS))
    if _allow_control():
        return actions
    return [a for a in actions if a in _OPEN_ACTIONS]


def _describe(action: str) -> Dict[str, Any]:
    # What the catalogue says the action does. This looked for a capabilities_doc.ENTRIES that never
    # existed, so every tool a client saw was described only as "ELI action <name>".
    description = f"ELI action {action}"
    try:
        from eli.execution.effectors.registry import facets_of
        about = facets_of(action).get("about")
        if about and about[1]:
            description = str(about[1])
    except Exception:
        log.debug("mcp.server: catalogue lookup failed", exc_info=True)
    return {
        "name": action,
        "description": description,
        # Args differ per action and are validated downstream by the effector, so the
        # schema stays open rather than asserting a shape that is not enforced here.
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    }


def _call(action: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    if action not in _exposed_actions():
        hint = ("" if _allow_control() else
                " (only ELI's read-only status actions are offered by default — turn the rest "
                "on under Extensions ▸ Settings, or set ELI_MCP_ALLOW_CONTROL=1)")
        return {"content": [{"type": "text", "text": f"'{action}' is not exposed{hint}"}],
                "isError": True}
    try:
        from eli.execution.executor_enhanced import execute
        result = execute(action, arguments or {}) or {}
    except Exception as e:
        log.debug("mcp.server: %s failed", action, exc_info=True)
        return {"content": [{"type": "text", "text": f"{action} failed: {e}"}], "isError": True}

    text = str(result.get("content") or result.get("response") or "").strip()
    if not text:
        text = json.dumps(result, ensure_ascii=False, default=str)[:4000]
    return {"content": [{"type": "text", "text": text}],
            "isError": not bool(result.get("ok", True))}


def handle(message: Dict[str, Any]) -> Dict[str, Any] | None:
    """Handle one JSON-RPC message. Returns None for notifications."""
    method = str(message.get("method") or "")
    msg_id = message.get("id")
    params = message.get("params") or {}

    if method == "initialize":
        from eli.runtime.version_record import current_version
        result = {
            "protocolVersion": _PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ELI", "version": current_version() or "0"},
        }
    elif method == "tools/list":
        result = {"tools": [_describe(a) for a in _exposed_actions()]}
    elif method == "tools/call":
        result = _call(str(params.get("name") or ""), params.get("arguments") or {})
    elif method.startswith("notifications/"):
        return None
    elif method == "ping":
        result = {}
    else:
        if msg_id is None:
            return None
        return {"jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": f"unknown method '{method}'"}}

    if msg_id is None:
        return None
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def serve(stdin=None, stdout=None) -> int:
    """Read JSON-RPC messages from stdin, write replies to stdout, until EOF."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except Exception:
            continue  # not our traffic; the transport is line-delimited JSON
        try:
            reply = handle(message)
        except Exception as e:
            log.debug("mcp.server: handler failed", exc_info=True)
            reply = {"jsonrpc": "2.0", "id": message.get("id"),
                     "error": {"code": -32603, "message": str(e)}}
        if reply is not None:
            stdout.write(json.dumps(reply, ensure_ascii=False, default=str) + "\n")
            stdout.flush()
    return 0


def connection_config(allow_control: bool = False) -> Dict[str, Any]:
    """The ``mcpServers`` entry another app needs to launch ELI as an MCP server.

    A frozen build (AppImage, portable, Windows/macOS bundle) is launched through its
    own executable with ``--mcp-server``; a source install through ``python -m``.
    """
    if getattr(sys, "frozen", False) or os.environ.get("APPIMAGE"):
        command = os.environ.get("APPIMAGE") or sys.executable
        args = ["--mcp-server"]
    else:
        command = sys.executable
        args = ["-m", "eli.integrations.mcp.server"]
    entry: Dict[str, Any] = {"command": command, "args": args}
    if allow_control:
        entry["env"] = {"ELI_MCP_ALLOW_CONTROL": "1"}
    return {"mcpServers": {"eli": entry}}


def main() -> int:
    """Process entry point. Stdout carries only protocol traffic.

    Importing the executor prints status banners, and native libraries write to the
    file descriptor directly, so the protocol gets a private duplicate of stdout and
    fd 1 itself is pointed at stderr for the life of the process.
    """
    try:
        protocol_out = os.fdopen(os.dup(1), "w", buffering=1, encoding="utf-8")
        os.dup2(2, 1)
    except OSError:
        protocol_out = sys.stdout
    sys.stdout = sys.stderr
    # Behind the licence gate like every other way into ELI (the `eli-mcp` script comes here
    # directly). Never a prompt: the protocol owns stdin, and why is written to stderr.
    from eli.licensing.launch_gate import launch_allowed
    if not launch_allowed(allow_gui_prompt=False):
        return 1
    return serve(stdout=protocol_out)


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())

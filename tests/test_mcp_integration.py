"""MCP integration — ELI as both a host that gains tools and a server that offers them.

The end-to-end test at the bottom is the one that matters: it launches ELI's own MCP
server as a real subprocess and drives it with ELI's own MCP client, so the handshake,
framing and dispatch are exercised as a running system rather than asserted in isolation.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap

import pytest

from eli.integrations.mcp import client as mcp_client


# --------------------------------------------------------------------------
# Client: config and failure reporting
# --------------------------------------------------------------------------

def test_no_config_means_no_servers(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_client, "config_path", lambda: tmp_path / "absent.json")
    assert mcp_client.load_config() == {}
    assert mcp_client.status()["configured"] == 0


def test_malformed_config_does_not_raise(monkeypatch, tmp_path):
    bad = tmp_path / "mcp_servers.json"
    bad.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(mcp_client, "config_path", lambda: bad)
    assert mcp_client.load_config() == {}


def test_entries_without_a_command_are_ignored(monkeypatch, tmp_path):
    cfg = tmp_path / "mcp_servers.json"
    cfg.write_text(json.dumps({"mcpServers": {
        "good": {"command": "echo"}, "bad": {"args": ["x"]}}}), encoding="utf-8")
    monkeypatch.setattr(mcp_client, "config_path", lambda: cfg)
    assert set(mcp_client.load_config()) == {"good"}


def test_missing_binary_reports_the_real_reason(monkeypatch, tmp_path):
    cfg = tmp_path / "mcp_servers.json"
    cfg.write_text(json.dumps({"mcpServers": {
        "ghost": {"command": "definitely-not-installed-xyz", "enabled": True}}}), encoding="utf-8")
    monkeypatch.setattr(mcp_client, "config_path", lambda: cfg)
    mcp_client.shutdown_all()
    res = mcp_client.call_tool("ghost.anything", {})
    assert res["ok"] is False
    assert "not installed" in res["error"]
    mcp_client.shutdown_all()


def test_servers_are_stopped_when_eli_exits():
    """Nothing called shutdown_all, so every server ELI started outlived it."""
    code = (
        "import atexit\n"
        "kept = []\n"
        "atexit.register = lambda fn, *a, **k: (kept.append(fn), fn)[1]\n"
        "from eli.integrations.mcp import client\n"
        "raise SystemExit(0 if client.shutdown_all in kept else 3)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=_repo_root(), capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-400:]


def test_calling_with_nothing_configured_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_client, "config_path", lambda: tmp_path / "absent.json")
    res = mcp_client.call_tool("whatever.tool", {})
    assert res["ok"] is False and "no MCP servers are configured" in res["error"]


# --------------------------------------------------------------------------
# Effector surface
# --------------------------------------------------------------------------

def test_effector_actions_are_registered_and_reachable():
    from eli.execution.effectors.registry import registered_actions
    import eli.execution.executor_enhanced as ex
    for a in ("MCP_STATUS", "MCP_TOOLS", "MCP_CALL"):
        assert a in registered_actions()
        assert a in ex.SUPPORTED_ACTIONS


def test_mcp_call_without_a_tool_asks_rather_than_guessing():
    import eli.execution.executor_enhanced as ex
    out = ex.execute("MCP_CALL", {})
    assert out["ok"] is False and "which MCP tool" in out["content"]


def test_router_reaches_the_mcp_actions():
    from eli.execution.router_enhanced import route
    assert route("what mcp servers do i have")["action"] == "MCP_STATUS"
    assert route("list my mcp tools")["action"] == "MCP_TOOLS"
    r = route("run mcp tool files.read_file")
    assert r["action"] == "MCP_CALL" and r["args"]["tool"] == "files.read_file"


def test_mcp_routes_do_not_steal_ordinary_traffic():
    """The guard is that the new actions stay in their lane. The exact destination of
    ordinary traffic is covered by the router's own suites and depends on process
    state (pending confirmations, follow-up context), so pinning it here would only
    make this test order-dependent."""
    from eli.execution.router_enhanced import route
    mine = {"MCP_STATUS", "MCP_TOOLS", "MCP_CALL", "COMPUTER_USE"}
    for phrase in ("run firefox", "search for flights to berlin", "open my notes",
                   "what is the time", "play some music", "click the submit button on screen"):
        assert route(phrase)["action"] not in mine, f"{phrase!r} was hijacked"


# --------------------------------------------------------------------------
# End to end: ELI's client against ELI's server, over a real subprocess
# --------------------------------------------------------------------------

@pytest.mark.timeout(120)
def test_eli_client_talks_to_eli_server_over_stdio(monkeypatch, tmp_path):
    """The whole loop: spawn the real server, discover its tools, call one."""
    launcher = tmp_path / "run_server.py"
    launcher.write_text(textwrap.dedent(f"""
        import sys
        sys.path.insert(0, {str(_repo_root())!r})
        from eli.integrations.mcp.server import serve
        raise SystemExit(serve())
    """).strip(), encoding="utf-8")

    cfg = tmp_path / "mcp_servers.json"
    cfg.write_text(json.dumps({"mcpServers": {
        # The sandbox hides $HOME; the server needs the repo (code + venv) and the launcher.
        "eli": {"command": sys.executable, "args": [str(launcher)], "enabled": True,
                "read_paths": [str(_repo_root()), str(tmp_path)]}}}), encoding="utf-8")
    monkeypatch.setattr(mcp_client, "config_path", lambda: cfg)

    mcp_client.shutdown_all()
    try:
        tools = mcp_client.list_tools(refresh=True)
        assert tools, "the client should discover ELI's own action surface"
        names = {t["name"] for t in tools}
        assert "DATE" in names, f"expected DATE among {len(names)} tools"

        st = mcp_client.status()
        assert st["servers"][0]["connected"] is True

        res = mcp_client.call_tool("eli.DATE", {})
        assert res["ok"] is True
        assert res["text"], "DATE should return something real"

        missing = mcp_client.call_tool("eli.NO_SUCH_ACTION", {})
        assert missing["ok"] is False
    finally:
        mcp_client.shutdown_all()


def _repo_root() -> str:
    import eli
    return os.path.dirname(os.path.dirname(os.path.abspath(eli.__file__)))


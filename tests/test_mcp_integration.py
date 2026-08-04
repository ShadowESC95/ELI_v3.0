"""MCP integration — ELI as both a host that gains tools and a server that offers them.

The end-to-end test at the bottom is the one that matters: it launches ELI's own MCP
server as a real subprocess and drives it with ELI's own MCP client, so the handshake,
framing and dispatch are exercised as a running system rather than asserted in isolation.
"""
from __future__ import annotations

import io
import json
import os
import sys
import textwrap

import pytest

from eli.integrations.mcp import client as mcp_client
from eli.integrations.mcp import server as mcp_server


# --------------------------------------------------------------------------
# Server: protocol handling
# --------------------------------------------------------------------------

def test_initialize_reports_tool_capability():
    reply = mcp_server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": "2024-11-05"}})
    assert reply["id"] == 1
    assert "tools" in reply["result"]["capabilities"]
    assert reply["result"]["serverInfo"]["name"] == "ELI"


def test_notifications_get_no_reply():
    assert mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_is_a_proper_jsonrpc_error():
    reply = mcp_server.handle({"jsonrpc": "2.0", "id": 7, "method": "does/not/exist"})
    assert reply["error"]["code"] == -32601


def test_tools_list_exposes_real_actions(monkeypatch):
    monkeypatch.delenv("ELI_MCP_ALLOW_CONTROL", raising=False)
    reply = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in reply["result"]["tools"]}
    assert len(names) > 50, "the real action surface should be exposed"
    assert all("inputSchema" in t for t in reply["result"]["tools"])


def test_control_actions_are_withheld_by_default(monkeypatch):
    monkeypatch.delenv("ELI_MCP_ALLOW_CONTROL", raising=False)
    exposed = set(mcp_server._exposed_actions())
    assert "COMPUTER_USE" not in exposed, "must not hand out the mouse by default"
    assert not any(a.startswith(("SHELL", "SELF_")) for a in exposed)


def test_control_actions_can_be_opted_into(monkeypatch):
    monkeypatch.setenv("ELI_MCP_ALLOW_CONTROL", "1")
    assert "COMPUTER_USE" in set(mcp_server._exposed_actions())


def test_calling_a_withheld_action_is_refused_not_executed(monkeypatch):
    monkeypatch.delenv("ELI_MCP_ALLOW_CONTROL", raising=False)
    called = []
    import eli.execution.executor_enhanced as ex
    monkeypatch.setattr(ex, "execute", lambda *a, **k: called.append(a))
    reply = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": "COMPUTER_USE", "arguments": {"goal": "x"}}})
    assert reply["result"]["isError"] is True
    assert called == [], "a withheld action must never reach the executor"


def test_failing_action_is_reported_as_an_error_not_a_success(monkeypatch):
    import eli.execution.executor_enhanced as ex
    monkeypatch.setattr(ex, "execute",
                        lambda *a, **k: {"ok": False, "content": "disk was unreadable"})
    monkeypatch.setattr(mcp_server, "_exposed_actions", lambda: ["DATE"])
    reply = mcp_server.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                               "params": {"name": "DATE", "arguments": {}}})
    assert reply["result"]["isError"] is True
    assert "disk was unreadable" in reply["result"]["content"][0]["text"]


def test_serve_loop_ignores_junk_and_answers_json(monkeypatch):
    stdin = io.StringIO('not json at all\n'
                        '{"jsonrpc":"2.0","id":9,"method":"ping"}\n')
    stdout = io.StringIO()
    mcp_server.serve(stdin=stdin, stdout=stdout)
    replies = [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]
    assert len(replies) == 1 and replies[0]["id"] == 9


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
        "ghost": {"command": "definitely-not-installed-xyz"}}}), encoding="utf-8")
    monkeypatch.setattr(mcp_client, "config_path", lambda: cfg)
    mcp_client.shutdown_all()
    res = mcp_client.call_tool("ghost.anything", {})
    assert res["ok"] is False
    assert "not installed" in res["error"]
    mcp_client.shutdown_all()


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
        "eli": {"command": sys.executable, "args": [str(launcher)]}}}), encoding="utf-8")
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


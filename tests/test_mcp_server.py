"""ELI as an MCP server: protocol behaviour, control gating, launch config, clean stdout."""
import io
import json
import subprocess
import sys

import pytest

from eli.integrations.mcp import server as srv


@pytest.fixture(autouse=True)
def _no_control(monkeypatch):
    monkeypatch.delenv("ELI_MCP_ALLOW_CONTROL", raising=False)


def _rpc(method, params=None, id_=1):
    return srv.handle({"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}})


def test_initialize_reports_tool_capability():
    reply = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                               "params": {"protocolVersion": "2024-11-05"}})
    assert reply["id"] == 1
    assert "tools" in reply["result"]["capabilities"]
    assert reply["result"]["serverInfo"]["name"] == "ELI" and reply["result"]["protocolVersion"]


def test_notifications_get_no_reply():
    assert srv.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_unknown_method_is_a_proper_jsonrpc_error():
    reply = srv.handle({"jsonrpc": "2.0", "id": 7, "method": "does/not/exist"})
    assert reply["error"]["code"] == -32601


def test_tools_list_exposes_real_actions(monkeypatch):
    from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
    monkeypatch.delenv("ELI_MCP_ALLOW_CONTROL", raising=False)
    reply = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in reply["result"]["tools"]}
    # every open action is a real one (the list cannot rot into names the executor lacks)
    assert set(srv._OPEN_ACTIONS) <= set(SUPPORTED_ACTIONS)
    assert names == set(srv._OPEN_ACTIONS)
    assert all("inputSchema" in t for t in reply["result"]["tools"])


def test_control_actions_are_withheld_by_default(monkeypatch):
    from eli.execution.executor_enhanced import SUPPORTED_ACTIONS
    monkeypatch.delenv("ELI_MCP_ALLOW_CONTROL", raising=False)
    exposed = set(srv._exposed_actions())
    assert "COMPUTER_USE" not in exposed, "must not hand out the mouse by default"
    assert not any(a.startswith(("SHELL", "SELF_")) for a in exposed)
    # what a list of prefixes to withhold never named: it changes things, runs other actions,
    # or reads what is the user's own
    for action in ("PLUGIN_INSTALL", "PLUGIN_UNINSTALL", "FIX_FILE", "CONFIRM_CODE_FIX",
                   "MEMORY_FORGET", "CLEAR_CHAT_HISTORY", "PERSONA_LOCK_SET", "PERSONA_LOCK_CLEAR",
                   "MCP_CALL", "SEQUENCE", "MULTI_COMMAND", "READ_FILE", "LIST_DIR", "GET_CLIPBOARD",
                   "SCREENSHOT", "SCREEN_READ_ANALYZE", "LISTEN_FOR_COMMAND", "DICTATE",
                   "MEMORY_RECALL", "PERSONAL_MEMORY_SUMMARY", "OPEN_APP", "CLOSE_APP",
                   "CREATE_FILE", "CREATE_FOLDER", "LORA_TRAIN", "TRAIN_VOICE", "WAKE_TRAIN"):
        assert action in SUPPORTED_ACTIONS, action
        assert action not in exposed, action
    # and every action not on the open list is withheld, whatever it is named
    assert exposed == set(SUPPORTED_ACTIONS) & set(srv._OPEN_ACTIONS)


def test_a_sequence_cannot_carry_a_withheld_action_through(monkeypatch):
    monkeypatch.delenv("ELI_MCP_ALLOW_CONTROL", raising=False)
    called = []
    import eli.execution.executor_enhanced as ex
    monkeypatch.setattr(ex, "execute", lambda *a, **k: called.append(a) or {"ok": True})
    for name, arguments in (("SEQUENCE", {"steps": [{"action": "RUN_CMD", "args": {"cmd": "id"}}]}),
                            ("MULTI_COMMAND", {"text": "run id and then read my notes"})):
        reply = srv.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                                   "params": {"name": name, "arguments": arguments}})
        assert reply["result"]["isError"] is True
    assert called == [], "a composite action must never reach the executor without the opt-in"


def test_control_actions_can_be_opted_into(monkeypatch):
    monkeypatch.setenv("ELI_MCP_ALLOW_CONTROL", "1")
    assert "COMPUTER_USE" in set(srv._exposed_actions())


def test_calling_a_withheld_action_is_refused_not_executed(monkeypatch):
    monkeypatch.delenv("ELI_MCP_ALLOW_CONTROL", raising=False)
    called = []
    import eli.execution.executor_enhanced as ex
    monkeypatch.setattr(ex, "execute", lambda *a, **k: called.append(a))
    reply = srv.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                               "params": {"name": "COMPUTER_USE", "arguments": {"goal": "x"}}})
    assert reply["result"]["isError"] is True
    assert called == [], "a withheld action must never reach the executor"


def test_failing_action_is_reported_as_an_error_not_a_success(monkeypatch):
    import eli.execution.executor_enhanced as ex
    monkeypatch.setattr(ex, "execute",
                        lambda *a, **k: {"ok": False, "content": "disk was unreadable"})
    monkeypatch.setattr(srv, "_exposed_actions", lambda: ["DATE"])
    reply = srv.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                               "params": {"name": "DATE", "arguments": {}}})
    assert reply["result"]["isError"] is True
    assert "disk was unreadable" in reply["result"]["content"][0]["text"]


def test_serve_loop_ignores_junk_and_answers_json(monkeypatch):
    stdin = io.StringIO('not json at all\n'
                        '{"jsonrpc":"2.0","id":9,"method":"ping"}\n')
    stdout = io.StringIO()
    srv.serve(stdin=stdin, stdout=stdout)
    replies = [json.loads(l) for l in stdout.getvalue().splitlines() if l.strip()]
    assert len(replies) == 1 and replies[0]["id"] == 9


def test_control_actions_appear_only_when_allowed(monkeypatch):
    monkeypatch.setattr("eli.execution.executor_enhanced.SUPPORTED_ACTIONS",
                        {"TIME", "SHELL_RUN"}, raising=False)
    monkeypatch.setenv("ELI_MCP_ALLOW_CONTROL", "1")
    names = {t["name"] for t in _rpc("tools/list")["result"]["tools"]}
    assert names == {"TIME", "SHELL_RUN"}


def test_an_exposed_action_runs_and_returns_text(monkeypatch):
    monkeypatch.setattr("eli.execution.executor_enhanced.SUPPORTED_ACTIONS", {"TIME"}, raising=False)
    monkeypatch.setattr("eli.execution.executor_enhanced.execute",
                        lambda action, args: {"ok": True, "content": "It is noon."})
    r = _rpc("tools/call", {"name": "TIME", "arguments": {}})["result"]
    assert r["isError"] is False and r["content"][0]["text"] == "It is noon."


def test_connection_config_for_a_source_install(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    entry = srv.connection_config()["mcpServers"]["eli"]
    assert entry["command"] == sys.executable
    assert entry["args"] == ["-m", "eli.integrations.mcp.server"] and "env" not in entry


def test_connection_config_for_the_appimage(monkeypatch):
    monkeypatch.setenv("APPIMAGE", "/opt/ELI.AppImage")
    entry = srv.connection_config()["mcpServers"]["eli"]
    assert entry["command"] == "/opt/ELI.AppImage" and entry["args"] == ["--mcp-server"]


def test_the_control_scope_is_carried_in_the_config():
    entry = srv.connection_config(allow_control=True)["mcpServers"]["eli"]
    assert entry["env"] == {"ELI_MCP_ALLOW_CONTROL": "1"}


def test_a_real_process_keeps_stdout_pure_protocol():
    """Importing the executor prints banners and native libs write to fd 1; none of it
    may reach the protocol stream."""
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    out = subprocess.run(
        [sys.executable, "-m", "eli", "--mcp-server"],
        input="\n".join(json.dumps(m) for m in msgs) + "\n",
        capture_output=True, text=True, timeout=120,
    )
    lines = [l for l in out.stdout.splitlines() if l.strip()]
    assert len(lines) == 2
    replies = [json.loads(l) for l in lines]
    assert replies[0]["id"] == 1 and replies[1]["result"]["tools"]

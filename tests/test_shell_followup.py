"""Deictic shell follow-up and timestamp diagnostic routing."""
from __future__ import annotations

from eli.execution.router_enhanced import route
from eli.runtime.shell_followup import (
    extract_shell_from_assistant,
    is_run_prior_shell_request,
    resolve_run_prior_shell,
)


def _action(text: str) -> str:
    return (route(text) or {}).get("action") or ""


def _args(text: str) -> dict:
    return (route(text) or {}).get("args") or {}


def test_run_that_command_is_not_open_app():
    assert _action("run that command") != "OPEN_APP"


def test_run_that_command_executes_prior_shell_block(monkeypatch):
    prior = (
        "I'll check the clock.\n\n"
        "```bash\n"
        "date -Iseconds\n"
        "echo ---\n"
        "stat --format='%Y %y' /proc/1/status\n"
        "```"
    )
    monkeypatch.setattr(
        "eli.runtime.shell_followup._last_assistant_text",
        lambda: prior,
    )
    assert is_run_prior_shell_request("run that command")
    assert resolve_run_prior_shell("run that command") == "date -Iseconds"
    r = route("run that command")
    assert r["action"] == "SHELL_EXEC"
    assert r["args"]["cmd"] == "date -Iseconds"


def test_extract_shell_prefers_read_only_line():
    text = "```bash\necho ---\ndate -Iseconds\n```"
    assert extract_shell_from_assistant(text) == "date -Iseconds"


def test_dig_into_timestamps_routes_to_diag():
    assert _action("yes please dig into the timestamps") == "TIMESTAMP_DIAG"


def test_portable_does_not_open_that_command():
    from eli.execution.portable_intent_contract import try_route
    assert try_route("run that command") is None


def test_a_chained_line_is_never_the_command_to_run():
    text = "```bash\nls ~; rm -rf ~/x\n```"
    assert extract_shell_from_assistant(text) == ""


def test_a_block_with_nothing_that_only_reads_gives_nothing_to_run():
    text = "```bash\nrm -rf ~/old_builds\nmkdir ~/new\n```"
    assert extract_shell_from_assistant(text) == ""


def test_a_substitution_or_a_written_file_is_not_picked():
    assert extract_shell_from_assistant("```bash\ncat $(rm -rf ~/x)\n```") == ""
    assert extract_shell_from_assistant("```bash\necho done > ~/.profile\n```") == ""
    assert extract_shell_from_assistant("```bash\nfind ~ -delete\nuptime\n```") == "uptime"


def test_that_command_is_from_this_conversation_not_another(monkeypatch):
    from eli.kernel import request_context as rc
    from eli.runtime import shell_followup

    other = {"role": "assistant", "content": "```bash\nuptime\n```", "session_id": "elsewhere"}

    class _Memory:
        def get_recent_conversation(self, limit=20, session_id=None, user_id=None, **_k):
            rows = [other]
            return [r for r in rows if session_id is None or r["session_id"] == session_id]

    monkeypatch.setattr("eli.memory.memory.get_memory", lambda *a, **k: _Memory())
    token = rc.session_id_var.set("this-one")
    try:
        assert shell_followup.resolve_run_prior_shell("run that command") is None
    finally:
        rc.session_id_var.reset(token)

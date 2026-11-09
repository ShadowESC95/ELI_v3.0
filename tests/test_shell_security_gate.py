"""Integration tests: shell command security gate in executor_enhanced.

Verifies:
- RUN_CMD rejects commands matching _BLOCKED_PATTERNS (shell -c, python -c, etc.)
- RUN_CMD rejects executables in _DENIED_EXECUTABLES
- RUN_CMD allows safe commands like 'ls', 'echo', 'cat'
- Blocked commands return ok=False with an error message (no subprocess spawned)
"""
import os
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _full_control_off(monkeypatch):
    """These tests verify the safety FLOOR, so they must run with ELI Full Control off
    (Full Control deliberately lifts this gate). The toggle is the sole control, so we
    force its check to False regardless of the persisted setting."""
    import eli.core.full_control as _fc
    monkeypatch.setattr(_fc, "is_full_control", lambda: False)
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_cmd(command: str, timeout: int = 5):
    """Call the RUN_CMD path in executor_enhanced directly."""
    from eli.execution.executor_enhanced import execute
    return execute("RUN_CMD", {"command": command, "timeout": timeout})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestShellSecurityGate:

    # ── Blocked pattern tests ────────────────────────────────────────────────

    def test_bash_minus_c_blocked(self):
        result = _run_cmd("bash -c 'rm -rf /'")
        assert result.get("ok") is False
        assert "block" in str(result).lower() or "deny" in str(result).lower() \
               or "secur" in str(result).lower() or "not allowed" in str(result).lower()

    def test_python_minus_c_blocked(self):
        result = _run_cmd("python3 -c 'import os; os.system(\"id\")'")
        assert result.get("ok") is False

    def test_sh_minus_c_blocked(self):
        result = _run_cmd("sh -c whoami")
        assert result.get("ok") is False

    def test_perl_minus_e_blocked(self):
        result = _run_cmd("perl -e 'print 1'")
        assert result.get("ok") is False

    def test_redirect_to_etc_blocked(self):
        result = _run_cmd("echo evil > /etc/passwd")
        assert result.get("ok") is False

    def test_chpasswd_blocked(self):
        result = _run_cmd("echo root:pwned | chpasswd")
        assert result.get("ok") is False

    def test_crontab_edit_blocked(self):
        result = _run_cmd("crontab -e")
        assert result.get("ok") is False

    # ── Denied executable tests ─────────────────────────────────────────────

    def test_rm_executable_blocked(self):
        result = _run_cmd("rm -rf /tmp/test")
        assert result.get("ok") is False

    def test_dd_blocked(self):
        result = _run_cmd("dd if=/dev/zero of=/tmp/x bs=1M count=1")
        assert result.get("ok") is False

    def test_nc_blocked(self):
        result = _run_cmd("nc -lvp 4444")
        assert result.get("ok") is False

    def test_iptables_blocked(self):
        result = _run_cmd("iptables -F")
        assert result.get("ok") is False

    # ── Allowed commands ────────────────────────────────────────────────────

    def test_ls_allowed(self):
        """ls /tmp should not be blocked by the security gate."""
        # We mock subprocess.run to avoid actually running it in the test suite
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "file1\nfile2\n"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc) as _sp:
            result = _run_cmd("ls /tmp")
        # If the gate passed, subprocess.run was called (not blocked)
        assert result.get("ok") is not False or _sp.called, (
            "ls /tmp was incorrectly blocked by the security gate"
        )

    def test_echo_allowed(self):
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "hello\n"
        mock_proc.stderr = ""

        with patch("subprocess.run", return_value=mock_proc):
            result = _run_cmd("echo hello")
        assert result.get("ok") is not False


class TestSecurityGateNoSubprocess:
    """Ensure blocked commands never reach subprocess.run."""

    def test_blocked_command_never_spawns_process(self):
        with patch("subprocess.run") as sp_mock:
            _run_cmd("bash -c 'id'")
        sp_mock.assert_not_called()

    def test_denied_executable_never_spawns_process(self):
        with patch("subprocess.run") as sp_mock:
            _run_cmd("rm /etc/hosts")
        sp_mock.assert_not_called()


# ---------------------------------------------------------------------------
# SHELL_EXEC: the whole line is read as the shell reads it
# ---------------------------------------------------------------------------

def _shell_exec(command: str):
    from eli.execution.executor_enhanced import execute
    return execute("SHELL_EXEC", {"cmd": command})


def _ran(mock_proc_out: str = ""):
    mock_proc = MagicMock()
    mock_proc.returncode = 0
    mock_proc.stdout = mock_proc_out
    mock_proc.stderr = ""
    return mock_proc


@pytest.fixture
def _no_owner_allowlist(monkeypatch):
    """No ELI_ALLOWED_CMDS: only commands that only read may run."""
    import eli.execution.effectors._base as _base
    monkeypatch.delenv("ELI_ALLOWED_CMDS", raising=False)
    monkeypatch.setattr(_base, "_get_security_manager", lambda: None)


class TestShellExecReadsEveryCommand:

    @pytest.mark.parametrize("line", [
        "ls ~; rm -rf ~/Documents",          # a second command after ;
        "ls && rm -rf $HOME",                # after &&
        "echo x | rm -rf ~/x",               # in a pipe
        "ls $(rm -rf ~/x)",                  # inside a substitution
        "echo \"`rm -rf ~/x`\"",             # inside backticks in quotes
        "env rm -rf ~/x",                    # behind a word that runs another command
        "find ~ -name '*.txt' -delete",      # a read-only command told to delete
        "cat /dev/null > ~/.bashrc",         # a file written by redirection
        "echo hi; touch ~/probe_file",       # a command that is not one that only reads
        "ls 'unclosed",                      # a line the shell could not read either
    ])
    def test_a_line_that_would_change_something_never_reaches_the_shell(self, line, _no_owner_allowlist):
        with patch("subprocess.run") as sp_mock:
            result = _shell_exec(line)
        assert result.get("ok") is False and result.get("blocked") is True, result
        sp_mock.assert_not_called()

    def test_a_line_of_commands_that_only_read_runs_under_the_shell(self, _no_owner_allowlist):
        with patch("subprocess.run", return_value=_ran("3\n")) as sp_mock:
            result = _shell_exec("grep -rn needle ~/notes 2>/dev/null | wc -l")
        assert result.get("ok") is True, result
        argv = sp_mock.call_args[0][0]
        assert argv[-1] == "grep -rn needle ~/notes 2>/dev/null | wc -l"

    def test_a_command_the_owner_allowed_by_name_runs(self, monkeypatch):
        import eli.execution.effectors._base as _base

        class _Owner:
            def is_command_allowed(self, name):
                return name == "git"
        monkeypatch.setattr(_base, "_get_security_manager", lambda: _Owner())
        with patch("subprocess.run", return_value=_ran()) as sp_mock:
            assert _shell_exec("git status && ls").get("ok") is True
        assert sp_mock.called
        with patch("subprocess.run") as sp_mock:
            assert _shell_exec("git status; touch x").get("blocked") is True
        sp_mock.assert_not_called()

    def test_full_control_still_lifts_the_floor(self, monkeypatch):
        import eli.core.full_control as _fc
        monkeypatch.setattr(_fc, "is_full_control", lambda: True)
        with patch("subprocess.run", return_value=_ran()) as sp_mock:
            assert _shell_exec("echo hi; touch ~/probe_file").get("ok") is True
        assert sp_mock.called

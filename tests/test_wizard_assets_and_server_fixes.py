"""Setup wizard must hard-fail missing nomic/piper correctly."""
from __future__ import annotations

from pathlib import Path


def test_windows_installer_no_desktop_server_icon():
    iss = Path("packaging/windows/installer.iss").read_text(encoding="utf-8")
    assert 'Name: "{autodesktop}\\ELI Server"' not in iss
    assert 'Name: "{autodesktop}\\{#MyAppName}"' in iss
    assert iss.count("[Run]") >= 1
    # Only one postinstall Launch ELI line in the final [Run] block
    assert 'Description: "Launch ELI"' in iss


def test_eli_entry_has_gui_singleton():
    src = Path("packaging/pyinstaller/eli_entry.py").read_text(encoding="utf-8")
    assert "_gui_singleton_or_exit" in src
    assert "ELI_ALLOW_MULTI_INSTANCE" in src
    assert "os.kill(" not in src, "os.kill(pid, 0) sends Ctrl+C on Windows; it does not test a process"


_HOLD_SINGLETON = r"""
import importlib.util, sys, time
sys.argv = ["ELI"]
spec = importlib.util.spec_from_file_location("eli_entry", "packaging/pyinstaller/eli_entry.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
entry._message_box_warning = lambda title, text: print("BLOCKED:", title, flush=True)
entry._gui_singleton_or_exit()
print("HOLDING", flush=True)
time.sleep(float(sys.argv[1]) if len(sys.argv) > 1 else 0)
"""


def test_a_second_window_is_refused_while_the_first_runs_and_allowed_after(tmp_path):
    """Two real processes against one lock file: the second exits while the first holds it, and a
    third starts once the first has gone (the OS released its lock; no stale-file cleanup)."""
    import os
    import subprocess
    import sys
    env = dict(os.environ, ELI_DATA_DIR=str(tmp_path))
    env.pop("ELI_ALLOW_MULTI_INSTANCE", None)
    first = subprocess.Popen([sys.executable, "-c", _HOLD_SINGLETON.replace('sys.argv = ["ELI"]', 'sys.argv = ["ELI", "30"]')],
                             env=env, stdout=subprocess.PIPE, text=True)
    try:
        assert first.stdout.readline().strip() == "HOLDING"
        second = subprocess.run([sys.executable, "-c", _HOLD_SINGLETON], env=env, capture_output=True,
                                text=True, timeout=120)
        assert second.returncode == 0 and "BLOCKED: ELI is already running" in second.stdout
        assert "HOLDING" not in second.stdout
    finally:
        first.kill()
        first.wait()
    third = subprocess.run([sys.executable, "-c", _HOLD_SINGLETON], env=env, capture_output=True,
                           text=True, timeout=120)
    assert "HOLDING" in third.stdout


def test_a_helper_run_through_the_frozen_app_is_python_and_never_a_second_window(tmp_path):
    """In a frozen build sys.executable is ELI itself, and the entry passed only -c and -m through:
    `ELI script.py` (the IDE's Run, the test and eval reports) started a GUI, met the single-instance
    guard, and put up "ELI is already running". Whatever `python` takes (switches, -c, -m, a .py
    script) runs as python, with python's exit status; the coding sandbox's -c runner still runs."""
    import os
    import subprocess
    import sys
    entry = "packaging/pyinstaller/eli_entry.py"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")    # were it to reach the GUI, nothing shows

    parse = subprocess.run([sys.executable, "-c", r"""
import importlib.util, sys
sys.argv = ["ELI"]
spec = importlib.util.spec_from_file_location("eli_entry", "packaging/pyinstaller/eli_entry.py")
entry = importlib.util.module_from_spec(spec); spec.loader.exec_module(entry)
for argv in (["cand.py", "a"], ["-u", "cand.py"], ["-I", "-X", "utf8", "-c", "pass", "x"], ["-m", "pip"], ["-"],
             ["--server"], ["--selftest"], [], ["picture.png"], ["-u"]):
    print(argv, entry._python_invocation(argv))
"""], env=env, capture_output=True, text=True, timeout=120)
    assert parse.returncode == 0, parse.stderr
    lines = parse.stdout.splitlines()
    assert lines[0].endswith("('path', 'cand.py', ['a'], [])")
    assert lines[1].endswith("('path', 'cand.py', [], ['-u'])")
    assert lines[2].endswith("('c', 'pass', ['x'], ['-I', '-X', 'utf8'])")
    assert lines[3].endswith("('m', 'pip', [], [])") and lines[4].endswith("('stdin', '-', [], [])")
    assert all(line.endswith(" None") for line in lines[5:]), lines[5:]     # ELI's own, or nothing to run

    script = tmp_path / "candidate.py"
    script.write_text("import sys\nimport sibling\nprint('ran', sys.argv[1:], sibling.VALUE)\n", encoding="utf-8")
    (tmp_path / "sibling.py").write_text("VALUE = 7\n", encoding="utf-8")
    ran = subprocess.run([sys.executable, entry, "-u", str(script), "one"], env=env, capture_output=True,
                         text=True, timeout=120)
    assert ran.returncode == 0 and "ran ['one'] 7" in ran.stdout, ran.stderr[-800:]
    assert "already running" not in ran.stdout + ran.stderr

    from eli.coding.sandbox import _FROZEN_RUNNER
    runner = subprocess.run([sys.executable, entry, "-c", _FROZEN_RUNNER, str(script), "two"], env=env,
                            capture_output=True, text=True, timeout=120)
    assert runner.returncode == 0 and "ran ['two'] 7" in runner.stdout, runner.stderr[-800:]

    script.write_text("raise ValueError('candidate failed')\n", encoding="utf-8")
    failed = subprocess.run([sys.executable, entry, str(script)], env=env, capture_output=True, text=True, timeout=120)
    assert failed.returncode == 1 and "ValueError: candidate failed" in failed.stderr

    missing = subprocess.run([sys.executable, entry, str(tmp_path / "gone.py")], env=env, capture_output=True,
                             text=True, timeout=120)
    assert missing.returncode == 2 and "can't open file" in missing.stderr


def test_the_frozen_app_hands_eli_s_own_commands_to_what_the_source_tree_runs_for_them():
    """A frozen ELI spawns `ELI --bundle <id>` for a bundle beside its window (eli/bundles), and tells
    other apps to run `ELI --mcp-server` (eli/integrations/mcp/server.connection_config); the entry knew
    neither, so each opened a second window and met the single-instance guard. Those commands, and
    --bundles, --mcp-config, --headless and --trust-agent, go to eli/__main__.py as in a source tree."""
    import json
    import os
    import subprocess
    import sys
    entry = "packaging/pyinstaller/eli_entry.py"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    modes = subprocess.run([sys.executable, "-c", r"""
import importlib.util, sys
sys.argv = ["ELI"]
spec = importlib.util.spec_from_file_location("eli_entry", "packaging/pyinstaller/eli_entry.py")
entry = importlib.util.module_from_spec(spec); spec.loader.exec_module(entry)
for argv in (["--bundle", "legal"], ["--bundles"], ["--mcp-server"], ["--mcp-config", "--allow-control"],
             ["--headless"], ["-H"], ["--trust-agent", "agent.py"], [], ["--server"], ["--selftest"]):
    sys.argv = ["ELI", *argv]
    print(entry._mode())
"""], env=env, capture_output=True, text=True, timeout=120)
    assert modes.returncode == 0, modes.stderr
    assert modes.stdout.split() == ["cli"] * 7 + ["gui", "server", "selftest"]

    config = subprocess.run([sys.executable, entry, "--mcp-config"], env=env, capture_output=True, text=True,
                            timeout=120)
    assert config.returncode == 0, config.stderr[-800:]
    assert "eli" in json.loads(config.stdout)["mcpServers"]
    bundle = subprocess.run([sys.executable, entry, "--bundle", "no-such-bundle"], env=env, capture_output=True,
                            text=True, timeout=120)
    assert bundle.returncode == 2 and "No bundle 'no-such-bundle'" in bundle.stderr, bundle.stderr[-800:]
    assert "already running" not in bundle.stdout + bundle.stderr

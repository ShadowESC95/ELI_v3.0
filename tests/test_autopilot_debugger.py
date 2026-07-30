"""Autopilot debugger (v3) — grounded diagnosis from real error text + git history."""
from __future__ import annotations

from eli.runtime import autopilot_debugger as dbg

_TB = '''Traceback (most recent call last):
  File "eli/cognition/agent_bus.py", line 20, in dispatch
    x = registry[key]
KeyError: 'missing'
'''


def test_traceback_gives_exception_and_affected_file():
    d = dbg.diagnose(_TB)
    assert d["ok"] and "KeyError" in d["exception"]
    assert "eli/cognition/agent_bus.py" in d["affected_files"]
    assert d["root_cause"].startswith("KeyError")


def test_rollback_and_validation_grounded():
    d = dbg.diagnose(_TB)
    assert d["suspect_commits"]
    assert any("git checkout" in l or "git revert" in l for l in d["rollback_plan"])
    assert any("test_no_silent_swallow" in c for c in d["validation_commands"])


def test_pytest_failure_and_empty_and_stdlib_exclusion():
    d = dbg.diagnose("FAILED tests/test_x.py::test_y - AssertionError")
    assert "tests/test_x.py" in d["affected_files"]
    assert "nothing to diagnose" in dbg.diagnose("")["root_cause"].lower()
    tb = ('Traceback (most recent call last):\n'
          '  File "/usr/lib/python3.12/json/__init__.py", line 1, in load\n'
          '  File "eli/memory/vector_store.py", line 10, in search\nValueError: x\n')
    d3 = dbg.diagnose(tb)
    assert "eli/memory/vector_store.py" in d3["affected_files"]
    assert not any("site-packages" in f or f.startswith("/usr") for f in d3["affected_files"])


def test_resolve_reproduces_a_real_failing_test_readonly(tmp_path):
    # resolve(apply=False) must RUN the validation and confirm the failure without touching source.
    import subprocess, sys, os
    from pathlib import Path
    from eli.runtime import autopilot_debugger as dbg
    root = dbg._repo_root()
    probe = root / "tests" / "test_ap_probe_tmp.py"
    probe.write_text("def test_probe_fail():\n    assert False\n", encoding="utf-8")
    try:
        t = dbg.resolve("FAILED tests/test_ap_probe_tmp.py::test_probe_fail - assert False", apply=False)
        assert t["ok"] and t["reproduced"] is True and t["applied"] is False
        assert "reproduc" in dbg.format_resolution(t).lower()
    finally:
        probe.unlink(missing_ok=True)


def test_resolve_no_repro_when_nothing_fails():
    from eli.runtime import autopilot_debugger as dbg
    t = dbg.resolve("", apply=False)
    assert t["ok"] and t["reproduced"] is False and t["resolved"] is False

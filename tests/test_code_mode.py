"""Code-mode: Full-Control-gated, AST-whitelisted in-process execution against eli.api.

Covers the restricted executor (gate + security whitelist + run), the eli.api facade
(sugar over execute()), and the generate→run→retry loop. No model required.
"""
import pytest

import eli.core.full_control as fc
from eli.coding import restricted_exec as R


# ── restricted executor: the Full Control gate ──────────────────────────────
def test_gate_blocks_when_full_control_off(monkeypatch):
    monkeypatch.setattr(fc, "is_full_control", lambda: False)
    r = R.run_restricted("result = 1 + 1", api=object())
    assert r.blocked is True and r.ok is False
    assert "Full Control" in r.meta.get("reason", "")


def test_runs_when_full_control_on(monkeypatch):
    monkeypatch.setattr(fc, "is_full_control", lambda: True)
    r = R.run_restricted("result = sum(x for x in range(5))", api=object())
    assert r.ok is True and r.result == 10


# ── restricted executor: the AST security whitelist ─────────────────────────
@pytest.mark.parametrize("code", [
    "import os\nresult = os.getcwd()",
    "result = eval('1+1')",
    "result = open('/etc/passwd').read()",
    "result = getattr(api, 'x')",
    "result = api.__class__.__bases__",
    "result = __import__('os')",
    "def f():\n    return 1\nresult = f()",
    "result = (lambda: 1)()",
    "with open('x') as fh:\n    result = 1",
    "exec('x = 1')\nresult = 1",
])
def test_security_escapes_rejected(monkeypatch, code):
    monkeypatch.setattr(fc, "is_full_control", lambda: True)
    r = R.run_restricted(code, api=object())
    assert r.ok is False and r.validation_error, f"should reject: {code!r}"


def test_validate_program_is_pure_and_correct():
    # static, no execution, no gate
    assert R.validate_program("result = api.recall('x')") is None
    assert R.validate_program("import os") is not None
    assert R.validate_program("y = a.__dict__") is not None


def test_runtime_error_and_stdout_captured(monkeypatch):
    monkeypatch.setattr(fc, "is_full_control", lambda: True)

    class API:
        def recall(self, q):
            return ["one"]
    r = R.run_restricted("print('trace'); result = api.recall('x')[5]", api=API())
    assert r.ok is False and r.runtime_error and "trace" in r.stdout


# ── the eli.api facade (sugar over execute) ─────────────────────────────────
def test_facade_proxies_to_execute(monkeypatch):
    import eli.execution.executor_enhanced as ex
    monkeypatch.setattr(ex, "execute",
                        lambda action, args=None, **kw: {"ok": True, "action": action, "args": args or {}})
    from eli.tools.api import api
    assert api.call("BACKGROUND_JOBS")["action"] == "BACKGROUND_JOBS"
    assert api.summarize_file("/x.pdf")["args"]["path"] == "/x.pdf"
    assert api.check_job(5)["args"]["job_id"] == 5


# ── the generate → run → retry loop ─────────────────────────────────────────
def test_code_mode_loop_and_retry(monkeypatch):
    monkeypatch.setattr(fc, "is_full_control", lambda: True)
    from eli.coding.code_mode import run_code_mode

    class API:
        def actions(self):
            return ["CHECK_JOB", "BACKGROUND_JOBS"]
        def background_jobs(self):
            return {"action": "BACKGROUND_JOBS"}
        def check_job(self, j):
            return {"action": "CHECK_JOB", "job": j}

    # one-shot success
    out = run_code_mode("list jobs", lambda p: "result = api.background_jobs()['action']", api=API())
    assert out["ok"] and out["result"] == "BACKGROUND_JOBS" and out["attempts"] == 1

    # fails first (import → rejected), then fixes → retry recovers
    seq = iter(["import os\nresult = os.getcwd()", "result = api.check_job(5)['action']"])
    out = run_code_mode("check job 5", lambda p: next(seq), api=API(), max_attempts=3)
    assert out["ok"] and out["result"] == "CHECK_JOB" and out["attempts"] == 2


def test_code_mode_blocked_without_full_control(monkeypatch):
    monkeypatch.setattr(fc, "is_full_control", lambda: False)
    from eli.coding.code_mode import run_code_mode
    out = run_code_mode("do it", lambda p: "result = 1", api=type("A", (), {"actions": lambda s: []})())
    assert out["ok"] is False and out["blocked"] is True


# ── a program cannot keep the turn's thread ─────────────────────────────────
def test_a_loop_that_never_ends_is_stopped_at_its_deadline(monkeypatch):
    """`while` is allowed and nothing bounded the run: a polling or runaway loop hung the turn's
    thread for good."""
    import threading
    monkeypatch.setattr(fc, "is_full_control", lambda: True)
    box = {}
    t = threading.Thread(target=lambda: box.update(r=R.run_restricted("while True:\n    pass", api=object(),
                                                                       deadline=0.5)), daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive(), "the program was never stopped"
    assert box["r"].ok is False and box["r"].runtime_error.startswith("stopped")


def test_output_is_bounded_and_the_process_stdout_is_never_swapped(monkeypatch):
    import sys
    monkeypatch.setattr(fc, "is_full_control", lambda: True)
    seen = []

    class API:
        def probe(self):
            seen.append(sys.stdout)
            return 1
    before = sys.stdout
    r = R.run_restricted("n = 0\nwhile n < 5000:\n    print('x' * 50)\n    n += 1\nresult = api.probe()",
                         api=API(), max_stdout=200)
    assert r.ok and len(r.stdout) == 200 and r.meta.get("stdout_truncated") is True
    assert seen == [before], "another thread's prints would have gone into the program's buffer"


def test_wait_is_bounded_by_the_deadline(monkeypatch):
    import time
    monkeypatch.setattr(fc, "is_full_control", lambda: True)
    t0 = time.monotonic()
    r = R.run_restricted("waited = wait(60)\nresult = waited", api=object(), deadline=0.3)
    assert r.ok is False and r.runtime_error.startswith("stopped")
    assert time.monotonic() - t0 < 5
    ok = R.run_restricted("result = wait(0.01)", api=object())
    assert ok.ok and ok.result >= 0

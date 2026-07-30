"""Frontier #4 — self-improvement closes the loop with a TEST-verify gate (not just import)."""
from __future__ import annotations
from eli.runtime.self_improvement import SelfImprovementEngine
from eli.runtime import autopilot_debugger as dbg


def _root():
    return dbg._repo_root()


def test_test_verify_green_red_and_absent():
    root = _root()
    eng = SelfImprovementEngine()
    src = root / "eli" / "zz_probe_src.py"
    tst = root / "tests" / "test_zz_probe_src.py"
    src.write_text("X = 1\n", encoding="utf-8")
    try:
        tst.write_text("from eli.zz_probe_src import X\ndef test_x():\n    assert X == 1\n", encoding="utf-8")
        assert eng._test_verify_patch({"file": "eli/zz_probe_src.py"})["verified"] is True
        tst.write_text("def test_x():\n    assert False\n", encoding="utf-8")
        assert eng._test_verify_patch({"file": "eli/zz_probe_src.py"})["verified"] is False
        # no test file at all → cannot verify (None), never a false 'green'
        assert eng._test_verify_patch({"file": "eli/no_such_zz.py"})["verified"] is None
    finally:
        src.unlink(missing_ok=True)
        tst.unlink(missing_ok=True)


def test_run_patch_cycle_accepts_verify_flag_and_is_stable_with_no_failures():
    eng = SelfImprovementEngine()
    # With no recurring failures, the cycle is a safe no-op — and must accept verify_tests.
    r = eng.run_patch_cycle(max_patches=1, dry_run=True, verify_tests=True)
    assert "summary" in r and r["dry_run"] is True

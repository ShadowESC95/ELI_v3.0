"""Frontier #4 — self-improvement closes the loop with a TEST-verify gate (not just import)."""
from __future__ import annotations
from eli.runtime.self_improvement import SelfImprovementEngine


def test_test_verify_green_red_and_absent(source_tree):
    """In a throwaway source tree: the probe module and its test were written into the checkout's eli/
    and tests/ while this ran."""
    eng = SelfImprovementEngine()
    (source_tree / "eli" / "zz_probe_src.py").write_text("X = 1\n", encoding="utf-8")
    tst = source_tree / "tests" / "test_zz_probe_src.py"
    tst.write_text("from eli.zz_probe_src import X\ndef test_x():\n    assert X == 1\n", encoding="utf-8")
    assert eng._test_verify_patch({"file": "eli/zz_probe_src.py"})["verified"] is True
    tst.write_text("def test_x():\n    assert False\n", encoding="utf-8")
    assert eng._test_verify_patch({"file": "eli/zz_probe_src.py"})["verified"] is False
    # no test file at all → cannot verify (None), never a false 'green'
    assert eng._test_verify_patch({"file": "eli/no_such_zz.py"})["verified"] is None


def test_run_patch_cycle_accepts_verify_flag_and_is_stable_with_no_failures():
    eng = SelfImprovementEngine()
    # With no recurring failures, the cycle is a safe no-op — and must accept verify_tests.
    r = eng.run_patch_cycle(max_patches=1, dry_run=True, verify_tests=True)
    assert "summary" in r and r["dry_run"] is True


def test_the_root_the_packaged_launch_check_reads_is_the_source_tree_in_use(source_tree):
    """The packaged app's launch check reads PROJECT_ROOT ("code_examiner_root", which must be outside
    the read-only bundle); it was a constant fixed at import, so a source tree named afterwards was
    not the one checked."""
    import eli.runtime.self_improvement as SI
    assert SI.PROJECT_ROOT == source_tree

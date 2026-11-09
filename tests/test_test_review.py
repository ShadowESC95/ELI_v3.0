"""Tests for the test/project run → review workflow (eli/runtime/test_review.py)."""
from __future__ import annotations

from pathlib import Path

import eli.runtime.test_review as TR


_JUNIT = """<?xml version="1.0"?>
<testsuites><testsuite>
  <testcase classname="tests.test_a" name="test_pass"/>
  <testcase classname="tests.test_a" name="test_fail"><failure message="assert 1==2">boom</failure></testcase>
  <testcase classname="tests.test_b" name="test_err"><error message="ImportError x">trace</error></testcase>
  <testcase classname="tests.test_c" name="test_xf"><skipped type="pytest.xfail">gap</skipped></testcase>
  <testcase classname="tests.test_c" name="test_skip"><skipped type="pytest.skip">later</skipped></testcase>
</testsuite></testsuites>
"""


def test_parse_junit_counts_and_failures(tmp_path):
    j = tmp_path / "j.xml"
    j.write_text(_JUNIT)
    totals, failures = TR._parse_junit(j)
    assert totals["total"] == 5
    assert totals["passed"] == 1 and totals["failed"] == 1 and totals["errored"] == 1
    assert totals["xfailed"] == 1 and totals["skipped"] == 1
    nodes = {f["node"] for f in failures}
    assert "tests.test_a::test_fail" in nodes and "tests.test_b::test_err" in nodes


def test_build_options_for_failures():
    failures = [{"node": "tests.test_a::x", "module": "tests.test_a", "message": "m"}]
    opts = TR.build_options({"xfailed": 0}, failures)
    ids = {o["id"] for o in opts}
    assert {"examine", "propose", "rerun"} <= ids
    examine = next(o for o in opts if o["id"] == "examine")
    assert "tests/test_a.py" in examine["command"]


def test_build_options_clean_run_offers_growth():
    opts = TR.build_options({"xfailed": 2}, [])
    ids = {o["id"] for o in opts}
    assert "generate" in ids and "eval" in ids and "gaps" in ids
    assert all("command" in o and "label" in o for o in opts)


def test_propose_fixes_orchestrates_examiner_over_modules():
    # delegates to the existing code examiner via the DAG orchestrator (parallel),
    # returning per-module findings + the orchestration report. A clean module → 0
    # findings (correct), but the structure + orchestration must be present.
    res = TR.propose_fixes(
        [{"node": "tests.t::x", "module": "eli.core.model_tier", "message": "boom"}],
        run_tier3=False)
    assert res["ok"] is True
    assert res["examined"] == ["eli/core/model_tier.py"]
    assert "orchestration" in res and res["orchestration"]["layers"]


def test_propose_fixes_no_resolvable_modules():
    res = TR.propose_fixes([{"node": "x::y", "module": "does.not.exist", "message": "m"}])
    assert res["ok"] is False and res["proposals"] == []


def test_run_and_review_integration():
    # light: run a small real target; the subprocess + parent both resolve the same
    # ELI_ARTIFACTS_DIR (the in-project test dir from conftest), so totals are parsed.
    res = TR.run_and_review("tests/test_lora_pipeline.py", timeout=300)
    assert "totals" in res and res["totals"]["total"] >= 1
    assert isinstance(res["options"], list) and res["options"]
    if res["ok"]:
        assert res["error_file"] is None
    assert res["report_path"]


def _fake_run(monkeypatch, *, returncode, junit=None):
    """The suite run replaced: it exits with `returncode` and writes `junit` (or nothing)."""
    import subprocess
    import types

    def run(*a, **k):
        if junit is not None:
            (TR._art() / "test_report.junit.xml").write_text(junit)
        return types.SimpleNamespace(returncode=returncode, stdout="", stderr="")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(TR, "propose_fixes", lambda failures, **k: {"ok": True, "proposals": []})


_PASSING = """<?xml version="1.0"?><testsuites><testsuite>
  <testcase classname="tests.test_a" name="test_pass"/></testsuite></testsuites>"""


def test_a_run_that_wrote_no_results_is_not_read_as_the_last_runs_pass(tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_ARTIFACTS_DIR", str(tmp_path))
    (tmp_path / "test_report.junit.xml").write_text(_PASSING)      # the last run's
    _fake_run(monkeypatch, returncode=4)
    res = TR.run_and_review("tests/test_nothing_here.py")
    assert res["ok"] is False and res["ran"] == 0 and res["returncode"] == 4
    assert res["totals"]["total"] == 0 and "no tests ran" in res["error"]


def test_a_run_that_exits_on_an_error_is_not_a_pass_whatever_its_results(tmp_path, monkeypatch):
    monkeypatch.setenv("ELI_ARTIFACTS_DIR", str(tmp_path))
    _fake_run(monkeypatch, returncode=2, junit=_PASSING)
    res = TR.run_and_review("tests/")
    assert res["ok"] is False and res["returncode"] == 2 and res["ran"] == 1
    _fake_run(monkeypatch, returncode=0, junit=_PASSING)
    assert TR.run_and_review("tests/")["ok"] is True

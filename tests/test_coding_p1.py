"""P1 coding-agent tests: repo index, patches, sandbox capabilities, eval board."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from eli.coding.repo_index import RepoIndex
from eli.coding.patches import compute_line_patches, is_repo_edit_task
from eli.coding.sandbox import capabilities, run_code


def test_repo_index_indexes_and_queries_symbols(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    (root / "demo.py").write_text(
        "import os\n\n\ndef verify_candidate():\n    return True\n",
        encoding="utf-8",
    )
    db = tmp_path / "idx.sqlite3"
    idx = RepoIndex(db, root)
    idx.project_root = tmp_path
    stats = idx.refresh(force=True)
    assert stats["updated"] >= 1
    found = idx.query_symbols(["verify_candidate"], def_lines=5)
    assert "verify_candidate" in found
    assert "def verify_candidate" in found["verify_candidate"]


def test_compute_line_patches_minimal_hunk():
    old = "alpha = 1\nbeta = 2\n"
    new = "alpha = 1\nbeta = 99\n"
    patches = compute_line_patches("pkg/demo.py", old, new)
    assert patches and patches[0]["old"] == "beta = 2\n"
    assert patches[0]["new"] == "beta = 99\n"


def test_is_repo_edit_task_detects_fix_verb():
    assert is_repo_edit_task("fix the bug in eli/coding/verification.py") is True
    assert is_repo_edit_task("hello there") is False


def test_sandbox_capabilities_reports_platform():
    caps = capabilities()
    assert caps.get("subprocess_isolation") is True
    assert "platform" in caps
    assert isinstance(caps.get("notes"), list)


def test_sandbox_still_runs_clean():
    r = run_code("print(2 + 2)", "python", timeout=8)
    assert r.clean and r.returncode == 0


def test_coding_eval_board_runs_stub_cases():
    from tools.eval.run_eval import run_board
    board = run_board("coding_agent", case_filter="coding_fibonacci")
    assert board["total"] >= 1
    rec = next(r for r in board["records"] if r["id"] == "coding_fibonacci_solved")
    assert rec["status"] == "pass"

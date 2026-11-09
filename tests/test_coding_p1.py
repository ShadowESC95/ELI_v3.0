"""P1 coding-agent tests: repo index, patches, sandbox capabilities, eval board."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from eli.coding.repo_index import RepoIndex
from eli.coding.patches import compute_line_patches, propose_file_patch
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


def _apply_in_order(text, patches):
    """Apply hunks the way the gated applier does: each old must occur exactly once."""
    for p in patches:
        assert text.count(p["old"]) == 1, f"ambiguous hunk: {p['old']!r}"
        text = text.replace(p["old"], p["new"], 1)
    return text


def test_a_change_to_a_repeated_line_lands_where_it_was_made():
    old = "def a():\n    x = 1\n    return x\n\n\ndef b():\n    x = 1\n    return x\n"
    new = "def a():\n    x = 1\n    return x\n\n\ndef b():\n    x = 2\n    return x\n"
    patches = compute_line_patches("pkg/demo.py", old, new)
    assert patches and _apply_in_order(old, patches) == new


def test_an_insertion_at_the_top_of_a_file_is_kept():
    old = "a = 1\nb = 2\n"
    new = "import os\na = 1\nb = 2\n"
    patches = compute_line_patches("pkg/demo.py", old, new)
    assert patches and _apply_in_order(old, patches) == new


def test_a_deletion_is_a_hunk_with_an_empty_new():
    old = "a = 1\nb = 2\nc = 3\n"
    new = "a = 1\nc = 3\n"
    patches = compute_line_patches("pkg/demo.py", old, new)
    assert patches and _apply_in_order(old, patches) == new
    assert any(p["new"] == "" for p in patches)


def test_mixed_edits_reproduce_the_new_file():
    old = "".join(f"line{i} = {i}\n" for i in range(12)) + "line3 = 3\n"
    new = "header = 0\n" + "".join(f"line{i} = {i * 10 if i % 4 == 3 else i}\n" for i in range(12) if i != 6) + "line3 = 3\n"
    patches = compute_line_patches("pkg/demo.py", old, new)
    assert patches and _apply_in_order(old, patches) == new


@pytest.fixture
def in_tree_module():
    from eli.core.paths import source_root
    d = Path(tempfile.mkdtemp(prefix="propose_", dir=str(Path(source_root()) / "artifacts")))
    f = d / "target_mod.py"
    f.write_text("def keep_me(a):\n    return a - 1\n\n\ndef other():\n    return 2\n", encoding="utf-8")
    yield f
    import shutil
    shutil.rmtree(d, ignore_errors=True)


def test_a_standalone_program_is_not_proposed_as_the_files_new_version(in_tree_module):
    """The coding agent writes a standalone runnable program; diffed into the named file it replaced
    that file's contents. It is refused because it drops the file's public names."""
    before = in_tree_module.read_text()
    program = "def main():\n    print(1)\n\n\nif __name__ == '__main__':\n    main()\n"
    res = propose_file_patch(in_tree_module, program)
    assert res["ok"] is False and res["applied"] is False
    assert "keep_me" in " ".join(res["problems"]) and "other" in " ".join(res["problems"])
    assert in_tree_module.read_text() == before


def test_a_new_version_of_the_file_is_proposed_not_applied(in_tree_module):
    before = in_tree_module.read_text()
    fixed = before.replace("return a - 1", "return a + 1")
    res = propose_file_patch(in_tree_module, fixed)
    assert res["ok"] and res["applied"] is False
    assert res["old"] == before and res["new"] == fixed and res["hunks"]
    assert res["file"].endswith("target_mod.py")
    assert in_tree_module.read_text() == before
    assert not list(in_tree_module.parent.glob("*.eli_bak*"))


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

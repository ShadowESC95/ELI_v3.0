"""SHOW_DIFF answers from a real diff: two files compared, or the source tree's own changes, and
says so when there is nothing to compare. It used to ask the model for "the recent changes" with no
evidence, and the diff in the reply was made up."""
from __future__ import annotations

import subprocess

import pytest


def _show(args):
    from eli.execution.effectors.misc import _h_show_diff
    return _h_show_diff("SHOW_DIFF", args)


def test_two_files_are_compared(tmp_path):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_text("alpha\nbeta\n", encoding="utf-8")
    b.write_text("alpha\ngamma\n", encoding="utf-8")
    out = _show({"paths": f"{a} {b}"})
    assert out["ok"] is True
    assert "-beta" in out["diff"] and "+gamma" in out["diff"]
    assert out["evidence_source"] == "difflib"


def test_a_missing_file_is_said_and_not_invented(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("x\n", encoding="utf-8")
    out = _show({"paths": [str(a), str(tmp_path / "nope.txt")]})
    assert out["ok"] is False and "nope.txt" in out["response"]


@pytest.mark.skipif(subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
                    reason="git is not installed")
def test_the_source_tree_changes_are_shown(tmp_path, monkeypatch):
    from eli.execution.effectors import misc
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    run = lambda *argv: subprocess.run(["git", "-C", str(repo), *argv], check=True, capture_output=True,
                                       env={**__import__("os").environ, **env})
    run("init", "-q")
    (repo / "tool.py").write_text("print('one')\n", encoding="utf-8")
    run("add", "tool.py")
    run("commit", "-q", "-m", "first")
    monkeypatch.setattr(misc, "_source_root", lambda: repo)

    last = _show({})
    assert last["ok"] is True and last["evidence_source"] == "git show HEAD" and "first" in last["diff"]
    (repo / "tool.py").write_text("print('two')\n", encoding="utf-8")
    now = _show({})
    assert now["evidence_source"] == "git diff HEAD"
    assert "+print('two')" in now["diff"]


def test_no_source_tree_is_said(tmp_path, monkeypatch):
    from eli.execution.effectors import misc
    monkeypatch.setattr(misc, "_source_root", lambda: tmp_path)
    monkeypatch.setattr(misc, "_git", lambda root, *argv: (False, "not a git repository"))
    out = _show({})
    assert out["ok"] is False and out["error"] == "no_source_tree"

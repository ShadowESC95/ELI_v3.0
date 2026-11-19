"""Coding advancements D (self-critique) + E (atomic multi-file patch set)."""
from pathlib import Path

import pytest


# ── D — self-critique ───────────────────────────────────────────────────────
def test_critique_returns_concrete_issues():
    from eli.coding.verification import critique_candidate
    issues = critique_candidate("sum a list", "def s(x): return x[0]",
                                lambda p, **k: "line 1: crashes on empty list; no None handling")
    assert "empty list" in issues


def test_critique_empty_when_ok_or_no_model():
    from eli.coding.verification import critique_candidate
    assert critique_candidate("x", "def s(x): return sum(x)", lambda p, **k: "OK") == ""
    assert critique_candidate("x", "code", generate=None) == ""


# ── E — atomic multi-file patch set ─────────────────────────────────────────
def _eng():
    from eli.runtime.self_improvement import get_self_improvement
    return get_self_improvement()


def test_patch_set_applies_atomically(workspace):
    f1 = workspace / "a.py"; f1.write_text("X = 1\n")
    f2 = workspace / "b.py"; f2.write_text("Y = 2\n")
    r = _eng().apply_patch_set([
        {"file": str(f1), "old": "X = 1", "new": "X = 10"},
        {"file": str(f2), "old": "Y = 2", "new": "Y = 20"},
    ], verify=False)
    assert r["ok"] and len(r["files"]) == 2
    assert f1.read_text().strip() == "X = 10" and f2.read_text().strip() == "Y = 20"


def test_patch_set_all_or_nothing_on_bad_patch(workspace):
    # One patch is bad (old not found) → the WHOLE set aborts, the valid sibling is untouched.
    f1 = workspace / "a.py"; f1.write_text("X = 1\n")
    f2 = workspace / "b.py"; f2.write_text("Y = 2\n")
    r = _eng().apply_patch_set([
        {"file": str(f1), "old": "X = 1", "new": "X = 99"},   # valid
        {"file": str(f2), "old": "NOPE", "new": "Z"},          # stale → reject set
    ], verify=False)
    assert not r["ok"]
    assert f1.read_text().strip() == "X = 1"   # NOT written — atomic
    assert f2.read_text().strip() == "Y = 2"


def test_patch_set_rejects_syntax_break(workspace):
    f1 = workspace / "a.py"; f1.write_text("X = 1\n")
    r = _eng().apply_patch_set([{"file": str(f1), "old": "X = 1", "new": "X = (1"}], verify=False)
    assert not r["ok"] and f1.read_text().strip() == "X = 1"


def test_patch_set_rejects_outside_project():
    r = _eng().apply_patch_set([{"file": "/tmp/evil.py", "old": "a", "new": "b"}], verify=False)
    assert not r["ok"] and "outside" in r["message"]


def test_patch_set_applies_a_deletion(workspace):
    """A hunk that only removes lines has an empty new; the whole set used to be refused for it,
    so no edit that removes lines could ever be applied."""
    f1 = workspace / "a.py"; f1.write_text("X = 1\nY = 2\nZ = 3\n")
    r = _eng().apply_patch_set([{"file": str(f1), "old": "Y = 2\n", "new": ""}], verify=False)
    assert r["ok"], r
    assert f1.read_text() == "X = 1\nZ = 3\n"


def test_patch_set_refuses_an_ambiguous_old(workspace):
    """Text that occurs twice does not say which place it means; the first one was rewritten."""
    f1 = workspace / "a.py"; f1.write_text("def a():\n    x = 1\n\n\ndef b():\n    x = 1\n")
    r = _eng().apply_patch_set([{"file": str(f1), "old": "    x = 1\n", "new": "    x = 2\n"}], verify=False)
    assert not r["ok"] and "2 times" in r["message"]
    assert f1.read_text() == "def a():\n    x = 1\n\n\ndef b():\n    x = 1\n"


@pytest.mark.parametrize("rel", ["eli/runtime/authority_gate.py", "eli/core/full_control.py",
                                 "eli/runtime/self_improvement.py", "tests/test_coding_critique_multifile.py"])
def test_patch_set_refuses_the_guardrail_files(rel):
    """The multi-file path never checked the protected list the single-file path enforces."""
    from eli.core.paths import source_root
    target = Path(source_root()) / rel
    before = target.read_text(encoding="utf-8")
    line = next(l for l in before.splitlines(keepends=True) if l.strip() and before.count(l) == 1)
    try:
        r = _eng().apply_patch_set([{"file": rel, "old": line, "new": line.rstrip("\n") + "  # changed\n"}],
                                   verify=False)
        assert not r["ok"] and not r["applied"] and "protected" in r["message"]
    finally:
        if target.read_text(encoding="utf-8") != before:
            target.write_text(before, encoding="utf-8")
    assert not list(target.parent.glob(target.name + ".eli_bak*"))


def test_patch_set_runs_the_targeted_tests_and_rolls_back_on_red(workspace, monkeypatch):
    """One gate sequence for every write: the set path skipped the targeted tests."""
    import eli.runtime.self_improvement as si
    f1 = workspace / "a.py"; f1.write_text("X = 1\n")
    f2 = workspace / "b.py"; f2.write_text("Y = 2\n")
    ran = []
    monkeypatch.setattr(si, "_dotted_module_for_path", lambda p: None)
    monkeypatch.setattr(si, "_run_targeted_tests",
                        lambda p, timeout=120.0: (ran.append(p.name) or True, p.name != "b.py", "1 failed"))
    r = _eng().apply_patch_set([{"file": str(f1), "old": "X = 1", "new": "X = 10"},
                                {"file": str(f2), "old": "Y = 2", "new": "Y = 20"}], verify=True)
    assert not r["ok"] and "targeted tests" in r["message"] and ran
    assert f1.read_text() == "X = 1\n" and f2.read_text() == "Y = 2\n"

"""
test_06_runtime.py
==================
Tests for eli.runtime — agent, approvals, grounded remediation, self-improvement.
"""
import pytest


def test_the_self_model_in_every_prompt_does_not_name_elis_room():
    """Told on every turn "You are presently in your Anomaly Room", ELI opened a reply to a complaint
    with "I'm in the Anomaly Room, which is where I put myself when ..."."""
    from eli.runtime.awareness_boot import AwarenessState
    from eli.world.core.ontology import DEFAULT_ROOMS
    text = AwarenessState()._live_self_model().lower()
    assert "presently in your" not in text
    assert not any(str(room.get("name", "")).lower() in text for room in DEFAULT_ROOMS.values() if room.get("name"))


def test_git_queries_the_code_monitor_makes_are_answered_and_writes_are_not_run(tmp_path):
    """`git diff` and `git status` came back empty with success, so no code change was ever seen."""
    import shutil
    import subprocess
    import pytest
    if not shutil.which("git"):
        pytest.skip("git is not installed here")
    from eli.planning.proposal_adapters import safe_git_completed
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@example.invalid", "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}

    def git(*a):
        subprocess.run(["git", *a], cwd=repo, env=env, check=True, capture_output=True)
    git("init", "-q")
    (repo / "a.py").write_text("x = 1\n")
    git("add", "a.py")
    git("commit", "-q", "-m", "one")
    first = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, env=env, capture_output=True, text=True).stdout.strip()
    (repo / "a.py").write_text("x = 2\n")
    git("commit", "-q", "-am", "two")
    r = safe_git_completed(["git", "diff", "--name-status", first, "HEAD"], cwd=str(repo), timeout=30)
    assert r.returncode == 0 and r.stdout.split() == ["M", "a.py"]
    assert safe_git_completed(["git", "status", "--porcelain"], cwd=str(repo)).returncode == 0
    wrote = safe_git_completed(["git", "commit", "-am", "x"], cwd=str(repo))
    assert wrote.returncode != 0 and "not run" in wrote.stderr
    out = tmp_path / "leak.txt"
    assert safe_git_completed(["git", "diff", f"--output={out}"], cwd=str(repo)).returncode != 0 and not out.exists()


def test_what_the_code_monitor_saw_is_kept_in_the_data_folder_not_the_tree():
    """Kept in the source tree, every engine start in a test or an eval wrote .code_monitor_state.json
    into the checkout."""
    from eli.core.paths import data_dir, project_root
    from eli.runtime.code_monitor import CodeMonitor
    for mon in (CodeMonitor(), CodeMonitor(project_root())):        # awareness_boot names the install's root
        assert mon.state_path.parent == data_dir() / "runtime"

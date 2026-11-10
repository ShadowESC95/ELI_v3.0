"""
test_06_runtime.py
==================
Tests for eli.runtime — agent, approvals, grounded remediation, self-improvement.
"""
import importlib
import pytest


def _load(mod_path):
    return importlib.import_module(mod_path)


def test_runtime_package_init():
    assert _load("eli.runtime") is not None


def test_runtime_eli_agent_has_class():
    # The standalone eli.runtime.eli_agent module was removed; its runtime
    # dispatch role is now CognitiveEngine.process, reached via the headless
    # runtime entry point. Verify that entry exists.
    mod = _load("eli.cli.headless")
    assert callable(getattr(mod, "run_headless", None)), \
        f"no runtime dispatch entry (run_headless) in eli.cli.headless: {dir(mod)}"


def test_runtime_grounded_remediation_has_class():
    mod = _load("eli.runtime.grounded_remediation")
    syms = dir(mod)
    # Module is function-based: offer_for_result, try_handle_query are the public API
    matches = [s for s in syms if "Remediat" in s or "Grounded" in s
               or s in ("offer_for_result", "try_handle_query", "capture_executor_failure")]
    assert matches, f"No remediation class/function in grounded_remediation.py: {syms}"


def test_runtime_self_improvement_has_class():
    mod = _load("eli.runtime.self_improvement")
    syms = dir(mod)
    matches = [s for s in syms if "Improve" in s or "Self" in s]
    assert matches, f"No self-improvement class: {syms}"


def test_runtime_security_loadable():
    assert _load("eli.runtime.security") is not None


def test_runtime_approval_engine_loadable():
    assert _load("eli.runtime.approval_engine") is not None


def test_runtime_authority_gate_loadable():
    assert _load("eli.runtime.authority_gate") is not None



def test_runtime_awareness_boot_loadable():
    assert _load("eli.runtime.awareness_boot") is not None


def test_runtime_capability_sync_loadable():
    assert _load("eli.runtime.capability_sync") is not None


def test_runtime_code_monitor_loadable():
    assert _load("eli.runtime.code_monitor") is not None


def test_runtime_control_contracts_loadable():
    assert _load("eli.runtime.control_contracts") is not None


def test_runtime_evidence_arbitration_loadable():
    assert _load("eli.runtime.evidence_arbitration") is not None


def test_runtime_final_response_provider_loadable():
    assert _load("eli.runtime.final_response_provider") is not None


def test_runtime_incident_log_loadable():
    assert _load("eli.runtime.incident_log") is not None


def test_runtime_last_trace_loadable():
    assert _load("eli.runtime.last_trace") is not None


def test_runtime_live_introspection_loadable():
    assert _load("eli.runtime.live_introspection") is not None



def test_runtime_operator_feed_loadable():
    assert _load("eli.runtime.operator_feed") is not None



def test_runtime_operator_state_loadable():
    assert _load("eli.runtime.operator_state") is not None



def test_runtime_persistence_gate_loadable():
    assert _load("eli.runtime.persistence_gate") is not None


def test_runtime_personal_memory_surface_loadable():
    assert _load("eli.runtime.personal_memory_surface") is not None


def test_runtime_pipeline_models_loadable():
    assert _load("eli.runtime.pipeline_models") is not None


def test_runtime_profile_extractor_loadable():
    assert _load("eli.runtime.profile_extractor") is not None


def test_runtime_reflection_loadable():
    assert _load("eli.runtime.reflection") is not None



def test_runtime_response_contracts_loadable():
    assert _load("eli.runtime.response_contracts") is not None



def test_runtime_response_policy_loadable():
    assert _load("eli.runtime.response_policy") is not None



def test_runtime_self_model_refresh_loadable():
    assert _load("eli.runtime.self_model_refresh") is not None



def test_runtime_stage_packets_loadable():
    assert _load("eli.runtime.stage_packets") is not None


def test_runtime_stage_packet_store_loadable():
    assert _load("eli.runtime.stage_packet_store") is not None


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

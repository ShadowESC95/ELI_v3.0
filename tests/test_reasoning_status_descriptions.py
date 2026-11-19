from eli.cognition.reasoning_modes import mode_description
from eli.runtime.reasoning_status import current_reasoning_mode_text


MODES = [
    "quick",
    "chain_of_thought",
    "self_consistency",
    "tree_of_thoughts",
    "constitutional_ai",
]


def test_mode_descriptions_exist_and_are_distinct():
    descriptions = {mode: mode_description(mode) for mode in MODES}

    for mode, desc in descriptions.items():
        assert isinstance(desc, str), mode
        assert len(desc) > 80, mode

    assert len(set(descriptions.values())) == len(MODES)


def test_mode_descriptions_capture_core_contract_terms():
    assert "direct" in mode_description("quick").lower()
    assert "scratchpad" in mode_description("chain_of_thought").lower()
    assert "n-sample" in mode_description("self_consistency").lower()
    assert "propose" in mode_description("tree_of_thoughts").lower()
    assert "critique" in mode_description("constitutional_ai").lower()

    for mode in MODES[1:]:
        assert "private" in mode_description(mode).lower()


def test_reasoning_status_uses_mode_specific_description():
    for mode in MODES:
        text = current_reasoning_mode_text(override=mode, explain=True)
        assert "Current reasoning mode:" in text
        assert mode_description(mode) in text


def test_the_mode_is_never_read_out_of_an_old_trace_or_snapshot_file(tmp_path, monkeypatch):
    """With no turn, engine or setting to say, it took the value of any key named "mode" in a
    trace, state or snapshot file under the working directory: a past run's, or another fact's."""
    import json
    from eli.runtime import reasoning_status as rs
    (tmp_path / "artifacts" / "runtime").mkdir(parents=True)
    (tmp_path / "artifacts" / "runtime" / "last_trace.json").write_text(json.dumps({"reasoning_mode": "tree_of_thoughts"}))
    (tmp_path / "artifacts" / "runtime_snapshot.json").write_text(json.dumps({"mode": "constitutional_ai"}))
    monkeypatch.setenv("ELI_PROJECT_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    for name in ("ELI_REASONING_MODE", "ELI_ACTIVE_REASONING_MODE", "ELI_CURRENT_REASONING_MODE"):
        monkeypatch.delenv(name, raising=False)
    assert rs.current_reasoning_mode() == "quick"
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.json").write_text(json.dumps({"reasoning_mode": "Self Consistency"}))
    assert rs.current_reasoning_mode() == "self_consistency"
    assert rs.current_reasoning_mode_label() == "Advanced"
    assert rs.current_reasoning_mode_label(override="tot") == "Research"

"""Unit tests for eli.runtime.persistence_gate.

Verifies that error-state and system-prefix assistant responses are blocked
from being stored as conversation turns, preventing context contamination.
"""
import pytest
from eli.runtime.persistence_gate import (
    should_store_conversation_turn,
    should_store_memory_text,
)


class TestShouldStoreConversationTurn:

    # ---- Error responses that must NEVER reach the DB ----

    @pytest.mark.parametrize("text", [
        "[ELI] GGUF error: llama_load_model_from_file failed",
        "[ELI] Model not ready: GGUF init deferred until explicit model load. Check config/settings.json.",
        "[eli] model not ready: ...",                          # lowercase prefix
        "GGUF init deferred until explicit model load",
        "GGUF unavailable: no model configured",
        "GGUF streaming failed: connection refused",
        "No GGUF model found",
        "inference failed after 3 retries",
        "requested tokens exceed context window limit",
    ])
    def test_error_response_blocked_for_assistant(self, text):
        assert not should_store_conversation_turn("assistant", text), (
            f"Error message should be blocked but was stored: {text!r}"
        )

    # ---- Legitimate responses that must pass through ----

    @pytest.mark.parametrize("text", [
        "I remember you prefer coffee over tea.",
        "Here is how to configure your settings.",
        "Sure, the GGUF model context window is 32k tokens.",
        "The model has a 32k context window, great for long documents.",
        "I understand you need help with your project.",
    ])
    def test_legitimate_response_allowed_for_assistant(self, text):
        assert should_store_conversation_turn("assistant", text), (
            f"Legitimate response was incorrectly blocked: {text!r}"
        )

    # ---- Error text in user turn should still be stored ----

    def test_error_pattern_in_user_turn_is_stored(self):
        # Users can quote error messages; that's legitimate context.
        text = "I got this error: GGUF unavailable: no model loaded. What do I do?"
        assert should_store_conversation_turn("user", text)

    # ---- Empty / None ----

    def test_empty_string_blocked(self):
        assert not should_store_conversation_turn("assistant", "")

    def test_none_blocked(self):
        assert not should_store_conversation_turn("assistant", None)


class TestShouldStoreMemoryText:

    @pytest.mark.parametrize("text", [
        "[ELI] GGUF error: something went wrong",
        "GGUF init deferred until explicit model load",
        "GGUF unavailable: detail",
    ])
    def test_error_text_not_stored_as_memory(self, text):
        assert not should_store_memory_text(text, role="assistant"), (
            f"Error text should not become a memory: {text!r}"
        )

    def test_normal_fact_stored(self):
        assert should_store_memory_text(
            "Alex enjoys working on local AI systems.", role="user"
        )


def test_a_memory_write_goes_through_the_real_gate_in_a_test_run_too(tmp_path, monkeypatch):
    """store_memory skipped the gate whenever ELI_TEST_MODE was set, so no test ever reached the gate
    the product runs: a question or a greeting would have been kept, as far as any test could tell."""
    from eli.core.paths import test_mode
    from eli.memory import memory as memory_module
    from eli.memory.memory import Memory
    assert test_mode(), "this runs as a test"
    monkeypatch.setattr(memory_module, "_eli_should_store_memory_text", should_store_memory_text)
    mem = Memory(db_path=tmp_path / "user.sqlite3")
    for refused in ("where did I put the blue kettle?", "haha that is funny"):
        out = mem.store_memory(refused, source="user")
        assert out == {"ok": False, "skipped": True, "reason": "persistence_gate"}, refused
    kept = mem.store_memory("The blue kettle lives on the kitchen windowsill now.", source="user")
    assert kept.get("ok") and kept.get("id")
    conn = mem._get_connection()
    try:
        assert [r[0] for r in conn.execute("SELECT text FROM memories")] == [
            "The blue kettle lives on the kitchen windowsill now."]
    finally:
        conn.close()

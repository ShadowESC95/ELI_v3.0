"""Tests for eli.runtime.chat_honesty_gate — CHAT-path honesty hard gate."""
from __future__ import annotations

import os

import pytest

from eli.runtime.chat_honesty_gate import (
    UNCERTAINTY_MARKER,
    apply_chat_honesty_gate,
    has_hedge_marker,
)


def test_has_hedge_marker_detects_refusal():
    assert has_hedge_marker("I can't verify that right now.")
    assert has_hedge_marker(f"{UNCERTAINTY_MARKER} maybe")


def test_grounded_response_passes_through():
    text, mode, meta = apply_chat_honesty_gate(
        "Paris is the capital of France.",
        user_input="tell me about Paris",
        evidence_used=True,
    )
    assert text == "Paris is the capital of France."
    assert mode == "grounded"
    assert meta.get("honesty_gate") == "passed_evidence"


def test_ungrounded_factual_gets_hedge(monkeypatch):
    monkeypatch.setenv("ELI_CHAT_HONESTY_GATE", "1")
    text, mode, meta = apply_chat_honesty_gate(
        "Eminem's real name is Bruce Samuelson.",
        user_input="what is Eminem's real name",
        evidence_used=False,
        grounding_confidence=0.1,
    )
    assert mode == "ungrounded_hedge"
    assert UNCERTAINTY_MARKER in text
    assert has_hedge_marker(text)
    assert meta.get("honesty_gate") == "forced_hedge"


def test_banter_passes_without_hedge():
    text, mode, meta = apply_chat_honesty_gate(
        "Doing alright, thanks for asking.",
        user_input="how are you doing today",
        evidence_used=False,
        grounding_confidence=0.0,
    )
    assert mode == "synthesized"
    assert UNCERTAINTY_MARKER not in text


def test_mid_grounding_gets_uncertainty_marker(monkeypatch):
    monkeypatch.setenv("ELI_CHAT_HONESTY_GATE", "1")
    text, mode, meta = apply_chat_honesty_gate(
        "The population is about 67 million.",
        user_input="what is the population of France",
        evidence_used=False,
        grounding_confidence=0.60,
    )
    assert mode == "uncertain"
    assert text.startswith(UNCERTAINTY_MARKER)
    assert meta.get("honesty_gate") == "uncertainty_marker"


def test_gate_disabled_skips_forced_hedge(monkeypatch):
    monkeypatch.setenv("ELI_CHAT_HONESTY_GATE", "0")
    text, mode, _ = apply_chat_honesty_gate(
        "Made up fact.",
        user_input="what is Eminem's real name",
        evidence_used=False,
        grounding_confidence=0.0,
    )
    assert text == "Made up fact."
    assert mode == "synthesized"

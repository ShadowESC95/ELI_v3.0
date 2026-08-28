"""Tests for eli.runtime.response_pipeline — unified visible-text contract."""
from __future__ import annotations

import json

from eli.runtime.response_pipeline import finalize_response_text, to_user_visible


def test_to_user_visible_delegates_to_coercer():
    assert to_user_visible({"response": "hello"}) == "hello"


def test_finalize_response_text_strips_json_surface():
    packet = json.dumps({"surface": "identity_evidence"})
    out = finalize_response_text(packet, action="SELF_REPORT")
    assert not out.strip().startswith("{")
    assert "ELI" in out or "Identity" in out


def test_finalize_response_text_plain_string():
    assert finalize_response_text("plain answer") == "plain answer"

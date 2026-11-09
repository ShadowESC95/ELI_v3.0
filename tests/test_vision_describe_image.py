"""Looking at an image swaps the text model out and back without a lock-order freeze.

describe_image held the llama.cpp lock for the whole swap and restored the text model inside it. The
restore places the embedder first, under the vector store's embed lock, while an embed running at
that moment holds the embed lock and waits for the llama.cpp lock: both threads stopped for good, and
with them every model call. No model is loaded here: the swap's steps are stand-ins.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from eli.cognition import gguf_inference as gi
from eli.perception import vision


@pytest.fixture
def swap(tmp_path, monkeypatch):
    image = tmp_path / "shot.png"
    from PIL import Image
    Image.new("RGB", (4, 4), "white").save(image)
    seen = {"restored_under_lock": None, "order": []}

    class _VL:
        def create_chat_completion(self, **_kw):
            seen["order"].append("vision")
            return {"choices": [{"message": {"content": "a white square"}}]}

    def _restore(*_a, **_k):
        seen["order"].append("restore")
        seen["restored_under_lock"] = gi._LLM_CALL_LOCK._is_owned()

    monkeypatch.setattr(vision, "vision_available", lambda: (True, ""))
    monkeypatch.setattr(vision, "vision_settings", lambda: {
        "model_path": str(tmp_path / "vl.gguf"), "fast_model_path": "", "default_prompt": "describe",
        "max_tokens": 16, "temperature": 0.1, "fast_no_swap": False, "max_image_px": 64})
    monkeypatch.setattr(vision, "_load_vl", lambda *_a, **_k: _VL())
    monkeypatch.setattr(vision, "_close_vl", lambda _vl: seen["order"].append("close"))
    monkeypatch.setattr(vision, "_image_data_uri", lambda *_a, **_k: "data:image/png;base64,")
    monkeypatch.setattr(gi, "is_loaded", lambda: True)
    monkeypatch.setattr(gi, "unload_model", lambda: seen["order"].append("unload"))
    monkeypatch.setattr(gi, "load_model", _restore)
    return image, seen


def test_the_text_model_is_restored_after_the_llama_lock_is_let_go(swap):
    image, seen = swap
    out = vision.describe_image(str(image))
    assert out["ok"] is True and out["swapped"] is True
    assert seen["order"] == ["unload", "vision", "close", "restore"]
    assert seen["restored_under_lock"] is False, (
        "the text model was reloaded while holding the llama.cpp lock: its loader takes the embed lock, "
        "which an embed holds while it waits for the llama.cpp lock")


def test_a_failed_look_still_gives_the_text_model_back(swap, monkeypatch):
    image, seen = swap

    class _Broken:
        def create_chat_completion(self, **_kw):
            raise RuntimeError("decode failed")

    monkeypatch.setattr(vision, "_load_vl", lambda *_a, **_k: _Broken())
    out = vision.describe_image(str(image))
    assert out["ok"] is False and "decode failed" in out["error"]
    assert seen["order"][-1] == "restore" and seen["restored_under_lock"] is False

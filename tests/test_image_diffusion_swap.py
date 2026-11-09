"""Diffusion backend + VRAM swap: weights detection, missing-weights messaging,
and the unload-LLM → generate → reload-LLM ordering around a diffusion job.

Deterministic — no real model load; generate_batch and the GGUF loader are mocked.
"""
from __future__ import annotations

import pytest


from eli.tools.image_engine.image_engine.visual_core import weights_present
from eli.tools.image_engine import gui_bridge as GB
from eli.tools.image_engine import ImageGenerationRequest, generate_images


def test_weights_present(tmp_path):
    # configs-only scaffold → False
    (tmp_path / "model_index.json").write_text("{}", encoding="utf-8")
    assert weights_present(str(tmp_path)) is False
    # add a weight file → True
    (tmp_path / "unet").mkdir()
    (tmp_path / "unet" / "diffusion_pytorch_model.safetensors").write_bytes(b"x")
    assert weights_present(str(tmp_path)) is True
    assert weights_present(str(tmp_path / "missing")) is False


def test_missing_weights_returns_fetch_message(tmp_path):
    # A diffusion model dir with configs but NO weight files → clear fetch note,
    # no images, no crash. Use a temp scaffold (not the real models/image/ssd-1b,
    # whose weights may now be downloaded locally) so the missing-weights path is
    # exercised deterministically regardless of the machine.
    (tmp_path / "model_index.json").write_text("{}", encoding="utf-8")
    req = ImageGenerationRequest(prompt="a lake", backend="diffusion",
                                 model=str(tmp_path), count=1,
                                 width=512, height=512, seed=1)
    res = generate_images(req, settings={"image_auto_personalize": False})
    assert res.saved_paths == []
    assert any("no weights downloaded" in n for n in res.personalization_notes)
    assert any("fetch_model" in n for n in res.personalization_notes)


def test_vram_swap_order_when_weights_present(tmp_path, monkeypatch):
    # Fake a model dir with weights so the diffusion branch + swap engage.
    (tmp_path / "model_index.json").write_text("{}", encoding="utf-8")
    (tmp_path / "x.safetensors").write_bytes(b"x")

    calls = []
    monkeypatch.setattr(GB, "generate_batch", lambda args: (calls.append("generate"), [])[1])

    import eli.cognition.gguf_inference as gguf
    monkeypatch.setattr(gguf, "unload_model", lambda: calls.append("unload"))
    monkeypatch.setattr(gguf, "reload_model", lambda **k: calls.append("reload"))

    req = ImageGenerationRequest(prompt="a lake", backend="diffusion",
                                 model=str(tmp_path), count=1, width=512, height=512, seed=1)
    res = generate_images(req, settings={"image_auto_personalize": False})
    assert calls == ["unload", "generate", "reload"], calls
    assert res.saved_paths == []


def test_no_swap_for_procedural(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(GB, "generate_batch", lambda args: (calls.append("generate"), [])[1])
    import eli.cognition.gguf_inference as gguf
    monkeypatch.setattr(gguf, "unload_model", lambda: calls.append("unload"))
    monkeypatch.setattr(gguf, "reload_model", lambda **k: calls.append("reload"))

    req = ImageGenerationRequest(prompt="a lake", backend="procedural", count=1,
                                 width=512, height=512, seed=1)
    generate_images(req, settings={"image_auto_personalize": False})
    assert "unload" not in calls and "reload" not in calls
    assert calls == ["generate"]


# ── a size the model does not take ──────────────────────────────────────────────
class _Pipe:
    """Stands in for a diffusers pipeline: it refuses a size off its multiple, as SD/SDXL do."""
    vae_scale_factor = 8

    def __init__(self):
        self.asked = None

    def __call__(self, *, width, height, **_):
        from types import SimpleNamespace
        from PIL import Image
        m = 8 * (2 if hasattr(self, "_pack_latents") else 1)
        if width % m or height % m:
            raise ValueError(f"`height` and `width` have to be divisible by {m}")
        self.asked = (width, height)
        return SimpleNamespace(images=[Image.new("RGB", (width, height))])


class _FluxLikePipe(_Pipe):
    def _pack_latents(self):
        return None


def _backend(pipe):
    import types
    from eli.tools.image_engine.image_engine import visual_core as vc
    b = object.__new__(vc.DiffusionBackend)
    b.pipe, b.steps, b.guidance, b.device = pipe, 1, 1.0, "cpu"
    b.torch = types.SimpleNamespace(Generator=lambda device: types.SimpleNamespace(manual_seed=lambda s: None))
    return b


def _spec(width, height):
    from eli.tools.image_engine.image_engine import visual_core as vc
    return vc.SceneSpec(prompt="a lighthouse", negative_prompt="", scene_type="landscape", style="painted",
                        title="t", width=width, height=height, seed=1, variant=0, supersample=1,
                        palette=None, project_tags=[], output_format="png")


@pytest.mark.parametrize("pipe,made", [(_Pipe, (1400, 896)), (_FluxLikePipe, (1392, 896))])
def test_the_default_size_is_made_at_a_size_the_model_takes(pipe, made):
    """The GUI's default 1400x900 is not a multiple of 8, and every SDXL job ended in
    'Image generation failed'."""
    p = pipe()
    image = _backend(p).generate(_spec(1400, 900))
    assert p.asked == made and image.size == (1400, 900)

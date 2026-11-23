"""What the asset-release scripts may publish (scripts/github_asset_manifest.py,
scripts/create_github_asset_archives.sh, scripts/asset_release_policy.py).

Every git-ignored file under models/ was marked for upload, so a cloned voice's reference
recording, the speaker profile, wake-word models trained on the owner's voice and adapters
fine-tuned on their conversations were published with the models. The archive path tarred
models/ whole. (Voices are no longer published here: the voice is fetched from its own pinned upstream.)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import github_asset_manifest as manifest  # noqa: E402

from eli.core import model_download  # noqa: E402

_CATALOG_MODEL = model_download.CATALOG[0]["filename"]
_EMBEDDER = model_download.AUX_MODELS[0]["filename"]


@pytest.mark.parametrize("rel", [
    "models/voice_profiles/my_voice.wav",
    "models/voice_profile/speaker.npy",
    "models/wakeword/hey_there.pt",
    "models/lora/adapters/chat/adapter_model.safetensors",
    "models/lora_outputs_3/checkpoint/optimizer.pt",
    "models/eli-lora-adapter-2/adapter.bin",
    "models/eli-merged/model.gguf",
    "models/training_data/conversations.jsonl",
    "models/_download.log",
    "models/some_model.gguf.part",
])
def test_the_persons_own_files_beside_the_models_are_never_published(rel):
    row = manifest._classify(rel)
    assert row["category"] == "private_user_asset" and row["upload_recommended"] is False


def test_a_model_the_catalog_names_is_published_only_under_a_licence_eli_may_pass_on():
    """The embedder's licence (Apache-2.0) lets ELI v3 pass it on; a catalog model whose licence the
    catalog does not say, or says is non-commercial, is never published (asset_release_policy)."""
    assert manifest._classify(f"models/embeddings/{_EMBEDDER}")["upload_recommended"] is True
    import asset_release_policy as policy
    for entry in model_download.CATALOG:
        published = manifest._classify(f"models/{entry['filename']}")["upload_recommended"]
        assert published == policy.redistributable(entry), entry["key"]


@pytest.mark.parametrize("rel", ["models/some-other-download.bin", "models/notes.json",
                                 "models/tts/piper/en_US-ryan-medium.onnx"])
def test_anything_else_under_models_is_not(rel):
    assert manifest._classify(rel)["upload_recommended"] is False


@pytest.mark.skipif(sys.platform == "win32" or not shutil.which("git"), reason="the bash archive builder")
def test_the_archives_hold_only_what_the_manifest_publishes(tmp_path):
    tree = tmp_path / "checkout"
    (tree / "scripts").mkdir(parents=True)
    for name in ("create_github_asset_archives.sh", "github_asset_manifest.py", "asset_release_policy.py"):
        shutil.copy(ROOT / "scripts" / name, tree / "scripts" / name)
    (tree / ".gitignore").write_text("models/\ntts_kokoro/\ndist/\n", encoding="utf-8")
    import asset_release_policy as policy
    files = {
        f"models/embeddings/{_EMBEDDER}": True,
        f"models/{_CATALOG_MODEL}": policy.redistributable(model_download.CATALOG[0]),
        "models/voice_profiles/my_voice.wav": False,
        "models/wakeword/hey_there.pt": False,
        "tts_kokoro/model.onnx": False,
    }
    for rel in files:
        (tree / rel).parent.mkdir(parents=True, exist_ok=True)
        (tree / rel).write_bytes(b"\0" * 16)
    subprocess.run(["git", "init", "-q", str(tree)], check=True)
    py_bin = tmp_path / "bin"
    py_bin.mkdir()
    (py_bin / "python3").symlink_to(sys.executable)
    env = dict(os.environ, PATH=f"{py_bin}:{os.environ.get('PATH', '')}",
               PYTHONPATH=str(ROOT))           # the model catalog, from this checkout
    out = subprocess.run(["bash", str(tree / "scripts" / "create_github_asset_archives.sh")],
                         cwd=str(tree), env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    packed = set()
    upload = tree / "dist" / "github_assets" / "upload"
    for name in ("eli-model-assets",):
        parts = sorted(upload.glob(f"{name}.tar.gz.part-*"))
        joined = tmp_path / f"{name}.tar.gz"
        joined.write_bytes(b"".join(p.read_bytes() for p in parts))
        with tarfile.open(joined) as tar:
            packed |= {m.name for m in tar.getmembers() if m.isfile()}
    assert packed == {rel for rel, published in files.items() if published}

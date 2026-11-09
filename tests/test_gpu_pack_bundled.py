"""Bundled GPU pack discovery for offline-first frozen/portable installs."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging" / "pyinstaller"))
import eli_gpu_pack  # noqa: E402


def test_pick_bundled_wheel_prefers_cuda(tmp_path, monkeypatch):
    d = tmp_path / "gpu-packs"
    d.mkdir()
    vulkan = d / "vulkan-llama_cpp_python-0.3.35-py3-none-linux_x86_64.whl"
    cuda = d / "cuda-llama_cpp_python-0.3.35-py3-none-linux_x86_64.whl"
    vulkan.write_bytes(b"x")
    cuda.write_bytes(b"x")
    monkeypatch.setattr(eli_gpu_pack, "_bundled_gpu_dir", lambda: d)
    pick = eli_gpu_pack._pick_bundled_wheel(prefer_cuda=True)
    assert pick is not None
    assert pick[0] == "cuda"
    assert pick[2] == cuda


def test_pick_bundled_wheel_vulkan_when_no_cuda(tmp_path, monkeypatch):
    d = tmp_path / "gpu-packs"
    d.mkdir()
    vulkan = d / "vulkan-llama_cpp_python-0.3.35-py3-none-linux_x86_64.whl"
    vulkan.write_bytes(b"x")
    monkeypatch.setattr(eli_gpu_pack, "_bundled_gpu_dir", lambda: d)
    pick = eli_gpu_pack._pick_bundled_wheel(prefer_cuda=True)
    assert pick is not None
    assert pick[0] == "vulkan"


# ── a bundled pack never downloads on its own, and a failed one is not retried every start ──
import json  # noqa: E402
import zipfile  # noqa: E402

import pytest  # noqa: E402

_WIN = sys.platform == "win32"
_CUDA = "ggml-cuda.dll" if _WIN else "libggml-cuda.so"
_RUNTIME = ("cudart64_12.dll", "cublas64_12.dll") if _WIN else ("libcudart.so.12", "libcublas.so.12")


def _staged(tmp_path, *, with_runtime: bool) -> Path:
    lib = tmp_path / "unpacked" / "llama_cpp" / "lib"
    lib.mkdir(parents=True)
    (lib.parent / "__init__.py").write_text('__version__ = "0.3.36"\n', encoding="utf-8")
    for name in (_CUDA,) + (_RUNTIME if with_runtime else ()):
        (lib / name).write_bytes(b"\0")
    return lib.parents[1]


@pytest.fixture()
def no_download(monkeypatch, tmp_path):
    fetched = []

    def fetch(libdir, *_):     # stands in for the pypi.org download
        fetched.append(libdir)
        for name in _RUNTIME:
            (libdir / name).write_bytes(b"\0")

    monkeypatch.setattr(eli_gpu_pack, "_vendor_cuda_runtime", fetch)
    monkeypatch.setattr(eli_gpu_pack, "_verify", lambda dest, **k: (True, ""))
    monkeypatch.setenv("ELI_GPU_PACK_LOG", str(tmp_path / "gpu-pack.log"))
    return fetched


def test_a_pack_that_carries_the_nvidia_runtime_is_not_sent_to_pypi(tmp_path, no_download):
    """Every CUDA pack fetched cudart and cublas from pypi.org, even when the wheel had them."""
    dest = tmp_path / "runtime" / "gpu"
    rc = eli_gpu_pack._activate_staged_gpu_pack(
        _staged(tmp_path, with_runtime=True), dest=dest, backend="cuda", version="0.3.36", source="bundled")
    assert rc == 0 and (dest / ".gpu_pack_ok").is_file()
    assert no_download == []


def test_without_a_download_allowed_a_pack_missing_its_runtime_fails_and_says_why(tmp_path, no_download):
    rc = eli_gpu_pack._activate_staged_gpu_pack(
        _staged(tmp_path, with_runtime=False), dest=tmp_path / "runtime" / "gpu", backend="cuda",
        version="0.3.36", source="bundled", allow_download=False)
    assert rc == 1 and no_download == []
    assert "download" in eli_gpu_pack.last_failure()


def test_the_start_up_install_never_downloads_and_does_not_retry_a_bundle_that_failed(tmp_path, monkeypatch, no_download):
    """The start-up hook (bundle_only) runs before ELI's network guard; offline, it unpacked the
    whole bundled wheel and tried pypi.org again at every start."""
    import types

    root = tmp_path / "ELI_v3"
    packs = tmp_path / "gpu-packs"
    packs.mkdir()
    wheel = packs / ("cuda-llama_cpp_python-0.3.36-py3-none-" + ("win_amd64" if _WIN else "linux_x86_64") + ".whl")
    with zipfile.ZipFile(wheel, "w") as z:
        z.writestr("llama_cpp/__init__.py", '__version__ = "0.3.36"\n')
        z.writestr(f"llama_cpp/lib/{_CUDA}", b"\0")
    unpacked = []
    real_install = eli_gpu_pack._install_from_local_wheel
    monkeypatch.setattr(eli_gpu_pack, "_install_from_local_wheel",
                        lambda *a, **k: unpacked.append(k) or real_install(*a, **k))
    monkeypatch.setattr(eli_gpu_pack, "_eli_root", lambda: root)
    monkeypatch.setattr(eli_gpu_pack, "_bundled_gpu_dir", lambda: packs)
    monkeypatch.setattr(eli_gpu_pack, "_platform_tag", lambda: "win_amd64" if _WIN else "linux_x86_64")
    monkeypatch.setattr("eli.core.hardware_profile.detect_hardware",
                        lambda: types.SimpleNamespace(has_gpu=True, gpu_name="NVIDIA GeForce RTX"))

    assert eli_gpu_pack.ensure_gpu_pack_for_hardware(bundle_only=True) == 1
    assert eli_gpu_pack.ensure_gpu_pack_for_hardware(bundle_only=True) == 1
    assert no_download == [], "a download at start-up nobody asked for"
    assert len(unpacked) == 1, "the bundle that failed was unpacked again"
    failed = json.loads((root / "runtime" / ".gpu_bundle_failed").read_text(encoding="utf-8"))
    assert failed["wheel"] == wheel.name and "download" in failed["reason"]
    # an install the person asks for still may fetch it
    assert eli_gpu_pack.ensure_gpu_pack_for_hardware() == 0 and len(no_download) == 1
    assert not (root / "runtime" / ".gpu_bundle_failed").exists()

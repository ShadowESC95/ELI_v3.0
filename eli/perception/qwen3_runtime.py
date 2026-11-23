"""The isolated runtime Qwen3-TTS runs in, made on a machine whose GPU has room for it.

qwen-tts 0.1.1 needs transformers 4.57.3 and accelerate 1.12.0, older than ELI's own (its lock); on ELI's
it does not run. So it gets a Python environment of its own under ELI's data folder
(`runtime/qwen3_tts`), made the same way on every install route, the packaged apps included, which carry
no pip: ELI fetches a pinned uv (Astral's installer, MIT or Apache-2.0, a single program, checked against
its SHA-256), and uv makes the environment with a Python of its own, PyTorch for the machine's CUDA
(`--torch-backend auto`), the packages in `qwen3_runtime.txt`, and qwen-tts without its demo's gradio.
`eli/perception/qwen3_worker.py` runs in it; `tts_qwen3` starts that worker and speaks through it.

It is made only where it will be used: an NVIDIA card with room for the model beside the language model
(`tts_qwen3.room_for`, eli/perception/gpu_room). The installers and the first-run setup ask
(`python -m eli.perception.qwen3_runtime --if-room`), and so does ELI itself the first time the voice
engine is automatic or natural on such a machine (in the background; Kokoro speaks meanwhile). CPU-only
machines never fetch any of it. Everything is fetched while the network is allowed for it (netguard),
once; after that it runs offline.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import threading
import zipfile
from pathlib import Path
from typing import Any, Dict, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)

UV_VERSION = "0.10.7"
_UV_RELEASE = f"https://github.com/astral-sh/uv/releases/download/{UV_VERSION}/"
# (archive, SHA-256) per platform: GitHub's own digests of the release assets. macOS has no CUDA, so no
# runtime is made there; its entries are kept so the fetch itself is the same everywhere.
UV_ASSETS: Dict[str, tuple] = {
    "linux-x86_64": ("uv-x86_64-unknown-linux-gnu.tar.gz",
                     "9ac6cee4e379a5abfca06e78a777b26b7ba1f81cb7935b97054d80d85ac00774"),
    "linux-aarch64": ("uv-aarch64-unknown-linux-gnu.tar.gz",
                      "20efc27d946860093650bcf26096a016b10fdaf03b13c33b75fbde02962beea9"),
    "windows-amd64": ("uv-x86_64-pc-windows-msvc.zip",
                      "8881afb877996a1373a12e816395122a8d39a3ac06cd066272acdb49510cf0fe"),
    "darwin-arm64": ("uv-aarch64-apple-darwin.tar.gz",
                     "1eb4dcc5e0fc8669fa0b33cf1151b64ba3b8c26b60dceff4f7a686129e2af22b"),
    "darwin-x86_64": ("uv-x86_64-apple-darwin.tar.gz",
                      "4fed9d4f4608fb3850db714ee37244436f850a2b6e485bc510795679c2d08866"),
}
PYTHON = "3.12"
QWEN_TTS = "qwen-tts==0.1.1"
_REQUIREMENTS = Path(__file__).with_name("qwen3_runtime.txt")
WORKER = Path(__file__).with_name("qwen3_worker.py")
_READY = "ready.json"

_LOCK = threading.Lock()
_BACKGROUND: Optional[threading.Thread] = None
_STATE: Dict[str, Any] = {"state": "", "detail": ""}


def runtime_dir() -> Path:
    try:
        from eli.core.paths import data_dir
        return Path(data_dir()) / "runtime" / "qwen3_tts"
    except Exception:
        return Path("runtime") / "qwen3_tts"


def _windows() -> bool:
    return os.name == "nt"


def python_path(root: Optional[Path] = None) -> Path:
    venv = (root or runtime_dir()) / "venv"
    return venv / ("Scripts/python.exe" if _windows() else "bin/python")


def _platform_key() -> str:
    machine = platform.machine().lower()
    machine = {"amd64": "x86_64", "x64": "x86_64"}.get(machine, machine)
    if sys.platform.startswith("linux") and machine == "arm64":
        machine = "aarch64"
    if sys.platform.startswith("linux"):
        return f"linux-{machine}"
    if sys.platform == "win32":
        return "windows-amd64" if machine == "x86_64" else f"windows-{machine}"
    if sys.platform == "darwin":
        return f"darwin-{'arm64' if machine in ('arm64', 'aarch64') else machine}"
    return f"{sys.platform}-{machine}"


def ready(root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """What the runtime holds when it is made and checked (its ready.json), else None."""
    root = root or runtime_dir()
    try:
        info = json.loads((root / _READY).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not python_path(root).is_file() or not info.get("ok") or not info.get("cuda"):
        return None
    return info


def state() -> Dict[str, Any]:
    """{"state": "" | "making" | "ready" | "failed", "detail": ...} for the diagnostics."""
    if ready():
        return {"state": "ready", "detail": str(ready())}
    return dict(_STATE)


def _fetch_uv(root: Path) -> Path:
    """The pinned uv, fetched into the runtime folder and checked against its SHA-256."""
    name = "uv.exe" if _windows() else "uv"
    target = root / "bin" / name
    if target.is_file():
        return target
    asset = UV_ASSETS.get(_platform_key())
    if asset is None:
        raise RuntimeError(f"no uv build is pinned for {_platform_key()}")
    archive, sha = asset
    import urllib.request
    from eli.core import netguard
    req = urllib.request.Request(_UV_RELEASE + archive, headers={"User-Agent": "ELI-voice-runtime"})
    with netguard.allow_network("voice runtime: uv"):
        with netguard.guarded_urlopen(req, timeout=300) as r:
            body = r.read()
    if hashlib.sha256(body).hexdigest() != sha:
        raise ValueError(f"{archive} does not match its pinned checksum")
    target.parent.mkdir(parents=True, exist_ok=True)
    if archive.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            member = next(n for n in zf.namelist() if n.rsplit("/", 1)[-1] == name)
            target.write_bytes(zf.read(member))
    else:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tf:
            member = next(m for m in tf.getmembers() if m.isfile() and m.name.rsplit("/", 1)[-1] == name)
            target.write_bytes(tf.extractfile(member).read())
    target.chmod(0o755)
    return target


def _uv(uv: Path, root: Path, *args: str) -> None:
    """Run uv with its interpreters and cache inside the runtime folder; raises with its own words."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "VIRTUAL_ENV", "PIP_", "UV_"))}
    env.update(UV_PYTHON_INSTALL_DIR=str(root / "python"), UV_CACHE_DIR=str(root / "cache"),
               UV_NO_CONFIG="1", UV_PYTHON_PREFERENCE="only-managed")
    out = subprocess.run([str(uv), *args], env=env, capture_output=True, text=True)
    if out.returncode != 0:
        tail = (out.stderr or out.stdout or "").strip().splitlines()[-3:]
        raise RuntimeError(f"uv {args[0]} {args[1] if len(args) > 1 else ''}: " + " / ".join(tail))


def check(root: Optional[Path] = None) -> Dict[str, Any]:
    """Ask the runtime's own Python what it holds (qwen3_worker.py --check)."""
    root = root or runtime_dir()
    out = subprocess.run([str(python_path(root)), str(WORKER), "--check"], capture_output=True, text=True,
                         env={k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "VIRTUAL_ENV"))})
    try:
        return json.loads((out.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "error": (out.stderr or "no answer").strip()[-300:]}


def make(root: Optional[Path] = None, *, torch_backend: str = "", require_cuda: bool = True) -> Dict[str, Any]:
    """Make (or finish making) the runtime: uv, a Python, PyTorch for this machine's CUDA (uv's
    `--torch-backend auto`; ELI_QWEN3_TTS_TORCH_BACKEND names another, a ROCm build say), the pinned
    packages and qwen-tts; then check it. {"ok", "info" | "error"}. Network is allowed for it while it runs."""
    root = root or runtime_dir()
    backend = torch_backend or os.environ.get("ELI_QWEN3_TTS_TORCH_BACKEND", "").strip() or "auto"
    with _LOCK:
        if ready(root):
            return {"ok": True, "info": ready(root), "already": True}
        _STATE.update(state="making", detail="")
        try:
            from eli.core import netguard
            with netguard.allow_network("voice runtime: Qwen3-TTS"):
                root.mkdir(parents=True, exist_ok=True)
                uv = _fetch_uv(root)
                py = python_path(root)
                if not py.is_file():
                    _uv(uv, root, "venv", "--python", PYTHON, str(root / "venv"))
                _uv(uv, root, "pip", "install", "--python", str(py), "--torch-backend", backend, "torch", "torchaudio")
                _uv(uv, root, "pip", "install", "--python", str(py), "-r", str(_REQUIREMENTS))
                _uv(uv, root, "pip", "install", "--python", str(py), "--no-deps", QWEN_TTS)
            info = check(root)
            if not info.get("ok") or (require_cuda and not info.get("cuda")):
                raise RuntimeError(f"the runtime does not see CUDA or lacks a package: {info}")
            if require_cuda:
                (root / _READY).write_text(json.dumps(info, indent=1), encoding="utf-8")
            shutil.rmtree(root / "cache", ignore_errors=True)   # uv's downloads: the environment holds what it needs
            _STATE.update(state="ready", detail="")
            log.info("[TTS] Qwen3-TTS runtime ready: %s", info)
            return {"ok": True, "info": info}
        except Exception as exc:
            _STATE.update(state="failed", detail=f"{type(exc).__name__}: {exc}")
            log.warning("[TTS] the Qwen3-TTS runtime could not be made: %s", _STATE["detail"], exc_info=True)
            return {"ok": False, "error": _STATE["detail"]}


def make_if_room(*, background: bool = False) -> Dict[str, Any]:
    """Make the runtime when this machine has a GPU with room for Qwen3-TTS (tts_qwen3.room_for), else
    nothing. With `background`, on a thread of its own, once per run."""
    global _BACKGROUND
    from eli.perception import tts_qwen3
    if ready():
        return {"ok": True, "already": True}
    if tts_qwen3.room_for("custom") is None:
        return {"ok": False, "skipped": "no GPU on this machine has room for Qwen3-TTS beside the language model"}
    if not background:
        return make()
    with _LOCK:
        if _BACKGROUND is None or not _BACKGROUND.is_alive():
            _BACKGROUND = threading.Thread(target=make, daemon=True, name="eli-qwen3-runtime")
            _BACKGROUND.start()
    return {"ok": True, "started": True}


def remove() -> bool:
    shutil.rmtree(runtime_dir(), ignore_errors=True)
    return not runtime_dir().exists()


def _main(argv=None) -> int:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["--if-room"]:
        res = make_if_room()
    elif args[:1] == ["--check"]:
        res = check() if python_path().is_file() else {"ok": False, "error": "not made"}
    else:
        res = make()
    print(json.dumps(res, default=str))
    return 0 if res.get("ok") or res.get("skipped") else 1


if __name__ == "__main__":
    raise SystemExit(_main())

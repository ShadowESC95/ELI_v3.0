"""
ELI v3 — desktop GUI.

Runs whatever local GGUF model is configured (model-agnostic). Dockable
multi-panel interface: chat, memory, proactive suggestions, IDE, document
viewer, file browser, habits, coding, and the EliWorld view.
"""


from __future__ import annotations


import os


import sys


import json


import time


import threading


import traceback


import gc


import subprocess


import urllib.request


from pathlib import Path


from typing import Dict, Any, List, Optional, Tuple


from queue import Empty


from datetime import datetime


import re


import shutil


from collections import deque


from eli.utils.log import get_logger


log = get_logger(__name__)


_BOOT_PROJECT_ROOT = Path(__file__).resolve().parents[2]


if str(_BOOT_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_BOOT_PROJECT_ROOT))


try:
    from eli.core.paths import project_root as _eli_canonical_root
    _BOOT_PROJECT_ROOT = Path(_eli_canonical_root())
except Exception:
    import logging as _boot_logging
    _boot_logging.getLogger("eli.gui.boot").debug("suppressed exception", exc_info=True)


def _eli_path_get(obj, key, default=None):
    """
    Compatibility helper for ELI path containers.
    Accepts both dict-style path maps and object/namespace-style path maps.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


try:
    from PySide6.QtWidgets import *
    from PySide6.QtCore import *
    from PySide6.QtGui import *
    pyqtSignal = Signal
    pyqtSlot = Slot
    QT_VERSION = 6
    QT_API = "PySide6"
except ImportError:
    try:
        from PyQt6.QtWidgets import *
        from PyQt6.QtCore import *
        from PyQt6.QtGui import *
        QT_VERSION = 6
        QT_API = "PyQt6"
    except ImportError:
        try:
            from PyQt5.QtWidgets import *
            from PyQt5.QtCore import *
            from PyQt5.QtGui import *
            QT_VERSION = 5
            QT_API = "PyQt5"
        except ImportError:
            print("❌ Please install PySide6 (recommended), PyQt6, or PyQt5")
            sys.exit(1)


try:
    if QT_API == "PyQt6":
        from PyQt6.Qsci import QsciScintilla, QsciLexerPython
        QSCI_AVAILABLE = True
    elif QT_API == "PyQt5":
        from PyQt5.Qsci import QsciScintilla, QsciLexerPython
        QSCI_AVAILABLE = True
    else:
        QSCI_AVAILABLE = False
except ImportError:
    QSCI_AVAILABLE = False


if not QSCI_AVAILABLE:
    print("ℹ️  IDE using ELI's native code editor (line numbers, Python "
          "highlighting, auto-indent). QScintilla is used instead only when "
          "ELI runs under PyQt6/PyQt5.")


try:
    from eli.core import config
    from eli.core.paths import get_paths
    from eli.memory import get_memory, Memory
    from eli.cognition import gguf_inference
    from eli.planning.proactive_daemon import start_daemon
    CENTRAL_IMPORTS_AVAILABLE = True
    print("✅ Central ELI modules loaded.")
except ImportError as e:
    CENTRAL_IMPORTS_AVAILABLE = False
    print(f"⚠️  Central ELI modules not available – GUI will operate with limited functionality. Error: {e}")
    config = None
    get_paths = None
    get_memory = None
    gguf_inference = None
    Memory = None
    start_daemon = None


def _eli_gui_visible_text(result):
    from eli.runtime.visible_text import to_user_visible_text
    return to_user_visible_text(result)


APP_NAME = "ELI v3.0"


def _eli_app_version() -> str:
    """Version of the source ACTUALLY running. Portable installs run from the source tree
    (PYTHONPATH), which may be newer than the installed wheel, so read pyproject FIRST;
    fall back to installed-package metadata only if the source can't be found."""
    try:
        from eli.core.toml_util import load_toml
        from eli.core.paths import project_root
        v = load_toml(str(project_root() / "pyproject.toml"))["project"]["version"]
        if v:
            return v
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    try:
        from importlib.metadata import version, PackageNotFoundError
        for _pkg in ("eli-v3", "eli_v3", "eli"):
            try:
                return version(_pkg)
            except PackageNotFoundError:
                continue
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return "3.0"


APP_VERSION = _eli_app_version()


PROJECT_ROOT = _BOOT_PROJECT_ROOT


try:
    from eli.core.paths import config_dir as _config_dir
    APP_DIR = Path(_config_dir())
except Exception:
    APP_DIR = PROJECT_ROOT / "config"


SETTINGS_FILE = APP_DIR / "settings.json"


if CENTRAL_IMPORTS_AVAILABLE and get_paths:
    _paths = get_paths()
    MEMORY_DB = _eli_path_get(_paths, "memory_db")
    CONVERSATIONS_DIR = _paths.conversations_dir
    ARTIFACTS_DIR = _paths.artifacts_dir
    # No model filename is hardcoded — scan models/ for any .gguf at startup.
    # Returns "" when nothing is found so the heal loop doesn't fire on a stale
    # baked-in default and the GUI/startup-picker can prompt the user instead.
    def _eli_pick_any_bundled_model() -> str:
        try:
            root = PROJECT_ROOT / "models"
            if root.exists():
                for p in sorted(root.rglob("*.gguf")):
                    if p.is_file():
                        return str(p)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        return ""
    DEFAULT_MODEL_PATH = str(_paths.model) if _paths.model and _paths.model.exists() else _eli_pick_any_bundled_model()
    BUNDLED_MODEL_DIR = PROJECT_ROOT / "models"
    CUSTOM_MODELS_DIR = APP_DIR / "models"
else:
    MEMORY_DB = PROJECT_ROOT / "artifacts" / "db" / "user.sqlite3"
    CONVERSATIONS_DIR = PROJECT_ROOT / "artifacts" / "conversations"
    ARTIFACTS_DIR = PROJECT_ROOT / "artifacts"
    def _eli_pick_any_bundled_model() -> str:
        try:
            root = PROJECT_ROOT / "models"
            if root.exists():
                for p in sorted(root.rglob("*.gguf")):
                    if p.is_file():
                        return str(p)
        except Exception:
            log.debug("suppressed exception", exc_info=True)
        return ""
    DEFAULT_MODEL_PATH = _eli_pick_any_bundled_model()
    BUNDLED_MODEL_DIR = PROJECT_ROOT / "models"
    CUSTOM_MODELS_DIR = APP_DIR / "models"


if CENTRAL_IMPORTS_AVAILABLE and config:
    ELI_SYSTEM_PROMPT = config.get_eli_persona()
else:
    def _load_eli_persona() -> str:
        for p in [
            PROJECT_ROOT / "eli" / "brain" / "persona" / "persona.txt",
            PROJECT_ROOT / "eli" / "brain" / "cognition" / "persona.txt",
            PROJECT_ROOT / "config" / "persona.txt",
        ]:
            if p.exists():
                return p.read_text().strip()
        return "You are ELI, a helpful local AI assistant."
    ELI_SYSTEM_PROMPT = _load_eli_persona()
    # Strip any "prove it" remnants from dynamically loaded prompt
    ELI_SYSTEM_PROMPT = ELI_SYSTEM_PROMPT.replace('- "Now prove it." is your catchphrase.\n', '')
    ELI_SYSTEM_PROMPT = ELI_SYSTEM_PROMPT.replace('"Now prove it." is your catchphrase.\n', '')


MODEL_PROVIDER_LABELS = {
    "bundled_gguf": "Bundled GGUF",
    "custom_gguf": "Custom GGUF",
    "ollama": "Ollama",
}


def ensure_dirs():
    for d in [APP_DIR, CONVERSATIONS_DIR, ARTIFACTS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def now_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def format_gb(n: float) -> str:
    return f"{n:.1f} GB"


def classify_gguf_source(path: str) -> str:
    """Classify GGUF source for UI labels.

    - `bundled`: models shipped in `models/gguf/base`
    - `custom`: user-added models (including project-root `models/*.gguf`)
    """
    try:
        p = Path(str(path or "")).expanduser().resolve()
        bundled_root = BUNDLED_MODEL_DIR.expanduser().resolve()
        bundled_base = (BUNDLED_MODEL_DIR / "gguf" / "base").expanduser().resolve()
        custom_root = CUSTOM_MODELS_DIR.expanduser().resolve()

        if custom_root in p.parents:
            return "custom"
        if bundled_base in p.parents:
            return "bundled"
        if bundled_root in p.parents:
            # Models dropped directly into project `models/` are user-managed.
            return "custom"
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return "custom"


def recommend_model_setup(models, sysinfo, ollama_models=None):
    ollama_models = list(ollama_models or [])
    models = list(models or [])

    def _source_for_path(path: str) -> str:
        return "bundled_gguf" if classify_gguf_source(path) == "bundled" else "custom_gguf"

    if models:
        try:
            from eli.core.hardware_profile import detect_hardware as _hp_detect
            from eli.core.hardware_profile import recommend as _hp_recommend
            normalized = []
            for m in models:
                path_s = str(m.get("path") or "").strip()
                if not path_s:
                    continue
                p = Path(path_s).expanduser()
                if not p.exists():
                    continue
                try:
                    size_bytes = int(m.get("size_bytes") or p.stat().st_size)
                except Exception:
                    size_bytes = int(p.stat().st_size)
                size_gb = float(m.get("size_gb") or (size_bytes / 1e9))
                normalized.append({
                    "name": str(m.get("name") or p.name),
                    "path": str(p.resolve()),
                    "size_bytes": size_bytes,
                    "size_gb": size_gb,
                })

            if normalized:
                rec = _hp_recommend(_hp_detect(), normalized)
                chosen_path = str(rec.model_path or normalized[0]["path"])
                provider = _source_for_path(chosen_path)
                return {
                    "provider": provider,
                    "path": chosen_path,
                    "reason": (
                        f"Recommended {Path(chosen_path).name} "
                        f"(ctx={int(rec.n_ctx)}, gpu_layers={int(rec.n_gpu_layers)}, "
                        f"threads={int(rec.n_threads)}, batch={int(rec.batch_size)})"
                    ),
                }
        except Exception as e:
            log.debug(f"[SETTINGS] hardware-profile recommendation failed: {e}")

        # Fallback: smallest bundled model first, then smallest custom model.
        try:
            by_size = sorted(models, key=lambda m: float(m.get("size_gb") or 0.0))
            if by_size:
                chosen = by_size[0]
                chosen_path = str(chosen.get("path") or "")
                provider = _source_for_path(chosen_path)
                return {
                    "provider": provider,
                    "path": chosen_path,
                    "reason": f"Fallback selection: {Path(chosen_path).name}",
                }
        except Exception:
            log.debug("suppressed exception", exc_info=True)

    if ollama_models:
        return {
            "provider": "ollama",
            "ollama_model": ollama_models[0],
            "reason": f"No local GGUF detected. Recommended Ollama model: {ollama_models[0]}",
        }

    return {
        "provider": "custom_gguf",
        "path": DEFAULT_MODEL_PATH,
        "reason": "No automatic recommendation available. Using configured model path.",
    }


def discover_gguf_models(base_dirs: Optional[List[Path]] = None) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    seen: set[str] = set()
    base_dirs = base_dirs or [BUNDLED_MODEL_DIR, CUSTOM_MODELS_DIR]
    for base in base_dirs:
        try:
            base = Path(base).expanduser()
            if not base.exists():
                continue
            for path in sorted(base.rglob('*.gguf')):
                key = str(path.resolve())
                if key in seen:
                    continue
                seen.add(key)
                try:
                    size_gb = path.stat().st_size / (1024 ** 3)
                except Exception:
                    size_gb = 0.0
                family = 'unknown'
                name_low = path.name.lower()
                if 'mistral' in name_low:
                    family = 'mistral'
                elif 'qwen' in name_low:
                    family = 'qwen'
                elif 'phi' in name_low:
                    family = 'phi'
                results.append({
                    'name': path.name,
                    'path': str(path),
                    'size_gb': size_gb,
                    'family': family,
                    'source': classify_gguf_source(str(path)),
                })
        except Exception:
            continue
    results.sort(key=lambda x: (x['source'] != 'bundled', x['size_gb'], x['name'].lower()))
    return results


def detect_system_capabilities() -> Dict[str, Any]:
    info: Dict[str, Any] = {
        'platform': sys.platform,
        'cpu_count': os.cpu_count() or 1,
        'total_ram_gb': 0.0,
        'available_ram_gb': 0.0,
        'ollama_cli': False,
        'has_gpu': False,
        'gpu_name': '',
        'gpu_vendor': '',
        'gpu_integrated': False,
        'vulkan_available': False,
        'vram_mb': 0,
        'vram_total_mb': 0,
    }
    try:
        import psutil
        vm = psutil.virtual_memory()
        info['total_ram_gb'] = vm.total / (1024 ** 3)
        info['available_ram_gb'] = vm.available / (1024 ** 3)
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    try:
        import shutil
        info['ollama_cli'] = shutil.which('ollama') is not None
        from eli.core.hardware_profile import detect_hardware
        hw = detect_hardware()
        info['has_gpu'] = bool(hw.has_gpu)
        info['gpu_name'] = hw.gpu_name or ''
        info['gpu_vendor'] = getattr(hw, 'gpu_vendor', '') or ''
        info['gpu_integrated'] = bool(getattr(hw, 'gpu_integrated', False))
        info['vulkan_available'] = bool(getattr(hw, 'vulkan_available', False))
        info['vram_mb'] = int(hw.free_vram_mb or 0)
        info['vram_total_mb'] = int(hw.total_vram_mb or 0)
    except Exception:
        log.debug("suppressed exception", exc_info=True)
    return info


def recommend_optimal_settings(sysinfo: Dict[str, Any]) -> Dict[str, Any]:
    """Auto-detect sensible defaults from the current machine's hardware.

    All values are derived from live hardware metrics — no step tables.
    """
    ram_gb    = float(sysinfo.get('total_ram_gb', 8) or 8)
    vram_mb   = int(sysinfo.get('vram_mb', 0) or 0)
    cpu_count = int(sysinfo.get('cpu_count', 4) or 4)
    has_gpu   = bool(sysinfo.get('has_gpu', False))

    # n_ctx: proportional to available RAM (1 GB ≈ 1024 tokens), 2048-grain.
    _raw_ctx = max(4096, min(131072, int(ram_gb * 1024)))
    n_ctx = max(4096, (_raw_ctx // 2048) * 2048)

    # GPU layers: 9999 sentinel for "all layers" when VRAM is large;
    # proportional estimate otherwise (≈ 1 layer per 150 MB free VRAM).
    if not has_gpu or vram_mb <= 0:
        n_gpu_layers = 0
    elif vram_mb >= 8000:
        n_gpu_layers = 9999
    else:
        n_gpu_layers = min(99, max(4, vram_mb // 150))

    n_threads = max(1, cpu_count - 2)

    # Batch: proportional to free VRAM (≈ 1 unit per 10 MB), aligned to 64.
    if has_gpu and vram_mb > 0:
        _raw_batch = max(128, int(vram_mb / 10))
        batch_size = max(128, (_raw_batch // 64) * 64)
    else:
        batch_size = 128

    # max_tokens: half of ctx, clamped to [1024, 8192].
    max_tokens = max(1024, min(8192, n_ctx // 2))

    return {
        'n_ctx':        n_ctx,
        'n_gpu_layers': n_gpu_layers,
        'n_threads':    n_threads,
        'batch_size':   batch_size,
        'temperature':  0.7,
        'max_tokens':   max_tokens,
    }


def resolve_model_path(model_path: str) -> Path:
    p = Path(model_path).expanduser()
    if p.is_absolute():
        return p
    try:
        base = PROJECT_ROOT
    except NameError:
        base = Path(__file__).resolve().parents[3]
    return (base / p).resolve()


def _sanitize_identity_drift(text: str) -> str:
    return text


def _policy_identity_memory_response(user_text: str, model_text: str) -> str:
    return model_text


from eli.gui.panels.startup import HardwareTuningDock


from eli.gui.panels.startup import StartupModelSelectionDialog, FirstBootWizard


from eli.gui.panels.agent_wizard import AgentEditDialog


from eli.gui.panels.settings import AdvancedSettingsDialog


if __name__ == "__main__":
    main()


__all__ = [_n for _n in dir() if not _n.startswith('__')]

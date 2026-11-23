"""Best-effort pip bootstrap for optional ELI runtime and test packages.

Installers (`install.sh`, `install.ps1`) and `run_tests.sh` pre-install these
deps into the project venv. When someone runs pytest or starts the API server
with a bare interpreter, this module can self-heal by pip-installing the same
pinned lists — no machine-specific paths or manual copy-paste steps.
"""
from __future__ import annotations

import importlib
import os
import subprocess
import sys
from typing import Sequence

from eli.core.paths import project_root as _project_root
from eli.utils.log import get_logger

log = get_logger(__name__)

# Keep in sync with pyproject.toml: the server's packages are in its dependencies, the tests' in the test extra.
_SERVER_MODULES = (
    "fastapi",
    "uvicorn",
    "httpx",
    "paho",
    "zeroconf",
    "segno",
    "cryptography",
)
_TEST_MODULES = (
    "fastapi",
    "uvicorn",
    "httpx",
    "pytest",
    "pyflakes",
)


def _bootstrap_disabled() -> bool:
    return str(os.environ.get("ELI_NO_PACKAGE_BOOTSTRAP", "")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def missing_modules(modules: Sequence[str]) -> list[str]:
    out: list[str] = []
    for name in modules:
        root = str(name or "").split(".", 1)[0].strip()
        if not root:
            continue
        try:
            importlib.import_module(root)
        except Exception:
            out.append(root)
    return out


def pip_install(*packages: str, quiet: bool = True, timeout_s: int = 600) -> bool:
    if not packages:
        return True
    cmd = [sys.executable, "-m", "pip", "install"]
    if quiet:
        cmd.append("--quiet")
    cmd.extend(packages)
    try:
        subprocess.check_call(cmd, timeout=timeout_s)
        return True
    except Exception:
        log.debug("package_bootstrap: pip install failed for %s", packages, exc_info=True)
        return False


def pip_install_requirements(filename: str, *, quiet: bool = True) -> bool:
    path = _project_root() / filename
    if not path.is_file():
        log.warning("package_bootstrap: requirements file missing: %s", path)
        return False
    cmd = [sys.executable, "-m", "pip", "install", "-r", str(path)]
    if quiet:
        cmd.append("--quiet")
    try:
        subprocess.check_call(cmd, timeout=900)
        return True
    except Exception:
        log.debug("package_bootstrap: requirements install failed for %s", path, exc_info=True)
        return False


def ensure_test_packages(*, force: bool = False) -> bool:
    """Install test-suite deps (API/RBAC drivers, lint gates, MQTT probe)."""
    if _bootstrap_disabled() and not force:
        return not missing_modules(_TEST_MODULES)
    missing = missing_modules(_TEST_MODULES)
    if not missing:
        return True
    log.info("Installing test dependencies: %s", ", ".join(missing))
    if pip_install_requirements("requirements-test.txt"):
        return not missing_modules(_TEST_MODULES)
    return pip_install(
        "fastapi>=0.110",
        "uvicorn[standard]>=0.27",
        "httpx>=0.27",
        "pytest>=7.0",
        "pyflakes>=3.0",
        "cryptography>=41",
        "paho-mqtt>=1.6,<3",
    ) and not missing_modules(_TEST_MODULES)


def ensure_server_packages(*, force: bool = False) -> bool:
    """Install REST/web-app server deps (fastapi, uvicorn, LAN HTTPS helpers)."""
    if _bootstrap_disabled() and not force:
        return not missing_modules(_SERVER_MODULES)
    missing = missing_modules(_SERVER_MODULES)
    if not missing:
        return True
    log.info("Installing server dependencies: %s", ", ".join(missing))
    ok = pip_install(
        "fastapi>=0.110",
        "uvicorn[standard]>=0.27",
        "httpx>=0.27",
        "paho-mqtt>=1.6,<3",
        "zeroconf>=0.70",
        "bleak>=0.21",
        "segno>=1.5",
        "cryptography>=41",
        "python-multipart>=0.0.9",
    )
    return ok and not missing_modules(_SERVER_MODULES)

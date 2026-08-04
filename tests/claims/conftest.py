"""Claims suite bootstrap — fresh clones self-verify without manual steps.

The artifacts this suite examines (``capability_manifest.json`` chief among them) are
generated, not committed, so a fresh clone has none.

This used to be an autouse *session fixture*, which could never do the job: six modules
in this directory read the manifest at **module level** to parametrise their tests
(``_MANIFEST_ACTIONS = {...}`` and friends), and module level runs during collection —
before any fixture fires. On a fresh clone the directory therefore errored out with
``FileNotFoundError`` during collection instead of bootstrapping itself.

Doing it at conftest import time fixes the ordering: pytest imports a directory's
conftest before the test modules in that directory, so the artifacts exist by the time
those module-level reads happen. The session fixture is kept as well, so the artifacts
are also refreshed for a warm tree exactly as before.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_MANIFEST = _REPO / "capability_manifest.json"


def _run_bootstrap() -> subprocess.CompletedProcess | None:
    script = _REPO / "tools" / "bootstrap_claims_artifacts.py"
    if not script.is_file():
        return None
    return subprocess.run([sys.executable, str(script)], cwd=str(_REPO),
                          capture_output=True, text=True)


def ensure_claims_artifacts() -> None:
    """Generate the claims artifacts when they are missing.

    A no-op on a warm tree, so the common case costs one stat call. Raises rather than
    skipping: a clone that cannot generate what it claims to verify is a real failure,
    not a reason to quietly report success over zero tests.
    """
    if _MANIFEST.is_file():
        return
    proc = _run_bootstrap()
    if proc is None:
        raise RuntimeError(
            "capability_manifest.json is missing and tools/bootstrap_claims_artifacts.py "
            "is not present, so the claims suite cannot generate what it verifies."
        )
    if proc.returncode != 0 or not _MANIFEST.is_file():
        msg = (proc.stderr or proc.stdout or "bootstrap failed").strip()
        raise RuntimeError(f"claims bootstrap failed: {msg}")


# Runs before pytest imports the test modules in this directory — which is the whole
# point; see the module docstring.
ensure_claims_artifacts()


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_claims_artifacts():
    """Refresh the artifacts for the session (unchanged behaviour on a warm tree)."""
    proc = _run_bootstrap()
    if proc is None:
        pytest.skip("bootstrap_claims_artifacts.py missing")
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "bootstrap failed").strip()
        pytest.fail(f"claims bootstrap failed: {msg}")

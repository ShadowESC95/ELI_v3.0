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


def manifest_file() -> Path:
    """The manifest the run generates once and the claims read: in a folder of its own in ELI's data
    folder (the run's sandbox). Generated at the checkout's root, what a build bundles, a test run
    left it and the inventory in the checkout. Not where an engine start regenerates its own
    (capability_updater.manifest_path): a test's engine rewrites that one with what the test changed."""
    from eli.core.paths import data_dir
    return Path(data_dir()) / "claims" / "capability_manifest.json"


def _run_bootstrap() -> subprocess.CompletedProcess | None:
    script = _REPO / "tools" / "bootstrap_claims_artifacts.py"
    if not script.is_file():
        return None
    return subprocess.run([sys.executable, str(script), "--out", str(manifest_file().parent)],
                          cwd=str(_REPO), capture_output=True, text=True)


_BOOTSTRAPPED: list = []


def ensure_claims_artifacts() -> None:
    """Generate the claims artifacts, or bring them up to what the code holds now, once per run.

    It ran only when the manifest was missing, so a manifest an older run had generated was what
    collection read (module-level parametrisation) for the whole run: after a new action, the
    manifest claims failed in the full run and passed alone once a later test had rewritten the
    file. Raises rather than skipping: a clone that cannot generate what it claims to verify is a
    real failure, not a reason to quietly report success over zero tests.
    """
    if _BOOTSTRAPPED:
        return
    proc = _run_bootstrap()
    _BOOTSTRAPPED.append(proc)
    if manifest_file().is_file():
        return                  # anything else the bootstrap reported fails the session (fixture below)
    if proc is None:
        raise RuntimeError(
            "capability_manifest.json is missing and tools/bootstrap_claims_artifacts.py "
            "is not present, so the claims suite cannot generate what it verifies."
        )
    msg = (proc.stderr or proc.stdout or "bootstrap failed").strip()
    raise RuntimeError(f"claims bootstrap failed: {msg}")


# Runs before pytest imports the test modules in this directory — which is the whole
# point; see the module docstring and ensure_claims_artifacts.
ensure_claims_artifacts()


@pytest.fixture(scope="session", autouse=True)
def _bootstrap_claims_artifacts():
    """Report how the bootstrap that ran before collection went (it is not run a second time)."""
    proc = _BOOTSTRAPPED[0] if _BOOTSTRAPPED else None
    if proc is None:
        pytest.skip("bootstrap_claims_artifacts.py missing")
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "bootstrap failed").strip()
        pytest.fail(f"claims bootstrap failed: {msg}")

"""Whether this copy of ELI v3's code is exactly what was released.

A copy installed from source (the wheel, the .deb, the portable folders and tarballs) is Python
files anyone can open in a text editor. The release build lists the SHA-256 of the files that
decide whether ELI may run (this package, and every entry point that asks it), signs the list
with ELI's release key and ships it as ``eli/RELEASE.manifest``. Before ELI runs, those files
are checked against it: a file changed, removed or added, or the manifest missing, and ELI
refuses to start, saying the copy has been altered and should be reinstalled. The check is made
from several places (the launch gate, the licence status, each bundle's licence), so taking one
out is not enough.

A frozen build (the Windows installer, the AppImage, the macOS app) keeps its code inside the
executable and is not checked here. Nor is the developer's own git checkout.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

MANIFEST = "RELEASE.manifest"
PRODUCT = "eli"
# What decides whether ELI may run, relative to the folder holding the ``eli`` package.
COVERED = (
    "eli/licensing",
    "eli/__init__.py",
    "eli/__main__.py",
    "eli/gui/app.py",
    "eli/gui/license_dialog.py",
    "eli/bundles/__init__.py",
    "eli/integrations/mcp/server.py",
    "eli/core/paths.py",
)


def package_dir() -> Path:
    """The ``eli`` package folder this code runs from."""
    return Path(__file__).resolve().parents[1]


def _version() -> str:
    try:
        from importlib.metadata import version

        return version("eli-v3")
    except Exception:
        pass
    try:
        import tomllib

        data = tomllib.loads((package_dir().parent / "pyproject.toml").read_text(encoding="utf-8"))
        return str(data["project"]["version"])
    except Exception:
        return ""


def checked() -> bool:
    """Whether this copy is one the check applies to: installed from source, not frozen and not
    the developer's checkout."""
    from eli.core.paths import is_frozen
    from eli.licensing.activation import developer_checkout

    return not is_frozen() and not developer_checkout()


@lru_cache(maxsize=1)
def problems() -> tuple[str, ...]:
    """What is wrong with this copy's code, or () when it is what was released (or not checked)."""
    try:
        if not checked():
            return ()
        from eli.licensing._sdk.integrity import check_tree, find_manifest

        token = find_manifest(package_dir(), (MANIFEST,))
        return tuple(check_tree(package_dir().parent, token, product=PRODUCT, version=_version()))
    except Exception as exc:  # a check that cannot run is a failed check, never a pass
        log.debug("integrity check failed to run", exc_info=True)
        return (f"the integrity check could not run ({exc})",)


def refusal() -> str:
    """Why ELI refuses to start, or "" when the copy is intact."""
    found = problems()
    if not found:
        return ""
    return ("This copy of ELI v3 has been altered or is incomplete, so it will not start: "
            + "; ".join(found) + ". Reinstall ELI v3 from its release.")

"""Whether a product's installed code is exactly what was released.

A product shipped as Python source (a wheel, a .deb of source, a portable folder) can be opened
in a text editor. Its release build lists the SHA-256 of every Python file it ships, signs that
list with ELI's *release* key (not the licence key: the release key lives in the release
pipeline, the licence key never does), and puts the signed list beside the code. At start the
product checks its files against it: a file changed, removed or added makes the check fail,
and the product refuses to start, saying the copy has been altered and should be reinstalled.

What this stops: deleting or editing a licence check, or dropping in a module to replace one.
What it cannot stop: someone who also finds and removes every place the check is made. A
frozen build (PyInstaller) keeps its code inside the executable, where none of this is a
matter of a text editor.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path
from typing import Any

from ._encoding import b64url_decode, b64url_encode, canonical_json
from .errors import InvalidKey

PREFIX = "ELIMAN-"
MANIFEST_NAME = "RELEASE.manifest"
_SUFFIXES = (".py",)
_SKIP_DIRS = {"__pycache__"}


def release_public_key():
    """ELI's release-signing key, which every release manifest is checked against."""
    from .keys import load_public_key

    return load_public_key(files(__package__ or "eli_sdk").joinpath("release.pub").read_bytes())


def _covered(root: Path, packages: Sequence[str]) -> list[Path]:
    out: list[Path] = []
    for package in packages:
        base = root / package
        if base.is_file():
            out.append(base)
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in _SUFFIXES and not (_SKIP_DIRS & set(path.parts)):
                out.append(path)
    return sorted(set(out))


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(root: str | Path, packages: Sequence[str], *, product: str,
                   version: str) -> dict[str, Any]:
    """Every Python file under ``packages`` (folders or single files, relative to ``root``)."""
    base = Path(root)
    return {
        "type": "eli-release-manifest",
        "product": product,
        "version": version,
        "packages": list(packages),
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "files": {p.relative_to(base).as_posix(): _digest(p) for p in _covered(base, packages)},
    }


def sign_manifest(manifest: dict[str, Any], private_key: Any) -> str:
    from .issuing import load_private_key

    raw = canonical_json(manifest)
    return f"{PREFIX}{b64url_encode(raw)}.{b64url_encode(load_private_key(private_key).sign(raw))}"


def read_manifest(token: str, public_key: Any = None) -> dict[str, Any]:
    """Verify a signed manifest and return it. Raises InvalidKey."""
    from cryptography.exceptions import InvalidSignature

    from .keys import load_public_key

    text = str(token or "").strip()
    if not text.startswith(PREFIX) or text.count(".") != 1:
        raise InvalidKey("not a release manifest")
    body_part, sig_part = text[len(PREFIX):].split(".", 1)
    try:
        body, signature = b64url_decode(body_part), b64url_decode(sig_part)
    except ValueError:
        raise InvalidKey("the release manifest is not valid base64") from None
    verifier = release_public_key() if public_key is None else load_public_key(public_key)
    try:
        verifier.verify(signature, body)
    except InvalidSignature:
        raise InvalidKey("the release manifest is not signed by ELI") from None
    manifest = json.loads(body.decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("type") != "eli-release-manifest":
        raise InvalidKey("not a release manifest")
    return manifest


def check_tree(root: str | Path, token: str | None, *, product: str, version: str,
               public_key: Any = None, limit: int = 5) -> list[str]:
    """What is wrong with the installed code, or [] when it is exactly what was released.

    ``token`` is the signed manifest's text; None (no manifest found) is itself a problem.
    """
    if not token:
        return ["the release manifest is missing"]
    try:
        manifest = read_manifest(token, public_key)
    except (InvalidKey, ValueError) as exc:
        return [str(exc)]
    problems: list[str] = []
    if manifest.get("product") != product:
        problems.append(f"the manifest is for {manifest.get('product')!r}, not {product!r}")
    if str(manifest.get("version")) != str(version):
        problems.append(f"the manifest is for version {manifest.get('version')}, not {version}")
    base = Path(root)
    listed: dict[str, str] = dict(manifest.get("files") or {})
    changed = [name for name, digest in sorted(listed.items())
               if not (base / name).is_file() or _digest(base / name) != digest]
    present = {p.relative_to(base).as_posix() for p in _covered(base, manifest.get("packages") or [])}
    added = sorted(present - set(listed))
    problems += [f"changed or removed: {name}" for name in changed[:limit]]
    problems += [f"not part of the release: {name}" for name in added[:limit]]
    extra = len(changed) + len(added) - min(len(changed), limit) - min(len(added), limit)
    if extra > 0:
        problems.append(f"and {extra} more")
    return problems


def find_manifest(root: str | Path, names: Iterable[str] = (MANIFEST_NAME,)) -> str | None:
    for name in names:
        path = Path(root) / name
        if path.is_file():
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError:
                return None
    return None

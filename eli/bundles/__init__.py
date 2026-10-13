"""Bundles: separate ELI applications that run on ELI v3's core in their own window.

A bundle is a specialised product (ELI Legal and ELI VR now; Healthcare, Finance and others to
come) that has its own window and its own rules, and uses parts of ELI v3: its local model runtime
in particular. ELI v3 can start one *instead of* its full window, at start-up or with
``eli --bundle <id>``, or alongside it from the Bundles menu.

Two ways a bundle makes itself known, and nothing else is ever loaded:

``in-process``  a Python package installed in ELI v3's environment that advertises itself under
                the ``eli.bundles`` entry-point group. It runs inside ELI v3's process, so it can
                use ELI v3's loaded model. Its entry point returns a description (see
                ``Bundle.from_description``) whose ``launch`` names the function to call.
``external``    a separately installed application (an AppImage, an installed program) that
                registered itself by writing ``<id>.json`` into a ``bundles`` folder of ELI v3's
                data. It runs as its own program with its own settings.

``ELI_EXTRA_BUNDLES`` (``module:attr`` entries, separated by commas) adds in-process bundles from
a source checkout that is on the path but not installed, for development.

A bundle that is not a Python program reaches the core over ELI's local server. It asks for it
with ``"needs": ["server"]`` and ELI has the server answering before the bundle starts (see
``eli.bundles.core_server``).

Licensing: a bundle names the edition it belongs to. Whether a licence entitles it is decided by
``entitled``, the same seam as ``eli.editions.active_editions``; until licences carry editions it
allows every bundle, and says so.
"""

from __future__ import annotations

import contextlib
import importlib
import json
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

log = logging.getLogger("eli.bundles")

GROUP = "eli.bundles"
CONTRACT = 1


@dataclass(frozen=True)
class Bundle:
    id: str
    title: str
    summary: str = ""
    version: str = ""
    edition: str = ""
    icon: str = ""
    kind: str = "in-process"  # in-process | external
    launch: str = ""  # in-process: "module:function"
    command: tuple[str, ...] = ()  # external: the program and its arguments
    origin: str = ""  # where it was found
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_description(
        cls, raw: dict[str, Any], *, origin: str, kind: str = "in-process"
    ) -> Bundle:
        if int(raw.get("contract", CONTRACT)) > CONTRACT:
            raise ValueError(
                f"bundle contract {raw.get('contract')} is newer than this ELI ({CONTRACT})"
            )
        bundle_id = str(raw["id"]).strip().lower()
        if not bundle_id or not bundle_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError(f"bad bundle id {raw.get('id')!r}")
        command = tuple(str(c) for c in raw.get("command") or ())
        if kind == "external" and not command:
            raise ValueError("an external bundle must give the command that starts it")
        if kind == "in-process" and ":" not in str(raw.get("launch", "")):
            raise ValueError(
                "an in-process bundle must name its launch function as module:function"
            )
        return cls(
            id=bundle_id,
            title=str(raw.get("title") or bundle_id),
            summary=str(raw.get("summary") or ""),
            version=str(raw.get("version") or ""),
            edition=str(raw.get("edition") or bundle_id),
            icon=str(raw.get("icon") or ""),
            kind=kind,
            launch=str(raw.get("launch") or ""),
            command=command,
            origin=origin,
            extra={
                k: v
                for k, v in raw.items()
                if k
                not in {
                    "id",
                    "title",
                    "summary",
                    "version",
                    "edition",
                    "icon",
                    "launch",
                    "command",
                    "contract",
                }
            },
        )


def _describe(target: Any) -> dict[str, Any]:
    value = target() if callable(target) else target
    if not isinstance(value, dict):
        raise TypeError(
            "a bundle entry point must give a dict, or a function returning one"
        )
    return value


def registry_dirs() -> list[Path]:
    """Folders where separately installed bundles register themselves."""
    out: list[Path] = []
    try:
        from eli.core.paths import data_dir

        out.append(data_dir() / "bundles")
    except Exception:
        log.debug("data_dir unavailable", exc_info=True)
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    out.append(base / "ELI_v3" / "bundles")
    unique: list[Path] = []
    for p in out:
        if p not in unique:
            unique.append(p)
    return unique


def discover() -> dict[str, Bundle]:
    """Every bundle that can be started here, by id. One that cannot be read is skipped and
    logged; it never stops ELI starting."""
    found: dict[str, Bundle] = {}

    def add(bundle: Bundle) -> None:
        if bundle.id in found and found[bundle.id].kind == "in-process":
            return  # an installed package is preferred: it can use ELI's own model
        found[bundle.id] = bundle

    try:
        eps = entry_points(group=GROUP)
    except TypeError:  # Python 3.9 style
        eps = entry_points().get(GROUP, [])  # type: ignore[attr-defined]
    for ep in eps:
        try:
            add(
                Bundle.from_description(
                    _describe(ep.load()), origin=f"package {ep.value}"
                )
            )
        except Exception:
            log.warning(
                "bundle entry point %s could not be loaded", ep.name, exc_info=True
            )
    for spec in [
        s.strip()
        for s in os.environ.get("ELI_EXTRA_BUNDLES", "").split(",")
        if s.strip()
    ]:
        try:
            module, _, attr = spec.partition(":")
            add(
                Bundle.from_description(
                    _describe(
                        getattr(importlib.import_module(module), attr or "describe")
                    ),
                    origin=f"ELI_EXTRA_BUNDLES {spec}",
                )
            )
        except Exception:
            log.warning("extra bundle %s could not be loaded", spec, exc_info=True)
    for folder in registry_dirs():
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                bundle = Bundle.from_description(raw, origin=str(path), kind="external")
                if not Path(bundle.command[0]).exists():
                    continue  # the program was removed since it registered
                add(bundle)
            except Exception:
                log.warning(
                    "bundle registration %s could not be read", path, exc_info=True
                )
    return dict(sorted(found.items()))


def entitled(bundle: Bundle) -> tuple[bool, str]:
    """Whether this installation's licence lets it start the bundle.

    The same seam as ``eli.editions.active_editions``: licences do not yet carry editions, so
    every bundle is allowed. When they do, look up ``bundle.edition`` in the licence here.
    """
    return True, "licences do not yet carry editions; every bundle is allowed"


def _host() -> dict[str, Any]:
    try:
        from importlib.metadata import version as _dist_version

        version = _dist_version("eli-v3")
    except Exception:  # noqa: BLE001 - the version is informative only
        version = ""
    return {
        "name": "ELI v3",
        "version": str(version),
        "root": os.environ.get("ELI_PROJECT_ROOT", ""),
    }


def _needs_server(bundle: Bundle) -> bool:
    return "server" in (bundle.extra.get("needs") or ())


@contextlib.contextmanager
def _core(bundle: Bundle) -> Iterator[int | None]:
    """What the bundle asked of ELI's core, for as long as it runs: the port of ELI's local
    server, or None when it asked for nothing or the server could not be had."""
    if not _needs_server(bundle):
        yield None
        return
    from eli.bundles import core_server

    with core_server.serving() as port:
        yield port


def launch(bundle_id: str, argv: list[str] | None = None) -> int:
    """Start a bundle in this process, in place of ELI v3's own window. An external bundle is
    started as its own program and this process waits for it."""
    bundles = discover()
    bundle = bundles.get(bundle_id.lower())
    if bundle is None:
        known = ", ".join(bundles) or "none are installed"
        print(f"No bundle {bundle_id!r} is installed here ({known}).", file=sys.stderr)
        return 2
    ok, why = entitled(bundle)
    if not ok:
        print(f"{bundle.title} is not included in this licence: {why}", file=sys.stderr)
        return 3
    log.info("starting bundle %s (%s) from %s", bundle.id, bundle.kind, bundle.origin)
    with _core(bundle) as port:
        if bundle.kind == "external":
            command = [*bundle.command, *(argv or [])]
            if port is None:
                return subprocess.call(command)
            return subprocess.call(
                command, env={**os.environ, "ELI_API_PORT": str(port)}
            )
        module, _, attr = bundle.launch.partition(":")
        run: Callable[..., Any] = getattr(importlib.import_module(module), attr)
        host = _host()
        if port is not None:
            host["api_port"] = port
        return int(run(list(argv or []), host=host) or 0)


def spawn(bundle_id: str) -> subprocess.Popen:
    """Start a bundle in a window of its own, beside a running ELI v3."""
    bundles = discover()
    bundle = bundles[bundle_id.lower()]
    ok, why = entitled(bundle)
    if not ok:
        raise PermissionError(why)
    env = dict(os.environ)
    server = None
    if _needs_server(bundle):
        # Beside a running ELI the bundle is served by this ELI, in this process: the model
        # already loaded in his window is the one that answers, and nothing is loaded twice.
        from eli.bundles import core_server

        port, server = core_server.start()
        if port is not None:
            env["ELI_API_PORT"] = str(port)
    if bundle.kind == "external":
        command = list(bundle.command)
    elif getattr(sys, "frozen", False):
        command = [sys.executable, "--bundle", bundle.id]
    else:
        command = [sys.executable, "-m", "eli", "--bundle", bundle.id]
    process = subprocess.Popen(command, env=env, start_new_session=True)
    if server is not None:
        # a server started for this bundle goes when the bundle does
        def _stop_when_closed() -> None:
            process.wait()
            server.should_exit = True

        import threading

        threading.Thread(target=_stop_when_closed, name="eli-bundle-watch", daemon=True).start()
    return process


__all__ = [
    "CONTRACT",
    "GROUP",
    "Bundle",
    "discover",
    "entitled",
    "launch",
    "registry_dirs",
    "spawn",
]

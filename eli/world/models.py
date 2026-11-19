"""ELI's own models: real things ELI brings into its rooms that its body did not come with.

Two ways in. A file on this computer (`import_file`: a glTF or GLB with the files it names), and
a thing ELI asks for by what it is (`fetch`: "a pine tree"), found in Poly Haven's library of
models, all CC0, and brought down with the Net switch, SSRF and size guards on (netguard). Each
is kept under ELI's data folder, `world/models/<id>/`, with a manifest beside them saying what
each is, where it came from, its licence and its size in metres, so ELI can place it at once;
a body (ELI VR) fetches the files from `/v1/world/model/<id>/<file>` and draws it.

Nothing here runs unless asked: no library is fetched at import, and with the Net off `fetch`
says so instead of guessing.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from eli.core.secure_io import secure_write_text
from eli.utils.log import get_logger

log = get_logger(__name__)

LIBRARY = "https://api.polyhaven.com"
LIBRARY_NAME = "Poly Haven"
LIBRARY_LICENCE = "CC0 1.0"
LIBRARY_RESOLUTION = "1k"          # the textures: enough for a thing stood in a room
LIBRARY_LIST_FRESH = 24 * 3600     # the list of what it has, kept this long
MODEL_FILE_MAX = 64 * 1024 * 1024  # one file of a model
MODEL_FILES_MAX = 40               # files one model may be made of
IMPORT_EXTENSIONS = (".gltf", ".glb")
SIDE_EXTENSIONS = (".bin", ".png", ".jpg", ".jpeg", ".webp", ".ktx2")
MANIFEST = "models.json"
_lock = threading.Lock()


def store() -> Path:
    from eli.core.paths import data_dir
    return data_dir() / "world" / "models"


def _manifest_path() -> Path:
    return store() / MANIFEST


def _read_manifest() -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(_manifest_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_manifest(data: Dict[str, Dict[str, Any]]) -> None:
    path = _manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    secure_write_text(path, json.dumps(data, indent=1, ensure_ascii=False))


def ident_of(text: str) -> str:
    """A safe id from a name: letters, digits, _ and -, as the body and the paths take it."""
    ident = re.sub(r"[^A-Za-z0-9_-]+", "_", str(text or "").strip()).strip("_")[:60]
    return ident or "model"


def listed() -> List[Dict[str, Any]]:
    """Every model ELI has, as a body is told of them: id, name, size in metres [wide, tall,
    deep], source, licence and the files it is made of (the first is the glTF)."""
    with _lock:
        kept = _read_manifest()
    out = []
    for ident, entry in kept.items():
        if not isinstance(entry, dict) or not (store() / ident).is_dir():
            continue
        out.append({"id": ident, "name": str(entry.get("name") or ident), "size": list(entry.get("size") or [1, 1, 1]),
                    "source": str(entry.get("source") or ""), "licence": str(entry.get("licence") or ""),
                    "authors": list(entry.get("authors") or []), "files": list(entry.get("files") or [])})
    return out


def props() -> List[Dict[str, Any]]:
    """The same, in the form the building takes a real thing: {"id", "name", "size"}."""
    return [{"id": m["id"], "name": m["name"], "size": m["size"]} for m in listed()]


def file_path(ident: str, rel: str) -> Optional[Path]:
    """The file `rel` of model `ident`, when the manifest lists it and it is there; else None.
    Only listed files, so a path can never reach outside the model's own folder."""
    ident = str(ident or "")
    rel = str(rel or "").replace("\\", "/")
    with _lock:
        entry = _read_manifest().get(ident)
    if not isinstance(entry, dict) or rel not in (entry.get("files") or []):
        return None
    path = (store() / ident / rel).resolve()
    try:
        path.relative_to((store() / ident).resolve())
    except ValueError:
        return None
    return path if path.is_file() else None


# ── a file on this computer ───────────────────────────────────────────────────

def _gltf_sidecars(gltf: Path) -> List[str]:
    """The files a .gltf names beside it (buffers and images with relative uris)."""
    try:
        data = json.loads(gltf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out: List[str] = []
    for key in ("buffers", "images"):
        for item in data.get(key) or []:
            uri = str((item or {}).get("uri") or "")
            if uri and not uri.startswith("data:") and "://" not in uri and ".." not in uri.split("/"):
                out.append(uri.replace("\\", "/"))
    return out


def import_file(path: str, name: str = "", size_m: Optional[List[float]] = None) -> Dict[str, Any]:
    """Bring a glTF or GLB on this computer into the store, with the files it names. `size_m` is
    its size [wide, tall, deep] when known; a body measures it otherwise."""
    src = Path(os.path.expanduser(str(path or ""))).resolve()
    if src.suffix.lower() not in IMPORT_EXTENSIONS:
        raise ValueError("a model is a .gltf or .glb file")
    if not src.is_file():
        raise FileNotFoundError(f"no file at {src}")
    files = [src.name] + (_gltf_sidecars(src) if src.suffix.lower() == ".gltf" else [])
    files = files[:MODEL_FILES_MAX]
    ident = ident_of(name or src.stem)
    with _lock:
        kept = _read_manifest()
        base = ident
        count = 2
        while ident in kept:
            ident, count = f"{base}_{count}", count + 1
        folder = store() / ident
        folder.mkdir(parents=True, exist_ok=True)
        for rel in files:
            here = src.parent / rel
            if not here.is_file():
                raise FileNotFoundError(f"{src.name} names {rel}, which is not beside it")
            if here.stat().st_size > MODEL_FILE_MAX:
                raise ValueError(f"{rel} is larger than {MODEL_FILE_MAX // (1024 * 1024)} MB")
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(here, folder / rel)
        kept[ident] = {"name": _plain(name or src.stem), "source": str(src), "licence": "",
                       "size": [round(float(v), 2) for v in (size_m or [1, 1, 1])][:3], "files": files,
                       "measured": bool(size_m), "added": time.time()}
        _write_manifest(kept)
    log.info("[WORLD_MODELS] imported %s from %s", ident, src)
    return {"id": ident, **kept[ident]}


def _plain(name: str) -> str:
    """"rock_moss_set_01" -> "rock moss set": what a thing is, without the library's numbering."""
    words = [w for w in re.split(r"[\s_]+", str(name or "").strip().lower()) if w and not w.isdigit()]
    return " ".join(words)[:48]


# ── the library ───────────────────────────────────────────────────────────────

def _get(url: str, max_bytes: int = MODEL_FILE_MAX) -> bytes:
    from eli.core.netguard import safe_fetch
    return safe_fetch(url, headers={"User-Agent": "ELI-v3 (local assistant; CC0 models for its rooms)"},
                      timeout=60, max_bytes=max_bytes)


def library(refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """What the library has, by id: name, tags, categories, dimensions. Kept for a day."""
    cache = store() / "library.json"
    try:
        if not refresh and time.time() - cache.stat().st_mtime < LIBRARY_LIST_FRESH:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data:
                return data
    except (OSError, ValueError):
        log.debug("the library's cached list could not be read; fetching it again", exc_info=True)
    data = json.loads(_get(f"{LIBRARY}/assets?t=models", max_bytes=16 * 1024 * 1024).decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError("the library's list could not be read")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(data), encoding="utf-8")
    return data


_STOP = {"a", "an", "the", "of", "some", "model", "models", "3d", "thing", "object", "item", "me", "please", "nice",
         "big", "small", "little", "large", "old", "new", "for", "my", "room", "in", "and", "with"}


def _words(text: str) -> List[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", str(text or "").lower()):
        if w in _STOP:
            continue
        if len(w) > 3 and w.endswith("s"):      # trees -> tree, rocks -> rock
            w = w[:-1]
        if w not in out:
            out.append(w)
    return out


def pick(want: str, catalogue: Dict[str, Dict[str, Any]]) -> Optional[Tuple[str, Dict[str, Any]]]:
    """The library's best match for what is wanted: every word must be in its name, tags or
    categories (a name word counts double), the most downloaded of those first. None when no word
    matches at all."""
    words = _words(want)
    if not words:
        return None
    best: Optional[Tuple[float, str, Dict[str, Any]]] = None
    for ident, entry in catalogue.items():
        if not isinstance(entry, dict):
            continue
        name_words = _words(entry.get("name") or ident) + _words(ident)
        tag_words = set(_words(" ".join(entry.get("tags") or [])) + _words(" ".join(entry.get("categories") or [])))
        score = 0.0
        for w in words:
            if w in name_words:
                score += 2.0
            elif w in tag_words:
                score += 1.0
            else:
                score = 0.0
                break
        if score <= 0.0:
            continue
        score += min(1.0, float(entry.get("download_count") or 0) / 100000.0)
        if best is None or score > best[0]:
            best = (score, ident, entry)
    return (best[1], best[2]) if best else None


def _size_from(entry: Dict[str, Any]) -> List[float]:
    """The library gives [x, y, z] in millimetres with z up: [wide, tall, deep] in metres."""
    dims = entry.get("dimensions") or []
    if len(dims) == 3 and all(isinstance(v, (int, float)) for v in dims):
        return [round(dims[0] / 1000.0, 2), round(dims[2] / 1000.0, 2), round(dims[1] / 1000.0, 2)]
    return [1.0, 1.0, 1.0]


def fetch(want: str) -> Dict[str, Any]:
    """Bring in the library's best match for what is wanted, or say why not: {"ok", "id", "name",
    "size", "reason"}. Already fetched: the one there is."""
    want = " ".join(str(want or "").split())[:80]
    if not want:
        return {"ok": False, "reason": "nothing was asked for"}
    from eli.core.netguard import OfflineError, should_block_network
    if should_block_network():
        return {"ok": False, "offline": True, "reason": "the Net is off, so nothing can be fetched"}
    try:
        found = pick(want, library())
    except OfflineError:
        return {"ok": False, "offline": True, "reason": "the Net is off, so nothing can be fetched"}
    except Exception as exc:  # noqa: BLE001 - said in words, never raised raw
        return {"ok": False, "reason": f"the library could not be reached: {exc}"}
    if found is None:
        return {"ok": False, "reason": f"{LIBRARY_NAME} has nothing called {want!r}"}
    ident, entry = found
    with _lock:
        kept = _read_manifest()
    if ident in kept and (store() / ident).is_dir():
        return {"ok": True, "id": ident, "name": kept[ident]["name"], "size": kept[ident]["size"], "had": True}
    try:
        files_info = json.loads(_get(f"{LIBRARY}/files/{ident}", max_bytes=4 * 1024 * 1024).decode("utf-8"))
        gltf = (((files_info.get("gltf") or {}).get(LIBRARY_RESOLUTION) or {}).get("gltf") or {})
        if not gltf.get("url"):
            return {"ok": False, "reason": f"{LIBRARY_NAME} has no glTF of {ident}"}
        includes = gltf.get("include") or {}
        if len(includes) + 1 > MODEL_FILES_MAX:
            return {"ok": False, "reason": f"{ident} is made of too many files"}
        folder = store() / ident
        folder.mkdir(parents=True, exist_ok=True)
        main = f"{ident}_{LIBRARY_RESOLUTION}.gltf"
        files = [main]
        (folder / main).write_bytes(_get(gltf["url"]))
        for rel, inc in includes.items():
            rel = str(rel).replace("\\", "/")
            if rel.startswith("/") or ".." in rel.split("/") or not (inc or {}).get("url"):
                continue
            (folder / rel).parent.mkdir(parents=True, exist_ok=True)
            (folder / rel).write_bytes(_get(inc["url"]))
            files.append(rel)
    except OfflineError:
        return {"ok": False, "offline": True, "reason": "the Net went off while fetching"}
    except Exception as exc:  # noqa: BLE001
        shutil.rmtree(store() / ident, ignore_errors=True)
        return {"ok": False, "reason": f"{ident} could not be fetched: {exc}"}
    size = _size_from(entry)
    authors = entry.get("authors") or {}
    with _lock:
        kept = _read_manifest()
        kept[ident] = {"name": _plain(entry.get("name") or ident), "source": f"{LIBRARY_NAME} {ident}",
                       "licence": LIBRARY_LICENCE, "authors": sorted(authors) if isinstance(authors, dict) else [],
                       "size": size, "files": files, "measured": True, "added": time.time()}
        _write_manifest(kept)
    log.info("[WORLD_MODELS] fetched %s (%s) from %s", ident, kept[ident]["name"], LIBRARY_NAME)
    return {"ok": True, "id": ident, "name": kept[ident]["name"], "size": size, "had": False}

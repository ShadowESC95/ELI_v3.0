"""
eli/runtime/generated_script_guard.py
─────────────────────────────────────
What the executor checks of a script or document once it has been generated: a script that carries
a known bad marker (a prompt echoed back, a TODO, a shell file holding Python) is quarantined and the
run reported as failed; a document lands where documents go and is held to a body that says
something. Nothing here writes a script: every one is written by the one generator
(eli/execution/effectors/coding.py).
"""
from __future__ import annotations

import re
import time
import os
import json
from pathlib import Path
from typing import Any


try:
    from eli.core.paths import canonical_root
    PROJECT_ROOT = canonical_root(Path(__file__).resolve().parents[2])
except Exception:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _artifact_event(kind: str, path: Path, **extra: Any) -> str:
    data = {"event": "artifact_generated", "kind": kind, "path": str(path)}
    data.update(extra)
    return json.dumps(data, ensure_ascii=False, default=str)


def _document_dir() -> Path:
    env_dir = os.environ.get("ELI_ARTIFACTS_DIR")
    root = Path(env_dir).expanduser() if env_dir else PROJECT_ROOT / "artifacts"
    d = (root if root.is_absolute() else PROJECT_ROOT / root) / "documents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(text: str, default: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(text or "").lower())[:80].strip("_")
    return slug or default


def _normalise_document_result(result: Any, args: dict[str, Any], action: str) -> Any:
    if not isinstance(result, dict):
        return result
    if result.get("ok") is False:
        return result

    topic = str(args.get("topic") or args.get("title") or args.get("description") or "document")
    fmt = str(args.get("format") or "md").lower().lstrip(".")
    if fmt not in {"md", "txt"}:
        fmt = "md"

    raw_content = str(result.get("document_content") or result.get("markdown") or "")
    visible = str(result.get("content") or result.get("response") or "")
    path_text = str(result.get("doc_path") or result.get("path") or "").strip()

    path = Path(path_text).expanduser() if path_text else None
    if path and path.exists() and path.parent.name == "scripts" and path.suffix.lower() in {".md", ".txt"}:
        new_path = _document_dir() / path.name
        if new_path.exists():
            new_path = _document_dir() / f"{new_path.stem}_{int(time.time())}{new_path.suffix}"
        try:
            path.replace(new_path)
            path = new_path
        except Exception:
            path = new_path
            path.write_text(raw_content or visible, encoding="utf-8")
    elif not path or not path.exists():
        content = raw_content or visible
        if not content.strip():
            return result
        if re.search(r"(?i)\b(document generated|document saved|generation failed|below the quality threshold)\b", content):
            return {
                "ok": False,
                "action": action,
                "error": "Document generation returned only a status/error message, not a document body.",
                "content": "Document generation returned only a status/error message, not a document body.",
                "response": "Document generation returned only a status/error message, not a document body.",
            }
        path = _document_dir() / f"{_slug(topic, 'document')}.{fmt}"
        if path.exists():
            path = _document_dir() / f"{path.stem}_{int(time.time())}{path.suffix}"
        path.write_text(content, encoding="utf-8")

    try:
        saved_content = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        saved_content = raw_content or visible

    bad_doc_markers = (
        "Generated in ELI_TEST_MODE",
        "Requested topic:",
        "(abstract here)",
        "TODO",
        "Add code here",
    )
    if any(marker.lower() in (saved_content or "").lower() for marker in bad_doc_markers):
        return {
            "ok": False,
            "action": action,
            "error": "Generated document rejected: output contained stub/template markers.",
            "content": "Generated document rejected: output contained stub/template markers.",
            "response": "Generated document rejected: output contained stub/template markers.",
            "doc_path": str(path) if path else "",
            "path": str(path) if path else "",
        }
    if len((saved_content or "").strip()) < 240:
        return {
            "ok": False,
            "action": action,
            "error": "Generated document rejected: output was too short to be useful.",
            "content": "Generated document rejected: output was too short to be useful.",
            "response": "Generated document rejected: output was too short to be useful.",
            "doc_path": str(path) if path else "",
            "path": str(path) if path else "",
        }

    msg = _artifact_event("document", path, chars=len(saved_content))
    result.update({
        "ok": True,
        "action": action,
        "doc_path": str(path),
        "path": str(path),
        "filename": path.name,
        "document_content": saved_content,
        "content": msg,
        "response": msg,
        "open_in_ide": True,
        "open_in_labs": False,
    })
    return result


def _invalid_generated_script(path: Any) -> tuple[bool, str]:
    try:
        p = Path(str(path))
        if not p.exists() or not p.is_file():
            return False, "no readable artifact"
        txt = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return True, f"could not read generated artifact: {e}"

    # A stub is told by the executor's one reader of stubs: in a comment, or the prompt come back.
    # "TODO" in a string is real code (a repo analyser counting TODOs, a to-do list app).
    from eli.execution.executor_enhanced import _stub_marker
    stub = _stub_marker(txt)
    if stub:
        return True, f"bad generated-script marker: {stub}"
    bad_patterns = [
        r"code fences are unavoidable",
        r"with open\([\"']~/eli/artifacts/user\.sqlite3[\"']",
    ]

    for pat in bad_patterns:
        if re.search(pat, txt, re.I):
            return True, f"bad generated-script marker: {pat}"

    if p.suffix in {".sh", ".bash"} and re.search(r"^\s*def\s+\w+\(", txt, re.M):
        return True, "shell artifact contains Python function definition"

    return False, ""


def _quarantine(path: Any, reason: str) -> None:
    try:
        p = Path(str(path))
        if not p.exists():
            return
        qdir = p.parent / "invalid"
        qdir.mkdir(parents=True, exist_ok=True)
        q = qdir / f"{p.name}.invalid_{int(time.time())}"
        p.rename(q)
        q.with_suffix(q.suffix + ".reason.txt").write_text(reason, encoding="utf-8")
    except Exception:
        pass


_SCRIPT_ACTIONS = frozenset({"GENERATE_SCRIPT", "CREATE_SCRIPT", "WRITE_SCRIPT", "GENERATE_CODE", "WRITE_CODE"})
_DOCUMENT_ACTIONS = frozenset({"GENERATE_DOCUMENT", "CREATE_DOCUMENT", "CREATE_DOC", "WRITE_DOCUMENT"})
# Where a generated script's result names its file (the generator says script_path).
_SCRIPT_PATH_KEYS = ("script_path", "path", "file", "filepath", "saved_path", "output_path")


def install(module_globals: dict[str, Any]) -> None:
    """Wrap the executor's execute so a generated script is checked once it is written and a
    generated document is saved and held as a document."""
    orig = module_globals.get("execute")
    if not callable(orig) or getattr(orig, "_eli_generated_script_guard", False):
        return

    def wrapped(action: Any = None, args: Any = None, *pargs: Any, **kwargs: Any):
        act = str(action or kwargs.get("action") or "").upper()
        data = args if isinstance(args, dict) else (kwargs.get("args") if isinstance(kwargs.get("args"), dict) else {})
        result = orig(action, args, *pargs, **kwargs)
        if act in _SCRIPT_ACTIONS and isinstance(result, dict):
            for key in _SCRIPT_PATH_KEYS:
                if not result.get(key):
                    continue
                bad, reason = _invalid_generated_script(result.get(key))
                if bad:
                    _quarantine(result.get(key), reason)
                    msg = f"Generated script failed validation: {reason}. Artifact quarantined."
                    return {"ok": False, "action": act, "error": msg, "content": msg, "response": msg}
        if act in _DOCUMENT_ACTIONS:
            return _normalise_document_result(result, data or {}, act)
        return result

    wrapped._eli_generated_script_guard = True
    module_globals["execute"] = wrapped
    module_globals["execute_action"] = wrapped

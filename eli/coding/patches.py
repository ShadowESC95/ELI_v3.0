"""Incremental repo patch utilities for the coding agent.

Bridges CodeAgent output to ``SelfImprovementEngine.apply_patch_set`` so repo-edit
tasks apply minimal ``{file, old, new}`` hunks instead of full-file rewrites.
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from eli.utils.log import get_logger

log = get_logger(__name__)

_REPO_EDIT_RE = re.compile(
    r"\b(?:fix|patch|update|change|modify|edit|refactor|repair|correct)\b",
    re.I,
)


def _project_root() -> Path:
    try:
        from eli.core.paths import project_root
        return Path(project_root())
    except Exception:
        return Path(__file__).resolve().parents[2]


def is_repo_edit_task(task: str) -> bool:
    """True when the task names an existing .py file and asks to change it."""
    if not _REPO_EDIT_RE.search(task or ""):
        return False
    try:
        from eli.runtime.code_examiner import _extract_named_paths
        paths = _extract_named_paths(task)
        return any(p.exists() and p.suffix == ".py" for p in paths)
    except Exception:
        return bool(re.search(r"\b[\w/\\.-]+\.py\b", task or ""))


def named_repo_files(task: str, *, limit: int = 4) -> List[Path]:
    try:
        from eli.runtime.code_examiner import _extract_named_paths
        return [p for p in _extract_named_paths(task)
                if p.exists() and p.suffix == ".py"][:limit]
    except Exception:
        return []


def compute_line_patches(
    file_path: str | Path,
    old_content: str,
    new_content: str,
) -> List[Dict[str, str]]:
    """Derive minimal verbatim ``{file, old, new}`` hunks from two file bodies."""
    if old_content == new_content:
        return []
    root = _project_root()
    p = Path(file_path)
    rel = str(p.relative_to(root) if p.is_absolute() else p).replace("\\", "/")

    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
    patches: List[Dict[str, str]] = []

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        old_chunk = "".join(old_lines[i1:i2])
        new_chunk = "".join(new_lines[j1:j2])
        if not old_chunk and not new_chunk:
            continue
        # apply_patch_set requires old to exist verbatim; skip pure insertions at EOF
        # that have no anchor (handled as append via surrounding context when possible).
        if not old_chunk.strip() and old_lines:
            # Insertion: anchor to the line before the insertion point.
            anchor = old_lines[i1 - 1] if i1 > 0 else ""
            if anchor:
                old_chunk = anchor
                new_chunk = anchor + new_chunk
        if old_chunk and old_chunk in old_content:
            patches.append({"file": rel, "old": old_chunk, "new": new_chunk})
    return patches


def apply_repo_patches(
    patches: List[Dict[str, str]],
    *,
    verify: bool = True,
) -> Dict[str, Any]:
    """Apply patches atomically via SelfImprovementEngine."""
    if not patches:
        return {"ok": False, "applied": False, "message": "no patches"}
    try:
        from eli.runtime.self_improvement import get_self_improvement
        return get_self_improvement().apply_patch_set(patches, verify=verify)
    except Exception as exc:
        log.debug(f"[PATCHES] apply failed: {exc}")
        return {"ok": False, "applied": False, "message": str(exc)}


def try_apply_repo_fix(
    task: str,
    new_content: str,
    *,
    target_file: Optional[Path] = None,
    verify: bool = True,
) -> Dict[str, Any]:
    """If ``task`` is a repo edit, compute and apply incremental patches."""
    files = [target_file] if target_file else named_repo_files(task)
    if not files:
        return {"ok": False, "applied": False, "message": "no target file"}
    target = files[0]
    try:
        old_content = target.read_text(encoding="utf-8")
    except Exception as exc:
        return {"ok": False, "applied": False, "message": f"read error: {exc}"}
    if new_content.strip() == old_content.strip():
        return {"ok": True, "applied": False, "message": "no changes"}
    patches = compute_line_patches(target, old_content, new_content)
    if not patches:
        return {"ok": False, "applied": False, "message": "could not derive verbatim hunks"}
    result = apply_repo_patches(patches, verify=verify)
    result["patches"] = len(patches)
    result["file"] = str(target)
    return result

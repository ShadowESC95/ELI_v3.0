"""What a coding-agent candidate would change in one of ELI's own files, as a proposal.

Nothing here writes. A candidate becomes a proposal for the one file it was written for: the
whole original and the whole new text (one exact replacement the gated applier,
``SelfImprovementEngine.apply_patch_set``, can apply once the owner approves it), plus the
minimal hunks that show what changes. A candidate that is not a version of that file (it drops
the file's public names, or does not parse) is refused here rather than diffed into it.
"""
from __future__ import annotations

import ast
import difflib
from pathlib import Path
from typing import Any, Dict, List, Set

from eli.utils.log import get_logger

log = get_logger(__name__)


def _source_root() -> Path:
    from eli.core.paths import source_root
    return Path(source_root())


def _public_names(source: str) -> Set[str]:
    """Top-level names a module offers to its importers: defs, classes and assigned names not
    starting with an underscore."""
    names: Set[str] = set()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                for n in ast.walk(t):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
    return {n for n in names if not n.startswith("_")}


def _unique_anchor(old_lines: List[str], i1: int, i2: int, text: str) -> tuple:
    """Grow the hunk [i1, i2) with surrounding lines until its text occurs exactly once in the
    file. Returns (start, end) or None when even the whole file is not enough (it never is
    ambiguous then, so None only comes back for an empty file)."""
    lo, hi = i1, i2
    while True:
        chunk = "".join(old_lines[lo:hi])
        if chunk and text.count(chunk) == 1:
            return lo, hi
        if lo == 0 and hi == len(old_lines):
            return None
        if lo > 0:
            lo -= 1
        if hi < len(old_lines):
            hi += 1


def compute_line_patches(file_path: str | Path, old_content: str, new_content: str) -> List[Dict[str, str]]:
    """Minimal ``{file, old, new}`` hunks that turn ``old_content`` into ``new_content``.

    Each hunk's ``old`` occurs exactly once in the file at the time it is applied (widened with
    context until it does), deletions carry an empty ``new``, an insertion at the very top is
    anchored to the lines after it, and the hunks are checked to reproduce ``new_content`` when
    applied in order. When they would not, nothing is returned rather than a wrong edit.
    """
    if old_content == new_content:
        return []
    p = Path(file_path)
    try:
        rel = str(p.resolve().relative_to(_source_root()) if p.is_absolute() else p)
    except ValueError:
        rel = str(p)
    rel = rel.replace("\\", "/")

    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)
    patches: List[Dict[str, str]] = []
    current = old_content
    # Bottom-up, so a hunk's line numbers still describe the text it is anchored in.
    opcodes = [op for op in difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes()
               if op[0] != "equal"]
    for _tag, i1, i2, j1, j2 in reversed(opcodes):
        cur_lines = current.splitlines(keepends=True)
        span = _unique_anchor(cur_lines, i1 if i2 > i1 else max(i1 - 1, 0),
                              i2 if i2 > i1 else min(i1 + 1, len(cur_lines)), current)
        if span is None:
            return []
        lo, hi = span
        before = "".join(cur_lines[lo:hi])
        replacement = "".join(cur_lines[lo:i1]) + "".join(new_lines[j1:j2]) + "".join(cur_lines[i2:hi])
        patches.append({"file": rel, "old": before, "new": replacement})
        current = "".join(cur_lines[:lo]) + replacement + "".join(cur_lines[hi:])
    if current != new_content:
        log.debug("[PATCHES] hunks for %s did not reproduce the candidate; none returned", rel)
        return []
    return patches


def propose_file_patch(target_file: str | Path, new_content: str) -> Dict[str, Any]:
    """The change a candidate would make to ``target_file``, checked but not applied.

    Returns {ok, applied: False, file, old, new, hunks, problems}. ``old``/``new`` are the whole
    texts, so the patch is one exact replacement for the gated applier.
    """
    out: Dict[str, Any] = {"ok": False, "applied": False, "file": "", "old": "", "new": "",
                           "hunks": [], "problems": []}
    root = _source_root()
    p = Path(target_file)
    p = (root / p if not p.is_absolute() else p).resolve()
    try:
        out["file"] = p.relative_to(root).as_posix()
    except ValueError:
        out["problems"].append(f"{p} is outside ELI's source tree")
        return out
    if not p.is_file() or p.suffix != ".py":
        out["problems"].append(f"{out['file']} is not an existing Python file")
        return out
    try:
        original = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        out["problems"].append(f"could not read {out['file']}: {exc}")
        return out
    if (new_content or "").strip() == original.strip():
        out["problems"].append("the candidate is the file unchanged")
        return out
    try:
        kept = _public_names(new_content)
    except SyntaxError as exc:
        out["problems"].append(f"the candidate does not parse: {exc.msg} (line {exc.lineno})")
        return out
    try:
        dropped = sorted(_public_names(original) - kept)
    except SyntaxError:
        dropped = []
    if dropped:
        # A standalone program written for the task is not a new version of this file.
        out["problems"].append(f"the candidate drops {len(dropped)} public name(s) of {out['file']}: "
                               + ", ".join(dropped[:8]))
        return out
    out.update(ok=True, old=original, new=new_content,
               hunks=compute_line_patches(p, original, new_content))
    return out

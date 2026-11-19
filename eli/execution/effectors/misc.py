"""Misc effector — CHECK_CHRONAL_ALIGNMENT, DATA_FABRICATOR, SHOW_DIFF."""
from __future__ import annotations

import os
import shlex
import subprocess
import time
from pathlib import Path

from eli.utils.log import get_logger

log = get_logger(__name__)

def _execute_impl(*a, **k):
    from eli.execution.executor_enhanced import _execute_impl as _f
    return _f(*a, **k)

from eli.utils.platform_compat import open_file  # noqa: E402


def _h_check_chronal_alignment(a, args):
    """The clock as this machine has it (local time, its zone and offset from UTC): facts for the
    model to answer the playful question in its own words. It answered with one fixed sentence that
    called the alignment "nominal", which nothing had checked."""
    import datetime as _dt
    now = _dt.datetime.now().astimezone()
    offset = now.strftime("%z")
    facts = (f"Local time: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
             f"Time zone: {now.tzname() or 'unnamed'} (UTC{offset[:3]}:{offset[3:]})\n"
             f"UTC: {now.astimezone(_dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
    question = str((args or {}).get("question") or (args or {}).get("message") or "check chronal alignment")
    return {"ok": True, "action": a, "content": facts, "response": facts, "explain_in_words": True,
            "question": question}


def _h_data_fabricator(a, args):
    try:
        # If a topic/prompt is provided, generate a document first then open it.
        topic = str((args or {}).get("topic") or (args or {}).get("query") or (args or {}).get("text") or (args or {}).get("message") or "").strip()
        if topic:
            r = _execute_impl(action="CREATE_DOCUMENT", args={"topic": topic, "format": (args or {}).get("format") or "md"})
            # If generation succeeded, also open it in this OS's editor
            if r.get("ok") and r.get("path"):
                from eli.utils.platform_compat import open_role
                open_role("editor", str(r.get("path"))) or open_file(str(r.get("path")))
            return r

        # Otherwise a new blank file in ELI's own documents folder (not a generic ~/Documents/ELI),
        # opened in this OS's editor.
        from eli.core.paths import documents_dir
        from eli.utils.platform_compat import open_role
        out_dir = Path(documents_dir())
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"data_construct_{int(time.time())}.md"
        if not out_path.exists():
            out_path.write_text("# Data Construct\n\n", encoding="utf-8")
        editor = open_role("editor", str(out_path))
        if editor:
            msg = f"Opened editor: {editor}"
            return {"ok": True, "action": a, "path": str(out_path), "content": msg, "response": msg}

        # Fallback: open with default system handler
        open_file(str(out_path))
        msg = f"Opened file: {out_path}"
        return {"ok": True, "action": a, "path": str(out_path), "content": msg, "response": msg}
    except Exception as e:
        return {"ok": False, "action": a, "error": str(e), "content": str(e), "response": str(e)}


# How long one read-only git command may take (code_monitor gives its git diff the same).
GIT_TIMEOUT_S = 30.0


def _source_root() -> Path:
    from eli.core.paths import get_paths
    return Path(get_paths().project_root)


def _git(root: Path, *argv: str) -> tuple:
    """(ran, output) of a read-only git command in `root`."""
    try:
        done = subprocess.run(["git", "-C", str(root), *argv], capture_output=True, text=True,
                              timeout=GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as failure:
        return False, str(failure)
    return done.returncode == 0, (done.stdout if done.returncode == 0 else done.stderr or "").strip()


def _diff_room() -> int:
    """Characters of diff the model can read beside its instructions: half its window."""
    try:
        from eli.cognition.context_budget import chars_per_token
        from eli.cognition.gguf_inference import context_window
        window = int(context_window() or 0)
        return int(window * float(chars_per_token()) / 2) if window else 0
    except Exception:
        log.debug("model window unknown", exc_info=True)
        return 0


def _shown(diff: str) -> str:
    room = _diff_room()
    if room and len(diff) > room:
        return diff[:room] + f"\n… ({len(diff) - room:,} more characters of the diff not shown)"
    return diff


def _paths_named(args) -> list:
    raw = (args or {}).get("paths") or (args or {}).get("files") or ""
    if isinstance(raw, (list, tuple)):
        named = [str(x).strip() for x in raw if str(x).strip()]
    else:
        try:
            named = shlex.split(str(raw))
        except ValueError:
            named = str(raw).split()
    for key in ("a", "b", "left", "right", "path", "other"):
        if (args or {}).get(key) and str(args[key]).strip() not in named:
            named.append(str(args[key]).strip())
    return named


def _h_show_diff(a, args):
    """The diff itself, as evidence: two named files compared, else the changes in ELI's source
    tree (uncommitted, else the last commit), else the version record of an installed build. It used
    to ask the model "show me the recent changes" with nothing to go on, and the reply was an
    invented diff."""
    import difflib
    named = _paths_named(args)
    if len(named) == 2:
        left, right = (Path(os.path.expanduser(n)) for n in named)
        missing = [str(p) for p in (left, right) if not p.is_file()]
        if missing:
            msg = "No diff: " + " and ".join(missing) + (" is" if len(missing) == 1 else " are") + " not a file."
            return {"ok": False, "action": a, "error": "missing_file", "content": msg, "response": msg}
        try:
            old = left.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
            new = right.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
        except OSError as failure:
            msg = f"No diff: {failure}"
            return {"ok": False, "action": a, "error": str(failure), "content": msg, "response": msg}
        diff = "".join(difflib.unified_diff(old, new, fromfile=str(left), tofile=str(right)))
        msg = (f"{left} and {right} are the same." if not diff
               else f"Differences between {left} and {right}:\n{_shown(diff)}")
        return {"ok": True, "action": a, "diff": diff, "files": [str(left), str(right)],
                "evidence_source": "difflib", "content": msg, "response": msg}
    if len(named) == 1 or len(named) > 2:
        msg = f"A diff compares two files; I was given {len(named)}: {', '.join(named)}."
        return {"ok": False, "action": a, "error": "needs_two_files", "content": msg, "response": msg}

    root = _source_root()
    inside, _ = _git(root, "rev-parse", "--is-inside-work-tree")
    if inside:
        _, stat = _git(root, "diff", "HEAD", "--stat")
        _, patch = _git(root, "diff", "HEAD")
        if patch:
            msg = f"Uncommitted changes in {root}:\n{stat}\n\n{_shown(patch)}"
            return {"ok": True, "action": a, "diff": patch, "repo": str(root), "evidence_source": "git diff HEAD",
                    "content": msg, "response": msg}
        ran, last = _git(root, "show", "--stat", "--patch", "--format=%h %ad %s", "--date=short", "HEAD")
        if ran and last:
            msg = f"No uncommitted changes in {root}. The last commit:\n{_shown(last)}"
            return {"ok": True, "action": a, "diff": last, "repo": str(root), "evidence_source": "git show HEAD",
                    "content": msg, "response": msg}
        msg = f"{root} is a git checkout, but git showed no changes and no commit to compare ({last or 'no output'})."
        return {"ok": False, "action": a, "error": "no_changes", "content": msg, "response": msg}
    try:
        from eli.runtime import version_record
        told = version_record.told()
    except Exception:
        log.debug("version record unreadable", exc_info=True)
        told = ""
    msg = ("This ELI was installed from a build, not a source checkout, so there is no source diff here to show."
           + (f" {told}" if told else ""))
    return {"ok": False, "action": a, "error": "no_source_tree", "evidence_source": "versions_seen",
            "content": msg, "response": msg}


from eli.execution.effectors.registry import register as _register

for _action, _fn in {
    "CHECK_CHRONAL_ALIGNMENT": _h_check_chronal_alignment,
    "DATA_FABRICATOR": _h_data_fabricator,
    "SHOW_DIFF": _h_show_diff,
}.items():
    _register(_action, _fn)

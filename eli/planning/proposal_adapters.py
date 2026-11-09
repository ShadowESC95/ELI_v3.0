from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import time
from typing import Any, Optional, Sequence


@dataclass
class CompletedLike:
    args: Any
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def _discover_git_dir(start: Path) -> Optional[Path]:
    start = start.resolve()
    for p in [start, *start.parents]:
        gd = p / ".git"
        if gd.is_dir():
            return gd
        if gd.is_file():
            try:
                txt = gd.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                txt = ""
            if txt.startswith("gitdir:"):
                rel = txt.split(":", 1)[1].strip()
                cand = (p / rel).resolve()
                if cand.exists():
                    return cand
    return None


def _read_head_short(git_dir: Path) -> str:
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""

    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        ref_path = git_dir / ref
        try:
            value = ref_path.read_text(encoding="utf-8", errors="replace").strip()
            return value[:7]
        except Exception:
            packed = git_dir / "packed-refs"
            try:
                for line in packed.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("^"):
                        continue
                    sha, name = line.split(" ", 1)
                    if name.strip() == ref:
                        return sha[:7]
            except Exception:
                return ""
            return ""
    return head[:7]


def safe_git_completed(args: Sequence[str] | None = None, cwd: str | None = None, **kwargs) -> CompletedLike:
    argv = list(args or [])
    wd = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    git_dir = _discover_git_dir(wd)

    if not argv:
        return CompletedLike(args=argv, returncode=1, stderr="empty args")

    if argv[:3] == ["git", "rev-parse", "--is-inside-work-tree"]:
        if git_dir:
            return CompletedLike(args=argv, returncode=0, stdout="true\n")
        return CompletedLike(args=argv, returncode=128, stderr="not a git repo\n")

    if argv[:4] == ["git", "rev-parse", "--short", "HEAD"]:
        if not git_dir:
            return CompletedLike(args=argv, returncode=128, stderr="not a git repo\n")
        short = _read_head_short(git_dir)
        if short:
            return CompletedLike(args=argv, returncode=0, stdout=short + "\n")
        return CompletedLike(args=argv, returncode=128, stderr="head unavailable\n")

    if len(argv) >= 2 and argv[0] == "git" and argv[1] in _READ_ONLY_GIT:
        return _run_read_only_git(argv, wd, git_dir, kwargs)

    # Anything that could change the repository is not run here: said as not run, not as done.
    return CompletedLike(args=argv, returncode=1, stdout="",
                         stderr="not run: only git's read-only queries run here\n")


# git subcommands that only read the repository. `git diff`/`git status` here gave back empty output
# with success, so the code monitor saw no change, ever, and "what changed in your code" had none.
_READ_ONLY_GIT = frozenset({"diff", "status", "log", "show", "ls-files", "rev-parse", "cat-file", "rev-list"})
# Options that would make a reading command write (a file, an external program) or reconfigure git.
_WRITING_OPTION_PREFIXES = ("--output", "-o", "--ext-diff", "--textconv", "-c", "--exec-path", "--git-dir",
                            "--work-tree")


def _run_read_only_git(argv: list, wd: Path, git_dir: Optional[Path], kwargs: dict) -> CompletedLike:
    import os
    import shutil
    import subprocess
    if not git_dir:
        return CompletedLike(args=argv, returncode=128, stderr="not a git repo\n")
    if any(str(a).startswith(_WRITING_OPTION_PREFIXES) for a in argv[2:]):
        return CompletedLike(args=argv, returncode=1, stderr="not run: an option that writes or reconfigures\n")
    git = shutil.which("git")
    if not git:
        return CompletedLike(args=argv, returncode=127, stderr="git is not installed\n")
    try:
        from eli.core.host_env import host_env
        env = host_env()
    except ImportError:
        env = dict(os.environ)
    env["GIT_OPTIONAL_LOCKS"] = "0"            # `git status` takes no index lock: it only reads
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    timeout = kwargs.get("timeout") or None
    try:
        done = subprocess.run([git, "--no-pager", *argv[1:]], cwd=str(wd), env=env, capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return CompletedLike(args=argv, returncode=124, stderr=f"git {argv[1]} did not finish in {timeout}s\n")
    except OSError as failure:
        return CompletedLike(args=argv, returncode=126, stderr=f"git could not run: {failure}\n")
    return CompletedLike(args=argv, returncode=done.returncode, stdout=done.stdout or "", stderr=done.stderr or "")


def proposal_only_enqueue(args: Sequence[str] | None = None, cwd: str | None = None, env: dict | None = None, **kwargs) -> CompletedLike:
    argv = list(args or [])
    from eli.core.paths import proactive_dir
    out_dir = proactive_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    queue = out_dir / "proposal_queue.jsonl"

    payload = {
        "ts": int(time.time()),
        "kind": "proposal_only_enqueue",
        "args": argv,
        "cwd": cwd,
        "env_keys": sorted(list((env or {}).keys()))[:64],
    }

    with queue.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    return CompletedLike(args=argv, returncode=0, stdout="enqueued proposal\n")

def governed_proposal_enqueue(
    kind,
    payload=None,
    source="unknown",
    priority=50,
    cwd=None,
    action_class="observe-only",
    emitter="unknown",
    requested_by="system",
    user_confirmed=False,
    approver="user",
):
    from eli.planning.proposal_queue import append_governed_proposal
    rec = append_governed_proposal(
        kind=kind,
        payload=payload or {},
        source=source,
        priority=priority,
        cwd=cwd,
        action_class=action_class,
        emitter=emitter,
        requested_by=requested_by,
        user_confirmed=user_confirmed,
        approver=approver,
    )
    return rec.to_dict() if hasattr(rec, "to_dict") else rec

def proactive_governed_enqueue(
    kind,
    payload=None,
    source="proactive",
    priority=50,
    cwd=None,
    action_class="observe-only",
    requested_by="system",
    user_confirmed=False,
    approver="user",
):
    from eli.planning.proposal_queue import append_governed_proposal
    rec = append_governed_proposal(
        kind=kind,
        payload=payload or {},
        source=source,
        priority=priority,
        cwd=cwd,
        action_class=action_class,
        emitter="proactive",
        requested_by=requested_by,
        user_confirmed=user_confirmed,
        approver=approver,
    )
    return rec.to_dict() if hasattr(rec, "to_dict") else rec


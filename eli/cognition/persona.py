"""
Canonical persona authority for ELI.

Base persona:
    eli/cognition/persona.txt

Dynamic overlay:
    eli/cognition/persona.auto.txt

The dynamic overlay is intended to be regenerated from habits,
reflection, self-improvement, memory summaries, or other local runtime
signals. No user-specific hardcoded names belong in this module.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping, Optional

_PERSONA_DIR = Path(__file__).resolve().parent
_PERSONA_FILE = Path(
    os.environ.get("ELI_PERSONA_FILE", str(_PERSONA_DIR / "persona.txt"))
).expanduser().resolve()
def _default_persona_auto_file() -> Path:
    # Delegate to paths.persona_auto_path() instead of re-deriving project_root() here —
    # duplicating it meant its ELI_PERSONA_AUTO_PATH override never reached this, the real write path.
    try:
        from eli.core.paths import persona_auto_path
        return persona_auto_path()
    except Exception:
        return _PERSONA_DIR / "persona.auto.txt"


_cached_persona: Optional[str] = None
_cached_signature: Optional[tuple] = None


def _clean_persona(raw: str) -> str:
    clean_lines = []
    in_template_block = False
    for line in (raw or "").splitlines():
        stripped = line.strip()
        if stripped.upper().startswith(("PARAMETER ", "FROM ")):
            continue
        if stripped.upper().startswith("TEMPLATE "):
            in_template_block = True
            continue
        if in_template_block:
            if '"""' in stripped:
                in_template_block = False
            continue
        if stripped.startswith("# Model parameters") or stripped.startswith("# Template for conversation"):
            continue
        clean_lines.append(line)
    return "\n".join(clean_lines).strip()


def _sanitize_auto_persona_text(text: str) -> str:
    text = text or ""

    line_rules = [
        (r'(?m)^- Runtime status:.*$', '- Runtime status: sanitized'),
        (r'(?m)^- Cognition runtime:.*$', '- Cognition runtime: sanitized'),
        (r'(?m)^- Memory recall:.*$', '- Memory recall: sanitized'),
    ]
    for patt, repl in line_rules:
        text = re.sub(patt, repl, text)

    inline_rules = [
        (r'(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b', '<LOCAL_EMAIL>'),
        (r'(?<![A-Za-z0-9_])/(?:home|Users|mnt)/\S+', '<LOCAL_PATH>'),
        (r'\b[A-Za-z]:\\\S+', '<LOCAL_PATH>'),
    ]
    for patt, repl in inline_rules:
        text = re.sub(patt, repl, text)

    cleaned_lines = []
    for line in text.splitlines():
        low = line.lower()
        if any(tok in low for tok in ("script_exec_proposal", "proposal_id")):
            continue
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    return text


def base_path() -> Path:
    return _PERSONA_FILE


def auto_path() -> Path:
    """The overlay, resolved when it is used (paths.persona_auto_path, ELI_PERSONA_AUTO_PATH). It was a
    module constant fixed at import, under a second variable of its own, so a store set aside after
    this module was first imported (a test's, a module run without the suite conftest) was not seen."""
    return Path(_default_persona_auto_file()).expanduser().resolve()


def _fallback_persona() -> str:
    from eli.core.config import DEFAULT_PERSONA
    return DEFAULT_PERSONA


def _file_signature(path: Path) -> tuple:
    try:
        st = path.stat()
        return (True, int(st.st_mtime_ns), int(st.st_size))
    except FileNotFoundError:
        return (False, -1, -1)


def _read_raw(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def read_base_persona() -> str:
    return _clean_persona(_read_raw(_PERSONA_FILE))


def read_auto_persona() -> str:
    return _clean_persona(_read_raw(auto_path()))


def _compose_persona() -> str:
    base = read_base_persona()
    auto = read_auto_persona()

    if base and auto:
        return (
            f"{base}\n\n"
            "# Auto-updating runtime persona overlay\n"
            "# Generated from habits, reflection, self-improvement, and runtime signals.\n\n"
            f"{auto}"
        ).strip()

    if base:
        return base.strip()

    if auto:
        return auto.strip()

    return _fallback_persona()


def get_persona() -> str:
    global _cached_persona, _cached_signature
    sig = (_file_signature(_PERSONA_FILE), _file_signature(auto_path()))
    if _cached_persona is None or _cached_signature != sig:
        _cached_persona = _compose_persona()
        _cached_signature = sig
    return _cached_persona or _fallback_persona()


def reload() -> str:
    global _cached_persona, _cached_signature
    _cached_persona = None
    _cached_signature = None
    return get_persona()


def write_base_persona(text: str) -> str:
    _PERSONA_FILE.parent.mkdir(parents=True, exist_ok=True)
    _PERSONA_FILE.write_text((text or "").rstrip() + "\n", encoding="utf-8")
    return reload()


def write_auto_persona(text: str) -> str:
    auto_path().parent.mkdir(parents=True, exist_ok=True)
    clean = _sanitize_auto_persona_text((text or "").rstrip() + "\n")
    auto_path().write_text(clean, encoding="utf-8")
    return reload()


def update_auto_sections(sections: Mapping[str, Any]) -> str:
    lines = [
        "# Auto-updated persona overlay",
        "# Generated from habits, reflection, self-improvement, memory, and runtime signals.",
        "",
    ]

    for key, value in sections.items():
        if value is None:
            continue

        title = str(key).strip()
        if not title:
            continue

        lines.append(f"## {title}")

        if isinstance(value, str):
            val = value.strip()
            if val:
                lines.append(val)
        elif isinstance(value, (list, tuple, set)):
            added = False
            for item in value:
                item_s = str(item).strip()
                if item_s:
                    lines.append(f"- {item_s}")
                    added = True
            if not added:
                lines.append("- none")
        else:
            val = str(value).strip()
            lines.append(val if val else "- none")

        lines.append("")

    return write_auto_persona("\n".join(lines).rstrip() + "\n")


__all__ = [
    "base_path",
    "auto_path",
    "read_base_persona",
    "read_auto_persona",
    "get_persona",
    "reload",
    "write_base_persona",
    "write_auto_persona",
    "update_auto_sections",
]

"""The source of the GUI the launcher opens.

eli/gui/app.py starts eli.gui.main_window, the eli/gui/main_window package. An
older single-file copy of the GUI stayed in the tree after the split and fixes kept landing in it
instead, with their tests passing: it is gone, and tests read the window that runs.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE_GUI = ROOT / "eli" / "gui" / "main_window"


def live_gui_files() -> list[Path]:
    return sorted(LIVE_GUI.rglob("*.py"))


def live_gui_source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in live_gui_files())

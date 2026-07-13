"""
ELI v3 — Labs Tab
Scientific workspace: Notebook, Conversations, ELI Memory, Jupyter, Calculator,
Physics constants, Report generator, File Chat, Workspaces, Sim/IDE.
"""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


from eli.utils.log import get_logger
log = get_logger(__name__)

_LABS_PLOT_DIR = Path(os.environ.get("ELI_LABS_PLOT_DIR") or tempfile.gettempdir())
_LABS_PLOT_FILES = {
    "plot": _LABS_PLOT_DIR / "eli_labs_plot.png",
    "projectile": _LABS_PLOT_DIR / "eli_projectile.png",
    "waves": _LABS_PLOT_DIR / "eli_waves.png",
}


def _labs_plot_literal(name: str) -> str:
    return repr(str(_LABS_PLOT_FILES[name]))

# Qt imports. Labs has to use the same Qt binding as the already-loaded main GUI —
# a PySide6 QWidget won't accept a PyQt6 QMainWindow as a parent, or the other way round.
# So I take whatever binding the GUI already imported, else ELI_QT_API, else fall back
# PySide6 → PyQt6 → PyQt5.

_QT = None
_QT_ERRORS = []

_eli_qt_pref = str(os.environ.get("ELI_QT_API") or "").strip()
if "PySide6.QtWidgets" in sys.modules or "PySide6" in sys.modules:
    _QT_IMPORT_ORDER = ["PySide6", "PyQt6", "PyQt5"]
elif "PyQt6.QtWidgets" in sys.modules or "PyQt6" in sys.modules:
    _QT_IMPORT_ORDER = ["PyQt6", "PySide6", "PyQt5"]
elif "PyQt5.QtWidgets" in sys.modules or "PyQt5" in sys.modules:
    _QT_IMPORT_ORDER = ["PyQt5", "PySide6", "PyQt6"]
elif _eli_qt_pref in {"PySide6", "PyQt6", "PyQt5"}:
    _QT_IMPORT_ORDER = [_eli_qt_pref] + [
        x for x in ("PySide6", "PyQt6", "PyQt5") if x != _eli_qt_pref
    ]
else:
    _QT_IMPORT_ORDER = ["PySide6", "PyQt6", "PyQt5"]

for _eli_qt_candidate in _QT_IMPORT_ORDER:
    try:
        if _eli_qt_candidate == "PySide6":
            from PySide6.QtWidgets import (
                QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QTabWidget, QSplitter,
                QLabel, QPushButton, QLineEdit, QTextEdit, QPlainTextEdit,
                QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
                QGroupBox, QFormLayout, QComboBox, QCheckBox, QTreeView,
                QFileDialog, QMessageBox, QInputDialog, QScrollArea,
                QFileSystemModel, QHeaderView, QSizePolicy, QFrame,
                QApplication, QProgressBar, QSpinBox, QDoubleSpinBox,
                QAbstractItemView, QStackedWidget,
            )
            from PySide6.QtCore import Qt, QTimer, QThread, QObject, Signal as pyqtSignal, QSize
            from PySide6.QtGui import QFont, QColor, QSyntaxHighlighter, QTextCharFormat
            _QT = "PySide6"
            break

        if _eli_qt_candidate == "PyQt6":
            from PyQt6.QtWidgets import (
                QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QTabWidget, QSplitter,
                QLabel, QPushButton, QLineEdit, QTextEdit, QPlainTextEdit,
                QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
                QGroupBox, QFormLayout, QComboBox, QCheckBox, QTreeView,
                QFileDialog, QMessageBox, QInputDialog, QScrollArea,
                QHeaderView, QSizePolicy, QFrame,
                QApplication, QProgressBar, QSpinBox, QDoubleSpinBox,
                QAbstractItemView, QStackedWidget,
            )
            # === PHASE14B_LABS_PYQT6_QFILESYSTEMMODEL_COMPAT ===
            # PyQt6 on this machine exposes QFileSystemModel through QtGui,
            # not QtWidgets. Do not let that import miss force Labs to PyQt5.
            try:
                from PyQt6.QtWidgets import QFileSystemModel
            except ImportError:
                from PyQt6.QtGui import QFileSystemModel
            from PyQt6.QtCore import Qt, QTimer, QThread, QObject, pyqtSignal, QSize
            from PyQt6.QtGui import QFont, QColor, QSyntaxHighlighter, QTextCharFormat
            _QT = "PyQt6"
            break

        if _eli_qt_candidate == "PyQt5":
            from PyQt5.QtWidgets import (
                QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QTabWidget, QSplitter,
                QLabel, QPushButton, QLineEdit, QTextEdit, QPlainTextEdit,
                QListWidget, QListWidgetItem, QTableWidget, QTableWidgetItem,
                QGroupBox, QFormLayout, QComboBox, QCheckBox, QTreeView,
                QFileDialog, QMessageBox, QInputDialog, QScrollArea,
                QFileSystemModel, QHeaderView, QSizePolicy, QFrame,
                QApplication, QProgressBar, QSpinBox, QDoubleSpinBox,
                QAbstractItemView, QStackedWidget,
            )
            from PyQt5.QtCore import Qt, QTimer, QThread, QObject, pyqtSignal, QSize
            from PyQt5.QtGui import QFont, QColor, QSyntaxHighlighter, QTextCharFormat
            _QT = "PyQt5"
            break

    except ImportError as _eli_qt_err:
        _QT_ERRORS.append(f"{_eli_qt_candidate}: {_eli_qt_err}")

if _QT is None:
    raise ImportError(
        "Labs tab could not load a compatible Qt binding. Attempts: "
        + " | ".join(_QT_ERRORS)
    )

# ── Optional: QsciScintilla ───────────────────────────────────────────────
# QScintilla has no PySide6 binding (Riverbank ships it for PyQt only).
# When running on PySide6 the editor falls back to QTextEdit + a custom
# Python syntax highlighter (see _PySyntaxHighlighter usage below).
try:
    if _QT == "PyQt6":
        from PyQt6.Qsci import QsciScintilla, QsciLexerPython
    elif _QT == "PyQt5":
        from PyQt5.Qsci import QsciScintilla, QsciLexerPython
    else:
        raise ImportError
    _QSCI = True
except ImportError:
    _QSCI = False

# ── Optional: matplotlib ──────────────────────────────────────────────────
try:
    import matplotlib
    matplotlib.use("QtAgg")
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.figure import Figure
    import matplotlib.pyplot as plt
    _MPL = True
except Exception:
    _MPL = False

# ── Optional: numpy ───────────────────────────────────────────────────────
try:
    import numpy as np
    _NP = True
except ImportError:
    _NP = False


# ═══════════════════════════════════════════════════════════════════════════
# Physics constants table
# ═══════════════════════════════════════════════════════════════════════════

PHYSICS_CONSTANTS = [
    # (Symbol, Name, Value, Unit, Category)
    ("c",    "Speed of light",              "2.997 924 58 × 10⁸",   "m/s",          "Universal"),
    ("h",    "Planck constant",             "6.626 070 15 × 10⁻³⁴", "J·s",          "Universal"),
    ("ħ",    "Reduced Planck (ℏ)",          "1.054 571 817 × 10⁻³⁴","J·s",          "Universal"),
    ("G",    "Gravitational constant",      "6.674 30 × 10⁻¹¹",     "m³/(kg·s²)",   "Universal"),
    ("kB",   "Boltzmann constant",          "1.380 649 × 10⁻²³",    "J/K",          "Thermodynamic"),
    ("NA",   "Avogadro constant",           "6.022 140 76 × 10²³",  "mol⁻¹",         "Chemical"),
    ("R",    "Gas constant",               "8.314 462 618",         "J/(mol·K)",    "Thermodynamic"),
    ("σ",    "Stefan-Boltzmann",           "5.670 374 419 × 10⁻⁸",  "W/(m²·K⁴)",    "Thermodynamic"),
    ("e",    "Elementary charge",          "1.602 176 634 × 10⁻¹⁹", "C",            "Electromagnetic"),
    ("ε₀",   "Vacuum permittivity",        "8.854 187 8128 × 10⁻¹²","F/m",          "Electromagnetic"),
    ("μ₀",   "Vacuum permeability",        "1.256 637 061 × 10⁻⁶",  "N/A²",         "Electromagnetic"),
    ("me",   "Electron mass",             "9.109 383 7015 × 10⁻³¹","kg",           "Atomic"),
    ("mp",   "Proton mass",               "1.672 621 923 × 10⁻²⁷", "kg",           "Atomic"),
    ("mn",   "Neutron mass",              "1.674 927 471 × 10⁻²⁷", "kg",           "Atomic"),
    ("mμ",   "Muon mass",                 "1.883 531 627 × 10⁻²⁸", "kg",           "Atomic"),
    ("α",    "Fine-structure constant",   "7.297 352 5693 × 10⁻³", "(dimensionless)","Atomic"),
    ("a₀",   "Bohr radius",               "5.291 772 109 × 10⁻¹¹", "m",            "Atomic"),
    ("Ry",   "Rydberg constant",          "1.097 373 156 848 × 10⁷","m⁻¹",          "Atomic"),
    ("Eh",   "Hartree energy",            "4.359 744 650 × 10⁻¹⁸", "J",            "Atomic"),
    ("F",    "Faraday constant",          "96 485.332 12",         "C/mol",        "Electromagnetic"),
    ("Φ₀",   "Magnetic flux quantum",     "2.067 833 848 × 10⁻¹⁵", "Wb",           "Electromagnetic"),
    ("RK",   "Von Klitzing constant",     "25 812.807 45",         "Ω",            "Electromagnetic"),
    ("g",    "Standard gravity",          "9.806 65",              "m/s²",         "Geophysical"),
    ("atm",  "Standard atmosphere",       "101 325",               "Pa",           "Geophysical"),
    ("ly",   "Light-year",                "9.460 730 472 × 10¹⁵",  "m",            "Astronomical"),
    ("pc",   "Parsec",                    "3.085 677 581 × 10¹⁶",  "m",            "Astronomical"),
    ("AU",   "Astronomical unit",         "1.495 978 707 × 10¹¹",  "m",            "Astronomical"),
    ("M☉",   "Solar mass",                "1.988 416 × 10³⁰",      "kg",           "Astronomical"),
    ("R☉",   "Solar radius",              "6.957 × 10⁸",           "m",            "Astronomical"),
    ("L☉",   "Solar luminosity",          "3.828 × 10²⁶",          "W",            "Astronomical"),
    ("eV",   "Electron-volt",             "1.602 176 634 × 10⁻¹⁹", "J",            "Units"),
    ("u",    "Atomic mass unit",          "1.660 539 066 × 10⁻²⁷", "kg",           "Units"),
]

# Numeric values for calculator lookups
PHYSICS_VALUES: Dict[str, float] = {
    "c":    2.99792458e8,
    "h":    6.62607015e-34,
    "hbar": 1.054571817e-34,
    "G":    6.67430e-11,
    "kB":   1.380649e-23,
    "NA":   6.02214076e23,
    "R":    8.314462618,
    "e":    1.602176634e-19,
    "eps0": 8.8541878128e-12,
    "mu0":  1.25663706212e-6,
    "me":   9.1093837015e-31,
    "mp":   1.67262192369e-27,
    "mn":   1.67492749804e-27,
    "alpha":7.2973525693e-3,
    "a0":   5.29177210903e-11,
    "g":    9.80665,
    "sigma":5.670374419e-8,
    "F":    96485.33212,
    "eV":   1.602176634e-19,
    "u":    1.66053906660e-27,
    "pi":   math.pi,
    "inf":  math.inf,
}


# ═══════════════════════════════════════════════════════════════════════════
# Python syntax highlighter (fallback when QSci not available)
# ═══════════════════════════════════════════════════════════════════════════


# Re-export every name defined above (incl. optional/underscored) so that
# `from eli.gui.labs._common import *` reproduces the monolith's exact module
# namespace for each extracted class — optional symbols (QsciScintilla, plt, np)
# are included only when their import succeeded, mirroring the original.
__all__ = [_n for _n in dir() if not _n.startswith('__')]

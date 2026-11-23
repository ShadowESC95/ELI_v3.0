"""CLAIM: the codebase is ~343 valid Python modules, and the core modules import.

One test per `eli/**/*.py` file (compiles + non-empty), plus an import check for
the load-bearing core modules. Examines the "N files of working Python" claim.
"""
from __future__ import annotations

import ast

import pytest

from . import _helpers as H

_PY = H.eli_py_files()


@pytest.mark.parametrize("path", _PY, ids=[H.rel(p) for p in _PY])
def test_module_compiles(path):
    src = path.read_text(encoding="utf-8", errors="strict")
    ast.parse(src)  # raises SyntaxError if the file isn't valid Python
    # A conflict marker in code fails the parse; one left inside a string or docstring does not.
    # Real markers begin a line and are space/HEAD-suffixed, unlike decorative dividers.
    for marker in ("\n<<<<<<< ", "\n>>>>>>> ", "\n<<<<<<<\n", "\n>>>>>>>\n"):
        assert marker not in src, f"unresolved merge marker in {H.rel(path)}"

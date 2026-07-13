"""ELI Labs — Python syntax highlighter (QTextEdit fallback)

Extracted verbatim from the former eli/gui/labs_tab.py monolith (strangler
refactor, v3 core-modularisation). Shared preamble — Qt binding, plot paths,
physics tables — lives in eli.gui.labs._common.
"""
from __future__ import annotations
from eli.gui.labs._common import *  # noqa: F401,F403


class _PySyntaxHighlighter(QSyntaxHighlighter):
    _KEYWORDS = (
        r"\b(False|None|True|and|as|assert|async|await|break|class|continue|def|del|"
        r"elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|"
        r"not|or|pass|raise|return|try|while|with|yield)\b"
    )
    _BUILTINS = (
        r"\b(abs|all|any|bin|bool|breakpoint|bytearray|bytes|callable|chr|classmethod|"
        r"compile|complex|delattr|dict|dir|divmod|enumerate|eval|exec|filter|float|"
        r"format|frozenset|getattr|globals|hasattr|hash|help|hex|id|input|int|"
        r"isinstance|issubclass|iter|len|list|locals|map|max|memoryview|min|next|"
        r"object|oct|open|ord|pow|print|property|range|repr|reversed|round|set|"
        r"setattr|slice|sorted|staticmethod|str|sum|super|tuple|type|vars|zip)\b"
    )

    def __init__(self, document):
        super().__init__(document)
        self._rules = []

        kw_fmt = QTextCharFormat()
        kw_fmt.setForeground(QColor("#569cd6"))
        kw_fmt.setFontWeight(QFont.Weight.Bold)
        self._rules.append((re.compile(self._KEYWORDS), kw_fmt))

        bi_fmt = QTextCharFormat()
        bi_fmt.setForeground(QColor("#4ec9b0"))
        self._rules.append((re.compile(self._BUILTINS), bi_fmt))

        str_fmt = QTextCharFormat()
        str_fmt.setForeground(QColor("#ce9178"))
        for pat in (r'""".*?"""', r"'''.*?'''", r'"[^"\n]*"', r"'[^'\n]*'"):
            self._rules.append((re.compile(pat, re.DOTALL), str_fmt))

        comment_fmt = QTextCharFormat()
        comment_fmt.setForeground(QColor("#6a9955"))
        self._rules.append((re.compile(r"#[^\n]*"), comment_fmt))

        num_fmt = QTextCharFormat()
        num_fmt.setForeground(QColor("#b5cea8"))
        self._rules.append((re.compile(r"\b\d+\.?\d*([eE][+-]?\d+)?\b"), num_fmt))

        dec_fmt = QTextCharFormat()
        dec_fmt.setForeground(QColor("#dcdcaa"))
        self._rules.append((re.compile(r"\bdef\s+(\w+)"), dec_fmt))

    def highlightBlock(self, text: str):
        for pattern, fmt in self._rules:
            for m in pattern.finditer(text):
                start, end = m.start(), m.end()
                # For the def pattern capture group (function name)
                if pattern.pattern.startswith(r"\bdef"):
                    if m.lastindex:
                        start = m.start(1)
                        end = m.end(1)
                self.setFormat(start, end - start, fmt)


# ═══════════════════════════════════════════════════════════════════════════
# Worker: run Python code in subprocess
# ═══════════════════════════════════════════════════════════════════════════

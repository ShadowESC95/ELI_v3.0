#!/usr/bin/env python3
"""Build a polished PDF of capabilities_and_actions.md, matching the user-manual style.

The capabilities doc is pure markdown tables (no diagrams) and uses only DejaVu-covered
glyphs (curly quotes, middot, arrow, em-dash), so this is a straight pandoc + xelatex
render: a title page, an auto table of contents, and the tables wrapped via longtable.
The source .md's own H1 is dropped (pandoc --title renders it) to avoid a duplicate.

Run:  .venv/bin/python blueprints/build_capabilities_pdf.py
"""
import re, subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "capabilities_and_actions.md"
BUILD_MD = HERE / "_capabilities_pdf_build.md"
OUT_PDF = HERE / "capabilities_and_actions.pdf"


def _weight_tables(text: str) -> str:
    # Size a table's columns to its ACTUAL content, not a fixed template. A fixed
    # weighting can only favour one shape — it starved the "Purpose" column of
    # package tables (name|num|num|text) while over-feeding a numeric column, yet
    # a sector table (name|code|num|short) needs the middle column wide instead.
    # Measuring per table serves both: the widest column (whichever it is) gets the
    # room, numeric columns stay tight, and the longest unbreakable token sets a
    # floor so a monospace path can't overrun its cell.
    #
    # Only tables with a genuinely wide column (content over WRAP chars, i.e. one
    # that MUST wrap) are given explicit p{} widths; tables that already fit at
    # natural width — e.g. the 5-column before→after tables — are left untouched,
    # because natural width renders them perfectly.
    CAP, FLOOR, WRAP = 42, 4, 40

    def _plain(cell):
        t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell)  # links -> text
        return t.replace("`", "").replace("**", "").replace("*", "")

    def _cells(line, n):
        parts = [c.strip() for c in line.strip().strip("|").split("|")]
        return (parts + [""] * n)[:n]

    def _is_row(line):
        s = line.strip()
        return s.startswith("|") and s.endswith("|") and s.count("|") >= 2

    def _is_sep(line):
        return _is_row(line) and set(line.strip()) <= set("|-: ")

    lines = text.split("\n")
    out, i = [], 0
    while i < len(lines):
        if i + 1 < len(lines) and _is_row(lines[i]) and _is_sep(lines[i + 1]):
            sep = lines[i + 1]
            ncols = sep.strip().count("|") - 1
            j = i + 2
            body = []
            while j < len(lines) and _is_row(lines[j]) and not _is_sep(lines[j]):
                body.append(lines[j]); j += 1
            if ncols in (4, 5, 6, 7):
                widths = [FLOOR] * ncols
                raw_max = 0
                for row in [lines[i]] + body:
                    for c, cell in enumerate(_cells(row, ncols)):
                        p = _plain(cell)
                        raw_max = max(raw_max, len(p))
                        # Weight by the longest UNBREAKABLE token, not the whole cell:
                        # a text column wraps at spaces so it needs only its longest
                        # word, while a monospace column (an action name / path) can't
                        # wrap and must get its full token width. Weighting by cell
                        # length instead lets two wide text columns starve a narrow-
                        # looking monospace one until its names overrun the next column.
                        longest = max((len(tok) for tok in p.split()), default=0)
                        widths[c] = max(widths[c], min(longest, CAP))
                if raw_max <= WRAP:            # fits at natural width — leave it
                    out.append(lines[i]); i += 1
                    continue
                aligns = ["c" if s.startswith(":") and s.endswith(":") else
                          "r" if s.endswith(":") else "l" if s.startswith(":") else "d"
                          for s in _cells(sep, ncols)]
                new = "|"
                for c in range(ncols):
                    d = "-" * max(widths[c], 3)
                    new += ({"c": f":{d}:", "r": f"{d}:", "l": f":{d}"}.get(aligns[c], d)) + "|"
                out.append(lines[i]); out.append(new); out.extend(body)
                i = j
                continue
        out.append(lines[i]); i += 1
    return "\n".join(out)


def main() -> int:
    text = SRC.read_text(encoding="utf-8")
    # Drop the H1 title + the italic subtitle line (pandoc --title/--subtitle render them),
    # so the PDF doesn't show the title twice.
    text = re.sub(r"^# ELI — Capabilities & Actions.*\n", "", text, count=1, flags=re.M)
    text = re.sub(r"^\*Auto-generated.*\n", "", text, count=1, flags=re.M)

    # Size the tables to their actual content so long action names (e.g.
    # CONFIRM_PENDING_REMEDIATION) and the wide "Example phrase(s)" column get room
    # instead of overflowing a fixed template. Same content-measured widths as
    # build_blueprint_pdf.py.
    text = _weight_tables(text)
    BUILD_MD.write_text(text, encoding="utf-8")

    subtitle = "Every routable action + representative activation phrases"
    cmd = [
        "pandoc", str(BUILD_MD), "-o", str(OUT_PDF),
        "--pdf-engine=xelatex", "--toc", "--toc-depth=2",
        "-V", "geometry:margin=1.6cm",
        "-V", "mainfont=DejaVu Sans", "-V", "monofont=DejaVu Sans Mono",
        "-V", "fontsize=10pt", "-V", "colorlinks=true", "-V", "linkcolor=blue",
        "-V", "title=ELI — Capabilities and Actions",
        "-V", f"subtitle={subtitle}",
    ]
    print("Running pandoc -> xelatex ...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("pandoc FAILED:\n", r.stderr[-2500:])
        return 1
    BUILD_MD.unlink(missing_ok=True)
    print(f"\n✅ PDF built: {OUT_PDF}  ({OUT_PDF.stat().st_size/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

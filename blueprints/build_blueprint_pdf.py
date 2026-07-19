#!/usr/bin/env python3
"""Convert blueprint .md files to styled PDFs (manual-style), rendering any ```mermaid
flowcharts to PNG via kroki. Reusable for any doc.

  .venv/bin/python blueprints/build_blueprint_pdf.py <file.md> [file2.md ...]
  .venv/bin/python blueprints/build_blueprint_pdf.py --amended   # the docs changed this session

Handles: mermaid → kroki PNG, emoji → text (no colour-emoji font locally), 4-column table
width weighting, dropping the markdown's own H1/subtitle (pandoc --title renders it), and
escaping LaTeX-hostile chars in the title.
"""
import re, subprocess, sys, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ASSETS = HERE / "pdf_assets"

EMOJI_MAP = {"🟢": "[basic] ", "🟡": "[intermediate] ", "🔴": "[advanced] ",
             "➡️": "->", "✅": "[done]", "❌": "[no]", "⚠️": "[!]", "✓": "-", "✗": "x", "⚛️": ""}
DECORATIVE = "📓🧠🧮📂🖥️🔬🧭🧪📄🗓️💬🎯🖼️⚡🌍⚙️🔧🎵🪟🔊✍️📊🌐⏰🧩🔍↻▶▶️"

AMENDED = [
    "gui.md", "what_eli_can_do.md", "project_overview.md", "state_snapshot.md",
    "capability_catalogue.md", "session_2026-06-18_hardening_scaling_and_release.md",
    "code_mode_execution_layer.md", "what_eli_is.md",
    "local_model_bakeoff_2026-06-17.md", "model_bakeoff_dossier.md",
]


def _render_mermaid(code: str, idx: int) -> Path | None:
    try:
        req = urllib.request.Request("https://kroki.io/mermaid/png", data=code.encode(),
                                     headers={"Content-Type": "text/plain",
                                              "User-Agent": "Mozilla/5.0 (eli-pdf)"}, method="POST")
        with urllib.request.urlopen(req, timeout=40) as r:
            data = r.read()
        if data[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        ASSETS.mkdir(exist_ok=True)
        p = ASSETS / f"{idx}.png"; p.write_bytes(data); return p
    except Exception:
        return None


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


def convert(md: Path) -> bool:
    text = md.read_text(encoding="utf-8")
    # Title = first H1; strip it + an italic subtitle so pandoc --title isn't duplicated.
    m = re.search(r"^#\s+(.+)$", text, flags=re.M)
    title = (m.group(1) if m else md.stem).replace("&", "and").replace("`", "")
    title = re.sub(r"[*_]", "", title)
    text = re.sub(r"^#\s+.+\n", "", text, count=1, flags=re.M)
    text = re.sub(r"^\*[^*\n].*\*\s*\n", "", text, count=1, flags=re.M)

    # Render mermaid blocks → images (replace from the end to keep offsets valid).
    blocks = list(re.finditer(r"```mermaid\n(.*?)\n```", text, re.DOTALL))
    for i, blk in enumerate(reversed(blocks), 1):
        idx = f"{md.stem}_{len(blocks)-i+1}"
        png = _render_mermaid(blk.group(1), idx)
        repl = (f"\n![]({png.as_posix()})\\\n" if png else "```\n" + blk.group(1) + "\n```")
        text = text[:blk.start()] + repl + text[blk.end():]

    for e, t in EMOJI_MAP.items():
        text = text.replace(e, t)
    for ch in DECORATIVE:
        text = text.replace(ch + " ", "").replace(ch, "")
    text = _weight_tables(text)

    build = HERE / f"_{md.stem}_build.md"; build.write_text(text, encoding="utf-8")
    out = md.with_suffix(".pdf")
    base = ["pandoc", str(build), "-o", str(out), "--pdf-engine=xelatex",
            "--toc", "--toc-depth=2", "-V", "geometry:margin=1.7cm",
            "-V", "mainfont=DejaVu Sans", "-V", "monofont=DejaVu Sans Mono",
            "-V", "fontsize=10pt", "-V", "colorlinks=true", "-V", "linkcolor=blue",
            "-V", f"title={title}", "--resource-path", str(HERE)]
    r = subprocess.run(base, capture_output=True, text=True)
    if r.returncode != 0:
        # Common failure: literal `$` (prices) or `$VAR` (shell) parsed as TeX math. Retry
        # with math/raw-tex disabled so those are treated as plain text.
        r = subprocess.run(base + ["-f", "markdown-raw_tex-tex_math_dollars-tex_math_single_backslash"],
                           capture_output=True, text=True)
    build.unlink(missing_ok=True)
    if r.returncode != 0:
        print(f"  ✗ {md.name}: {r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'pandoc failed'}")
        return False
    print(f"  ✓ {md.name} → {out.name} ({out.stat().st_size//1024} KB)")
    return True


def main() -> int:
    args = sys.argv[1:]
    files = ([HERE / f for f in AMENDED] if (not args or args[0] == "--amended")
             else [Path(a) for a in args])
    ok = 0
    for f in files:
        if f.is_file() and convert(f):
            ok += 1
        elif not f.is_file():
            print(f"  ? missing: {f}")
    print(f"\n{ok}/{len(files)} converted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

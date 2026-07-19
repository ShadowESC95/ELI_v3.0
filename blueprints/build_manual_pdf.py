#!/usr/bin/env python3
"""Build a polished PDF of ELI_USER_MANUAL.md with the Mermaid flowcharts rendered.

Pipeline: extract ```mermaid blocks -> render each to PNG via kroki.io -> swap the fences
for image refs in a PDF-only copy -> map/strip emoji (no colour-emoji font locally) ->
pandoc + xelatex -> PDF. The source .md is untouched (keeps emoji + mermaid for GitHub).

Run:  .venv/bin/python blueprints/build_manual_pdf.py
"""
import base64, re, subprocess, sys, time, urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "ELI_USER_MANUAL.md"
ASSETS = HERE / "manual_assets"
BUILD_MD = HERE / "_manual_pdf_build.md"
OUT_PDF = HERE / "ELI_USER_MANUAL.pdf"

# Emoji -> text (meaningful tags) or "" (decorative). The .md keeps the originals.
EMOJI_MAP = {
    "🟢": "[Anyone] ", "🟡": "[A bit technical] ", "🔴": "[Advanced] ",
    "➡️": "->", "✓": "-", "✗": "x",
}
DECORATIVE = "🎵🪟🔊📄✍️📊🌐⏰🔧🧩"


def render_mermaid(code: str, idx: int) -> Path | None:
    """Render one mermaid block to PNG via kroki, retrying transient failures.

    kroki.io intermittently 400s / rate-limits a burst of requests — the SAME
    source that fails in the batch succeeds on retry. Without retrying, a flaked
    block fell back to a raw code block in the PDF, silently dropping a diagram
    that had rendered fine before.
    """
    last = "unknown error"
    for attempt in range(5):
        try:
            req = urllib.request.Request(
                "https://kroki.io/mermaid/png", data=code.encode("utf-8"),
                headers={"Content-Type": "text/plain",
                         "User-Agent": "Mozilla/5.0 (ELI-manual-build)"}, method="POST")
            with urllib.request.urlopen(req, timeout=40) as r:
                data = r.read()
            if data[:8] != b"\x89PNG\r\n\x1a\n":
                last = "response was not a PNG"
            else:
                ASSETS.mkdir(exist_ok=True)
                p = ASSETS / f"diagram_{idx:02d}.png"
                p.write_bytes(data)
                print(f"  ok block {idx}: {len(data)} bytes -> {p.name}"
                      + (f" (attempt {attempt + 1})" if attempt else ""))
                return p
        except Exception as e:
            last = str(e)
        time.sleep(1.5 * (attempt + 1))  # back off, then retry the same source
    print(f"  ! block {idx}: {last} (gave up after 5 tries)")
    return None


def _weight_tables(text: str) -> str:
    # Size a table's columns to its ACTUAL content, not a fixed template, so the
    # widest column (whichever it is) gets the room and numeric columns stay tight.
    # Only tables with a genuinely wide column (content over WRAP chars) are given
    # explicit p{} widths; narrower tables render fine at natural width and are left
    # alone. Columns are weighted by their longest UNBREAKABLE token (a text column
    # wraps at spaces; a monospace name/path can't) so a monospace column never
    # overruns the next one. Shared verbatim with build_blueprint_pdf.py.
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

    # PDF-only de-duplication: pandoc's --title and --toc already render the title and a
    # contents list, so strip the markdown's own H1 title block and the hand-written
    # "## Table of contents" section (which exists for GitHub anchors). The source .md
    # keeps both. Without this the PDF showed the title and a contents list twice.
    text = re.sub(r"^# ELI .*User Manual\s*\n", "", text, count=1, flags=re.M)
    text = re.sub(r"^### Plain-English Edition.*\n", "", text, count=1, flags=re.M)
    text = re.sub(r"^\*A friendly, no-jargon guide.*\n", "", text, count=1, flags=re.M)
    text = re.sub(r"## Table of contents\n.*?\n---\n", "", text, count=1, flags=re.DOTALL)

    blocks = list(re.finditer(r"```mermaid\n(.*?)\n```", text, re.DOTALL))
    print(f"Found {len(blocks)} mermaid blocks; rendering via kroki...")

    # Replace from the end so offsets stay valid.
    out = text
    for i, m in enumerate(reversed(blocks), start=1):
        idx = len(blocks) - i + 1
        png = render_mermaid(m.group(1), idx)
        if png:
            repl = f"\n![]({png.as_posix()})\\\n"
        else:
            # Fallback: keep the diagram source as a readable code block.
            repl = "```\n" + m.group(1) + "\n```"
        out = out[:m.start()] + repl + out[m.end():]

    # Emoji handling (PDF only).
    for e, t in EMOJI_MAP.items():
        out = out.replace(e, t)
    for ch in DECORATIVE:
        out = out.replace(ch + " ", "").replace(ch, "")

    # Content-measured table widths (shared with the other blueprint builders). The
    # manual's tables are all 2–3 columns and already fit at natural width, so this
    # is a no-op here — it's in place for consistency and any future wide table.
    out = _weight_tables(out)

    BUILD_MD.write_text(out, encoding="utf-8")

    cmd = [
        "pandoc", str(BUILD_MD), "-o", str(OUT_PDF),
        "--pdf-engine=xelatex", "--toc", "--toc-depth=2", "-V", "geometry:margin=2cm",
        "-V", "mainfont=DejaVu Sans", "-V", "monofont=DejaVu Sans Mono",
        "-V", "colorlinks=true", "-V", "linkcolor=blue", "-V", "toccolor=black",
        "-V", "title=ELI — The Complete User Manual",
        "-V", "subtitle=Plain-English Edition · with flowcharts",
        "--resource-path", str(HERE),
    ]
    print("Running pandoc -> xelatex ...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("pandoc FAILED:\n", r.stderr[-2500:])
        return 1
    BUILD_MD.unlink(missing_ok=True)
    size = OUT_PDF.stat().st_size
    print(f"\n✅ PDF built: {OUT_PDF}  ({size/1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

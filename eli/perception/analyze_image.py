#!/usr/bin/env python3
"""
ELI image analysis helper.

Analyzes an image file and writes a Markdown report.
Supports: PIL/Pillow metadata extraction, optional OCR via tesseract.

Usage (module):
    from eli.perception.analyze_image import analyze_image_file
    result = analyze_image_file("/path/to/img.png", "/path/to/report.md")

Usage (CLI):
    python3 -m eli.perception.analyze_image --path img.png --out report.md
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ocr_text(path: Path, max_chars: int = 2000) -> str:
    """The image's text through the one OCR helper (screen_locator.ocr_text_from_image), which has
    tesseract print to stdout: nothing is written beside the user's image, so analysing notes.png
    never creates or overwrites a notes.txt next to it. Page segmentation 3 (tesseract's own
    default, a whole page) suits a document or screenshot; empty when no OCR is installed."""
    from eli.perception.screen_locator import ocr_text_from_image
    try:
        text = ocr_text_from_image(path, psm=3)
    except Exception:
        return ""
    return " ".join(str(text or "").split())[:max_chars]


def _pil_info(path: Path) -> Dict[str, Any]:
    """Extract basic metadata via Pillow; return {} if not installed."""
    try:
        from PIL import Image  # type: ignore
        with Image.open(path) as img:
            return {
                "format": img.format or path.suffix.lstrip(".").upper(),
                "mode": img.mode,
                "width": img.width,
                "height": img.height,
            }
    except Exception:
        return {
            "format": path.suffix.lstrip(".").upper() or "unknown",
            "mode": "unknown",
            "width": None,
            "height": None,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_image_file(
    path: str,
    out_md: str,
    *,
    ocr: bool = True,
    max_ocr_chars: int = 2000,
) -> Dict[str, Any]:
    """
    Analyze an image file and write a Markdown report.

    Returns a dict with keys: ok, path, format, width, height, ocr_text,
    artifacts_dir, out_md, analyzed_at.
    """
    p = Path(path).expanduser().resolve()
    outp = Path(out_md).expanduser().resolve()
    outp.parent.mkdir(parents=True, exist_ok=True)

    if not p.exists():
        return {"ok": False, "error": f"File not found: {p}", "path": str(p)}

    artifacts_dir = outp.parent / f"analyze_image_{time.strftime('%Y%m%d_%H%M%S')}"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    info = _pil_info(p)
    file_size = p.stat().st_size

    ocr_text = ""
    if ocr:
        ocr_text = _ocr_text(p, max_chars=max_ocr_chars)

    report = {
        "ok": True,
        "path": str(p),
        "format": info.get("format", "unknown"),
        "mode": info.get("mode", "unknown"),
        "width": info.get("width"),
        "height": info.get("height"),
        "size_bytes": file_size,
        "ocr_text": ocr_text,
        "artifacts_dir": str(artifacts_dir),
        "out_md": str(outp),
        "analyzed_at": time.time(),
    }

    (artifacts_dir / "report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )

    # Build Markdown
    md: list[str] = []
    md.append(f"# Image analysis: `{p.name}`\n\n")
    md.append(f"- **Format**: {report['format']}  \n")
    if report["width"] and report["height"]:
        md.append(f"- **Dimensions**: {report['width']} × {report['height']} px  \n")
    md.append(f"- **File size**: {file_size:,} bytes  \n")
    md.append(f"- **Color mode**: {report['mode']}  \n")
    if ocr_text:
        md.append("\n## OCR text\n\n")
        md.append(ocr_text[:1500])
        if len(ocr_text) > 1500:
            md.append("\n\n*(truncated)*")
        md.append("\n")
    else:
        md.append("\n*No OCR text extracted (tesseract not available or no text detected).*\n")

    outp.write_text("".join(md), encoding="utf-8")
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Analyze an image file")
    ap.add_argument("--path", required=True, help="Path to image file")
    ap.add_argument("--out", default="artifacts/image_report_latest.md", help="Output Markdown path")
    ap.add_argument("--no-ocr", action="store_true", help="Disable OCR")
    ns = ap.parse_args()
    result = analyze_image_file(ns.path, ns.out, ocr=not ns.no_ocr)
    print(json.dumps(result, indent=2))

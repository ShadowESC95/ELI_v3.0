"""ANALYZE_IMAGE's OCR must never write into the user's own folder.

It ran `tesseract <image> <image without suffix>`, and tesseract writes <base>.txt: analysing
notes.png overwrote a notes.txt beside it, or left a stray .txt next to every image analysed.
A stand-in tesseract on PATH behaves as the real one does with each kind of output base.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from eli.perception.analyze_image import analyze_image_file

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the stand-in tesseract is a shell script")

_FAKE_TESSERACT = """#!/bin/sh
# like tesseract: `tesseract IMAGE stdout ...` prints the text, `tesseract IMAGE BASE` writes BASE.txt
if [ "$2" = "stdout" ]; then
  echo "Invoice total 42"
else
  echo "Invoice total 42" > "$2.txt"
fi
"""


@pytest.fixture
def fake_tesseract(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "tesseract"
    exe.write_text(_FAKE_TESSERACT)
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    return exe


def _image(folder: Path) -> Path:
    from PIL import Image
    folder.mkdir(parents=True, exist_ok=True)
    img = folder / "notes.png"
    Image.new("RGB", (8, 8), "white").save(img)
    return img


def test_ocr_leaves_the_users_folder_as_it_was(tmp_path, fake_tesseract):
    folder = tmp_path / "user_folder"
    img = _image(folder)
    mine = folder / "notes.txt"
    mine.write_text("my own notes, not OCR output")
    before = sorted(p.name for p in folder.iterdir())

    rep = analyze_image_file(str(img), str(tmp_path / "out" / "report.md"), ocr=True)

    assert rep["ok"] is True
    assert rep["ocr_text"] == "Invoice total 42", "the image's text was not read"
    assert mine.read_text() == "my own notes, not OCR output", "the user's notes.txt was overwritten"
    assert sorted(p.name for p in folder.iterdir()) == before, "OCR left a file in the user's folder"


def test_ocr_never_reports_an_old_text_file_as_the_images_text(tmp_path, monkeypatch):
    """When tesseract fails, a notes.txt that happens to sit beside notes.png is not the image's text."""
    folder = tmp_path / "user_folder"
    img = _image(folder)
    (folder / "notes.txt").write_text("unrelated words")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    failing = bin_dir / "tesseract"
    failing.write_text("#!/bin/sh\nexit 1\n")
    failing.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.setitem(sys.modules, "pytesseract", None)          # no Python OCR either
    rep = analyze_image_file(str(img), str(tmp_path / "out" / "report.md"), ocr=True)
    assert rep["ocr_text"] == ""

"""Turn a file (PDF, image, text) into plain text. Scanned PDFs fall back to OCR page by page."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}


def ocr_image(path: Path) -> str:
    import pytesseract
    from PIL import Image

    with Image.open(path) as img:
        return pytesseract.image_to_string(img.convert("L"), config="--psm 6")


def _pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def _ocr_pdf(path: Path) -> str:
    if not shutil.which("pdftoppm"):
        return ""
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-r", "200", "-png", str(path), f"{tmp}/page"], check=True)
        return "\n".join(ocr_image(p) for p in sorted(Path(tmp).glob("page*.png")))


def extract_text(path: str | Path) -> tuple[str, str]:
    """Return (text, method) where method is one of: text, pdf_text, pdf_ocr, image_ocr."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in IMAGE_TYPES:
        return ocr_image(p), "image_ocr"
    if suffix == ".pdf":
        text = _pdf_text(p)
        if len(text.strip()) >= 20:
            return text, "pdf_text"
        return _ocr_pdf(p), "pdf_ocr"
    return p.read_text(encoding="utf-8", errors="ignore"), "text"

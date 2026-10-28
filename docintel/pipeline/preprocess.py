"""Preprocessing (F2): native-text vs scanned detection, page splitting,
deskew and denoise for scanned pages."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
import pymupdf as fitz

MIN_NATIVE_CHARS = 25
MAX_SKEW = 6.0  # degrees searched each way


@dataclass
class Page:
    index: int
    width: float
    height: float
    native: bool
    image: np.ndarray | None = None  # grayscale, only for scanned pages


def deskew(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """Projection-profile skew search: text lines give the sharpest row-sum
    profile when level. Runs on a downscaled copy, so it costs a few ms."""
    scale = min(1.0, 800 / gray.shape[1])
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    inv = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    if cv2.countNonZero(inv) < 50:
        return gray, 0.0
    sh, sw = inv.shape
    best, angle = -1.0, 0.0
    for a in np.arange(-MAX_SKEW, MAX_SKEW + 1e-6, 0.25):
        m = cv2.getRotationMatrix2D((sw / 2, sh / 2), a, 1.0)
        score = float(np.var(cv2.warpAffine(inv, m, (sw, sh)).sum(axis=1, dtype=np.float64)))
        if score > best:
            best, angle = score, float(a)
    if abs(angle) < 0.1:
        return gray, 0.0
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    out = cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)
    return out, angle


def denoise(gray: np.ndarray) -> np.ndarray:
    # Median blur removes scanner speckle; non-local means was ~10x slower for no OCR gain.
    return cv2.medianBlur(gray, 3)


def load_pages(path: str, dpi: int = 200) -> tuple[fitz.Document, list[Page]]:
    """Open PDF or image; image files are wrapped as a single-page PDF."""
    doc = fitz.open(path)
    if not doc.is_pdf:
        doc = fitz.open("pdf", doc.convert_to_pdf())
    pages = []
    for i, p in enumerate(doc):
        native = len(p.get_text().strip()) >= MIN_NATIVE_CHARS
        img = None
        if not native:
            pix = p.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
            img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
            img, _ = deskew(denoise(img))
        pages.append(Page(i, p.rect.width, p.rect.height, native, img))
    return doc, pages

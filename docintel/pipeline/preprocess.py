"""Preprocessing (F2): native-text vs scanned detection, page splitting,
deskew and denoise for scanned pages."""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import pymupdf as fitz
import numpy as np

MIN_NATIVE_CHARS = 25


@dataclass
class Page:
    index: int
    width: float
    height: float
    native: bool
    image: np.ndarray | None = None  # grayscale, only for scanned pages


def deskew(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """Estimate skew from the min-area rectangle around ink pixels."""
    inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    pts = np.column_stack(np.where(inv > 0))
    if len(pts) < 50:
        return gray, 0.0
    angle = cv2.minAreaRect(pts[:, ::-1].astype(np.float32))[-1]
    if angle > 45:
        angle -= 90
    h, w = gray.shape
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    out = cv2.warpAffine(gray, m, (w, h), flags=cv2.INTER_CUBIC, borderValue=255)
    return out, angle


def denoise(gray: np.ndarray) -> np.ndarray:
    return cv2.fastNlMeansDenoising(cv2.medianBlur(gray, 3), h=10)


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

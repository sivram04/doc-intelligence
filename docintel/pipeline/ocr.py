"""OCR and layout (F3): text spans with boxes in reading order, plus
key-value pairs. Native PDFs use PyMuPDF; scanned pages use Tesseract when
installed (baseline) — PaddleOCR/docTR/Surya plug in behind the same API."""
from __future__ import annotations

from dataclasses import dataclass

import pymupdf as fitz

from .preprocess import Page


@dataclass
class Span:
    text: str
    bbox: tuple[float, float, float, float]
    page: int
    bold: bool = False


@dataclass
class Layout:
    spans: list[Span]
    kv: list[tuple[Span, Span]]

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.spans)


def _native_spans(page: fitz.Page, idx: int) -> list[Span]:
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for s in line["spans"]:
                if s["text"].strip():
                    out.append(Span(s["text"].strip(), tuple(s["bbox"]), idx,
                                    bool(s["flags"] & 16) or "Bold" in s["font"]))
    return out


def _tesseract_spans(page: Page) -> list[Span]:
    try:
        import pytesseract
    except ImportError as e:
        raise RuntimeError("Scanned page needs OCR: pip install pytesseract + Tesseract binary") from e
    d = pytesseract.image_to_data(page.image, output_type=pytesseract.Output.DICT)
    scale = page.width / page.image.shape[1]
    lines: dict[tuple, list[int]] = {}
    for i, t in enumerate(d["text"]):
        if t.strip():
            lines.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(i)
    out = []
    for ids in lines.values():
        x0 = min(d["left"][i] for i in ids) * scale
        y0 = min(d["top"][i] for i in ids) * scale
        x1 = max(d["left"][i] + d["width"][i] for i in ids) * scale
        y1 = max(d["top"][i] + d["height"][i] for i in ids) * scale
        out.append(Span(" ".join(d["text"][i] for i in ids), (x0, y0, x1, y1), page.index))
    return out


def pair_key_values(spans: list[Span]) -> list[tuple[Span, Span]]:
    """Label span -> nearest value span to its right on the same line, else directly below."""
    pairs = []
    for s in spans:
        if not (s.bold or s.text.endswith(":")):
            continue
        cands = [v for v in spans if v is not s and not v.bold and v.page == s.page]
        right = [v for v in cands if abs(v.bbox[1] - s.bbox[1]) < 4 and v.bbox[0] >= s.bbox[2] - 1]
        below = [v for v in cands if 0 < v.bbox[1] - s.bbox[3] < 12 and abs(v.bbox[0] - s.bbox[0]) < 4]
        if right:
            pairs.append((s, min(right, key=lambda v: v.bbox[0])))
        elif below:
            pairs.append((s, min(below, key=lambda v: v.bbox[1])))
    return pairs


def analyze(doc: fitz.Document, pages: list[Page]) -> Layout:
    spans: list[Span] = []
    for p in pages:
        spans += _native_spans(doc[p.index], p.index) if p.native else _tesseract_spans(p)
    spans.sort(key=lambda s: (s.page, round(s.bbox[1] / 3), s.bbox[0]))  # reading order
    return Layout(spans, pair_key_values(spans))

"""OCR and layout (F3): text spans with boxes in reading order, plus
key-value pairs. Native PDFs use PyMuPDF; scanned pages use Tesseract when
installed (baseline) — PaddleOCR/docTR/Surya plug in behind the same API."""
from __future__ import annotations

import os
from dataclasses import dataclass
from itertools import pairwise

import pymupdf as fitz

from .preprocess import Page

WINDOWS_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


@dataclass
class Span:
    text: str
    bbox: tuple[float, float, float, float]
    page: int
    bold: bool | None = False  # None: unknown (OCR)


@dataclass
class Layout:
    spans: list[Span]
    kv: list[tuple[Span, Span]]

    @property
    def text(self) -> str:
        return "\n".join(s.text for s in self.spans)

    def lines(self) -> list[list[int]]:
        """Span ids grouped into visual lines (same page, overlapping vertical extent),
        left to right. Table rows come out as one line of cells."""
        out: list[list[int]] = []
        last = None
        for i in sorted(range(len(self.spans)), key=lambda i: (self.spans[i].page, self.spans[i].bbox[1])):
            s = self.spans[i]
            mid = (s.bbox[1] + s.bbox[3]) / 2
            if last and last[0] == s.page and last[1] <= mid <= last[2]:
                out[-1].append(i)
                last = (s.page, min(last[1], s.bbox[1]), max(last[2], s.bbox[3]))
            else:
                out.append([i])
                last = (s.page, s.bbox[1], s.bbox[3])
        return [sorted(line, key=lambda i: self.spans[i].bbox[0]) for line in out]

    def render(self) -> str:
        """Layout-preserving serialisation for LLMs: one visual line per row, cells as
        `[span id] text` separated by ' | ', with page markers."""
        rows, page = [], None
        for line in self.lines():
            p = self.spans[line[0]].page
            if p != page:
                rows.append(f"--- page {p + 1} ---")
                page = p
            rows.append(" | ".join(f"[{i}] {self.spans[i].text}" for i in line))
        return "\n".join(rows)


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
    cmd = os.getenv("TESSERACT_CMD", WINDOWS_TESSERACT)
    if os.path.exists(cmd):
        pytesseract.pytesseract.tesseract_cmd = cmd
    d = pytesseract.image_to_data(page.image, output_type=pytesseract.Output.DICT)
    scale = page.width / page.image.shape[1]
    lines: dict[tuple, list[int]] = {}
    for i, t in enumerate(d["text"]):
        if t.strip():
            lines.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(i)
    def span(ids: list[int], bold: bool | None) -> Span:
        x0 = min(d["left"][i] for i in ids) * scale
        y0 = min(d["top"][i] for i in ids) * scale
        x1 = max(d["left"][i] + d["width"][i] for i in ids) * scale
        y1 = max(d["top"][i] + d["height"][i] for i in ids) * scale
        return Span(" ".join(d["text"][i] for i in ids), (x0, y0, x1, y1), page.index, bold)

    # OCR has no font info: split "Label: value" lines at the colon; lines
    # without one may be a stacked label or a value (bold=None = unknown).
    # Split a Tesseract line into cells at wide horizontal gaps (table columns), then
    # split "Label: value" cells at the colon.
    cells: list[list[int]] = []
    for ids in lines.values():
        h = sorted(d["height"][i] for i in ids)[len(ids) // 2]
        cur = [ids[0]]
        for a, b in pairwise(ids):
            if d["left"][b] - (d["left"][a] + d["width"][a]) > 1.8 * h:
                cells.append(cur)
                cur = []
            cur.append(b)
        cells.append(cur)
    out = []
    for ids in cells:
        cut = next((k for k, i in enumerate(ids) if d["text"][i].endswith(":")), None)
        if cut is None:
            out.append(span(ids, None))
        else:
            out.append(span(ids[:cut + 1], True))
            if ids[cut + 1:]:
                out.append(span(ids[cut + 1:], False))
    return out


def pair_key_values(spans: list[Span]) -> list[tuple[Span, Span]]:
    """Label span -> nearest value span to its right on the same line, else directly below."""
    pairs = []
    for s in spans:
        if s.bold is False and not s.text.endswith(":"):
            continue
        cands = [v for v in spans if v is not s and v.bold is not True and v.page == s.page]
        right = [v for v in cands if abs(v.bbox[1] - s.bbox[1]) < 4 and v.bbox[0] >= s.bbox[2] - 1]
        below = [v for v in cands if 0 < v.bbox[1] - s.bbox[3] < 14 and abs(v.bbox[0] - s.bbox[0]) < 6]
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

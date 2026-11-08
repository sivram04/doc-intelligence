"""Read a long PDF (native or scanned) into passages that keep their page number."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..pipeline.ocr import analyze
from ..pipeline.preprocess import load_pages

PASSAGE_WORDS = 90  # about one paragraph; small enough to cite, big enough to carry context
MONEY_RE = re.compile(r"(?:[$€£₹]|USD|EUR|GBP|INR|Rs\.?)\s?\d[\d,.]*(?:\s?(?:million|billion|bn|m|k))?", re.I)
DATE_RE = re.compile(r"\b(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{4}-\d{2}-\d{2}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|"
                     r"Oct|Nov|Dec)[a-z]*\.? \d{1,2},? \d{4}|\d{1,2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|"
                     r"Dec)[a-z]* \d{4}|(?:Q[1-4]|FY) ?\d{2,4})\b")


@dataclass
class Passage:
    id: int
    page: int  # 1-based, as printed to users
    text: str


@dataclass
class LongDoc:
    file: str
    pages: list[str]  # full text per page (index 0 = page 1), used to check quotes
    passages: list[Passage]
    meta: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"file": self.file, "pages": self.pages, "passages": [asdict(p) for p in self.passages],
                "meta": self.meta}

    @classmethod
    def from_json(cls, d: dict) -> LongDoc:
        return cls(d["file"], d["pages"], [Passage(**p) for p in d["passages"]], d.get("meta", {}))


def _is_heading(text: str) -> bool:
    words = text.split()
    return 1 <= len(words) <= 10 and not text.rstrip().endswith((".", ",", ";")) and (
        text.isupper() or re.match(r"^(\d+(\.\d+)*\.?|[IVX]+\.|Section|Chapter|Part)\s", text) is not None
        or text.istitle())


def read(path: str | Path, progress=None) -> LongDoc:
    doc, pages = load_pages(str(path))
    layout = analyze(doc, pages)
    page_lines: dict[int, list[str]] = {}
    for line in layout.lines():
        p = layout.spans[line[0]].page
        page_lines.setdefault(p, []).append(" ".join(layout.spans[i].text for i in line))
    texts, passages, outline = [], [], []
    for p in range(len(pages)):
        lines = page_lines.get(p, [])
        texts.append("\n".join(lines))
        buf: list[str] = []

        def flush():
            if buf:
                passages.append(Passage(len(passages), p + 1, " ".join(buf)))
                buf.clear()

        for ln in lines:
            if _is_heading(ln):
                flush()
                outline.append({"heading": ln.strip(), "page": p + 1})
            buf.append(ln)
            words = sum(len(b.split()) for b in buf)
            if words >= 2 * PASSAGE_WORDS or (words >= PASSAGE_WORDS and ln.rstrip().endswith((".", ":", ";"))):
                flush()
        flush()
        if progress:
            progress(p + 1, len(pages))
    info = doc.metadata or {}
    full = "\n".join(texts)
    meta = {"title": info.get("title") or (outline[0]["heading"] if outline else Path(path).stem),
            "author": info.get("author") or None, "created": info.get("creationDate") or None,
            "pages": len(pages), "scanned_pages": sum(not pg.native for pg in pages),
            "words": len(full.split()), "outline": outline[:200],
            "amounts": _mentions(MONEY_RE, texts), "dates": _mentions(DATE_RE, texts)}
    return LongDoc(Path(path).name, texts, passages, meta)


def _mentions(rx: re.Pattern, texts: list[str], limit: int = 40) -> list[dict]:
    """Distinct mentions with the first page they appear on (metadata panel, not claims)."""
    seen: dict[str, int] = {}
    for i, t in enumerate(texts):
        for m in rx.finditer(t):
            seen.setdefault(m.group().strip(), i + 1)
    return [{"text": k, "page": v} for k, v in list(seen.items())[:limit]]

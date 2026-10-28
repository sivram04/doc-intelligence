"""End-to-end pipeline: preprocess -> OCR/layout -> classify -> extract -> validate -> route."""
from __future__ import annotations

import time
from pathlib import Path

from ..extract.base import Extractor
from ..extract.rules import RulesExtractor
from ..schemas import DocType, ExtractionResult
from ..validators import validate_document
from .classify import classify
from .ocr import analyze
from .preprocess import load_pages

DEFAULT_THRESHOLD = 0.85  # per-field thresholds are tuned in Phase 5 (F8)


def process(path: str | Path, extractor: Extractor | None = None) -> ExtractionResult:
    extractor = extractor or RulesExtractor()
    t0 = time.perf_counter()
    doc, pages = load_pages(str(path))
    layout = analyze(doc, pages)
    doc_type, conf = classify(layout.text)
    fields = extractor.extract(layout, doc_type) if doc_type != DocType.OTHER else {}
    result = ExtractionResult(doc_id=Path(path).stem, doc_type=doc_type, type_confidence=conf,
                              model=extractor.name, fields=fields,
                              latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                              tokens=dict(getattr(extractor, "last_usage", {}) or {}))
    result.flags = validate_document(doc_type, fields)
    return result


def route(result: ExtractionResult, thresholds: dict[str, float] | None = None) -> dict[str, list[str]]:
    """Split fields into auto-accepted vs human review (F8)."""
    thresholds = thresholds or {}
    out = {"auto": [], "review": []}
    for name, f in result.fields.items():
        ok = f.value is not None and f.confidence >= thresholds.get(name, DEFAULT_THRESHOLD)
        out["auto" if ok and not result.flags else "review"].append(name)
    return out

"""End-to-end pipeline: preprocess -> OCR/layout -> classify -> extract -> validate -> route."""
from __future__ import annotations

import time
from pathlib import Path

from ..extract.base import Extractor
from ..extract.normalize import normalize
from ..extract.rules import RulesExtractor
from ..routing import field_score, load_thresholds, threshold_for
from ..schemas import DocType, ExtractionResult
from ..validators import validate_document
from .classify import classify
from .ocr import analyze
from .preprocess import load_pages


def process(path: str | Path, extractor: Extractor | None = None) -> ExtractionResult:
    extractor = extractor or RulesExtractor()
    t0 = time.perf_counter()
    doc, pages = load_pages(str(path))
    layout = analyze(doc, pages)
    doc_type, conf = classify(layout.text)
    fields = normalize(doc_type, extractor.extract(layout, doc_type)) if doc_type != DocType.OTHER else {}
    result = ExtractionResult(doc_id=Path(path).stem, doc_type=doc_type, type_confidence=conf,
                              model=extractor.name, fields=fields,
                              latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                              tokens=dict(getattr(extractor, "last_usage", {}) or {}))
    result.flags = validate_document(doc_type, fields)
    return result


def route(result: ExtractionResult, thresholds: dict | None = None) -> dict[str, list[str]]:
    """Split fields into auto-accepted vs human review (F8) using calibrated scores
    and the thresholds fitted by scripts/calibrate.py."""
    thresholds = thresholds or load_thresholds()
    t = threshold_for(thresholds, result.doc_type.value)
    flagged = bool(result.flags) or result.doc_type == DocType.OTHER
    out: dict[str, list[str]] = {"auto": [], "review": []}
    for name, f in result.fields.items():
        out["auto" if field_score(f.model_dump(), flagged) >= t else "review"].append(name)
    return out

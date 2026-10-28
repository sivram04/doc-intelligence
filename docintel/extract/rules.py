"""Layout + fuzzy label matching extractor. Free, offline, CPU-only baseline
that every model in the race (F7) has to beat."""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from ..pipeline.ocr import Layout
from ..schemas import BBox, DocType, ExtractedField, field_types
from ..synth.generator import LABELS
from .base import coerce


def _norm(s: str) -> str:
    return s.lower().rstrip(":").strip()


class RulesExtractor:
    name = "rules-v1"

    def __init__(self, min_sim: float = 0.6):
        self.min_sim = min_sim

    def extract(self, layout: Layout, doc_type: DocType) -> dict[str, ExtractedField[Any]]:
        types = field_types(doc_type)
        labels = LABELS.get(doc_type.value, {})
        out: dict[str, ExtractedField[Any]] = {}
        taken: set[int] = set()
        for name, typ in types.items():
            variants = [_norm(v) for v in labels.get(name, [name.replace("_", " ")])]
            best, best_sim = None, 0.0
            for i, (k, v) in enumerate(layout.kv):
                if i in taken:
                    continue
                sim = max(SequenceMatcher(None, _norm(k.text), lv).ratio() for lv in variants)
                if sim > best_sim:
                    best, best_sim = (i, v), sim
            if best is None or best_sim < self.min_sim:
                out[name] = ExtractedField[Any]()
                continue
            i, v = best
            value = coerce(v.text, typ)
            taken.add(i)
            conf = best_sim * (1.0 if value is not None else 0.3)
            out[name] = ExtractedField[Any](value=value, confidence=round(conf, 3),
                                            bbox=BBox(x0=v.bbox[0], y0=v.bbox[1], x1=v.bbox[2], y1=v.bbox[3], page=v.page))
        return out

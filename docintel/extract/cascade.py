"""Model routing: cheap local model first, escalate only documents it cannot be trusted on.

Spec: `cascade:<primary>|<fallback>`, e.g.
`cascade:ollama:docintel-qwen1.5b-ft-v2|gemini:gemini-3.5-flash-lite`.
A document escalates when the primary fails outright, misses more than a fifth of
its fields, or fails validation. If the fallback fails too, the primary's answer
stands and confidence routing sends it to a human.
"""
from __future__ import annotations

import logging
from typing import Any

from ..pipeline.ocr import Layout
from ..schemas import DocType, ExtractedField
from ..validators import validate_document
from .base import Extractor

log = logging.getLogger("docintel.cascade")
MAX_MISSING = 0.2


def escalation_reason(doc_type: DocType, fields: dict[str, ExtractedField[Any]]) -> str | None:
    if not fields:
        return "empty"
    # discount and shipping are legitimately absent on many invoices
    optional = {"discount", "shipping"}
    required = {k: f for k, f in fields.items() if k not in optional}
    missing = sum(f.value is None for f in required.values()) / max(len(required), 1)
    if missing > MAX_MISSING:
        return f"missing {missing:.0%} of required fields"
    flags = validate_document(doc_type, fields)
    return f"validation: {flags[0]}" if flags else None


class CascadeExtractor:
    def __init__(self, primary: Extractor, fallback: Extractor):
        self.primary, self.fallback = primary, fallback
        self.name = f"cascade:{primary.name}|{fallback.name}"
        self.last_usage: dict[str, int] = {}
        self.last_route = "primary"

    def extract(self, layout: Layout, doc_type: DocType) -> dict[str, ExtractedField[Any]]:
        fields: dict[str, ExtractedField[Any]] = {}
        try:
            fields = self.primary.extract(layout, doc_type)
            reason = escalation_reason(doc_type, fields)
        except Exception as e:  # noqa: BLE001 - any primary failure is a reason to escalate
            reason = f"primary error: {str(e)[:120]}"
        self.last_route = "primary"
        self.last_usage = dict(getattr(self.primary, "last_usage", {}) or {})
        if reason is None:
            return fields
        try:
            out = self.fallback.extract(layout, doc_type)
        except Exception:
            log.warning("fallback failed", exc_info=True)
            if not fields:
                raise
            return fields
        self.last_route = "fallback"
        self.last_usage = dict(getattr(self.fallback, "last_usage", {}) or {})
        log.info("escalated", extra={"ctx": {"reason": reason}})
        return out

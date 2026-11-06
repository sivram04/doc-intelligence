from __future__ import annotations

import re
from typing import Any, Protocol

from ..pipeline.ocr import Layout
from ..schemas import DocType, ExtractedField

MONEY_RE = re.compile(r"-?\$?\s*-?\d[\d,]*(?:\.\d+)?|\.\d+")
# European style: 1.234,56 / 12 345,00 / 12,50 (comma decimal, 1-2 digits after it)
EU_RE = re.compile(r"\d{1,3}(?:[.\s\u00a0\u202f]\d{3})*,\d{1,2}(?![\d,.])")


def coerce(raw: Any, typ: type, columns: dict[str, type] | None = None) -> Any:
    """Normalise a raw value into the schema's value type; None if it won't parse.
    Table fields (typ is list) coerce every cell by its column type and drop empty rows."""
    if isinstance(raw, dict) and "value" in raw:  # some models wrap table cells like scalar fields
        raw = raw["value"]
    if raw is None:
        return None
    if typ is list:
        if not isinstance(raw, list):
            return None
        rows = []
        for r in raw:
            if isinstance(r, dict):
                row = {k: coerce(r.get(k), t) for k, t in (columns or {}).items()}
                if any(v is not None for v in row.values()):
                    rows.append(row)
        return rows
    if typ in (float, int):
        if re.fullmatch(r"\s*\D{0,4}\d{1,3}(\.\d{3})+\s*", str(raw)):  # 60.000 is sixty thousand (dot grouping)
            raw = str(raw).replace(".", "")
        elif eu := EU_RE.search(str(raw)):
            digits = re.sub(r"[.\s\u00a0\u202f]", "", eu.group()).replace(",", ".")
            raw = str(raw)[:eu.start()] + digits + str(raw)[eu.end():]
        m = MONEY_RE.search(str(raw))
        if not m:
            return None
        neg = str(raw).strip().startswith("(") and str(raw).strip().endswith(")")  # accounting negatives
        cleaned = re.sub(r"[^\d.\-]", "", m.group())
        if not re.search(r"\d", cleaned):  # a lone "," or "-" is not a number
            return None
        num = float(cleaned) * (-1 if neg else 1)
        return int(num) if typ is int else round(num, 2)
    return str(raw).strip()


class Extractor(Protocol):
    name: str

    def extract(self, layout: Layout, doc_type: DocType) -> dict[str, ExtractedField[Any]]: ...

from __future__ import annotations

import re
from typing import Any, Protocol

from ..pipeline.ocr import Layout
from ..schemas import DocType, ExtractedField

MONEY_RE = re.compile(r"-?\$?\s*-?[\d,]+(?:\.\d+)?")


def coerce(raw: str | None, typ: type) -> Any:
    """Normalise a raw string into the schema's value type; None if it won't parse."""
    if raw is None:
        return None
    if typ in (float, int):
        m = MONEY_RE.search(str(raw))
        if not m:
            return None
        num = float(re.sub(r"[^\d.\-]", "", m.group()))
        return int(num) if typ is int else round(num, 2)
    return str(raw).strip()


class Extractor(Protocol):
    name: str

    def extract(self, layout: Layout, doc_type: DocType) -> dict[str, ExtractedField[Any]]: ...

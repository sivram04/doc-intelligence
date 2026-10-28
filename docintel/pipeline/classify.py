"""Document-type classification (F4). Keyword baseline with a softmax-style
confidence; a fine-tuned LayoutLMv3 classifier replaces it in Phase 3."""
from __future__ import annotations

import math
import re

from ..schemas import DocType

KEYWORDS = {
    DocType.W2: ["w-2", "wage and tax", "employer identification", "medicare wages", "social security wages"],
    DocType.PAYSTUB: ["earnings statement", "pay stub", "payroll", "net pay", "ytd", "year-to-date", "pay date"],
    DocType.BANK_STATEMENT: ["statement period", "opening balance", "beginning balance", "deposits", "withdrawals", "account holder"],
    DocType.INVOICE: ["invoice", "bill to", "subtotal", "amount due", "total due"],
}


def classify(text: str) -> tuple[DocType, float]:
    t = text.lower()
    scores = {dt: sum(len(re.findall(re.escape(k), t)) for k in kws) for dt, kws in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return DocType.OTHER, 0.5
    z = sum(math.exp(s) for s in scores.values())
    return best, round(math.exp(scores[best]) / z, 3)

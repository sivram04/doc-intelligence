"""Document-type classification (F4).

A TF-IDF (word + char n-gram) logistic-regression model trained on the train
split's OCR text (scripts/train_classifier.py). Falls back to a keyword
baseline when no trained model is present, so the pipeline always runs.
"""
from __future__ import annotations

import math
import os
import re
from functools import lru_cache
from pathlib import Path

from ..schemas import DocType

MODEL_PATH = Path(os.getenv("DOCTYPE_MODEL", "models/doctype.joblib"))
MIN_CONFIDENCE = 0.4  # below this the doc is routed as OTHER

KEYWORDS = {
    DocType.W2: ["w-2", "wage and tax", "employer identification", "medicare wages", "social security wages"],
    DocType.PAYSTUB: ["earnings statement", "pay stub", "payroll", "net pay", "ytd", "year-to-date", "pay date"],
    DocType.BANK_STATEMENT: ["statement period", "opening balance", "beginning balance", "deposits", "withdrawals",
                             "account holder"],
    DocType.INVOICE: ["invoice", "bill to", "subtotal", "amount due", "total due"],
}


def classify_keywords(text: str) -> tuple[DocType, float]:
    t = text.lower()
    scores = {dt: sum(len(re.findall(re.escape(k), t)) for k in kws) for dt, kws in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return DocType.OTHER, 0.5
    z = sum(math.exp(s) for s in scores.values())
    return best, round(math.exp(scores[best]) / z, 3)


@lru_cache(maxsize=1)
def _model():
    if not MODEL_PATH.exists():
        return None
    import joblib
    return joblib.load(MODEL_PATH)


def classify(text: str) -> tuple[DocType, float]:
    model = _model()
    if model is None:
        return classify_keywords(text)
    proba = model.predict_proba([text])[0]
    i = int(proba.argmax())
    if proba[i] < MIN_CONFIDENCE:
        return DocType.OTHER, round(float(proba[i]), 3)
    return DocType(model.classes_[i]), round(float(proba[i]), 3)

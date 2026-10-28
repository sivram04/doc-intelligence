"""Field-level precision / recall / F1 against ground truth (N1)."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def match(pred: Any, gold: Any) -> bool:
    if pred is None or gold is None:
        return pred is gold
    if isinstance(gold, (int, float)) and isinstance(pred, (int, float)):
        return abs(float(pred) - float(gold)) <= 0.01
    return " ".join(str(pred).lower().split()) == " ".join(str(gold).lower().split())


class FieldScorer:
    def __init__(self):
        self.tp: dict[str, int] = defaultdict(int)
        self.fp: dict[str, int] = defaultdict(int)
        self.fn: dict[str, int] = defaultdict(int)

    def add(self, doc_type: str, pred: dict[str, Any], gold: dict[str, Any]) -> None:
        for name, g in gold.items():
            p = pred.get(name)
            if p is not None and match(p, g):
                self.tp[doc_type] += 1
            else:
                if p is not None:
                    self.fp[doc_type] += 1
                self.fn[doc_type] += 1

    def report(self) -> dict[str, dict[str, float]]:
        out = {}
        for dt in sorted(set(self.tp) | set(self.fp) | set(self.fn)):
            tp, fp, fn = self.tp[dt], self.fp[dt], self.fn[dt]
            p = tp / (tp + fp) if tp + fp else 0.0
            r = tp / (tp + fn) if tp + fn else 0.0
            out[dt] = {"precision": round(p, 4), "recall": round(r, 4),
                       "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0, "fields": tp + fn}
        return out

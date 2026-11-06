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
        self.tables: dict[str, list[int]] = {}

    def add(self, doc_type: str, pred: dict[str, Any], gold: dict[str, Any]) -> None:
        for name, g in gold.items():
            p = pred.get(name)
            if isinstance(g, list):  # table field: score rows, kept apart from scalar-field F1
                self._add_rows(f"{doc_type}.{name}", p if isinstance(p, list) else [], g)
                continue
            if g is None:  # field genuinely absent on this document: null is right, a value is a false positive
                if p is not None:
                    self.fp[doc_type] += 1
                continue
            if p is not None and match(p, g):
                self.tp[doc_type] += 1
            else:
                if p is not None:
                    self.fp[doc_type] += 1
                self.fn[doc_type] += 1

    def _add_rows(self, key: str, pred: list[dict], gold: list[dict]) -> None:
        """A predicted row is a hit when every gold column matches an unused gold row."""
        unused = list(range(len(gold)))
        hits = 0
        for r in pred:
            for j in unused:
                # columns the document doesn't print (gold None) aren't scored: a derived value is not an error
                if all(match(r.get(k), v) for k, v in gold[j].items() if v is not None):
                    unused.remove(j)
                    hits += 1
                    break
        t = self.tables.setdefault(key, [0, 0, 0])
        t[0], t[1], t[2] = t[0] + hits, t[1] + len(pred) - hits, t[2] + len(gold) - hits

    def table_report(self) -> dict[str, dict[str, float]]:
        out = {}
        for key, (tp, fp, fn) in sorted(self.tables.items()):
            p = tp / (tp + fp) if tp + fp else 0.0
            r = tp / (tp + fn) if tp + fn else 0.0
            out[key] = {"row_precision": round(p, 4), "row_recall": round(r, 4),
                        "row_f1": round(2 * p * r / (p + r), 4) if p + r else 0.0, "rows": tp + fn}
        return out

    def report(self) -> dict[str, dict[str, float]]:
        out = {}
        for dt in sorted(set(self.tp) | set(self.fp) | set(self.fn)):
            tp, fp, fn = self.tp[dt], self.fp[dt], self.fn[dt]
            p = tp / (tp + fp) if tp + fp else 0.0
            r = tp / (tp + fn) if tp + fn else 0.0
            out[dt] = {"precision": round(p, 4), "recall": round(r, 4),
                       "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0, "fields": tp + fn}
        return out

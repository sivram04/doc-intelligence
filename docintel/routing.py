"""Confidence routing (F8): calibrated per-field scores and thresholds.

A model's self-reported confidence is not enough on its own, so each field
gets a composite score:
  model confidence
  x evidence   (the value is actually present in the OCR span it cites)
  x validation (the document passed its field-level rules)
Thresholds are fitted on the val split to hit a target precision among
auto-accepted fields: per document type when val has enough fields of that
type, otherwise one global threshold. Fields below threshold go to review.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

THRESHOLDS_PATH = Path(os.getenv("THRESHOLDS", "models/thresholds.json"))
NO_EVIDENCE = 0.6  # multiplier when the value can't be found in its cited span
FLAGGED = 0.5      # multiplier when the document failed a validation rule: decisive, not a nudge
MIN_FIELDS_PER_TYPE = 40
SUSPICIOUS = re.compile(r"[~|_^`{}\[\]<>\\]")
DEFAULT_THRESHOLD = 0.85
# Policy floor: a fitted threshold may tighten routing but never loosen it below this. With a
# small val split, fitting alone can learn to accept fields from documents that failed validation.
MIN_THRESHOLD = 0.85


def _alnum(x: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(x).lower())


def has_evidence(value: Any, source_text: str | None) -> bool:
    """Is the extracted value literally present in the OCR text it was taken from?"""
    if value is None or not source_text:
        return False
    if isinstance(value, (int, float)):
        digits = re.sub(r"\D", "", source_text)
        forms = {re.sub(r"\D", "", f"{float(value):.2f}")}
        if float(value).is_integer():
            forms.add(str(int(value)))  # e.g. tax year 2024, printed without cents
        return any(f in digits for f in forms)
    if SUSPICIOUS.search(str(value)):
        return False  # OCR noise characters never occur in names or IDs
    v = _alnum(value)
    return bool(v) and v in _alnum(source_text)


def field_score(field: dict, doc_flagged: bool) -> float:
    if field.get("value") is None:
        return 0.0
    score = float(field.get("confidence") or 0.0)
    if not has_evidence(field["value"], field.get("source_text")):
        score *= NO_EVIDENCE
    if doc_flagged:
        score *= FLAGGED
    return round(score, 4)


def load_thresholds(path: Path = THRESHOLDS_PATH) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"global": DEFAULT_THRESHOLD, "by_type": {}}


def threshold_for(thresholds: dict, doc_type: str) -> float:
    return thresholds.get("by_type", {}).get(doc_type, thresholds.get("global", DEFAULT_THRESHOLD))


def fit_threshold(points: list[tuple[float, bool]], target_precision: float) -> float | None:
    """Lowest threshold whose auto-accepted set (score >= t) meets the target precision.
    points: (score, is_correct). Returns None if no threshold reaches the target."""
    best = None
    for t in sorted({s for s, _ in points if s > 0}, reverse=True):
        accepted = [ok for s, ok in points if s >= t]
        if sum(accepted) / len(accepted) >= target_precision:
            best = t
        else:
            break  # precision only falls as t drops further
    return best


def fit(points_by_type: dict[str, list[tuple[float, bool]]], target_precision: float) -> dict:
    all_points = [p for pts in points_by_type.values() for p in pts]
    global_t = fit_threshold(all_points, target_precision)
    global_t = max(global_t, MIN_THRESHOLD) if global_t is not None else None
    by_type = {}
    for dt, pts in points_by_type.items():
        if len(pts) >= MIN_FIELDS_PER_TYPE:
            t = fit_threshold(pts, target_precision)
            if t is not None:
                by_type[dt] = max(t, MIN_THRESHOLD)
    return {"target_precision": target_precision, "global": global_t if global_t is not None else 1.01,
            "by_type": by_type, "min_threshold": MIN_THRESHOLD, "no_evidence_factor": NO_EVIDENCE,
            "flagged_factor": FLAGGED}

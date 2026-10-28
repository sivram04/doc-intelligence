"""Phase 5: fit confidence-routing thresholds on val, measure them on test.

    python scripts/calibrate.py --model ollama_docintel-qwen1.5b-ft --target 0.99

Reports, at the chosen thresholds: review rate, precision of auto-accepted
fields, residual error rate (wrong values that skipped review, over all
fields), and a reliability table (score bin -> observed accuracy).
"""
import argparse
import json
from collections import defaultdict
from itertools import pairwise
from pathlib import Path

from docintel.eval.metrics import match
from docintel.routing import field_score, fit, threshold_for
from docintel.schemas import DocType
from docintel.validators import validate_document

BINS = [0.0, 0.3, 0.5, 0.7, 0.85, 0.95, 1.01]


def points(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        if r["error"]:
            out += [{"doc_type": r["doc_type"], "field": k, "score": 0.0, "ok": False} for k in r["gold"]]
            continue
        flagged = bool(validate_document(DocType(r["pred_type"]), r["fields"])) if r["pred_type"] != "other" else True
        for name, gold in r["gold"].items():
            f = r["fields"].get(name, {})
            out.append({"doc_type": r["doc_type"], "field": name, "score": field_score(f, flagged),
                        "ok": f.get("value") is not None and match(f.get("value"), gold)})
    return out


def measure(pts: list[dict], th: dict) -> dict:
    auto = [p for p in pts if p["score"] >= threshold_for(th, p["doc_type"])]
    wrong_auto = sum(not p["ok"] for p in auto)
    reliability = []
    for lo, hi in pairwise(BINS):
        b = [p for p in pts if lo <= p["score"] < hi]
        if b:
            reliability.append({"bin": f"{lo:.2f}-{min(hi, 1):.2f}", "fields": len(b),
                                "accuracy": round(sum(p["ok"] for p in b) / len(b), 4)})
    return {"fields": len(pts), "auto_accepted": len(auto),
            "review_rate": round(1 - len(auto) / len(pts), 4),
            "auto_precision": round(1 - wrong_auto / len(auto), 4) if auto else None,
            "residual_error_rate": round(wrong_auto / len(pts), 4),
            "overall_accuracy": round(sum(p["ok"] for p in pts) / len(pts), 4),
            "reliability": reliability}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="ollama_docintel-qwen1.5b-ft", help="name used in reports/preds_<model>_*.jsonl")
    ap.add_argument("--target", type=float, default=0.99, help="target precision of auto-accepted fields")
    ap.add_argument("--reports", default="reports")
    ap.add_argument("--out", default="models/thresholds.json")
    a = ap.parse_args()

    val = points(Path(a.reports) / f"preds_{a.model}_val.jsonl")
    test = points(Path(a.reports) / f"preds_{a.model}_test.jsonl")
    by_type: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    for p in val:
        by_type[p["doc_type"]].append((p["score"], p["ok"]))
    th = fit(by_type, a.target) | {"model": a.model, "fitted_on": "val", "val_fields": len(val)}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(th, indent=2))

    baseline = {"global": 0.85, "by_type": {}}  # the pre-calibration rule: raw confidence >= 0.85
    raw = [p | {"score": s} for p, s in zip(test, raw_scores(Path(a.reports) / f"preds_{a.model}_test.jsonl"))]
    report = {"thresholds": th, "test": measure(test, th), "test_uncalibrated_raw_conf_0.85": measure(raw, baseline)}
    (Path(a.reports) / f"routing_{a.model}.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


def raw_scores(path: Path) -> list[float]:
    """Raw model confidence in the same order as points(), for the before/after comparison."""
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        r = json.loads(line)
        for name in r["gold"]:
            f = r["fields"].get(name, {}) if not r["error"] else {}
            out.append(float(f.get("confidence") or 0.0) if f.get("value") is not None else 0.0)
    return out


if __name__ == "__main__":
    main()

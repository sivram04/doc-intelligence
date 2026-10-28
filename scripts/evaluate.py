"""Phase 1/2 eval on the frozen test split: field F1 per doc type and per
modality (native PDF vs scan), classification accuracy, review rate,
latency, throughput, and cross-document detection rate.

    python scripts/evaluate.py                           # rules baseline
    python scripts/evaluate.py --model gemini-2.5-flash  # via Project 1 gateway
"""
import argparse
import json
import os
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from docintel.eval.metrics import FieldScorer
from docintel.extract.llm import LLMExtractor
from docintel.extract.rules import RulesExtractor
from docintel.pipeline.run import process, route
from docintel.schemas import DocType
from docintel.validators import validate_case


def run_one(args: tuple[str, str, str | None]) -> dict:
    data, doc_id, model = args
    gt = json.loads((Path(data) / "docs" / f"{doc_id}.json").read_text())
    res = process(Path(data) / "docs" / gt["file"], LLMExtractor(model) if model else RulesExtractor())
    return {"gt": gt, "doc_type": res.doc_type.value, "latency_ms": res.latency_ms, "model": res.model,
            "pred": {k: f.value for k, f in res.fields.items()},
            "fields": {k: f.model_dump() for k, f in res.fields.items()}, "routing": route(res)}


def pctl(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/synthetic")
    ap.add_argument("--split", default="test")
    ap.add_argument("--model", default=None, help="gateway model name; omit for rules baseline")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    ap.add_argument("--out", default="reports")
    a = ap.parse_args()

    # One Tesseract thread per worker process; otherwise N workers x M OpenMP threads thrash the CPU.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    ids = json.loads((Path(a.data) / "splits.json").read_text())["splits"][a.split]
    t0 = time.perf_counter()
    with ProcessPoolExecutor(a.workers) as pool:
        rows = list(pool.map(run_one, [(a.data, i, a.model) for i in ids], chunksize=2))
    elapsed = time.perf_counter() - t0

    by_type, by_modality = FieldScorer(), FieldScorer()
    latency: dict[str, list[float]] = defaultdict(list)
    type_ok = review = total = 0
    case_docs: dict[str, dict] = defaultdict(dict)
    for r in rows:
        gt = r["gt"]
        gold = {k: f["value"] for k, f in gt["fields"].items()}
        by_type.add(gt["doc_type"], r["pred"], gold)
        by_modality.add(gt["modality"], r["pred"], gold)
        latency[gt["modality"]].append(r["latency_ms"])
        type_ok += r["doc_type"] == gt["doc_type"]
        review += len(r["routing"]["review"])
        total += len(r["routing"]["review"]) + len(r["routing"]["auto"])
        if gt["case_id"]:
            case_docs[gt["case_id"]][DocType(r["doc_type"])] = r["fields"]

    cases = json.loads((Path(a.data) / "cases.json").read_text())
    tp = fp = pos = neg = 0
    for cid, docs in case_docs.items():
        flagged = bool(validate_case(docs))
        bad = cases[cid]["inconsistent"] is not None
        pos, neg = pos + bad, neg + (not bad)
        tp, fp = tp + (flagged and bad), fp + (flagged and not bad)

    all_lat = [x for v in latency.values() for x in v]
    report = {
        "model": rows[0]["model"] if rows else None, "split": a.split, "documents": len(ids),
        "field_scores_by_type": by_type.report(),
        "field_scores_by_modality": by_modality.report(),
        "classification_accuracy": round(type_ok / len(ids), 4),
        "review_rate": round(review / max(total, 1), 4),
        "latency_ms": {m: {"p50": pctl(v, 0.5), "p95": pctl(v, 0.95)} for m, v in sorted(latency.items())},
        "p95_latency_ms": pctl(all_lat, 0.95),
        "workers": a.workers,
        "docs_per_minute": round(len(ids) / elapsed * 60, 1),
        "cross_doc": {"seeded": pos, "detected": tp, "detection_rate": round(tp / pos, 4) if pos else None,
                      "false_positive_rate": round(fp / neg, 4) if neg else None},
    }
    Path(a.out).mkdir(exist_ok=True)
    name = (report["model"] or "none").replace(":", "_")
    (Path(a.out) / f"eval_{name}_{a.split}.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

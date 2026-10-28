"""Phase 1/2 eval on the frozen test split: field F1 per doc type, classification
accuracy, review rate, throughput, and cross-document detection rate.

    python scripts/evaluate.py                       # rules baseline
    python scripts/evaluate.py --model gemini-2.5-flash  # via Project 1 gateway
"""
import argparse
import json
import time
from collections import defaultdict
from pathlib import Path

from docintel.eval.metrics import FieldScorer
from docintel.extract.llm import LLMExtractor
from docintel.extract.rules import RulesExtractor
from docintel.pipeline.run import process, route
from docintel.schemas import DocType
from docintel.validators import validate_case

ap = argparse.ArgumentParser()
ap.add_argument("--data", default="data/synthetic")
ap.add_argument("--split", default="test")
ap.add_argument("--model", default=None, help="gateway model name; omit for rules baseline")
ap.add_argument("--out", default="reports")
a = ap.parse_args()

data = Path(a.data)
ids = json.loads((data / "splits.json").read_text())["splits"][a.split]
extractor = LLMExtractor(a.model) if a.model else RulesExtractor()
scorer, type_ok, review, total_fields, latencies = FieldScorer(), 0, 0, 0, []
case_docs: dict[str, dict] = defaultdict(dict)

t0 = time.perf_counter()
for doc_id in ids:
    gt = json.loads((data / "docs" / f"{doc_id}.json").read_text())
    res = process(data / "docs" / f"{doc_id}.pdf", extractor)
    latencies.append(res.latency_ms)
    type_ok += res.doc_type.value == gt["doc_type"]
    scorer.add(gt["doc_type"], {k: f.value for k, f in res.fields.items()},
               {k: f["value"] for k, f in gt["fields"].items()})
    r = route(res)
    review += len(r["review"])
    total_fields += len(r["review"]) + len(r["auto"])
    if gt["case_id"]:
        case_docs[gt["case_id"]][res.doc_type] = {k: f.model_dump() for k, f in res.fields.items()}
elapsed = time.perf_counter() - t0

cases = json.loads((data / "cases.json").read_text())
tp = fp = pos = neg = 0
for cid, docs in case_docs.items():
    flagged = bool(validate_case({DocType(k): v for k, v in docs.items()}))
    bad = cases[cid]["inconsistent"] is not None
    pos += bad
    neg += not bad
    tp += flagged and bad
    fp += flagged and not bad

latencies.sort()
report = {
    "model": extractor.name, "split": a.split, "documents": len(ids),
    "field_scores": scorer.report(),
    "classification_accuracy": round(type_ok / len(ids), 4),
    "review_rate": round(review / max(total_fields, 1), 4),
    "p95_latency_ms": latencies[int(0.95 * (len(latencies) - 1))],
    "docs_per_minute": round(len(ids) / elapsed * 60, 1),
    "cross_doc": {"seeded": pos, "detected": tp, "detection_rate": round(tp / pos, 4) if pos else None,
                  "false_positive_rate": round(fp / neg, 4) if neg else None},
}
Path(a.out).mkdir(exist_ok=True)
(Path(a.out) / f"eval_{extractor.name.replace(':', '_')}_{a.split}.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))

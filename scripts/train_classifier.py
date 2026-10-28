"""Train the document-type classifier on the train split's OCR text and report
val accuracy. The OCR text is cached so later stages can reuse it.

    python scripts/train_classifier.py
"""
import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report
from sklearn.pipeline import FeatureUnion, make_pipeline

from docintel.pipeline.ocr import analyze
from docintel.pipeline.preprocess import load_pages


def ocr_text(args: tuple[str, str]) -> tuple[str, str, str]:
    data, doc_id = args
    gt = json.loads((Path(data) / "docs" / f"{doc_id}.json").read_text())
    doc, pages = load_pages(str(Path(data) / "docs" / gt["file"]))
    return doc_id, gt["doc_type"], analyze(doc, pages).text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="+", default=["data/synthetic", "data/synthetic_v2"])
    ap.add_argument("--out", default="models/doctype.joblib")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()

    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    jobs = {"train": [], "val": []}
    for d in a.data:
        splits = json.loads((Path(d) / "splits.json").read_text())["splits"]
        for s in jobs:
            jobs[s] += [(d, i) for i in splits[s]]
    with ProcessPoolExecutor() as pool:
        rows = {s: list(pool.map(ocr_text, jobs[s], chunksize=2)) for s in jobs}

    features = FeatureUnion([
        ("word", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
        ("char", TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=2, sublinear_tf=True)),
    ])
    model = make_pipeline(features, LogisticRegression(C=4.0, max_iter=2000, random_state=a.seed))
    model.fit([t for _, _, t in rows["train"]], [y for _, y, _ in rows["train"]])

    y_val = [y for _, y, _ in rows["val"]]
    pred = model.predict([t for _, _, t in rows["val"]])
    print(classification_report(y_val, pred, digits=3, zero_division=0))
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, a.out)
    metrics = {"val_accuracy": round(accuracy_score(y_val, pred), 4),
               "train_docs": len(rows["train"]), "val_docs": len(rows["val"])}
    Path("reports").mkdir(exist_ok=True)
    Path("reports/classifier.json").write_text(json.dumps(metrics, indent=2))
    print(metrics)


if __name__ == "__main__":
    main()

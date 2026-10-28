"""Phase 4/6: log a trained model to MLflow and run the champion/challenger gate (F10, N4).

The challenger is promoted only if its test field F1 beats the current
champion by at least --margin. Every run is logged with its training config,
training-data hash and eval metrics, whether or not it is promoted.

    python scripts/register_model.py --name docintel-qwen1.5b-ft --extractor ollama:docintel-qwen1.5b-ft \
        --train-metrics models/finetuned/metrics.json
"""
import argparse
import json
import os
from pathlib import Path

import mlflow

CHAMPION = Path("reports/champion.json")


def overall_f1(report: dict) -> float:
    s = report["field_scores_by_type"]
    n = sum(v["fields"] for v in s.values())
    return round(sum(v["f1"] * v["fields"] for v in s.values()) / n, 4) if n else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True, help="registered model name / run name")
    ap.add_argument("--extractor", required=True, help="extractor spec the eval report was produced with")
    ap.add_argument("--train-metrics", required=True)
    ap.add_argument("--reports", default="reports")
    ap.add_argument("--margin", type=float, default=0.005)
    a = ap.parse_args()

    report = json.loads((Path(a.reports) / f"eval_{a.extractor.replace(':', '_')}_test.json").read_text())
    train = json.loads(Path(a.train_metrics).read_text())
    f1 = overall_f1(report)
    champ = json.loads(CHAMPION.read_text()) if CHAMPION.exists() else None
    promoted = champ is None or f1 >= champ["test_f1"] + a.margin

    mlflow.set_tracking_uri(os.getenv("MLFLOW_TRACKING_URI", "file:./mlruns"))
    mlflow.set_experiment("docintel-extraction")
    with mlflow.start_run(run_name=a.name) as run:
        mlflow.log_params({**train["config"], "train_data_sha256": train["train_data_sha256"],
                           "train_examples": train["train_examples"], "extractor": a.extractor})
        mlflow.log_metrics({
            "train_loss": train["train_loss"], "val_loss": train["val_loss"], "test_field_f1": f1,
            "test_review_rate": report["review_rate"], "test_p95_latency_ms": report["p95_latency_ms"],
            "cross_doc_detection_rate": report["cross_doc"]["detection_rate"] or 0.0,
            **{f"test_f1_{k}": v["f1"] for k, v in report["field_scores_by_type"].items()},
            **{f"test_f1_{k}": v["f1"] for k, v in report["field_scores_by_modality"].items()},
        })
        for i, h in enumerate(h for h in train.get("history", []) if "loss" in h):
            mlflow.log_metric("train_loss_step", h["loss"], step=i)
        mlflow.log_dict(report, "eval_report.json")
        mlflow.log_dict(train, "train_metrics.json")
        mlflow.set_tags({"promoted": str(promoted), "gpu": train.get("gpu", "")})
        run_id = run.info.run_id

    decision = {"challenger": a.name, "challenger_f1": f1, "champion": champ["name"] if champ else None,
                "champion_f1": champ["test_f1"] if champ else None, "margin": a.margin,
                "promoted": promoted, "mlflow_run_id": run_id}
    if promoted:
        CHAMPION.write_text(json.dumps({"name": a.name, "extractor": a.extractor, "test_f1": f1,
                                        "mlflow_run_id": run_id}, indent=2))
    print(json.dumps(decision, indent=2))


if __name__ == "__main__":
    main()

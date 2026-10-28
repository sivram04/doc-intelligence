"""Build the supervised fine-tuning set (chat JSONL).

Every example is exactly what the extractor sends at inference (same system
prompt, same layout-preserving page text) paired with the gold JSON answer,
so the tuned model learns the production format. Sources:
- the train splits of benchmark v1 and v2, plus
- augmentation documents from the v2 generator, rendered only in the
  *non-held-out* layout families.
Test documents, v1 HELDOUT_LABELS and each type's held-out v2 layout family are
never used, so test F1 still measures generalisation to unseen layouts.

    python scripts/build_sft.py --aug-cases 250 --aug-invoices 300
"""
import argparse
import json
import os
import random
import shutil
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from faker import Faker

from docintel.extract.llm import PROMPT, SYSTEM, describe_tables
from docintel.pipeline.ocr import analyze
from docintel.pipeline.preprocess import load_pages
from docintel.routing import has_evidence
from docintel.schemas import DocType, field_types
from docintel.synth.generator_v2 import make_case_v2, make_invoice_v2, render_doc
from docintel.synth.templates import FAMILIES


def augment(out: Path, n_cases: int, n_invoices: int, scan_rate: float, seed: int) -> list[Path]:
    """Render extra train-only v2 documents (seed differs from the benchmark's; held-out families excluded)."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    rng, fk = random.Random(seed), Faker("en_US")
    fk.seed_instance(seed)
    docs = [d for i in range(n_cases)
            for d in make_case_v2(f"aug{i:04d}", fk, rng, rng.choice([None, None, None, "w2_inflated", "deposits_low"]))]
    docs += [make_invoice_v2(f"auginv{i:04d}", fk, rng) for i in range(n_invoices)]
    for doc_id, doc_type, values in docs:
        family = rng.randrange(FAMILIES[doc_type] - 1)
        render_doc(doc_id, doc_type, values, out, rng, family, rng.random() < scan_rate, None)
    return sorted(out.glob("*.json"))


def example(gt_path: str) -> dict:
    gt = json.loads(Path(gt_path).read_text())
    layout = analyze(*load_pages(str(Path(gt_path).with_name(gt["file"]))))
    dt = DocType(gt["doc_type"])
    types = field_types(dt)
    answer = {}
    for name, typ in types.items():
        v = gt["fields"].get(name, {}).get("value")  # absent on this document -> null is the right answer
        if typ is list:
            answer[name] = {"value": v, "confidence": 1.0 if v else 0.0, "span": None}
            continue
        span = next((i for i, s in enumerate(layout.spans) if has_evidence(v, s.text)), None) if v is not None else None
        # Value not legible in the OCR text: still teach the right answer, but with lower confidence
        # so the tuned model's scores carry signal for confidence routing (F8).
        conf = 0.0 if v is None else 1.0 if span is not None else 0.5
        answer[name] = {"value": v, "confidence": conf, "span": span}
    user = PROMPT.format(doc_type=dt.value, spans=layout.render(),
                         fields=", ".join(n for n, t in types.items() if t is not list), tables=describe_tables(types))
    return {"doc_id": gt["doc_id"], "doc_type": dt.value, "modality": gt["modality"],
            "benchmark": gt.get("benchmark", "v1"),
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user},
                         {"role": "assistant", "content": json.dumps(answer)}]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", nargs="+", default=["data/synthetic", "data/synthetic_v2"])
    ap.add_argument("--out", default="data/sft")
    ap.add_argument("--aug-cases", type=int, default=250)
    ap.add_argument("--aug-invoices", type=int, default=300)
    ap.add_argument("--scan-rate", type=float, default=0.4)
    ap.add_argument("--seed", type=int, default=1234)
    a = ap.parse_args()

    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    out = Path(a.out)
    aug = augment(out / "aug_docs", a.aug_cases, a.aug_invoices, a.scan_rate, a.seed)
    jobs: dict[str, list[str]] = {"train": [str(p) for p in aug], "val": []}
    for d in map(Path, a.data):
        splits = json.loads((d / "splits.json").read_text())["splits"]
        for s in jobs:
            jobs[s] += [str(d / "docs" / f"{i}.json") for i in splits[s]]
    stats = {}
    with ProcessPoolExecutor() as pool:
        for split, paths in jobs.items():
            rows = list(pool.map(example, paths, chunksize=4))
            with (out / f"{split}.jsonl").open("w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            stats[split] = {"examples": len(rows), "scans": sum(r["modality"] == "scan" for r in rows),
                            "v2": sum(r["benchmark"] == "v2" for r in rows),
                            "max_chars": max(sum(len(m["content"]) for m in r["messages"]) for r in rows)}
    (out / "stats.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()

"""Phase 4: build the supervised fine-tuning set (chat JSONL).

Every example is exactly what the extractor sends at inference (same system
prompt, same OCR spans) paired with the gold JSON answer, so the tuned model
learns the production format. Sources:
- the frozen train split, plus
- extra augmentation documents rendered with *train* label wording only.
Test documents and HELDOUT_LABELS are never touched, so test F1 still
measures generalisation to unseen wording.

    python scripts/build_sft.py --aug-cases 300 --aug-invoices 300
"""
import argparse
import json
import os
import random
import shutil
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from faker import Faker

from docintel.extract.llm import PROMPT, SYSTEM
from docintel.pipeline.ocr import analyze
from docintel.pipeline.preprocess import load_pages
from docintel.schemas import DocType, field_types
from docintel.synth.generator import make_case, make_invoice, money, render


def augment(out: Path, n_cases: int, n_invoices: int, scan_rate: float, seed: int) -> list[Path]:
    """Render extra train-only documents (seed differs from the benchmark's)."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    rng, fk = random.Random(seed), Faker()
    fk.seed_instance(seed)
    docs = [d for i in range(n_cases)
            for d in make_case(f"aug{i:04d}", fk, rng, rng.choice([None, None, None, "w2_inflated", "deposits_low"]))]
    docs += [make_invoice(f"auginv{i:04d}", fk, rng) for i in range(n_invoices)]
    for d in docs:
        render(d, out, rng, heldout=False, scan=rng.random() < scan_rate)
    return sorted(out.glob("*.json"))


def find_span(spans: list[str], value) -> int | None:
    text = money(value) if isinstance(value, float) else str(value)
    for i, s in enumerate(spans):
        if text in s:
            return i
    return None


def example(gt_path: str) -> dict:
    gt = json.loads(Path(gt_path).read_text())
    layout = analyze(*load_pages(str(Path(gt_path).with_name(gt["file"]))))
    spans = [s.text for s in layout.spans]
    dt = DocType(gt["doc_type"])
    answer = {}
    for name in field_types(dt):
        v = gt["fields"][name]["value"]
        span = find_span(spans, v)
        # Value not legible in the OCR text: still teach the right answer, but with lower confidence
        # so the tuned model's scores carry signal for confidence routing (F8).
        answer[name] = {"value": v, "confidence": 1.0 if span is not None else 0.5, "span": span}
    user = PROMPT.format(doc_type=dt.value, spans="\n".join(f"[{i}] {t}" for i, t in enumerate(spans)),
                         fields=", ".join(field_types(dt)))
    return {"doc_id": gt["doc_id"], "doc_type": dt.value, "modality": gt["modality"],
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user},
                         {"role": "assistant", "content": json.dumps(answer)}]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/synthetic")
    ap.add_argument("--out", default="data/sft")
    ap.add_argument("--aug-cases", type=int, default=300)
    ap.add_argument("--aug-invoices", type=int, default=300)
    ap.add_argument("--scan-rate", type=float, default=0.4)
    ap.add_argument("--seed", type=int, default=1234)
    a = ap.parse_args()

    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    data, out = Path(a.data), Path(a.out)
    splits = json.loads((data / "splits.json").read_text())["splits"]
    aug = augment(out / "aug_docs", a.aug_cases, a.aug_invoices, a.scan_rate, a.seed)
    jobs = {
        "train": [str(data / "docs" / f"{i}.json") for i in splits["train"]] + [str(p) for p in aug],
        "val": [str(data / "docs" / f"{i}.json") for i in splits["val"]],
    }
    stats = {}
    with ProcessPoolExecutor() as pool:
        for split, paths in jobs.items():
            rows = list(pool.map(example, paths, chunksize=4))
            with (out / f"{split}.jsonl").open("w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")
            stats[split] = {"examples": len(rows),
                            "scans": sum(r["modality"] == "scan" for r in rows)}
    (out / "stats.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()

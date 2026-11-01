"""Append real receipts (CORD-v2 train + validation splits) to the fine-tuning set.

Only those splits are used; the test split stays unseen for scripts/eval_cord.py.
Targets keep amounts exactly as printed, so the model learns real number formats.

    python scripts/build_sft_real.py --limit 800
"""
import argparse
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from datasets import load_dataset

from docintel.extract.llm import PROMPT, SYSTEM, describe_tables
from docintel.pipeline.ocr import analyze
from docintel.pipeline.preprocess import load_pages
from docintel.schemas import DocType, field_types


def as_list(x):
    return x if isinstance(x, list) else [x] if x else []


def example(args: tuple) -> dict:
    img, gt, tag = args
    layout = analyze(*load_pages(img), engine="rapidocr")
    p = gt.get("gt_parse", {})
    sub = p.get("sub_total") if isinstance(p.get("sub_total"), dict) else {}
    tot = p.get("total") if isinstance(p.get("total"), dict) else {}
    gold = {"total": tot.get("total_price"), "subtotal": sub.get("subtotal_price"), "tax": sub.get("tax_price"),
            "discount": sub.get("discount_price")}
    types = field_types(DocType.INVOICE)
    answer = {}
    for name, typ in types.items():
        v = gold.get(name)
        if isinstance(v, list):
            v = v[0] if v else None
        if typ is list:
            rows = [{"description": m.get("nm"), "quantity": m.get("cnt"), "unit_price": m.get("unitprice"),
                     "amount": m.get("price")} for m in as_list(p.get("menu")) if isinstance(m, dict)]
            answer[name] = {"value": rows or None, "confidence": 1.0 if rows else 0.0, "span": None}
            continue
        span = next((i for i, s in enumerate(layout.spans) if v and str(v) in s.text), None)
        answer[name] = {"value": v, "confidence": 0.0 if v is None else 1.0 if span is not None else 0.5, "span": span}
    user = PROMPT.format(doc_type="invoice", spans=layout.render(),
                         fields=", ".join(n for n, t in types.items() if t is not list), tables=describe_tables(types))
    return {"doc_id": tag, "doc_type": "invoice", "modality": "photo", "benchmark": "cord",
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user},
                         {"role": "assistant", "content": json.dumps(answer)}]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/sft")
    ap.add_argument("--limit", type=int, default=800)
    a = ap.parse_args()
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    tmp = Path(tempfile.mkdtemp())
    for split, target, limit in (("train", "train.jsonl", a.limit), ("validation", "val.jsonl", 50)):
        ds = load_dataset("naver-clova-ix/cord-v2", split=split)
        jobs = []
        for i, r in enumerate(ds.select(range(min(limit, len(ds))))):
            img = tmp / f"{split}{i}.png"
            r["image"].convert("RGB").save(img, dpi=(200, 200))
            jobs.append((str(img), json.loads(r["ground_truth"]), f"cord-{split}-{i}"))
        with ThreadPoolExecutor(4) as pool:
            rows = list(pool.map(example, jobs))
        with (Path(a.out) / target).open("a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
        print(split, len(rows))


if __name__ == "__main__":
    main()

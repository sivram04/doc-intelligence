"""Add real SROIE receipts (Malaysia) to the fine-tuning set: receipts whose printed total already
includes GST, which the synthetic invoices and CORD receipts never show.

SROIE labels only company, date and total. Subtotal, tax and line items come from a teacher
model (Gemini), and a receipt is kept only when the teacher agrees with the human label and the
numbers add up:
  - teacher total == human total, and
  - subtotal - discount + tax + shipping == total (the invoice validator raises no total flag), and
  - line items, if any, sum to the subtotal or the total.
Company, date and total in the answer are always the human labels. Receipts used by
scripts/eval_sroie.py (seed 3, the first 40 and the first 15) are excluded.

    python scripts/build_sft_sroie.py --n 300 --val 25
"""
import argparse
import json
import random
import sys
from pathlib import Path

from huggingface_hub import hf_hub_download

from docintel.extract.llm import PROMPT, SYSTEM, describe_tables
from docintel.extract.normalize import date_only, normalize
from docintel.extract.registry import make_extractor
from docintel.pipeline.ocr import analyze
from docintel.pipeline.preprocess import load_pages
from docintel.routing import has_evidence
from docintel.schemas import DocType, field_types
from docintel.validators import validate_document

sys.path.insert(0, str(Path(__file__).parent))
from eval_sroie import RAW, REPO, same_total


def held_out(samples: list, seed: int = 3) -> set[str]:
    """Exactly the receipts eval_sroie.py scores (both sample sizes used so far)."""
    return {s["filepath"] for n in (40, 15) for s in random.Random(seed).sample(samples, n)}


def build(s: dict, teacher) -> tuple[dict | None, str]:
    img = Path(hf_hub_download(REPO, s["filepath"], repo_type="dataset", local_dir=RAW))
    layout = analyze(*load_pages(str(img)))
    fields = normalize(DocType.INVOICE, teacher.extract(layout, DocType.INVOICE))
    v = {n: f.value for n, f in fields.items()}
    if not same_total(v.get("total"), s["total"]):
        return None, "teacher total differs from label"
    if any(flag.startswith(("Total ", "Tax ")) for flag in validate_document(DocType.INVOICE, fields)):
        return None, "numbers do not add up"
    items = v.get("line_items") or []
    amts = [r.get("amount") for r in items if isinstance(r, dict) and r.get("amount") is not None]
    if amts and not any(x is not None and abs(sum(amts) - x) <= 0.05 for x in (v.get("subtotal"), v.get("total"))):
        v["line_items"] = None  # keep the receipt, drop rows we cannot trust
    v["vendor_name"] = s["company"]
    v["invoice_date"] = date_only(s["date"])
    v["currency"] = "MYR"  # every SROIE receipt is Malaysian (printed as RM); the teacher sometimes says USD
    types = field_types(DocType.INVOICE)
    answer = {}
    for name, typ in types.items():
        val = v.get(name)
        if typ is list:
            answer[name] = {"value": val or None, "confidence": 1.0 if val else 0.0, "span": None}
            continue
        span = next((i for i, sp in enumerate(layout.spans) if has_evidence(val, sp.text)), None) \
            if val is not None else None
        if isinstance(val, float):
            val = f"{val:,.2f}"
        answer[name] = {"value": val, "confidence": 0.0 if val is None else 1.0 if span is not None else 0.5,
                        "span": span}
    user = PROMPT.format(doc_type="invoice", spans=layout.render(),
                         fields=", ".join(n for n, t in types.items() if t is not list), tables=describe_tables(types))
    return {"doc_id": "sroie-" + Path(s["filepath"]).stem, "doc_type": "invoice", "modality": "scan",
            "benchmark": "sroie", "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user},
                                               {"role": "assistant", "content": json.dumps(answer)}]}, "kept"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=300, help="receipts to try (kept ones are fewer)")
    ap.add_argument("--val", type=int, default=25)
    ap.add_argument("--teacher", default="gemini:gemini-3.5-flash-lite")
    ap.add_argument("--sft", default="data/sft")
    a = ap.parse_args()
    samples = json.loads(Path(hf_hub_download(REPO, "samples.json", repo_type="dataset", local_dir=RAW)).read_text())
    samples = samples["samples"] if isinstance(samples, dict) else samples
    test = held_out(samples)
    pool = [s for s in samples if s["filepath"] not in test]
    random.Random(11).shuffle(pool)
    teacher = make_extractor(a.teacher)
    rows, why = [], {}
    for k, s in enumerate(pool[:a.n], 1):
        try:
            row, reason = build(s, teacher)
        except Exception as e:  # noqa: BLE001 - one bad receipt must not stop the run
            row, reason = None, f"error: {str(e)[:60]}"
        why[reason] = why.get(reason, 0) + 1
        if row:
            rows.append(row)
        print(f"{k}/{a.n} kept={len(rows)} {reason}", flush=True)
    assert not {r["doc_id"].removeprefix("sroie-") for r in rows} & {Path(t).stem for t in test}
    split = {"val": rows[:a.val], "train": rows[a.val:]}
    for name, new in split.items():
        f = Path(a.sft) / f"{name}.jsonl"
        kept = [r for r in map(json.loads, f.read_text(encoding="utf-8").splitlines()) if r.get("benchmark") != "sroie"]
        f.write_text("".join(json.dumps(r) + "\n" for r in kept + new), encoding="utf-8")
        print(f"{name}: {len(kept)} existing + {len(new)} sroie = {len(kept) + len(new)}")
    print(json.dumps(why, indent=1))


if __name__ == "__main__":
    main()

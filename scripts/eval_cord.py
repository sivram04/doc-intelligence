"""Real-document check on CORD-v2 (public, photographed receipts with human labels).

Synthetic benchmarks can flatter a pipeline built alongside its generator, so
this runs the same OCR -> layout -> extractor path on real phone photos of
receipts and scores what maps onto the invoice schema: total, subtotal, tax and
line items (name + price). Amounts are compared on their digits, because CORD
uses Indonesian formatting (60.000 = sixty thousand).

    python scripts/eval_cord.py --extractor gemini:gemini-3.5-flash-lite --limit 50
Dataset: naver-clova-ix/cord-v2 (CC BY 4.0), test split.
"""
import argparse
import json
import re
import tempfile
import time
from pathlib import Path

from docintel.extract.base import coerce
from docintel.extract.normalize import normalize
from docintel.extract.registry import make_extractor
from docintel.pipeline.ocr import analyze
from docintel.pipeline.preprocess import load_pages
from docintel.schemas import DocType
from docintel.validators import validate_document


def digits(x) -> str:
    if x is None:
        return ""
    if isinstance(x, (int, float)):
        x = f"{x:.2f}".rstrip("0").rstrip(".") if float(x) != int(x) else str(int(x))
    return re.sub(r"\D", "", str(x)).lstrip("0")


def gold_of(gt: dict) -> dict:
    p = gt.get("gt_parse", {})
    menu = p.get("menu", [])
    menu = menu if isinstance(menu, list) else [menu]
    sub = p.get("sub_total", {}) if isinstance(p.get("sub_total"), dict) else {}
    tot = p.get("total", {}) if isinstance(p.get("total"), dict) else {}
    return {"total": tot.get("total_price"), "subtotal": sub.get("subtotal_price"), "tax": sub.get("tax_price"),
            "items": [(m.get("nm", ""), m.get("price")) for m in menu if isinstance(m, dict) and m.get("price")]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extractor", default="gemini:gemini-3.5-flash-lite")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--ocr", default="auto", choices=["auto", "tesseract", "rapidocr"])
    ap.add_argument("--out", default="reports/cord")
    a = ap.parse_args()

    from datasets import load_dataset  # optional dependency: only this script needs it

    ds = load_dataset("naver-clova-ix/cord-v2", split="test")
    ex = make_extractor(a.extractor)
    hits = {"total": [0, 0], "subtotal": [0, 0], "tax": [0, 0]}
    safety = [0, 0]  # wrong totals, wrong totals with no flag and no low-confidence field
    rows_tp = rows_pred = rows_gold = 0
    lat, failures, per_doc = [], 0, []
    tmp = Path(tempfile.mkdtemp())
    for k, rec in enumerate(ds.select(range(min(a.limit, len(ds))))):
        gold = gold_of(json.loads(rec["ground_truth"]))
        img = tmp / f"cord{k}.png"
        rec["image"].convert("RGB").save(img, dpi=(200, 200))
        t0 = time.perf_counter()
        try:
            layout = analyze(*load_pages(str(img)), engine=a.ocr)
            fields = normalize(DocType.INVOICE, ex.extract(layout, DocType.INVOICE))  # same as the app
        except Exception as e:  # noqa: BLE001 - count and continue; a failed receipt scores zero
            failures += 1
            per_doc.append({"i": k, "error": str(e)[:200]})
            fields = {}
        lat.append((time.perf_counter() - t0) * 1000)
        pred = {n: (fields[n].value if n in fields else None) for n in ("total", "subtotal", "tax")}
        flags = validate_document(DocType.INVOICE, fields) if fields else ["extraction failed"]
        low = [n for n in ("total", "subtotal", "tax") if n in fields and fields[n].confidence < 0.5]
        caught = bool(flags or low)
        if gold["total"] is not None:
            wrong = pred["total"] is None or coerce(gold["total"], float) != pred["total"]
            safety[0] += wrong
            safety[1] += wrong and not caught
        for n, h in hits.items():
            if gold[n] is not None:
                h[1] += 1
                h[0] += pred[n] is not None and coerce(gold[n], float) == pred[n]
        items = fields.get("line_items").value if fields.get("line_items") else None
        pitems = [(r.get("description") or "", r.get("amount")) for r in (items or [])]
        unused = list(range(len(gold["items"])))
        for _, amt in pitems:
            j = next((j for j in unused if digits(gold["items"][j][1]) == digits(amt)), None)
            if j is not None:
                unused.remove(j)
                rows_tp += 1
        rows_pred += len(pitems)
        rows_gold += len(gold["items"])
        per_doc.append({"i": k, "gold": gold, "pred": pred, "rows_pred": len(pitems), "flags": flags, "low_conf": low})
    p = rows_tp / rows_pred if rows_pred else 0.0
    r = rows_tp / rows_gold if rows_gold else 0.0
    lat.sort()
    report = {"dataset": "naver-clova-ix/cord-v2 test", "extractor": ex.name, "ocr": a.ocr, "receipts": len(lat), "failures": failures,
              "field_accuracy": {n: round(h / t, 4) if t else None for n, (h, t) in hits.items()},
              "field_support": {n: t for n, (_, t) in hits.items()},
              "wrong_totals": safety[0], "wrong_totals_unflagged": safety[1],
              "line_item_amounts": {"precision": round(p, 4), "recall": round(r, 4),
                                    "f1": round(2 * p * r / (p + r), 4) if p + r else 0.0, "gold_rows": rows_gold},
              "p50_latency_ms": round(lat[len(lat) // 2]), "p95_latency_ms": round(lat[int(0.95 * (len(lat) - 1))])}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    name = ex.name.replace(":", "_").replace("/", "_") + f"_{a.ocr}"
    (Path(a.out) / f"cord_{name}.json").write_text(json.dumps(report, indent=2))
    (Path(a.out) / f"cord_{name}_docs.jsonl").write_text("\n".join(json.dumps(d) for d in per_doc))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

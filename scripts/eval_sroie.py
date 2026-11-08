"""Real-document check on SROIE (ICDAR 2019): scanned shop receipts from Malaysia with
human labels for company, date and total. Runs the same pipeline as the app (OCR,
extraction, normalisation, validation, review routing) and reports:

  accuracy           vendor, date and total compared with the labels
  wrong_total_unflagged
                     wrong totals that would reach the CSV with no flag and no field sent
                     to review  <- the safety number

Dataset: Voxel51/scanned_receipts on Hugging Face (SROIE; research use).
    python scripts/eval_sroie.py --extractor ollama:docintel-qwen1.5b-ft-v5 --n 40
"""
import argparse
import json
import random
import re
import time
from pathlib import Path

from huggingface_hub import hf_hub_download

from docintel.accounting import parse_date
from docintel.extract.base import coerce
from docintel.extract.registry import make_extractor
from docintel.pipeline.run import process, route

REPO = "Voxel51/scanned_receipts"
RAW = Path("data/sroie_raw")


def norm_name(s) -> str:
    s = re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower())
    s = re.sub(r"\b(sdn|bhd|s b|enterprise|trading|co|ltd|plt|m)\b", " ", s)
    return " ".join(s.split())


def same_vendor(pred, gold) -> bool:
    """Same letters, ignoring spacing and punctuation (OCR often merges words: GARDENIABAKERIES(KL))
    and registration numbers; a misread letter (SANYO for SANYU) still counts as wrong."""
    p, g = (re.sub(r"\d{4,}\w*|[^a-z]", "", norm_name(x).replace(" ", "")) for x in (pred, gold))
    return bool(p) and (p == g or (len(p) >= 6 and (p in g or g in p)))


def same_date(pred, gold) -> bool:
    if not pred or not gold:
        return False
    return parse_date(str(pred), day_first=True) == parse_date(str(gold), day_first=True)


def same_total(pred, gold) -> bool:
    g = coerce(str(gold).replace("$", "").replace("RM", ""), float)
    return pred is not None and g is not None and abs(float(pred) - g) <= 0.01


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extractor", default="ollama:docintel-qwen1.5b-ft-v5")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--seed", type=int, default=3)
    ap.add_argument("--out", default="reports/sroie")
    a = ap.parse_args()
    samples = json.loads(Path(hf_hub_download(REPO, "samples.json", repo_type="dataset", local_dir=RAW)).read_text())
    samples = samples["samples"] if isinstance(samples, dict) else samples
    pick = random.Random(a.seed).sample(samples, a.n)
    ex = make_extractor(a.extractor)
    hits = {"vendor": 0, "date": 0, "total": 0}
    wrong_total = unflagged = failures = 0
    rows, lat = [], []
    for k, s in enumerate(pick, 1):
        img = Path(hf_hub_download(REPO, s["filepath"], repo_type="dataset", local_dir=RAW))
        t0 = time.perf_counter()
        try:
            res = process(img, ex)
        except Exception as e:  # noqa: BLE001 - counted, not fatal
            failures += 1
            rows.append({"file": s["filepath"], "error": str(e)[:200]})
            continue
        lat.append(time.perf_counter() - t0)
        f = {n: v.value for n, v in res.fields.items()}
        review = route(res)["review"]
        ok = {"vendor": same_vendor(f.get("vendor_name"), s["company"]),
              "date": same_date(f.get("invoice_date"), s["date"]),
              "total": same_total(f.get("total"), s["total"])}
        for n in hits:
            hits[n] += ok[n]
        if not ok["total"]:
            wrong_total += 1
            unflagged += not (res.flags or "total" in review)
        rows.append({"file": s["filepath"], "doc_type": res.doc_type.value, "gold": {n: s[n] for n in
                     ("company", "date", "total")}, "pred": {n: f.get(n) for n in ("vendor_name", "invoice_date",
                     "total", "subtotal", "tax")}, "ok": ok, "flags": res.flags, "review": review})
        print(f"{k}/{a.n} {ok}", flush=True)
    n = a.n - failures
    report = {"dataset": "SROIE (Voxel51/scanned_receipts)", "extractor": ex.name, "receipts": a.n,
              "failures": failures, "accuracy": {k: round(v / n, 3) for k, v in hits.items()},
              "wrong_totals": wrong_total, "wrong_total_unflagged": unflagged,
              "p50_seconds": round(sorted(lat)[len(lat) // 2], 1) if lat else None}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    name = ex.name.replace(":", "_").replace("/", "_")
    (out / f"sroie_{name}.json").write_text(json.dumps(report, indent=2))
    (out / f"sroie_{name}_docs.json").write_text(json.dumps(rows, indent=1, default=str))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

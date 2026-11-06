"""Add international invoices (GST, VAT, US sales tax) to the fine-tuning set.

Renders fresh invoices with a different seed from the international test set
(data/synthetic_intl, seed 7), builds chat examples exactly like build_sft.py,
and writes amounts in the answer the way each country prints them
("1.234,56" in Germany, "1,23,456.00" in India), as the prompt asks.
Rows from an earlier run of this script are replaced, not duplicated.

    python scripts/build_sft_intl.py --n 720 --val 45
    python scripts/build_sft_intl.py --layouts --n 960 --val 60   # many page designs (docintel.synth.layouts)

With --layouts only the training families are used; the held-out designs stay unseen.
"""
import argparse
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from build_sft import example

from docintel.synth import layouts
from docintel.synth.intl_invoices import EU, generate, money

STYLE = {"IN": "indian", "UK": "plain", "US": "plain"} | {k: v[2] for k, v in EU.items()}


def localise(row: dict) -> dict:
    """Scalar amounts in the answer as the invoice prints them."""
    gt = json.loads(Path(row.pop("_gt")).read_text(encoding="utf-8"))
    style = STYLE[gt["locale"]]
    answer = json.loads(row["messages"][2]["content"])
    for name, f in answer.items():
        gold = gt["fields"].get(name, {}).get("value")
        if isinstance(gold, float):
            f["value"] = money(gold, style)
    row["messages"][2]["content"] = json.dumps(answer)
    return row


def build(path: str) -> dict:
    row = example(path)
    row["_gt"] = path
    return localise(row)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/synthetic_intl_train")
    ap.add_argument("--sft", default="data/sft")
    ap.add_argument("--n", type=int, default=720)
    ap.add_argument("--val", type=int, default=45)
    ap.add_argument("--seed", type=int, default=2024)  # the test sets use 7 and 11
    ap.add_argument("--layouts", action="store_true", help="varied page designs instead of the single template")
    a = ap.parse_args()
    if a.layouts and a.out == "data/synthetic_intl_train":
        a.out = "data/synthetic_layouts_train"
    bench = "layouts" if a.layouts else "intl"

    os.environ.setdefault("OMP_THREAD_LIMIT", "1")
    if a.layouts:
        print(layouts.generate(Path(a.out), a.n, 0.35, a.seed, layouts.TRAIN_FAMILIES))
    else:
        print(generate(Path(a.out), a.n, 0.35, a.seed))
    paths = sorted(str(p) for p in (Path(a.out) / "docs").glob("*.json"))
    with ProcessPoolExecutor() as pool:
        rows = list(pool.map(build, paths, chunksize=8))
    split = {"val": rows[:a.val], "train": rows[a.val:]}
    for name, new in split.items():
        f = Path(a.sft) / f"{name}.jsonl"
        kept = [r for r in map(json.loads, f.read_text(encoding="utf-8").splitlines()) if r.get("benchmark") != bench]
        f.write_text("".join(json.dumps(r) + "\n" for r in kept + new), encoding="utf-8")
        print(f"{name}: {len(kept)} existing + {len(new)} {bench} = {len(kept) + len(new)}")


if __name__ == "__main__":
    main()

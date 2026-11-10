"""Fill in receipt subtotals the teacher left blank, but only when the page prints that amount.

Malaysian receipts often show "Total incl. GST" plus the GST, with the pre-tax amount on its own line.
If total - tax appears on the page, that line is the subtotal; otherwise the field stays blank.
    python scripts/fix_sroie_subtotals.py data/sft/train.jsonl data/sft/val.jsonl
"""
import json
import re
import sys
from pathlib import Path

SPAN = re.compile(r"\[(\d+)\]\s*([^|\n]*)")


def num(s):
    try:
        return float(str(s).replace(",", "").replace("RM", "").strip())
    except (TypeError, ValueError):
        return None


def fix(row):
    if row.get("benchmark") != "sroie":
        return False
    out = json.loads(row["messages"][-1]["content"])
    if out["subtotal"]["value"] is not None:
        return False
    total, tax = num(out["total"]["value"]), num(out["tax"]["value"])
    if total is None or tax is None or tax <= 0:
        return False
    want = round(total - tax, 2)
    for sid, text in SPAN.findall(row["messages"][1]["content"]):
        for tok in re.findall(r"\d[\d,]*\.\d{2}", text):
            if abs(num(tok) - want) < 0.005:
                out["subtotal"] = {"value": tok, "confidence": 1.0, "span": int(sid)}
                row["messages"][-1]["content"] = json.dumps(out, ensure_ascii=False)
                return True
    return False


for path in sys.argv[1:]:
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]
    n = sum(fix(r) for r in rows)
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    left = sum(1 for r in rows if r.get("benchmark") == "sroie"
               and json.loads(r["messages"][-1]["content"])["subtotal"]["value"] is None)
    print(f"{path}: filled {n} subtotals; {left} receipts still blank (amount not printed)")

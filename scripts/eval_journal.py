"""How often is the journal entry right? Builds the entry from the model's fields and from the
gold fields of each invoice and compares them, so the score reflects what an accountant would
actually post, not just field F1.

    python scripts/eval_journal.py reports/intl/preds_gemini_gemini-3.5-flash-lite_test.jsonl

An entry is *exact* when every line has the same account and amount (within 1 cent).
It is *safe* when it is exact or it was sent to review: a wrong entry that would post
without review is the failure that matters.
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from docintel.accounting import build_entry


def lines(e) -> Counter:
    return Counter((ln.account_code, round(ln.debit, 2), round(ln.credit, 2)) for ln in e.lines)


def values(fields: dict) -> dict:
    return {k: (f.get("value") if isinstance(f, dict) else f) for k, f in fields.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("preds")
    a = ap.parse_args()
    stats: dict[str, Counter] = defaultdict(Counter)
    misses = []
    for row in map(json.loads, Path(a.preds).read_text(encoding="utf-8").splitlines()):
        if row["doc_type"] != "invoice":
            continue
        group = row["doc_id"].rsplit("-", 1)[-1] if "-" in row["doc_id"] else "all"
        gold = build_entry(row["gold"], row["doc_id"], "gold", company="")
        pred = build_entry(values(row.get("fields") or {}), row["doc_id"], "pred", company="")
        exact = lines(gold) == lines(pred) and gold.date == pred.date
        for key in (group, "all"):
            s = stats[key]
            s["invoices"] += 1
            s["exact"] += exact
            s["review"] += pred.needs_review
            s["safe"] += exact or pred.needs_review
            s["wrong_unreviewed"] += not exact and not pred.needs_review
            s["total_ok"] += abs(sum(ln.credit for ln in pred.lines if ln.account_code == "2000") -
                                 sum(ln.credit for ln in gold.lines if ln.account_code == "2000")) < 0.01
            s["tax_ok"] += sorted((ln.account_code, ln.debit) for ln in pred.lines if ln.account_code in ("1400", "6810")) \
                == sorted((ln.account_code, ln.debit) for ln in gold.lines if ln.account_code in ("1400", "6810"))
            s["date_ok"] += gold.date == pred.date
        if not exact and not pred.needs_review:
            misses.append({"doc": row["doc_id"], "gold_only": sorted(lines(gold) - lines(pred)),
                           "pred_only": sorted(lines(pred) - lines(gold)), "dates": [gold.date, pred.date]})
    report = {}
    for key, s in sorted(stats.items(), key=lambda kv: (kv[0] != "all", kv[0])):
        n = s["invoices"]
        report[key] = {"invoices": n, **{k: round(s[k] / n, 3) for k in
                                         ("exact", "review", "safe", "wrong_unreviewed", "total_ok", "tax_ok", "date_ok")}}
    out = Path(a.preds).with_name(Path(a.preds).name.replace("preds_", "journal_").replace(".jsonl", ".json"))
    out.write_text(json.dumps({"report": report, "wrong_unreviewed": misses}, indent=1))
    print(json.dumps(report, indent=1))
    print(f"{len(misses)} wrong entries would post without review; details in {out}")


if __name__ == "__main__":
    main()

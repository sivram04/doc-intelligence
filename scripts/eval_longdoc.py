"""Long-document benchmark: question answering and summary faithfulness on synthetic reports
with known facts (docintel.synth.report).

Question answering
  correct          answer contains the planted value and cites the page it is on
  wrong_shown      an answer was shown but it is wrong, including any answer to an
                   unanswerable question  <- the number that must stay near 0
  not_found        the system said the document does not state it (safe, but a miss)
  unanswerable_ok  questions whose answer is NOT in the report, answered "not stated"
Summary
  statements       overview sentences and key points shown to the user
  false_claims     shown statements with a number that is not on the cited page of the
                   source PDF (checked against the generator's text, not our OCR)

    python scripts/eval_longdoc.py --data data/longdoc --model gemini:gemini-3.5-flash-lite
"""
import argparse
import json
import re
import time
from pathlib import Path

import pymupdf

from docintel.longdoc import verify
from docintel.longdoc.index import Index
from docintel.longdoc.llm import Chat
from docintel.longdoc.qa import ask
from docintel.longdoc.reader import read
from docintel.longdoc.summarize import summarize


def has(answer: str, gold: str) -> bool:
    if re.search(r"\d", gold):
        return bool(verify.numbers(gold) & verify.numbers(answer)) and verify.numbers(gold) <= verify.numbers(answer)
    return verify.norm(gold) in verify.norm(answer)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/longdoc")
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default="reports/longdoc")
    a = ap.parse_args()
    chat = Chat(a.model)
    totals = {"questions": 0, "correct": 0, "right_page": 0, "wrong_shown": 0, "not_found": 0,
              "unanswerable": 0, "unanswerable_ok": 0, "statements": 0, "false_claims": 0, "rejected": 0}
    docs = []
    for gt_path in sorted(Path(a.data).glob("report*.json")):
        gt = json.loads(gt_path.read_text())
        pdf = gt_path.with_suffix(".pdf")
        t0 = time.perf_counter()
        doc = read(pdf)
        index = Index.build(doc.passages)
        t_read = time.perf_counter() - t0
        # True page text from the generator's PDF; for scans, OCR text is all there is, so use ours.
        truth = doc.pages if gt["modality"] == "scan" else [p.get_text() for p in pymupdf.open(pdf)]
        qa_rows = []
        for f in gt["facts"]:
            r = ask(doc, index, f["q"], chat)
            totals["questions"] += 1
            ok = r["found"] and has(r["answer"], f["answer"])
            page_ok = ok and any(c["page"] == f["page"] for c in r["citations"])
            totals["correct"] += ok
            totals["right_page"] += page_ok
            totals["wrong_shown"] += r["found"] and not ok
            totals["not_found"] += not r["found"]
            qa_rows.append({"q": f["q"], "gold": f["answer"], "gold_page": f["page"], **r})
        for q in gt["unanswerable"]:
            r = ask(doc, index, q, chat)
            totals["unanswerable"] += 1
            totals["unanswerable_ok"] += not r["found"]
            totals["wrong_shown"] += r["found"]  # any answer to a question the report cannot answer is wrong
            qa_rows.append({"q": q, "gold": None, **r})
        t1 = time.perf_counter()
        s = summarize(doc, chat)
        shown = s["overview"] + s["key_points"]
        false = []
        for st in shown:
            nums = verify.numbers(st["text"])
            cited = " ".join(truth[c["page"] - 1] for c in st["citations"] if 1 <= c["page"] <= len(truth))
            if nums - verify.numbers(cited) or not st["citations"]:
                false.append(st)
        totals["statements"] += len(shown)
        totals["false_claims"] += len(false)
        totals["rejected"] += len(s["rejected"])
        docs.append({"file": gt["file"], "pages": doc.meta["pages"], "modality": gt["modality"],
                     "read_index_s": round(t_read, 1), "summary_s": round(time.perf_counter() - t1, 1),
                     "qa": qa_rows, "summary": s, "false_claims": false})
        print(f"{gt['file']}: {doc.meta['pages']} pages ({gt['modality']}), {len(shown)} statements, "
              f"{len(false)} false, {len(s['rejected'])} rejected", flush=True)
    q = totals["questions"] or 1
    report = {"model": chat.name, "documents": len(docs), **totals,
              "answer_accuracy": round(totals["correct"] / q, 3),
              "citation_page_accuracy": round(totals["right_page"] / q, 3),
              "wrong_answer_rate": round(totals["wrong_shown"] / (q + totals["unanswerable"]), 3),
              "unanswerable_accuracy": round(totals["unanswerable_ok"] / max(1, totals["unanswerable"]), 3),
              "false_claim_rate": round(totals["false_claims"] / max(1, totals["statements"]), 3)}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    name = chat.name.replace(":", "_").replace("/", "_")
    (out / f"longdoc_{name}.json").write_text(json.dumps(report, indent=2))
    (out / f"longdoc_{name}_docs.json").write_text(json.dumps(docs, indent=1))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

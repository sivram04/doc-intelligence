"""Synthetic long reports (default 70 pages) with known facts, for measuring the long-document
feature: does a question get the right answer and page, and does the summary state anything
the document does not say?

Each report is a fictional company's annual report: highlights, quarterly results, segments,
workforce, sustainability, risks, governance and outlook, padded with realistic procedural
text so the facts are spread across the pages. Ground truth lists every planted fact with its
page, the question that asks for it, and questions whose answers are NOT in the report.

    python -m docintel.synth.report --out data/longdoc --n 2 --pages 70
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from faker import Faker
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer

from .generator_v2 import rasterise

FILLER_TOPICS = ["internal controls", "procurement policy", "data retention", "supplier onboarding",
                 "training programmes", "facility maintenance", "document management", "audit procedures",
                 "travel policy", "information security", "records classification", "quality assurance"]


def facts_for(fk: Faker, rng: random.Random, company: str, year: int) -> list[dict]:
    rev = rng.randint(180, 950) + rng.choice([0.2, 0.4, 0.6, 0.8])
    growth = rng.choice([3.1, 4.8, 6.2, 7.5, 9.4, 11.3, 12.7])
    margin = rng.choice([8.4, 10.2, 12.9, 14.6, 17.3])
    profit = round(rev * margin / 100, 1)
    staff = rng.randint(1200, 9800)
    women = rng.choice([31, 36, 38, 42, 45])
    emis = rng.randint(12000, 88000)
    cut = rng.choice([8, 11, 14, 17, 21])
    div = rng.choice([0.42, 0.55, 0.68, 0.81, 1.12])
    q = [round(rev * w, 1) for w in (0.23, 0.24, 0.25)]
    q.append(round(rev - sum(q), 1))
    ceo, cfo, chair = fk.name(), fk.name(), fk.name()
    city = fk.city()
    seg = rng.choice(["Industrial Services", "Digital Solutions", "Logistics", "Healthcare Products"])
    seg_share = rng.choice([34, 41, 47, 52])
    target = rng.choice([2028, 2030, 2032])
    capex = rng.randint(14, 90)
    return [
        {"key": "revenue", "section": "Financial highlights",
         "sentence": f"Group revenue for {year} was ${rev:,.1f} million, an increase of {growth}% on the prior year.",
         "q": f"What was group revenue in {year}?", "answer": f"{rev:,.1f}"},
        {"key": "growth", "section": "Financial highlights", "sentence": None,
         "q": "By what percentage did revenue grow compared with the prior year?", "answer": f"{growth}",
         "same_as": "revenue"},
        {"key": "profit", "section": "Financial highlights",
         "sentence": f"Operating profit reached ${profit:,.1f} million, giving an operating margin of {margin}%.",
         "q": "What was the operating profit?", "answer": f"{profit:,.1f}"},
        {"key": "margin", "section": "Financial highlights", "sentence": None, "q": "What was the operating margin?",
         "answer": f"{margin}", "same_as": "profit"},
        {"key": "q4", "section": "Quarterly results",
         "sentence": f"Fourth-quarter revenue was ${q[3]:,.1f} million, the strongest quarter of the year, "
                     f"compared with ${q[0]:,.1f} million in the first quarter.",
         "q": "What was revenue in the fourth quarter?", "answer": f"{q[3]:,.1f}"},
        {"key": "segment", "section": "Segment review",
         "sentence": f"The {seg} segment contributed {seg_share}% of group revenue and remained the largest segment.",
         "q": "Which segment was the largest, and what share of revenue did it contribute?",
         "answer": f"{seg_share}"},
        {"key": "staff", "section": "Our people",
         "sentence": f"At the end of {year} the company employed {staff:,} people, of whom {women}% were women.",
         "q": "How many people did the company employ at year end?", "answer": f"{staff:,}"},
        {"key": "women", "section": "Our people", "sentence": None, "q": "What percentage of employees were women?",
         "answer": f"{women}", "same_as": "staff"},
        {"key": "emissions", "section": "Sustainability",
         "sentence": f"Scope 1 and 2 emissions fell {cut}% to {emis:,} tonnes of CO2 equivalent.",
         "q": "What were the Scope 1 and 2 emissions?", "answer": f"{emis:,}"},
        {"key": "netzero", "section": "Sustainability",
         "sentence": f"The board has committed to reaching net zero operational emissions by {target}.",
         "q": "By what year does the company aim to reach net zero operational emissions?", "answer": f"{target}"},
        {"key": "dividend", "section": "Shareholder returns",
         "sentence": f"The board recommends a final dividend of ${div:.2f} per share.",
         "q": "What final dividend per share does the board recommend?", "answer": f"{div:.2f}"},
        {"key": "capex", "section": "Outlook",
         "sentence": f"Capital expenditure of ${capex} million is planned for {year + 1}, mainly for a new facility "
                     f"in {city}.",
         "q": f"How much capital expenditure is planned for {year + 1}?", "answer": f"{capex}"},
        {"key": "ceo", "section": "Governance",
         "sentence": f"{ceo} serves as Chief Executive Officer and {cfo} as Chief Financial Officer.",
         "q": "Who is the Chief Executive Officer?", "answer": ceo},
        {"key": "chair", "section": "Governance", "sentence": f"The board is chaired by {chair}.",
         "q": "Who chairs the board?", "answer": chair},
    ]


UNANSWERABLE = ["What was the company's market share in Brazil?", "How many patents did the company file?",
                "What is the share price target set by analysts?", "What was the research and development budget?",
                "How many customer complaints were recorded?"]


def generate_one(path: Path, rng: random.Random, pages: int) -> dict:
    fk = Faker("en_GB")
    fk.seed_instance(rng.randrange(1 << 30))
    company = fk.company()
    year = rng.choice([2023, 2024, 2025])
    facts = facts_for(fk, rng, company, year)
    styles = getSampleStyleSheet()
    sections = ["Chair's letter", "Financial highlights", "Quarterly results", "Segment review", "Our people",
                "Sustainability", "Risk management", "Shareholder returns", "Governance", "Outlook"]
    base, extra = divmod(max(len(sections), pages - 1), len(sections))
    story, page_of = [Paragraph(f"{company} Annual Report {year}", styles["Title"]),
                      Paragraph("Synthetic document for testing; the company and all figures are fictional.",
                                styles["Italic"]), PageBreak()], {}
    page = 2
    for si, s in enumerate(sections):
        per = base + (si < extra)
        story.append(Paragraph(s, styles["Heading1"]))
        mine = [f for f in facts if f["section"] == s and f["sentence"]]
        slots = sorted(rng.sample(range(per), min(len(mine), per)))
        for k in range(per):
            for _ in range(3):  # three filler paragraphs fill most of a page
                story.append(Paragraph(" ".join(fk.paragraph(nb_sentences=5) for _ in range(1)) + " This section "
                                       f"also covers {rng.choice(FILLER_TOPICS)} for the period under review.",
                                       styles["BodyText"]))
            for f, slot in zip(mine, slots, strict=False):
                if slot == k:
                    story.append(Paragraph(f["sentence"], styles["BodyText"]))
                    page_of[f["key"]] = page
            story.append(PageBreak())
            page += 1
    SimpleDocTemplate(str(path), pagesize=A4, title=f"{company} Annual Report {year}", author=company).build(story)
    for f in facts:
        f["page"] = page_of.get(f.get("same_as") or f["key"])
    return {"file": path.name, "company": company, "year": year, "pages": page - 1, "facts": facts,
            "unanswerable": rng.sample(UNANSWERABLE, 3)}


def generate(out: Path, n: int, pages: int, seed: int, scan: bool) -> list[dict]:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    gts = []
    for i in range(n):
        pdf = out / f"report{i:02d}.pdf"
        gt = generate_one(pdf, rng, pages)
        if scan and i % 2 == 1:  # every second report becomes a scanned copy
            rasterise(pdf, rng)
            gt["modality"] = "scan"
        else:
            gt["modality"] = "native"
        (out / f"report{i:02d}.json").write_text(json.dumps(gt, indent=1))
        gts.append(gt)
    return gts


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/longdoc")
    ap.add_argument("--n", type=int, default=2)
    ap.add_argument("--pages", type=int, default=70)
    ap.add_argument("--seed", type=int, default=21)
    ap.add_argument("--scan", action="store_true", help="make every second report a scan")
    a = ap.parse_args()
    for g in generate(Path(a.out), a.n, a.pages, a.seed, a.scan):
        print(g["file"], g["pages"], "pages", g["modality"], g["company"])

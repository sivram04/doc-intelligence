"""Benchmark v2: realistic, internally consistent financial documents.

Cases (one person: W-2 + pay stub + bank statement) and standalone invoices.
Every number is derived, not decorated: line items sum to the subtotal,
earnings/deductions sum to gross/net with YTD, transactions produce the
statement totals and running balance, and the W-2 matches the pay stub's YTD
(unless a cross-document inconsistency is seeded on purpose).

Split is assigned per group before rendering. The test split is rendered only
with each type's held-out layout family; train/val never see it.
Scans: every page is rasterised and degraded, saved as an image-only PDF.
"""
from __future__ import annotations

import io
import json
import random
from datetime import date, timedelta
from pathlib import Path

import pymupdf
from faker import Faker
from PIL import Image

from .generator import degrade
from .split import assign_split, write_manifest
from .templates import FAMILIES, render_v2

PERIODS = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}
SS_CAP, SS_RATE, MED_RATE = 168_600.0, 0.062, 0.0145
STATES = {"CA": 0.06, "NY": 0.055, "IL": 0.0495, "PA": 0.0307, "GA": 0.0549, "NC": 0.045, "MA": 0.05, "TX": 0.0}
SERVICES = ["Consulting services", "Software license (annual)", "Cloud hosting - standard tier", "Onsite support",
            "Data migration", "Security audit", "Training workshop", "Custom report development", "Hardware - laptop",
            "Monitor 27in", "Network switch 24-port", "Maintenance retainer", "Design review", "API integration",
            "Project management", "Backup storage 1TB", "Office chairs", "Printer toner", "Courier delivery"]
MERCHANTS = ["GROCERY OUTLET", "SHELL OIL", "AMAZON MKTP", "CITY UTILITIES", "NETFLIX.COM", "COSTCO WHSE", "UBER TRIP",
             "TARGET", "STARBUCKS", "VERIZON WIRELESS", "PHARMACY", "HOME DEPOT", "RESTAURANT", "GYM MEMBERSHIP"]
SCAN_DPI = 200


def r2(x: float) -> float:
    return round(x + 1e-9, 2)


def address(fk: Faker) -> str:
    return f"{fk.street_address()}\n{fk.city()}, {fk.state_abbr()} {fk.zipcode()}"


def make_case_v2(cid: str, fk: Faker, rng: random.Random, bad: str | None) -> list[tuple[str, str, dict]]:
    person, employer = fk.name(), fk.company()
    emp_addr, home = address(fk), address(fk)
    year = rng.choice([2023, 2024, 2025])
    freq = rng.choice(list(PERIODS))
    n_per = PERIODS[freq]
    state = rng.choice(list(STATES))
    hourly = freq in ("weekly", "biweekly") and rng.random() < 0.6
    hours = 80.0 if freq == "biweekly" else 40.0
    base = r2(rng.uniform(40_000, 190_000) / n_per)
    if hourly:  # regular pay must equal hours x rate to the cent
        base = r2(r2(base / hours) * hours)
    k401 = rng.choice([0, 0.03, 0.05, 0.06])
    health = r2(rng.choice([0, 45, 85, 140]) * 12 / n_per)

    def period_pay(i: int) -> dict:
        ot = r2(rng.uniform(0, 0.12) * base) if hourly and rng.random() < 0.5 else 0.0
        bonus = r2(rng.uniform(0.05, 0.2) * base) if i == n_per - 1 and rng.random() < 0.4 else 0.0
        return {"regular": base, "overtime": ot, "bonus": bonus}

    periods = [period_pay(i) for i in range(n_per)]
    gross_all = [sum(p.values()) for p in periods]
    ytd_gross = r2(sum(gross_all))
    cur = periods[-1]
    gross = r2(sum(cur.values()))

    def deds(g: float, ytd_before: float) -> dict:
        fed = r2(g * rng.uniform(0.09, 0.16))
        ss = r2(min(g, max(0.0, SS_CAP - ytd_before)) * SS_RATE)
        return {"Federal income tax": fed, "Social Security": ss, "Medicare": r2(g * MED_RATE),
                f"{state} state tax": r2(g * STATES[state]), "401(k)": r2(g * k401), "Health insurance": health}

    ytd_before, ded_hist = 0.0, []
    for g in gross_all:
        ded_hist.append(deds(g, ytd_before))
        ytd_before += g
    cur_d = ded_hist[-1]
    ytd_d = {k: r2(sum(d[k] for d in ded_hist)) for k in cur_d}
    deductions = [{"name": k, "current": v, "ytd": ytd_d[k]} for k, v in cur_d.items() if ytd_d[k] > 0]
    total_ded = r2(sum(d["current"] for d in deductions))
    net = r2(gross - total_ded)

    rate = r2(base / hours) if hourly else 0.0
    earnings = [{"name": "Regular", "hours": hours if hourly else 0, "rate": rate, "current": cur["regular"],
                 "ytd": r2(sum(p["regular"] for p in periods))}]
    if any(p["overtime"] for p in periods):
        earnings.append({"name": "Overtime", "hours": r2(cur["overtime"] / (rate * 1.5)) if rate and cur["overtime"] else 0,
                         "rate": r2(rate * 1.5), "current": cur["overtime"],
                         "ytd": r2(sum(p["overtime"] for p in periods))})
    if any(p["bonus"] for p in periods):
        earnings.append({"name": "Bonus", "hours": 0, "rate": 0, "current": cur["bonus"],
                         "ytd": r2(sum(p["bonus"] for p in periods))})
    pay_date = date(year, 12, rng.randint(15, 31))
    start = pay_date - timedelta(days={"weekly": 7, "biweekly": 14, "semimonthly": 15, "monthly": 30}[freq])

    wages_401k_excluded = r2(ytd_gross - ytd_d["401(k)"] - ytd_d["Health insurance"])
    w2_wages = r2(wages_401k_excluded * (1.25 if bad == "w2_inflated" else 1.0))
    ss_wages = r2(min(ytd_gross - ytd_d["Health insurance"], SS_CAP))
    med_wages = r2(ytd_gross - ytd_d["Health insurance"])
    w2 = {"employer_name": employer, "employer_address": emp_addr, "employer_ein": fk.numerify("##-#######"),
          "employee_name": person, "employee_address": home, "employee_ssn_last4": fk.numerify("####"),
          "control_number": fk.bothify("??####").upper(), "tax_year": year, "wages": w2_wages,
          "federal_tax_withheld": ytd_d["Federal income tax"], "social_security_wages": ss_wages,
          "social_security_tax": r2(ss_wages * SS_RATE), "medicare_wages": med_wages,
          "medicare_tax": r2(med_wages * MED_RATE), "box12_d": ytd_d["401(k)"], "state": state,
          "state_id": fk.numerify("###-####-#"), "state_wages": w2_wages,
          "state_tax_withheld": ytd_d[f"{state} state tax"]}
    stub = {"employer_name": employer, "employer_address": emp_addr, "employee_name": person,
            "employee_id": fk.numerify("E#####"), "pay_period": f"{start:%m/%d/%Y} - {pay_date - timedelta(days=1):%m/%d/%Y}",
            "pay_date": f"{pay_date:%m/%d/%Y}", "pay_frequency": freq, "check_no": fk.numerify("######"),
            "earnings": earnings, "deductions": deductions, "gross_pay": gross, "ytd_gross": ytd_gross,
            "total_deductions": total_ded, "ytd_deductions": r2(sum(ytd_d.values())), "net_pay": net,
            "deposit_last4": fk.numerify("####")}

    # bank: November statement with salary deposits plus everyday debits
    month_start = date(year, 11, 1)
    deposits_per_month = max(1, round(n_per / 12))
    salary = net * (0.5 if bad == "deposits_low" else 1.0)
    txs = []
    for k in range(deposits_per_month):
        txs.append((month_start + timedelta(days=min(29, 1 + k * 30 // deposits_per_month + rng.randint(0, 2))),
                    f"PAYROLL {employer.upper()[:18]} DIR DEP", r2(salary)))
    for _ in range(rng.randint(6, 40)):
        txs.append((month_start + timedelta(days=rng.randint(0, 29)),
                    f"{rng.choice(MERCHANTS)} #{rng.randint(100, 9999)}", -r2(rng.uniform(4, 0.18 * salary))))
    if rng.random() < 0.5:
        txs.append((month_start + timedelta(days=rng.randint(0, 29)), "MOBILE DEPOSIT", r2(rng.uniform(20, 600))))
    txs.sort(key=lambda t: t[0])
    opening = r2(rng.uniform(300, 15_000))
    bal, transactions = opening, []
    for d, desc, amt in txs:
        bal = r2(bal + amt)
        transactions.append({"date": f"{d:%m/%d}", "description": desc, "amount": amt, "balance": bal})
    deps = r2(sum(t["amount"] for t in transactions if t["amount"] > 0))
    wds = r2(-sum(t["amount"] for t in transactions if t["amount"] < 0))
    bank = {"bank_name": rng.choice(["First Example Bank", "Demo Credit Union", "Sample National Bank",
                                     "Placeholder Savings & Loan"]),
            "account_holder": person, "holder_address": home, "account_last4": fk.numerify("####"),
            "statement_period": f"11/01/{year} - 11/30/{year}", "opening_balance": opening,
            "total_deposits": deps, "total_withdrawals": wds, "closing_balance": bal, "transactions": transactions}
    return [(f"{cid}-w2", "w2", w2), (f"{cid}-paystub", "paystub", stub), (f"{cid}-bank", "bank_statement", bank)]


def make_invoice_v2(doc_id: str, fk: Faker, rng: random.Random) -> tuple[str, str, dict]:
    items = []
    for _ in range(rng.choice([2, 3, 4, 5, 6, 8, 10, 14, 18, 24])):
        qty = rng.choice([1, 1, 1, 2, 3, 5, 10, 12, 0.5, 7.5, 40])
        price = r2(rng.choice([rng.uniform(5, 120), rng.uniform(120, 2500)]))
        items.append({"description": rng.choice(SERVICES), "quantity": qty, "unit_price": price, "amount": r2(qty * price)})
    sub = r2(sum(i["amount"] for i in items))
    dpct = rng.choice([0, 0, 0, 5, 10])
    disc = r2(sub * dpct / 100)
    rate = rng.choice([0, 5, 6.25, 7.25, 8.25, 8.875])
    tax = r2((sub - disc) * rate / 100)
    ship = rng.choice([0, 0, 15.0, 45.0, 120.0])
    issued = fk.date_between(start_date="-2y", end_date="today")
    terms = rng.choice([("Net 15", 15), ("Net 30", 30), ("Due on receipt", 0), ("Net 45", 45)])
    bill = fk.company()
    return (doc_id, "invoice", {
        "vendor_name": fk.company(), "vendor_address": address(fk), "invoice_number": fk.bothify("INV-#####"),
        "invoice_date": f"{issued:%m/%d/%Y}", "due_date": f"{issued + timedelta(days=terms[1]):%m/%d/%Y}",
        "terms": terms[0], "bill_to": bill, "bill_to_address": address(fk),
        "ship_to": bill if rng.random() < 0.6 else fk.company(), "ship_to_address": address(fk),
        "line_items": items, "subtotal": sub, "discount": disc, "discount_pct": dpct, "tax_rate": rate, "tax": tax,
        "shipping": ship, "total": r2(sub - disc + tax + ship), "remit_last4": fk.numerify("####")})


# Ground-truth fields per type (what extractors are scored on).
GT_FIELDS = {
    "w2": ["employer_name", "employer_ein", "employee_name", "employee_ssn_last4", "tax_year", "wages",
           "federal_tax_withheld", "social_security_wages", "social_security_tax", "medicare_wages", "medicare_tax",
           "state", "state_wages", "state_tax_withheld"],
    "paystub": ["employer_name", "employee_name", "pay_date", "pay_frequency", "gross_pay", "total_deductions",
                "net_pay", "ytd_gross"],
    "bank_statement": ["bank_name", "account_holder", "account_last4", "statement_period", "opening_balance",
                       "total_deposits", "total_withdrawals", "closing_balance", "transactions"],
    "invoice": ["vendor_name", "invoice_number", "invoice_date", "due_date", "bill_to", "subtotal", "discount", "tax",
                "shipping", "total", "line_items"],
}
TABLE_KEEP = {"line_items": ["description", "quantity", "unit_price", "amount"],
              "transactions": ["date", "description", "amount"]}


def rasterise(pdf: Path, rng: random.Random) -> None:
    """Replace a PDF with an image-only PDF of degraded scans of each page."""
    out = pymupdf.open()
    with pymupdf.open(pdf) as d:
        for page in d:
            pix = page.get_pixmap(dpi=SCAN_DPI, colorspace=pymupdf.csGRAY)
            img = degrade(Image.frombytes("L", (pix.width, pix.height), pix.samples), rng)
            buf = io.BytesIO()
            img.save(buf, "PNG")
            np_ = out.new_page(width=page.rect.width, height=page.rect.height)
            np_.insert_image(np_.rect, stream=buf.getvalue())
    tmp = pdf.with_suffix(".scan.pdf")
    out.save(tmp, garbage=3, deflate=True)
    out.close()
    tmp.replace(pdf)


def render_doc(doc_id: str, doc_type: str, values: dict, out: Path, rng: random.Random, family: int,
               scan: bool, case_id: str | None) -> dict:
    pdf = out / f"{doc_id}.pdf"
    page = render_v2(doc_type, values, str(pdf), rng, family)
    fields = {}
    for name in GT_FIELDS[doc_type]:
        if name in TABLE_KEEP:
            rows = [{k: r[k] for k in TABLE_KEEP[name]} for r in values[name]]
            fields[name] = {"value": rows, "bbox": None,
                            "row_boxes": [page.rows.get(name, {}).get(i, {}) for i in range(len(rows))]}
        else:  # amounts not printed on the page (e.g. no discount line) are absent, not zero
            val = values[name] if (values[name] or name not in ("discount", "shipping")) else None
            fields[name] = {"value": val, "bbox": page.boxes.get(name)}
    if scan:
        rasterise(pdf, rng)
    gt = {"doc_id": doc_id, "doc_type": doc_type, "case_id": case_id, "file": pdf.name, "benchmark": "v2",
          "layout_family": family, "pages": page.page + 1, "modality": "scan" if scan else "native",
          "fields": fields}
    (out / f"{doc_id}.json").write_text(json.dumps(gt, indent=1))
    return gt


def generate_v2(out_dir: Path, n_cases: int = 60, n_invoices: int = 80, inconsistency_rate: float = 0.2,
                scan_rate: float = 0.4, seed: int = 42) -> dict:
    rng = random.Random(seed)
    fk = Faker("en_US")
    fk.seed_instance(seed)
    docs_dir = out_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    cases, assignment = {}, {}
    groups: list[tuple[str, list]] = []
    for i in range(n_cases):
        cid = f"case{i:04d}"
        bad = rng.choice(["w2_inflated", "deposits_low"]) if rng.random() < inconsistency_rate else None
        cases[cid] = {"inconsistent": bad}
        groups.append((cid, make_case_v2(cid, fk, rng, bad)))
    groups += [(f"inv{i:04d}", [make_invoice_v2(f"inv{i:04d}", fk, rng)]) for i in range(n_invoices)]
    for gid, docs in groups:
        split = assign_split(gid, seed)
        for doc_id, doc_type, values in docs:
            held_out = FAMILIES[doc_type] - 1
            family = held_out if split == "test" else rng.randrange(held_out)
            render_doc(doc_id, doc_type, values, docs_dir, rng, family, rng.random() < scan_rate, gid if gid in cases else None)
            assignment[doc_id] = split
    (out_dir / "cases.json").write_text(json.dumps(cases, indent=2))
    write_manifest(out_dir, assignment, seed)
    return cases

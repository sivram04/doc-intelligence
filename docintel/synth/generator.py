"""Synthetic financial-form generator (Phase 0).

Renders W-2s, pay stubs, bank statements and invoices with ReportLab and
writes a ground-truth JSON per document (values + bounding boxes).
Documents are grouped into "cases" (one person: W-2 + pay stub + bank
statement) and a fraction of cases get a seeded cross-document inconsistency
so Phase 2 detection rate can be measured. All data is fake (F12).

To keep the benchmark honest:
- the split is decided per group *before* rendering, and test documents use
  label wording from HELDOUT_LABELS, which no extractor is allowed to import;
- fonts vary; and a share of documents is rasterised and degraded (skew,
  noise, blur, JPEG artefacts) to simulate scans.
"""
from __future__ import annotations

import io
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf
from faker import Faker
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .split import assign_split, write_manifest

W, H = letter
FONTS = [("Helvetica-Bold", "Helvetica"), ("Times-Bold", "Times-Roman"), ("Courier-Bold", "Courier")]
SIZE = 10
SCAN_DPI = 200

TITLES = {
    "w2": ["Form W-2 Wage and Tax Statement", "W-2 Wage and Tax Statement"],
    "paystub": ["Earnings Statement", "Pay Stub", "Payroll Advice"],
    "bank_statement": ["Account Statement", "Monthly Bank Statement"],
    "invoice": ["INVOICE", "Tax Invoice", "Invoice"],
}

# field -> label variants seen in train/val (extractors may use these)
LABELS = {
    "w2": {
        "employer_name": ["Employer name", "c Employer's name"],
        "employer_ein": ["Employer EIN", "b Employer identification number"],
        "employee_name": ["Employee name", "e Employee's name"],
        "employee_ssn_last4": ["SSN (last 4)", "Employee SSN last 4"],
        "tax_year": ["Tax year", "Year"],
        "wages": ["1 Wages, tips, other comp.", "Wages, tips, other compensation"],
        "federal_tax_withheld": ["2 Federal income tax withheld", "Federal tax withheld"],
        "social_security_wages": ["3 Social security wages", "Social security wages"],
        "medicare_wages": ["5 Medicare wages and tips", "Medicare wages"],
    },
    "paystub": {
        "employer_name": ["Company", "Employer"],
        "employee_name": ["Employee", "Employee name"],
        "pay_date": ["Pay date", "Check date"],
        "pay_frequency": ["Pay frequency", "Frequency"],
        "gross_pay": ["Gross pay", "Current gross"],
        "total_deductions": ["Total deductions", "Deductions"],
        "net_pay": ["Net pay", "Net amount"],
        "ytd_gross": ["YTD gross", "Year-to-date gross"],
    },
    "bank_statement": {
        "bank_name": ["Bank", "Institution"],
        "account_holder": ["Account holder", "Customer"],
        "account_last4": ["Account ending", "Account no. (last 4)"],
        "statement_period": ["Statement period", "Period"],
        "opening_balance": ["Opening balance", "Beginning balance"],
        "total_deposits": ["Total deposits", "Deposits and credits"],
        "total_withdrawals": ["Total withdrawals", "Withdrawals and debits"],
        "closing_balance": ["Closing balance", "Ending balance"],
    },
    "invoice": {
        "vendor_name": ["From", "Vendor"],
        "invoice_number": ["Invoice #", "Invoice number"],
        "invoice_date": ["Invoice date", "Date"],
        "bill_to": ["Bill to", "Customer"],
        "subtotal": ["Subtotal", "Sub-total"],
        "tax": ["Tax", "Sales tax"],
        "total": ["Total due", "Amount due"],
    },
}

# Test-split-only wording. Never import this from an extractor.
HELDOUT_LABELS = {
    "w2": {
        "employer_name": "Name of employer", "employer_ein": "Federal EIN",
        "employee_name": "Recipient name", "employee_ssn_last4": "Social security no. (last four)",
        "tax_year": "Reporting year", "wages": "Box 1 wages",
        "federal_tax_withheld": "Box 2 federal withholding", "social_security_wages": "Box 3 SS wages",
        "medicare_wages": "Box 5 Medicare wages",
    },
    "paystub": {
        "employer_name": "Paid by", "employee_name": "Pay to", "pay_date": "Payment date",
        "pay_frequency": "Pay period type", "gross_pay": "Gross earnings this period",
        "total_deductions": "Total withholdings", "net_pay": "Take-home pay", "ytd_gross": "Gross YTD",
    },
    "bank_statement": {
        "bank_name": "Financial institution", "account_holder": "Primary owner",
        "account_last4": "Acct. ending in", "statement_period": "Statement dates",
        "opening_balance": "Previous balance", "total_deposits": "Credits",
        "total_withdrawals": "Debits", "closing_balance": "New balance",
    },
    "invoice": {
        "vendor_name": "Seller", "invoice_number": "Ref. no.", "invoice_date": "Issued",
        "bill_to": "Sold to", "subtotal": "Net amount", "tax": "VAT / tax", "total": "Balance due",
    },
}

PERIODS = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}


def money(x: float) -> str:
    return f"${x:,.2f}"


@dataclass
class Doc:
    doc_id: str
    doc_type: str
    values: dict
    case_id: str | None = None
    fields: dict = field(default_factory=dict)  # filled with bbox on render


def degrade(img: Image.Image, rng: random.Random) -> Image.Image:
    """Make a clean render look like a office-scanner page."""
    img = img.rotate(rng.uniform(-3, 3), resample=Image.BICUBIC, expand=False, fillcolor=255)
    if rng.random() < 0.5:
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.4, 1.0)))
    arr = np.asarray(img, dtype=np.float32)
    arr = arr * rng.uniform(0.85, 1.0) + rng.uniform(0, 20)  # contrast / paper tone
    arr += np.random.default_rng(rng.randrange(2**32)).normal(0, rng.uniform(6, 16), arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=rng.randint(35, 75))
    return Image.open(buf).convert("L")


def render(doc: Doc, out_dir: Path, rng: random.Random, heldout: bool = False, scan: bool = False) -> None:
    """Draw the doc and record value bounding boxes (PDF points, top-left origin).
    Boxes are for the unrotated page; scans add up to 3 degrees of skew."""
    pdf = out_dir / f"{doc.doc_id}.pdf"
    label_font, value_font = rng.choice(FONTS)
    c = canvas.Canvas(str(pdf), pagesize=letter, invariant=1)
    c.setFont(label_font, 16)
    c.drawString(50, H - 60, rng.choice(TITLES[doc.doc_type]))
    stacked = rng.random() < 0.4  # label above value vs label left of value
    two_col = not stacked and rng.random() < 0.5
    items = list(LABELS[doc.doc_type].items())
    if rng.random() < 0.3:
        rng.shuffle(items)
    x, y = 50.0, H - 110
    for i, (name, variants) in enumerate(items):
        label = (HELDOUT_LABELS[doc.doc_type][name] if heldout else rng.choice(variants)) + ("" if stacked else ":")
        raw = doc.values[name]
        text = money(raw) if isinstance(raw, float) else str(raw)
        col_x = x + (280 if two_col and i % 2 else 0)
        c.setFont(label_font, SIZE)
        c.drawString(col_x, y, label)
        if stacked:
            vx, vy = col_x, y - 14
        else:
            vx, vy = col_x + stringWidth(label, label_font, SIZE) + 8, y
        c.setFont(value_font, SIZE)
        c.drawString(vx, vy, text)
        vw = stringWidth(text, value_font, SIZE)
        doc.fields[name] = {
            "value": raw,
            "bbox": {"x0": round(vx, 1), "y0": round(H - vy - SIZE * 0.8, 1),
                     "x1": round(vx + vw, 1), "y1": round(H - vy + SIZE * 0.25, 1), "page": 0},
        }
        if not two_col or i % 2:
            y -= 40 if stacked else 24
    c.setFont(value_font, 7)
    c.drawString(50, 40, "SYNTHETIC DOCUMENT - generated for testing, contains no real data")
    c.save()

    file = pdf.name
    if scan:
        with pymupdf.open(pdf) as d:
            pix = d[0].get_pixmap(dpi=SCAN_DPI, colorspace=pymupdf.csGRAY)
            img = Image.frombytes("L", (pix.width, pix.height), pix.samples)
        pdf.unlink()
        file = f"{doc.doc_id}.png"
        degrade(img, rng).save(out_dir / file, dpi=(SCAN_DPI, SCAN_DPI))
    gt = {"doc_id": doc.doc_id, "doc_type": doc.doc_type, "case_id": doc.case_id, "file": file,
          "modality": "scan" if scan else "native", "heldout_labels": heldout, "fields": doc.fields}
    (out_dir / f"{doc.doc_id}.json").write_text(json.dumps(gt, indent=2))


def make_case(case_id: str, fk: Faker, rng: random.Random, inconsistent: str | None):
    person, employer = fk.name(), fk.company()
    year = rng.choice([2023, 2024, 2025])
    freq = rng.choice(list(PERIODS))
    annual = round(rng.uniform(38_000, 185_000), 2)
    gross = round(annual / PERIODS[freq], 2)
    ded = round(gross * rng.uniform(0.18, 0.32), 2)
    net = round(gross - ded, 2)
    w2_wages = annual * (1.25 if inconsistent == "w2_inflated" else 1.0)
    w2 = Doc(f"{case_id}-w2", "w2", {
        "employer_name": employer, "employer_ein": fk.numerify("##-#######"),
        "employee_name": person, "employee_ssn_last4": fk.numerify("####"),
        "tax_year": year, "wages": round(w2_wages, 2),
        "federal_tax_withheld": round(w2_wages * rng.uniform(0.08, 0.2), 2),
        "social_security_wages": round(min(w2_wages, 168_600), 2),
        "medicare_wages": round(w2_wages, 2),
    }, case_id)
    stub = Doc(f"{case_id}-paystub", "paystub", {
        "employer_name": employer, "employee_name": person,
        "pay_date": f"12/{rng.randint(15, 31):02d}/{year}", "pay_frequency": freq,
        "gross_pay": gross, "total_deductions": ded, "net_pay": net,
        "ytd_gross": round(gross * PERIODS[freq], 2),
    }, case_id)
    month_dep = net * PERIODS[freq] / 12
    if inconsistent == "deposits_low":
        month_dep *= 0.5
    month_dep = round(month_dep + rng.uniform(0, 300), 2)
    opening = round(rng.uniform(500, 20_000), 2)
    wd = round(rng.uniform(0.5, 1.0) * month_dep, 2)
    bank = Doc(f"{case_id}-bank", "bank_statement", {
        "bank_name": rng.choice(["First Example Bank", "Demo Credit Union", "Sample National"]),
        "account_holder": person, "account_last4": fk.numerify("####"),
        "statement_period": f"11/01/{year} - 11/30/{year}",
        "opening_balance": opening, "total_deposits": month_dep,
        "total_withdrawals": wd, "closing_balance": round(opening + month_dep - wd, 2),
    }, case_id)
    return [w2, stub, bank]


def make_invoice(doc_id: str, fk: Faker, rng: random.Random) -> Doc:
    sub = round(rng.uniform(50, 25_000), 2)
    tax = round(sub * rng.choice([0, 0.05, 0.0725, 0.0825]), 2)
    return Doc(doc_id, "invoice", {
        "vendor_name": fk.company(), "invoice_number": fk.bothify("INV-#####"),
        "invoice_date": fk.date(pattern="%m/%d/%Y"), "bill_to": fk.company(),
        "subtotal": sub, "tax": tax, "total": round(sub + tax, 2),
    })


def generate(out_dir: Path, n_cases: int = 60, n_invoices: int = 60, inconsistency_rate: float = 0.2,
             scan_rate: float = 0.4, seed: int = 42) -> dict:
    rng = random.Random(seed)
    fk = Faker()
    fk.seed_instance(seed)
    docs_dir = out_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    cases, assignment = {}, {}
    groups: list[tuple[str, list[Doc]]] = []
    for i in range(n_cases):
        cid = f"case{i:04d}"
        bad = rng.choice(["w2_inflated", "deposits_low"]) if rng.random() < inconsistency_rate else None
        cases[cid] = {"inconsistent": bad}
        groups.append((cid, make_case(cid, fk, rng, bad)))
    groups += [(f"inv{i:04d}", [make_invoice(f"inv{i:04d}", fk, rng)]) for i in range(n_invoices)]
    for gid, docs in groups:
        split = assign_split(gid, seed)
        for d in docs:
            render(d, docs_dir, rng, heldout=split == "test", scan=rng.random() < scan_rate)
            assignment[d.doc_id] = split
    (out_dir / "cases.json").write_text(json.dumps(cases, indent=2))
    write_manifest(out_dir, assignment, seed)
    return cases

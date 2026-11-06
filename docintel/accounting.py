"""Journal entries from invoices.

Each invoice becomes one balanced entry. For a purchase (a bill you received):

    Dr  expense account per line item (mapped from the description)
    Dr  input tax (GST/VAT, recoverable) or sales tax expense
    Dr  freight
        Cr  purchase discounts
        Cr  accounts payable (the invoice total)

A sales invoice (vendor is your company, set COMPANY_NAME) mirrors it:
receivable, revenue, output tax.

Nothing is forced to balance silently. Line items that don't add up to the
subtotal, or a total that doesn't match its parts, put the difference in a
Suspense account and mark the entry for review; differences of a few cents go
to Rounding.

The chart of accounts and keyword mapping are defaults; override them with a
JSON file at COA_PATH (same shape as DEFAULT_COA). Account choices a reviewer
makes are remembered per vendor and description (ACCOUNT_MEMORY).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

ROUNDING_LIMIT = 0.05

DEFAULT_COA: dict = {
    "payable": {"code": "2000", "name": "Accounts Payable"},
    "receivable": {"code": "1200", "name": "Accounts Receivable"},
    "input_tax": {"code": "1400", "name": "Input Tax Credit (GST/VAT)"},
    "output_tax": {"code": "2200", "name": "Output Tax Payable (GST/VAT)"},
    "sales_tax_expense": {"code": "6810", "name": "Sales Tax Paid"},
    "sales_tax_payable": {"code": "2210", "name": "Sales Tax Payable"},
    "freight_in": {"code": "5100", "name": "Freight and Delivery"},
    "purchase_discounts": {"code": "5200", "name": "Purchase Discounts"},
    "sales_discounts": {"code": "4900", "name": "Sales Discounts"},
    "revenue": {"code": "4000", "name": "Sales Revenue"},
    "shipping_income": {"code": "4100", "name": "Shipping Income"},
    "rounding": {"code": "6900", "name": "Rounding Differences"},
    "suspense": {"code": "9999", "name": "Suspense (needs review)"},
    "default_expense": {"code": "6099", "name": "General Expenses"},
    # Expense accounts and the words in a line description that point to them (first match wins).
    "expenses": [
        {"code": "6020", "name": "Software and Subscriptions",
         "keywords": ["software", "licen", "subscription", "saas", "cloud", "hosting", "storage", "domain", "seat"]},
        {"code": "6030", "name": "Computer Equipment",
         "keywords": ["laptop", "monitor", "hardware", "server", "switch", "router", "computer", "keyboard", "printer"]},
        {"code": "6090", "name": "Furniture and Fixtures", "keywords": ["chair", "desk", "furniture", "cabinet", "table"]},
        {"code": "6010", "name": "Office Supplies",
         "keywords": ["paper", "toner", "ink", "stationery", "supplies", "pens", "notebook", "envelope", "office"]},
        {"code": "6040", "name": "Professional Services",
         "keywords": ["consult", "development", "design", "review", "audit", "legal", "migration", "implementation",
                      "training", "report", "support", "service fee", "advisory", "engineering", "labour", "labor"]},
        {"code": "6050", "name": "Rent and Utilities",
         "keywords": ["rent", "lease", "electricity", "water", "internet", "utility", "utilities", "telephone", "phone"]},
        {"code": "6060", "name": "Travel and Meals",
         "keywords": ["hotel", "flight", "airfare", "travel", "meal", "food", "restaurant", "taxi", "lodging"]},
        {"code": "6070", "name": "Repairs and Maintenance", "keywords": ["repair", "maintenance", "servicing", "spare"]},
        {"code": "6080", "name": "Marketing and Advertising",
         "keywords": ["advertis", "marketing", "ads", "campaign", "promotion", "printing"]},
    ],
}

RECOVERABLE_TAX = re.compile(r"\b(gst|cgst|sgst|igst|utgst|vat|hst|pst|qst|ust|mwst|tva|iva|btw)\b", re.IGNORECASE)
GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]\b")
VAT_ID = re.compile(r"\b(?:GB|DE|FR|IT|ES|NL|BE|AT|IE|PT|PL|SE|DK|FI)\s?[\dA-Z]{8,12}\b")
SYMBOLS = {"$": "USD", "₹": "INR", "€": "EUR", "£": "GBP", "¥": "JPY", "Rs": "INR", "A$": "AUD", "C$": "CAD"}


def load_coa() -> dict:
    path = os.getenv("COA_PATH")
    if path and Path(path).exists():
        return DEFAULT_COA | json.loads(Path(path).read_text(encoding="utf-8"))
    return DEFAULT_COA


def _norm(s: object) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", str(s or "").lower()).split())


class AccountMemory:
    """Account choices reviewers made, keyed by vendor + description, then description alone."""

    def __init__(self, path: Path | None = None):
        self.path = path or Path(os.getenv("ACCOUNT_MEMORY", "data/account_memory.json"))
        self.rules: dict[str, dict] = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}

    def get(self, vendor: str | None, description: str | None) -> dict | None:
        d = _norm(description)
        return self.rules.get(f"{_norm(vendor)}|{d}") or self.rules.get(f"|{d}")

    def remember(self, vendor: str | None, description: str | None, account: dict) -> None:
        d = _norm(description)
        if not d:
            return
        acct = {"code": account["code"], "name": account["name"]}
        self.rules[f"{_norm(vendor)}|{d}"] = acct
        self.rules[f"|{d}"] = acct
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.rules, indent=1), encoding="utf-8")


def map_account(description: str | None, coa: dict, memory: AccountMemory | None = None,
                vendor: str | None = None) -> tuple[dict, float, str]:
    """(account, confidence, why) for one line item."""
    if memory and (hit := memory.get(vendor, description)):
        return hit, 0.95, "learned from an earlier review"
    # The last keyword in the description wins: in English the head noun comes last
    # ("printer paper" is paper, "laptop stand" is equipment).
    # A trailing "for ..." / "with ..." describes the purpose, not the item ("toner for printer").
    full = _norm(description)
    best = None
    for text in (f" {re.split(r' (?:for|with|incl|including) ', full)[0]} ", f" {full} "):
        for acct in coa["expenses"]:
            for kw in acct["keywords"]:
                pos = text.rfind(f" {kw}")
                if pos >= 0 and (best is None or pos > best[0]):
                    best = (pos, acct, kw)
        if best:
            break
    if best:
        _, acct, kw = best
        return {"code": acct["code"], "name": acct["name"]}, 0.8, f"description mentions '{kw}'"
    return coa["default_expense"], 0.3, "no keyword matched; default account"


def detect_currency(fields: dict, text: str = "") -> str | None:
    cur = str(fields.get("currency") or "").strip().upper()
    if re.fullmatch(r"[A-Z]{3}", cur):
        return cur
    if cur in SYMBOLS:
        return SYMBOLS[cur]
    tax_id = f"{fields.get('vendor_tax_id') or ''} {fields.get('buyer_tax_id') or ''}"
    if GSTIN.search(tax_id) or "₹" in text or re.search(r"\bINR\b|\bRs\.?\s?\d", text):
        return "INR"
    for sym, code in (("€", "EUR"), ("£", "GBP")):
        if sym in text:
            return code
    m = re.search(r"\b(USD|EUR|GBP|INR|CAD|AUD|SGD|AED|JPY|CHF)\b", text)
    return m.group(1) if m else ("USD" if "$" in text else None)


def parse_date(raw: object, day_first: bool) -> str | None:
    """ISO date from a printed one; None when it can't be read unambiguously enough to post."""
    s = str(raw or "").strip()
    if not s:
        return None
    fmts = ["%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d-%b-%y", "%b %d %Y"]
    fmts += (["%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%y", "%m/%d/%y"] if day_first
             else ["%m/%d/%Y", "%m-%d-%Y", "%d.%m.%Y", "%d/%m/%Y", "%m/%d/%y", "%d/%m/%y"])
    for f in fmts:
        try:
            return datetime.strptime(s, f).date().isoformat()  # noqa: DTZ007 - a calendar date, no time zone
        except ValueError:
            continue
    return None


@dataclass
class JournalLine:
    account_code: str
    account_name: str
    debit: float = 0.0
    credit: float = 0.0
    description: str = ""
    confidence: float = 1.0
    basis: str = ""  # why this account / amount
    items: list[str] = field(default_factory=list)  # line-item descriptions posted here (a reviewer can re-map them)


@dataclass
class JournalEntry:
    entry_id: str
    date: str | None
    reference: str | None
    party: str | None
    currency: str | None
    kind: str  # purchase | sale
    file: str
    lines: list[JournalLine] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    job_id: str = ""

    @property
    def debits(self) -> float:
        return round(sum(line.debit for line in self.lines), 2)

    @property
    def credits(self) -> float:
        return round(sum(line.credit for line in self.lines), 2)

    @property
    def needs_review(self) -> bool:
        return bool(self.issues) or any(line.confidence < 0.5 for line in self.lines)

    def to_dict(self) -> dict:
        return asdict(self) | {"debits": self.debits, "credits": self.credits, "needs_review": self.needs_review}


def _num(v: object) -> float | None:
    return float(v) if isinstance(v, (int, float)) else None


def build_entry(fields: dict, file: str, entry_id: str, text: str = "", coa: dict | None = None,
                memory: AccountMemory | None = None, company: str | None = None,
                accounts: dict[int, dict] | None = None) -> JournalEntry:
    """One journal entry for one invoice. `fields` maps field name -> value (reviewer's final values
    where present). `accounts` overrides the account for line-item rows by index."""
    coa = coa or load_coa()
    company = company if company is not None else os.getenv("COMPANY_NAME", "")
    vendor = fields.get("vendor_name")
    sale = bool(company) and _norm(company) and _norm(company) in _norm(vendor)
    party = fields.get("bill_to") if sale else vendor
    currency = detect_currency(fields, text)
    day_first = currency in ("INR", "EUR", "GBP", "AUD") or bool(GSTIN.search(str(fields.get("vendor_tax_id") or "")))
    e = JournalEntry(entry_id, parse_date(fields.get("invoice_date"), day_first), fields.get("invoice_number"),
                     party, currency, "sale" if sale else "purchase", file)
    if e.date is None:
        e.issues.append(f"Invoice date '{fields.get('invoice_date') or ''}' could not be read")
    if not e.reference:
        e.issues.append("No invoice number")

    side = "credit" if sale else "debit"         # side for expense/revenue, tax and freight
    other = "debit" if sale else "credit"        # side for the party and discounts

    def add(acct: dict, amount: float, desc: str, conf: float = 1.0, basis: str = "", on: str = side,
            items: list[str] | None = None) -> None:
        amount = round(amount, 2)
        if abs(amount) < 0.005:
            return
        if amount < 0:  # a negative debit is a credit
            amount, on = -amount, ("credit" if on == "debit" else "debit")
        e.lines.append(JournalLine(acct["code"], acct["name"], amount if on == "debit" else 0.0,
                                   amount if on == "credit" else 0.0, desc, conf, basis, items or []))

    subtotal, tax, total = _num(fields.get("subtotal")), _num(fields.get("tax")), _num(fields.get("total"))
    discount, shipping = abs(_num(fields.get("discount")) or 0.0), _num(fields.get("shipping")) or 0.0
    items = [r for r in fields.get("line_items") or [] if isinstance(r, dict) and _num(r.get("amount")) is not None]

    # 1. Lines: expense (purchase) or revenue (sale), grouped by account
    grouped: dict[str, list] = {}
    for i, r in enumerate(items):
        if accounts and i in accounts:
            acct, conf, why = accounts[i], 1.0, "chosen by reviewer"
        elif sale:
            acct, conf, why = coa["revenue"], 0.9, "sales invoice"
        else:
            acct, conf, why = map_account(r.get("description"), coa, memory, vendor)
        g = grouped.setdefault(acct["code"], [acct, 0.0, [], 1.0, why])
        g[1] += float(r["amount"])
        g[2].append(str(r.get("description") or f"line {i + 1}"))
        g[3] = min(g[3], conf)
    for acct, amount, descs, conf, why in grouped.values():
        desc = "; ".join(descs[:3]) + (f" (+{len(descs) - 3} more)" if len(descs) > 3 else "")
        add(acct, amount, desc, conf, why, items=descs)
    lines_total = round(sum(float(r["amount"]) for r in items), 2)
    net = subtotal if subtotal is not None else lines_total if items else None
    if not items and net is not None:
        add(coa["revenue"] if sale else coa["default_expense"], net, "invoice subtotal (no line items read)", 0.3,
            "no line items")
    if items and subtotal is not None and abs(lines_total - subtotal) > ROUNDING_LIMIT:
        e.issues.append(f"Line items sum to {lines_total:,.2f} but subtotal is {subtotal:,.2f}")
        add(coa["suspense"], subtotal - lines_total, "difference between subtotal and line items", 0.0, "unexplained")

    # 2. Discount, freight, tax
    if discount:
        add(coa["sales_discounts"] if sale else coa["purchase_discounts"], discount, "discount", on=other)
    if shipping:
        add(coa["shipping_income"] if sale else coa["freight_in"], shipping, "shipping / freight")
    tax_rows = [r for r in fields.get("tax_lines") or [] if isinstance(r, dict) and _num(r.get("amount")) is not None]
    recoverable = bool(RECOVERABLE_TAX.search(" ".join(str(r.get("tax_type") or "") for r in tax_rows))) or \
        currency in ("INR", "EUR", "GBP", "AUD", "CAD") or bool(GSTIN.search(str(fields.get("vendor_tax_id") or ""))) or \
        bool(VAT_ID.search(str(fields.get("vendor_tax_id") or "")))
    if sale:
        tax_acct = coa["output_tax"] if recoverable else coa["sales_tax_payable"]
    else:
        tax_acct = coa["input_tax"] if recoverable else coa["sales_tax_expense"]
    if tax_rows:
        for r in tax_rows:
            rate = f" {r['rate']:g}%" if _num(r.get("rate")) is not None else ""
            add(tax_acct, float(r["amount"]), f"{r.get('tax_type') or 'tax'}{rate}".strip())
        tax_total = round(sum(float(r["amount"]) for r in tax_rows), 2)
        if tax is not None and abs(tax_total - tax) > ROUNDING_LIMIT:
            e.issues.append(f"Tax lines sum to {tax_total:,.2f} but tax total is {tax:,.2f}")
    elif tax:
        add(tax_acct, tax, "tax")

    # 3. The party: payable (purchase) or receivable (sale) for the invoice total
    implied = round((net or 0.0) - discount + (tax or 0.0) + shipping, 2)
    if total is None:
        total = implied
        e.issues.append(f"No total read; using subtotal - discount + tax + shipping = {implied:,.2f}")
    party_acct = coa["receivable"] if sale else coa["payable"]
    add(party_acct, total, f"{'Invoice to' if sale else 'Bill from'} {party or 'unknown party'}", on=other)

    # 4. Balance: cents go to rounding; anything more is a real discrepancy for a person to look at
    diff = round(e.debits - e.credits, 2)
    if diff:
        fix_on = "credit" if diff > 0 else "debit"
        if abs(diff) <= ROUNDING_LIMIT:
            add(coa["rounding"], abs(diff), "rounding", on=fix_on)
        else:
            e.issues.append(f"Total {total:,.2f} != subtotal - discount + tax + shipping ({implied:,.2f})")
            add(coa["suspense"], abs(diff), "unbalanced difference", 0.0, "unexplained", on=fix_on)
    assert abs(e.debits - e.credits) < 0.005, "entry must balance"
    return e

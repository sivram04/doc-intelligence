"""Journal entries from invoices: balance, tax treatment, mapping, discrepancies, exports."""
import pytest

from docintel.accounting import DEFAULT_COA, AccountMemory, build_entry, detect_currency, map_account, parse_date
from docintel.extract.base import coerce


def _codes(e, side):
    return {line.account_code: getattr(line, side) for line in e.lines if getattr(line, side)}


def test_us_purchase_balances_with_discount_freight_and_sales_tax():
    e = build_entry({"vendor_name": "Acme", "invoice_number": "INV-1", "invoice_date": "10/02/2026",
                     "subtotal": 600.0, "discount": -30.0, "tax": 45.6, "shipping": 15.0, "total": 630.6,
                     "line_items": [{"description": "Laptop stand", "amount": 100.0},
                                    {"description": "Software license", "amount": 300.0},
                                    {"description": "Consulting hours", "amount": 200.0}]},
                    "a.pdf", "JE-1", text="$630.60", company="")
    assert e.debits == e.credits == 660.6 and not e.issues and not e.needs_review
    assert e.date == "2026-10-02" and e.currency == "USD"
    assert _codes(e, "credit") == {"5200": 30.0, "2000": 630.6}
    assert _codes(e, "debit")["6810"] == 45.6  # US sales tax is a cost, not a credit


def test_gst_invoice_posts_recoverable_input_tax_per_tax_line_and_reads_dates_day_first():
    e = build_entry({"vendor_name": "Tata Supplies", "invoice_number": "GST/118", "invoice_date": "05/11/2026",
                     "vendor_tax_id": "27AAPFU0939F1ZV", "subtotal": 10000.0, "tax": 1800.0, "total": 11800.0,
                     "line_items": [{"description": "Printer paper A4", "amount": 10000.0}],
                     "tax_lines": [{"tax_type": "CGST", "rate": 9, "amount": 900.0},
                                   {"tax_type": "SGST", "rate": 9, "amount": 900.0}]}, "b.pdf", "JE-2", company="")
    assert e.currency == "INR" and e.date == "2026-11-05"
    tax = [line for line in e.lines if line.account_code == "1400"]
    assert [t.description for t in tax] == ["CGST 9%", "SGST 9%"] and e.debits == e.credits == 11800.0
    assert next(line for line in e.lines if line.debit == 10000.0).account_name == "Office Supplies"


def test_discrepancies_go_to_suspense_and_review_never_silently_balance():
    e = build_entry({"vendor_name": "X", "invoice_date": "2026-11-01", "subtotal": 500.0, "tax": 0.0, "total": 520.0},
                    "c.pdf", "JE-3", company="")
    assert e.needs_review and any("Total 520.00" in i for i in e.issues)
    assert _codes(e, "debit")["9999"] == 20.0 and e.debits == e.credits
    e = build_entry({"vendor_name": "X", "invoice_number": "1", "invoice_date": "2026-11-01", "subtotal": 300.0,
                     "total": 300.0, "line_items": [{"description": "Design", "amount": 250.0}]}, "d.pdf", "JE-4",
                    company="")
    assert any("Line items sum to 250.00" in i for i in e.issues) and e.debits == e.credits == 300.0


def test_cents_go_to_rounding_without_review():
    e = build_entry({"vendor_name": "X", "invoice_number": "1", "invoice_date": "2026-11-01", "subtotal": 100.0,
                     "tax": 8.25, "total": 108.24, "line_items": [{"description": "Design", "amount": 100.0}]},
                    "e.pdf", "JE-5", company="")
    assert not e.issues and _codes(e, "credit")["6900"] == 0.01


def test_sales_invoice_when_vendor_is_our_company():
    e = build_entry({"vendor_name": "My Co Ltd", "bill_to": "Client A", "invoice_number": "S-9",
                     "invoice_date": "Nov 3, 2026", "subtotal": 1000.0, "tax": 80.0, "total": 1080.0},
                    "f.pdf", "JE-6", company="My Co")
    assert e.kind == "sale" and e.party == "Client A"
    assert _codes(e, "debit") == {"1200": 1080.0} and _codes(e, "credit")["4000"] == 1000.0


@pytest.mark.parametrize(("desc", "account"), [
    ("Printer paper A4", "Office Supplies"), ("Toner cartridge for printer", "Office Supplies"),
    ("Laptop stand", "Computer Equipment"), ("Annual SaaS subscription", "Software and Subscriptions"),
    ("Hotel stay 2 nights", "Travel and Meals"), ("Widget X-200", "General Expenses")])
def test_account_mapping(desc, account):
    assert map_account(desc, DEFAULT_COA)[0]["name"] == account


def test_reviewer_choice_is_remembered(tmp_path):
    m = AccountMemory(tmp_path / "mem.json")
    m.remember("Acme", "Widget X-200", {"code": "6030", "name": "Computer Equipment"})
    acct, conf, why = map_account("Widget  X-200", DEFAULT_COA, AccountMemory(tmp_path / "mem.json"), "ACME")
    assert acct["code"] == "6030" and conf > 0.9 and "earlier review" in why


def test_currency_dates_and_number_formats():
    assert detect_currency({"currency": "€"}) == "EUR" and detect_currency({}, "Total ₹ 4,500") == "INR"
    assert detect_currency({"vendor_tax_id": "29ABCDE1234F1Z5"}) == "INR"
    assert parse_date("03/04/2026", day_first=True) == "2026-04-03"
    assert parse_date("03/04/2026", day_first=False) == "2026-03-04"
    assert parse_date("not a date", True) is None
    assert [coerce(s, float) for s in ["1.234,56", "EUR 12.345,00", "1,23,456.78", "12,50", "1,500", "60.000"]] == \
        [1234.56, 12345.0, 123456.78, 12.5, 1500.0, 60000.0]


def test_journal_exports():
    from docintel.export import journal_tables
    e = build_entry({"vendor_name": "Acme", "invoice_number": "INV-1", "invoice_date": "2026-10-02", "subtotal": 100.0,
                     "total": 100.0, "line_items": [{"description": "Design", "amount": 100.0}]}, "a.pdf", "JE-1",
                    company="")
    t = journal_tables([e])
    assert set(t) == {"journal_entries", "quickbooks_journal", "xero_manual_journal"}
    assert t["quickbooks_journal"][1][0][1] == "10/02/2026"
    assert sum(r[-1] for r in t["xero_manual_journal"][1]) == 0  # Xero: signed amounts sum to zero

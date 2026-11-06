"""Validation rules (F6): field-level checks and cross-document "case" checks.
Every flag is a human-readable reason."""
from __future__ import annotations

import re
from typing import Any

from .extract.normalize import known_tax_id
from .schemas import DocType

TOL = 0.01


def _v(fields: dict, name: str) -> Any:
    f = fields.get(name)
    if f is None:
        return None
    return f["value"] if isinstance(f, dict) else f.value


def _close(a: float, b: float, tol: float = TOL) -> bool:
    return abs(a - b) <= tol


def validate_document(doc_type: DocType, fields: dict) -> list[str]:
    v = lambda n: _v(fields, n)
    flags: list[str] = []
    if doc_type == DocType.W2:
        ssw, sst = v("social_security_wages"), v("social_security_tax")
        if None not in (ssw, sst) and not _close(ssw * 0.062, sst, 1.0):
            flags.append(f"Social security tax {sst:,.2f} is not 6.2% of SS wages ({ssw * 0.062:,.2f})")
        if v("employer_ein") and not re.fullmatch(r"\d{2}-\d{7}", str(v("employer_ein"))):
            flags.append(f"EIN '{v('employer_ein')}' is not in NN-NNNNNNN format")
        if v("employee_ssn_last4") and not re.fullmatch(r"\d{4}", str(v("employee_ssn_last4"))):
            flags.append("SSN last-4 must be exactly 4 digits")
        if None not in (v("wages"), v("federal_tax_withheld")) and v("federal_tax_withheld") > v("wages"):
            flags.append("Federal tax withheld exceeds wages")
        if None not in (v("wages"), v("medicare_wages")) and v("medicare_wages") + 1 < v("wages"):
            flags.append("Medicare wages are lower than box 1 wages (unusual)")
    elif doc_type == DocType.PAYSTUB:
        g, d, n = v("gross_pay"), v("total_deductions"), v("net_pay")
        if None not in (g, d, n) and not _close(g - d, n):
            flags.append(f"Net pay {n:,.2f} != gross {g:,.2f} - deductions {d:,.2f}")
        if None not in (g, d) and d > g:
            flags.append(f"Deductions {d:,.2f} exceed gross pay {g:,.2f}")
        if None not in (g, v("ytd_gross")) and v("ytd_gross") + TOL < g:
            flags.append("YTD gross is lower than current-period gross")
    elif doc_type == DocType.BANK_STATEMENT:
        o, dep, wd, c = (v(k) for k in ("opening_balance", "total_deposits", "total_withdrawals", "closing_balance"))
        if None not in (o, dep, wd, c) and not _close(o + dep - wd, c):
            flags.append(f"Closing balance {c:,.2f} != opening + deposits - withdrawals ({o + dep - wd:,.2f})")
        txs = v("transactions")
        if txs and None not in (dep, wd):
            amts = [t.get("amount") for t in txs if isinstance(t, dict) and t.get("amount") is not None]
            cr, db = sum(a for a in amts if a > 0), -sum(a for a in amts if a < 0)
            if not _close(cr, dep, 0.05) or not _close(db, wd, 0.05):
                flags.append(f"Transactions sum to +{cr:,.2f} / -{db:,.2f}, summary says +{dep:,.2f} / -{wd:,.2f}")
    elif doc_type == DocType.INVOICE:
        s, t, tot = v("subtotal"), v("tax"), v("total")
        disc, ship = abs(v("discount") or 0.0), v("shipping") or 0.0  # discounts are often printed negative
        if None not in (s, t, tot) and not _close(s - disc + t + ship, tot):
            flags.append(f"Total {tot:,.2f} != subtotal - discount + tax + shipping ({s - disc + t + ship:,.2f})")
        items = v("line_items")
        if items and s is not None:
            amts = [i.get("amount") for i in items if isinstance(i, dict) and i.get("amount") is not None]
            if amts and not _close(sum(amts), s, 0.05):
                flags.append(f"Line items sum to {sum(amts):,.2f}, subtotal says {s:,.2f}")
        if None not in (s, t) and t > s:
            flags.append(f"Tax {t:,.2f} exceeds subtotal {s:,.2f} (fields likely swapped)")
        for name in ("vendor_tax_id", "buyer_tax_id"):
            if known_tax_id(v(name)) is False:
                flags.append(f"{name.replace('_', ' ').capitalize()} '{v(name)}' does not match the GSTIN/VAT format")
        taxes = [r.get("amount") for r in v("tax_lines") or [] if isinstance(r, dict) and r.get("amount") is not None]
        if taxes and t is not None and not _close(sum(taxes), t, 0.05):
            flags.append(f"Tax lines sum to {sum(taxes):,.2f}, tax total says {t:,.2f}")
    return flags


PERIODS = {"weekly": 52, "biweekly": 26, "semimonthly": 24, "monthly": 12}


def validate_case(docs: dict[DocType, dict], wage_tol: float = 0.10, deposit_tol: float = 0.25) -> list[str]:
    """Cross-document checks over one applicant's documents."""
    flags: list[str] = []
    w2, stub, bank = docs.get(DocType.W2), docs.get(DocType.PAYSTUB), docs.get(DocType.BANK_STATEMENT)
    if w2 and stub:
        wages, ytd = _v(w2, "wages"), _v(stub, "ytd_gross")
        if wages and ytd and abs(wages - ytd) / ytd > wage_tol:
            flags.append(f"W-2 wages {wages:,.0f} differ from pay-stub YTD gross {ytd:,.0f} by "
                         f"{abs(wages - ytd) / ytd:.0%} (> {wage_tol:.0%})")
        for name_field in ("employee_name", "employer_name"):
            a, b = _v(w2, name_field), _v(stub, name_field)
            if a and b and a.lower() != b.lower():
                flags.append(f"{name_field.replace('_', ' ')} differs: W-2 '{a}' vs pay stub '{b}'")
    if stub and bank:
        net, freq, dep = _v(stub, "net_pay"), _v(stub, "pay_frequency"), _v(bank, "total_deposits")
        if net and dep and freq in PERIODS:
            expected = net * PERIODS[freq] / 12
            if dep < expected * (1 - deposit_tol):
                flags.append(f"Monthly deposits {dep:,.0f} are well below expected net income {expected:,.0f}")
    return flags

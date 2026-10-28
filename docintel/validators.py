"""Validation rules (F6): field-level checks and cross-document "case" checks.
Every flag is a human-readable reason."""
from __future__ import annotations

import re
from typing import Any

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
        if None not in (g, v("ytd_gross")) and v("ytd_gross") + TOL < g:
            flags.append("YTD gross is lower than current-period gross")
    elif doc_type == DocType.BANK_STATEMENT:
        o, dep, wd, c = (v(k) for k in ("opening_balance", "total_deposits", "total_withdrawals", "closing_balance"))
        if None not in (o, dep, wd, c) and not _close(o + dep - wd, c):
            flags.append(f"Closing balance {c:,.2f} != opening + deposits - withdrawals ({o + dep - wd:,.2f})")
    elif doc_type == DocType.INVOICE:
        s, t, tot = v("subtotal"), v("tax"), v("total")
        if None not in (s, t, tot) and not _close(s + t, tot):
            flags.append(f"Total {tot:,.2f} != subtotal + tax ({s + t:,.2f})")
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

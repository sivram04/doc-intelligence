"""Post-extraction normalisation, applied to every extractor's output.

- Party names: first line only (models often append the address).
- Currency: symbols to ISO codes.
- Discount: stored as a positive amount, however it is printed.
- Tax IDs: OCR on scans confuses I/1, O/0, S/5, B/8, Z/2. GSTINs and EU VAT numbers
  have fixed layouts (which positions are letters, which are digits), so a
  misread character can be put back when only one reading fits. IDs that still
  don't fit are left as read and flagged by the invoice validator (known_tax_id).
"""
from __future__ import annotations

import re
from typing import Any

from ..schemas import DocType, ExtractedField

TO_DIGIT = {"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "T": "1", "Z": "2", "S": "5", "B": "8", "G": "6"}
TO_LETTER = {v: k for k, v in {"O": "0", "I": "1", "Z": "2", "S": "5", "B": "8", "G": "6"}.items()}
CURRENCY = {"$": "USD", "US$": "USD", "₹": "INR", "RS": "INR", "RS.": "INR", "€": "EUR", "£": "GBP", "¥": "JPY",
            "A$": "AUD", "C$": "CAD", "S$": "SGD", "EURO": "EUR", "EUROS": "EUR", "DOLLARS": "USD", "RUPEES": "INR"}
# Layout per ID type: L = letter, D = digit, A = either
GSTIN_LAYOUT = "DDLLLLLDDDDLAZA"
VAT_LAYOUT = {"DE": "DDDDDDDDD", "FR": "AADDDDDDDDD", "NL": "DDDDDDDDDBDD", "ES": "ADDDDDDDA", "IT": "DDDDDDDDDDD",
              "GB": "DDDDDDDDD", "AT": "UDDDDDDDD", "BE": "DDDDDDDDDD", "IE": "DAAAAAAA", "PT": "DDDDDDDDD"}


def _fit(chars: str, layout: str) -> str | None:
    """Coerce each character to its slot type; None if a slot can't be satisfied."""
    if len(chars) != len(layout):
        return None
    out = []
    for c, slot in zip(chars, layout, strict=True):
        if slot == "D" and not c.isdigit():
            c = TO_DIGIT.get(c, c)
        elif slot == "L" and not c.isalpha():
            c = TO_LETTER.get(c, c)
        elif slot in ("Z", "U", "B") and c != slot:
            c = slot if TO_LETTER.get(c) == slot or (slot == "Z" and c == "2") or (slot == "U" and c == "V") else c
        ok = (slot == "D" and c.isdigit()) or (slot == "L" and c.isalpha()) or slot == "A" or c == slot
        if not ok:
            return None
        out.append(c)
    return "".join(out)


def tax_id(raw: str | None) -> str | None:
    if not raw:
        return raw
    text = str(raw).strip()
    compact = re.sub(r"[\s.\-]", "", text.upper())
    compact = re.sub(r"^(?:GSTIN|GST|VAT|VATNO|USTIDNR|TVA|NIF|PIVA|BTW):?", "", compact)
    if len(compact) == 15 and (g := _fit(compact, GSTIN_LAYOUT)):
        return g
    # EU/UK VAT: two-letter country prefix, sometimes misread as digits (IT -> 1T, 11)
    prefix = "".join(TO_LETTER.get(c, c) for c in compact[:2])
    if prefix == "11":
        prefix = "IT"
    if prefix in VAT_LAYOUT and (body := _fit(compact[2:], VAT_LAYOUT[prefix])):
        return prefix + body
    return text


def currency(raw: str | None) -> str | None:
    if not raw:
        return raw
    s = str(raw).strip()
    if re.fullmatch(r"[A-Za-z]{3}", s):
        return s.upper()
    return CURRENCY.get(s.upper(), CURRENCY.get(s, s))


def first_line(raw: str | None) -> str | None:
    if not raw:
        return raw
    line = next((ln.strip() for ln in str(raw).splitlines() if ln.strip()), "")
    return line or None


RULES = {
    DocType.INVOICE: {"vendor_name": first_line, "bill_to": first_line, "currency": currency,
                      "vendor_tax_id": tax_id, "buyer_tax_id": tax_id,
                      "discount": lambda v: abs(v) if isinstance(v, (int, float)) else v},
}


def normalize(doc_type: DocType, fields: dict[str, ExtractedField[Any]]) -> dict[str, ExtractedField[Any]]:
    for name, fn in RULES.get(doc_type, {}).items():
        f = fields.get(name)
        if f is not None and f.value is not None:
            f.value = fn(f.value)
    return fields


def known_tax_id(raw: str | None) -> bool | None:
    """True when the ID fits a GSTIN or EU/UK VAT layout, False when it looks like one but doesn't
    fit (likely misread), None for other kinds of ID (EIN, ABN, ...) that aren't checked."""
    if not raw:
        return None
    compact = re.sub(r"[\s.\-]", "", str(raw).upper())
    if len(compact) == 15 and compact[:2].isdigit():
        return _fit(compact, GSTIN_LAYOUT) == compact
    if compact[:2] in VAT_LAYOUT:
        return _fit(compact[2:], VAT_LAYOUT[compact[:2]]) == compact[2:]
    if re.fullmatch(r"\d{2}[A-Z0-9]{12,14}", compact):  # GSTIN-like but the wrong length
        return False
    return None

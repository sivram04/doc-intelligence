"""Post-extraction normalisation, applied to every extractor's output.

- Party names: first line only (models often append the address).
- Currency: symbols to ISO codes.
- Discount: stored as a positive amount, however it is printed.
- Totals: small models sometimes put the grand total in the tax field (receipts that print
  "Subtotal / Tax / Total" in a narrow column). When tax exceeds the subtotal and the
  numbers only add up with tax read as the total, the two are put back and marked
  low-confidence, so the document still goes to review.
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
            "A$": "AUD", "C$": "CAD", "S$": "SGD", "EURO": "EUR", "EUROS": "EUR", "DOLLARS": "USD", "RUPEES": "INR",
            "RM": "MYR", "RM.": "MYR", "RINGGIT": "MYR"}
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


TIME_SUFFIX = re.compile(r"[\sT,]+\d{1,2}:\d{2}(?::\d{2})?(?:\s*[AaPp]\.?[Mm]\.?)?\s*$")


def date_only(raw: str | None) -> str | None:
    """Receipts print the time next to the date ('12/06/2018 19:40'); the date field keeps the date."""
    return TIME_SUFFIX.sub("", str(raw)).strip() or raw if raw else raw


def first_line(raw: str | None) -> str | None:
    if not raw:
        return raw
    line = next((ln.strip() for ln in str(raw).splitlines() if ln.strip()), "")
    return line or None


RULES = {
    DocType.INVOICE: {"vendor_name": first_line, "bill_to": first_line, "currency": currency,
                      "invoice_date": date_only, "due_date": date_only,
                      "vendor_tax_id": tax_id, "buyer_tax_id": tax_id,
                      "discount": lambda v: abs(v) if isinstance(v, (int, float)) else v},
}


REPAIRED_CONFIDENCE = 0.4  # below every review threshold: a repaired total is always checked by a person


def _num(f: ExtractedField[Any] | None) -> float | None:
    return float(f.value) if f is not None and isinstance(f.value, (int, float)) else None


def repair_total_in_tax(fields: dict[str, ExtractedField[Any]]) -> bool:
    """Tax read as the grand total: tax > subtotal, and the printed total equals the subtotal
    (model copied it) or subtotal + tax (model summed them). Then tax - subtotal is the real tax."""
    sub, tax, tot = (_num(fields.get(n)) for n in ("subtotal", "tax", "total"))
    if None in (sub, tax) or sub <= 0 or tax <= sub:
        return False
    disc = abs(_num(fields.get("discount")) or 0.0)
    ship = _num(fields.get("shipping")) or 0.0
    base = sub - disc + ship
    if tot is not None and not (abs(tot - sub) <= 0.01 or abs(tot - (sub + tax)) <= 0.01):
        return False
    if not 0 < tax - base <= 0.35 * base:  # what is left must look like a tax, not another amount
        return False
    fields["total"] = ExtractedField(value=round(tax, 2), confidence=REPAIRED_CONFIDENCE,
                                     source_text=fields["tax"].source_text)
    fields["tax"] = ExtractedField(value=round(tax - base, 2), confidence=REPAIRED_CONFIDENCE)
    return True


def normalize(doc_type: DocType, fields: dict[str, ExtractedField[Any]]) -> dict[str, ExtractedField[Any]]:
    for name, fn in RULES.get(doc_type, {}).items():
        f = fields.get(name)
        if f is not None and f.value is not None:
            f.value = fn(f.value)
    if doc_type == DocType.INVOICE:
        repair_total_in_tax(fields)
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

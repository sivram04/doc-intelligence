"""Is each extracted table row really one row of the page?

A model can read every amount right and still pair some with the wrong
description (seen on scans: the amounts of two lines swapped). Totals and sums
cannot catch that, and it puts money in the wrong expense account. So every
row is checked against the OCR layout: its amount and its description must be
printed on the same visual line. Rows that fail are reported, and the document
goes to review.
"""
from __future__ import annotations

import re

from .ocr import Layout

MIN_OVERLAP = 0.5  # share of the description's words that must appear on the amount's line


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if len(w) > 1 or w.isdigit()}


def _amount_forms(x: float) -> set[str]:
    """Digit strings an amount can be printed as (1,234.56 / 1.234,56 / 1 234,56 / 1234.5 all -> 123456)."""
    cents = f"{abs(x):.2f}".replace(".", "")
    return {cents, cents.rstrip("0") if cents.endswith("0") else cents}


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def misaligned_rows(layout: Layout, rows: list[dict], text_col: str = "description",
                    amount_col: str = "amount") -> list[str]:
    """Descriptions of rows whose amount is not printed on the same line as their description.
    Rows whose amount can't be found on any single line are not judged (nothing to compare)."""
    lines = []
    for ids in layout.lines():
        cells = [layout.spans[i].text for i in ids]
        lines.append((_words(" ".join(cells)), {_digits(c) for c in cells}))
    bad = []
    for r in rows:
        desc, amt = str(r.get(text_col) or ""), r.get(amount_col)
        want = _words(desc)
        if not want or not isinstance(amt, (int, float)) or amt == 0:
            continue
        forms = _amount_forms(float(amt))
        holders = [w for w, nums in lines if nums & forms]
        if not holders:
            continue
        if not any(len(want & w) / len(want) >= MIN_OVERLAP for w in holders):
            bad.append(desc)
    return bad


def row_flags(layout: Layout, fields: dict) -> list[str]:
    flags = []
    for table, text_col in (("line_items", "description"), ("transactions", "description")):
        f = fields.get(table)
        rows = getattr(f, "value", None) if f is not None else None
        if rows:
            bad = misaligned_rows(layout, rows, text_col)
            if bad:
                shown = ", ".join(f"'{d[:40]}'" for d in bad[:3]) + (f" and {len(bad) - 3} more" if len(bad) > 3 else "")
                flags.append(f"{len(bad)} {table.replace('_', ' ')} row(s) don't match the page "
                             f"(amount not on the same line as its description): {shown}")
    return flags

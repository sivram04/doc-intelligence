"""Many invoice layouts, so the model learns invoices in general, not one template.

The values (vendor, items, taxes, totals) come from intl_invoices.make_invoice; this
module only changes how they are printed. Each document picks a layout family (the
page structure) and then, independently, its own label wording, column set and order,
date format, currency display, fonts, colours and extra clutter (bank details, notes,
ship-to). Families:

  0 classic      vendor top-left, title top-right, meta label/value on the right
  1 centered     centred letterhead, meta as a strip of boxes (label above value)
  2 sidebar      tinted left column holds vendor and meta; table on the right
  3 letterhead   vendor block top-right, big title left, bordered meta grid
  4 minimal      everything left-aligned as "Label: value", totals under the table
  5 grid         fully ruled table, dark header band, totals inside the grid
  6 receipt      narrow centred column, stacked fields          (held out: test only)
  7 landscape    landscape page, wide table with extra columns  (held out: test only)

Families 6 and 7 never appear in training data, so scores on them measure how the
model copes with page designs it has not seen.

    python -m docintel.synth.layouts --out data/synthetic_layouts --n 120 --families 0-7
"""
from __future__ import annotations

import argparse
import json
import random
import zlib
from pathlib import Path

from reportlab.lib.colors import HexColor, black, white
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

from .generator_v2 import rasterise
from .intl_invoices import EU, HSN, ground_truth, make_invoice, money

TRAIN_FAMILIES = [0, 1, 2, 3, 4, 5]
HELD_OUT = [6, 7]
NAMES = ["classic", "centered", "sidebar", "letterhead", "minimal", "grid", "receipt", "landscape"]

LABELS = {
    "inv": ["Invoice No.", "Invoice #", "Invoice Number", "Bill No.", "Document No.", "Inv. No."],
    "date": ["Invoice Date", "Date", "Date of Issue", "Issue Date", "Dated"],
    "due": ["Due Date", "Payment Due", "Due By", "Pay By"],
    "po": ["PO Number", "P.O. #", "Purchase Order", "Your Order Ref.", "Order No."],
    "bill": ["Bill To", "Billed To", "Customer", "Invoice To", "Sold To", "Client"],
    "sub": ["Subtotal", "Sub-total", "Net Total", "Total excl. tax", "Amount before tax"],
    "disc": ["Discount", "Less: Discount", "Rebate"],
    "ship": ["Shipping", "Delivery", "Freight", "Carriage", "Shipping & handling"],
    "total": ["Total", "Total Due", "Amount Due", "Grand Total", "Balance Due", "Total Payable", "Invoice Total"],
    "desc": ["Description", "Item", "Particulars", "Product / Service", "Details"],
    "qty": ["Qty", "Quantity", "Units", "Qty."],
    "price": ["Rate", "Unit Price", "Price", "Unit Cost"],
    "amount": ["Amount", "Line Total", "Net", "Total"],
}
TITLES = {"IN": ["TAX INVOICE", "Tax Invoice", "GST INVOICE"], "UK": ["VAT INVOICE", "INVOICE", "Tax Invoice"],
          "US": ["INVOICE", "Invoice", "BILL"], "EU": ["INVOICE", "RECHNUNG / INVOICE", "FACTURE / INVOICE", "Invoice"]}
DATE_FMTS = {"US": ["%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"], "UK": ["%d/%m/%Y", "%d %B %Y", "%d %b %Y"],
             "IN": ["%d/%m/%Y", "%d-%m-%Y", "%d-%b-%Y"], "EU": ["%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"]}
FONTS = [("Helvetica", "Helvetica-Bold"), ("Times-Roman", "Times-Bold"), ("Courier", "Courier-Bold")]
ACCENTS = ["#1f4e79", "#7a1f1f", "#1f5c3a", "#333333", "#5b2c83", "#8a5a00", "#00606b"]
UNITS = ["pcs", "ea", "hrs", "set", "box"]


def region(loc: str) -> str:
    return "EU" if loc in EU else loc


class Style:
    """Per-document choices that are independent of the page structure."""

    def __init__(self, v: dict, rng: random.Random, family: int):
        self.rng = rng
        self.lab = {k: rng.choice(opts) for k, opts in LABELS.items()}
        if v["locale"] == "IN":
            self.lab["sub"] = rng.choice(["Total before tax", "Taxable value", "Sub Total"])
        self.reg, self.bold = rng.choice(FONTS if family != 6 else FONTS[::2])
        self.accent = HexColor(rng.choice(ACCENTS))
        self.title = rng.choice(TITLES[region(v["locale"])])
        self.colon = rng.random() < 0.5  # "Invoice No.:" vs "Invoice No."
        # Currency display: symbol before/after the total, ISO code, or a note "All amounts in EUR".
        self.cur = rng.choice(["symbol", "symbol", "code", "note"])
        self.sku = rng.random() < 0.35
        self.unit = rng.random() < 0.25
        self.line_tax = rng.random() < 0.25 and len(v["tax_lines"]) == 1
        self.qty_first = rng.random() < 0.2
        self.serial = rng.random() < 0.6
        self.bank = rng.random() < 0.5
        self.notes = rng.random() < 0.5
        self.ship_to = rng.random() < 0.3
        self.size = rng.choice([8, 8.5, 9])

    def label(self, key: str) -> str:
        s = self.lab[key]
        return f"{s}:" if self.colon and not s.endswith(":") else s


def restyle_dates(v: dict, rng: random.Random) -> None:
    """Pick a date format the locale really uses; ground truth is the printed string."""
    fmt = rng.choice(DATE_FMTS[region(v["locale"])])
    v["date_fmt"] = fmt
    v["invoice_date"] = v["invoice_date_d"].strftime(fmt)
    v["due_date"] = v["due_date_d"].strftime(fmt) if v["due_date_d"] != v["invoice_date_d"] else None


class Pen:
    def __init__(self, c: canvas.Canvas, s: Style):
        self.c, self.s = c, s

    def t(self, x, y, text, bold=False, size=None, color=black, align="left", maxw=None):
        font, size = (self.s.bold if bold else self.s.reg), size or self.s.size
        text = str(text)
        if maxw:  # long addresses: cut to fit (addresses are not scored)
            while text and stringWidth(text, font, size) > maxw:
                text = text[:-1]
        self.c.setFont(font, size)
        self.c.setFillColor(color)
        {"left": self.c.drawString, "right": self.c.drawRightString, "center": self.c.drawCentredString}[align](
            x, y, text)

    def rect(self, x, y, w, h, fill=None, stroke=None):
        if fill:
            self.c.setFillColor(HexColor(fill))
        if stroke:
            self.c.setStrokeColor(HexColor(stroke))
        self.c.rect(x, y, w, h, fill=1 if fill else 0, stroke=1 if stroke else 0)

    def line(self, x0, y0, x1, y1, color="#999999", width=0.6):
        self.c.setStrokeColor(HexColor(color))
        self.c.setLineWidth(width)
        self.c.line(x0, y0, x1, y1)


def total_text(v: dict, s: Style) -> str:
    m = money(v["total"], v["style"])
    if s.cur == "code":
        return f"{v['currency']} {m}"
    if s.cur == "note":
        return m
    if v["locale"] in EU:
        return f"{m} {v['sym']}"
    return f"{v['sym']} {m}" if v["locale"] == "IN" else f"{v['sym']}{m}"


def tax_label(v: dict, tl: dict, rng: random.Random) -> str:
    r = f"{tl['rate']:g}%"
    return rng.choice([f"{tl['tax_type']} @ {r}", f"{tl['tax_type']} {r}", f"{tl['tax_type']} ({r})"])


def meta_rows(v: dict, s: Style) -> list[tuple[str, str]]:
    rows = [(s.label("inv"), v["invoice_number"]), (s.label("date"), v["invoice_date"])]
    if v["due_date"]:
        rows.append((s.label("due"), v["due_date"]))
    if v["purchase_order"]:
        rows.append((s.label("po"), v["purchase_order"]))
    if v["locale"] == "IN":
        rows.append(("Place of Supply:" if s.colon else "Place of Supply", v["place"]))
    return rows


def total_rows(v: dict, s: Style) -> list[tuple[str, str, bool]]:
    st, rng = v["style"], s.rng
    rows = [(s.label("sub"), money(v["subtotal"], st), False)]
    if v["discount"]:
        rows.append((s.label("disc"), money(-v["discount"], st), False))
    if v["shipping"]:
        rows.append((s.label("ship"), money(v["shipping"], st), False))
    for tl in v["tax_lines"]:
        rows.append((tax_label(v, tl, rng), money(tl["amount"], st), False))
    if len(v["tax_lines"]) > 1:
        rows.append(("Total tax", money(v["tax"], st), False))
    lab = s.label("total")
    if s.cur == "code" and rng.random() < 0.5:
        lab = f"{s.lab['total']} ({v['currency']})"
    rows.append((lab, total_text(v, s), True))
    return rows


def columns(v: dict, s: Style, wide: bool = False) -> list[tuple[str, str, float, str]]:
    """(key, header, width weight, align) for the line-item table."""
    cols = []
    if s.serial:
        cols.append(("#", "#", 0.5, "left"))
    if s.sku or wide:
        cols.append(("sku", "SKU" if v["locale"] != "IN" else "Code", 1.2, "left"))
    desc = ("description", s.lab["desc"], 5.0, "left")
    qty = ("quantity", s.lab["qty"], 0.9, "right")
    cols += [qty, desc] if s.qty_first else [desc]
    if v["locale"] == "IN":
        cols.append(("hsn", "HSN/SAC", 1.2, "left"))
    if not s.qty_first:
        cols.append(qty)
    if s.unit or wide:
        cols.append(("unit", "Unit", 0.8, "left"))
    cols.append(("unit_price", s.lab["price"], 1.6, "right"))
    if s.line_tax or wide:
        cols.append(("line_tax", "Tax %", 0.9, "right"))
    cols.append(("amount", s.lab["amount"], 1.9, "right"))
    return cols


def cell(v: dict, key: str, k: int, it: dict, rng: random.Random) -> str:
    st = v["style"]
    if key == "#":
        return str(k)
    if key == "sku":
        return f"{zlib.crc32(it['description'].encode()) % 90000 + 10000}"
    if key == "hsn":
        return HSN.get(it["description"], "998313")
    if key == "quantity":
        return f"{it['quantity']:g}"
    if key == "unit":
        return UNITS[zlib.crc32(it["description"].encode()) % len(UNITS)]
    if key == "line_tax":
        return f"{v['tax_lines'][0]['rate']:g}%"
    if key in ("unit_price", "amount"):
        return money(it[key], st)
    return it["description"]


def table(p: Pen, v: dict, x0: float, x1: float, y: float, cols, ruled: bool, band: str | None,
          zebra: bool) -> float:
    """Draw the item table from y downwards; returns the y below it."""
    total = sum(w for _, _, w, _ in cols)
    xs, x = [], x0
    for _, _, w, _ in cols:
        xs.append((x, x + (x1 - x0) * w / total))
        x += (x1 - x0) * w / total
    rh, size = 15, p.s.size
    if band:
        p.rect(x0, y - 4, x1 - x0, rh, fill=band)
    hdr_color = white if band and band not in ("#eeeeee", "#f2f2f2", "#e8eef5") else black
    for (key, head, _, al), (a, b) in zip(cols, xs, strict=True):
        p.t(a + 3 if al == "left" else b - 3, y, head, True, size, hdr_color, al)
    if not band:
        p.line(x0, y - 5, x1, y - 5, "#444444", 0.8)
    top = y + rh - 4
    y -= rh + 2
    for k, it in enumerate(v["line_items"], 1):
        if zebra and k % 2 == 0:
            p.rect(x0, y - 4, x1 - x0, rh - 1, fill="#f4f4f4")
        for (key, _, _, al), (a, b) in zip(cols, xs, strict=True):
            p.t(a + 3 if al == "left" else b - 3, y, cell(v, key, k, it, p.s.rng), size=size, align=al)
        y -= rh - 1
    bottom = y + rh - 5
    if ruled:
        p.c.setLineWidth(0.5)
        p.c.setStrokeColor(HexColor("#888888"))
        p.c.rect(x0, bottom, x1 - x0, top - bottom, fill=0, stroke=1)
        for a, _ in xs[1:]:
            p.line(a, bottom, a, top, "#888888", 0.5)
    else:
        p.line(x0, bottom, x1, bottom)
    return bottom - 14


def totals(p: Pen, v: dict, xl: float, xr: float, y: float, boxed: bool = False) -> float:
    rows = total_rows(v, p.s)
    if boxed:
        p.rect(xl - 6, y - 13 * len(rows) + 6, xr - xl + 10, 13 * len(rows) + 4, stroke="#888888")
    for lab, val, strong in rows:
        size = p.s.size + (1.5 if strong else 0)
        if strong:
            y -= 3
        p.t(xl, y, lab, strong, size)
        p.t(xr, y, val, strong, size, align="right")
        y -= 13
    if p.s.cur == "note":
        p.t(xl, y - 2, f"All amounts in {v['currency']}", size=p.s.size - 1, color=HexColor("#555555"))
        y -= 14
    return y


def party(p: Pen, v: dict, x: float, y: float, w: float, title_color=None) -> float:
    p.t(x, y, p.s.lab["bill"].upper() if p.s.rng.random() < 0.4 else p.s.label("bill"), True,
        color=title_color or p.s.accent)
    p.t(x, y - 13, v["bill_to"], True, p.s.size + 1, maxw=w)
    p.t(x, y - 25, v["buyer_address"], size=p.s.size - 0.5, maxw=w)
    y -= 37
    if v["buyer_tax_id"]:
        p.t(x, y, f"{tax_name(v)}: {v['buyer_tax_id']}", size=p.s.size - 0.5)
        y -= 12
    return y


def ship_to(p: Pen, v: dict, x: float, y: float, w: float) -> None:
    if p.s.ship_to:
        p.t(x, y, "Ship To", True, color=p.s.accent)
        p.t(x, y - 13, v["buyer_address"], size=p.s.size - 0.5, maxw=w)


def tax_name(v: dict) -> str:
    return {"IN": "GSTIN", "US": "EIN"}.get(v["locale"], "VAT No.")


def vendor_tax(v: dict) -> str | None:
    if not v["vendor_tax_id"]:
        return None
    return v["vendor_tax_id"] if v["locale"] == "US" else f"{tax_name(v)}: {v['vendor_tax_id']}"


def footer(p: Pen, v: dict, x: float, y: float, w: float) -> None:
    rng = p.s.rng
    if p.s.notes:
        p.t(x, y, rng.choice(["Payment terms: 30 days net.", "Please quote the invoice number with payment.",
                              "Goods remain our property until paid in full."]), size=p.s.size - 1, maxw=w)
        y -= 12
    if p.s.bank:
        acct = "".join(str(rng.randrange(10)) for _ in range(10))
        p.t(x, y, f"Bank: Example Bank  Account {acct}  Ref: {v['invoice_number']}", size=p.s.size - 1, maxw=w)
        y -= 12
    p.t(x, max(y, 30), "Synthetic document for testing; not a real invoice.", size=6.5, color=HexColor("#888888"))


# ---- families -------------------------------------------------------------------------------------------------

def f_classic(p: Pen, v: dict, W: float, H: float) -> None:
    s, m = p.s, 40
    p.t(m, H - 50, v["vendor_name"], True, 15, s.accent)
    p.t(m, H - 64, v["vendor_address"], size=8, maxw=300)
    if vendor_tax(v):
        p.t(m, H - 76, vendor_tax(v), size=8.5)
    p.t(W - m, H - 52, s.title, True, 16, s.accent, "right")
    for i, (lb, val) in enumerate(meta_rows(v, s)):
        p.t(W - m - 200, H - 100 - 13 * i, lb, True)
        p.t(W - m, H - 100 - 13 * i, val, align="right")
    party(p, v, m, H - 110, 260)
    ship_to(p, v, m, H - 175, 260)
    y = table(p, v, m, W - m, H - 215, columns(v, s), False, "#eeeeee", False)
    y = totals(p, v, W - m - 210, W - m - 4, y)
    footer(p, v, m, min(y - 20, 90), W - 2 * m)


def f_centered(p: Pen, v: dict, W: float, H: float) -> None:
    s, m = p.s, 45
    p.t(W / 2, H - 48, v["vendor_name"], True, 17, s.accent, "center")
    p.t(W / 2, H - 62, v["vendor_address"], size=8, align="center", maxw=W - 2 * m)
    if vendor_tax(v):
        p.t(W / 2, H - 74, vendor_tax(v), size=8.5, align="center")
    p.line(m, H - 84, W - m, H - 84, "#444444", 1)
    p.t(W / 2, H - 104, s.title, True, 14, align="center")
    meta = meta_rows(v, s)
    bw = (W - 2 * m) / len(meta)
    for i, (lb, val) in enumerate(meta):  # strip of boxes: label above value
        x = m + i * bw
        p.rect(x, H - 150, bw, 32, stroke="#999999")
        p.t(x + bw / 2, H - 129, lb.rstrip(":"), True, s.size - 0.5, align="center")
        p.t(x + bw / 2, H - 143, val, size=s.size, align="center", maxw=bw - 4)
    party(p, v, m, H - 175, 240)
    ship_to(p, v, W / 2 + 10, H - 175, 230)
    y = table(p, v, m, W - m, H - 240, columns(v, s), False, s.rng.choice(["#e8eef5", "#333333"]), True)
    y = totals(p, v, W - m - 220, W - m - 4, y, boxed=s.rng.random() < 0.5)
    footer(p, v, m, min(y - 20, 90), W - 2 * m)


def f_sidebar(p: Pen, v: dict, W: float, H: float) -> None:
    s, side = p.s, 165
    p.rect(0, 0, side, H, fill=s.rng.choice(["#eef2f7", "#f3efe6", "#eef5ef"]))
    x = 18
    p.t(x, H - 50, v["vendor_name"], True, 12, s.accent, maxw=side - 24)
    p.t(x, H - 63, v["vendor_address"][:30], size=7.5, maxw=side - 24)
    p.t(x, H - 73, v["vendor_address"][30:60], size=7.5, maxw=side - 24)
    if vendor_tax(v):
        p.t(x, H - 86, vendor_tax(v), size=7.5, maxw=side - 24)
    y = H - 120
    for lb, val in meta_rows(v, s):  # stacked: label above value
        p.t(x, y, lb.rstrip(":").upper(), True, 7, s.accent)
        p.t(x, y - 11, val, size=s.size, maxw=side - 24)
        y -= 30
    y = party(p, v, x, y - 10, side - 24)
    m = side + 22
    p.t(W - 30, H - 55, s.title, True, 20, s.accent, "right")
    y = table(p, v, m, W - 30, H - 120, columns(v, s), False, None, s.rng.random() < 0.5)
    y = totals(p, v, W - 230, W - 34, y)
    footer(p, v, m, min(y - 20, 90), W - m - 30)


def f_letterhead(p: Pen, v: dict, W: float, H: float) -> None:
    s, m = p.s, 40
    p.t(m, H - 60, s.title, True, 24, s.accent)
    p.t(W - m, H - 45, v["vendor_name"], True, 12, align="right")
    p.t(W - m, H - 58, v["vendor_address"], size=7.5, align="right", maxw=260)
    if vendor_tax(v):
        p.t(W - m, H - 70, vendor_tax(v), size=8, align="right")
    meta = meta_rows(v, s)
    gx, gy, cw, rh = m, H - 95, 110, 16  # bordered two-column grid
    for i, (lb, val) in enumerate(meta):
        yy = gy - i * rh
        p.rect(gx, yy - 5, cw, rh, fill="#f2f2f2", stroke="#999999")
        p.rect(gx + cw, yy - 5, 130, rh, stroke="#999999")
        p.t(gx + 4, yy, lb.rstrip(":"), True, s.size - 0.5)
        p.t(gx + cw + 4, yy, val, size=s.size, maxw=124)
    party(p, v, W - m - 220, H - 100, 215)
    top = gy - len(meta) * rh - 30
    ship_to(p, v, W - m - 220, top + 12, 215)
    y = table(p, v, m, W - m, top - 10, columns(v, s), s.rng.random() < 0.5, "#eeeeee", False)
    y = totals(p, v, W - m - 210, W - m - 4, y, boxed=True)
    footer(p, v, m, min(y - 20, 90), W - 2 * m)


def f_minimal(p: Pen, v: dict, W: float, H: float) -> None:
    s, m = p.s, 50
    s.colon = True
    p.t(m, H - 50, f"{s.title}", True, 13)
    p.t(m, H - 72, v["vendor_name"], True, 11)
    p.t(m, H - 84, v["vendor_address"], size=8, maxw=W - 2 * m)
    y = H - 96
    if vendor_tax(v):
        p.t(m, y, vendor_tax(v), size=8.5)
        y -= 12
    y -= 8
    for lb, val in meta_rows(v, s):
        p.t(m, y, f"{lb} {val}")
        y -= 12
    y = party(p, v, m, y - 10, W - 2 * m)
    y = table(p, v, m, W - m, y - 12, columns(v, s), False, None, False)
    y = totals(p, v, m, m + 230, y)
    footer(p, v, m, min(y - 20, 90), W - 2 * m)


def f_grid(p: Pen, v: dict, W: float, H: float) -> None:
    s, m = p.s, 36
    p.rect(0, H - 80, W, 80, fill=s.rng.choice(["#1f4e79", "#333333", "#1f5c3a"]))
    p.t(m, H - 40, v["vendor_name"], True, 15, white)
    p.t(m, H - 55, v["vendor_address"], size=8, color=white, maxw=330)
    if vendor_tax(v):
        p.t(m, H - 67, vendor_tax(v), size=8, color=white)
    p.t(W - m, H - 45, s.title, True, 18, white, "right")
    meta = meta_rows(v, s)
    for i, (lb, val) in enumerate(meta):
        p.t(W - m - 190, H - 105 - 13 * i, lb, True)
        p.t(W - m, H - 105 - 13 * i, val, align="right")
    party(p, v, m, H - 105, 250)
    top = H - 120 - 13 * max(len(meta), 4)
    y = table(p, v, m, W - m, top, columns(v, s), True, "#333333", False)
    # totals as extra ruled rows under the table, right half
    rows = total_rows(v, s)
    x0 = W / 2
    y -= 2
    for lab, val, strong in rows:
        p.rect(x0, y - 5, W - m - x0, 15, stroke="#888888", fill="#f2f2f2" if strong else None)
        p.t(x0 + 4, y, lab, strong)
        p.t(W - m - 4, y, val, strong, align="right")
        y -= 15
    if s.cur == "note":
        p.t(x0 + 4, y - 4, f"All amounts in {v['currency']}", size=s.size - 1)
        y -= 14
    footer(p, v, m, min(y - 20, 90), W - 2 * m)


def f_receipt(p: Pen, v: dict, W: float, H: float) -> None:
    """Held out. A narrow centred column: everything stacked, dashed separators."""
    s = p.s
    x0, x1 = W / 2 - 165, W / 2 + 165
    cx = W / 2
    p.t(cx, H - 50, v["vendor_name"], True, 13, align="center", maxw=330)
    p.t(cx, H - 63, v["vendor_address"], size=7.5, align="center", maxw=330)
    y = H - 75
    if vendor_tax(v):
        p.t(cx, y, vendor_tax(v), size=8, align="center")
        y -= 12
    p.t(cx, y - 8, s.title, True, 12, align="center")
    y -= 26
    dash = "- " * 60

    def sep(yy):
        p.t(cx, yy, dash, size=6, align="center", maxw=330)

    sep(y)
    y -= 14
    for lb, val in meta_rows(v, s):
        p.t(x0, y, lb)
        p.t(x1, y, val, align="right")
        y -= 12
    p.t(x0, y, f"{s.label('bill')} {v['bill_to']}", maxw=330)
    y -= 12
    if v["buyer_tax_id"]:
        p.t(x0, y, f"{tax_name(v)}: {v['buyer_tax_id']}")
        y -= 12
    sep(y)
    y -= 16
    cols = [c for c in columns(v, s) if c[0] in ("description", "quantity", "unit_price", "amount", "hsn")]
    y = table(p, v, x0, x1, y, cols, False, None, False)
    sep(y + 6)
    y = totals(p, v, x0, x1, y - 6)
    sep(y)
    footer(p, v, x0, y - 16, 330)


def f_landscape(p: Pen, v: dict, W: float, H: float) -> None:
    """Held out. Landscape page; vendor and meta side by side; wide table with every extra column."""
    s, m = p.s, 36
    p.t(m, H - 45, v["vendor_name"], True, 16, s.accent)
    p.t(m, H - 59, v["vendor_address"], size=8, maxw=360)
    if vendor_tax(v):
        p.t(m, H - 71, vendor_tax(v), size=8)
    p.t(W / 2, H - 45, s.title, True, 18, align="center")
    meta = meta_rows(v, s)
    for i, (lb, val) in enumerate(meta):
        col, row = divmod(i, 3)
        x = W - m - 330 + col * 170
        p.t(x, H - 75 - 13 * row, lb, True)
        p.t(x + 160, H - 75 - 13 * row, val, align="right")
    party(p, v, m, H - 95, 300)
    ship_to(p, v, 380, H - 95, 300)
    y = table(p, v, m, W - m, H - 160, columns(v, s, wide=True), True, "#e8eef5", True)
    y = totals(p, v, W - m - 240, W - m - 4, y, boxed=s.rng.random() < 0.5)
    footer(p, v, m, min(y - 20, 60), W / 2)


FAMILIES = [f_classic, f_centered, f_sidebar, f_letterhead, f_minimal, f_grid, f_receipt, f_landscape]


def render_layout(v: dict, path: Path, rng: random.Random, family: int) -> None:
    from reportlab.lib.pagesizes import A4, landscape, letter
    size = landscape(A4) if family == 7 else (letter if v["locale"] == "US" and rng.random() < 0.7 else A4)
    c = canvas.Canvas(str(path), pagesize=size)
    W, H = size
    restyle_dates(v, rng)
    FAMILIES[family](Pen(c, Style(v, rng, family)), v, W, H)
    c.showPage()
    c.save()


def parse_families(spec: str) -> list[int]:
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def generate(out: Path, n: int, scan_rate: float, seed: int, families: list[int]) -> dict:
    rng = random.Random(seed)
    docs = out / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    locales = ["IN", "IN", "UK", "US", "US", "DE", "FR", "NL", "ES", "IT"]
    ids, by_family = [], {}
    for i in range(n):
        loc = locales[i % len(locales)]
        fam = families[(i // len(locales) + i) % len(families)]
        doc_id = f"lay{i:04d}-{NAMES[fam]}-{loc.lower()}"
        v = make_invoice(doc_id, loc, rng)
        pdf = docs / f"{doc_id}.pdf"
        render_layout(v, pdf, rng, fam)
        scan = rng.random() < scan_rate
        if scan:
            rasterise(pdf, rng)
        gt = ground_truth(doc_id, v, "scan" if scan else "native", pdf.name) | {"benchmark": "layouts",
                                                                                "layout": NAMES[fam]}
        (docs / f"{doc_id}.json").write_text(json.dumps(gt, indent=1, default=str))
        ids.append(doc_id)
        by_family[NAMES[fam]] = by_family.get(NAMES[fam], 0) + 1
    (out / "splits.json").write_text(json.dumps({"seed": seed, "splits": {"train": [], "val": [], "test": ids}},
                                                indent=1))
    (out / "cases.json").write_text("{}")
    return {"documents": len(ids), "by_layout": by_family}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/synthetic_layouts")
    ap.add_argument("--n", type=int, default=120)
    ap.add_argument("--scan-rate", type=float, default=0.3)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--families", default="0-7")
    a = ap.parse_args()
    print(generate(Path(a.out), a.n, a.scan_rate, a.seed, parse_families(a.families)))

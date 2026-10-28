"""Realistic document templates (benchmark v2).

Each document type has several *layout families*. The last family of each type
is held out: it is only ever rendered for the test split, so test F1 measures
generalisation to an unseen layout, not memorised coordinates.

- W-2: the IRS grid (boxes a-f, 1-20, state/local strip), two families.
- Invoice: letterhead, bill-to/ship-to, line-item table that can span pages,
  discount / tax rate / shipping, payment terms; three families.
- Pay stub: employee/period block, earnings table (hours, rate, current, YTD),
  deductions table (current, YTD), summary; two families.
- Bank statement: account summary box, multi-page transaction table with
  running balance; two families.

Every value drawn for a schema field is recorded with its bounding box (PDF
points, top-left origin) so ground truth matches what is on the page.
All content is synthetic (F12): SSNs are masked, names come from Faker.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

W, H = letter
FAMILIES = {"w2": 2, "invoice": 3, "paystub": 2, "bank_statement": 2}
FONTS = [("Helvetica", "Helvetica-Bold"), ("Times-Roman", "Times-Bold"), ("Courier", "Courier-Bold")]
ACCENTS = ["#1f3a5f", "#2e5e4e", "#5b2a3c", "#3d3d3d", "#7a4b12", "#123d6a"]


def money(x: float, sign: bool = False) -> str:
    s = f"${abs(x):,.2f}"
    if x < 0:
        return f"-{s}" if not sign else f"({s})"
    return s


def plain(x: float) -> str:
    return f"{x:,.2f}"


@dataclass
class Page:
    """Canvas wrapper that records the bbox of every field value it draws."""
    c: canvas.Canvas
    page: int = 0
    boxes: dict = field(default_factory=dict)
    rows: dict = field(default_factory=dict)

    def text(self, x, y, s, font, size, color=black, align="left", key=None, row=None):
        s = str(s)
        w = stringWidth(s, font, size)
        x0 = {"left": x, "right": x - w, "center": x - w / 2}[align]
        self.c.setFillColor(color)
        self.c.setFont(font, size)
        self.c.drawString(x0, y, s)
        bbox = {"x0": round(x0, 1), "y0": round(H - y - size * 0.8, 1), "x1": round(x0 + w, 1),
                "y1": round(H - y + size * 0.25, 1), "page": self.page}
        if key:
            self.boxes[key] = bbox
        if row:
            self.rows.setdefault(row[0], {}).setdefault(row[1], {})[row[2]] = bbox
        return w

    def new_page(self):
        self.c.showPage()
        self.page += 1


def _footer(p: Page, font: str, n: int, total: int):
    p.text(W / 2, 24, f"Page {n} of {total}", font, 7, HexColor("#666666"), "center")
    p.text(36, 24, "SYNTHETIC DOCUMENT - generated for testing, contains no real data", font, 6, HexColor("#888888"))


# --------------------------------------------------------------------------- W-2
def render_w2(c: canvas.Canvas, v: dict, rng: random.Random, family: int) -> Page:
    p = Page(c)
    reg, bold = rng.choice(FONTS[:2])
    lab = HexColor("#222222")
    x0, top, w = 36, H - 60, W - 72
    if family == 1:  # held-out: shaded header band, boxes 1-6 in a 3x2 block, different widths
        c.setFillColor(HexColor("#e8ecf2"))
        c.rect(x0, top + 6, w, 22, fill=1, stroke=0)
    c.setLineWidth(0.8)
    c.setStrokeColor(black)
    left_w = w * (0.5 if family == 0 else 0.46)
    row_h = 30

    def box(x, y, bw, bh, label, value, key=None, vsize=9.5):
        c.setStrokeColor(black)
        c.rect(x, y - bh, bw, bh, fill=0, stroke=1)
        p.text(x + 3, y - 8, label, reg, 6, lab)
        if value is not None:
            p.text(x + 5, y - bh + 7, value, reg, vsize, black, key=key)

    p.text(x0, top + 12, "Form W-2", bold, 13 if family == 0 else 12)
    p.text(x0 + 70, top + 12, "Wage and Tax Statement", bold, 11)
    p.text(x0 + w, top + 12, str(v["tax_year"]), bold, 16, align="right", key="tax_year")
    p.text(x0 + w - 60, top + 14, "Department of the Treasury - Internal Revenue Service", reg, 5.5, lab, "right")

    y = top
    box(x0, y, left_w * 0.5, row_h, "a  Employee's social security number", f"XXX-XX-{v['employee_ssn_last4']}",
        "employee_ssn_last4")
    box(x0 + left_w * 0.5, y, left_w * 0.5, row_h, "OMB No. 1545-0008", None)
    y -= row_h
    box(x0, y, left_w, row_h, "b  Employer identification number (EIN)", v["employer_ein"], "employer_ein")
    y -= row_h
    c.rect(x0, y - row_h * 2.4, left_w, row_h * 2.4)
    p.text(x0 + 3, y - 8, "c  Employer's name, address, and ZIP code", reg, 6, lab)
    p.text(x0 + 5, y - 20, v["employer_name"], reg, 9.5, key="employer_name")
    for i, line in enumerate(v["employer_address"].split("\n")):
        p.text(x0 + 5, y - 32 - 11 * i, line, reg, 9)
    y -= row_h * 2.4
    box(x0, y, left_w, row_h, "d  Control number", v["control_number"])
    y -= row_h
    c.rect(x0, y - row_h * 2.4, left_w, row_h * 2.4)
    p.text(x0 + 3, y - 8, "e  Employee's first name and initial    Last name", reg, 6, lab)
    p.text(x0 + 5, y - 20, v["employee_name"], reg, 9.5, key="employee_name")
    for i, line in enumerate(v["employee_address"].split("\n")):
        p.text(x0 + 5, y - 32 - 11 * i, line, reg, 9)
    p.text(x0 + 3, y - row_h * 2.4 + 4, "f  Employee's address and ZIP code", reg, 6, lab)
    left_bottom = y - row_h * 2.4

    rx, rw = x0 + left_w, w - left_w
    pairs = [("1  Wages, tips, other compensation", "wages", "2  Federal income tax withheld", "federal_tax_withheld"),
             ("3  Social security wages", "social_security_wages", "4  Social security tax withheld", "social_security_tax"),
             ("5  Medicare wages and tips", "medicare_wages", "6  Medicare tax withheld", "medicare_tax"),
             ("7  Social security tips", None, "8  Allocated tips", None),
             ("9", None, "10  Dependent care benefits", None),
             ("11  Nonqualified plans", None, "12a  See instructions for box 12", "box12")]
    y = top
    for la, ka, lb, kb in pairs:
        box(rx, y, rw / 2, row_h, la, plain(v[ka]) if ka else None, ka)
        if kb == "box12":
            val_b = f"D  {plain(v['box12_d'])}" if v["box12_d"] else None
        else:
            val_b = plain(v[kb]) if kb else None
        box(rx + rw / 2, y, rw / 2, row_h, lb, val_b, kb if kb != "box12" else None)
        y -= row_h
    box(rx, y, rw / 2, row_h * 1.4, "13  Statutory  Retirement  Sick pay", "X  Retirement plan" if v["box12_d"] else None)
    box(rx + rw / 2, y, rw / 2, row_h * 1.4, "14  Other", None)
    y = min(y - row_h * 1.4, left_bottom)

    # state / local strip
    cols = [("15  State   Employer's state ID number", 0.30, f"{v['state']}   {v['state_id']}", "state"),
            ("16  State wages, tips, etc.", 0.18, plain(v["state_wages"]), "state_wages"),
            ("17  State income tax", 0.16, plain(v["state_tax_withheld"]), "state_tax_withheld"),
            ("18  Local wages, tips, etc.", 0.14, None, None), ("19  Local income tax", 0.12, None, None),
            ("20  Locality name", 0.10, None, None)]
    cx = x0
    for label, frac, val, key in cols:
        box(cx, y, w * frac, row_h, label, val, key, 8.5)
        cx += w * frac
    p.text(x0, y - row_h - 14, "Copy B - To Be Filed With Employee's FEDERAL Tax Return.", bold, 7)
    _footer(p, reg, 1, 1)
    return p


# ----------------------------------------------------------------------- invoice
def render_invoice(c: canvas.Canvas, v: dict, rng: random.Random, family: int) -> Page:
    p = Page(c)
    reg, bold = rng.choice(FONTS)
    accent = HexColor(rng.choice(ACCENTS))
    m = 40
    labels = [("Invoice #", "Invoice date", "Due date", "Terms"), ("Invoice No.", "Date", "Due", "Payment terms"),
              ("Ref. no.", "Issued", "Payment due", "Terms")][family]

    def header():
        if family == 0:   # logo block left, INVOICE right
            c.setFillColor(accent)
            c.circle(m + 14, H - 58, 14, fill=1, stroke=0)
            p.text(m + 36, H - 54, v["vendor_name"], bold, 15, key="vendor_name")
            for i, line in enumerate(v["vendor_address"].split("\n")):
                p.text(m + 36, H - 68 - 10 * i, line, reg, 8.5, HexColor("#444444"))
            p.text(W - m, H - 56, "INVOICE", bold, 22, accent, "right")
        elif family == 1:  # full-width colour band
            c.setFillColor(accent)
            c.rect(0, H - 92, W, 92, fill=1, stroke=0)
            p.text(m, H - 50, v["vendor_name"], bold, 17, white, key="vendor_name")
            p.text(m, H - 66, v["vendor_address"].replace("\n", "  |  "), reg, 8, white)
            p.text(W - m, H - 52, "Tax Invoice", bold, 18, white, "right")
        else:              # held-out: centred letterhead, rule, metadata grid
            p.text(W / 2, H - 50, v["vendor_name"].upper(), bold, 16, black, "center", key="vendor_name")
            p.text(W / 2, H - 64, v["vendor_address"].replace("\n", ", "), reg, 8, HexColor("#444444"), "center")
            c.setStrokeColor(accent)
            c.setLineWidth(2)
            c.line(m, H - 74, W - m, H - 74)
            p.text(m, H - 96, "STATEMENT OF CHARGES", bold, 12, accent)

    header()
    meta_x = W - m - 190 if family != 2 else m
    meta_y = H - 120
    meta = [(labels[0], v["invoice_number"], "invoice_number"), (labels[1], v["invoice_date"], "invoice_date"),
            (labels[2], v["due_date"], "due_date"), (labels[3], v["terms"], None)]
    for i, (lb, val, key) in enumerate(meta):
        if family == 2:
            x = m + i * 130
            p.text(x, meta_y, lb, bold, 8, HexColor("#555555"))
            p.text(x, meta_y - 13, val, reg, 9.5, key=key)
        else:
            p.text(meta_x, meta_y - 14 * i, lb, bold, 8.5, HexColor("#555555"))
            p.text(W - m, meta_y - 14 * i, val, reg, 9.5, align="right", key=key)

    by = H - 200
    bill_lbl, ship_lbl = [("BILL TO", "SHIP TO"), ("Billed to", "Deliver to"), ("Sold to", "Ship to")][family]
    for j, (lbl, name, addr, key) in enumerate([(bill_lbl, v["bill_to"], v["bill_to_address"], "bill_to"),
                                                (ship_lbl, v["ship_to"], v["ship_to_address"], None)]):
        x = m + j * 220
        p.text(x, by, lbl, bold, 8, accent)
        p.text(x, by - 14, name, reg, 10, key=key)
        for i, line in enumerate(addr.split("\n")):
            p.text(x, by - 26 - 10 * i, line, reg, 8.5, HexColor("#444444"))

    cols = [("Description", m + 6, "left"), ("Qty", 360, "right"), ("Rate", 450, "right"), ("Amount", W - m - 6, "right")]
    if family == 1:
        cols = [("Item", m + 6, "left"), ("Hours/Qty", 350, "right"), ("Unit price", 450, "right"), ("Line total", W - m - 6, "right")]
    total_pages = 1 + (len(v["line_items"]) > 14)

    def table_head(y):
        if family != 2:
            c.setFillColor(accent if family == 0 else HexColor("#eeeeee"))
            c.rect(m, y - 5, W - 2 * m, 18, fill=1, stroke=0)
        hc = white if family == 0 else black
        for name, x, al in cols:
            p.text(x, y, name, bold, 8.5, hc, al)
        return y - 20

    y = table_head(H - 300)
    for i, it in enumerate(v["line_items"]):
        if y < 120:
            _footer(p, reg, p.page + 1, total_pages)
            p.new_page()
            y = table_head(H - 70)
        if family == 0 and i % 2:
            c.setFillColor(HexColor("#f4f4f4"))
            c.rect(m, y - 4, W - 2 * m, 15, fill=1, stroke=0)
        p.text(cols[0][1], y, it["description"], reg, 8.5, row=("line_items", i, "description"))
        p.text(cols[1][1], y, f"{it['quantity']:g}", reg, 8.5, align="right", row=("line_items", i, "quantity"))
        p.text(cols[2][1], y, plain(it["unit_price"]), reg, 8.5, align="right", row=("line_items", i, "unit_price"))
        p.text(cols[3][1], y, plain(it["amount"]), reg, 8.5, align="right", row=("line_items", i, "amount"))
        if family == 2:
            c.setStrokeColor(HexColor("#cccccc"))
            c.setLineWidth(0.4)
            c.line(m, y - 5, W - m, y - 5)
        y -= 16

    y -= 10
    totals = [("Subtotal", plain(v["subtotal"]), "subtotal")]
    if v["discount"]:
        totals.append((f"Discount ({v['discount_pct']:g}%)", f"-{plain(v['discount'])}", "discount"))
    totals.append((f"Tax ({v['tax_rate']:g}%)" if family != 1 else f"Sales tax @ {v['tax_rate']:g}%", plain(v["tax"]), "tax"))
    if v["shipping"]:
        totals.append(("Shipping", plain(v["shipping"]), "shipping"))
    totals.append(("TOTAL DUE" if family != 2 else "Balance due", money(v["total"]), "total"))
    for i, (lb, val, key) in enumerate(totals):
        last = i == len(totals) - 1
        p.text(400, y - 15 * i, lb, bold if last else reg, 10 if last else 9)
        p.text(W - m - 6, y - 15 * i, val, bold if last else reg, 10 if last else 9, align="right", key=key)
    ny = y - 15 * len(totals) - 20
    p.text(m, ny, "Payment instructions", bold, 8.5)
    p.text(m, ny - 12, f"Bank transfer to account ending {v['remit_last4']}. Please quote {v['invoice_number']}.", reg, 8)
    p.text(m, ny - 24, "Thank you for your business.", reg, 8, HexColor("#555555"))
    _footer(p, reg, p.page + 1, total_pages)
    return p


# ---------------------------------------------------------------------- pay stub
def render_paystub(c: canvas.Canvas, v: dict, rng: random.Random, family: int) -> Page:
    p = Page(c)
    reg, bold = rng.choice(FONTS[:2])
    accent = HexColor(rng.choice(ACCENTS))
    m = 40
    title = ["EARNINGS STATEMENT", "Payroll Advice"][family]
    if family == 0:
        p.text(m, H - 50, v["employer_name"], bold, 14, key="employer_name")
        p.text(m, H - 63, v["employer_address"].replace("\n", ", "), reg, 8, HexColor("#444444"))
        p.text(W - m, H - 50, title, bold, 13, accent, "right")
    else:  # held-out: boxed header, two-column info grid
        c.setStrokeColor(accent)
        c.setLineWidth(1.5)
        c.rect(m, H - 80, W - 2 * m, 45)
        p.text(m + 10, H - 55, title, bold, 13, accent)
        p.text(W - m - 10, H - 52, v["employer_name"], bold, 11, align="right", key="employer_name")
        p.text(W - m - 10, H - 66, v["employer_address"].replace("\n", ", "), reg, 7.5, HexColor("#444444"), "right")

    info = [("Employee", v["employee_name"], "employee_name"), ("Employee ID", v["employee_id"], None),
            ("Pay period", v["pay_period"], None), ("Pay date", v["pay_date"], "pay_date"),
            ("Pay frequency", v["pay_frequency"].capitalize(), "pay_frequency"), ("Check no.", v["check_no"], None)]
    for i, (lb, val, key) in enumerate(info):
        col, row = (i % 2, i // 2) if family == 0 else (i // 3, i % 3)
        x, y = m + col * 270, H - 100 - row * 16
        p.text(x, y, f"{lb}:", bold, 8.5)
        p.text(x + 85, y, val, reg, 9, key=key)

    def table(y, title, head, rows, total_label, total_cur, total_ytd, kcur, kytd):
        c.setFillColor(HexColor("#eef0f3"))
        c.rect(m, y - 5, W - 2 * m, 16, fill=1, stroke=0)
        xs = [m + 6, 260, 330, 430, W - m - 6]
        for j, h in enumerate([title, *head]):
            p.text(xs[j], y, h, bold, 8.5, black, "left" if j == 0 else "right")
        y -= 17
        for r in rows:
            for j, val in enumerate(r):
                p.text(xs[j], y, val, reg, 8.5, black, "left" if j == 0 else "right")
            y -= 13
        c.setStrokeColor(HexColor("#999999"))
        c.line(m, y + 8, W - m, y + 8)
        p.text(xs[0], y - 3, total_label, bold, 9)
        p.text(xs[3], y - 3, plain(total_cur), bold, 9, align="right", key=kcur)
        p.text(xs[4], y - 3, plain(total_ytd), bold, 9, align="right", key=kytd)
        return y - 30

    y = H - 165
    earn_rows = [[e["name"], f"{e['hours']:.2f}" if e["hours"] else "", plain(e["rate"]) if e["rate"] else "",
                  plain(e["current"]), plain(e["ytd"])] for e in v["earnings"]]
    y = table(y, "Earnings", ["Hours", "Rate", "Current", "Year to date"], earn_rows, "Gross pay",
              v["gross_pay"], v["ytd_gross"], "gross_pay", "ytd_gross")
    ded_rows = [[d["name"], "", "", plain(d["current"]), plain(d["ytd"])] for d in v["deductions"]]
    y = table(y, "Deductions", ["", "", "Current", "Year to date"], ded_rows, "Total deductions",
              v["total_deductions"], v["ytd_deductions"], "total_deductions", None)
    c.setFillColor(accent)
    c.rect(330, y - 6, W - m - 330, 22, fill=1, stroke=0)
    p.text(340, y + 1, "NET PAY", bold, 11, white)
    p.text(W - m - 8, y + 1, money(v["net_pay"]), bold, 12, white, "right", key="net_pay")
    p.text(m, y + 1, f"Direct deposit to account ending {v['deposit_last4']}", reg, 8, HexColor("#444444"))
    _footer(p, reg, 1, 1)
    return p


# ------------------------------------------------------------- bank statement
def render_bank(c: canvas.Canvas, v: dict, rng: random.Random, family: int) -> Page:
    p = Page(c)
    reg, bold = rng.choice(FONTS[:2])
    accent = HexColor(rng.choice(ACCENTS))
    m = 40
    per_page_first, per_page = 18, 34
    n = len(v["transactions"])
    total_pages = 1 + max(0, -(-(n - per_page_first) // per_page))

    if family == 0:
        c.setFillColor(accent)
        c.rect(m, H - 64, 28, 28, fill=1, stroke=0)
        p.text(m + 36, H - 52, v["bank_name"], bold, 15, key="bank_name")
        p.text(W - m, H - 50, "Account Statement", bold, 11, accent, "right")
    else:  # held-out: centred name, rule, summary on the left
        p.text(W / 2, H - 50, v["bank_name"], bold, 16, accent, "center", key="bank_name")
        c.setStrokeColor(accent)
        c.line(m, H - 60, W - m, H - 60)
    p.text(m, H - 95, v["account_holder"], bold, 10, key="account_holder")
    for i, line in enumerate(v["holder_address"].split("\n")):
        p.text(m, H - 108 - 11 * i, line, reg, 9)
    ax = W - m - 200 if family == 0 else m + 300
    p.text(ax, H - 95, "Account number", bold, 8.5)
    p.text(ax, H - 107, f"XXXX XXXX {v['account_last4']}", reg, 9.5, key="account_last4")
    p.text(ax, H - 122, "Statement period", bold, 8.5)
    p.text(ax, H - 134, v["statement_period"], reg, 9.5, key="statement_period")

    sy = H - 175
    c.setStrokeColor(HexColor("#bbbbbb"))
    c.setFillColor(HexColor("#f6f7f9"))
    c.rect(m, sy - 62, W - 2 * m, 74, fill=1, stroke=1)
    p.text(m + 10, sy, "Account summary", bold, 9.5, accent)
    summ = [("Beginning balance", money(v["opening_balance"]), "opening_balance"),
            ("Deposits and other credits", f"+{money(v['total_deposits'])}", "total_deposits"),
            ("Withdrawals and other debits", f"-{money(v['total_withdrawals'])}", "total_withdrawals"),
            ("Ending balance", money(v["closing_balance"]), "closing_balance")]
    for i, (lb, val, key) in enumerate(summ):
        p.text(m + 10, sy - 14 - 12 * i, lb, reg, 8.5)
        p.text(m + 300, sy - 14 - 12 * i, val, bold if i == 3 else reg, 9, align="right", key=key)

    xs = [m + 4, m + 70, 430, W - m - 4]

    def head(y):
        c.setFillColor(HexColor("#e6e9ee"))
        c.rect(m, y - 4, W - 2 * m, 15, fill=1, stroke=0)
        for j, h in enumerate(["Date", "Description", "Amount", "Balance"]):
            p.text(xs[j], y, h, bold, 8.5, black, "left" if j < 2 else "right")
        return y - 15

    y = head(sy - 85)
    on_page = 0
    for i, t in enumerate(v["transactions"]):
        limit = per_page_first if p.page == 0 else per_page
        if on_page >= limit:
            _footer(p, reg, p.page + 1, total_pages)
            p.new_page()
            p.text(m, H - 40, f"{v['bank_name']}  -  account ending {v['account_last4']}  (continued)", reg, 8)
            y, on_page = head(H - 65), 0
        if i % 2:
            c.setFillColor(HexColor("#fafafa"))
            c.rect(m, y - 3, W - 2 * m, 13, fill=1, stroke=0)
        p.text(xs[0], y, t["date"], reg, 8.5, row=("transactions", i, "date"))
        p.text(xs[1], y, t["description"], reg, 8.5, row=("transactions", i, "description"))
        p.text(xs[2], y, money(t["amount"], sign=True), reg, 8.5, align="right", row=("transactions", i, "amount"))
        p.text(xs[3], y, plain(t["balance"]), reg, 8.5, align="right")
        y -= 13
        on_page += 1
    _footer(p, reg, p.page + 1, total_pages)
    return p


RENDERERS = {"w2": render_w2, "invoice": render_invoice, "paystub": render_paystub, "bank_statement": render_bank}


def render_v2(doc_type: str, values: dict, path: str, rng: random.Random, family: int) -> Page:
    c = canvas.Canvas(path, pagesize=letter, invariant=1)
    page = RENDERERS[doc_type](c, values, rng, family)
    c.save()
    return page


"""International invoice benchmark: India (GST), EU (VAT), UK (VAT) and US (sales tax).

Each locale prints numbers, dates, tax IDs and taxes its own way, which is what
"any invoice" has to survive:

- India: GSTIN for both parties, HSN/SAC codes, CGST + SGST within a state or IGST
  across states, lakh grouping (1,23,456.00), dd/mm/yyyy, "Rs."/"INR".
- EU: VAT IDs (DE/FR/NL/ES/IT), one or two VAT rates, comma decimals
  (1.234,56 or 1 234,56), dd.mm.yyyy or dd/mm/yyyy, EUR.
- UK: GB VAT number, 20% VAT, 1,234.56, dd/mm/yyyy, GBP.
- US: optional EIN, state sales tax, mm/dd/yyyy, $.

Ground truth covers every invoice field, including currency, tax IDs, purchase
order and tax lines. Synthetic companies only (Faker); no real businesses.

    python -m docintel.synth.intl_invoices --out data/synthetic_intl --n 60
"""
from __future__ import annotations

import argparse
import json
import random
from datetime import timedelta
from pathlib import Path

from faker import Faker
from reportlab.lib.colors import HexColor, black
from reportlab.pdfgen import canvas

from .generator_v2 import r2, rasterise

W, H = 595.0, 842.0  # A4 points
GOODS = ["Printer paper A4 (500 sheets)", "Toner cartridge", "Office chair, ergonomic", "Laptop 14in",
         "Network switch 24-port", "Monitor 27in", "Desk lamp LED", "Annual software licence", "Cloud hosting (monthly)",
         "Consulting services", "Implementation support", "Design review", "Data migration", "Training workshop",
         "Spare parts kit", "Maintenance visit", "Courier charges", "Marketing campaign setup"]
HSN = {"Printer paper A4 (500 sheets)": "4802", "Toner cartridge": "8443", "Office chair, ergonomic": "9401",
       "Laptop 14in": "8471", "Network switch 24-port": "8517", "Monitor 27in": "8528", "Desk lamp LED": "9405"}
INDIAN_STATES = [("27", "Maharashtra"), ("29", "Karnataka"), ("07", "Delhi"), ("33", "Tamil Nadu"), ("24", "Gujarat")]
EU = {"DE": ("de_DE", [19, 7], "dot"), "FR": ("fr_FR", [20, 10], "space"), "NL": ("nl_NL", [21, 9], "dot"),
      "ES": ("es_ES", [21, 10], "dot"), "IT": ("it_IT", [22, 10], "dot")}
GT = ["vendor_name", "invoice_number", "invoice_date", "due_date", "bill_to", "subtotal", "discount", "tax", "shipping",
      "total", "line_items", "currency", "vendor_tax_id", "buyer_tax_id", "purchase_order", "tax_lines"]


def money(x: float, style: str) -> str:
    """Format an amount the way the locale prints it."""
    neg, x = x < 0, abs(x)
    whole, frac = f"{x:.2f}".split(".")
    if style == "indian":
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + groups + [tail]) if head or groups else tail
        out = f"{whole}.{frac}"
    elif style in ("dot", "space"):
        sep = "." if style == "dot" else " "
        out = f"{int(whole):,}".replace(",", sep) + f",{frac}"
    else:
        out = f"{int(whole):,}.{frac}"
    return f"-{out}" if neg else out


def gstin(fk: Faker, state: str) -> str:
    return f"{state}{fk.lexify('?????').upper()}{fk.numerify('####')}{fk.lexify('?').upper()}1Z{fk.numerify('#')}"


def make_invoice(doc_id: str, locale: str, rng: random.Random) -> dict:
    fk = Faker({"IN": "en_IN", "UK": "en_GB", "US": "en_US"}.get(locale, EU.get(locale, ("en_US",))[0]))
    fk.seed_instance(rng.randrange(1 << 30))
    n = rng.choice([2, 3, 4, 5, 6, 8, 10, 12])
    items = []
    for _ in range(n):
        desc = rng.choice(GOODS)
        qty = rng.choice([1, 1, 2, 3, 5, 10, 12, 25])
        price = r2(rng.choice([rng.uniform(4, 90), rng.uniform(90, 1800)]) * (80 if locale == "IN" else 1))
        items.append({"description": desc, "quantity": float(qty), "unit_price": price, "amount": r2(qty * price)})
    sub = r2(sum(i["amount"] for i in items))
    disc = r2(sub * rng.choice([0, 0, 0, 0.05, 0.1]))
    ship = rng.choice([0.0, 0.0, 25.0, 60.0]) * (80 if locale == "IN" else 1)
    base = r2(sub - disc + ship)
    issued = fk.date_between(start_date="-18m", end_date="today")
    due = issued + timedelta(days=rng.choice([0, 15, 30, 45]))
    v = {"vendor_name": fk.company(), "bill_to": fk.company(), "invoice_date_d": issued, "due_date_d": due,
         "line_items": items, "subtotal": sub, "discount": disc or None, "shipping": ship or None,
         "purchase_order": fk.bothify("PO-#####") if rng.random() < 0.6 else None,
         "vendor_address": fk.address().replace("\n", ", "), "buyer_address": fk.address().replace("\n", ", "),
         "locale": locale}
    if locale == "IN":
        (vs, _), (bs, bname) = rng.choice(INDIAN_STATES), rng.choice(INDIAN_STATES)
        rate = rng.choice([5, 12, 18, 28])
        v |= {"currency": "INR", "style": "indian", "date_fmt": "%d/%m/%Y", "vendor_tax_id": gstin(fk, vs),
              "buyer_tax_id": gstin(fk, bs), "invoice_number": fk.bothify("GST/##-##/####"),
              "place": f"{bname} ({bs})", "sym": rng.choice(["Rs.", "INR"])}
        if vs == bs:
            half = r2(base * rate / 200)
            v["tax_lines"] = [{"tax_type": "CGST", "rate": rate / 2, "taxable_amount": base, "amount": half},
                              {"tax_type": "SGST", "rate": rate / 2, "taxable_amount": base, "amount": half}]
        else:
            v["tax_lines"] = [{"tax_type": "IGST", "rate": float(rate), "taxable_amount": base,
                               "amount": r2(base * rate / 100)}]
    elif locale == "UK":
        v |= {"currency": "GBP", "style": "plain", "date_fmt": "%d/%m/%Y", "sym": "£",
              "vendor_tax_id": f"GB{fk.numerify('#########')}", "buyer_tax_id": None,
              "invoice_number": fk.bothify("INV-######"),
              "tax_lines": [{"tax_type": "VAT", "rate": 20.0, "taxable_amount": base, "amount": r2(base * 0.2)}]}
    elif locale == "US":
        rate = rng.choice([6.0, 6.25, 7.25, 8.25, 8.875])
        v |= {"currency": "USD", "style": "plain", "date_fmt": "%m/%d/%Y", "sym": "$",
              "vendor_tax_id": f"EIN {fk.numerify('##-#######')}" if rng.random() < 0.4 else None, "buyer_tax_id": None,
              "invoice_number": fk.bothify("INV-#####"),
              "tax_lines": [{"tax_type": "Sales tax", "rate": rate, "taxable_amount": base,
                             "amount": r2(base * rate / 100)}]}
    else:
        _, rates, style = EU[locale]
        v |= {"currency": "EUR", "style": style, "date_fmt": rng.choice(["%d.%m.%Y", "%d/%m/%Y"]), "sym": "€",
              "vendor_tax_id": f"{locale}{fk.numerify('#########')}", "buyer_tax_id": f"{locale}{fk.numerify('#########')}",
              "invoice_number": fk.bothify("RE-####-###")}
        if rng.random() < 0.3 and len(items) > 1:  # two VAT rates on one invoice
            low = r2(items[0]["amount"])
            high = r2(base - low)
            v["tax_lines"] = [{"tax_type": "VAT", "rate": float(rates[0]), "taxable_amount": high,
                               "amount": r2(high * rates[0] / 100)},
                              {"tax_type": "VAT", "rate": float(rates[1]), "taxable_amount": low,
                               "amount": r2(low * rates[1] / 100)}]
        else:
            v["tax_lines"] = [{"tax_type": "VAT", "rate": float(rates[0]), "taxable_amount": base,
                               "amount": r2(base * rates[0] / 100)}]
    v["tax"] = r2(sum(t["amount"] for t in v["tax_lines"]))
    v["total"] = r2(base + v["tax"])
    v["invoice_date"] = issued.strftime(v["date_fmt"])
    v["due_date"] = due.strftime(v["date_fmt"]) if due != issued else None
    return v


def render(v: dict, path: Path, rng: random.Random) -> None:
    c = canvas.Canvas(str(path), pagesize=(W, H))
    reg, bold = rng.choice([("Helvetica", "Helvetica-Bold"), ("Times-Roman", "Times-Bold")])
    accent = HexColor(rng.choice(["#1f4e79", "#7a1f1f", "#1f5c3a", "#333333"]))
    m, st, sym = 40, v["style"], v["sym"]

    def t(x, y, s, font=reg, size=9, color=black, align="left"):
        c.setFont(font, size)
        c.setFillColor(color)
        {"left": c.drawString, "right": c.drawRightString, "center": c.drawCentredString}[align](x, y, str(s))

    def amt(x: float) -> str:
        if v["locale"] in ("IN",):
            return money(x, st)
        return f"{money(x, st)} {sym}" if v["locale"] in EU else f"{sym}{money(x, st)}"

    loc = v["locale"]
    title = {"IN": "TAX INVOICE", "UK": "VAT INVOICE", "US": "INVOICE"}.get(loc, rng.choice(["RECHNUNG / INVOICE",
                                                                                              "FACTURE / INVOICE",
                                                                                              "INVOICE"]))
    t(m, H - 50, v["vendor_name"], bold, 15, accent)
    t(m, H - 64, v["vendor_address"][:95], size=8)
    tax_label = {"IN": "GSTIN", "US": ""}.get(loc, "VAT No.")
    if v["vendor_tax_id"]:
        t(m, H - 76, f"{tax_label + ': ' if tax_label else ''}{v['vendor_tax_id']}", size=8.5)
    t(W - m, H - 52, title, bold, 16, accent, "right")

    meta = [("Invoice No.", v["invoice_number"]), ("Invoice Date", v["invoice_date"])]
    if v["due_date"]:
        meta.append(("Due Date", v["due_date"]))
    if v["purchase_order"]:
        meta.append(("PO Number", v["purchase_order"]))
    if loc == "IN":
        meta.append(("Place of Supply", v["place"]))
    for i, (lb, val) in enumerate(meta):
        t(W - m - 200, H - 100 - 13 * i, lb, bold, 8.5)
        t(W - m, H - 100 - 13 * i, val, size=9, align="right")

    t(m, H - 110, "Bill To", bold, 8.5, accent)
    t(m, H - 124, v["bill_to"], bold, 10)
    t(m, H - 136, v["buyer_address"][:70], size=8)
    if v["buyer_tax_id"]:
        t(m, H - 148, f"{tax_label}: {v['buyer_tax_id']}", size=8.5)

    y = H - 200
    hsn = loc == "IN"
    cols = [("#", m + 4, "left"), ("Description", m + 24, "left")]
    if hsn:
        cols.append(("HSN/SAC", 300, "left"))
    cols += [("Qty", 380, "right"), ("Rate", 460, "right"), ("Amount", W - m - 4, "right")]
    c.setFillColor(HexColor("#eeeeee"))
    c.rect(m, y - 4, W - 2 * m, 16, fill=1, stroke=0)
    for name, x, al in cols:
        t(x, y, name, bold, 8.5, align=al)
    y -= 18
    for k, it in enumerate(v["line_items"], 1):
        t(m + 4, y, k, size=8.5)
        t(m + 24, y, it["description"], size=8.5)
        if hsn:
            t(300, y, HSN.get(it["description"], "998313"), size=8.5)
        t(380, y, f"{it['quantity']:g}", size=8.5, align="right")
        t(460, y, money(it["unit_price"], st), size=8.5, align="right")
        t(W - m - 4, y, money(it["amount"], st), size=8.5, align="right")
        y -= 14
    c.setStrokeColor(HexColor("#999999"))
    c.line(m, y + 6, W - m, y + 6)

    y -= 10
    lab = {"IN": "Taxable", "US": "Subtotal"}.get(loc, "Net amount")
    rows = [("Subtotal" if loc != "IN" else "Total before tax", v["subtotal"])]
    if v["discount"]:
        rows.append(("Discount", -v["discount"]))
    if v["shipping"]:
        rows.append(("Freight" if loc == "IN" else "Shipping", v["shipping"]))
    if v["discount"] or v["shipping"]:
        rows.append((f"{lab} value", r2(v["subtotal"] - (v["discount"] or 0) + (v["shipping"] or 0))))
    for tl in v["tax_lines"]:
        rows.append((f"{tl['tax_type']} @ {tl['rate']:g}%", tl["amount"]))
    if len(v["tax_lines"]) > 1:
        rows.append(("Total tax", v["tax"]))
    for lb, val in rows:
        t(W - m - 200, y, lb, size=9)
        t(W - m - 4, y, money(val, st), size=9, align="right")
        y -= 13
    t(W - m - 200, y - 4, f"Grand Total ({v['currency']})" if loc in ("IN",) or loc in EU else "Total Due",
      bold, 10.5)
    t(W - m - 4, y - 4, amt(v["total"]), bold, 10.5, align="right")
    t(m, 60, "Thank you for your business. Synthetic document for testing; not a real invoice.", size=7,
      color=HexColor("#777777"))
    c.showPage()
    c.save()


def ground_truth(doc_id: str, v: dict, modality: str, file: str) -> dict:
    fields = {}
    for k in GT:
        val = v.get(k)
        if k == "line_items":
            val = [{c: r[c] for c in ("description", "quantity", "unit_price", "amount")} for r in val]
        fields[k] = {"value": val, "bbox": None}
    return {"doc_id": doc_id, "doc_type": "invoice", "case_id": None, "file": file, "benchmark": "intl",
            "locale": v["locale"], "pages": 1, "modality": modality, "fields": fields}


def generate(out: Path, n: int, scan_rate: float, seed: int) -> dict:
    rng = random.Random(seed)
    docs = out / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    locales = ["IN", "IN", "UK", "US", "DE", "FR", "NL", "ES", "IT"]
    ids = []
    for i in range(n):
        loc = locales[i % len(locales)]
        doc_id = f"intl{i:04d}-{loc.lower()}"
        v = make_invoice(doc_id, loc, rng)
        pdf = docs / f"{doc_id}.pdf"
        render(v, pdf, rng)
        scan = rng.random() < scan_rate
        if scan:
            rasterise(pdf, rng)
        (docs / f"{doc_id}.json").write_text(json.dumps(ground_truth(doc_id, v, "scan" if scan else "native",
                                                                    pdf.name), indent=1, default=str))
        ids.append(doc_id)
    splits = {"seed": seed, "splits": {"train": [], "val": [], "test": ids}}  # evaluation only
    (out / "splits.json").write_text(json.dumps(splits, indent=1))
    return {"documents": len(ids), "by_locale": {loc: sum(i.endswith(loc.lower()) for i in ids) for loc in set(locales)}}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/synthetic_intl")
    ap.add_argument("--n", type=int, default=45)
    ap.add_argument("--scan-rate", type=float, default=0.3)
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    print(generate(Path(a.out), a.n, a.scan_rate, a.seed))

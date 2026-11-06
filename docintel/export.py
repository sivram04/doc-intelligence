"""CSV export of a batch: one row per document, one file per document type.

- invoices.csv, w2.csv, paystubs.csv, bank_statements.csv: header fields, one row per document
- line_items.csv, transactions.csv: table rows, linked back to their document by `file`
- summary.csv: every uploaded file with its type, status, review state and error

Values are the reviewer's final values where a document was reviewed, the model's
otherwise; `review_status` says which. Amounts are plain numbers so spreadsheets
can sum them.
"""
from __future__ import annotations

import csv
import io
import zipfile

from .schemas import TABLE_COLUMNS, DocType, field_types

FILES = {DocType.INVOICE: "invoices", DocType.W2: "w2", DocType.PAYSTUB: "paystubs",
         DocType.BANK_STATEMENT: "bank_statements"}
# Table rows carry one identifying header field so they can be joined without the file name.
TABLE_KEY = {"line_items": "invoice_number", "transactions": "account_last4"}
FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(v: object) -> object:
    """Plain value for a CSV cell; text that a spreadsheet would run as a formula is quoted."""
    if v is None:
        return ""
    if isinstance(v, float):
        return round(v, 2)
    if isinstance(v, str) and v.startswith(FORMULA_START):
        return "'" + v
    return v


def review_status(job: dict) -> str:
    if job["status"] != "done":
        return job["status"]
    if job.get("reviewed"):
        return "reviewed"
    return "needs_review" if (job.get("routing") or {}).get("review") else "auto_approved"


def values(job: dict) -> dict[str, object]:
    """Field name -> final value (reviewer's where present)."""
    fields = {k: f.get("value") for k, f in job["result"]["fields"].items()}
    return fields | (job.get("final") or {})


def tables(jobs: list[dict]) -> dict[str, tuple[list[str], list[list]]]:
    """CSV name -> (header, rows)."""
    out: dict[str, tuple[list[str], list[list]]] = {}
    summary = []
    for j in jobs:
        r = j.get("result") or {}
        summary.append([j["filename"], r.get("doc_type", ""), j["status"], review_status(j),
                        len((j.get("routing") or {}).get("review", [])), "; ".join(r.get("flags", [])),
                        j.get("error") or ""])
        if j["status"] != "done" or r.get("doc_type") not in {d.value for d in FILES}:
            continue
        dt = DocType(r["doc_type"])
        types = field_types(dt)
        scalars = [n for n, t in types.items() if t is not list]
        v = values(j)
        name = FILES[dt]
        out.setdefault(name, (["file", "review_status", *scalars, "flags"], []))[1].append(
            [j["filename"], review_status(j), *(_cell(v.get(n)) for n in scalars), "; ".join(r.get("flags", []))])
        for table in (n for n, t in types.items() if t is list):
            cols = list(TABLE_COLUMNS[table])
            key = TABLE_KEY[table]
            rows = out.setdefault(table, (["file", key, "row", *cols], []))[1]
            for i, row in enumerate(v.get(table) or [], 1):
                if isinstance(row, dict):
                    rows.append([j["filename"], _cell(v.get(key)), i, *(_cell(row.get(c)) for c in cols)])
    out["summary"] = (["file", "doc_type", "status", "review_status", "fields_to_review", "flags", "error"], summary)
    return out


def to_csv(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows([_cell(c) for c in row] for row in rows)
    return buf.getvalue().encode("utf-8-sig")  # BOM: Excel opens UTF-8 correctly


def to_zip(jobs: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, (header, rows) in tables(jobs).items():
            z.writestr(f"{name}.csv", to_csv(header, rows))
    return buf.getvalue()

"""CSV export of a batch: one row per document, one file per document type.

- invoices.csv, w2.csv, paystubs.csv, bank_statements.csv: header fields, one row per document
- line_items.csv, tax_lines.csv, transactions.csv: table rows, linked back to their document by `file`
- summary.csv: every uploaded file with its type, status, review state and error
- journal_entries.csv: one balanced journal entry per invoice (docintel.accounting), plus
  quickbooks_journal.csv and xero_manual_journal.csv in those products' import formats

Values are the reviewer's final values where a document was reviewed, the model's
otherwise; `review_status` says which. Amounts are plain numbers so spreadsheets
can sum them.
"""
from __future__ import annotations

import csv
import io
import zipfile

from .accounting import AccountMemory, JournalEntry, build_entry, load_coa
from .schemas import TABLE_COLUMNS, DocType, field_types

FILES = {DocType.INVOICE: "invoices", DocType.W2: "w2", DocType.PAYSTUB: "paystubs",
         DocType.BANK_STATEMENT: "bank_statements"}
# Table rows carry one identifying header field so they can be joined without the file name.
TABLE_KEY = {"line_items": "invoice_number", "transactions": "account_last4", "tax_lines": "invoice_number"}
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
    out |= journal_tables(journal(jobs))
    return out


def source_text(job: dict) -> str:
    """The OCR text the fields were read from (currency symbols, tax labels)."""
    return " ".join(str(f.get("source_text") or "") for f in job["result"]["fields"].values())


def journal(jobs: list[dict]) -> list[JournalEntry]:
    """One journal entry per processed invoice, in upload order."""
    memory, coa = AccountMemory(), load_coa()
    entries = []
    for j in jobs:
        r = j.get("result") or {}
        if j["status"] != "done" or r.get("doc_type") != DocType.INVOICE.value:
            continue
        e = build_entry(values(j), j["filename"], f"JE-{len(entries) + 1:04d}", source_text(j), coa, memory)
        e.job_id = j["job_id"]
        if review_status(j) == "needs_review":
            e.issues.insert(0, "Extracted fields not yet reviewed")
        entries.append(e)
    return entries


def journal_tables(entries: list[JournalEntry]) -> dict[str, tuple[list[str], list[list]]]:
    """Generic journal, plus QuickBooks Online and Xero manual-journal import files.
    Account names in the import files must exist in your accounting software (see COA_PATH)."""
    if not entries:
        return {}
    generic, qbo, xero = [], [], []
    for e in entries:
        note = "; ".join(e.issues)
        memo = f"{e.reference or e.file} {e.party or ''}".strip()
        us_date = ""
        if e.date:
            y, m, d = e.date.split("-")
            us_date = f"{m}/{d}/{y}"
        for line in e.lines:
            generic.append([e.entry_id, e.date or "", e.reference or "", e.party or "", e.kind, line.account_code,
                            line.account_name, line.debit or "", line.credit or "", e.currency or "", line.description,
                            line.basis, "yes" if e.needs_review else "no", note, e.file])
            qbo.append([e.entry_id, us_date, e.currency or "", memo, line.account_name, line.debit or "",
                        line.credit or "", line.description, e.party or ""])
            xero.append([memo, e.date or "", line.description, line.account_code, "Tax Exempt",
                         round(line.debit - line.credit, 2)])
    return {
        "journal_entries": (["entry_id", "date", "reference", "party", "kind", "account_code", "account_name", "debit",
                             "credit", "currency", "description", "account_basis", "needs_review", "issues", "file"],
                            generic),
        "quickbooks_journal": (["Journal No", "Journal Date", "Currency", "Memo", "Account", "Debits", "Credits",
                                "Description", "Name"], qbo),
        "xero_manual_journal": (["*Narration", "*Date", "Description", "*AccountCode", "*TaxRate", "*Amount"], xero),
    }


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

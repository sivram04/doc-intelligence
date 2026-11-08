"""Batch upload, CSV export, and public-demo isolation and limits."""
import csv
import importlib
import io
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from tests.test_core import case_dir  # noqa: F401 - shared fixture


def _app(tmp_path, monkeypatch, demo: bool):
    monkeypatch.setenv("DOCINTEL_DB", str(tmp_path / "b.db"))
    monkeypatch.setenv("CORRECTIONS_LOG", str(tmp_path / "corr.jsonl"))
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "up"))
    monkeypatch.setenv("THRESHOLDS", str(tmp_path / "none.json"))
    for k in ("DATABASE_URL", "REDIS_URL", "API_KEYS"):
        monkeypatch.delenv(k, raising=False)
    if demo:
        monkeypatch.setenv("DEMO_MODE", "1")
        monkeypatch.setenv("DEMO_MAX_DOCS", "2")
    else:
        monkeypatch.delenv("DEMO_MODE", raising=False)
    import docintel.store
    importlib.reload(docintel.store)
    import docintel.api.main as m
    return importlib.reload(m)


def _wait(c, batch_id):
    for _ in range(200):
        b = c.get(f"/batches/{batch_id}").json()
        if b["complete"]:
            return b
        time.sleep(0.1)
    raise AssertionError("batch did not finish")


def _files(case_dir, n=None):  # noqa: F811
    d, docs = case_dir
    return [("files", (f"{x.doc_id}.pdf", (d / f"{x.doc_id}.pdf").read_bytes(), "application/pdf"))
            for x in docs[:n]]


def test_batch_to_csv(tmp_path, monkeypatch, case_dir):  # noqa: F811
    m = _app(tmp_path, monkeypatch, demo=False)
    c = TestClient(m.app)
    files = _files(case_dir) + [("files", ("notes.txt", b"hello", "text/plain"))]
    r = c.post("/batches", files=files).json()
    assert len(r["accepted"]) == len(files) - 1 and r["refused"][0]["file"] == "notes.txt"
    b = _wait(c, r["batch_id"])
    assert b["counts"].get("done") == b["total"]

    z = zipfile.ZipFile(io.BytesIO(c.get(f"/batches/{r['batch_id']}/export.zip").content))
    names = set(z.namelist())
    assert {"invoices.csv", "line_items.csv", "summary.csv"} <= names
    inv = list(csv.DictReader(io.StringIO(z.read("invoices.csv").decode("utf-8-sig"))))
    assert len(inv) == 1 and inv[0]["total"] and inv[0]["review_status"] in ("auto_approved", "needs_review")

    # A reviewer's correction is what the CSV exports
    jid = next(d["job_id"] for d in b["documents"] if d["doc_type"] == "invoice")
    fields = {k: f["value"] for k, f in c.get(f"/jobs/{jid}").json()["result"]["fields"].items()}
    fields["total"] = 9999.5
    assert c.post(f"/jobs/{jid}/review", json={"fields": fields, "reviewer": "t", "seconds": 1}).status_code == 200
    one = c.get(f"/batches/{r['batch_id']}/export/invoices.csv").content.decode("utf-8-sig")
    row = next(csv.DictReader(io.StringIO(one)))
    assert row["total"] == "9999.5" and row["review_status"] == "reviewed"
    assert c.get(f"/batches/{r['batch_id']}/export/nope.csv").status_code == 404


def test_table_rows_export_with_reviewer_edits():
    from docintel.export import tables
    job = {"filename": "a.pdf", "status": "done", "reviewed": 1.0, "error": None,
           "routing": {"auto": [], "review": ["line_items"]},
           "result": {"doc_type": "invoice", "flags": [], "fields": {
               "invoice_number": {"value": "INV-7"}, "total": {"value": 30.0},
               "line_items": {"value": [{"description": "Bolts", "quantity": 2, "unit_price": 5, "amount": 10}]}}},
           "final": {"line_items": [{"description": "Bolts", "quantity": 2, "unit_price": 5, "amount": 10},
                                    {"description": "Labour", "quantity": 1, "unit_price": 20, "amount": 20}]}}
    out = tables([job])
    header, rows = out["line_items"]
    assert header[:3] == ["file", "invoice_number", "row"]
    assert [r[3] for r in rows] == ["Bolts", "Labour"] and rows[1][1] == "INV-7"
    assert out["invoices"][1][0][1] == "reviewed"


def test_csv_neutralises_formulas():
    from docintel.export import _cell
    assert _cell("=HYPERLINK(1)") == "'=HYPERLINK(1)" and _cell(-12.5) == -12.5 and _cell(None) == ""


@pytest.mark.parametrize("demo", [True])
def test_demo_isolates_visitors_and_caps_uploads(tmp_path, monkeypatch, case_dir, demo):  # noqa: F811
    m = _app(tmp_path, monkeypatch, demo=demo)
    alice, bob = TestClient(m.app), TestClient(m.app)
    assert alice.get("/config").json()["remaining"] == 2

    r = alice.post("/batches", files=_files(case_dir, 3)).json()
    assert len(r["accepted"]) == 2 and "own computer" in r["refused"][0]["error"]
    _wait(alice, r["batch_id"])
    assert alice.post("/batches", files=_files(case_dir, 1)).status_code == 429

    # Bob sees none of Alice's work. He shares Alice's IP here (one test client), so the
    # per-IP limit blocks him too: clearing cookies does not reset the allowance.
    assert bob.get(f"/batches/{r['batch_id']}").status_code == 404
    assert bob.get(f"/jobs/{r['accepted'][0]['job_id']}").status_code == 404
    assert bob.get("/review/queue").json() == [] and bob.get("/batches").json() == []
    assert bob.post("/batches", files=_files(case_dir, 1)).status_code == 429


def test_demo_report_limit_follows_network_not_just_cookie(tmp_path, monkeypatch):
    """Clearing cookies (a new browser id) must not reset the long-document demo limit."""
    import pymupdf
    from fastapi.testclient import TestClient

    import docintel.api.main as m
    monkeypatch.setattr(m, "DEMO", True)
    monkeypatch.setattr(m, "LONGDOC_DIR", tmp_path)
    monkeypatch.setattr(m, "_ld_process", lambda rid: None)
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Annual report page one with enough text to count as native.")
    pdf = doc.tobytes()
    codes = []
    for _ in range(m.DEMO_MAX_DOCS + 1):
        c = TestClient(m.app)  # fresh client = no cookie, same address
        codes.append(c.post("/reports", files={"file": ("r.pdf", pdf, "application/pdf")}).status_code)
    assert codes == [202] * m.DEMO_MAX_DOCS + [429]

"""Bad inputs and security: every upload either works or fails with a clear message, never a crash,
and one visitor can never see or touch another's documents."""
import io
import json
import time

import pymupdf
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from tests.test_batches import _app, _wait


def pdf_bytes(pages=1, text="Invoice INV-1001 Total 120.00", encrypt=False, meta=None) -> bytes:
    d = pymupdf.open()
    for _ in range(pages):
        d.new_page().insert_text((72, 72), text)
    if meta:
        d.set_metadata(meta)
    if encrypt:
        return d.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="secret")
    return d.tobytes()


def png_bytes(rotate=0) -> bytes:
    img = Image.new("RGB", (600, 400), "white")
    buf = io.BytesIO()
    img.rotate(rotate, expand=True).save(buf, "PNG")
    return buf.getvalue()


BAD = {
    "empty.pdf": b"",
    "renamed.pdf": b"MZ\x90\x00 this is really an exe",
    "text.pdf": b"just some text, not a pdf",
    "truncated.pdf": b"%PDF-1.7\n1 0 obj << /Type /Catalog >>\n",  # looks like a PDF, is not one
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    m = _app(tmp_path, monkeypatch, demo=False)
    monkeypatch.setattr(m, "LONGDOC_DIR", tmp_path / "ld")
    return m, TestClient(m.app)


def test_batch_refuses_non_documents_by_content(client):
    _, c = client
    r = c.post("/batches", files=[("files", (n, b, "application/pdf")) for n, b in BAD.items() if n != "truncated.pdf"]
               + [("files", ("ok.pdf", pdf_bytes(), "application/pdf"))])
    body = r.json()
    assert r.status_code == 202
    assert [a["file"] for a in body["accepted"]] == ["ok.pdf"]
    assert {x["file"] for x in body["refused"]} == {"empty.pdf", "renamed.pdf", "text.pdf"}


def test_broken_encrypted_and_blank_pdfs_fail_cleanly(client):
    _, c = client
    files = {"truncated.pdf": BAD["truncated.pdf"], "locked.pdf": pdf_bytes(encrypt=True),
             "blank.pdf": pdf_bytes(text=" "), "photo.png": png_bytes(), "sideways.png": png_bytes(90)}
    r = c.post("/batches", files=[("files", (n, b, "application/octet-stream")) for n, b in files.items()])
    accepted = {a["file"] for a in r.json()["accepted"]}
    b = _wait(c, r.json()["batch_id"])
    for d in b["documents"]:  # processed or failed with a reason; never stuck, never a 500
        assert d["status"] in ("done", "failed"), d
        if d["status"] == "failed":
            assert d["error"], d
        if d["file"] == "locked.pdf":
            assert d["status"] == "failed" and "password" in d["error"]
    assert c.get(f"/batches/{r.json()['batch_id']}/export.zip").status_code == 200
    assert accepted  # at least the readable ones were taken


def test_report_upload_keeps_pdf_metadata_and_refuses_junk(client):
    m, c = client
    m._ld_process = lambda rid: None  # reading is tested in test_longdoc; here only the upload path
    r = c.post("/reports", files={"file": ("r.pdf", pdf_bytes(meta={"title": "Annual Report 2025",
                                                                    "author": "Example plc"}), "application/pdf")})
    assert r.status_code == 202
    src = m.LONGDOC_DIR / r.json()["report_id"] / "source.pdf"
    assert pymupdf.open(src).metadata["title"] == "Annual Report 2025"  # stored as uploaded, not re-rendered
    for name, data in BAD.items():
        r = c.post("/reports", files={"file": (name, data, "application/pdf")})
        assert r.status_code == 415, (name, r.status_code)
    r = c.post("/reports", files={"file": ("locked.pdf", pdf_bytes(encrypt=True), "application/pdf")})
    assert r.status_code == 415 and "password" in r.json()["detail"]
    r = c.post("/reports", files={"file": ("scan.png", png_bytes(), "image/png")})
    assert r.status_code == 202


def test_report_page_limit_and_bad_ids(client, monkeypatch):
    m, c = client
    monkeypatch.setattr(m, "MAX_LONGDOC_PAGES", 3)
    m._ld_process = lambda rid: None
    r = c.post("/reports", files={"file": ("long.pdf", pdf_bytes(pages=4), "application/pdf")})
    assert r.status_code == 413 and "3 pages" in r.json()["detail"]
    for rid in ("../../etc", "..%2F..", "x" * 300, "abc"):
        assert c.get(f"/reports/{rid}").status_code == 404
    rid = c.post("/reports", files={"file": ("r.pdf", pdf_bytes(), "application/pdf")}).json()["report_id"]
    assert c.get(f"/reports/{rid}/page/0.png").status_code == 404
    assert c.get(f"/reports/{rid}/page/99.png").status_code == 404
    assert c.post(f"/reports/{rid}/ask", json={"question": "x"}).status_code == 422  # too short
    assert c.post(f"/reports/{rid}/ask", json={"question": "What was revenue?"}).status_code == 409  # still reading


def test_tricky_filenames_are_stored_safely(client):
    m, c = client
    names = ["../../evil.pdf", "<script>alert(1)</script>.pdf", "=cmd|'/c calc'!A1.pdf", "naïve façade.pdf"]
    r = c.post("/batches", files=[("files", (n, pdf_bytes(), "application/pdf")) for n in names])
    assert len(r.json()["accepted"]) == 4
    b = _wait(c, r.json()["batch_id"])
    for p in m.UPLOAD_DIR.iterdir():  # files on disk are named by job id, never by the uploaded name
        assert p.parent == m.UPLOAD_DIR and p.stem.isalnum()
    assert {d["file"] for d in b["documents"]} == set(names)  # shown back as text, escaped by the UI
    summary = c.get(f"/batches/{r.json()['batch_id']}/export/summary.csv").content.decode("utf-8-sig")
    assert "\n'=cmd" in summary.replace("\r", "")  # spreadsheet formula neutralised


def test_visitors_cannot_reach_each_others_reports(tmp_path, monkeypatch):
    m = _app(tmp_path, monkeypatch, demo=True)
    monkeypatch.setattr(m, "LONGDOC_DIR", tmp_path / "ld")
    m._ld_process = lambda rid: None
    alice, bob = TestClient(m.app), TestClient(m.app)
    rid = alice.post("/reports", files={"file": ("a.pdf", pdf_bytes(), "application/pdf")}).json()["report_id"]
    assert alice.get(f"/reports/{rid}").status_code == 200
    for path in (f"/reports/{rid}", f"/reports/{rid}/page/1.png"):
        assert bob.get(path).status_code == 404
    assert bob.post(f"/reports/{rid}/ask", json={"question": "What was revenue?"}).status_code == 404
    assert bob.get("/reports").json() == []
    assert "owner" not in alice.get(f"/reports/{rid}").json()  # ids of other visitors never leak


def test_demo_report_limits(tmp_path, monkeypatch):
    m = _app(tmp_path, monkeypatch, demo=True)
    monkeypatch.setattr(m, "LONGDOC_DIR", tmp_path / "ld")
    monkeypatch.setattr(m, "DEMO_LONGDOC_PAGES", 2)
    monkeypatch.setattr(m, "DEMO_MAX_QUESTIONS", 1)
    m._ld_process = lambda rid: None
    c = TestClient(m.app)
    r = c.post("/reports", files={"file": ("big.pdf", pdf_bytes(pages=3), "application/pdf")})
    assert r.status_code == 413 and m.REPO_URL in r.json()["detail"]
    rid = c.post("/reports", files={"file": ("r.pdf", pdf_bytes(), "application/pdf")}).json()["report_id"]
    st = json.loads((m.LONGDOC_DIR / rid / "state.json").read_text())
    (m.LONGDOC_DIR / rid / "state.json").write_text(json.dumps(st | {"questions": 1}))
    r = c.post(f"/reports/{rid}/ask", json={"question": "What was revenue?"})
    assert r.status_code == 429 and m.REPO_URL in r.json()["detail"]


def test_demo_reports_expire(tmp_path, monkeypatch):
    m = _app(tmp_path, monkeypatch, demo=True)
    monkeypatch.setattr(m, "LONGDOC_DIR", tmp_path / "ld")
    m._ld_process = lambda rid: None
    c = TestClient(m.app)
    rid = c.post("/reports", files={"file": ("r.pdf", pdf_bytes(), "application/pdf")}).json()["report_id"]
    st = json.loads((m.LONGDOC_DIR / rid / "state.json").read_text())
    (m.LONGDOC_DIR / rid / "state.json").write_text(json.dumps(st | {"created": time.time() - m.DEMO_TTL - 60}))
    c.post("/reports", files={"file": ("r2.pdf", pdf_bytes(), "application/pdf")})  # any upload purges
    assert not (m.LONGDOC_DIR / rid).exists()


def test_spoofed_cloudflare_header_does_not_reset_the_demo_limit(tmp_path, monkeypatch):
    m = _app(tmp_path, monkeypatch, demo=True)
    assert not m.TRUST_CLOUDFLARE
    codes = []
    for k in range(3):  # a new cookie and a new fake address every time
        c = TestClient(m.app)
        r = c.post("/batches", headers={"cf-connecting-ip": f"198.51.100.{k}"},
                   files=[("files", ("a.pdf", pdf_bytes(), "application/pdf"))])
        codes.append(r.status_code)
    assert codes == [202, 202, 429]

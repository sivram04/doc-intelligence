"""Security checks: browser headers, visitor isolation for every per-document endpoint, and hostile uploads."""
import io
import zipfile

from fastapi.testclient import TestClient

from tests.test_batches import _app, _wait
from tests.test_robustness import pdf_bytes


def test_security_headers_on_pages_and_api(tmp_path, monkeypatch):
    c = TestClient(_app(tmp_path, monkeypatch, demo=True).app)
    for path in ("/", "/app", "/config", "/health"):
        h = c.get(path).headers
        assert h["x-content-type-options"] == "nosniff", path
        csp = h["content-security-policy"]
        assert "object-src 'none'" in csp and "frame-ancestors" in csp and "connect-src 'self'" in csp, path


def test_one_visitor_cannot_reach_another_visitors_documents(tmp_path, monkeypatch):
    m = _app(tmp_path, monkeypatch, demo=True)
    alice, bob = TestClient(m.app), TestClient(m.app)
    monkeypatch.setattr(m, "DEMO_MAX_DOCS", 10)
    monkeypatch.setattr(m, "client_of", lambda request: request.headers.get("x-net", ""))
    a = {"x-net": "a"}
    b = {"x-net": "b"}
    batch = alice.post("/batches", headers=a, files=[("files", ("secret.pdf", pdf_bytes(text="Alice INV-1"), "application/pdf"))]).json()
    _wait(alice, batch["batch_id"])
    job = batch["accepted"][0]["job_id"]
    bid = batch["batch_id"]
    assert alice.get(f"/jobs/{job}", headers=a).status_code == 200
    for path in (f"/jobs/{job}", f"/jobs/{job}/pages", f"/jobs/{job}/page/0.png", f"/batches/{bid}",
                 f"/batches/{bid}/export.zip", f"/batches/{bid}/export/invoices.csv", f"/batches/{bid}/journal"):
        assert bob.get(path, headers=b).status_code == 404, path
    assert all(j["job_id"] != job for j in bob.get("/review/queue", headers=b).json())
    r = bob.post(f"/jobs/{job}/review", headers=b, json={"fields": {}, "reviewer": "x", "seconds": 1})
    assert r.status_code == 404
    rep = alice.post("/reports", headers=a, files={"file": ("r.pdf", pdf_bytes(pages=2), "application/pdf")})
    assert rep.status_code in (200, 202), rep.text
    rid = rep.json()["report_id"]
    for path in (f"/reports/{rid}", f"/reports/{rid}/page/1.png"):
        assert bob.get(path, headers=b).status_code == 404, path
    assert bob.post(f"/reports/{rid}/ask", headers=b, json={"question": "revenue?"}).status_code == 404


def test_hostile_uploads_are_refused(tmp_path, monkeypatch):
    c = TestClient(_app(tmp_path, monkeypatch, demo=False).app)
    exe = b"MZ" + b"\0" * 2000  # a Windows program renamed to .pdf
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("a.txt", "x")
    html = b"<html><script>alert(1)</script></html>"
    files = [("files", ("invoice.pdf", exe, "application/pdf")), ("files", ("scan.png", z.getvalue(), "image/png")),
             ("files", ("page.pdf", html, "application/pdf")), ("files", ("../../etc/passwd.pdf", pdf_bytes(), "application/pdf"))]
    r = c.post("/batches", files=files)
    body = r.json() if r.status_code == 202 else r.json()["detail"]
    refused = {x["file"] for x in body["refused"]}
    assert {"invoice.pdf", "scan.png", "page.pdf"} <= refused
    # the path-like name is stored as text only; nothing is written outside the upload folder
    assert not (tmp_path.parent / "etc").exists()


def test_oversized_upload_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    c = TestClient(_app(tmp_path, monkeypatch, demo=False).app)
    big = pdf_bytes() + b"0" * 1_200_000
    r = c.post("/batches", files=[("files", ("big.pdf", big, "application/pdf"))])
    assert r.status_code in (413, 422) or any("MB" in x["error"] for x in r.json().get("refused", []))


def test_visitor_ip_behind_a_trusted_proxy(tmp_path, monkeypatch):
    m = _app(tmp_path, monkeypatch, demo=True)
    from starlette.requests import Request

    def req(xff):
        return Request({"type": "http", "client": ("10.0.0.1", 1), "headers": [(b"x-forwarded-for", xff.encode())]})

    assert m.client_of(req("1.1.1.1")) == m.client_of(req("2.2.2.2"))  # off by default: header ignored
    monkeypatch.setattr(m, "TRUST_PROXY_HOPS", 1)
    assert m.client_of(req("1.1.1.1")) != m.client_of(req("2.2.2.2"))  # each visitor counted separately
    # a forged left-most entry cannot change who the proxy says is calling
    assert m.client_of(req("9.9.9.9, 1.1.1.1")) == m.client_of(req("1.1.1.1"))


def test_demo_cookie_works_inside_a_frame_over_https(tmp_path, monkeypatch):
    c = TestClient(_app(tmp_path, monkeypatch, demo=True).app)
    plain = c.get("/config").headers["set-cookie"].lower()
    assert "samesite=lax" in plain and "secure" not in plain and "httponly" in plain
    tls = TestClient(_app(tmp_path, monkeypatch, demo=True).app).get("/config", headers={"x-forwarded-proto": "https"})
    ck = tls.headers["set-cookie"].lower()
    assert "samesite=none" in ck and "secure" in ck and "partitioned" in ck and "httponly" in ck

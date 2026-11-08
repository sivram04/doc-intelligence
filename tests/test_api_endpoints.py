"""Endpoints the QA report listed as untested: health/readiness, metrics, review stats,
page images, the sample-document seeder, API keys, and many visitors at once."""
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from PIL import Image

from tests.test_batches import _app, _wait
from tests.test_robustness import pdf_bytes


def test_health_ready_and_metrics(tmp_path, monkeypatch):
    m = _app(tmp_path, monkeypatch, demo=False)
    c = TestClient(m.app)
    assert c.get("/health").status_code == 200
    r = c.get("/ready")
    assert r.status_code == 200 and r.json() == {"database": True, "queue": None, "ok": True}
    c.post("/batches", files=[("files", ("a.pdf", pdf_bytes(), "application/pdf"))])
    text = c.get("/metrics").text
    assert "docintel" in text and "# TYPE" in text  # Prometheus format with our counters


def test_ready_reports_a_broken_queue(tmp_path, monkeypatch):
    m = _app(tmp_path, monkeypatch, demo=False)
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1/0")  # nothing listens there
    r = TestClient(m.app).get("/ready")
    assert r.status_code == 503 and r.json()["queue"] is False and r.json()["ok"] is False


def test_page_image_and_review_stats(tmp_path, monkeypatch):
    m = _app(tmp_path, monkeypatch, demo=False)
    c = TestClient(m.app)
    batch = c.post("/batches", files=[("files", ("a.pdf", pdf_bytes(pages=2), "application/pdf"))]).json()
    job = batch["accepted"][0]["job_id"]
    _wait(c, batch["batch_id"])
    assert [p["page"] for p in c.get(f"/jobs/{job}/pages").json()] == [0, 1]
    png = c.get(f"/jobs/{job}/page/1.png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    assert Image.open(io.BytesIO(png.content)).width > 500
    assert c.get(f"/jobs/{job}/page/5.png").status_code == 404
    stats = c.get("/review/stats").json()
    assert {"documents_reviewed", "fields_corrected"} <= set(stats)


def test_demo_seed_makes_one_batch_and_counts_against_the_limit(tmp_path, monkeypatch):
    m = _app(tmp_path, monkeypatch, demo=True)
    c = TestClient(m.app)
    r = c.post("/demo/seed?n=2")
    assert r.status_code == 202, r.text
    body = r.json()
    assert len(body["queued"]) == 2 and body["batch_id"]
    assert c.get("/config").json()["remaining"] == 0  # samples use the visitor's daily allowance
    assert c.post("/demo/seed?n=1").status_code == 429


def test_api_keys_lock_the_api_but_not_health(tmp_path, monkeypatch):
    monkeypatch.setenv("API_KEYS", "k1,k2")
    m = _app(tmp_path, monkeypatch, demo=False)
    monkeypatch.setenv("API_KEYS", "k1,k2")  # _app clears it; set again for the request-time check
    c = TestClient(m.app)
    assert c.get("/health").status_code == 200
    assert c.get("/batches").status_code == 401
    assert c.get("/batches", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get("/batches", headers={"X-API-Key": "k2"}).status_code == 200


def test_ten_visitors_at_once_get_only_their_own_documents(tmp_path, monkeypatch):
    """Load check: 10 demo visitors upload at the same moment; nothing fails or leaks across visitors."""
    m = _app(tmp_path, monkeypatch, demo=True)
    monkeypatch.setattr(m, "client_of", lambda request: request.headers.get("x-test-visitor", ""))

    def visit(k: int):
        c = TestClient(m.app)
        h = {"x-test-visitor": f"v{k}"}  # a different network per visitor
        t0 = time.perf_counter()
        r = c.post("/batches", headers=h, files=[("files", (f"visitor{k}.pdf", pdf_bytes(text=f"Visitor {k} INV-{k}"),
                                                             "application/pdf"))])
        assert r.status_code == 202, r.text
        b = _wait(c, r.json()["batch_id"])
        mine = [d["file"] for bb in c.get("/batches", headers=h).json() for d in c.get(f"/batches/{bb['batch_id']}",
                                                                                  headers=h).json()["documents"]]
        return k, b["counts"], mine, time.perf_counter() - t0

    with ThreadPoolExecutor(10) as pool:
        results = list(pool.map(visit, range(10)))
    for k, counts, mine, _ in results:
        assert counts.get("failed", 0) == 0, counts
        assert mine == [f"visitor{k}.pdf"], (k, mine)
    assert max(r[3] for r in results) < 60
    json.dumps(results)

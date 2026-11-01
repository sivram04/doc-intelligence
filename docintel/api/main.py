"""REST API (F1, F9): upload -> async job -> status/result, case cross-checks,
and the human review loop (queue, page images, corrections, reviewer stats).

Jobs run on an in-process thread pool and persist to SQLite (docintel.store);
the Redis + arq worker swap keeps the same endpoints (see docker-compose.yml).
Set EXTRACTOR to choose the model, e.g. EXTRACTOR=ollama:docintel-qwen1.5b-ft.
"""
from __future__ import annotations

import contextlib
import hmac
import json
import os
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import cv2
import pymupdf
import redis.asyncio as redis
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, File, Header, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from ..jobs import run_job
from ..obs import CORRECTIONS, REVIEWS, setup_logging
from ..pipeline.preprocess import load_pages
from ..schemas import DocType
from ..store import Store
from ..validators import validate_case

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "data/uploads"))
DEMO_DIR = Path(os.getenv("DEMO_DIR", "data/synthetic"))
STATIC = Path(__file__).parent / "static"
MAX_UPLOAD = int(os.getenv("MAX_UPLOAD_MB", "25")) * 1_000_000
RENDER_ZOOM = 2.0

app = FastAPI(title="Document Intelligence Engine", version="0.2.0")
pool = ThreadPoolExecutor(max_workers=int(os.getenv("WORKERS", "2")))
store = Store()
setup_logging(os.getenv("LOG_LEVEL", "INFO"))


class ReviewIn(BaseModel):
    fields: dict[str, Any]
    reviewer: str = Field(min_length=1, max_length=64)
    seconds: float = Field(ge=0)


def _run(job_id: str, path: Path) -> None:
    with contextlib.suppress(Exception):  # the failure is recorded on the job by run_job
        run_job(store, job_id, str(path))


async def _enqueue(job_id: str, path: Path) -> None:
    """Redis/arq when REDIS_URL is set (production); in-process pool otherwise (dev)."""
    url = os.getenv("REDIS_URL")
    if not url:
        pool.submit(_run, job_id, path)
        return
    if not hasattr(app.state, "arq"):
        app.state.arq = await create_pool(RedisSettings.from_dsn(url))
    await app.state.arq.enqueue_job("process_document", job_id, str(path))


def require_key(x_api_key: str | None = Header(default=None)) -> None:
    keys = [k for k in os.getenv("API_KEYS", "").split(",") if k]
    if keys and not any(hmac.compare_digest(x_api_key or "", k) for k in keys):
        raise HTTPException(401, "Missing or invalid X-API-Key")


def sniff(data: bytes) -> str | None:
    """Trust content, not the filename: returns the extension the bytes really are."""
    for magic, ext in ((b"%PDF-", ".pdf"), (b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"),
                       (b"II*\x00", ".tif"), (b"MM\x00*", ".tif")):
        if data.startswith(magic):
            return ext
    return None


async def _submit(src: Path | bytes, filename: str, case_id: str | None, ext: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{job_id}{ext}"
    if isinstance(src, bytes):
        path.write_bytes(src)
    else:
        shutil.copyfile(src, path)
    store.create_job(job_id, filename, str(path), case_id)
    await _enqueue(job_id, path)
    return job_id


def _get(job_id: str) -> dict:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job")
    return job


@app.get("/health")
def health():
    return {"ok": True, "extractor": os.getenv("EXTRACTOR", "rules")}


@app.get("/ready")
async def ready():
    """Readiness: dependencies reachable, so a load balancer can hold traffic until they are."""
    checks: dict[str, bool | None] = {"database": False, "queue": None}
    with contextlib.suppress(Exception):
        checks["database"] = store.ping()
    if url := os.getenv("REDIS_URL"):
        checks["queue"] = False
        with contextlib.suppress(Exception):
            client = redis.from_url(url)
            checks["queue"] = bool(await client.ping())
            await client.aclose()
    ok = bool(checks["database"]) and checks["queue"] is not False
    return JSONResponse(checks | {"ok": ok}, status_code=200 if ok else 503)


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/documents", status_code=202, dependencies=[Depends(require_key)])
async def upload(file: UploadFile = File(...), case_id: str | None = None):  # noqa: B008
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, f"File exceeds {MAX_UPLOAD // 1_000_000} MB")
    ext = sniff(data)
    if ext is None:
        raise HTTPException(415, "Unsupported file: expected PDF, PNG, JPEG or TIFF content")
    return {"job_id": await _submit(data, file.filename or "upload", case_id, ext), "status": "queued"}


@app.get("/jobs/{job_id}", dependencies=[Depends(require_key)])
def job(job_id: str):
    return _get(job_id)


@app.get("/jobs/{job_id}/pages", dependencies=[Depends(require_key)])
def pages(job_id: str):
    with pymupdf.open(_get(job_id)["path"]) as d:
        return [{"page": i, "width": p.rect.width, "height": p.rect.height} for i, p in enumerate(d)]


@app.get("/jobs/{job_id}/page/{n}.png", dependencies=[Depends(require_key)])
def page_image(job_id: str, n: int):
    """Native pages render as-is. Scanned pages are served exactly as OCR saw them (deskewed and
    denoised), so the field boxes, which are in that image's coordinates, line up."""
    doc, pages = load_pages(_get(job_id)["path"])
    with doc:
        if not 0 <= n < len(pages):
            raise HTTPException(404, "No such page")
        if pages[n].native:
            png = doc[n].get_pixmap(matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM)).tobytes("png")
        else:
            ok, buf = cv2.imencode(".png", pages[n].image)
            if not ok:
                raise HTTPException(500, "Could not encode page image")
            png = buf.tobytes()
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})


@app.get("/cases/{case_id}/check", dependencies=[Depends(require_key)])
def check_case(case_id: str):
    done = [j for j in store.jobs(case_id) if j["status"] == "done"]
    if not done:
        raise HTTPException(404, "No processed documents for this case")
    docs = {DocType(j["result"]["doc_type"]): j["result"]["fields"] for j in done}
    return {"case_id": case_id, "documents": len(done), "flags": validate_case(docs)}


@app.get("/review/queue", dependencies=[Depends(require_key)])
def review_queue():
    return [{"job_id": j["job_id"], "filename": j["filename"], "doc_type": j["result"]["doc_type"],
             "to_review": len(j["routing"]["review"]), "fields": len(j["result"]["fields"]),
             "flags": j["result"]["flags"]} for j in store.review_queue()]


@app.post("/jobs/{job_id}/review", dependencies=[Depends(require_key)])
def submit_review(job_id: str, body: ReviewIn):
    job = _get(job_id)
    if job["status"] != "done":
        raise HTTPException(409, f"Job is {job['status']}")
    unknown = set(body.fields) - set(job["result"]["fields"])
    if unknown:
        raise HTTPException(422, f"Unknown fields: {sorted(unknown)}")
    out = store.save_review(job_id, body.reviewer, body.seconds, body.fields)
    REVIEWS.inc()
    CORRECTIONS.inc(out["fields_corrected"])
    return out


@app.get("/review/stats", dependencies=[Depends(require_key)])
def review_stats():
    return store.stats()


@app.post("/demo/seed", status_code=202, dependencies=[Depends(require_key)])
async def seed_demo(n: int = 6, split: str = "test"):
    """Queue n synthetic documents from the benchmark (demo only; never real documents)."""
    ids = json.loads((DEMO_DIR / "splits.json").read_text())["splits"][split][:max(1, min(n, 50))]
    out = []
    for doc_id in ids:
        gt = json.loads((DEMO_DIR / "docs" / f"{doc_id}.json").read_text())
        out.append(await _submit(DEMO_DIR / "docs" / gt["file"], gt["file"], gt.get("case_id"),
                                 Path(gt["file"]).suffix))
    return {"queued": out}


@app.get("/")
def ui():
    return FileResponse(STATIC / "index.html")

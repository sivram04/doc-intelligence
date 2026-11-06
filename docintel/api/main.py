"""REST API (F1, F9): upload -> async job -> status/result, case cross-checks,
and the human review loop (queue, page images, corrections, reviewer stats).

Jobs run on an in-process thread pool and persist to SQLite (docintel.store);
the Redis + arq worker swap keeps the same endpoints (see docker-compose.yml).
Set EXTRACTOR to choose the model, e.g. EXTRACTOR=ollama:docintel-qwen1.5b-ft.

Batches: upload many files at once and download the extracted data as CSV.
DEMO_MODE=1 is for a public instance: each visitor gets an anonymous cookie and
sees only their own documents, uploads are capped per visitor and per day, and
everything is deleted after DEMO_TTL_HOURS. Self-hosted installs leave it off.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import shutil
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import cv2
import pymupdf
import redis.asyncio as redis
from arq import create_pool
from arq.connections import RedisSettings
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from ..export import tables, to_csv, to_zip
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
MAX_BATCH = int(os.getenv("MAX_BATCH_FILES", "200"))

DEMO = os.getenv("DEMO_MODE", "").lower() in ("1", "true", "yes")
DEMO_MAX_DOCS = int(os.getenv("DEMO_MAX_DOCS", "2"))        # per visitor per day
DEMO_MAX_PAGES = int(os.getenv("DEMO_MAX_PAGES", "3"))      # per document
DEMO_DAILY_CAP = int(os.getenv("DEMO_DAILY_CAP", "150"))    # all visitors together (free LLM quota)
DEMO_TTL = float(os.getenv("DEMO_TTL_HOURS", "24")) * 3600
REPO_URL = os.getenv("REPO_URL", "https://github.com/sivram04/document-intelligence")
COOKIE = "docintel_visitor"
DAY = 86400

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


def visitor(request: Request, response: Response) -> str:
    """Whose documents these are. Self-hosted: one owner. Demo: an anonymous per-browser id."""
    if not DEMO:
        return "local"
    vid = request.cookies.get(COOKIE, "")
    if not (len(vid) == 32 and vid.isalnum()):
        vid = secrets.token_hex(16)
        https = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        response.set_cookie(COOKIE, vid, max_age=int(DEMO_TTL), httponly=True, samesite="lax", secure=https)
    return vid


def client_of(request: Request) -> str:
    """Salted hash of the caller's IP (Cloudflare header first), so limits survive cleared cookies
    without storing addresses."""
    ip = request.headers.get("cf-connecting-ip") or (request.client.host if request.client else "")
    salt = os.getenv("DEMO_SALT", "docintel")
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:16]


def demo_purge() -> None:
    for path in store.purge_before(time.time() - DEMO_TTL):
        with contextlib.suppress(OSError):
            Path(path).unlink()


def demo_allowance(owner: str, client: str) -> int:
    """How many more documents this visitor may submit today."""
    since = time.time() - DAY
    mine = store.count_since(since, owner=owner, client=client)
    return max(0, min(DEMO_MAX_DOCS - mine, DEMO_DAILY_CAP - store.count_since(since)))


def limit_message() -> str:
    return (f"The public demo processes {DEMO_MAX_DOCS} documents per visitor per day. To process more, "
            f"run it on your own computer: it is free and open source, and your files never leave your machine. "
            f"Setup guide: {REPO_URL}#quick-start")


def page_count(data: bytes, ext: str) -> int:
    if ext == ".pdf":
        with pymupdf.open(stream=data, filetype="pdf") as d:
            return len(d)
    return 1


def sniff(data: bytes) -> str | None:
    """Trust content, not the filename: returns the extension the bytes really are."""
    for magic, ext in ((b"%PDF-", ".pdf"), (b"\x89PNG\r\n\x1a\n", ".png"), (b"\xff\xd8\xff", ".jpg"),
                       (b"II*\x00", ".tif"), (b"MM\x00*", ".tif")):
        if data.startswith(magic):
            return ext
    return None


async def _submit(src: Path | bytes, filename: str, case_id: str | None, ext: str,
                  batch_id: str | None = None, owner: str = "local", client: str | None = None) -> str:
    job_id = uuid.uuid4().hex[:12]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{job_id}{ext}"
    if isinstance(src, bytes):
        path.write_bytes(src)
    else:
        shutil.copyfile(src, path)
    store.create_job(job_id, filename, str(path), case_id, batch_id, owner, client)
    await _enqueue(job_id, path)
    return job_id


def _get(job_id: str, owner: str = "local") -> dict:
    job = store.get_job(job_id)
    if job is None or (DEMO and job.get("owner") != owner):  # another visitor's job looks like no job
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


async def _read_upload(file: UploadFile) -> tuple[bytes, str]:
    """File bytes and real extension, or an HTTPException saying why the file is refused."""
    data = await file.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, f"File exceeds {MAX_UPLOAD // 1_000_000} MB")
    ext = sniff(data)
    if ext is None:
        raise HTTPException(415, "Unsupported file: expected PDF, PNG, JPEG or TIFF content")
    if DEMO:
        try:
            n = page_count(data, ext)
        except Exception:  # noqa: BLE001 - a PDF PyMuPDF cannot open
            raise HTTPException(415, "Could not open this PDF") from None
        if n > DEMO_MAX_PAGES:
            raise HTTPException(413, f"The public demo accepts documents of up to {DEMO_MAX_PAGES} pages")
    return data, ext


@app.get("/config")
def config(request: Request, owner: str = Depends(visitor)):
    """What the console needs to know: demo limits and how many documents this visitor has left."""
    out = {"demo": DEMO, "repo_url": REPO_URL, "max_batch": MAX_BATCH, "extractor": os.getenv("EXTRACTOR", "rules")}
    if DEMO:
        out |= {"max_docs": DEMO_MAX_DOCS, "max_pages": DEMO_MAX_PAGES, "ttl_hours": DEMO_TTL / 3600,
                "remaining": demo_allowance(owner, client_of(request)), "limit_message": limit_message()}
    return out


@app.post("/documents", status_code=202, dependencies=[Depends(require_key)])
async def upload(request: Request, file: UploadFile = File(...), case_id: str | None = None,  # noqa: B008
                 owner: str = Depends(visitor)):
    client = client_of(request)
    if DEMO:
        demo_purge()
        if demo_allowance(owner, client) < 1:
            raise HTTPException(429, limit_message())
    data, ext = await _read_upload(file)
    return {"job_id": await _submit(data, file.filename or "upload", case_id, ext, None, owner, client),
            "status": "queued"}


@app.post("/batches", status_code=202, dependencies=[Depends(require_key)])
async def upload_batch(request: Request, files: list[UploadFile] = File(...),  # noqa: B008
                       owner: str = Depends(visitor)):
    """Many files at once. Each file is accepted or refused on its own; refused ones are listed
    with the reason and the rest still run."""
    if len(files) > MAX_BATCH:
        raise HTTPException(413, f"At most {MAX_BATCH} files per batch")
    client = client_of(request)
    allowance = len(files)
    if DEMO:
        demo_purge()
        allowance = demo_allowance(owner, client)
        if allowance < 1:
            raise HTTPException(429, limit_message())
    batch_id = uuid.uuid4().hex[:12]
    accepted, refused = [], []
    for f in files:
        name = f.filename or "upload"
        if len(accepted) >= allowance:
            refused.append({"file": name, "error": limit_message()})
            continue
        try:
            data, ext = await _read_upload(f)
        except HTTPException as e:
            refused.append({"file": name, "error": e.detail})
            continue
        accepted.append({"file": name, "job_id": await _submit(data, name, None, ext, batch_id, owner, client)})
    if not accepted:
        raise HTTPException(422, {"message": "No file in this batch could be processed", "refused": refused})
    return {"batch_id": batch_id, "accepted": accepted, "refused": refused}


@app.get("/batches", dependencies=[Depends(require_key)])
def list_batches(owner: str = Depends(visitor)):
    return store.batches(owner)


def _batch(batch_id: str, owner: str) -> list[dict]:
    jobs = store.batch(batch_id, owner)
    if not jobs:
        raise HTTPException(404, "Unknown batch")
    return jobs


@app.get("/batches/{batch_id}", dependencies=[Depends(require_key)])
def batch_status(batch_id: str, owner: str = Depends(visitor)):
    jobs = _batch(batch_id, owner)
    counts: dict[str, int] = {}
    docs = []
    for j in jobs:
        counts[j["status"]] = counts.get(j["status"], 0) + 1
        r = j.get("result") or {}
        docs.append({"job_id": j["job_id"], "file": j["filename"], "status": j["status"],
                     "doc_type": r.get("doc_type"), "to_review": len((j.get("routing") or {}).get("review", [])),
                     "reviewed": bool(j.get("reviewed")), "flags": r.get("flags", []), "error": j.get("error")})
    finished = counts.get("done", 0) + counts.get("failed", 0)
    return {"batch_id": batch_id, "total": len(jobs), "finished": finished, "complete": finished == len(jobs),
            "counts": counts, "documents": docs}


@app.get("/batches/{batch_id}/export.zip", dependencies=[Depends(require_key)])
def export_batch(batch_id: str, owner: str = Depends(visitor)):
    """Every CSV of the batch in one zip (one file per document type, plus table rows and a summary)."""
    return Response(to_zip(_batch(batch_id, owner)), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="docintel-{batch_id}.zip"'})


@app.get("/batches/{batch_id}/export/{name}.csv", dependencies=[Depends(require_key)])
def export_csv(batch_id: str, name: str, owner: str = Depends(visitor)):
    found = tables(_batch(batch_id, owner))
    if name not in found:
        raise HTTPException(404, f"No {name}.csv in this batch; available: {sorted(found)}")
    return Response(to_csv(*found[name]), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@app.get("/jobs/{job_id}", dependencies=[Depends(require_key)])
def job(job_id: str, owner: str = Depends(visitor)):
    return _get(job_id, owner)


@app.get("/jobs/{job_id}/pages", dependencies=[Depends(require_key)])
def pages(job_id: str, owner: str = Depends(visitor)):
    with pymupdf.open(_get(job_id, owner)["path"]) as d:
        return [{"page": i, "width": p.rect.width, "height": p.rect.height} for i, p in enumerate(d)]


@app.get("/jobs/{job_id}/page/{n}.png", dependencies=[Depends(require_key)])
def page_image(job_id: str, n: int, owner: str = Depends(visitor)):
    """Native pages render as-is. Scanned pages are served exactly as OCR saw them (deskewed and
    denoised), so the field boxes, which are in that image's coordinates, line up."""
    doc, pages = load_pages(_get(job_id, owner)["path"])
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
def check_case(case_id: str, owner: str = Depends(visitor)):
    done = [j for j in store.jobs(case_id) if j["status"] == "done" and (not DEMO or j.get("owner") == owner)]
    if not done:
        raise HTTPException(404, "No processed documents for this case")
    docs = {DocType(j["result"]["doc_type"]): j["result"]["fields"] for j in done}
    return {"case_id": case_id, "documents": len(done), "flags": validate_case(docs)}


@app.get("/review/queue", dependencies=[Depends(require_key)])
def review_queue(owner: str = Depends(visitor)):
    return [{"job_id": j["job_id"], "filename": j["filename"], "doc_type": j["result"]["doc_type"],
             "batch_id": j.get("batch_id"),
             "to_review": len(j["routing"]["review"]), "fields": len(j["result"]["fields"]),
             "flags": j["result"]["flags"]} for j in store.review_queue(owner if DEMO else None)]


@app.post("/jobs/{job_id}/review", dependencies=[Depends(require_key)])
def submit_review(job_id: str, body: ReviewIn, owner: str = Depends(visitor)):
    job = _get(job_id, owner)
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
def review_stats(owner: str = Depends(visitor)):
    return store.stats(owner if DEMO else None)


@app.post("/demo/seed", status_code=202, dependencies=[Depends(require_key)])
async def seed_demo(request: Request, n: int = 6, split: str = "test", owner: str = Depends(visitor)):
    """Queue n synthetic sample documents from the benchmark, as one batch (never real documents)."""
    client = client_of(request)
    if DEMO:
        demo_purge()
        n = min(n, demo_allowance(owner, client))
        if n < 1:
            raise HTTPException(429, limit_message())
    ids = json.loads((DEMO_DIR / "splits.json").read_text())["splits"][split][:max(1, min(n, 50))]
    batch_id = uuid.uuid4().hex[:12]
    out = []
    for doc_id in ids:
        gt = json.loads((DEMO_DIR / "docs" / f"{doc_id}.json").read_text())
        out.append(await _submit(DEMO_DIR / "docs" / gt["file"], gt["file"], gt.get("case_id"),
                                 Path(gt["file"]).suffix, batch_id, owner, client))
    return {"queued": out, "batch_id": batch_id}


@app.get("/")
def ui():
    return FileResponse(STATIC / "index.html")

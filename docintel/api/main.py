"""REST API (F1, F9): upload -> async job -> status/result, case cross-checks,
and the human review loop (queue, page images, corrections, reviewer stats).

Jobs run on an in-process thread pool and persist to SQLite (docintel.store);
the Redis + arq worker swap keeps the same endpoints (see docker-compose.yml).
Set EXTRACTOR to choose the model, e.g. EXTRACTOR=ollama:docintel-qwen1.5b-ft.
"""
from __future__ import annotations

import json
import os
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import cv2
import pymupdf
from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..extract.registry import make_extractor
from ..pipeline.preprocess import load_pages
from ..pipeline.run import process, route
from ..schemas import DocType
from ..store import Store
from ..validators import validate_case

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "data/uploads"))
DEMO_DIR = Path(os.getenv("DEMO_DIR", "data/synthetic"))
STATIC = Path(__file__).parent / "static"
ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
RENDER_ZOOM = 2.0

app = FastAPI(title="Document Intelligence Engine", version="0.2.0")
pool = ThreadPoolExecutor(max_workers=int(os.getenv("WORKERS", "2")))
store = Store()


class ReviewIn(BaseModel):
    fields: dict[str, Any]
    reviewer: str = Field(min_length=1, max_length=64)
    seconds: float = Field(ge=0)


def _run(job_id: str, path: Path) -> None:
    store.update_job(job_id, status="running")
    try:
        result = process(path, make_extractor(os.getenv("EXTRACTOR", "rules")))
        store.update_job(job_id, status="done", result=result.model_dump(mode="json"), routing=route(result))
    except Exception as e:  # noqa: BLE001 - surfaced to the client via job status
        store.update_job(job_id, status="failed", error=str(e)[:500])


def _submit(src: Path | bytes, filename: str, case_id: str | None) -> str:
    job_id = uuid.uuid4().hex[:12]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{job_id}{Path(filename).suffix.lower()}"
    if isinstance(src, bytes):
        path.write_bytes(src)
    else:
        shutil.copyfile(src, path)
    store.create_job(job_id, filename, str(path), case_id)
    pool.submit(_run, job_id, path)
    return job_id


def _get(job_id: str) -> dict:
    job = store.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job")
    return job


@app.get("/health")
def health():
    return {"ok": True, "extractor": os.getenv("EXTRACTOR", "rules")}


@app.post("/documents", status_code=202)
async def upload(file: UploadFile = File(...), case_id: str | None = None):  # noqa: B008
    if Path(file.filename or "").suffix.lower() not in ALLOWED:
        raise HTTPException(415, f"Unsupported file type {Path(file.filename or '').suffix!r}")
    return {"job_id": _submit(await file.read(), file.filename or "upload", case_id), "status": "queued"}


@app.get("/jobs/{job_id}")
def job(job_id: str):
    return _get(job_id)


@app.get("/jobs/{job_id}/pages")
def pages(job_id: str):
    with pymupdf.open(_get(job_id)["path"]) as d:
        return [{"page": i, "width": p.rect.width, "height": p.rect.height} for i, p in enumerate(d)]


@app.get("/jobs/{job_id}/page/{n}.png")
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


@app.get("/cases/{case_id}/check")
def check_case(case_id: str):
    done = [j for j in store.jobs(case_id) if j["status"] == "done"]
    if not done:
        raise HTTPException(404, "No processed documents for this case")
    docs = {DocType(j["result"]["doc_type"]): j["result"]["fields"] for j in done}
    return {"case_id": case_id, "documents": len(done), "flags": validate_case(docs)}


@app.get("/review/queue")
def review_queue():
    return [{"job_id": j["job_id"], "filename": j["filename"], "doc_type": j["result"]["doc_type"],
             "to_review": len(j["routing"]["review"]), "fields": len(j["result"]["fields"]),
             "flags": j["result"]["flags"]} for j in store.review_queue()]


@app.post("/jobs/{job_id}/review")
def submit_review(job_id: str, body: ReviewIn):
    job = _get(job_id)
    if job["status"] != "done":
        raise HTTPException(409, f"Job is {job['status']}")
    unknown = set(body.fields) - set(job["result"]["fields"])
    if unknown:
        raise HTTPException(422, f"Unknown fields: {sorted(unknown)}")
    return store.save_review(job_id, body.reviewer, body.seconds, body.fields)


@app.get("/review/stats")
def review_stats():
    return store.stats()


@app.post("/demo/seed", status_code=202)
def seed_demo(n: int = 6, split: str = "test"):
    """Queue n synthetic documents from the benchmark (demo only; never real documents)."""
    ids = json.loads((DEMO_DIR / "splits.json").read_text())["splits"][split][:max(1, min(n, 50))]
    out = []
    for doc_id in ids:
        gt = json.loads((DEMO_DIR / "docs" / f"{doc_id}.json").read_text())
        out.append(_submit(DEMO_DIR / "docs" / gt["file"], gt["file"], gt.get("case_id")))
    return {"queued": out}


@app.get("/")
def ui():
    return FileResponse(STATIC / "index.html")

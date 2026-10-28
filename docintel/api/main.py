"""REST API (F1): upload -> async job -> status/result, plus case cross-checks.

Jobs run on an in-process thread pool for now; the Redis + arq worker swap
keeps the same endpoints (see docker-compose.yml)."""
from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile

from ..pipeline.run import process, route
from ..schemas import DocType
from ..validators import validate_case

UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "data/uploads"))
ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}

app = FastAPI(title="Document Intelligence Engine", version="0.1.0")
pool = ThreadPoolExecutor(max_workers=int(os.getenv("WORKERS", "2")))
jobs: dict[str, dict] = {}


def _run(job_id: str, path: Path) -> None:
    jobs[job_id]["status"] = "running"
    try:
        result = process(path)
        jobs[job_id] |= {"status": "done", "result": result.model_dump(mode="json"), "routing": route(result)}
    except Exception as e:  # noqa: BLE001 - surfaced to the client via job status
        jobs[job_id] |= {"status": "failed", "error": str(e)}


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/documents", status_code=202)
async def upload(file: UploadFile = File(...), case_id: str | None = None):  # noqa: B008
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(415, f"Unsupported file type {ext!r}")
    job_id = uuid.uuid4().hex[:12]
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = UPLOAD_DIR / f"{job_id}{ext}"
    path.write_bytes(await file.read())
    jobs[job_id] = {"job_id": job_id, "status": "queued", "case_id": case_id, "filename": file.filename}
    pool.submit(_run, job_id, path)
    return {"job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
def job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Unknown job")
    return jobs[job_id]


@app.get("/cases/{case_id}/check")
def check_case(case_id: str):
    done = [j for j in jobs.values() if j.get("case_id") == case_id and j["status"] == "done"]
    if not done:
        raise HTTPException(404, "No processed documents for this case")
    docs = {DocType(j["result"]["doc_type"]): j["result"]["fields"] for j in done}
    return {"case_id": case_id, "documents": len(done), "flags": validate_case(docs)}

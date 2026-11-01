"""Document job execution, shared by the API's dev thread pool and the arq worker."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from .extract.registry import make_extractor
from .obs import FIELDS, JOBS, LATENCY
from .pipeline.run import process, route
from .store import Store

log = logging.getLogger("docintel.jobs")


def run_job(store: Store, job_id: str, path: str) -> None:
    store.update_job(job_id, status="running")
    t0 = time.perf_counter()
    try:
        result = process(Path(path), make_extractor(os.getenv("EXTRACTOR", "rules")))
        routing = route(result)
        store.update_job(job_id, status="done", result=result.model_dump(mode="json"), routing=routing)
    except Exception as e:
        log.exception("job failed", extra={"ctx": {"job_id": job_id}})
        store.update_job(job_id, status="failed", error=str(e)[:500])
        JOBS.labels("failed", "unknown").inc()
        raise
    LATENCY.observe(time.perf_counter() - t0)
    JOBS.labels("done", result.doc_type.value).inc()
    for name, fields in routing.items():
        FIELDS.labels(name).inc(len(fields))
    log.info("job done", extra={"ctx": {"job_id": job_id, "doc_type": result.doc_type.value,
                                        "review_fields": len(routing["review"])}})

"""arq worker: `arq docintel.worker.WorkerSettings`. Retries transient failures; metrics on :9101."""
from __future__ import annotations

import asyncio
import os

from arq.connections import RedisSettings
from prometheus_client import start_http_server

from .jobs import run_job
from .obs import setup_logging
from .store import Store


async def startup(ctx: dict) -> None:
    setup_logging(os.getenv("LOG_LEVEL", "INFO"))
    ctx["store"] = Store()
    start_http_server(int(os.getenv("WORKER_METRICS_PORT", "9101")))


async def process_document(ctx: dict, job_id: str, path: str) -> None:
    # OCR and inference block; keep the event loop free for arq heartbeats.
    await asyncio.to_thread(run_job, ctx["store"], job_id, path)


class WorkerSettings:
    functions = [process_document]  # noqa: RUF012 - arq reads this class attribute
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    max_jobs = int(os.getenv("WORKERS", "2"))
    job_timeout = int(os.getenv("JOB_TIMEOUT", "600"))
    max_tries = 3
    keep_result = 3600

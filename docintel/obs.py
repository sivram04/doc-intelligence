"""Observability: Prometheus metrics and JSON logs with PII masking (F12)."""
from __future__ import annotations

import json
import logging
import re
import sys
import time

from prometheus_client import Counter, Histogram

JOBS = Counter("docintel_jobs_total", "Processed documents", ["status", "doc_type"])
LATENCY = Histogram("docintel_job_seconds", "End-to-end document processing time",
                    buckets=(0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300))
FIELDS = Counter("docintel_fields_total", "Routed fields", ["route"])
REVIEWS = Counter("docintel_reviews_total", "Submitted reviews")
CORRECTIONS = Counter("docintel_corrections_total", "Fields corrected by reviewers")

_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b\d{2}-\d{7}\b"), "[EIN]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[EMAIL]"),
    (re.compile(r"\b(?:\d[ -]?){13,19}\b"), "[CARD]"),
    (re.compile(r"\b\d{9,17}\b"), "[ACCOUNT]"),
]


def mask(text: str) -> str:
    for pattern, token in _PATTERNS:
        text = pattern.sub(token, text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": round(time.time(), 3), "level": record.levelname, "logger": record.name,
               "msg": mask(record.getMessage())}
        out |= {k: mask(str(v)) for k, v in getattr(record, "ctx", {}).items()}
        if record.exc_info:
            out["exc"] = mask(self.formatException(record.exc_info))
        return json.dumps(out)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)

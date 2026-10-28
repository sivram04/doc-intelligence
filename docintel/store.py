"""Persistence for jobs, reviews and corrections (SQLite; one file, safe across threads).

Corrections are also appended to data/corrections/corrections.jsonl, which is
the input to the active-learning flywheel (Phase 6).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(os.getenv("DOCINTEL_DB", "data/docintel.db"))
CORRECTIONS_LOG = Path(os.getenv("CORRECTIONS_LOG", "data/corrections/corrections.jsonl"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY, filename TEXT, path TEXT, case_id TEXT, status TEXT NOT NULL,
    result TEXT, routing TEXT, error TEXT, created REAL NOT NULL, reviewed REAL
);
CREATE TABLE IF NOT EXISTS reviews (
    job_id TEXT NOT NULL, reviewer TEXT NOT NULL, seconds REAL, fields_reviewed INTEGER,
    fields_corrected INTEGER, created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS corrections (
    job_id TEXT NOT NULL, field TEXT NOT NULL, predicted TEXT, corrected TEXT, confidence REAL,
    reviewer TEXT NOT NULL, created REAL NOT NULL
);
"""


class Store:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)

    def _exec(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur.fetchall()

    def create_job(self, job_id: str, filename: str, path: str, case_id: str | None) -> None:
        self._exec("INSERT INTO jobs (job_id, filename, path, case_id, status, created) VALUES (?,?,?,?,?,?)",
                   (job_id, filename, path, case_id, "queued", time.time()))

    def update_job(self, job_id: str, **cols) -> None:
        cols = {k: json.dumps(v) if k in ("result", "routing") else v for k, v in cols.items()}
        sets = ", ".join(f"{k} = ?" for k in cols)
        self._exec(f"UPDATE jobs SET {sets} WHERE job_id = ?", (*cols.values(), job_id))

    def get_job(self, job_id: str) -> dict | None:
        rows = self._exec("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        return _job(rows[0]) if rows else None

    def jobs(self, case_id: str | None = None) -> list[dict]:
        if case_id:
            return [_job(r) for r in self._exec("SELECT * FROM jobs WHERE case_id = ? ORDER BY created", (case_id,))]
        return [_job(r) for r in self._exec("SELECT * FROM jobs ORDER BY created")]

    def review_queue(self) -> list[dict]:
        """Processed, not yet reviewed, with at least one field routed to review (lowest confidence first)."""
        rows = [_job(r) for r in self._exec("SELECT * FROM jobs WHERE status = 'done' AND reviewed IS NULL")]
        rows = [j for j in rows if j["routing"] and j["routing"]["review"]]
        return sorted(rows, key=lambda j: -len(j["routing"]["review"]))

    def save_review(self, job_id: str, reviewer: str, seconds: float, final: dict[str, object]) -> dict:
        job = self.get_job(job_id)
        if job is None or job["result"] is None:
            raise KeyError(job_id)
        now, changed = time.time(), []
        for name, value in final.items():
            pred = job["result"]["fields"].get(name, {})
            if _differs(pred.get("value"), value):
                changed.append({"job_id": job_id, "doc_type": job["result"]["doc_type"], "field": name,
                                "predicted": pred.get("value"), "corrected": value,
                                "confidence": pred.get("confidence"), "reviewer": reviewer, "ts": now})
        with self._lock:
            self._db.executemany(
                "INSERT INTO corrections (job_id, field, predicted, corrected, confidence, reviewer, created) "
                "VALUES (?,?,?,?,?,?,?)",
                [(c["job_id"], c["field"], json.dumps(c["predicted"]), json.dumps(c["corrected"]),
                  c["confidence"], reviewer, now) for c in changed])
            self._db.execute("INSERT INTO reviews VALUES (?,?,?,?,?,?)",
                             (job_id, reviewer, seconds, len(final), len(changed), now))
            self._db.execute("UPDATE jobs SET reviewed = ? WHERE job_id = ?", (now, job_id))
            self._db.commit()
        if changed:
            CORRECTIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
            with CORRECTIONS_LOG.open("a", encoding="utf-8") as f:
                for c in changed:
                    f.write(json.dumps(c) + "\n")
        return {"job_id": job_id, "fields_reviewed": len(final), "fields_corrected": len(changed)}

    def stats(self) -> dict:
        r = self._exec("SELECT COUNT(*) n, SUM(fields_reviewed) f, SUM(fields_corrected) c, AVG(seconds) s FROM reviews")[0]
        per = self._exec("SELECT reviewer, COUNT(*) docs, SUM(fields_corrected) corrected, AVG(seconds) avg_s "
                         "FROM reviews GROUP BY reviewer ORDER BY docs DESC")
        jobs = self._exec("SELECT status, COUNT(*) n FROM jobs GROUP BY status")
        return {"documents_reviewed": r["n"], "fields_reviewed": r["f"] or 0, "fields_corrected": r["c"] or 0,
                "avg_seconds_per_doc": round(r["s"], 1) if r["s"] else None,
                "queue": len(self.review_queue()), "jobs": {j["status"]: j["n"] for j in jobs},
                "reviewers": [dict(p) for p in per]}


def _differs(pred: object, final: object) -> bool:
    if isinstance(pred, (int, float)) and isinstance(final, (int, float)):
        return abs(float(pred) - float(final)) > 0.005
    return ("" if pred is None else str(pred)).strip() != ("" if final is None else str(final)).strip()


def _job(row: sqlite3.Row) -> dict:
    d = dict(row)
    for k in ("result", "routing"):
        d[k] = json.loads(d[k]) if d[k] else None
    return d

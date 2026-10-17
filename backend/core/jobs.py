# backend/core/jobs.py
# Durable background jobs: state lives in MySQL, uploads on disk until the job ends.
#
# Callers use only this interface (submit / get / set_progress / purge_expired), so a real queue
# can replace the in-process worker pool later. A job created before an app restart is still
# retrievable after it; a job that was running when the app stopped is reported as failed, not
# silently lost.
#
#   core.jobs.register("kind", handler)       handler(job, report) -> result dict
#   job_id = core.jobs.submit("kind", data=bytes, filename="x.pdf", meta={...})
#   core.jobs.get(job_id)                      {'status', 'progress', 'result', 'error', ...}

import json
import logging
import os
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from core.config import data_dir
from db.mysql import execute, query, query_one

log = logging.getLogger(__name__)

DEFAULT_TTL_MINUTES = 60
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
WORKERS = 2

_handlers = {}
_pool = None
_pool_lock = threading.Lock()


def uploads_root():
    return os.path.join(data_dir(), "uploads")


def _job_dir(job_id):
    return os.path.join(uploads_root(), job_id)


def register(kind, handler):
    """Register the function that runs jobs of this kind: handler(job, report) -> JSON-able result."""
    _handlers[kind] = handler


def _get_pool():
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="job")
        return _pool


def shutdown_pool():
    global _pool
    with _pool_lock:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
            _pool = None


class UploadTooLarge(ValueError):
    pass


def create(kind, data=None, filename=None, meta=None, ttl_minutes=DEFAULT_TTL_MINUTES, created_by=None):
    """Store a job row (and its upload, if any) and return its id. Does not run it."""
    if data is not None and len(data) > MAX_UPLOAD_BYTES:
        raise UploadTooLarge(f"upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    job_id = str(uuid.uuid4())
    input_ref = None
    if data is not None:
        os.makedirs(_job_dir(job_id), exist_ok=True)
        safe = os.path.basename(filename or "upload.bin") or "upload.bin"
        input_ref = os.path.join(job_id, safe)
        with open(os.path.join(uploads_root(), input_ref), "wb") as f:
            f.write(data)
    execute(
        "INSERT INTO jobs (id, kind, status, input_ref, meta_json, created_by, expires_at) "
        "VALUES (%s, %s, 'queued', %s, %s, %s, DATE_ADD(NOW(), INTERVAL %s MINUTE))",
        (job_id, kind, input_ref, json.dumps(meta or {}), created_by, int(ttl_minutes)))
    return job_id


def read_input(job):
    """Bytes of the job's upload, or None when it has none (or is already deleted)."""
    if not job.get("input_ref"):
        return None
    path = os.path.join(uploads_root(), job["input_ref"])
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


def _delete_upload(job_id):
    shutil.rmtree(_job_dir(job_id), ignore_errors=True)


def get(job_id):
    """The job as a dict (result and meta decoded), or None."""
    row = query_one("SELECT * FROM jobs WHERE id = %s", (job_id,))
    if not row:
        return None
    row["result"] = json.loads(row["result_json"]) if row.get("result_json") else None
    row["meta"] = json.loads(row["meta_json"]) if row.get("meta_json") else {}
    return row


def set_progress(job_id, text):
    execute("UPDATE jobs SET progress = %s WHERE id = %s", (str(text)[:250], job_id))


def complete(job_id, result):
    execute("UPDATE jobs SET status = 'done', result_json = %s, progress = NULL WHERE id = %s",
            (json.dumps(result), job_id))
    _delete_upload(job_id)


def fail(job_id, error):
    execute("UPDATE jobs SET status = 'failed', error = %s WHERE id = %s", (str(error)[:2000], job_id))
    _delete_upload(job_id)


def _run(job_id):
    job = get(job_id)
    if job is None:
        return
    handler = _handlers.get(job["kind"])
    if handler is None:
        fail(job_id, f"no handler registered for job kind {job['kind']!r}")
        return
    execute("UPDATE jobs SET status = 'running' WHERE id = %s", (job_id,))
    try:
        result = handler(job, lambda text: set_progress(job_id, text))
        complete(job_id, result)
    except Exception as exc:                       # the job records the failure; the worker survives
        log.exception(f"[JOBS] {job['kind']} {job_id} failed")
        fail(job_id, exc)


def submit(kind, **create_kwargs):
    """Create a job and run it on the worker pool. Returns the job id."""
    purge_expired()
    job_id = create(kind, **create_kwargs)
    _get_pool().submit(_run, job_id)
    return job_id


def purge_expired():
    """Delete expired jobs and their uploads. Returns how many were removed."""
    rows = query("SELECT id FROM jobs WHERE expires_at < NOW()")
    for r in rows:
        _delete_upload(r["id"])
    if rows:
        execute("DELETE FROM jobs WHERE expires_at < NOW()")
    return len(rows)


def recover_interrupted():
    """After a restart, jobs left 'running' or 'queued' by the previous process cannot finish."""
    rows = query("SELECT id FROM jobs WHERE status IN ('queued', 'running')")
    for r in rows:
        fail(r["id"], "interrupted by an application restart")
    return len(rows)


def update_meta(job_id, patch):
    """Merge keys into a job's meta (used to attach a confirmation or a follow-up job id)."""
    job = get(job_id)
    if job is None:
        return None
    meta = {**job["meta"], **patch}
    execute("UPDATE jobs SET meta_json = %s WHERE id = %s", (json.dumps(meta), job_id))
    return meta


def delete(job_id):
    """Remove a job and its upload now."""
    _delete_upload(job_id)
    execute("DELETE FROM jobs WHERE id = %s", (job_id,))

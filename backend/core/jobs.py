# backend/core/jobs.py
# Durable background jobs: state lives in MySQL, uploads in storage (core.storage) until the job
# ends, and the work runs wherever core.queue sends it (threads in this process, or separate
# worker processes fed by Kafka). No job state lives in process memory, so any web copy can
# report on a job that any worker is running.
#
#   core.jobs.register("kind", handler)       handler(job, report) -> result dict
#   job_id = core.jobs.submit("kind", data=bytes, filename="x.pdf", meta={...})
#   core.jobs.get(job_id)                      {'status', 'progress', 'result', 'error', ...}
#
# Leases. A job runs only after a worker claims it: one UPDATE sets status 'running', the
# worker's name and a lease expiry. The worker renews the lease while it works. A worker that
# dies stops renewing; once the lease has expired, recover_interrupted() puts the job back in the
# queue (up to MAX_ATTEMPTS tries in all), and another worker claims it. A message delivered
# twice finds the job already claimed or finished and does nothing.

import json
import logging
import os
import socket
import threading
import uuid

from core import config, queue, storage
from db.mysql import execute, query, query_one

log = logging.getLogger(__name__)

DEFAULT_TTL_MINUTES = 60
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ATTEMPTS = 3

_handlers = {}
WORKER_NAME = f"{socket.gethostname()}:{os.getpid()}"


def uploads_root():
    """Local folder of uploads (local storage backend)."""
    return storage.LocalStorage().root("uploads")


def register(kind, handler):
    """Register the function that runs jobs of this kind: handler(job, report) -> JSON-able result."""
    _handlers[kind] = handler


def registered_kinds():
    return sorted(_handlers)


def shutdown_pool():
    queue.shutdown()


class UploadTooLarge(ValueError):
    pass


def create(kind, data=None, filename=None, meta=None, ttl_minutes=DEFAULT_TTL_MINUTES, created_by=None):
    """Store a job row (and its upload, if any) and return its id. Does not run it."""
    if data is not None and len(data) > MAX_UPLOAD_BYTES:
        raise UploadTooLarge(f"upload exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
    job_id = str(uuid.uuid4())
    input_ref = None
    if data is not None:
        safe = os.path.basename((filename or "upload.bin").replace("\\", "/")) or "upload.bin"
        if safe in (".", ".."):
            safe = "upload.bin"
        input_ref = f"{job_id}/{safe}"
        storage.put("uploads", input_ref, data)
    execute(
        "INSERT INTO jobs (id, kind, status, input_ref, meta_json, created_by, expires_at) "
        "VALUES (%s, %s, 'queued', %s, %s, %s, DATE_ADD(NOW(), INTERVAL %s MINUTE))",
        (job_id, kind, input_ref, json.dumps(meta or {}), created_by, int(ttl_minutes)))
    return job_id


def read_input(job):
    """Bytes of the job's upload, or None when it has none (or is already deleted)."""
    if not job.get("input_ref"):
        return None
    return storage.get("uploads", job["input_ref"])


def _delete_upload(job_id):
    try:
        storage.delete_prefix("uploads", job_id)
    except Exception as exc:                  # a storage hiccup must not lose the job's outcome
        log.warning(f"[JOBS] could not delete upload of {job_id}: {exc}")


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


def complete(job_id, result, owner=None):
    """Record the result. With owner, only if that worker still holds the lease. True when recorded."""
    sql = "UPDATE jobs SET status = 'done', result_json = %s, progress = NULL, lease_expires = NULL WHERE id = %s"
    params = [json.dumps(result), job_id]
    if owner:
        sql += " AND lease_owner = %s AND status = 'running'"
        params.append(owner)
    done = execute(sql, tuple(params))
    if done or not owner:
        _delete_upload(job_id)
    return bool(done) or not owner


def fail(job_id, error, owner=None):
    sql = "UPDATE jobs SET status = 'failed', error = %s, lease_expires = NULL WHERE id = %s"
    params = [str(error)[:2000], job_id]
    if owner:
        sql += " AND lease_owner = %s AND status = 'running'"
        params.append(owner)
    done = execute(sql, tuple(params))
    if done or not owner:
        _delete_upload(job_id)
    return bool(done) or not owner


def claim(job_id, owner=WORKER_NAME):
    """Take the job's lease. False when it is finished or another worker holds a live lease."""
    seconds = config.get("JOB_LEASE_SECONDS")
    n = execute(
        "UPDATE jobs SET status = 'running', lease_owner = %s, attempts = attempts + 1, "
        "lease_expires = DATE_ADD(NOW(), INTERVAL %s SECOND) "
        "WHERE id = %s AND (status = 'queued' OR (status = 'running' AND "
        "(lease_expires IS NULL OR lease_expires < NOW())))",
        (owner, int(seconds), job_id))
    return n == 1


def _renew(job_id, owner):
    execute("UPDATE jobs SET lease_expires = DATE_ADD(NOW(), INTERVAL %s SECOND) "
            "WHERE id = %s AND lease_owner = %s AND status = 'running'",
            (int(config.get("JOB_LEASE_SECONDS")), job_id, owner))


def run(job_id, owner=WORKER_NAME):
    """Claim and run one job. Returns 'done', 'failed' or 'not_claimed'."""
    if not claim(job_id, owner):
        return "not_claimed"
    job = get(job_id)
    handler = _handlers.get(job["kind"])
    if handler is None:
        fail(job_id, f"no handler registered for job kind {job['kind']!r}", owner)
        return "failed"
    stop = threading.Event()
    interval = max(1.0, config.get("JOB_LEASE_SECONDS") / 3)

    def heartbeat():
        while not stop.wait(interval):
            try:
                _renew(job_id, owner)
            except Exception as exc:
                log.warning(f"[JOBS] lease renewal for {job_id} failed: {exc}")

    beat = threading.Thread(target=heartbeat, name=f"lease-{job_id[:8]}", daemon=True)
    beat.start()
    try:
        result = handler(job, lambda text: set_progress(job_id, text))
        complete(job_id, result, owner)
        return "done"
    except Exception as exc:                       # the job records the failure; the worker survives
        log.exception(f"[JOBS] {job['kind']} {job_id} failed")
        fail(job_id, exc, owner)
        return "failed"
    finally:
        stop.set()


def submit(kind, **create_kwargs):
    """Create a job and hand it to the queue. Returns the job id."""
    purge_expired()
    job_id = create(kind, **create_kwargs)
    queue.dispatch(kind, job_id, run)
    return job_id


def purge_expired():
    """Delete expired jobs and their uploads. Returns how many were removed."""
    rows = query("SELECT id FROM jobs WHERE expires_at < NOW()")
    for r in rows:
        _delete_upload(r["id"])
    if rows:
        execute("DELETE FROM jobs WHERE expires_at < NOW()")
    return len(rows)


def recover_interrupted(dispatch=True, startup=False):
    """Requeue jobs nobody is working on: queued for longer than a lease, or running with an
    expired lease.

    startup: the inline-mode process has just started, so every queued or running job belongs
    to a process that no longer exists, whatever its lease says.
    A job that has already been tried MAX_ATTEMPTS times is failed instead, so a job that kills
    its worker cannot loop forever. Returns (requeued, failed).
    """
    if startup:
        rows = query("SELECT id, kind, attempts FROM jobs WHERE status IN ('queued', 'running')")
    else:
        stale = int(config.get("JOB_LEASE_SECONDS"))
        rows = query("SELECT id, kind, attempts FROM jobs WHERE "
                     "(status = 'queued' AND created_at < DATE_SUB(NOW(), INTERVAL %s SECOND)) OR "
                     "(status = 'running' AND (lease_expires IS NULL OR lease_expires < NOW()))", (stale,))
    requeued = failed = 0
    for r in rows:
        if (r.get("attempts") or 0) >= MAX_ATTEMPTS:
            if fail(r["id"], f"interrupted {r['attempts']} times (worker or application restarts); not retried"):
                failed += 1
            continue
        n = execute("UPDATE jobs SET status = 'queued', lease_owner = NULL, lease_expires = NULL "
                    "WHERE id = %s AND (status = 'queued' OR (status = 'running' AND "
                    "(%s OR lease_expires IS NULL OR lease_expires < NOW())))", (r["id"], bool(startup)))
        if n and dispatch:
            queue.dispatch(r["kind"], r["id"], run)
        requeued += 1 if n else 0
    return requeued, failed


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

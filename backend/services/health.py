# backend/services/health.py
# Service liveness: server, ML model name and MySQL connectivity.

from db.mysql import ping


def health():
    """Health check endpoint checking server, ML model status, and MySQL database connectivity."""
    from ml.ner import MODEL_NAME
    db_status = "not_connected"
    try:
        p = ping()
        db_status = f"connected ({p.get('database')}, {p.get('table_count', 0)} tables)"
    except Exception as e:
        db_status = f"unavailable ({e})"

    return {
        "status": "ok",
        "database": db_status,
        "model": MODEL_NAME
    }


# ── load-balancer probes ─────────────────────────────────────────────────────
def live():
    """The process is up and serving requests. Checks nothing else, so a slow database never
    gets a healthy copy restarted."""
    return {"status": "live"}, 200


OPTIONAL_BUDGET_SECONDS = 0.5


def _check(fn):
    try:
        detail = fn()
        return {"ok": True, "detail": detail}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)[:200]}


def _check_within(fn, seconds):
    """_check with a time limit (a DNS lookup cannot be bounded by a socket timeout)."""
    import threading
    box = {}
    t = threading.Thread(target=lambda: box.update(result=_check(fn)), daemon=True)
    t.start()
    t.join(seconds)
    return box.get("result") or {"ok": False, "detail": f"no answer within {seconds} s"}


def ready():
    """Whether this copy can serve: each dependency with ok/detail, and whether it is required.

    Required: MySQL (jobs, caches), storage, the FAERS database, and Kafka when jobs are queued
    on it. Optional components (HBase) are reported but do not take the copy out of rotation.
    """
    import os

    from core import config, queue, storage
    from faers.registry import database_path

    def mysql():
        p = ping()
        return f"{p.get('database')} ({p.get('table_count', 0)} tables)"

    def store():
        b = storage.backend()
        if b.name == "s3":
            b.client.head_bucket(Bucket=b.bucket)
            return f"s3 bucket {b.bucket}"
        root = b.root("uploads")
        os.makedirs(root, exist_ok=True)
        if not os.access(root, os.W_OK):
            raise PermissionError(f"{root} is not writable")
        return "local"

    def faers_db():
        path = database_path()
        if not os.path.exists(path):
            raise FileNotFoundError("FAERS database not loaded")
        return f"{os.path.getsize(path) // 1_000_000} MB"

    def kafka():
        from services.pipeline import broker_reachable
        if not broker_reachable(timeout=0.5):
            raise ConnectionError(f"no broker at {config.get('KAFKA_BOOTSTRAP_SERVERS')}")
        return "reachable"

    def hbase():                        # a TCP probe: readiness must answer in well under a second
        import socket
        host, port = config.get("HBASE_HOST"), config.get("HBASE_PORT")
        try:
            socket.create_connection((host, port), timeout=0.5).close()
        except OSError as exc:
            raise ConnectionError(f"HBase Thrift server not reachable at {host}:{port}") from exc
        return "reachable"

    checks = {"mysql": (mysql, True), "storage": (store, True), "faers_database": (faers_db, True),
              "kafka": (kafka, queue.mode() == "kafka"), "hbase": (hbase, False)}
    components = {}
    for name, (fn, required) in checks.items():          # an optional check never slows the probe
        result = _check(fn) if required else _check_within(fn, OPTIONAL_BUDGET_SECONDS)
        components[name] = {**result, "required": required}
    ok = all(c["ok"] for c in components.values() if c["required"])
    return {"status": "ready" if ok else "not_ready", "queue_mode": queue.mode(),
            "storage": config.get("STORAGE_BACKEND"), "components": components}, 200 if ok else 503

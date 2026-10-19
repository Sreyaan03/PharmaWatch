"""
Web copies keep no state of their own: a job taken by one process is run by another and read back
by the first, and a cache written by one process is served to another. Uses two real processes
and the shared MySQL database and storage, exactly as two machines would.
"""

import io
import os
import subprocess
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)


def _mysql_ready():
    try:
        from db.mysql import query_one
        return query_one("SELECT COUNT(*) AS n FROM jobs WHERE lease_owner IS NULL") is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mysql_ready(), reason="MySQL with the migrated schema is not reachable")


def _other_process(code, env):
    """Run Python in a separate process (another 'machine') sharing only MySQL and storage."""
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True,
                         text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout.strip().splitlines()[-1]


@pytest.fixture
def shared_env(tmp_path, monkeypatch):
    env = {**os.environ, "DATA_DIR": str(tmp_path), "STORAGE_BACKEND": "local", "QUEUE_MODE": "kafka",
           "PYTHONIOENCODING": "utf-8"}
    for k in ("DATA_DIR", "STORAGE_BACKEND", "QUEUE_MODE"):
        monkeypatch.setenv(k, env[k])
    return env


def test_a_job_uploaded_here_is_run_by_another_process_and_read_back_here(shared_env, monkeypatch):
    from app import app
    from core import queue
    published = []
    monkeypatch.setattr(queue, "dispatch", lambda kind, job_id, runner: published.append((kind, job_id)))
    client = app.test_client()
    r = client.post("/api/prescription", data={"file": (io.BytesIO(b"Tab Warfarin 5 mg OD\nTab Aspirin 75 mg OD"), "rx.txt")},
                    content_type="multipart/form-data")
    assert r.status_code == 202
    job_id = r.get_json()["job_id"]
    assert published == [("rx_extract", job_id)]
    assert client.get(f"/api/prescription/{job_id}").get_json()["status"] == "queued"   # nothing ran here

    outcome = _other_process(
        "from workers.run import load_handlers, handle_message\n"
        "load_handlers()\n"
        f"print(handle_message({{'job_id': '{job_id}'}}, owner='worker-on-another-machine'))", shared_env)
    assert outcome == "done"

    body = client.get(f"/api/prescription/{job_id}").get_json()
    assert body["status"] == "done" and len(body["extraction"]["lines"]) == 2
    from core import jobs
    assert jobs.get(job_id)["lease_owner"] == "worker-on-another-machine"
    assert not os.path.exists(os.path.join(shared_env["DATA_DIR"], "uploads", job_id))   # upload removed


def test_a_cache_written_by_one_process_is_served_to_another(shared_env):
    from core.cache import PayloadCache
    from db.mysql import execute
    cache = PayloadCache("temporal_cache", "data_type", "statelessness-test")
    key = ("zz-test-drug", "zz-test-event", "statelessness")
    execute("DELETE FROM temporal_cache WHERE drug = %s", (key[0],))
    try:
        _other_process(
            "from core.cache import PayloadCache\n"
            "PayloadCache('temporal_cache', 'data_type', 'other').set("
            f"{key[0]!r}, {key[1]!r}, {key[2]!r}, {{'written_by': 'other process'}}, version='v1')\n"
            "print('ok')", shared_env)
        assert cache.get(*key, version="v1") == {"written_by": "other process"}
    finally:
        execute("DELETE FROM temporal_cache WHERE drug = %s", (key[0],))


def test_the_state_audit_is_written_down():
    """Every module-level cache is listed in docs/SCALING.md with whether it is safe to duplicate."""
    import ast
    doc = open(os.path.join(os.path.dirname(BACKEND), "docs", "SCALING.md"), encoding="utf-8").read()
    missing = []
    for root, dirs, files in os.walk(BACKEND):
        dirs[:] = [d for d in dirs if d not in {"tests", "__pycache__", "trained_models", "http_cache", "validation"}]
        for f in files:
            if not f.endswith(".py"):
                continue
            path = os.path.join(root, f)
            for node in ast.parse(open(path, encoding="utf-8").read()).body:
                if isinstance(node, ast.Assign):
                    for t in node.targets:
                        name = getattr(t, "id", "")
                        if name.startswith("_") and not name.startswith("__") and not name.isupper() and isinstance(
                                node.value, (ast.Dict, ast.List, ast.Set, ast.Constant)) and \
                                (not isinstance(node.value, ast.Constant) or node.value.value is None):
                            rel = os.path.relpath(path, BACKEND).replace(os.sep, "/")
                            if f"`{rel}` `{name}`" not in doc:
                                missing.append(f"{rel} {name}")
    assert not missing, f"add to the state audit in docs/SCALING.md: {missing}"


def test_readiness_reports_each_dependency(shared_env):
    from app import app
    r = app.test_client().get("/health/ready")
    body = r.get_json()
    assert set(body["components"]) == {"mysql", "storage", "faers_database", "kafka", "hbase"}
    assert body["components"]["kafka"]["required"] is True          # QUEUE_MODE=kafka here
    assert r.status_code == (200 if body["status"] == "ready" else 503)
    assert app.test_client().get("/health/live").status_code == 200

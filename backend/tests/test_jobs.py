"""Durable background jobs: state in MySQL, uploads on disk only while a job is live."""

import os
import sys
import time

import pytest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core import jobs  # noqa: E402
from db import mysql  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_uploads(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    yield
    jobs.shutdown_pool()


def _wait(job_id, statuses=("done", "failed"), timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        j = jobs.get(job_id)
        if j and j["status"] in statuses:
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {jobs.get(job_id)}")


def _upload_dir(job_id):
    return os.path.join(jobs.uploads_root(), job_id)


def test_a_job_survives_losing_every_database_connection():
    """Stands in for an app restart: nothing about the job lives in process memory."""
    job_id = jobs.create("t_persist", meta={"k": 1})
    mysql.reset_pool()
    j = jobs.get(job_id)
    assert j is not None and j["status"] == "queued" and j["meta"] == {"k": 1}


def test_a_finished_job_keeps_its_result_and_leaves_no_upload_on_disk():
    seen = {}

    def handler(job, report):
        seen["bytes"] = jobs.read_input(job)
        report("working")
        return {"echo": len(seen["bytes"])}

    jobs.register("t_ok", handler)
    job_id = jobs.submit("t_ok", data=b"hello world", filename="rx.pdf")
    j = _wait(job_id)
    assert j["status"] == "done" and j["result"] == {"echo": 11} and seen["bytes"] == b"hello world"
    assert not os.path.exists(_upload_dir(job_id)), "upload bytes remain on disk after completion"


def test_a_failing_job_is_recorded_and_its_upload_removed():
    def handler(job, report):
        raise RuntimeError("boom")

    jobs.register("t_fail", handler)
    job_id = jobs.submit("t_fail", data=b"x", filename="a.txt")
    j = _wait(job_id)
    assert j["status"] == "failed" and "boom" in j["error"]
    assert not os.path.exists(_upload_dir(job_id))


def test_an_unknown_kind_fails_instead_of_hanging():
    job_id = jobs.submit("t_nobody_handles_this")
    j = _wait(job_id)
    assert j["status"] == "failed" and "no handler" in j["error"]


def test_oversize_uploads_are_refused_before_anything_is_stored(tmp_path):
    with pytest.raises(jobs.UploadTooLarge):
        jobs.create("t_big", data=b"0" * (jobs.MAX_UPLOAD_BYTES + 1), filename="big.pdf")
    assert not os.path.exists(jobs.uploads_root()) or os.listdir(jobs.uploads_root()) == []


def test_filenames_cannot_escape_the_upload_folder():
    job_id = jobs.create("t_path", data=b"x", filename="../../evil.txt")
    j = jobs.get(job_id)
    assert os.path.dirname(os.path.join(jobs.uploads_root(), j["input_ref"])) == _upload_dir(job_id)
    jobs.fail(job_id, "cleanup")


def test_expired_jobs_and_their_files_are_purged():
    job_id = jobs.create("t_expire", data=b"x", filename="a.txt", ttl_minutes=-5)
    assert os.path.exists(_upload_dir(job_id))
    assert jobs.purge_expired() >= 1
    assert jobs.get(job_id) is None and not os.path.exists(_upload_dir(job_id))


def test_jobs_interrupted_by_a_restart_are_reported_failed_not_lost():
    job_id = jobs.create("t_interrupt", data=b"x", filename="a.txt")
    mysql.execute("UPDATE jobs SET status = 'running' WHERE id = %s", (job_id,))
    assert jobs.recover_interrupted() >= 1
    j = jobs.get(job_id)
    assert j["status"] == "failed" and "restart" in j["error"] and not os.path.exists(_upload_dir(job_id))


def test_the_in_memory_task_dict_is_gone():
    import importlib.util
    from services import signals
    assert not hasattr(signals, "MINING_TASKS")
    assert importlib.util.find_spec("services.text_mining") is None


# ── the NER mining endpoints still work, now on jobs ─────────────────────────
def test_ner_mining_endpoints_run_through_a_job(monkeypatch):
    from app import app
    from services import signals

    monkeypatch.setattr(signals, "run_ner", lambda text: [
        {"token": "Metformin", "label": "Medication", "score": 0.9, "start": 0, "end": 9},
        {"token": "nausea", "label": "Sign_symptom", "score": 0.8, "start": 20, "end": 26}])
    monkeypatch.setattr(signals, "compute_disproportionality_helper",
                        lambda d, e, source=None: {"a": 5, "prr": 2.5, "signal_strength": "signal"})
    monkeypatch.setattr(signals, "upsert_signal", lambda *a, **k: None)
    monkeypatch.setattr(signals, "insert_drug_event", lambda *a, **k: None)
    app.config["TESTING"] = True
    client = app.test_client()

    r = client.post("/api/signals/ner-mine", data={"text": "Metformin caused nausea"})
    assert r.status_code == 200
    task_id = r.get_json()["task_id"]
    body = client.get(f"/api/signals/ner-mine/stream/{task_id}").get_data(as_text=True)
    assert "event: done" in body and '"drug": "Metformin"' in body
    assert client.get("/api/signals/ner-mine/stream/does-not-exist").status_code == 404


def test_unsupported_and_oversize_uploads_are_rejected(monkeypatch):
    import io
    from app import app
    app.config["TESTING"] = True
    client = app.test_client()
    r = client.post("/api/signals/ner-mine", data={"file": (io.BytesIO(b"x"), "evil.exe")},
                    content_type="multipart/form-data")
    assert r.status_code == 400
    r = client.post("/api/signals/ner-mine", data={"file": (io.BytesIO(b"0" * (jobs.MAX_UPLOAD_BYTES + 1)), "a.pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 413

"""
The job queue: inline (threads in the web process) and kafka (separate workers) give the same
results; a job runs once however often its message arrives; a dead worker's job is finished by
another; a job that keeps killing workers stops being retried.
"""

import os
import sys
import threading
import time

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from core import jobs, queue  # noqa: E402


def _mysql_ready():
    try:
        from db.mysql import query_one
        return query_one("SELECT COUNT(*) AS n FROM jobs WHERE lease_owner IS NULL") is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _mysql_ready(), reason="MySQL with the migrated jobs table is not reachable")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.delenv("QUEUE_MODE", raising=False)
    yield
    queue.shutdown()


def _wait(job_id, statuses=("done", "failed"), timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        j = jobs.get(job_id)
        if j and j["status"] in statuses:
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {jobs.get(job_id)}")


def _expire(job_id):
    from db.mysql import execute
    execute("UPDATE jobs SET lease_expires = DATE_SUB(NOW(), INTERVAL 1 SECOND) WHERE id = %s", (job_id,))


class FakeKafka:
    """Stands in for the broker: dispatch() publishes here, workers read from here."""
    def __init__(self):
        self.messages = []

    def send(self, topic, key, value):
        self.messages.append((topic, value))

        class Done:
            def get(self, timeout=None):
                return None
        return Done()


@pytest.fixture
def kafka_mode(monkeypatch):
    broker = FakeKafka()
    monkeypatch.setenv("QUEUE_MODE", "kafka")
    monkeypatch.setattr(queue, "ensure_topics", lambda kinds: None)
    monkeypatch.setattr(queue, "_get_producer", lambda: broker)
    return broker


def _register_echo(calls=None):
    def handler(job, report):
        if calls is not None:
            calls.append(job["id"])
        report("working")
        return {"bytes": len(jobs.read_input(job) or b""), "meta": job["meta"]}
    jobs.register("t_echo", handler)


def test_inline_and_kafka_workers_give_the_same_result(kafka_mode, monkeypatch):
    from workers.run import handle_message
    _register_echo()
    kafka_job = jobs.submit("t_echo", data=b"hello", filename="a.txt", meta={"k": 1})
    topic, msg = kafka_mode.messages[-1]
    assert topic == "jobs.t_echo" and jobs.get(kafka_job)["status"] == "queued"   # nothing ran in the web process
    assert handle_message(msg, owner="worker-1") == "done"

    monkeypatch.setenv("QUEUE_MODE", "inline")
    inline_job = jobs.submit("t_echo", data=b"hello", filename="a.txt", meta={"k": 1})
    assert _wait(inline_job)["result"] == jobs.get(kafka_job)["result"] == {"bytes": 5, "meta": {"k": 1}}
    assert jobs.get(kafka_job)["lease_owner"] == "worker-1"


def test_a_message_delivered_twice_runs_the_job_once(kafka_mode):
    from workers.run import handle_message
    calls = []
    _register_echo(calls)
    job_id = jobs.submit("t_echo")
    msg = kafka_mode.messages[-1][1]
    assert handle_message(msg, owner="w1") == "done"
    assert handle_message(msg, owner="w2") == "not_claimed"
    assert calls == [job_id]


def test_a_dead_workers_job_is_finished_by_another_and_the_late_result_is_ignored(kafka_mode):
    _register_echo()
    job_id = jobs.submit("t_echo", data=b"abc", filename="a.txt")
    assert jobs.claim(job_id, owner="dead-worker")           # took the job, then died
    assert not jobs.claim(job_id, owner="other")             # its lease is still live
    _expire(job_id)
    requeued, failed = jobs.recover_interrupted()            # the workers' sweeper
    assert (requeued, failed) == (1, 0) and jobs.get(job_id)["status"] == "queued"
    assert kafka_mode.messages[-1][1]["job_id"] == job_id    # republished
    assert jobs.run(job_id, owner="other") == "done"
    assert jobs.complete(job_id, {"stale": True}, owner="dead-worker") is False
    assert jobs.get(job_id)["result"] == {"bytes": 3, "meta": {}}


def test_a_job_that_keeps_killing_workers_is_failed_not_retried_forever(kafka_mode):
    _register_echo()
    job_id = jobs.submit("t_echo")
    for attempt in range(jobs.MAX_ATTEMPTS):
        assert jobs.claim(job_id, owner=f"w{attempt}")
        _expire(job_id)
        jobs.recover_interrupted(dispatch=False)
    j = jobs.get(job_id)
    assert j["status"] == "failed" and "interrupted 3 times" in j["error"]


def test_a_running_job_keeps_its_lease_while_it_works(kafka_mode, monkeypatch):
    monkeypatch.setenv("JOB_LEASE_SECONDS", "3")
    started, release = threading.Event(), threading.Event()

    def slow(job, report):
        started.set()
        release.wait(10)
        return {"ok": True}
    jobs.register("t_slow", slow)
    job_id = jobs.submit("t_slow")
    t = threading.Thread(target=jobs.run, args=(job_id, "busy-worker"))
    t.start()
    started.wait(5)
    time.sleep(4)                                            # longer than the lease: the heartbeat renews it
    assert not jobs.claim(job_id, owner="thief")
    assert jobs.recover_interrupted(dispatch=False) == (0, 0)
    release.set()
    t.join(10)
    assert jobs.get(job_id)["status"] == "done"


def test_inline_startup_requeues_what_the_previous_process_left(monkeypatch):
    from db.mysql import execute
    _register_echo()
    job_id = jobs.create("t_echo", data=b"xy", filename="a.txt")
    execute("UPDATE jobs SET status = 'running', lease_owner = 'old-process', "
            "lease_expires = DATE_ADD(NOW(), INTERVAL 1 HOUR) WHERE id = %s", (job_id,))
    requeued, _ = jobs.recover_interrupted(startup=True)
    assert requeued >= 1
    assert _wait(job_id)["result"] == {"bytes": 2, "meta": {}}


def test_queue_mode_must_be_known(monkeypatch):
    monkeypatch.setenv("QUEUE_MODE", "carrier-pigeon")
    with pytest.raises(ValueError):
        queue.mode()


def test_workers_refuse_to_start_in_inline_mode():
    from workers.run import main
    assert main([]) == 2

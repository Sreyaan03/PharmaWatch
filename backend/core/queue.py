# backend/core/queue.py
# Where background jobs run, chosen by the QUEUE_MODE setting:
#
#   inline  a thread pool inside the process that submitted the job (the default; one machine,
#           nothing else to run)
#   kafka   the job id is published to the topic jobs.<kind>; worker processes
#           (python -m workers.run) in one consumer group take jobs from it. Adding workers adds
#           throughput, up to JOB_TOPIC_PARTITIONS per kind, with no code change.
#
# Either way the job's state lives in the jobs table, and a job runs only after a worker claims
# its lease there (core.jobs), so a message delivered twice never runs a job twice.

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

from core import config

log = logging.getLogger(__name__)

TOPIC_PREFIX = "jobs."

_pool = None
_producer = None
_known_topics = set()
_lock = threading.Lock()


def mode():
    value = (config.get("QUEUE_MODE") or "inline").lower()
    if value not in ("inline", "kafka"):
        raise ValueError(f"QUEUE_MODE must be 'inline' or 'kafka', not {value!r}")
    return value


def topic_for(kind):
    return TOPIC_PREFIX + kind


def _get_pool():
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=config.get("JOB_THREADS"), thread_name_prefix="job")
        return _pool


def shutdown():
    """Stop the inline pool and close the producer (tests, process exit)."""
    global _pool, _producer
    with _lock:
        if _pool is not None:
            _pool.shutdown(wait=False, cancel_futures=True)
            _pool = None
        if _producer is not None:
            try:
                _producer.close(timeout=2)
            except Exception:
                pass
            _producer = None


def ensure_topics(kinds):
    """Create jobs.<kind> topics with JOB_TOPIC_PARTITIONS partitions (existing ones are left alone)."""
    from kafka.admin import KafkaAdminClient, NewTopic
    from kafka.errors import TopicAlreadyExistsError
    wanted = [topic_for(k) for k in kinds if topic_for(k) not in _known_topics]
    if not wanted:
        return
    admin = KafkaAdminClient(bootstrap_servers=config.get("KAFKA_BOOTSTRAP_SERVERS"), client_id="pharmawatch-jobs")
    try:
        existing = set(admin.list_topics())
        new = [NewTopic(t, num_partitions=config.get("JOB_TOPIC_PARTITIONS"), replication_factor=1)
               for t in wanted if t not in existing]
        if new:
            try:
                admin.create_topics(new)
            except TopicAlreadyExistsError:
                pass
        _known_topics.update(wanted)
    finally:
        admin.close()


def _get_producer():
    global _producer
    with _lock:
        if _producer is None:
            from kafka import KafkaProducer
            _producer = KafkaProducer(bootstrap_servers=config.get("KAFKA_BOOTSTRAP_SERVERS"),
                                      value_serializer=lambda v: json.dumps(v).encode("utf-8"),
                                      key_serializer=lambda k: k.encode("utf-8"),
                                      acks="all", retries=3, max_block_ms=10000)
        return _producer


def dispatch(kind, job_id, runner):
    """Hand a stored job to whatever runs jobs. runner(job_id) is used in inline mode."""
    if mode() == "inline":
        _get_pool().submit(runner, job_id)
        return "inline"
    ensure_topics([kind])
    _get_producer().send(topic_for(kind), key=job_id, value={"job_id": job_id, "kind": kind}).get(timeout=10)
    return "kafka"

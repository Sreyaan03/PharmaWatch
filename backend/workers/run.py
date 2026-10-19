# backend/workers/run.py
# A worker process for QUEUE_MODE=kafka: takes job ids from the jobs.<kind> topics and runs them
# with the same handlers the web app uses inline. Start as many as the load needs; Kafka shares
# the partitions of each topic among the workers of the group.
#
#   python -m workers.run                          # every kind of job
#   python -m workers.run --kinds rx_extract rx_review
#
# The offset is committed after the job has finished (or was already taken), so a worker killed
# mid-job leaves its message to be delivered again; the job's lease (core.jobs) decides who runs
# it. A sweeper requeues jobs whose lease has expired.

import argparse
import json
import logging
import sys
import threading

from core import config, jobs, queue

log = logging.getLogger("worker")

GROUP_ID = "pharmawatch-workers"


def load_handlers():
    """Import the modules that register job handlers (the same ones the web app imports)."""
    import services.prescription.service  # noqa: F401  rx_extract, rx_review
    import services.signals  # noqa: F401  ner_mining
    from faers import pipeline as faers_pipeline
    faers_pipeline.register_job()  # faers_refresh
    return jobs.registered_kinds()


def handle_message(value, owner=jobs.WORKER_NAME):
    """Run the job named in one message. Returns what jobs.run reports, or 'ignored'."""
    job_id = (value or {}).get("job_id")
    if not job_id:
        return "ignored"
    return jobs.run(job_id, owner)


def sweep_forever(stop, interval):
    while not stop.wait(interval):
        try:
            requeued, failed = jobs.recover_interrupted()
            if requeued or failed:
                log.info(f"sweeper: requeued {requeued}, failed {failed}")
        except Exception as exc:
            log.warning(f"sweeper: {exc}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run PharmaWatch background jobs from Kafka.")
    parser.add_argument("--kinds", nargs="+", help="job kinds to take (default: all)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [WORKER] %(levelname)s %(message)s")
    if queue.mode() != "kafka":
        print("[-] workers run only with QUEUE_MODE=kafka; in inline mode the web app runs jobs itself.",
              file=sys.stderr)
        return 2

    threads = config.get("TORCH_THREADS")
    if threads > 0:                       # several workers on one machine: share the cores, do not oversubscribe
        import torch
        torch.set_num_threads(threads)
    kinds = args.kinds or load_handlers()
    if args.kinds:
        load_handlers()
    queue.ensure_topics(kinds)

    from kafka import KafkaConsumer
    consumer = KafkaConsumer(
        *[queue.topic_for(k) for k in kinds],
        bootstrap_servers=config.get("KAFKA_BOOTSTRAP_SERVERS"), group_id=GROUP_ID,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        enable_auto_commit=False, auto_offset_reset="earliest",
        max_poll_records=1, max_poll_interval_ms=30 * 60 * 1000,
        session_timeout_ms=10000, heartbeat_interval_ms=3000)   # a dead worker's partitions move within ~10 s
    stop = threading.Event()
    threading.Thread(target=sweep_forever, args=(stop, config.get("JOB_LEASE_SECONDS")),
                     name="sweeper", daemon=True).start()
    log.info(f"{jobs.WORKER_NAME} taking {kinds}")
    try:
        for record in consumer:
            try:
                outcome = handle_message(record.value)
                log.info(f"{record.topic} {record.value.get('job_id')}: {outcome}")
            except Exception as exc:              # the job store is down: leave the message for later
                log.error(f"{record.topic} offset {record.offset}: {exc}")
                continue
            consumer.commit()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        consumer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
ingest/consumer.py — PharmaWatch Kafka consumer.

Delivers new evidence and announces new data. It computes no statistics: disproportionality
comes only from the FAERS batch path (faers/), on the full database.

  faers-release    → faers_releases (a newer FAERS quarter is available; the refresh runs as a job)
  pubmed-stream    → kafka_pubmed  + evidence_links (paper → drugs and terms named in its title)
  clinical-trials  → kafka_trials  + evidence_links (trial → the drug it studies)
  fda-alerts       → kafka_alerts  + evidence_links (recall → drugs named in the product)

Every write is an upsert on a natural key, so a message delivered twice changes nothing.
"""

import json
import logging
import os
import sys
import time

from dotenv import load_dotenv

# Run from the backend directory: `python -m ingest.consumer`
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
load_dotenv(os.path.join(backend_dir, ".env"))

from db.mysql import execute, ping  # noqa: E402
from faers import pipeline as faers_pipeline  # noqa: E402
from faers.mentions import MentionIndex  # noqa: E402
from faers.registry import database_path  # noqa: E402

KAFKA_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CONSUMER] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("consumer")

_msg_count_since_purge = 0
_index = None


def init_kafka_tables():
    """Verify MySQL connectivity and database readiness."""
    try:
        status = ping()
        log.info(f"Connected to MySQL database '{status.get('database')}' ({status.get('table_count')} tables).")
    except Exception as e:
        log.warning(f"MySQL check returned: {e}. Ensure the MySQL container is running.")


def mention_index():
    """Drug and term names from the FAERS identity tables, loaded once (empty without a database)."""
    global _index
    if _index is None:
        _index = MentionIndex.from_database(database_path())
        log.info(f"Mention index: {len(_index.drugs):,} drug names, {len(_index.pts):,} terms")
    return _index


def reset_mention_index():
    global _index
    _index = None


def record_pipeline_stat(topic, partition=0, offset=None):
    """One row per delivered message; a redelivered message (same topic, partition, offset) is ignored."""
    global _msg_count_since_purge
    try:
        execute(
            "INSERT IGNORE INTO kafka_pipeline_stats (topic, partition_id, messages_in, last_offset, recorded_at) "
            "VALUES (%s, %s, 1, %s, NOW())",
            (topic, int(partition or 0), str(offset) if offset is not None else None))
        _msg_count_since_purge += 1
        if _msg_count_since_purge >= 500:            # keep the table bounded
            execute("DELETE FROM kafka_pipeline_stats WHERE recorded_at < NOW() - INTERVAL 7 DAY")
            _msg_count_since_purge = 0
    except Exception as e:
        log.error(f"Failed to record pipeline stat: {e}")


# ── evidence links ────────────────────────────────────────────────────────────
def link_evidence(kind, ref_id, text, title=None, url=None, published=None, drug_hint=None, find_terms=False):
    """Attach one item of external evidence to each drug it names. Returns the number of links written.

    drug_hint: the drug the source was queried for (trials), matched like any other name.
    find_terms: also attach to Preferred Terms named in the text (papers). Trial titles name the
    condition treated, and recall texts name defects, so neither is searched for terms.
    """
    index = mention_index()
    drugs = index.drugs_in(drug_hint) if drug_hint else []
    seen = {d["rxcui"] for d in drugs}
    drugs += [d for d in index.drugs_in(text) if d["rxcui"] not in seen]
    terms = index.pts_in(text) if find_terms else []
    written = 0
    for d in drugs:
        for term in (terms or [{"pt": "", "matched": None}]):
            matched = d["matched"] + (f" / {term['matched']}" if term["matched"] else "")
            execute(
                "INSERT INTO evidence_links (rxcui, ingredient, pt, kind, ref_id, title, url, published, matched_text) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE ingredient = VALUES(ingredient), title = VALUES(title), url = VALUES(url), "
                "published = VALUES(published), matched_text = VALUES(matched_text)",
                (d["rxcui"], d["ingredient"], term["pt"], kind, str(ref_id)[:191], (title or text or "")[:2000],
                 url, (published or "")[:64], matched[:191]))
            written += 1
    return written


# ── handlers ──────────────────────────────────────────────────────────────────
def handle_release(msg):
    """A newer FAERS quarter is available. Record it; refresh only if auto-refresh is on."""
    quarter = msg.get("quarter", "")
    if not quarter:
        return "ignored"
    try:
        quarter = faers_pipeline.normalize_quarter(quarter)
        status = faers_pipeline.record_release(quarter)
    except ValueError:
        log.warning(f"faers-release with an invalid quarter: {quarter!r}")
        return "ignored"
    if status == "recorded" and faers_pipeline.auto_refresh_enabled():
        job_id = faers_pipeline.start_refresh(quarter, created_by="kafka-consumer")
        log.info(f"FAERS {quarter}: refresh job {job_id} started (FAERS_AUTO_REFRESH on)")
        return "refreshing"
    log.info(f"FAERS {quarter}: {status}")
    return status


def handle_pubmed(msg):
    pmid = msg.get("pmid", "")
    title = msg.get("title", "")
    if not pmid:
        return
    execute(
        "INSERT INTO kafka_pubmed (pmid, title, journal, pubdate, query, ingested_at) "
        "VALUES (%s, %s, %s, %s, %s, NOW()) "
        "ON DUPLICATE KEY UPDATE title = VALUES(title), journal = VALUES(journal)",
        (pmid, title, msg.get("journal", ""), msg.get("pubdate", ""), msg.get("query", "")))
    link_evidence("paper", pmid, title, url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                  published=msg.get("pubdate", ""), find_terms=True)


def handle_trials(msg):
    nct_id = msg.get("nct_id", "")
    title = msg.get("title", "")
    if not nct_id:
        return
    execute(
        "INSERT INTO kafka_trials (nct_id, drug, title, phase, status, ingested_at) "
        "VALUES (%s, %s, %s, %s, %s, NOW()) "
        "ON DUPLICATE KEY UPDATE title = VALUES(title), status = VALUES(status)",
        (nct_id, msg.get("drug", ""), title, json.dumps(msg.get("phase", [])), msg.get("status", "")))
    link_evidence("trial", nct_id, title, url=f"https://clinicaltrials.gov/study/{nct_id}",
                  drug_hint=msg.get("drug", ""))


def handle_alerts(msg):
    if msg.get("source") == "fda_enforcement":
        key = msg.get("recall_num", "")
        title = msg.get("title", "")
        link = ""
        summary = msg.get("product", "")
        pub = msg.get("report_date", "")
        feed = f"class:{msg.get('class', '')} firm:{msg.get('firm', '')}"
    else:
        key = msg.get("title", "")[:80]
        title = msg.get("title", "")
        link = msg.get("link", "")
        summary = msg.get("summary", "")
        pub = msg.get("published", "")
        feed = msg.get("feed_url", "")
    if not title or not key:
        return
    execute(
        "INSERT INTO kafka_alerts (alert_key, title, link, summary, published, feed_url, ingested_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, NOW()) "
        "ON DUPLICATE KEY UPDATE title = VALUES(title), summary = VALUES(summary)",
        (key, title, link, summary, pub, feed))
    link_evidence("alert", key, f"{summary} {title}", title=title, url=link or None, published=pub)


TOPIC_HANDLERS = {
    "faers-release": handle_release,
    "pubmed-stream": handle_pubmed,
    "clinical-trials": handle_trials,
    "fda-alerts": handle_alerts,
}


def handle_record(topic, partition, offset, msg):
    """Run the handler for one delivered message and record it. Returns False for an unknown topic."""
    handler = TOPIC_HANDLERS.get(topic)
    if handler is None:
        return False
    handler(msg)
    record_pipeline_stat(topic, partition, offset)
    return True


def create_consumer(retries=10, delay=5):
    """Create Kafka consumer with retry logic."""
    from kafka import KafkaConsumer
    from kafka.errors import KafkaError
    for attempt in range(1, retries + 1):
        try:
            consumer = KafkaConsumer(
                *TOPIC_HANDLERS.keys(),
                bootstrap_servers=KAFKA_SERVERS,
                value_deserializer=lambda v: json.loads(v.decode("utf-8")),
                group_id="pharmawatch-consumer-group",
                auto_offset_reset="earliest",
                enable_auto_commit=True,
                auto_commit_interval_ms=5000,
                session_timeout_ms=30000,
                max_poll_records=50,
            )
            log.info(f"Consumer connected to Kafka at {KAFKA_SERVERS}; topics {list(TOPIC_HANDLERS)}")
            return consumer
        except KafkaError:
            log.warning(f"Kafka not ready (attempt {attempt}/{retries}), retrying in {delay}s...")
            time.sleep(delay)
    log.error("Could not connect to Kafka. Exiting.")
    sys.exit(1)


def main():
    log.info("PharmaWatch Kafka consumer starting...")
    init_kafka_tables()
    faers_pipeline.register_job()
    consumer = create_consumer()
    total = 0
    try:
        for record in consumer:
            try:
                if handle_record(record.topic, record.partition, record.offset, record.value):
                    total += 1
                    if total % 50 == 0:
                        log.info(f"Processed {total} messages")
            except Exception as e:
                log.error(f"Error processing {record.topic} partition {record.partition} offset {record.offset}: {e}")
    except KeyboardInterrupt:
        log.info("Consumer stopped by user.")
    finally:
        consumer.close()
        log.info(f"Consumer shut down after {total} messages.")


if __name__ == "__main__":
    main()

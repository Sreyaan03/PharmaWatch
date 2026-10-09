#!/usr/bin/env python3
"""
kafka_consumer.py — PharmaWatch Kafka Consumer (Production MySQL Engine)

Reads all 4 topics and writes processed data into MySQL:
  faers-raw        → signals_cache (PRR computed from co-occurrence counts)
                   → drug_events_agg & drug_events
  pubmed-stream    → kafka_pubmed  (literature signals)
  clinical-trials  → kafka_trials  (active trial monitoring)
  fda-alerts       → kafka_alerts  (real FDA safety alerts)
"""

import os
import sys
import json
import time
import hashlib
import logging
from kafka import KafkaConsumer
from kafka.errors import KafkaError
from dotenv import load_dotenv

# Ensure backend directory is in sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

load_dotenv(os.path.join(backend_dir, ".env"))

from db.mysql import query, query_one, execute, ping
from faers.disproportionality import compute_metrics

KAFKA_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CONSUMER] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("consumer")

_msg_count_since_purge = 0


def init_kafka_tables():
    """Verify MySQL connectivity and database readiness."""
    try:
        status = ping()
        log.info(f"✅ Connected to MySQL database '{status.get('database')}' ({status.get('table_count')} tables).")
    except Exception as e:
        log.warning(f"⚠️ MySQL check returned: {e}. Ensure MySQL container is running.")


def record_pipeline_stat(topic, offset=None):
    """
    Track real Kafka message counts and periodically purge rows older than 7 days
    to prevent unbounded disk growth.
    """
    global _msg_count_since_purge
    try:
        sql = """
            INSERT INTO kafka_pipeline_stats (topic, messages_in, last_offset, recorded_at)
            VALUES (%s, 1, %s, NOW())
        """
        execute(sql, (topic, str(offset) if offset is not None else None))
        _msg_count_since_purge += 1

        # Purge stats older than 7 days every 500 messages
        if _msg_count_since_purge >= 500:
            execute("DELETE FROM kafka_pipeline_stats WHERE recorded_at < NOW() - INTERVAL 7 DAY")
            _msg_count_since_purge = 0
            log.info("🧹 Auto-purged kafka_pipeline_stats older than 7 days.")
    except Exception as e:
        log.error(f"Failed to record pipeline stat: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER 1: faers-raw
# ══════════════════════════════════════════════════════════════════════════════
def compute_prr(a, ab, ac, total):
    """
    Compute PRR, ROR, BCPNN IC with Haldane-Anscombe continuity correction.
    """
    b = max(int(ab) - int(a), 0)
    c = max(int(ac) - int(a), 0)
    d = max(int(total) - int(ab) - int(ac) + int(a), 0)
    return compute_metrics(int(a), b, c, d)


def handle_faers(msg):
    """Process a faers-raw message → update drug_events_agg and signals_cache."""
    drug = msg.get("drug", "").lower().strip()
    event = msg.get("event", "").lower().strip()
    count = int(msg.get("count", 1))

    if not drug or not event:
        return

    # 1. Upsert aggregated counts
    sql_agg = """
        INSERT INTO drug_events_agg (drug, event, report_count, source, updated_at)
        VALUES (%s, %s, %s, 'kafka_faers', NOW())
        ON DUPLICATE KEY UPDATE
            report_count = VALUES(report_count),
            updated_at = NOW()
    """
    execute(sql_agg, (drug, event, count))

    # 2. Keep unique row in drug_events
    raw_hash = hashlib.md5(f"{drug}:{event}:kafka_faers".encode("utf-8")).hexdigest()
    sql_events = """
        INSERT INTO drug_events (drug, event, source, confidence, raw_hash, created_at)
        VALUES (%s, %s, 'kafka_faers', 1.0, %s, NOW())
        ON DUPLICATE KEY UPDATE confidence = 1.0
    """
    execute(sql_events, (drug, event, raw_hash))

    # 3. Compute disproportionality from aggregated counts (explicit int casting)
    row_a = query_one("SELECT COALESCE(report_count, 0) AS cnt FROM drug_events_agg WHERE drug = %s AND event = %s", (drug, event))
    row_ab = query_one("SELECT COALESCE(SUM(report_count), 0) AS cnt FROM drug_events_agg WHERE drug = %s", (drug,))
    row_ac = query_one("SELECT COALESCE(SUM(report_count), 0) AS cnt FROM drug_events_agg WHERE event = %s", (event,))
    row_tot = query_one("SELECT COALESCE(SUM(report_count), 0) AS cnt FROM drug_events_agg")

    a = int(row_a["cnt"]) if row_a and row_a.get("cnt") else 0
    ab = int(row_ab["cnt"]) if row_ab and row_ab.get("cnt") else 0
    ac = int(row_ac["cnt"]) if row_ac and row_ac.get("cnt") else 0
    total = int(row_tot["cnt"]) if row_tot and row_tot.get("cnt") else 0

    if a < 3 or total < 10:
        return

    metrics = compute_prr(a, ab, ac, total)

    sql_signals = """
        INSERT INTO signals_cache (
            drug, event, a, b, c, d, prr, prr_ci_low, prr_ci_high, ror, ror_ci_low, ror_ci_high,
            chi2, bcpnn_ic, ic025, n_reports, signal_strength, source, computed_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'kafka_faers', NOW())
        ON DUPLICATE KEY UPDATE
            a = VALUES(a), b = VALUES(b), c = VALUES(c), d = VALUES(d),
            prr = VALUES(prr), prr_ci_low = VALUES(prr_ci_low), prr_ci_high = VALUES(prr_ci_high),
            ror = VALUES(ror), ror_ci_low = VALUES(ror_ci_low), ror_ci_high = VALUES(ror_ci_high),
            chi2 = VALUES(chi2), bcpnn_ic = VALUES(bcpnn_ic), ic025 = VALUES(ic025),
            n_reports = VALUES(n_reports), signal_strength = VALUES(signal_strength),
            source = VALUES(source), computed_at = NOW()
    """
    execute(sql_signals, (
        drug, event, metrics["a"], metrics["b"], metrics["c"], metrics["d"],
        metrics["prr"], metrics["prr_ci_low"], metrics["prr_ci_high"],
        metrics["ror"], metrics["ror_ci_low"], metrics["ror_ci_high"],
        metrics["chi2"], metrics["bcpnn_ic"], metrics["ic025"],
        metrics["n_reports"], metrics["signal_strength"]
    ))


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER 2: pubmed-stream
# ══════════════════════════════════════════════════════════════════════════════
def handle_pubmed(msg):
    pmid = msg.get("pmid", "")
    title = msg.get("title", "")
    journal = msg.get("journal", "")
    pubdate = msg.get("pubdate", "")
    query_str = msg.get("query", "")

    if not pmid:
        return

    sql = """
        INSERT INTO kafka_pubmed (pmid, title, journal, pubdate, query, ingested_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE title = VALUES(title), journal = VALUES(journal)
    """
    execute(sql, (pmid, title, journal, pubdate, query_str))


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER 3: clinical-trials
# ══════════════════════════════════════════════════════════════════════════════
def handle_trials(msg):
    nct_id = msg.get("nct_id", "")
    drug = msg.get("drug", "")
    title = msg.get("title", "")
    phase = json.dumps(msg.get("phase", []))
    status = msg.get("status", "")

    if not nct_id:
        return

    sql = """
        INSERT INTO kafka_trials (nct_id, drug, title, phase, status, ingested_at)
        VALUES (%s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE title = VALUES(title), status = VALUES(status)
    """
    execute(sql, (nct_id, drug, title, phase, status))


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER 4: fda-alerts
# ══════════════════════════════════════════════════════════════════════════════
def handle_alerts(msg):
    source = msg.get("source", "")
    if source == "fda_enforcement":
        key = msg.get("recall_num", "")
        title = msg.get("title", "")
        link = ""
        summary = msg.get("product", "")
        pub = msg.get("report_date", "")
        feed = f"class:{msg.get('class','')} firm:{msg.get('firm','')}"
    else:
        key = msg.get("title", "")[:80]
        title = msg.get("title", "")
        link = msg.get("link", "")
        summary = msg.get("summary", "")
        pub = msg.get("published", "")
        feed = msg.get("feed_url", "")

    if not title or not key:
        return

    sql = """
        INSERT INTO kafka_alerts (alert_key, title, link, summary, published, feed_url, ingested_at)
        VALUES (%s, %s, %s, %s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE title = VALUES(title), summary = VALUES(summary)
    """
    execute(sql, (key, title, link, summary, pub, feed))


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CONSUMER LOOP
# ══════════════════════════════════════════════════════════════════════════════
TOPIC_HANDLERS = {
    "faers-raw": handle_faers,
    "pubmed-stream": handle_pubmed,
    "clinical-trials": handle_trials,
    "fda-alerts": handle_alerts,
}


def create_consumer(retries=10, delay=5):
    """Create Kafka consumer with retry logic."""
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
            log.info(f"✅ Consumer connected to Kafka at {KAFKA_SERVERS}")
            log.info(f"   Subscribed topics: {list(TOPIC_HANDLERS.keys())}")
            return consumer
        except KafkaError:
            log.warning(f"Kafka not ready (attempt {attempt}/{retries}), retrying in {delay}s...")
            time.sleep(delay)
    log.error("❌ Could not connect to Kafka. Exiting.")
    sys.exit(1)


def main():
    log.info("🚀 PharmaWatch Kafka Consumer starting up (MySQL backend)...")
    init_kafka_tables()
    consumer = create_consumer()

    total_processed = 0
    log.info("✅ Consumer running. Waiting for messages...")

    try:
        for record in consumer:
            topic = record.topic
            offset = record.offset
            msg = record.value

            try:
                handler = TOPIC_HANDLERS.get(topic)
                if handler:
                    handler(msg)
                    record_pipeline_stat(topic, offset)
                    total_processed += 1

                    if total_processed % 50 == 0:
                        log.info(f"📊 Total processed: {total_processed} messages across all topics")

            except Exception as e:
                log.error(f"Error processing message from {topic} offset {offset}: {e}")

    except KeyboardInterrupt:
        log.info("Consumer stopped by user.")
    finally:
        consumer.close()
        log.info(f"Consumer shut down. Total messages processed: {total_processed}")


if __name__ == "__main__":
    main()

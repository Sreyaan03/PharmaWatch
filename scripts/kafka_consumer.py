#!/usr/bin/env python3
"""
kafka_consumer.py — PharmaWatch Kafka Consumer

Reads all 4 topics and writes processed data into SQLite:

  faers-raw        → signals_cache (PRR computed from co-occurrence counts)
                   → drug_events   (raw drug-event pairs)
  pubmed-stream    → kafka_pubmed  (new table: literature signals)
  clinical-trials  → kafka_trials  (new table: active trial monitoring)
  fda-alerts       → kafka_alerts  (new table: real FDA safety alerts)

The Flask app reads from these same SQLite tables — no changes needed to the app.

Run:  python scripts/kafka_consumer.py
Env:  KAFKA_BOOTSTRAP_SERVERS (from .env or environment)
"""

import os
import sys
import json
import math
import time
import sqlite3
import logging
import datetime
from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable
from dotenv import load_dotenv

# ── Load environment ──────────────────────────────────────────────────────────
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))

KAFKA_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
DB_PATH = os.path.join(os.path.dirname(__file__), "..", "backend", "pharmawatch.db")

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [CONSUMER] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("consumer")


# ── Database helpers ──────────────────────────────────────────────────────────
def get_db():
    """Open SQLite with WAL mode and 10s busy timeout to handle concurrent access."""
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    # WAL mode: allows Flask app to read while consumer writes simultaneously
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")   # faster writes, still safe
    conn.execute("PRAGMA busy_timeout=10000")   # wait up to 10s on lock
    return conn


def retry_db_write(fn, conn, retries=5):
    """Execute fn(conn) with retries on SQLite lock/IO errors."""
    for attempt in range(retries):
        try:
            fn(conn)
            return True
        except sqlite3.OperationalError as e:
            if attempt < retries - 1:
                wait = 0.2 * (2 ** attempt)  # 0.2s, 0.4s, 0.8s, 1.6s, 3.2s
                log.warning(f"SQLite busy (attempt {attempt+1}/{retries}), retrying in {wait:.1f}s: {e}")
                time.sleep(wait)
            else:
                log.error(f"SQLite write failed after {retries} attempts: {e}")
                return False


def init_kafka_tables():
    """Create the 3 new tables for Kafka-sourced data if they don't exist."""
    conn = get_db()
    conn.executescript("""
        -- PubMed literature signals
        CREATE TABLE IF NOT EXISTS kafka_pubmed (
            pmid        TEXT PRIMARY KEY,
            title       TEXT,
            journal     TEXT,
            pubdate     TEXT,
            query       TEXT,
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- ClinicalTrials active studies
        CREATE TABLE IF NOT EXISTS kafka_trials (
            nct_id      TEXT PRIMARY KEY,
            drug        TEXT,
            title       TEXT,
            phase       TEXT,
            status      TEXT,
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- FDA MedWatch safety alerts
        CREATE TABLE IF NOT EXISTS kafka_alerts (
            alert_key   TEXT PRIMARY KEY,
            title       TEXT,
            link        TEXT,
            summary     TEXT,
            published   TEXT,
            feed_url    TEXT,
            ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Kafka pipeline stats (real consumer lag tracking)
        CREATE TABLE IF NOT EXISTS kafka_pipeline_stats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            topic       TEXT NOT NULL,
            messages_in INTEGER DEFAULT 0,
            last_offset TEXT,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()
    log.info(f"[DB] Kafka tables ready at {DB_PATH}")


def record_pipeline_stat(topic, offset=None):
    """Track real Kafka message counts for the pipeline dashboard."""
    conn = get_db()
    conn.execute("""
        INSERT INTO kafka_pipeline_stats (topic, messages_in, last_offset)
        VALUES (?, 1, ?)
    """, (topic, str(offset) if offset else None))
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER 1: faers-raw
# Computes PRR from FAERS co-occurrence counts and upserts into signals_cache
# ══════════════════════════════════════════════════════════════════════════════
def compute_prr(a, ab, ac, total):
    """Compute PRR, ROR, BCPNN IC from a 2×2 contingency table."""
    b = max(ab - a, 0)
    c = max(ac - a, 0)
    d = max(total - ab - ac + a, 0)
    prr = (a / max(a + b, 1)) / max(c / max(c + d, 1), 1e-9)
    ror = (a * d) / max(b * c, 1e-9)
    # BCPNN Information Component
    p_drug  = (a + b) / max(total, 1)
    p_event = (a + c) / max(total, 1)
    p_joint = a / max(total, 1)
    ic = math.log2(max(p_joint, 1e-9) / max(p_drug * p_event, 1e-9))
    return prr, ror, ic


def handle_faers(msg, conn):
    """Process a faers-raw message → write to drug_events and signals_cache."""
    drug    = msg.get("drug", "").lower().strip()
    event   = msg.get("event", "").lower().strip()
    count   = int(msg.get("count", 1))

    if not drug or not event:
        return

    # Insert into drug_events (raw pairs)
    conn.execute("""
        INSERT INTO drug_events (drug, event, source, confidence)
        VALUES (?, ?, 'kafka_faers', 1.0)
    """, (drug, event))

    # Count co-occurrences for PRR from drug_events table
    a  = conn.execute("SELECT COUNT(*) FROM drug_events WHERE drug=? AND event=?",
                      (drug, event)).fetchone()[0]
    ab = conn.execute("SELECT COUNT(*) FROM drug_events WHERE drug=?",
                      (drug,)).fetchone()[0]
    ac = conn.execute("SELECT COUNT(*) FROM drug_events WHERE event=?",
                      (event,)).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM drug_events").fetchone()[0]

    if a < 3 or total < 10:
        return   # not enough data for meaningful PRR

    prr, ror, ic = compute_prr(a, ab, ac, total)
    b = ab - a
    c = ac - a
    d = total - ab - ac + a
    n_reports = a

    # Classify severity
    if prr >= 4.0 and ic > 1.0:
        severity = "critical"
    elif prr >= 2.0 and ic > 0:
        severity = "high"
    elif prr >= 1.5:
        severity = "moderate"
    else:
        severity = "low"

    conn.execute("""
        INSERT INTO signals_cache
          (drug, event, a, b, c, d, prr, ror, bcpnn_ic, n_reports, severity, source, computed_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,'kafka_faers',CURRENT_TIMESTAMP)
        ON CONFLICT(drug, event) DO UPDATE SET
            a=excluded.a, b=excluded.b, c=excluded.c, d=excluded.d,
            prr=excluded.prr, ror=excluded.ror, bcpnn_ic=excluded.bcpnn_ic,
            n_reports=excluded.n_reports, severity=excluded.severity,
            source=excluded.source, computed_at=CURRENT_TIMESTAMP
    """, (drug, event, a, b, c, d,
          round(prr, 4), round(ror, 4), round(ic, 4),
          n_reports, severity))


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER 2: pubmed-stream
# ══════════════════════════════════════════════════════════════════════════════
def handle_pubmed(msg, conn):
    pmid    = msg.get("pmid", "")
    title   = msg.get("title", "")
    journal = msg.get("journal", "")
    pubdate = msg.get("pubdate", "")
    query   = msg.get("query", "")

    if not pmid:
        return

    conn.execute("""
        INSERT OR IGNORE INTO kafka_pubmed (pmid, title, journal, pubdate, query)
        VALUES (?, ?, ?, ?, ?)
    """, (pmid, title, journal, pubdate, query))


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER 3: clinical-trials
# ══════════════════════════════════════════════════════════════════════════════
def handle_trials(msg, conn):
    nct_id = msg.get("nct_id", "")
    drug   = msg.get("drug", "")
    title  = msg.get("title", "")
    phase  = json.dumps(msg.get("phase", []))
    status = msg.get("status", "")

    if not nct_id:
        return

    conn.execute("""
        INSERT OR REPLACE INTO kafka_trials (nct_id, drug, title, phase, status)
        VALUES (?, ?, ?, ?, ?)
    """, (nct_id, drug, title, phase, status))


# ══════════════════════════════════════════════════════════════════════════════
# HANDLER 4: fda-alerts
# ══════════════════════════════════════════════════════════════════════════════
def handle_alerts(msg, conn):
    key     = msg.get("title", "")[:80]
    title   = msg.get("title", "")
    link    = msg.get("link", "")
    summary = msg.get("summary", "")
    pub     = msg.get("published", "")
    feed    = msg.get("feed_url", "")

    if not title:
        return

    conn.execute("""
        INSERT OR IGNORE INTO kafka_alerts (alert_key, title, link, summary, published, feed_url)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (key, title, link, summary, pub, feed))


# ══════════════════════════════════════════════════════════════════════════════
# MAIN CONSUMER LOOP
# ══════════════════════════════════════════════════════════════════════════════
TOPIC_HANDLERS = {
    "faers-raw":        handle_faers,
    "pubmed-stream":    handle_pubmed,
    "clinical-trials":  handle_trials,
    "fda-alerts":       handle_alerts,
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
                auto_offset_reset="earliest",   # replay from beginning if new group
                enable_auto_commit=True,
                auto_commit_interval_ms=5000,
                session_timeout_ms=30000,
                max_poll_records=50,
            )
            log.info(f"✅ Consumer connected to Kafka at {KAFKA_SERVERS}")
            log.info(f"   Subscribed topics: {list(TOPIC_HANDLERS.keys())}")
            return consumer
        except NoBrokersAvailable:
            log.warning(f"Kafka not ready (attempt {attempt}/{retries}), retrying in {delay}s...")
            import time; import time as t; t.sleep(delay)
    log.error("❌ Could not connect to Kafka. Exiting.")
    sys.exit(1)


def main():
    log.info("🚀 PharmaWatch Kafka Consumer starting up...")
    init_kafka_tables()
    consumer = create_consumer()

    total_processed = 0
    conn = get_db()

    log.info("✅ Consumer running. Waiting for messages...")

    try:
        for record in consumer:
            topic   = record.topic
            offset  = record.offset
            msg     = record.value

            try:
                handler = TOPIC_HANDLERS.get(topic)
                if handler:
                    handler(msg, conn)
                    conn.commit()
                    record_pipeline_stat(topic, offset)
                    total_processed += 1

                    if total_processed % 50 == 0:
                        log.info(f"📊 Total processed: {total_processed} messages across all topics")

            except Exception as e:
                log.error(f"Error processing message from {topic} offset {offset}: {e}")
                conn.rollback()

    except KeyboardInterrupt:
        log.info("Consumer stopped by user.")
    finally:
        conn.close()
        consumer.close()
        log.info(f"Consumer shut down. Total messages processed: {total_processed}")


if __name__ == "__main__":
    main()

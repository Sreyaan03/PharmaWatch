# backend/routes/pipeline.py
# ─────────────────────────────────────────────────────────────────────────────
# Pipeline routes: real Kafka consumer stats for the dashboard
# Reads from kafka_pipeline_stats table written by kafka_consumer.py
# ─────────────────────────────────────────────────────────────────────────────

import os
import sqlite3
import pathlib
from flask import Blueprint, jsonify

pipeline_bp = Blueprint("pipeline", __name__)
DB_PATH = os.path.join(pathlib.Path(__file__).parent.parent, "pharmawatch.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@pipeline_bp.route("/api/pipeline/stats", methods=["GET"])
def pipeline_stats():
    """
    Returns real Kafka consumer message counts per topic.
    The distributed_storage.js dashboard reads this to replace Math.random().
    Falls back gracefully if Kafka hasn't run yet.
    """
    try:
        conn = get_db()

        # Total messages processed per topic (all time)
        topic_counts = conn.execute("""
            SELECT topic, COUNT(*) as total, MAX(recorded_at) as last_seen
            FROM kafka_pipeline_stats
            GROUP BY topic
        """).fetchall()

        # Messages in the last 10 minutes (throughput indicator)
        recent_counts = conn.execute("""
            SELECT topic, COUNT(*) as recent
            FROM kafka_pipeline_stats
            WHERE recorded_at >= datetime('now', '-10 minutes')
            GROUP BY topic
        """).fetchall()

        recent_map = {r["topic"]: r["recent"] for r in recent_counts}

        topics = {}
        for row in topic_counts:
            t = row["topic"]
            topics[t] = {
                "total_messages":    row["total"],
                "last_seen":         row["last_seen"],
                "recent_10min":      recent_map.get(t, 0),
                "msgs_per_min":      round(recent_map.get(t, 0) / 10, 1),
            }

        # PubMed papers ingested
        pubmed_count = 0
        trials_count = 0
        alerts_count = 0
        try:
            pubmed_count = conn.execute("SELECT COUNT(*) FROM kafka_pubmed").fetchone()[0]
            trials_count = conn.execute("SELECT COUNT(*) FROM kafka_trials").fetchone()[0]
            alerts_count = conn.execute("SELECT COUNT(*) FROM kafka_alerts").fetchone()[0]
        except Exception:
            pass

        conn.close()

        kafka_running = len(topics) > 0

        return jsonify({
            "kafka_running":   kafka_running,
            "topics":          topics,
            "ingested": {
                "pubmed_papers":  pubmed_count,
                "active_trials":  trials_count,
                "fda_alerts":     alerts_count,
            },
            "source": "kafka_pipeline_stats" if kafka_running else "kafka_not_started"
        })

    except Exception as e:
        return jsonify({
            "kafka_running": False,
            "topics": {},
            "ingested": {},
            "source": "error",
            "error": str(e)
        })


@pipeline_bp.route("/api/pipeline/alerts", methods=["GET"])
def pipeline_alerts():
    """Returns the latest FDA safety alerts ingested via Kafka."""
    try:
        conn = get_db()
        rows = conn.execute("""
            SELECT alert_key, title, link, summary, published, ingested_at
            FROM kafka_alerts
            ORDER BY ingested_at DESC
            LIMIT 20
        """).fetchall()
        conn.close()
        return jsonify({"alerts": [dict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        return jsonify({"alerts": [], "count": 0, "error": str(e)})


@pipeline_bp.route("/api/pipeline/pubmed", methods=["GET"])
def pipeline_pubmed():
    """Returns the latest PubMed papers ingested via Kafka."""
    try:
        conn = get_db()
        rows = conn.execute("""
            SELECT pmid, title, journal, pubdate, ingested_at
            FROM kafka_pubmed
            ORDER BY ingested_at DESC
            LIMIT 20
        """).fetchall()
        conn.close()
        return jsonify({"papers": [dict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        return jsonify({"papers": [], "count": 0, "error": str(e)})


@pipeline_bp.route("/api/pipeline/trials", methods=["GET"])
def pipeline_trials():
    """Returns active clinical trials ingested via Kafka."""
    try:
        conn = get_db()
        rows = conn.execute("""
            SELECT nct_id, drug, title, phase, status, ingested_at
            FROM kafka_trials
            ORDER BY ingested_at DESC
            LIMIT 30
        """).fetchall()
        conn.close()
        return jsonify({"trials": [dict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        return jsonify({"trials": [], "count": 0, "error": str(e)})

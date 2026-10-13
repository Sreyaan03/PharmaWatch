# backend/routes/pipeline.py
# ─────────────────────────────────────────────────────────────────────────────
# Pipeline routes: real Kafka consumer stats for the dashboard
# Reads from kafka_pipeline_stats table written by ingest/consumer.py
# ─────────────────────────────────────────────────────────────────────────────

from flask import Blueprint, jsonify
from db.mysql import query, query_one

pipeline_bp = Blueprint("pipeline", __name__)


@pipeline_bp.route("/api/pipeline/stats", methods=["GET"])
def pipeline_stats():
    """
    Returns real Kafka consumer message counts per topic.
    The distributed_storage.js dashboard reads this to replace Math.random().
    Falls back gracefully if Kafka hasn't run yet.
    """
    try:
        # Total messages processed per topic (all time)
        topic_counts = query("""
            SELECT topic, COUNT(*) AS total, MAX(recorded_at) AS last_seen
            FROM kafka_pipeline_stats
            GROUP BY topic
        """)

        # Messages in the last 10 minutes (throughput indicator)
        recent_counts = query("""
            SELECT topic, COUNT(*) AS recent
            FROM kafka_pipeline_stats
            WHERE recorded_at >= NOW() - INTERVAL 10 MINUTE
            GROUP BY topic
        """)

        recent_map = {r["topic"]: int(r.get("recent") or 0) for r in recent_counts if r.get("topic")}

        topics = {}
        for row in topic_counts:
            t = row.get("topic")
            if not t:
                continue
            recent_10 = recent_map.get(t, 0)
            topics[t] = {
                "total_messages": int(row.get("total") or 0),
                "last_seen": str(row.get("last_seen") or ""),
                "recent_10min": recent_10,
                "msgs_per_min": round(recent_10 / 10.0, 1),
            }

        # Query counts for PubMed, ClinicalTrials, FDA Alerts
        row_pm = query_one("SELECT COUNT(*) AS cnt FROM kafka_pubmed")
        row_tr = query_one("SELECT COUNT(*) AS cnt FROM kafka_trials")
        row_al = query_one("SELECT COUNT(*) AS cnt FROM kafka_alerts")

        pubmed_count = int(row_pm["cnt"]) if row_pm and row_pm.get("cnt") else 0
        trials_count = int(row_tr["cnt"]) if row_tr and row_tr.get("cnt") else 0
        alerts_count = int(row_al["cnt"]) if row_al and row_al.get("cnt") else 0

        kafka_running = len(topics) > 0

        return jsonify({
            "kafka_running": kafka_running,
            "topics": topics,
            "ingested": {
                "pubmed_papers": pubmed_count,
                "active_trials": trials_count,
                "fda_alerts": alerts_count,
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
        rows = query("""
            SELECT alert_key, title, link, summary, published, ingested_at
            FROM kafka_alerts
            ORDER BY ingested_at DESC
            LIMIT 20
        """)
        return jsonify({"alerts": rows, "count": len(rows)})
    except Exception as e:
        return jsonify({"alerts": [], "count": 0, "error": str(e)})


@pipeline_bp.route("/api/pipeline/pubmed", methods=["GET"])
def pipeline_pubmed():
    """Returns the latest PubMed papers ingested via Kafka."""
    try:
        rows = query("""
            SELECT pmid, title, journal, pubdate, ingested_at
            FROM kafka_pubmed
            ORDER BY ingested_at DESC
            LIMIT 20
        """)
        return jsonify({"papers": rows, "count": len(rows)})
    except Exception as e:
        return jsonify({"papers": [], "count": 0, "error": str(e)})


@pipeline_bp.route("/api/pipeline/trials", methods=["GET"])
def pipeline_trials():
    """Returns active clinical trials ingested via Kafka."""
    try:
        rows = query("""
            SELECT nct_id, drug, title, phase, status, ingested_at
            FROM kafka_trials
            ORDER BY ingested_at DESC
            LIMIT 30
        """)
        return jsonify({"trials": rows, "count": len(rows)})
    except Exception as e:
        return jsonify({"trials": [], "count": 0, "error": str(e)})

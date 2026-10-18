# backend/services/pipeline.py
# Business logic for backend/routes/pipeline.py (no Flask imports).

from db.mysql import query, query_one


def broker_reachable(timeout=1.0):
    """True when the first configured Kafka bootstrap server accepts a TCP connection."""
    import os
    import socket
    server = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092").split(",")[0].strip()
    host, _, port = server.partition(":")
    try:
        socket.create_connection((host or "localhost", int(port or 9092)), timeout=timeout).close()
        return True
    except (OSError, ValueError):
        return False

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

        # Rows from past runs stay in MySQL after the broker stops, so their presence says nothing
        # about whether Kafka is running now. Ask the broker.
        kafka_running = broker_reachable()

        return {
            "kafka_running": kafka_running,
            "topics": topics,
            "ingested": {
                "pubmed_papers": pubmed_count,
                "active_trials": trials_count,
                "fda_alerts": alerts_count,
            },
            "source": "kafka_pipeline_stats" if kafka_running else "kafka_not_started"
        }, 200

    except Exception as e:
        return {
            "kafka_running": False,
            "topics": {},
            "ingested": {},
            "source": "error",
            "error": str(e)
        }, 200

def pipeline_alerts():
    """Returns the latest FDA safety alerts ingested via Kafka."""
    try:
        rows = query("""
            SELECT alert_key, title, link, summary, published, ingested_at
            FROM kafka_alerts
            ORDER BY ingested_at DESC
            LIMIT 20
        """)
        return {"alerts": rows, "count": len(rows)}, 200
    except Exception as e:
        return {"alerts": [], "count": 0, "error": str(e)}, 200

def pipeline_pubmed():
    """Returns the latest PubMed papers ingested via Kafka."""
    try:
        rows = query("""
            SELECT pmid, title, journal, pubdate, ingested_at
            FROM kafka_pubmed
            ORDER BY ingested_at DESC
            LIMIT 20
        """)
        return {"papers": rows, "count": len(rows)}, 200
    except Exception as e:
        return {"papers": [], "count": 0, "error": str(e)}, 200

def pipeline_trials():
    """Returns active clinical trials ingested via Kafka."""
    try:
        rows = query("""
            SELECT nct_id, drug, title, phase, status, ingested_at
            FROM kafka_trials
            ORDER BY ingested_at DESC
            LIMIT 30
        """)
        return {"trials": rows, "count": len(rows)}, 200
    except Exception as e:
        return {"trials": [], "count": 0, "error": str(e)}, 200


# ── FAERS quarterly releases (announced on the faers-release topic) ──────────
def faers_releases():
    """Loaded quarters and announced quarters not loaded yet, for the dashboard notice."""
    from faers import pipeline as faers_pipeline
    loaded = sorted(faers_pipeline.loaded_quarters())
    try:
        pending = faers_pipeline.pending_releases()
    except Exception:
        return {"loaded": loaded, "pending": [], "store": "unavailable",
                "auto_refresh": faers_pipeline.auto_refresh_enabled()}, 200
    return {"loaded": loaded, "latest_loaded": loaded[-1] if loaded else None, "pending": pending,
            "auto_refresh": faers_pipeline.auto_refresh_enabled(), "store": "ok"}, 200


def start_faers_refresh(quarter, created_by="admin"):
    """Start the refresh job for an announced quarter (admin)."""
    from faers import pipeline as faers_pipeline
    try:
        quarter = faers_pipeline.normalize_quarter(quarter or "")
    except ValueError as exc:
        return {"error": str(exc)}, 400
    if quarter in faers_pipeline.loaded_quarters():
        return {"error": f"{quarter} is already loaded"}, 409
    try:
        job_id = faers_pipeline.start_refresh(quarter, created_by=created_by)
    except Exception:
        return {"error": "Job store unavailable (MySQL is not reachable)."}, 503
    if job_id is None:
        return {"error": f"a refresh of {quarter} is already running"}, 409
    return {"quarter": quarter, "job_id": job_id, "status": "refreshing"}, 202

# backend/services/health.py
# Service liveness: server, ML model name and MySQL connectivity.

from db.mysql import ping


def health():
    """Health check endpoint checking server, ML model status, and MySQL database connectivity."""
    from ml.ner import MODEL_NAME
    db_status = "not_connected"
    try:
        p = ping()
        db_status = f"connected ({p.get('database')}, {p.get('table_count', 0)} tables)"
    except Exception as e:
        db_status = f"unavailable ({e})"

    return {
        "status": "ok",
        "database": db_status,
        "model": MODEL_NAME
    }

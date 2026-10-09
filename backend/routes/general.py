# backend/routes/general.py
# ─────────────────────────────────────────────────────────────────────────────
# General routes: index serving, health status, and live service probes
# ─────────────────────────────────────────────────────────────────────────────

import os
import pathlib
from flask import Blueprint, jsonify, send_from_directory, make_response
from db.mysql import ping

general_bp = Blueprint("general", __name__)

# Resolve frontend static directory relative to this file
SRC_DIR = str(pathlib.Path(__file__).parent.parent.parent / "src")


@general_bp.route("/")
def serve_index():
    """Serve the PharmaWatch frontend — never cached so script paths always update."""
    resp = make_response(send_from_directory(SRC_DIR, "index.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


@general_bp.route("/api/health", methods=["GET"])
def health():
    """Health check endpoint checking server, ML model status, and MySQL database connectivity."""
    from helpers.models import MODEL_NAME
    db_status = "not_connected"
    try:
        p = ping()
        db_status = f"connected ({p.get('database')}, {p.get('table_count', 0)} tables)"
    except Exception as e:
        db_status = f"unavailable ({e})"

    return jsonify({
        "status": "ok",
        "database": db_status,
        "model": MODEL_NAME
    })

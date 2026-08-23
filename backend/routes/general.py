# backend/routes/general.py
# ─────────────────────────────────────────────────────────────────────────────
# General routes: index serving, health status, and dashboard statistics
# ─────────────────────────────────────────────────────────────────────────────

import os
import pathlib
import sqlite3
import math
from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify, send_from_directory, make_response

general_bp = Blueprint("general", __name__)

# Resolve frontend static directory relative to this file
SRC_DIR = str(pathlib.Path(__file__).parent.parent.parent / "src")
DB_PATH = os.path.join(pathlib.Path(__file__).parent.parent, "pharmawatch.db")

@general_bp.route("/")
def serve_index():
    """Serve the PharmaWatch frontend — never cached so script paths always update."""
    resp = make_response(send_from_directory(SRC_DIR, "index.html"))
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['Expires'] = '0'
    return resp

@general_bp.route("/api/health", methods=["GET"])
def health():
    """Simple ping endpoint - useful to check the server is running."""
    from helpers.models import MODEL_NAME
    return jsonify({"status": "ok", "model": MODEL_NAME})



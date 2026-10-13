# backend/routes/general.py
# ─────────────────────────────────────────────────────────────────────────────
# General routes: index serving, health status, and live service probes
# ─────────────────────────────────────────────────────────────────────────────

from flask import Blueprint, jsonify, send_from_directory, make_response
from core.config import SRC_DIR
from services import health as svc

general_bp = Blueprint("general", __name__)


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
    return jsonify(svc.health())

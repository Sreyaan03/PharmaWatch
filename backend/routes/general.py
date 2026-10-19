# backend/routes/general.py
# ─────────────────────────────────────────────────────────────────────────────
# General routes: health status and live service probes (the index page is served by app.py)
# ─────────────────────────────────────────────────────────────────────────────

from flask import Blueprint, jsonify
from services import health as svc

general_bp = Blueprint("general", __name__)


@general_bp.route("/health/live", methods=["GET"])
def health_live():
    """Liveness for orchestrators: the process answers."""
    payload, status = svc.live()
    return jsonify(payload), status


@general_bp.route("/health/ready", methods=["GET"])
def health_ready():
    """Readiness for load balancers: 503 until every required dependency is reachable."""
    payload, status = svc.ready()
    return jsonify(payload), status


@general_bp.route("/api/health", methods=["GET"])
def health():
    """Health check endpoint checking server, ML model status, and MySQL database connectivity."""
    return jsonify(svc.health())

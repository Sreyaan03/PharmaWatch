# backend/routes/dashboard.py
# HTTP layer only: parse the request, call the service, return JSON.

from flask import Blueprint, jsonify
from services import dashboard as svc

dashboard_bp = Blueprint("dashboard", __name__)

@dashboard_bp.route("/api/local-stats", methods=["GET"])
def local_stats():
    """Returns real counts from MySQL and DuckDB for dashboard animated counters."""
    payload, status = svc.local_stats()
    return jsonify(payload), status


@dashboard_bp.route("/api/dashboard/signal-intensity", methods=["GET"])
def dashboard_signal_intensity():
    """Returns hourly signal counts for the last 24 hours grouped by severity using real computed_at timestamps."""
    payload, status = svc.dashboard_signal_intensity()
    return jsonify(payload), status


@dashboard_bp.route("/api/dashboard/drug-categories", methods=["GET"])
def dashboard_drug_categories():
    """Returns drug category distribution from the signals cache."""
    payload, status = svc.dashboard_drug_categories()
    return jsonify(payload), status


@dashboard_bp.route("/api/dashboard/prr-distribution", methods=["GET"])
def prr_distribution():
    """Returns counts of signals bucketed by PRR ranges."""
    payload, status = svc.prr_distribution()
    return jsonify(payload), status

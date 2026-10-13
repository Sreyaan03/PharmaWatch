# backend/routes/analytics.py
# HTTP layer only: parse the request, call the service, return JSON.

from flask import Blueprint, request, jsonify
from services import analytics as svc

analytics_bp = Blueprint("analytics", __name__)

@analytics_bp.route("/api/analytics/prr", methods=["GET"])
def api_prr():
    """Computes disproportionality metrics (PRR, ROR, BCPNN) locally via DuckDB."""
    drug = request.args.get("drug", "Metformin").strip()
    event = request.args.get("event", "Nausea").strip()
    payload, status = svc.api_prr(drug, event)
    return jsonify(payload), status


@analytics_bp.route("/api/analytics/temporal", methods=["GET"])
def api_temporal():
    """Returns monthly or quarterly report counts over time for a drug."""
    drug = request.args.get("drug", "Metformin").strip()
    granularity = request.args.get("granularity", "month").strip()
    payload, status = svc.api_temporal(drug, granularity)
    return jsonify(payload), status


@analytics_bp.route("/api/analytics/top-reactions", methods=["GET"])
def api_top_reactions():
    """Returns top MedDRA adverse events for a drug."""
    drug = request.args.get("drug", "Metformin").strip()
    limit = request.args.get("limit", 20, type=int)
    payload, status = svc.api_top_reactions(drug, limit)
    return jsonify(payload), status


@analytics_bp.route("/api/analytics/data-coverage", methods=["GET"])
def api_data_coverage():
    """Returns data coverage statistics of the loaded DuckDB database."""
    payload, status = svc.api_data_coverage()
    return jsonify(payload), status

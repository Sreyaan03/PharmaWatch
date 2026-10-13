# backend/routes/temporal.py
# HTTP layer only: parse the request, call the service, return JSON.

from flask import Blueprint, request, jsonify
from services import temporal as svc

temporal_bp = Blueprint("temporal", __name__)

@temporal_bp.route("/api/temporal/velocity", methods=["GET"])
def api_temporal_velocity():
    """Calculates monthly report velocity and detects velocity spikes (Z-score > 2.0)."""
    drug = request.args.get("drug", "Metformin").strip()
    event = request.args.get("event", "Nausea").strip()
    payload, status = svc.api_temporal_velocity(drug, event)
    return jsonify(payload), status


@temporal_bp.route("/api/temporal/tto", methods=["GET"])
def api_temporal_tto():
    """Generates Time-To-Onset (TTO) distribution for drug-event pair from FAERS DuckDB or fallback."""
    drug = request.args.get("drug", "Metformin").strip()
    event = request.args.get("event", "Nausea").strip()
    payload, status = svc.api_temporal_tto(drug, event)
    return jsonify(payload), status


@temporal_bp.route("/api/temporal/seasonality", methods=["GET"])
def api_temporal_seasonality():
    """Calculates 12-month seasonality index for reporting patterns from FAERS DuckDB or fallback."""
    drug = request.args.get("drug", "Metformin").strip()
    event = request.args.get("event", "Nausea").strip()
    payload, status = svc.api_temporal_seasonality(drug, event)
    return jsonify(payload), status

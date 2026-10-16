# backend/routes/validation.py
# HTTP layer only: parse the request, call the service, return JSON.

from flask import Blueprint, jsonify
from services import validation as svc

validation_bp = Blueprint("validation", __name__)


@validation_bp.route("/api/validation/summary", methods=["GET"])
def validation_summary():
    """The recorded results of the validation benchmarks (signals, interactions, text mining)."""
    payload, status = svc.validation_summary()
    return jsonify(payload), status

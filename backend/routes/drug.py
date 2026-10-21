# backend/routes/drug.py
# HTTP layer only: parse the request, call the service, return JSON.

from flask import Blueprint, jsonify

from services import drug_profile as svc

drug_bp = Blueprint("drug", __name__)


@drug_bp.route("/api/drug/<name>/profile", methods=["GET"])
def drug_profile(name):
    """One page per drug: identity, report count, top reactions, boxed-warning status, trials,
    linked external evidence and surveillance alerts. Resolves the name once."""
    payload, status = svc.build_profile(name)
    return jsonify(payload), status

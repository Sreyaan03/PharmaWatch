# backend/routes/demographics.py
# HTTP layer only: parse the request, call the service, return JSON.

from flask import Blueprint, request, jsonify
from services import demographics as svc

demographics_bp = Blueprint("demographics", __name__)

@demographics_bp.route("/api/demographics/sex", methods=["GET"])
def api_demographics_sex():
    """Returns sex-stratified report counts (Male vs Female vs Unknown) from real FAERS data."""
    drug  = request.args.get("drug",  "Metformin").strip()
    event = request.args.get("event", "Nausea").strip()
    payload, status = svc.api_demographics_sex(drug, event)
    return jsonify(payload), status


@demographics_bp.route("/api/demographics/age", methods=["GET"])
def api_demographics_age():
    """Returns age-stratified report counts from real FAERS data."""
    drug  = request.args.get("drug",  "Metformin").strip()
    event = request.args.get("event", "Nausea").strip()
    payload, status = svc.api_demographics_age(drug, event)
    return jsonify(payload), status


@demographics_bp.route("/api/demographics/geo", methods=["GET"])
def api_demographics_geo():
    """Returns country report counts from real FAERS data."""
    drug  = request.args.get("drug",  "Metformin").strip()
    event = request.args.get("event", "Nausea").strip()
    payload, status = svc.api_demographics_geo(drug, event)
    return jsonify(payload), status


@demographics_bp.route("/api/demographics/overview", methods=["GET"])
def api_demographics_overview():
    """Combined endpoint returning sex, age, and geo stratification."""
    drug  = request.args.get("drug",  "Metformin").strip()
    event = request.args.get("event", "Nausea").strip()
    payload, status = svc.api_demographics_overview(drug, event)
    return jsonify(payload), status

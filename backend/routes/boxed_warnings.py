# backend/routes/boxed_warnings.py
# HTTP layer only: parse the request, call the service, return JSON.

from flask import Blueprint, request, jsonify
from services import boxed_warnings as svc

boxed_warnings_bp = Blueprint("boxed_warnings", __name__)

@boxed_warnings_bp.route("/api/boxed-warning/<drug_name>", methods=["GET"])
def get_boxed_warning(drug_name):
    """Fetches the boxed warning text from the openFDA Drug Labeling API for ANY drug."""
    payload, status = svc.get_boxed_warning(drug_name)
    return jsonify(payload), status


@boxed_warnings_bp.route("/api/boxed-warning-events/<drug_name>", methods=["GET"])
def get_boxed_warning_events(drug_name):
    """Fetches FAERS AE counts and cross-references them with the boxed warning text."""
    payload, status = svc.get_boxed_warning_events(drug_name)
    return jsonify(payload), status


@boxed_warnings_bp.route("/api/trials/<drug_name>", methods=["GET"])
def get_trials(drug_name):
    """Fetches REAL clinical trial data from ClinicalTrials.gov API v2 for a drug."""
    payload, status = svc.get_trials(drug_name)
    return jsonify(payload), status


@boxed_warnings_bp.route("/api/boxed-warning/violations/<drug_name>", methods=["GET"])
def get_violations_for_drug(drug_name):
    """Detects boxed-warning violations for a drug."""
    payload, status = svc.get_violations_for_drug(drug_name)
    return jsonify(payload), status


@boxed_warnings_bp.route("/api/boxed-warning/timeline/<drug_name>", methods=["GET"])
def get_warning_timeline(drug_name):
    """Before & After analysis of FAERS reports around boxed warning date."""
    payload, status = svc.get_warning_timeline(drug_name)
    return jsonify(payload), status


@boxed_warnings_bp.route("/api/boxed-warning/bias-analysis/<drug_name>", methods=["GET"])
def get_bias_analysis(drug_name):
    """Weber Effect + Notoriety Bias analysis."""
    payload, status = svc.get_bias_analysis(drug_name)
    return jsonify(payload), status


@boxed_warnings_bp.route("/api/prr-trials", methods=["GET"])
def get_prr_with_trials():
    """Option B: Cross-reference FAERS PRR signal with ClinicalTrials.gov data."""
    drug = request.args.get('drug', 'Metformin')
    event = request.args.get('event', 'Nausea')
    payload, status = svc.get_prr_with_trials(drug, event)
    return jsonify(payload), status


@boxed_warnings_bp.route("/api/boxed-warning/violations/all", methods=["GET"])
def get_all_violations_route():
    """Return all violation records stored in the local SQLite database."""
    limit_raw = request.args.get('limit', 200)
    payload, status = svc.get_all_violations_route(limit_raw)
    return jsonify(payload), status

# backend/routes/boxed_warnings.py
# HTTP layer only: parse the request, call the service, return JSON.

from flask import Blueprint, request, jsonify
from core.http_params import bounded_int
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


@boxed_warnings_bp.route("/api/boxed-warning/post-warning-reports/<drug_name>", methods=["GET"])
def get_post_warning_reports(drug_name):
    """Reports of events named in the boxed warning, before and after a sourced warning year."""
    payload, status = svc.get_post_warning_reports(drug_name, request.args.get("warning_year"))
    return jsonify(payload), status


@boxed_warnings_bp.route("/api/boxed-warning/violations/<drug_name>", methods=["GET"])
def get_violations_for_drug(drug_name):
    """Old path for post-warning-reports; returns the same data."""
    payload, status = svc.get_post_warning_reports(drug_name, request.args.get("warning_year"))
    return jsonify(payload), status


@boxed_warnings_bp.route("/api/boxed-warning/timeline/<drug_name>", methods=["GET"])
def get_warning_timeline(drug_name):
    """Reports per year, described before and after a sourced warning year."""
    payload, status = svc.get_warning_timeline(drug_name, request.args.get("warning_year"))
    return jsonify(payload), status


@boxed_warnings_bp.route("/api/boxed-warning/bias-analysis/<drug_name>", methods=["GET"])
def get_bias_analysis(drug_name):
    """Descriptive look for an early peak in reporting (Weber effect pattern)."""
    payload, status = svc.get_bias_analysis(drug_name, request.args.get("warning_year"))
    return jsonify(payload), status


@boxed_warnings_bp.route("/api/prr-trials", methods=["GET"])
def get_prr_with_trials():
    """A pair's disproportionality next to registered trials that mention it."""
    drug = request.args.get('drug', 'Metformin')
    event = request.args.get('event', 'Nausea')
    payload, status = svc.get_prr_with_trials(drug, event)
    return jsonify(payload), status


@boxed_warnings_bp.route("/api/boxed-warning/post-warning-reports/all", methods=["GET"])
def get_all_post_warning_reports_route():
    """Return all stored post-warning report records."""
    limit = bounded_int(request.args.get('limit'), 200, 1, 5000)
    payload, status = svc.get_all_post_warning_reports(limit)
    return jsonify(payload), status


@boxed_warnings_bp.route("/api/boxed-warning/violations/all", methods=["GET"])
def get_all_violations_route():
    """Old path for post-warning-reports/all; returns the same data."""
    limit = bounded_int(request.args.get('limit'), 200, 1, 5000)
    payload, status = svc.get_all_violations_route(limit)
    return jsonify(payload), status

# backend/routes/pipeline.py
# HTTP layer only: parse the request, call the service, return JSON.

from flask import Blueprint, jsonify, request

from core.auth import require_admin
from services import evidence as evidence_svc
from services import pipeline as svc

pipeline_bp = Blueprint("pipeline", __name__)

@pipeline_bp.route("/api/pipeline/stats", methods=["GET"])
def pipeline_stats():
    """
    Returns real Kafka consumer message counts per topic.
    The distributed_storage.js dashboard reads this to replace Math.random().
    Falls back gracefully if Kafka hasn't run yet.
    """
    payload, status = svc.pipeline_stats()
    return jsonify(payload), status


@pipeline_bp.route("/api/pipeline/alerts", methods=["GET"])
def pipeline_alerts():
    """Returns the latest FDA safety alerts ingested via Kafka."""
    payload, status = svc.pipeline_alerts()
    return jsonify(payload), status


@pipeline_bp.route("/api/pipeline/pubmed", methods=["GET"])
def pipeline_pubmed():
    """Returns the latest PubMed papers ingested via Kafka."""
    payload, status = svc.pipeline_pubmed()
    return jsonify(payload), status


@pipeline_bp.route("/api/pipeline/trials", methods=["GET"])
def pipeline_trials():
    """Returns active clinical trials ingested via Kafka."""
    payload, status = svc.pipeline_trials()
    return jsonify(payload), status


@pipeline_bp.route("/api/faers/releases", methods=["GET"])
def faers_releases():
    """Loaded FAERS quarters and newer quarters announced on the faers-release topic."""
    payload, status = svc.faers_releases()
    return jsonify(payload), status


@pipeline_bp.route("/api/admin/faers/refresh", methods=["POST"])
@require_admin
def faers_refresh():
    """Start the download-parse-load-identity job for an announced quarter."""
    body = request.get_json(silent=True) or {}
    payload, status = svc.start_faers_refresh(body.get("quarter"))
    return jsonify(payload), status


@pipeline_bp.route("/api/evidence", methods=["GET"])
def evidence():
    """External evidence (alerts, papers, trials) linked to a drug, optionally to one term."""
    drug = (request.args.get("drug") or "").strip()
    if not drug:
        return jsonify({"error": "drug is required"}), 400
    payload, status = evidence_svc.links_for(drug, pt=(request.args.get("pt") or "").strip() or None)
    return jsonify(payload), status

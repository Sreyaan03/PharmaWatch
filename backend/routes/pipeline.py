# backend/routes/pipeline.py
# HTTP layer only: parse the request, call the service, return JSON.

from flask import Blueprint, jsonify
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

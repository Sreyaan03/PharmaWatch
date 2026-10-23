# backend/routes/interactions.py
# HTTP layer only: parse the request, call the service, return JSON.

from flask import Blueprint, request, jsonify
from core.http_params import bounded_int
from services import interactions as svc

interactions_bp = Blueprint("interactions", __name__)

@interactions_bp.route("/api/graph", methods=["GET"])
def get_interaction_graph():
    """Fetches REAL co-prescribed drug data from openFDA for the selected drug."""
    target_drug = request.args.get('drug', 'Metformin').upper()
    payload, status = svc.get_interaction_graph(target_drug)
    return jsonify(payload), status


@interactions_bp.route("/api/graph/detailed", methods=["GET"])
def get_detailed_graph():
    """Enhanced graph: co-prescription + FDA label warnings."""
    target_drug = request.args.get('drug', 'Metformin')
    payload, status = svc.get_detailed_graph(target_drug)
    return jsonify(payload), status


@interactions_bp.route("/api/graph/twosides", methods=["GET"])
def get_twosides_graph():
    """Fetches known interactions from HBase (TWOSIDES dataset)."""
    raw = request.args.get("drug", "").strip()
    payload, status = svc.get_twosides_graph(raw)
    return jsonify(payload), status


@interactions_bp.route("/api/graph/predict", methods=["POST"])
def predict_interaction():
    """Runs GNN inference on a drug pair."""
    body = request.get_json(silent=True) or {}
    payload, status = svc.predict_interaction(body)
    return jsonify(payload), status


@interactions_bp.route("/api/interactions/resolve", methods=["GET"])
def resolve_drug_endpoint():
    """Resolve a drug name or SMILES string."""
    raw = (request.args.get("drug") or request.args.get("input") or "").strip()
    payload, status = svc.resolve_drug_endpoint(raw)
    return jsonify(payload), status


@interactions_bp.route("/api/interactions/polypharmacy", methods=["POST"])
def polypharmacy_analysis():
    """Accepts up to 5 drugs, runs GNN on combinations and fetches details."""
    body  = request.get_json(silent=True) or {}
    payload, status = svc.polypharmacy_analysis(body)
    return jsonify(payload), status


@interactions_bp.route("/api/interactions/uncharted", methods=["GET"])
def uncharted_interactions():
    """Priority #2 — Uncharted Interactions panel."""
    drugs_param = request.args.get("drugs", "")
    min_reports = bounded_int(request.args.get("min_reports"), 50, 0, 100000)
    payload, status = svc.uncharted_interactions(drugs_param, min_reports)
    return jsonify(payload), status


@interactions_bp.route("/api/interactions/recent", methods=["GET"])
def recent_predictions():
    """Return the last N GNN predictions stored in SQLite."""
    limit = bounded_int(request.args.get("limit"), 20, 1, 100)
    payload, status = svc.recent_predictions(limit)
    return jsonify(payload), status

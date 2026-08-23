# backend/routes/analytics.py
# ─────────────────────────────────────────────────────────────────────────────
# Analytics Routes: Local DuckDB FAERS data analytics
# ─────────────────────────────────────────────────────────────────────────────

from flask import Blueprint, jsonify, request
from faers.analytics import (
    compute_prr_duckdb,
    get_temporal_trend,
    get_top_reactions,
    get_data_coverage
)

analytics_bp = Blueprint("analytics", __name__)

@analytics_bp.route("/api/analytics/prr", methods=["GET"])
def api_prr():
    """Computes disproportionality metrics (PRR, ROR, BCPNN) locally via DuckDB."""
    drug = request.args.get("drug", "Metformin").strip()
    event = request.args.get("event", "Nausea").strip()
    
    if not drug or not event:
        return jsonify({"error": "Parameters 'drug' and 'event' are required."}), 400
        
    result = compute_prr_duckdb(drug, event)
    if result is None:
        return jsonify({
            "error": "DuckDB offline or not initialized",
            "source": "fallback_fda"
        }), 503
        
    return jsonify(result)

@analytics_bp.route("/api/analytics/temporal", methods=["GET"])
def api_temporal():
    """Returns monthly or quarterly report counts over time for a drug."""
    drug = request.args.get("drug", "Metformin").strip()
    granularity = request.args.get("granularity", "month").strip()
    
    if not drug:
        return jsonify({"error": "Parameter 'drug' is required."}), 400
        
    result = get_temporal_trend(drug, granularity)
    if result is None:
        return jsonify({
            "error": "DuckDB offline or not initialized"
        }), 503
        
    return jsonify(result)

@analytics_bp.route("/api/analytics/top-reactions", methods=["GET"])
def api_top_reactions():
    """Returns top MedDRA adverse events for a drug."""
    drug = request.args.get("drug", "Metformin").strip()
    limit = request.args.get("limit", 20, type=int)
    
    if not drug:
        return jsonify({"error": "Parameter 'drug' is required."}), 400
        
    result = get_top_reactions(drug, limit)
    if result is None:
        return jsonify({
            "error": "DuckDB offline or not initialized"
        }), 503
        
    return jsonify(result)

@analytics_bp.route("/api/analytics/data-coverage", methods=["GET"])
def api_data_coverage():
    """Returns data coverage statistics of the loaded DuckDB database."""
    return jsonify(get_data_coverage())

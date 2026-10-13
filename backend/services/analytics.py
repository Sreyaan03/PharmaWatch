# backend/services/analytics.py
# Business logic for backend/routes/analytics.py (no Flask imports).

from faers.analytics import (
    compute_prr_duckdb,
    get_temporal_trend,
    get_top_reactions,
    get_data_coverage
)

def api_prr(drug, event):
    """Computes disproportionality metrics (PRR, ROR, BCPNN) locally via DuckDB."""
    if not drug or not event:
        return {"error": "Parameters 'drug' and 'event' are required."}, 400
        
    result = compute_prr_duckdb(drug, event)
    if result is None:
        return {
            "error": "DuckDB offline or not initialized",
            "source": "fallback_fda"
        }, 503
        
    return result, 200

def api_temporal(drug, granularity):
    """Returns monthly or quarterly report counts over time for a drug."""
    if not drug:
        return {"error": "Parameter 'drug' is required."}, 400
        
    result = get_temporal_trend(drug, granularity)
    if result is None:
        return {
            "error": "DuckDB offline or not initialized"
        }, 503
        
    return result, 200

def api_top_reactions(drug, limit):
    """Returns top MedDRA adverse events for a drug."""
    if not drug:
        return {"error": "Parameter 'drug' is required."}, 400
        
    result = get_top_reactions(drug, limit)
    if result is None:
        return {
            "error": "DuckDB offline or not initialized"
        }, 503
        
    return result, 200

def api_data_coverage():
    """Returns data coverage statistics of the loaded DuckDB database."""
    return get_data_coverage(), 200

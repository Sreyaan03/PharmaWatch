# backend/services/temporal.py
# Business logic for backend/routes/temporal.py (no Flask imports).

from clients.http import http_requests
from core.cache import PayloadCache
from faers.stats import zscore_spikes
from faers.analytics import (
    get_temporal_velocity_duckdb,
    get_tto_duckdb,
    get_seasonality_duckdb
)
import logging
log = logging.getLogger(__name__)

_cache = PayloadCache("temporal_cache", "data_type", "TEMPORAL")
get_cached_payload = _cache.get
set_cached_payload = _cache.set

def api_temporal_velocity(drug, event):
    """Calculates monthly report velocity and detects velocity spikes (Z-score > 2.0)."""
    if not drug:
        return {"error": "Parameter 'drug' is required."}, 400

    cached = get_cached_payload(drug, event, "velocity")
    if cached:
        return cached, 200

    # 1. Try DuckDB local FAERS dataset first
    duck_res = get_temporal_velocity_duckdb(drug, event)
    if duck_res and len(duck_res.get("counts", [])) > 0:
        set_cached_payload(drug, event, "velocity", duck_res)
        return duck_res, 200

    # 2. Try openFDA live API
    FDA_BASE = "https://api.fda.gov/drug/event.json"
    url = f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug}"+AND+patient.reaction.reactionmeddrapt:"{event}"&count=receivedate'

    months = []
    counts = []

    try:
        res = http_requests.get(url, timeout=5).json()
        results = res.get("results", [])
        if results:
            monthly_map = {}
            for item in results:
                time_str = str(item.get("time", ""))
                if len(time_str) >= 6:
                    ym = time_str[:6]
                    monthly_map[ym] = monthly_map.get(ym, 0) + item.get("count", 0)

            sorted_ym = sorted(monthly_map.keys())[-24:]
            for ym in sorted_ym:
                months.append(ym)
                counts.append(monthly_map[ym])
    except Exception as e:
        log.warning(f"[TEMPORAL VELOCITY FDA FETCH WARN] {e}, falling back to synthetic trend")

    # 3. No data found — return empty instead of synthetic data
    if not counts:
        return {
            "drug": drug,
            "event": event,
            "months": [],
            "counts": [],
            "spikes": [],
            "z_scores": [],
            "has_spike": False,
            "source": "no_data",
            "message": f"No FAERS reports found for '{drug}' + '{event}'. This drug-event pair may not exist in the database."
        }, 200

    spikes, z_scores = zscore_spikes(counts)

    payload = {
        "drug": drug,
        "event": event,
        "months": months,
        "counts": counts,
        "spikes": spikes,
        "z_scores": z_scores,
        "has_spike": any(spikes),
        "source": "openfda"
    }

    set_cached_payload(drug, event, "velocity", payload)
    return payload, 200

def api_temporal_tto(drug, event):
    """Generates Time-To-Onset (TTO) distribution for drug-event pair from FAERS DuckDB or fallback."""
    if not drug:
        return {"error": "Parameter 'drug' is required."}, 400

    cached = get_cached_payload(drug, event, "tto")
    if cached:
        return cached, 200

    # 1. Try DuckDB local FAERS dataset first
    duck_res = get_tto_duckdb(drug, event)
    if duck_res and sum(duck_res.get("counts", [])) > 0:
        set_cached_payload(drug, event, "tto", duck_res)
        return duck_res, 200

    # 2. No data — return empty
    return {
        "drug": drug,
        "event": event,
        "buckets": ["0-30 days", "31-60 days", "61-90 days", "91-180 days", "181-365 days", ">365 days"],
        "counts": [0, 0, 0, 0, 0, 0],
        "total_reports": 0,
        "median_days": None,
        "peak_bucket": None,
        "source": "no_data",
        "message": f"No FAERS reports found for '{drug}' + '{event}'."
    }, 200

def api_temporal_seasonality(drug, event):
    """Calculates 12-month seasonality index for reporting patterns from FAERS DuckDB or fallback."""
    if not drug:
        return {"error": "Parameter 'drug' is required."}, 400

    cached = get_cached_payload(drug, event, "seasonality")
    if cached:
        return cached, 200

    # 1. Try DuckDB local FAERS dataset first
    duck_res = get_seasonality_duckdb(drug, event)
    if duck_res and sum(duck_res.get("counts", [])) > 0:
        set_cached_payload(drug, event, "seasonality", duck_res)
        return duck_res, 200

    # 2. No data — return empty
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return {
        "drug": drug,
        "event": event,
        "months": months,
        "counts": [0] * 12,
        "seasonality_index": [0.0] * 12,
        "elevated_months": [],
        "is_seasonal": False,
        "source": "no_data",
        "message": f"No FAERS reports found for '{drug}' + '{event}'."
    }, 200

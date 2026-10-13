# backend/services/temporal.py
# Business logic for backend/routes/temporal.py (no Flask imports).

import json
import numpy as np
from datetime import datetime, timedelta
from db.mysql import query_one, execute
from clients.http import http_requests
from faers.analytics import (
    get_temporal_velocity_duckdb,
    get_tto_duckdb,
    get_seasonality_duckdb
)

def get_cached_payload(drug, event, data_type):
    """Retrieve cached payload from temporal_cache if less than 24 hours old."""
    try:
        sql = "SELECT payload, cached_at FROM temporal_cache WHERE drug = %s AND event = %s AND data_type = %s LIMIT 1"
        row = query_one(sql, (drug.lower(), event.lower(), data_type))
        if row and row.get("payload"):
            cached_at = row["cached_at"]
            if isinstance(cached_at, str):
                try:
                    cached_at = datetime.fromisoformat(cached_at.rstrip("Z"))
                except ValueError:
                    cached_at = datetime.strptime(cached_at[:19], "%Y-%m-%d %H:%M:%S")
            if datetime.now() - cached_at < timedelta(hours=24):
                return json.loads(row["payload"])
    except Exception as e:
        print(f"[TEMPORAL CACHE ERROR] {e}")
    return None

def set_cached_payload(drug, event, data_type, payload):
    """Cache payload in MySQL."""
    try:
        sql = """
            INSERT INTO temporal_cache (drug, event, data_type, payload, cached_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON DUPLICATE KEY UPDATE payload = VALUES(payload), cached_at = NOW()
        """
        execute(sql, (drug.lower(), event.lower(), data_type, json.dumps(payload)))
    except Exception as e:
        print(f"[TEMPORAL CACHE SAVE ERROR] {e}")

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
        print(f"[TEMPORAL VELOCITY FDA FETCH WARN] {e}, falling back to synthetic trend")

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

    spikes = []
    z_scores = []
    counts_arr = np.array(counts, dtype=float)

    for i in range(len(counts)):
        if i < 3:
            z_scores.append(0.0)
            spikes.append(False)
        else:
            window = counts_arr[max(0, i-12):i]
            mean = np.mean(window)
            std = np.std(window)
            if std == 0:
                std = 1.0
            z = (counts_arr[i] - mean) / std
            z_scores.append(round(float(z), 2))
            spikes.append(bool(z > 2.0))

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

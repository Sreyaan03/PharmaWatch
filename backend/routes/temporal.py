# backend/routes/temporal.py
# ─────────────────────────────────────────────────────────────────────────────
# Temporal Analysis Routes: Velocity spikes, Time-to-Onset (TTO), Seasonality
# Priority 1: DuckDB FAERS dataset
# Priority 2: openFDA REST API
# Priority 3: Graceful fallback
# ─────────────────────────────────────────────────────────────────────────────

import json
import numpy as np
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify

from database import get_db
from helpers.http_client import http_requests
from faers.analytics import (
    get_temporal_velocity_duckdb,
    get_tto_duckdb,
    get_seasonality_duckdb
)

temporal_bp = Blueprint("temporal", __name__)

def get_cached_payload(drug, event, data_type):
    """Retrieve cached payload from temporal_cache if less than 24 hours old."""
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT payload, cached_at FROM temporal_cache WHERE drug=? AND event=? AND data_type=?",
            (drug.lower(), event.lower(), data_type)
        ).fetchone()
        conn.close()
        if row:
            cached_at = datetime.strptime(row['cached_at'][:19], "%Y-%m-%d %H:%M:%S")
            if datetime.now() - cached_at < timedelta(hours=24):
                return json.loads(row['payload'])
    except Exception as e:
        print(f"[TEMPORAL CACHE ERROR] {e}")
    return None

def set_cached_payload(drug, event, data_type, payload):
    """Cache payload in SQLite."""
    try:
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO temporal_cache (drug, event, data_type, payload) VALUES (?, ?, ?, ?)",
            (drug.lower(), event.lower(), data_type, json.dumps(payload))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[TEMPORAL CACHE SAVE ERROR] {e}")


@temporal_bp.route("/api/temporal/velocity", methods=["GET"])
def api_temporal_velocity():
    """Calculates monthly report velocity and detects velocity spikes (Z-score > 2.0)."""
    drug = request.args.get("drug", "Metformin").strip()
    event = request.args.get("event", "Nausea").strip()

    if not drug:
        return jsonify({"error": "Parameter 'drug' is required."}), 400

    cached = get_cached_payload(drug, event, "velocity")
    if cached:
        return jsonify(cached)

    # 1. Try DuckDB local FAERS dataset first
    duck_res = get_temporal_velocity_duckdb(drug, event)
    if duck_res and len(duck_res.get("counts", [])) > 0:
        set_cached_payload(drug, event, "velocity", duck_res)
        return jsonify(duck_res)

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
        return jsonify({
            "drug": drug,
            "event": event,
            "months": [],
            "counts": [],
            "spikes": [],
            "z_scores": [],
            "has_spike": False,
            "source": "no_data",
            "message": f"No FAERS reports found for '{drug}' + '{event}'. This drug-event pair may not exist in the database."
        })

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
    return jsonify(payload)


@temporal_bp.route("/api/temporal/tto", methods=["GET"])
def api_temporal_tto():
    """Generates Time-To-Onset (TTO) distribution for drug-event pair from FAERS DuckDB or fallback."""
    drug = request.args.get("drug", "Metformin").strip()
    event = request.args.get("event", "Nausea").strip()

    if not drug:
        return jsonify({"error": "Parameter 'drug' is required."}), 400

    cached = get_cached_payload(drug, event, "tto")
    if cached:
        return jsonify(cached)

    # 1. Try DuckDB local FAERS dataset first
    duck_res = get_tto_duckdb(drug, event)
    if duck_res and sum(duck_res.get("counts", [])) > 0:
        set_cached_payload(drug, event, "tto", duck_res)
        return jsonify(duck_res)

    # 2. No data — return empty
    return jsonify({
        "drug": drug,
        "event": event,
        "buckets": ["0-30 days", "31-60 days", "61-90 days", "91-180 days", "181-365 days", ">365 days"],
        "counts": [0, 0, 0, 0, 0, 0],
        "total_reports": 0,
        "median_days": None,
        "peak_bucket": None,
        "source": "no_data",
        "message": f"No FAERS reports found for '{drug}' + '{event}'."
    })


@temporal_bp.route("/api/temporal/seasonality", methods=["GET"])
def api_temporal_seasonality():
    """Calculates 12-month seasonality index for reporting patterns from FAERS DuckDB or fallback."""
    drug = request.args.get("drug", "Metformin").strip()
    event = request.args.get("event", "Nausea").strip()

    if not drug:
        return jsonify({"error": "Parameter 'drug' is required."}), 400

    cached = get_cached_payload(drug, event, "seasonality")
    if cached:
        return jsonify(cached)

    # 1. Try DuckDB local FAERS dataset first
    duck_res = get_seasonality_duckdb(drug, event)
    if duck_res and sum(duck_res.get("counts", [])) > 0:
        set_cached_payload(drug, event, "seasonality", duck_res)
        return jsonify(duck_res)

    # 2. No data — return empty
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return jsonify({
        "drug": drug,
        "event": event,
        "months": months,
        "counts": [0] * 12,
        "seasonality_index": [0.0] * 12,
        "elevated_months": [],
        "is_seasonal": False,
        "source": "no_data",
        "message": f"No FAERS reports found for '{drug}' + '{event}'."
    })

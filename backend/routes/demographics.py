# backend/routes/demographics.py
# ─────────────────────────────────────────────────────────────────────────────
# Patient Stratification & Demographics Routes (Sex, Age, Geo)
# Priority 1: DuckDB FAERS dataset
# Priority 2: openFDA REST API (live)
# Priority 3: Return no_data — never synthesize fake results
# ─────────────────────────────────────────────────────────────────────────────

import json
from datetime import datetime, timedelta
from flask import Blueprint, request, jsonify

from database import get_db
from helpers.http_client import http_requests
from faers.analytics import (
    get_demographics_sex_duckdb,
    get_demographics_age_duckdb,
    get_demographics_geo_duckdb
)

demographics_bp = Blueprint("demographics", __name__)

FDA_BASE = "https://api.fda.gov/drug/event.json"

def get_cached_payload(drug, event, strata_type):
    """Retrieve cached payload from demographic_cache if less than 24 hours old."""
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT payload, cached_at FROM demographic_cache WHERE drug=? AND event=? AND strata_type=?",
            (drug.lower(), event.lower(), strata_type)
        ).fetchone()
        conn.close()
        if row:
            cached_at = datetime.strptime(row['cached_at'][:19], "%Y-%m-%d %H:%M:%S")
            if datetime.now() - cached_at < timedelta(hours=24):
                return json.loads(row['payload'])
    except Exception as e:
        print(f"[DEMO CACHE ERROR] {e}")
    return None

def set_cached_payload(drug, event, strata_type, payload):
    """Cache payload in SQLite."""
    try:
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO demographic_cache (drug, event, strata_type, payload) VALUES (?, ?, ?, ?)",
            (drug.lower(), event.lower(), strata_type, json.dumps(payload))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[DEMO CACHE SAVE ERROR] {e}")

def _no_data_response(drug, event, strata_type):
    """Standard empty response when no real data exists."""
    return {
        "drug": drug,
        "event": event,
        "source": "no_data",
        "total_reports": 0,
        "message": f"No FAERS reports found for '{drug}' + '{event}'. This drug-event pair may not exist in the database."
    }


@demographics_bp.route("/api/demographics/sex", methods=["GET"])
def api_demographics_sex():
    """Returns sex-stratified report counts (Male vs Female vs Unknown) from real FAERS data."""
    drug  = request.args.get("drug",  "Metformin").strip()
    event = request.args.get("event", "Nausea").strip()

    if not drug:
        return jsonify({"error": "Parameter 'drug' is required."}), 400

    cached = get_cached_payload(drug, event, "sex")
    if cached:
        return jsonify(cached)

    # 1. Try DuckDB local FAERS dataset first
    duck_res = get_demographics_sex_duckdb(drug, event)
    if duck_res and duck_res.get("total_reports", 0) > 0:
        set_cached_payload(drug, event, "sex", duck_res)
        return jsonify(duck_res)

    # 2. Try openFDA live API — count by patient sex
    try:
        url = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug}"'
               f'+AND+patient.reaction.reactionmeddrapt:"{event}"'
               f'&count=patient.patientsex')
        res = http_requests.get(url, timeout=6).json()
        results = res.get("results", [])
        if results:
            sex_map = {str(r.get("term", "")): r.get("count", 0) for r in results}
            # openFDA sex codes: 1=Male, 2=Female, 0=Unknown
            f_cnt = sex_map.get("2", 0)
            m_cnt = sex_map.get("1", 0)
            u_cnt = sex_map.get("0", 0)
            total = f_cnt + m_cnt + u_cnt
            if total > 0:
                risk_ratio = round(f_cnt / max(1, m_cnt), 2)
                payload = {
                    "drug": drug, "event": event,
                    "female_count": int(f_cnt), "male_count": int(m_cnt), "unknown_count": int(u_cnt),
                    "total_reports": int(total),
                    "female_prr": None, "male_prr": None, "overall_prr": None,
                    "sex_risk_ratio": risk_ratio,
                    "higher_risk_group": "Female" if risk_ratio > 1.1 else ("Male" if risk_ratio < 0.9 else "Balanced"),
                    "source": "openfda"
                }
                set_cached_payload(drug, event, "sex", payload)
                return jsonify(payload)
    except Exception as e:
        print(f"[DEMO SEX FDA WARN] {e}")

    # 3. No data — return empty
    return jsonify(_no_data_response(drug, event, "sex"))


@demographics_bp.route("/api/demographics/age", methods=["GET"])
def api_demographics_age():
    """Returns age-stratified report counts from real FAERS data."""
    drug  = request.args.get("drug",  "Metformin").strip()
    event = request.args.get("event", "Nausea").strip()

    if not drug:
        return jsonify({"error": "Parameter 'drug' is required."}), 400

    cached = get_cached_payload(drug, event, "age")
    if cached:
        return jsonify(cached)

    # 1. Try DuckDB local FAERS dataset first
    duck_res = get_demographics_age_duckdb(drug, event)
    if duck_res and duck_res.get("total_reports", 0) > 0:
        set_cached_payload(drug, event, "age", duck_res)
        return jsonify(duck_res)

    # 2. Try openFDA live API — count by patient age group
    try:
        url = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug}"'
               f'+AND+patient.reaction.reactionmeddrapt:"{event}"'
               f'&count=patient.patientonsetage')
        res = http_requests.get(url, timeout=6).json()
        results = res.get("results", [])
        if results:
            # Bucket raw ages into groups
            pediatric = adult = geriatric = 0
            for r in results:
                age = r.get("term", 0)
                cnt = r.get("count", 0)
                try:
                    age = float(age)
                    if age < 18:   pediatric += cnt
                    elif age < 65: adult     += cnt
                    else:          geriatric += cnt
                except Exception:
                    adult += cnt
            total = pediatric + adult + geriatric
            if total > 0:
                counts = [pediatric, adult, geriatric]
                max_idx = counts.index(max(counts))
                groups = ["Pediatric (<18)", "Adult (18-64)", "Geriatric (>=65)"]
                payload = {
                    "drug": drug, "event": event,
                    "groups": groups, "counts": counts,
                    "prr_values": [None, None, None],
                    "total_reports": total,
                    "highest_risk_group": groups[max_idx],
                    "source": "openfda"
                }
                set_cached_payload(drug, event, "age", payload)
                return jsonify(payload)
    except Exception as e:
        print(f"[DEMO AGE FDA WARN] {e}")

    # 3. No data — return empty
    return jsonify({
        **_no_data_response(drug, event, "age"),
        "groups": ["Pediatric (<18)", "Adult (18-64)", "Geriatric (>=65)"],
        "counts": [0, 0, 0],
        "prr_values": [None, None, None],
        "highest_risk_group": None,
    })


@demographics_bp.route("/api/demographics/geo", methods=["GET"])
def api_demographics_geo():
    """Returns country report counts from real FAERS data."""
    drug  = request.args.get("drug",  "Metformin").strip()
    event = request.args.get("event", "Nausea").strip()

    if not drug:
        return jsonify({"error": "Parameter 'drug' is required."}), 400

    cached = get_cached_payload(drug, event, "geo")
    if cached:
        return jsonify(cached)

    # 1. Try DuckDB local FAERS dataset first
    duck_res = get_demographics_geo_duckdb(drug, event)
    if duck_res and duck_res.get("total_reports", 0) > 0:
        set_cached_payload(drug, event, "geo", duck_res)
        return jsonify(duck_res)

    # 2. Try openFDA live API — count by country
    try:
        url = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug}"'
               f'+AND+patient.reaction.reactionmeddrapt:"{event}"'
               f'&count=primarysource.reportercountry.exact&limit=15')
        res = http_requests.get(url, timeout=6).json()
        results = res.get("results", [])
        if results:
            countries = [r.get("term", "??") for r in results]
            counts    = [r.get("count", 0)   for r in results]
            total     = sum(counts)
            mean_cnt  = total / max(1, len(counts))
            grrs      = [round(c / max(1.0, mean_cnt), 2) for c in counts]
            if total > 0:
                payload = {
                    "drug": drug, "event": event,
                    "countries": countries, "counts": counts,
                    "grr_scores": grrs,
                    "total_reports": total,
                    "top_country": countries[0] if countries else None,
                    "source": "openfda"
                }
                set_cached_payload(drug, event, "geo", payload)
                return jsonify(payload)
    except Exception as e:
        print(f"[DEMO GEO FDA WARN] {e}")

    # 3. No data — return empty
    return jsonify({
        **_no_data_response(drug, event, "geo"),
        "countries": [], "counts": [], "grr_scores": [],
        "top_country": None,
    })


@demographics_bp.route("/api/demographics/overview", methods=["GET"])
def api_demographics_overview():
    """Combined endpoint returning sex, age, and geo stratification."""
    drug  = request.args.get("drug",  "Metformin").strip()
    event = request.args.get("event", "Nausea").strip()

    sex_res = get_demographics_sex_duckdb(drug, event) or {}
    age_res = get_demographics_age_duckdb(drug, event) or {}
    geo_res = get_demographics_geo_duckdb(drug, event) or {}

    return jsonify({"drug": drug, "event": event,
                    "sex": sex_res, "age": age_res, "geo": geo_res})

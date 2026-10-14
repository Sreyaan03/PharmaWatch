# backend/services/temporal.py
# Business logic for backend/routes/temporal.py (no Flask imports).

from clients.http import http_requests
from core.cache import PayloadCache
from faers.stats import (MIN_SEASONALITY_YEARS, MIN_VELOCITY_MONTHS, SEASONALITY_ALPHA,
                        seasonality_test, zscore_spikes)
from faers import analytics
from faers.analytics import (
    get_temporal_velocity_duckdb,
    get_seasonality_duckdb
)
import logging
log = logging.getLogger(__name__)

ALGORITHM = "temporal-v3"      # bump when a calculation changes, so older cache entries are not served

_cache = PayloadCache("temporal_cache", "data_type", "TEMPORAL")


def _version():
    from faers.registry import data_version
    return f"{data_version()}|{ALGORITHM}"


def get_cached_payload(drug, event, kind):
    return _cache.get(drug, event, kind, version=_version())


def set_cached_payload(drug, event, kind, payload):
    _cache.set(drug, event, kind, payload, version=_version())

def api_temporal_velocity(drug, event):
    """Calculates monthly report velocity and detects velocity spikes (Z-score > 2.0)."""
    if not drug:
        return {"error": "Parameter 'drug' is required."}, 400

    cached = get_cached_payload(drug, event, "velocity")
    if cached:
        return cached, 200

    # 1. Loaded FAERS data, by month of receipt, for the resolved ingredient and term
    from services.signals import resolve_drug_event
    ctx = resolve_drug_event(drug, event)
    if ctx["ready"]:
        empty = {"drug": drug, "event": event, "months": [], "counts": [], "spikes": [],
                 "z_scores": [], "has_spike": False, "source": "no_data",
                 "identity": ctx["identity"]}
        if not ctx["ok"]:
            return {**empty, "unresolved": ctx["unresolved"],
                    "message": ctx["note"] or "The drug or event could not be resolved."}, 200
        res = analytics.get_receipt_velocity(ctx["rxcuis"], ctx["pt"])
        if res is None or sum(res["counts"]) == 0:
            return {**empty, "message": f"No FAERS reports found for '{drug}' + '{event}'."}, 200
        if res["span_months"] < MIN_VELOCITY_MONTHS:
            return {**empty, "span_months": res["span_months"],
                    "message": (f"{res['span_months']} month(s) of reports are loaded; at least "
                                f"{MIN_VELOCITY_MONTHS} are needed to look for a spike.")}, 200
        spikes, z_scores = zscore_spikes(res["counts"])
        payload = {"drug": drug, "event": event, "months": res["months"], "counts": res["counts"],
                   "spikes": spikes, "z_scores": z_scores, "has_spike": any(spikes),
                   "basis": "reports by month of FDA receipt", "span_months": res["span_months"],
                   "identity": ctx["identity"], "source": "faers_local"}
        set_cached_payload(drug, event, "velocity", payload)
        return payload, 200

    # 2. A database without drug identity: the older text-matched query, then openFDA live
    duck_res = get_temporal_velocity_duckdb(drug, event)
    if duck_res and len(duck_res.get("counts", [])) > 0:
        set_cached_payload(drug, event, "velocity", duck_res)
        return duck_res, 200

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
    """Time to onset: days from the start of the suspect drug to the event, from the reports' own dates.

    Only reports with a complete event date and therapy start date contribute; the response says
    how many of the pair's reports that is, and how many were left out and why.
    """
    if not drug:
        return {"error": "Parameter 'drug' is required."}, 400

    cached = get_cached_payload(drug, event, "tto")
    if cached:
        return cached, 200

    from services.signals import resolve_drug_event
    ctx = resolve_drug_event(drug, event)
    base = {
        "drug": drug, "event": event,
        "buckets": ["0-30 days", "31-60 days", "61-90 days", "91-180 days", "181-365 days", ">365 days"],
        "counts": [0, 0, 0, 0, 0, 0], "total_reports": 0, "median_days": None, "peak_bucket": None,
    }
    if ctx["ready"] and not ctx["ok"]:
        return {**base, "source": "no_data", "unresolved": ctx["unresolved"], "identity": ctx["identity"],
                "message": ctx["note"] or "The drug or event could not be resolved."}, 200

    if ctx["ok"]:
        res = analytics.get_time_to_onset(ctx["rxcuis"], ctx["pt"])
        if res is not None:
            payload = {
                **base, **res,
                "total_reports": res["usable_cases"],
                "identity": ctx["identity"],
                "source": "faers_local",
            }
            if res["usable_cases"] < 10:
                payload["source"] = "no_data"
                payload["message"] = (
                    f"Only {res['usable_cases']} of {res['cases']} reports for this pair have a complete "
                    "event date and therapy start date; at least 10 are needed for a time-to-onset result."
                )
            set_cached_payload(drug, event, "tto", payload)
            return payload, 200

    return {**base, "source": "no_data", "message": f"No FAERS reports found for '{drug}' + '{event}'."}, 200

def api_temporal_seasonality(drug, event):
    """Calculates 12-month seasonality index for reporting patterns from FAERS DuckDB or fallback."""
    if not drug:
        return {"error": "Parameter 'drug' is required."}, 400

    cached = get_cached_payload(drug, event, "seasonality")
    if cached:
        return cached, 200

    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

    # 1. Loaded FAERS data: event months over the calendar years the window covers completely
    from services.signals import resolve_drug_event
    ctx = resolve_drug_event(drug, event)
    if ctx["ready"]:
        empty = {"drug": drug, "event": event, "months": months, "counts": [0] * 12,
                 "seasonality_index": [0.0] * 12, "elevated_months": [], "is_seasonal": False,
                 "source": "no_data", "identity": ctx["identity"]}
        if not ctx["ok"]:
            return {**empty, "unresolved": ctx["unresolved"],
                    "message": ctx["note"] or "The drug or event could not be resolved."}, 200
        res = analytics.get_event_seasonality(ctx["rxcuis"], ctx["pt"])
        years = res["years"] if res else []
        if len(years) < MIN_SEASONALITY_YEARS:
            return {**empty, "years_used": years,
                    "message": (f"Seasonality needs at least {MIN_SEASONALITY_YEARS} complete calendar "
                                f"years inside the loaded data; {len(years)} qualify. A single "
                                f"quarter of reports cannot show a pattern across the year.")}, 200
        counts = res["counts"]
        total = sum(counts)
        test = seasonality_test(counts)
        is_seasonal = bool(test["applicable"] and test["p_value"] < SEASONALITY_ALPHA)
        avg = total / 12 if total else 1.0
        index = [round(c / avg, 2) for c in counts]
        payload = {
            "drug": drug, "event": event, "months": months, "counts": counts,
            "seasonality_index": index,
            "elevated_months": [months[i] for i, v in enumerate(index) if v >= 1.15] if is_seasonal else [],
            "is_seasonal": is_seasonal,
            "test": {"name": "chi-square vs days-in-month", "alpha": SEASONALITY_ALPHA, **test},
            "years_used": years, "basis": "month of event, complete calendar years only",
            "identity": ctx["identity"], "source": "faers_local",
        }
        if total == 0:
            payload.update(source="no_data", message=f"No FAERS reports found for '{drug}' + '{event}'.")
            return payload, 200
        set_cached_payload(drug, event, "seasonality", payload)
        return payload, 200

    # 2. A database without drug identity: the older text-matched query
    duck_res = get_seasonality_duckdb(drug, event)
    if duck_res and sum(duck_res.get("counts", [])) > 0:
        set_cached_payload(drug, event, "seasonality", duck_res)
        return duck_res, 200

    # 3. No data — return empty
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

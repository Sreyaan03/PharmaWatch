# backend/services/dashboard.py
# Business logic for backend/routes/dashboard.py (no Flask imports).

from datetime import datetime, timedelta, timezone
from db.mysql import query, query_one
import logging
log = logging.getLogger(__name__)

def local_stats():
    """Returns real counts from MySQL and DuckDB for dashboard animated counters."""
    try:
        r_drugs = query_one("SELECT COUNT(DISTINCT drug) AS cnt FROM signals_cache")
        drugs_monitored = int(r_drugs["cnt"]) if r_drugs and r_drugs.get("cnt") else 0

        # Only pairs that meet the signal criteria count as signals; "moderate" is the legacy
        # label for pairs that do not.
        r_signals = query_one(
            "SELECT COUNT(*) AS cnt FROM signals_cache WHERE signal_strength IN ('signal', 'strong_signal', 'critical', 'high')")
        signals_detected = int(r_signals["cnt"]) if r_signals and r_signals.get("cnt") else 0

        r_pairs = query_one("SELECT COUNT(*) AS cnt FROM drug_events")
        drug_event_pairs = int(r_pairs["cnt"]) if r_pairs and r_pairs.get("cnt") else 0

        r_inter = query_one("SELECT COUNT(*) AS cnt FROM interaction_predictions")
        interactions_analyzed = int(r_inter["cnt"]) if r_inter and r_inter.get("cnt") else 0

        # The number of reports comes from the loaded FAERS data or is not reported at all.
        reports_analyzed = None
        source_label = "mysql"
        try:
            from faers.analytics import get_data_coverage
            cov = get_data_coverage()
            if cov and cov.get("status") == "online" and cov.get("total_reports", 0) > 0:
                reports_analyzed = cov["total_reports"]
                source_label = "duckdb+mysql"
        except Exception as e:
            log.error(f"[DUCKDB COVERAGE ERROR] {e}")

        sources = query("SELECT source, COUNT(*) AS c FROM drug_events GROUP BY source") if drug_event_pairs > 0 else []

        return {
            "drugs_monitored": drugs_monitored,
            "signals_detected": signals_detected,
            "drug_event_pairs": drug_event_pairs,
            "interactions_analyzed": interactions_analyzed,
            "reports_analyzed": reports_analyzed,
            "total_records": drug_event_pairs,
            "by_source": {r["source"]: int(r["c"]) for r in sources if r.get("source")},
            "source": source_label
        }, 200
    except Exception as e:
        return {
            "drugs_monitored": 0, "signals_detected": 0,
            "drug_event_pairs": 0, "interactions_analyzed": 0,
            "reports_analyzed": 0, "total_records": 0, "by_source": {},
            "source": "fallback", "error": str(e)
        }, 200

# Rows cached before the classes were renamed carry the old labels.
_CLASS_OF = {"strong_signal": "strong_signal", "critical": "strong_signal",
             "signal": "signal", "high": "signal",
             "none": "none", "moderate": "none"}


def dashboard_signal_intensity():
    """Signals computed in each of the last 24 hours, by class, from the real computed_at times.

    This charts when pairs were computed, not when events occurred. With no recent rows the
    series are empty and `source` says so; nothing is distributed over the hours.
    """
    try:
        now = datetime.now(timezone.utc)
        hours = [(now - timedelta(hours=(23 - i))).strftime("%H:00") for i in range(24)]

        cutoff = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        sql = """
            SELECT DATE_FORMAT(computed_at, '%%H:00') AS hour, signal_strength AS severity, COUNT(*) AS cnt
            FROM signals_cache
            WHERE computed_at >= %s
            GROUP BY hour, signal_strength
        """
        rows = query(sql, (cutoff,))

        hourly = {}
        for r in rows:
            h = r.get("hour")
            if not h:
                continue
            cls = _CLASS_OF.get(r.get("severity"), "none")
            bucket = hourly.setdefault(h, {"strong_signal": 0, "signal": 0, "none": 0})
            bucket[cls] += int(r["cnt"])

        series = {c: [hourly.get(h, {}).get(c, 0) for h in hours]
                  for c in ("strong_signal", "signal", "none")}
        return {
            "labels": hours,
            "strong_signal": series["strong_signal"],
            "signal": series["signal"],
            "none": series["none"],
            "source": "mysql_real_timestamps" if rows else "no_data",
        }, 200
    except Exception as e:
        now = datetime.now(timezone.utc)
        hours_fb = [(now - timedelta(hours=(23 - i))).strftime("%H:00") for i in range(24)]
        return {
            "labels": hours_fb, "strong_signal": [0] * 24,
            "signal": [0] * 24, "none": [0] * 24,
            "source": "fallback", "error": str(e)
        }, 200

def dashboard_drug_categories():
    """Returns drug category distribution from the signals cache."""
    CATEGORY_MAP = {
        "cardiovascular": ["warfarin", "aspirin", "clopidogrel", "atorvastatin", "lisinopril",
                           "amlodipine", "metoprolol", "losartan", "digoxin", "amiodarone",
                           "carvedilol", "furosemide", "spironolactone", "hydrochlorothiazide"],
        "antibiotics":    ["amoxicillin", "azithromycin", "ciprofloxacin", "doxycycline",
                           "metronidazole", "vancomycin", "cephalexin", "trimethoprim",
                           "levofloxacin", "clarithromycin", "penicillin", "erythromycin"],
        "cns_psychiatric":["sertraline", "fluoxetine", "escitalopram", "citalopram",
                           "venlafaxine", "quetiapine", "olanzapine", "risperidone",
                           "aripiprazole", "clonazepam", "alprazolam", "lorazepam",
                           "gabapentin", "pregabalin", "lithium", "lamotrigine"],
        "diabetes":       ["metformin", "insulin", "glipizide", "glyburide", "sitagliptin",
                           "pioglitazone", "empagliflozin", "liraglutide", "glargine",
                           "canagliflozin", "semaglutide", "dulaglutide"],
        "pain_nsaid":     ["ibuprofen", "naproxen", "celecoxib", "diclofenac", "indomethacin",
                           "morphine", "oxycodone", "hydrocodone", "tramadol", "acetaminophen",
                           "fentanyl", "buprenorphine", "codeine"],
    }
    try:
        rows = query("SELECT DISTINCT drug FROM signals_cache LIMIT 500")
        drugs = [r["drug"].lower() for r in rows if r.get("drug")]
        
        counts = {cat: 0 for cat in CATEGORY_MAP}
        counts["other"] = 0
        for drug in drugs:
            matched = False
            for cat, keywords in CATEGORY_MAP.items():
                if any(kw in drug for kw in keywords):
                    counts[cat] += 1
                    matched = True
                    break
            if not matched:
                counts["other"] += 1
                
        source = "mysql_signals_cache" if drugs else "empty_cache"
        return {
            "labels": ["Cardiovascular", "Antibiotics", "CNS/Psychiatric", "Diabetes", "Pain/NSAID", "Other"],
            "data": [
                counts["cardiovascular"], counts["antibiotics"], counts["cns_psychiatric"],
                counts["diabetes"], counts["pain_nsaid"], counts["other"]
            ],
            "source": source
        }, 200
    except Exception as e:
        return {
            "labels": ["Cardiovascular", "Antibiotics", "CNS/Psychiatric", "Diabetes", "Pain/NSAID", "Other"],
            "data": [0, 0, 0, 0, 0, 0],
            "source": "fallback_error", "error": str(e)
        }, 200

def prr_distribution():
    """Returns counts of signals bucketed by PRR ranges."""
    try:
        sql = """
            SELECT 
              SUM(CASE WHEN prr >= 1.0 AND prr < 1.5 THEN 1 ELSE 0 END) as bin1,
              SUM(CASE WHEN prr >= 1.5 AND prr < 2.0 THEN 1 ELSE 0 END) as bin2,
              SUM(CASE WHEN prr >= 2.0 AND prr < 2.5 THEN 1 ELSE 0 END) as bin3,
              SUM(CASE WHEN prr >= 2.5 AND prr < 3.0 THEN 1 ELSE 0 END) as bin4,
              SUM(CASE WHEN prr >= 3.0 AND prr < 3.5 THEN 1 ELSE 0 END) as bin5,
              SUM(CASE WHEN prr >= 3.5 AND prr < 4.0 THEN 1 ELSE 0 END) as bin6,
              SUM(CASE WHEN prr >= 4.0 AND prr < 5.0 THEN 1 ELSE 0 END) as bin7,
              SUM(CASE WHEN prr >= 5.0 THEN 1 ELSE 0 END) as bin8
            FROM signals_cache
        """
        bins = query_one(sql)
        bin_counts = [int(bins.get(f"bin{i}") or 0) for i in range(1, 9)] if bins else [0]*8
        return {
            "labels": ["1.0–1.5", "1.5–2.0", "2.0–2.5", "2.5–3.0", "3.0–3.5", "3.5–4.0", "4.0–5.0", "5.0+"],
            "data": bin_counts,
            "source": "mysql_signals_cache"
        }, 200
    except Exception as e:
        return {
            "labels": ["1.0–1.5", "1.5–2.0", "2.0–2.5", "2.5–3.0", "3.0–3.5", "3.5–4.0", "4.0–5.0", "5.0+"],
            "data": [0]*8,
            "source": "fallback_error",
            "error": str(e)
        }, 200


# Product-use and administrative terms: reported often, but they describe how a product was
# used or recorded, not a patient's adverse reaction. They are left out of "top reported
# events" and the response lists them so the exclusion is visible.
NON_CLINICAL_EVENT_TERMS = [
    "off label use", "drug ineffective", "product dose omission issue",
    "product use in unapproved indication", "intentional product use issue",
    "product use issue", "wrong technique in product usage process",
    "inappropriate schedule of product administration", "incorrect dose administered",
    "drug dose omission",
]


def top_events(limit=7):
    """Most reported adverse event terms in the loaded FAERS data, with real case counts."""
    from faers.analytics import get_top_events
    from faers.registry import data_version

    limit = max(1, min(int(limit), 25))
    res = get_top_events(limit, exclude=NON_CLINICAL_EVENT_TERMS)
    if res is None or not res["events"]:
        return {"events": [], "total_cases": 0, "source": "no_data",
                "message": "No FAERS data is loaded."}, 200
    return {
        "events": res["events"],
        "total_cases": res["total_cases"],
        "excluded_terms": NON_CLINICAL_EVENT_TERMS,
        "data_version": data_version(),
        "source": "faers_local",
    }, 200

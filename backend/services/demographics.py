# backend/services/demographics.py
# Patient stratification for a drug-event pair: sex, age and country.
#
# Every stratum is a 2x2 table of its own (the reports of that sex or age group only), so each
# PRR is a real disproportionality measure for that group with its own interval. A group is
# called "higher risk" only when its interval lies entirely above every other group's; otherwise
# the response says no difference is shown. Nothing here is estimated from constants.

import logging

from clients.http import http_requests
from core.cache import PayloadCache
from faers import analytics

log = logging.getLogger(__name__)

FDA_BASE = "https://api.fda.gov/drug/event.json"
MIN_REPORTS = 10                       # fewer drug-event reports than this: too few to stratify
ALGORITHM = "strata-v2"                # bump when the calculation changes, so old cache entries are not served

AGE_GROUPS = ["Pediatric (<18)", "Adult (18-64)", "Geriatric (>=65)"]

_cache = PayloadCache("demographic_cache", "strata_type", "DEMO")


def _version():
    from faers.registry import data_version
    return f"{data_version()}|{ALGORITHM}"


def get_cached_payload(drug, event, strata_type):
    return _cache.get(drug, event, strata_type, version=_version())


def set_cached_payload(drug, event, strata_type, payload):
    _cache.set(drug, event, strata_type, payload, version=_version())


def _no_data_response(drug, event, strata_type, **extra):
    """Standard empty response when no real data exists."""
    out = {
        "drug": drug,
        "event": event,
        "source": "no_data",
        "total_reports": 0,
        "message": f"No FAERS reports found for '{drug}' + '{event}'. This drug-event pair may not exist in the database.",
    }
    out.update(extra)
    return out


def _trim(m):
    """The parts of a stratum's metrics the API exposes; PRR and ROR are null when undefined."""
    ok = m["defined"]
    return {
        "a": m["a"], "b": m["b"], "c": m["c"], "d": m["d"], "n": m.get("n"),
        "prr": m["prr"] if ok else None,
        "prr_ci_low": m["prr_ci_low"] if ok else None,
        "prr_ci_high": m["prr_ci_high"] if ok else None,
        "ror": m["ror"] if ok else None,
        "signal_strength": m["signal_strength"] if ok else "unavailable",
    }


def _clearly_higher(name, strata):
    """The stratum whose PRR interval lies entirely above all the others', or None."""
    mine = strata[name]
    if mine["prr_ci_low"] is None:
        return False
    return all(o["prr_ci_high"] is None or mine["prr_ci_low"] > o["prr_ci_high"]
               for k, o in strata.items() if k != name and o["prr_ci_low"] is not None) \
        and any(o["prr_ci_low"] is not None for k, o in strata.items() if k != name)


def _resolve(drug, event):
    from services.signals import resolve_drug_event
    return resolve_drug_event(drug, event)


def _unresolved_response(drug, event, strata_type, ctx, **extra):
    return _no_data_response(
        drug, event, strata_type, unresolved=ctx["unresolved"], identity=ctx["identity"],
        **({"note": ctx["note"]} if ctx["note"] else {}), **extra)


# ── sex ──────────────────────────────────────────────────────────────────────────
def api_demographics_sex(drug, event):
    """Sex-stratified reports with a PRR (and interval) computed within each sex."""
    if not drug:
        return {"error": "Parameter 'drug' is required."}, 400

    cached = get_cached_payload(drug, event, "sex")
    if cached:
        return cached, 200

    ctx = _resolve(drug, event)
    if ctx["ready"] and not ctx["ok"]:
        return _unresolved_response(drug, event, "sex", ctx), 200
    if ctx["ok"]:
        res = analytics.get_sex_strata(ctx["rxcuis"], ctx["pt"])
        if res is not None:
            f, m = _trim(res["strata"]["F"]), _trim(res["strata"]["M"])
            total = f["a"] + m["a"] + res["unknown"]
            if total >= MIN_REPORTS:
                overall = analytics.compute_prr_ingredients(ctx["rxcuis"], ctx["pt"])
                if f["prr_ci_low"] is not None and f["prr_ci_low"] > (m["prr_ci_high"] or float("inf")):
                    higher = "Female"
                elif m["prr_ci_low"] is not None and m["prr_ci_low"] > (f["prr_ci_high"] or float("inf")):
                    higher = "Male"
                else:
                    higher = "No difference shown"
                ratio = round(f["prr"] / m["prr"], 2) if f["prr"] and m["prr"] else None
                payload = {
                    "drug": drug, "event": event,
                    "female_count": f["a"], "male_count": m["a"], "unknown_count": res["unknown"],
                    "total_reports": total,
                    "female_prr": f["prr"], "male_prr": m["prr"],
                    "overall_prr": overall["prr"] if overall and overall.get("defined") else None,
                    "strata": {"F": f, "M": m},
                    "sex_risk_ratio": ratio,
                    "higher_risk_group": higher,
                    "interpretation": ("The female and male PRR intervals overlap: no difference is shown."
                                       if higher == "No difference shown" else
                                       f"The {higher.lower()} PRR interval lies entirely above the other."),
                    "identity": ctx["identity"],
                    "source": "faers_local",
                }
                set_cached_payload(drug, event, "sex", payload)
                return payload, 200

    # openFDA live counts: counts only. Raw counts by sex say nothing about risk, so no group is named.
    try:
        url = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug}"'
               f'+AND+patient.reaction.reactionmeddrapt:"{event}"'
               f'&count=patient.patientsex')
        results = http_requests.get(url, timeout=15).json().get("results", [])
        if results:
            sex_map = {str(r.get("term", "")): r.get("count", 0) for r in results}
            f_cnt, m_cnt, u_cnt = sex_map.get("2", 0), sex_map.get("1", 0), sex_map.get("0", 0)
            total = f_cnt + m_cnt + u_cnt
            if total > 0:
                payload = {
                    "drug": drug, "event": event,
                    "female_count": int(f_cnt), "male_count": int(m_cnt), "unknown_count": int(u_cnt),
                    "total_reports": int(total),
                    "female_prr": None, "male_prr": None, "overall_prr": None,
                    "sex_risk_ratio": None, "higher_risk_group": None,
                    "interpretation": "Counts only: raw report counts by sex do not measure risk.",
                    "source": "openfda", "fallback": True,
                }
                return payload, 200
    except Exception as e:
        log.warning(f"[DEMO SEX FDA WARN] {e}")

    return _no_data_response(drug, event, "sex"), 200


# ── age ──────────────────────────────────────────────────────────────────────────
def api_demographics_age(drug, event):
    """Age-group reports with a PRR (and interval) computed within each group."""
    if not drug:
        return {"error": "Parameter 'drug' is required."}, 400

    cached = get_cached_payload(drug, event, "age")
    if cached:
        return cached, 200

    empty = {"groups": AGE_GROUPS, "counts": [0, 0, 0], "prr_values": [None, None, None],
             "highest_risk_group": None}
    ctx = _resolve(drug, event)
    if ctx["ready"] and not ctx["ok"]:
        return _unresolved_response(drug, event, "age", ctx, **empty), 200
    if ctx["ok"]:
        res = analytics.get_age_strata(ctx["rxcuis"], ctx["pt"])
        if res is not None:
            strata = {g: _trim(res["strata"][g]) for g in AGE_GROUPS}
            total = sum(strata[g]["a"] for g in AGE_GROUPS) + res["unknown"]
            if total >= MIN_REPORTS:
                highest = next((g for g in AGE_GROUPS if _clearly_higher(g, strata)), None)
                payload = {
                    "drug": drug, "event": event,
                    "groups": AGE_GROUPS,
                    "counts": [strata[g]["a"] for g in AGE_GROUPS],
                    "prr_values": [strata[g]["prr"] for g in AGE_GROUPS],
                    "prr_ci": [[strata[g]["prr_ci_low"], strata[g]["prr_ci_high"]] for g in AGE_GROUPS],
                    "strata": strata,
                    "unknown_age_count": res["unknown"],
                    "total_reports": total,
                    "highest_risk_group": highest,
                    "interpretation": (f"{highest} has a PRR interval entirely above the other groups."
                                       if highest else "No age group's PRR interval lies entirely above the others."),
                    "identity": ctx["identity"],
                    "source": "faers_local",
                }
                set_cached_payload(drug, event, "age", payload)
                return payload, 200

    try:
        url = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug}"'
               f'+AND+patient.reaction.reactionmeddrapt:"{event}"'
               f'&count=patient.patientonsetage')
        results = http_requests.get(url, timeout=15).json().get("results", [])
        if results:
            pediatric = adult = geriatric = 0
            for r in results:
                cnt = r.get("count", 0)
                try:
                    age = float(r.get("term", 0))
                except (TypeError, ValueError):
                    continue                      # an unreadable age is not assigned to a group
                if age < 18:
                    pediatric += cnt
                elif age < 65:
                    adult += cnt
                else:
                    geriatric += cnt
            total = pediatric + adult + geriatric
            if total > 0:
                return {
                    "drug": drug, "event": event, "groups": AGE_GROUPS,
                    "counts": [pediatric, adult, geriatric], "prr_values": [None, None, None],
                    "total_reports": total, "highest_risk_group": None,
                    "interpretation": "Counts only: raw report counts by age group do not measure risk.",
                    "source": "openfda", "fallback": True,
                }, 200
    except Exception as e:
        log.warning(f"[DEMO AGE FDA WARN] {e}")

    return {**_no_data_response(drug, event, "age"), **empty}, 200


# ── country ──────────────────────────────────────────────────────────────────────
def api_demographics_geo(drug, event):
    """Reports by country of occurrence. This is where reports come from, not where risk is higher."""
    if not drug:
        return {"error": "Parameter 'drug' is required."}, 400

    cached = get_cached_payload(drug, event, "geo")
    if cached:
        return cached, 200

    empty = {"countries": [], "counts": [], "share_pct": [], "grr_scores": [], "top_country": None}
    note = ("Share of this pair's reports by country of occurrence. It reflects reporting volume "
            "(population, pharmacovigilance systems), not a difference in risk.")
    ctx = _resolve(drug, event)
    if ctx["ready"] and not ctx["ok"]:
        return _unresolved_response(drug, event, "geo", ctx, **empty), 200
    if ctx["ok"]:
        rows = analytics.get_country_counts(ctx["rxcuis"], ctx["pt"], 15)
        if rows is not None:
            total = sum(n for _, n in rows)
            if total >= MIN_REPORTS:
                counts = [n for _, n in rows]
                mean = total / len(counts)
                payload = {
                    "drug": drug, "event": event,
                    "countries": [c for c, _ in rows], "counts": counts,
                    "share_pct": [round(100 * n / total, 1) for n in counts],
                    "grr_scores": [round(n / mean, 2) for n in counts],     # kept for older clients
                    "total_reports": total,
                    "top_country": rows[0][0],
                    "note": note, "identity": ctx["identity"], "source": "faers_local",
                }
                set_cached_payload(drug, event, "geo", payload)
                return payload, 200

    try:
        url = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug}"'
               f'+AND+patient.reaction.reactionmeddrapt:"{event}"'
               f'&count=occurcountry.exact&limit=15')
        results = http_requests.get(url, timeout=15).json().get("results", [])
        if results:
            countries = [r.get("term", "??") for r in results]
            counts = [r.get("count", 0) for r in results]
            total = sum(counts)
            if total > 0:
                mean = total / len(counts)
                return {
                    "drug": drug, "event": event, "countries": countries, "counts": counts,
                    "share_pct": [round(100 * n / total, 1) for n in counts],
                    "grr_scores": [round(n / mean, 2) for n in counts],
                    "total_reports": total, "top_country": countries[0],
                    "note": note, "source": "openfda", "fallback": True,
                }, 200
    except Exception as e:
        log.warning(f"[DEMO GEO FDA WARN] {e}")

    return {**_no_data_response(drug, event, "geo"), **empty}, 200


def api_demographics_overview(drug, event):
    """Combined sex, age and country stratification."""
    return {
        "drug": drug, "event": event,
        "sex": api_demographics_sex(drug, event)[0],
        "age": api_demographics_age(drug, event)[0],
        "geo": api_demographics_geo(drug, event)[0],
    }, 200

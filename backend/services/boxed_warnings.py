# backend/services/boxed_warnings.py
# Boxed-warning domain logic.
#
# What these functions report is how adverse events were *reported* for a drug whose label has a
# boxed warning. They do not say that a warning worked or failed, that a drug was prescribed
# against a warning, or that a reported event was caused by the drug. A warning year comes only
# from a sourced record (backend/data/boxed_warning_dates.csv) or from the caller; the label's
# revision date is returned separately and is not the year the warning was issued.

from datetime import datetime

from database import insert_violation, get_violations, get_all_violations
from clients.http import http_requests
from services.warning_rules import (REPORTING_CAVEAT, event_in_warning, resolve_warning_year,
                                    synonyms_by_term)

FDA_BASE = "https://api.fda.gov/drug/event.json"
LABEL_BASE = "https://api.fda.gov/drug/label.json"

WEBER_WINDOW_YEARS = 2
EARLY_PEAK_RATIO = 1.3          # window peak vs later average, for the sourced-year analysis
RELATIVE_PEAK_RATIO = 1.4       # early-third peak vs the rest, for the descriptive pattern


# ── shared lookups ────────────────────────────────────────────────────────────
def _synonyms():
    try:
        from services.identity import load_synonyms
        return synonyms_by_term(load_synonyms())
    except Exception:
        return {}


def _label_with_boxed_warning(drug_name):
    """The openFDA label carrying a boxed warning for the drug, or None."""
    for field in ("generic_name", "brand_name"):
        url = f'{LABEL_BASE}?search=openfda.{field}:"{drug_name}"+AND+_exists_:boxed_warning&limit=1'
        res = http_requests.get(url, timeout=10).json()
        if res.get("results"):
            return res["results"][0]
    return None


def _revision_date(label):
    raw = (label or {}).get("effective_time", "")
    if raw and len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return None


def _reaction_counts(drug_name, extra_search=""):
    """(top reactions, total reports) for a drug, trying generic name then product name."""
    events, total = [], 0
    for field in ("patient.drug.openfda.generic_name", "patient.drug.medicinalproduct"):
        base = f'{FDA_BASE}?search={field}:"{drug_name}"{extra_search}'
        if not total:
            res = http_requests.get(f"{base}&limit=1", timeout=10).json()
            total = res.get("meta", {}).get("results", {}).get("total", 0)
        if not events:
            res = http_requests.get(f"{base}&count=patient.reaction.reactionmeddrapt.exact&limit=25",
                                    timeout=10).json()
            events = res.get("results", [])
        if events and total:
            break
    return events, total


def _reports_by_year(drug_name):
    """{year: reports} by receive date, from openFDA, up to the current year."""
    raw = []
    for field in ("patient.drug.openfda.generic_name", "patient.drug.medicinalproduct"):
        url = f'{FDA_BASE}?search={field}:"{drug_name}"&count=receivedate'
        raw = http_requests.get(url, timeout=12).json().get("results", [])
        if raw:
            break
    this_year = datetime.now().year
    year_counts = {}
    for entry in raw:
        time_str = str(entry.get("time", ""))
        if len(time_str) >= 4 and time_str[:4].isdigit():
            yr = int(time_str[:4])
            if 1990 <= yr <= this_year:
                year_counts[yr] = year_counts.get(yr, 0) + entry.get("count", 0)
    return year_counts


def _no_warning(drug_name, **extra):
    return {"drug": drug_name, "has_boxed_warning": False,
            "message": "No boxed warning was found on the label for this drug, so no "
                       "warning-relative analysis was run.", **extra}


def _warning_year_fields(info):
    return {"warning_year": info["year"], "warning_year_source": info["origin"],
            "warning_year_source_url": info["source_url"]}


def _snippet(text, phrase):
    idx = text.lower().find(phrase)
    if idx < 0:
        return ""
    return text[max(0, idx - 60):min(len(text), idx + 120)].strip()


# ── label and event cross-reference ──────────────────────────────────────────
def get_boxed_warning(drug_name):
    """Fetches the boxed warning text from the openFDA Drug Labeling API for ANY drug."""
    try:
        label = _label_with_boxed_warning(drug_name)
        if label is None:
            return {
                "has_warning": False,
                "drug": drug_name,
                "warning_text": "",
                "brand_name": "",
                "generic_name": ""
            }, 200

        warning_text = " ".join(label.get("boxed_warning", [""]))
        openfda = label.get("openfda", {})
        return {
            "has_warning": True,
            "drug": drug_name,
            "warning_text": warning_text,
            "label_revision_date": _revision_date(label),
            "brand_name": ", ".join(openfda.get("brand_name", [])),
            "generic_name": ", ".join(openfda.get("generic_name", [])),
            "pharm_class": ", ".join(openfda.get("pharm_class_epc", [])),
            "route": ", ".join(openfda.get("route", []))
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500


def get_boxed_warning_events(drug_name):
    """Reported event terms for a drug, split by whether the boxed warning names them."""
    try:
        events, total_reports = _reaction_counts(drug_name)
        label = _label_with_boxed_warning(drug_name)
        warning_text = " ".join(label.get("boxed_warning", [""])) if label else ""
        synonyms = _synonyms()

        warned_events, other_events, boxed_total = [], [], 0
        for ev in events:
            term, count = ev["term"], ev["count"]
            hit = event_in_warning(term, warning_text, synonyms)
            entry = {
                "term": term,
                "count": count,
                "is_boxed_warning": hit is not None,
                "matched_by": hit["by"] if hit else None,
                "percentage": round(count / total_reports * 100, 2) if total_reports > 0 else 0
            }
            if hit:
                warned_events.append(entry)
                boxed_total += count
            else:
                other_events.append(entry)

        boxed_pct = round(boxed_total / total_reports * 100, 2) if total_reports > 0 else 0
        return {
            "drug": drug_name,
            "total_reports": total_reports,
            "has_boxed_warning": bool(warning_text),
            "warned_events": warned_events,
            "other_events": other_events,
            "boxed_warning_event_count": boxed_total,
            "boxed_warning_percentage": boxed_pct,
            "matching": "whole reported term, or a reviewed synonym, named in the warning text",
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500


def get_trials(drug_name):
    """Fetches REAL clinical trial data from ClinicalTrials.gov API v2 for a drug."""
    CT_BASE = "https://clinicaltrials.gov/api/v2/studies"
    try:
        params = {
            "query.intr": drug_name,
            "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING,COMPLETED",
            "pageSize": 20,
            "fields": "NCTId,BriefTitle,OverallStatus,Phase,Condition,EnrollmentCount,StartDate,PrimaryCompletionDate,InterventionName"
        }
        res = http_requests.get(CT_BASE, params=params, timeout=10)
        data = res.json()
        studies = data.get("studies", [])

        results = []
        active_safety_count = 0
        recruiting_count = 0

        for study in studies:
            proto = study.get("protocolSection", {})
            id_module = proto.get("identificationModule", {})
            status_module = proto.get("statusModule", {})
            design_module = proto.get("designModule", {})
            conditions_module = proto.get("conditionsModule", {})

            nct_id = id_module.get("nctId", "")
            title = id_module.get("briefTitle", "Untitled Study")
            status = status_module.get("overallStatus", "UNKNOWN")
            phases = design_module.get("phases", ["N/A"])
            phase_str = ", ".join(phases) if phases else "N/A"
            conditions = conditions_module.get("conditions", [])
            enrollment = design_module.get("enrollmentInfo", {}).get("count", 0)
            start_date = status_module.get("startDateStruct", {}).get("date", "")

            is_safety_relevant = (
                any(p in ["PHASE3", "PHASE4"] for p in phases) or
                status in ["RECRUITING", "ACTIVE_NOT_RECRUITING"]
            )
            if is_safety_relevant:
                active_safety_count += 1
            if status == "RECRUITING":
                recruiting_count += 1

            results.append({
                "nct_id": nct_id,
                "title": title,
                "status": status,
                "phase": phase_str,
                "conditions": conditions[:5],
                "enrollment": enrollment,
                "start_date": start_date,
                "is_safety_relevant": is_safety_relevant,
                "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else ""
            })

        return {
            "drug": drug_name,
            "total_studies": len(results),
            "recruiting_count": recruiting_count,
            "active_safety_monitoring": active_safety_count > 0,
            "active_safety_count": active_safety_count,
            "studies": results
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500


# ── post-warning reports of labelled events ──────────────────────────────────
def get_post_warning_reports(drug_name, warning_year=None):
    """Reports of events the boxed warning names, with the split before/after the warning year.

    The split needs a sourced warning year; without one only total counts are returned.
    """
    try:
        label = _label_with_boxed_warning(drug_name)
        if label is None:
            stored = get_violations(drug_name)
            return _no_warning(drug_name, events=[], violations=[], events_detected=0,
                               violations_detected=0, stored=stored, stored_violations=stored), 200

        warning_text = " ".join(label.get("boxed_warning", [""]))
        info = resolve_warning_year(drug_name, warning_year)
        year = info["year"]
        synonyms = _synonyms()
        events, total_reports = _reaction_counts(drug_name)

        found = []
        for ev in events:
            term, total_count = ev["term"], ev["count"]
            hit = event_in_warning(term, warning_text, synonyms)
            if hit is None:
                continue
            percentage = round(total_count / total_reports * 100, 2) if total_reports > 0 else 0

            post_count = pre_count = None
            if year:
                since = f"{year}0101"
                post_count = 0
                for field in ("patient.drug.openfda.generic_name", "patient.drug.medicinalproduct"):
                    url = (f'{FDA_BASE}?search={field}:"{drug_name}"'
                           f'+AND+patient.reaction.reactionmeddrapt:"{term}"'
                           f'+AND+receivedate:[{since}+TO+99991231]&limit=1')
                    post_count = http_requests.get(url, timeout=10).json() \
                        .get("meta", {}).get("results", {}).get("total", 0)
                    if post_count:
                        break
                pre_count = max(0, total_count - post_count)

            snippet = _snippet(warning_text, hit["matched"])
            found.append({
                "term": term,
                "total_count": total_count,
                "post_count": post_count,
                "pre_count": pre_count,
                "percentage": percentage,
                "matched_by": hit["by"],
                "snippet": snippet[:200],
            })

            if year:
                insert_violation(
                    drug=drug_name,
                    adverse_event=term,
                    faers_count_post_warning=post_count,
                    faers_count_pre_warning=pre_count,
                    percentage_of_total=percentage,
                    warning_effective_date=f"{year}-01-01",
                    warning_text_snippet=snippet[:200] or None,
                    detection_method="post_warning_faers",
                )

        found.sort(key=lambda x: (x["post_count"] or 0, x["total_count"]), reverse=True)

        if year:
            message = (f"{len(found)} event type(s) named in the boxed warning were reported for "
                       f"'{drug_name}'; the post-warning count is reports received from {year} "
                       f"onward. {REPORTING_CAVEAT}")
        else:
            message = (f"{len(found)} event type(s) named in the boxed warning were reported for "
                       f"'{drug_name}'. No sourced warning year is on record for this drug, so "
                       f"reports are not split into before and after. Supply ?warning_year= or "
                       f"add the year, with a source, to boxed_warning_dates.csv.")

        stored = get_violations(drug_name)
        return {
            "drug": drug_name,
            "has_boxed_warning": True,
            "label_revision_date": _revision_date(label),
            **_warning_year_fields(info),
            "total_reports": total_reports,
            "events_detected": len(found),
            "events": found,
            "stored": stored,
            "message": message,
            # Legacy names, same data, kept so existing callers keep working.
            "warning_date": str(year) if year else "unknown",
            "total_faers_reports": total_reports,
            "violations_detected": len(found),
            "violations": found,
            "stored_violations": stored,
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500


def get_violations_for_drug(drug_name, warning_year=None):
    """Old name for get_post_warning_reports; returns the same data."""
    return get_post_warning_reports(drug_name, warning_year)


# ── reports over time around a warning year ──────────────────────────────────
def _slope(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0
    x_mean, y_mean = sum(xs) / n, sum(ys) / n
    num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))
    return round(num / den, 2) if den != 0 else 0.0


def get_warning_timeline(drug_name, warning_year=None):
    """Reports per year, with a before/after description when a warning year is sourced."""
    try:
        label = _label_with_boxed_warning(drug_name)
        if label is None:
            return _no_warning(drug_name, labels=[], counts=[]), 200

        info = resolve_warning_year(drug_name, warning_year)
        year = info["year"]
        year_counts = _reports_by_year(drug_name)
        if not year_counts:
            return {"drug": drug_name, "has_boxed_warning": True, "labels": [], "counts": [],
                    "label_revision_date": _revision_date(label), **_warning_year_fields(info),
                    "error": "No time-series data available from openFDA for this drug."}, 200

        years = sorted(year_counts)
        counts = [year_counts[y] for y in years]

        warning_index = pre_slope = post_slope = None
        if year:
            warning_index = next((i for i, y in enumerate(years) if y >= year), None)
            if warning_index is not None and warning_index > 1:
                pre_slope = _slope(list(range(warning_index)), counts[:warning_index])
                post_slope = _slope(list(range(len(years) - warning_index)), counts[warning_index:])

        if pre_slope is not None and post_slope is not None:
            interpretation = (
                f"Reports per year changed by {pre_slope:+.0f} per year before {year} and by "
                f"{post_slope:+.0f} per year from {year} on. {REPORTING_CAVEAT}")
        elif year:
            interpretation = (f"Too few years of data before {year} to compare slopes. "
                              f"{REPORTING_CAVEAT}")
        else:
            interpretation = ("No sourced warning year is on record for this drug, so annual "
                              "report counts are shown without a before/after comparison. "
                              + REPORTING_CAVEAT)

        return {
            "drug": drug_name,
            "has_boxed_warning": True,
            "labels": [str(y) for y in years],
            "counts": counts,
            "label_revision_date": _revision_date(label),
            **_warning_year_fields(info),
            "warning_index": warning_index,
            "pre_slope": pre_slope,
            "post_slope": post_slope,
            "interpretation": interpretation,
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500


def get_bias_analysis(drug_name, warning_year=None):
    """Descriptive look for an early peak in reporting (the pattern known as the Weber effect).

    With a sourced warning year the peak in the first years after it is compared with later
    years. Without one only the relative pattern (early third of the series against the rest) is
    described, and is labelled as such. Neither shows why reporting changed.
    """
    try:
        label = _label_with_boxed_warning(drug_name)
        if label is None:
            return _no_warning(drug_name, weber_peak_detected=False, labels=[], counts=[]), 200

        info = resolve_warning_year(drug_name, warning_year)
        year = info["year"]
        year_counts = _reports_by_year(drug_name)
        base = {"drug": drug_name, "has_boxed_warning": True, **_warning_year_fields(info),
                "label_revision_date": _revision_date(label), "descriptive": True}
        if not year_counts:
            return {**base, "weber_peak_detected": False, "labels": [], "counts": [],
                    "error": "No time-series data available."}, 200

        years = sorted(year_counts)
        counts = [year_counts[y] for y in years]
        detected, method, peak_year, note = False, None, None, ""
        data_first_year = years[0]

        if year and year >= data_first_year:
            window = [y for y in years if year <= y <= year + WEBER_WINDOW_YEARS]
            after = [y for y in years if y > year + WEBER_WINDOW_YEARS]
            if window and after:
                peak = max(year_counts[y] for y in window)
                after_avg = sum(year_counts[y] for y in after) / len(after)
                method = "window"
                if after_avg > 0 and peak > after_avg * EARLY_PEAK_RATIO:
                    detected = True
                    peak_year = next(y for y in window if year_counts[y] == peak)
                    note = (f"Reports peaked in {peak_year} ({peak:,}), "
                            f"{round((peak / after_avg - 1) * 100)}% above the average of the later "
                            f"years ({int(after_avg):,} per year), within {WEBER_WINDOW_YEARS} years "
                            f"of the {year} warning year.")
                else:
                    note = (f"No peak above {EARLY_PEAK_RATIO:.1f} times the later average in "
                            f"{year}-{year + WEBER_WINDOW_YEARS}.")
            else:
                note = (f"Too few years after {year} in the loaded series "
                        f"({years[0]}-{years[-1]}) to compare.")
        elif year:
            note = (f"The warning year {year} is before the first year of data ({data_first_year}); "
                    f"the window around it cannot be examined.")
        else:
            note = "No sourced warning year; only the relative pattern is described."

        if not detected and len(years) >= 6:
            third = max(1, len(years) // 3)
            early_peak = max(counts[:third])
            late_avg = sum(counts[third:]) / len(counts[third:])
            if late_avg > 0 and early_peak > late_avg * RELATIVE_PEAK_RATIO:
                detected, method = True, "relative_pattern"
                peak_year = years[counts.index(early_peak)]
                note = (f"Relative pattern: reports peaked early, in {peak_year} ({early_peak:,}), "
                        f"then settled near {int(late_avg):,} per year "
                        f"({round((early_peak / late_avg - 1) * 100)}% above). This is a description "
                        f"of the shape of the counts, not a dated warning effect.")
            elif method is None:
                method = "relative_pattern"
                note += (f" No early peak above {RELATIVE_PEAK_RATIO:.1f} times the later average "
                         f"in {years[0]}-{years[-1]}.")

        note = (note + " Early peaks of this shape are described in the literature as the Weber "
                "effect or notoriety bias, but report counts alone cannot show why reporting changed.")
        return {
            **base,
            "labels": [str(y) for y in years],
            "counts": counts,
            "weber_peak_detected": detected,
            "method": method,
            "peak_year": peak_year,
            "notoriety_note": note.strip(),
            "data_coverage": f"{years[0]}-{years[-1]}",
            "faers_predates_warning": bool(year and year < data_first_year),
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500


# ── signal and registered trials ─────────────────────────────────────────────
def get_prr_with_trials(drug, event):
    """Disproportionality for a pair next to registered trials that mention the drug or the pair.

    A registered trial is context, not confirmation: it says the drug or pair is under study.
    """
    CT_BASE = "https://clinicaltrials.gov/api/v2/studies"

    try:
        from services.signals import compute_disproportionality_helper
        metrics = compute_disproportionality_helper(drug, event) or {}
        available = bool(metrics) and not metrics.get("unresolved")
        a, b, c, d = (metrics.get(k, 0) for k in ("a", "b", "c", "d"))
        prr = metrics.get("prr", 0.0)
        has_signal = available and metrics.get("signal_strength", "none") != "none"

        def _studies(params):
            return http_requests.get(CT_BASE, params=params, timeout=10).json().get("studies", [])

        status_filter = "RECRUITING,ACTIVE_NOT_RECRUITING"
        pair_studies = _studies({"query.intr": drug, "query.cond": event,
                                 "filter.overallStatus": status_filter, "pageSize": 5,
                                 "fields": "NCTId,BriefTitle,OverallStatus,Phase,EnrollmentCount"})
        drug_studies = _studies({"query.intr": drug, "filter.overallStatus": status_filter,
                                 "pageSize": 5, "fields": "NCTId,BriefTitle,OverallStatus,Phase"})

        trial_matches = []
        for s in pair_studies:
            proto = s.get("protocolSection", {})
            id_mod = proto.get("identificationModule", {})
            nct_id = id_mod.get("nctId", "")
            trial_matches.append({
                "nct_id": nct_id,
                "title": id_mod.get("briefTitle", ""),
                "status": proto.get("statusModule", {}).get("overallStatus", ""),
                "phase": ", ".join(proto.get("designModule", {}).get("phases", ["N/A"])),
                "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else ""
            })

        if not available:
            context = "NOT_AVAILABLE"
            message = (f"No disproportionality could be computed for '{drug}' and '{event}' "
                       f"({', '.join(metrics.get('unresolved') or ['no data'])}).")
        elif has_signal and trial_matches:
            context = "SIGNAL_AND_EVENT_UNDER_STUDY"
            message = (f"PRR {prr:.2f} meets the signal criteria, and {len(trial_matches)} active "
                       f"registered stud{'y' if len(trial_matches) == 1 else 'ies'} also list "
                       f"'{drug}' with '{event}'. A registered trial shows the pair is under "
                       f"study; it is not evidence the signal is real.")
        elif has_signal and drug_studies:
            context = "SIGNAL_AND_DRUG_UNDER_STUDY"
            message = (f"PRR {prr:.2f} meets the signal criteria. {len(drug_studies)} active "
                       f"registered stud{'y' if len(drug_studies) == 1 else 'ies'} list '{drug}', "
                       f"none with '{event}'.")
        elif has_signal:
            context = "SIGNAL_ONLY"
            message = (f"PRR {prr:.2f} meets the signal criteria; no active registered trial "
                       f"lists '{drug}' with '{event}'.")
        else:
            context = "NO_SIGNAL"
            message = f"PRR {prr:.2f} does not meet the signal criteria in the data used."

        return {
            "drug": drug,
            "event": event,
            "prr": round(prr, 4),
            "a": a, "b": b, "c": c, "d": d,
            "is_signal": has_signal,
            "signal_strength": metrics.get("signal_strength", "none"),
            "trial_matches": trial_matches,
            "active_drug_study_count": len(drug_studies),
            "trial_context": context,
            "trial_context_message": message,
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500


def get_all_violations_route(limit_raw):
    """Return all stored post-warning report records."""
    try:
        limit = int(limit_raw)
        rows = get_all_violations(limit=limit)
        return {"count": len(rows), "reports": rows, "violations": rows}, 200
    except Exception as e:
        return {"error": str(e)}, 500


def get_all_post_warning_reports(limit_raw):
    return get_all_violations_route(limit_raw)

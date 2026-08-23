# backend/routes/boxed_warnings.py
# ─────────────────────────────────────────────────────────────────────────────
# Boxed Warnings routes: warnings lookup, violations tracking, Weber bias, trial data
# ─────────────────────────────────────────────────────────────────────────────

from flask import Blueprint, request, jsonify

from database import insert_violation, get_violations, get_all_violations
from helpers.external_apis import _get_true_warning_year
from helpers.http_client import http_requests

boxed_warnings_bp = Blueprint("boxed_warnings", __name__)

@boxed_warnings_bp.route("/api/boxed-warning/<drug_name>", methods=["GET"])
def get_boxed_warning(drug_name):
    """Fetches the boxed warning text from the openFDA Drug Labeling API for ANY drug."""
    LABEL_BASE = "https://api.fda.gov/drug/label.json"
    try:
        url = f'{LABEL_BASE}?search=openfda.generic_name:"{drug_name}"+AND+_exists_:boxed_warning&limit=1'
        res = http_requests.get(url, timeout=10).json()

        if "results" not in res or len(res["results"]) == 0:
            url2 = f'{LABEL_BASE}?search=openfda.brand_name:"{drug_name}"+AND+_exists_:boxed_warning&limit=1'
            res = http_requests.get(url2, timeout=10).json()

        if "results" not in res or len(res["results"]) == 0:
            return jsonify({
                "has_warning": False,
                "drug": drug_name,
                "warning_text": "",
                "brand_name": "",
                "generic_name": ""
            })

        label = res["results"][0]
        warning_text = " ".join(label.get("boxed_warning", [""]))
        openfda = label.get("openfda", {})

        return jsonify({
            "has_warning": True,
            "drug": drug_name,
            "warning_text": warning_text,
            "brand_name": ", ".join(openfda.get("brand_name", [])),
            "generic_name": ", ".join(openfda.get("generic_name", [])),
            "pharm_class": ", ".join(openfda.get("pharm_class_epc", [])),
            "route": ", ".join(openfda.get("route", []))
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@boxed_warnings_bp.route("/api/boxed-warning-events/<drug_name>", methods=["GET"])
def get_boxed_warning_events(drug_name):
    """Fetches FAERS AE counts and cross-references them with the boxed warning text."""
    FDA_BASE = "https://api.fda.gov/drug/event.json"
    LABEL_BASE = "https://api.fda.gov/drug/label.json"

    try:
        total_url = f'{FDA_BASE}?search=patient.drug.openfda.generic_name:"{drug_name}"&limit=1'
        total_res = http_requests.get(total_url, timeout=10).json()
        total_reports = total_res.get("meta", {}).get("results", {}).get("total", 0)

        if total_reports == 0:
            total_url2 = f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug_name}"&limit=1'
            total_res = http_requests.get(total_url2, timeout=10).json()
            total_reports = total_res.get("meta", {}).get("results", {}).get("total", 0)

        events_url = f'{FDA_BASE}?search=patient.drug.openfda.generic_name:"{drug_name}"&count=patient.reaction.reactionmeddrapt.exact&limit=25'
        events_res = http_requests.get(events_url, timeout=10).json()
        events = events_res.get("results", [])

        if not events:
            events_url2 = f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug_name}"&count=patient.reaction.reactionmeddrapt.exact&limit=25'
            events_res = http_requests.get(events_url2, timeout=10).json()
            events = events_res.get("results", [])

        label_url = f'{LABEL_BASE}?search=openfda.generic_name:"{drug_name}"+AND+_exists_:boxed_warning&limit=1'
        label_res = http_requests.get(label_url, timeout=10).json()
        warning_text = ""
        if "results" in label_res and len(label_res["results"]) > 0:
            warning_text = " ".join(label_res["results"][0].get("boxed_warning", [""])).lower()

        warned_events = []
        non_warned_events = []
        boxed_total = 0

        for ev in events:
            term = ev["term"]
            count = ev["count"]
            term_lower = term.lower().replace("_", " ").replace("^", "'")

            keywords = term_lower.split()
            is_match = False
            if warning_text:
                for kw in keywords:
                    if len(kw) >= 4 and kw in warning_text:
                        is_match = True
                        break

            entry = {
                "term": term,
                "count": count,
                "is_boxed_warning": is_match,
                "percentage": round(count / total_reports * 100, 2) if total_reports > 0 else 0
            }

            if is_match:
                warned_events.append(entry)
                boxed_total += count
            else:
                non_warned_events.append(entry)

        boxed_pct = round(boxed_total / total_reports * 100, 2) if total_reports > 0 else 0

        return jsonify({
            "drug": drug_name,
            "total_reports": total_reports,
            "has_boxed_warning": bool(warning_text),
            "warned_events": warned_events,
            "other_events": non_warned_events,
            "boxed_warning_event_count": boxed_total,
            "boxed_warning_percentage": boxed_pct
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@boxed_warnings_bp.route("/api/trials/<drug_name>", methods=["GET"])
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

        return jsonify({
            "drug": drug_name,
            "total_studies": len(results),
            "recruiting_count": recruiting_count,
            "active_safety_monitoring": active_safety_count > 0,
            "active_safety_count": active_safety_count,
            "studies": results
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@boxed_warnings_bp.route("/api/boxed-warning/violations/all", methods=["GET"])
def get_all_violations_route():
    """Return all violation records stored in the local SQLite database."""
    try:
        limit = int(request.args.get('limit', 200))
        rows = get_all_violations(limit=limit)
        return jsonify({"count": len(rows), "violations": rows})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@boxed_warnings_bp.route("/api/boxed-warning/violations/<drug_name>", methods=["GET"])
def get_violations_for_drug(drug_name):
    """Detects boxed-warning violations for a drug."""
    FDA_BASE   = "https://api.fda.gov/drug/event.json"
    LABEL_BASE = "https://api.fda.gov/drug/label.json"

    try:
        label_url = (f'{LABEL_BASE}?search=openfda.generic_name:"{drug_name}"'
                     f'+AND+_exists_:boxed_warning&limit=1')
        label_res = http_requests.get(label_url, timeout=10).json()

        if "results" not in label_res or not label_res["results"]:
            label_url2 = (f'{LABEL_BASE}?search=openfda.brand_name:"{drug_name}"'
                          f'+AND+_exists_:boxed_warning&limit=1')
            label_res = http_requests.get(label_url2, timeout=10).json()

        if "results" not in label_res or not label_res["results"]:
            return jsonify({
                "drug": drug_name,
                "has_boxed_warning": False,
                "violations": [],
                "stored_violations": get_violations(drug_name),
                "message": "No boxed warning found for this drug."
            })

        label        = label_res["results"][0]
        warning_text = " ".join(label.get("boxed_warning", [""]))
        effective_time_raw = label.get("effective_time", "")
        warning_date = None
        if effective_time_raw and len(effective_time_raw) == 8:
            warning_date = f"{effective_time_raw[:4]}-{effective_time_raw[4:6]}-{effective_time_raw[6:]}"

        events_url = (f'{FDA_BASE}?search=patient.drug.openfda.generic_name:"{drug_name}"'
                      f'&count=patient.reaction.reactionmeddrapt.exact&limit=25')
        events_res = http_requests.get(events_url, timeout=10).json()
        events = events_res.get("results", [])

        if not events:
            events_url2 = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug_name}"'
                           f'&count=patient.reaction.reactionmeddrapt.exact&limit=25')
            events_res2 = http_requests.get(events_url2, timeout=10).json()
            events = events_res2.get("results", [])

        total_url = (f'{FDA_BASE}?search=patient.drug.openfda.generic_name:"{drug_name}"'
                     f'&limit=1')
        total_res = http_requests.get(total_url, timeout=10).json()
        total_reports = total_res.get("meta", {}).get("results", {}).get("total", 0)
        if total_reports == 0:
            total_url2 = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug_name}"'
                          f'&limit=1')
            total_res2 = http_requests.get(total_url2, timeout=10).json()
            total_reports = total_res2.get("meta", {}).get("results", {}).get("total", 0)

        warning_lower = warning_text.lower()
        violations_found = []

        for ev in events:
            term       = ev["term"]
            total_count = ev["count"]
            term_lower = term.lower().replace("_", " ")

            keywords   = [w for w in term_lower.split() if len(w) >= 4]
            is_warned  = any(kw in warning_lower for kw in keywords)
            if not is_warned:
                continue

            percentage = round(total_count / total_reports * 100, 2) if total_reports > 0 else 0

            post_count = 0
            pre_count  = 0
            if warning_date:
                warn_dt_compact = warning_date.replace("-", "")
                post_url = (f'{FDA_BASE}?search=patient.drug.openfda.generic_name:"{drug_name}"'
                            f'+AND+patient.reaction.reactionmeddrapt:"{term}"'
                            f'+AND+receivedate:[{warn_dt_compact}+TO+99991231]&limit=1')
                post_res = http_requests.get(post_url, timeout=10).json()
                post_count = post_res.get("meta", {}).get("results", {}).get("total", 0)

                if post_count == 0:
                    post_url2 = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug_name}"'
                                 f'+AND+patient.reaction.reactionmeddrapt:"{term}"'
                                 f'+AND+receivedate:[{warn_dt_compact}+TO+99991231]&limit=1')
                    post_res2 = http_requests.get(post_url2, timeout=10).json()
                    post_count = post_res2.get("meta", {}).get("results", {}).get("total", 0)

                pre_count = max(0, total_count - post_count)
            else:
                post_count = total_count
                pre_count  = 0

            matched_kw = next((kw for kw in keywords if kw in warning_lower), "")
            snippet = ""
            if matched_kw:
                idx = warning_lower.find(matched_kw)
                start = max(0, idx - 60)
                end   = min(len(warning_text), idx + 120)
                snippet = warning_text[start:end].strip()

            violations_found.append({
                "term":             term,
                "total_count":      total_count,
                "post_count":       post_count,
                "pre_count":        pre_count,
                "percentage":       percentage,
                "warning_date":     warning_date or "unknown",
                "snippet":          snippet[:200] if snippet else "",
            })

            insert_violation(
                drug                    = drug_name,
                adverse_event           = term,
                faers_count_post_warning= post_count,
                faers_count_pre_warning = pre_count,
                percentage_of_total     = percentage,
                warning_effective_date  = warning_date,
                warning_text_snippet    = snippet[:200] if snippet else None,
                detection_method        = "post_warning_faers"
            )

        violations_found.sort(key=lambda x: x["post_count"], reverse=True)

        return jsonify({
            "drug":                 drug_name,
            "has_boxed_warning":    True,
            "warning_date":         warning_date or "unknown",
            "total_faers_reports":  total_reports,
            "violations_detected":  len(violations_found),
            "violations":           violations_found,
            "stored_violations":    get_violations(drug_name),
            "message":              (
                f"{len(violations_found)} warned adverse events found with post-warning "
                f"FAERS reports for '{drug_name}'. These represent continued prescriptions "
                f"despite the FDA Black Box Warning (effective {warning_date or 'date unknown'})."
            )
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@boxed_warnings_bp.route("/api/boxed-warning/timeline/<drug_name>", methods=["GET"])
def get_warning_timeline(drug_name):
    """Before & After analysis of FAERS reports around boxed warning date."""
    FDA_BASE   = "https://api.fda.gov/drug/event.json"
    LABEL_BASE = "https://api.fda.gov/drug/label.json"

    try:
        label_url = (f'{LABEL_BASE}?search=openfda.generic_name:"{drug_name}"'
                     f'+AND+_exists_:boxed_warning&limit=1')
        label_res = http_requests.get(label_url, timeout=10).json()
        if "results" not in label_res or not label_res["results"]:
            label_url2 = (f'{LABEL_BASE}?search=openfda.brand_name:"{drug_name}"'
                          f'+AND+_exists_:boxed_warning&limit=1')
            label_res = http_requests.get(label_url2, timeout=10).json()

        api_year = None
        if "results" in label_res and label_res["results"]:
            et = label_res["results"][0].get("effective_time", "")
            if et and len(et) >= 4:
                api_year = int(et[:4])
        warning_year = _get_true_warning_year(drug_name, api_year)

        yearly_url = (f'{FDA_BASE}?search=patient.drug.openfda.generic_name:"{drug_name}"'
                      f'&count=receivedate')
        yr_res = http_requests.get(yearly_url, timeout=12).json()
        raw = yr_res.get("results", [])

        if not raw:
            yearly_url2 = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug_name}"'
                           f'&count=receivedate')
            yr_res2 = http_requests.get(yearly_url2, timeout=12).json()
            raw = yr_res2.get("results", [])

        year_counts = {}
        for entry in raw:
            time_str = str(entry.get("time", ""))
            if len(time_str) >= 4:
                yr = int(time_str[:4])
                if 1990 <= yr <= 2025:
                    year_counts[yr] = year_counts.get(yr, 0) + entry.get("count", 0)

        if not year_counts:
            return jsonify({
                "drug": drug_name,
                "error": "No time-series data available from openFDA for this drug."
            })

        years  = sorted(year_counts.keys())
        counts = [year_counts[y] for y in years]

        def slope(xs, ys):
            n = len(xs)
            if n < 2: return 0.0
            x_mean = sum(xs) / n
            y_mean = sum(ys) / n
            num = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
            den = sum((xs[i] - x_mean) ** 2 for i in range(n))
            return round(num / den, 2) if den != 0 else 0.0

        warning_index = None
        pre_slope     = None
        post_slope    = None

        if warning_year and warning_year in years:
            warning_index = years.index(warning_year)
        elif warning_year:
            for i, y in enumerate(years):
                if y >= warning_year:
                    warning_index = i
                    break

        if warning_index is not None and warning_index > 1:
            pre_years   = years[:warning_index]
            pre_counts  = counts[:warning_index]
            post_years  = years[warning_index:]
            post_counts = counts[warning_index:]
            pre_slope   = slope(list(range(len(pre_years))),  pre_counts)
            post_slope  = slope(list(range(len(post_years))), post_counts)

        return jsonify({
            "drug":           drug_name,
            "labels":         [str(y) for y in years],
            "counts":         counts,
            "warning_year":   warning_year,
            "warning_index":  warning_index,
            "pre_slope":      pre_slope,
            "post_slope":     post_slope,
            "interpretation": (
                "Post-warning slope is lower than pre-warning — warning reduced prescribing."
                if (pre_slope is not None and post_slope is not None and post_slope < pre_slope)
                else (
                    "Post-warning slope remains elevated — warning has not substantially reduced prescribing."
                    if (pre_slope is not None and post_slope is not None)
                    else "Insufficient data for slope comparison."
                )
            )
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@boxed_warnings_bp.route("/api/boxed-warning/bias-analysis/<drug_name>", methods=["GET"])
def get_bias_analysis(drug_name):
    """Weber Effect + Notoriety Bias analysis."""
    FDA_BASE   = "https://api.fda.gov/drug/event.json"
    LABEL_BASE = "https://api.fda.gov/drug/label.json"

    try:
        label_url = (f'{LABEL_BASE}?search=openfda.generic_name:"{drug_name}"'
                     f'+AND+_exists_:boxed_warning&limit=1')
        label_res = http_requests.get(label_url, timeout=10).json()
        if "results" not in label_res or not label_res["results"]:
            label_url2 = (f'{LABEL_BASE}?search=openfda.brand_name:"{drug_name}"'
                          f'+AND+_exists_:boxed_warning&limit=1')
            label_res = http_requests.get(label_url2, timeout=10).json()

        api_year = None
        if "results" in label_res and label_res["results"]:
            et = label_res["results"][0].get("effective_time", "")
            if et and len(et) >= 4:
                api_year = int(et[:4])
        warning_year = _get_true_warning_year(drug_name, api_year)

        yearly_url = (f'{FDA_BASE}?search=patient.drug.openfda.generic_name:"{drug_name}"'
                      f'&count=receivedate')
        yr_res = http_requests.get(yearly_url, timeout=12).json()
        raw = yr_res.get("results", [])
        if not raw:
            yearly_url2 = (f'{FDA_BASE}?search=patient.drug.medicinalproduct:"{drug_name}"'
                           f'&count=receivedate')
            yr_res2 = http_requests.get(yearly_url2, timeout=12).json()
            raw = yr_res2.get("results", [])

        year_counts = {}
        for entry in raw:
            time_str = str(entry.get("time", ""))
            if len(time_str) >= 4:
                yr = int(time_str[:4])
                if 1990 <= yr <= 2025:
                    year_counts[yr] = year_counts.get(yr, 0) + entry.get("count", 0)

        if not year_counts:
            return jsonify({
                "drug": drug_name, "warning_year": warning_year,
                "weber_peak_detected": False,
                "error": "No time-series FAERS data available."
            })

        years  = sorted(year_counts.keys())
        counts = [year_counts[y] for y in years]

        weber_detected  = False
        peak_year       = None
        notoriety_note  = ""
        weber_tier      = None
        data_first_year = years[0] if years else None
        faers_predates_warning = (
            warning_year is not None and
            data_first_year is not None and
            warning_year < data_first_year
        )

        if not warning_year:
            notoriety_note = "Warning date unavailable — cannot assess Weber Effect."

        elif not faers_predates_warning:
            window_years  = [y for y in years if warning_year <= y <= warning_year + 2]
            window_counts = [year_counts[y] for y in window_years]
            after_years   = [y for y in years if y > warning_year + 2]
            after_counts  = [year_counts[y] for y in after_years]

            if window_counts and after_counts:
                window_peak = max(window_counts)
                after_avg   = sum(after_counts) / len(after_counts)
                if window_peak > after_avg * 1.3:
                    weber_detected = True
                    weber_tier = "exact"
                    peak_year  = window_years[window_counts.index(window_peak)]
                    pct_above  = round((window_peak / after_avg - 1) * 100)
                    notoriety_note = (
                        f"Weber Effect detected (Tier 1 — exact window): "
                        f"FAERS reports peaked in {peak_year} ({window_peak:,} reports), "
                        f"{pct_above}% above the post-window average ({int(after_avg):,}/yr). "
                        f"This spike reflects hyper-reporting driven by media coverage of "
                        f"the {warning_year} boxed warning (Notoriety Bias), not necessarily "
                        f"a true increase in adverse event incidence."
                    )
                else:
                    notoriety_note = (
                        f"No clear Weber peak in the {warning_year}–{warning_year+2} window "
                        f"for '{drug_name}'. Reporting appears stable relative to post-warning average."
                    )
            else:
                notoriety_note = (
                    f"Insufficient data in the exact warning window ({warning_year}–{warning_year+2}) "
                    f"to assess Weber Effect directly. Attempting proxy analysis…"
                )

        if not weber_detected and warning_year and faers_predates_warning and len(years) >= 4:
            proxy_window_size = min(3, len(years) // 3)
            proxy_years  = years[:proxy_window_size]
            proxy_counts = [year_counts[y] for y in proxy_years]
            baseline_counts = [year_counts[y] for y in years[proxy_window_size:]]

            proxy_peak   = max(proxy_counts)
            baseline_avg = sum(baseline_counts) / len(baseline_counts) if baseline_counts else 0

            if baseline_avg > 0 and proxy_peak > baseline_avg * 1.3:
                weber_detected = True
                weber_tier = "proxy"
                peak_year  = proxy_years[proxy_counts.index(proxy_peak)]
                pct_above  = round((proxy_peak / baseline_avg - 1) * 100)
                yrs_before = data_first_year - warning_year
                notoriety_note = (
                    f"Weber Effect detected (Tier 2 — historical proxy): "
                    f"The FDA boxed warning was issued in {warning_year}, "
                    f"{yrs_before} year{'s' if yrs_before != 1 else ''} before FAERS electronic records begin "
                    f"({data_first_year}). The earliest available FAERS data ({peak_year}) still shows "
                    f"{proxy_peak:,} reports — {pct_above}% above the long-run baseline "
                    f"({int(baseline_avg):,}/yr), consistent with a declining tail of the Weber "
                    f"hyper-reporting surge that peaked around {warning_year}–{warning_year + 2}. "
                    f"Notoriety Bias almost certainly inflated early reporting."
                )
            else:
                notoriety_note += (
                    f" Proxy analysis ({data_first_year}–{proxy_years[-1]}) vs baseline "
                    f"({years[proxy_window_size]}–{years[-1]}): early counts are NOT "
                    f"substantially elevated — reporting appears historically flat for '{drug_name}'."
                ) if notoriety_note else (
                    f"No Weber pattern found via proxy analysis for '{drug_name}' "
                    f"(warning: {warning_year}, earliest FAERS: {data_first_year})."
                )

        if not weber_detected and len(years) >= 6:
            third  = max(1, len(years) // 3)
            early_counts  = counts[:third]
            late_counts   = counts[third:]
            early_peak    = max(early_counts)
            late_avg      = sum(late_counts) / len(late_counts) if late_counts else 0

            if late_avg > 0 and early_peak > late_avg * 1.4:
                weber_detected = True
                weber_tier = "relative"
                peak_idx   = counts.index(early_peak)
                peak_year  = years[peak_idx]
                pct_above  = round((early_peak / late_avg - 1) * 100)
                notoriety_note = (
                    f"Weber Effect detected (Tier 3 — relative pattern): "
                    f"FAERS reporting for '{drug_name}' peaked early at {peak_year} "
                    f"({early_peak:,} reports) then declined to a long-run average of "
                    f"{int(late_avg):,}/yr — a {pct_above}% early-period elevation. "
                    f"This characteristic rise-then-fall pattern is the hallmark of "
                    f"Notoriety Bias: heightened clinician and patient awareness in the "
                    f"years immediately after regulatory action drives over-reporting "
                    f"that subsequently normalises."
                )
            elif not notoriety_note:
                notoriety_note = (
                    f"No Weber Effect pattern detected for '{drug_name}' across any "
                    f"detection tier. Reporting appears relatively flat over the "
                    f"observation period ({years[0]}–{years[-1]})."
                )

        return jsonify({
            "drug":                drug_name,
            "labels":              [str(y) for y in years],
            "counts":              counts,
            "warning_year":        warning_year,
            "weber_peak_detected": weber_detected,
            "weber_tier":          weber_tier,
            "peak_year":           peak_year,
            "notoriety_note":      notoriety_note,
            "data_coverage":       f"{data_first_year}–{years[-1]}" if years else "N/A",
            "faers_predates_warning": faers_predates_warning,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@boxed_warnings_bp.route("/api/prr-trials", methods=["GET"])
def get_prr_with_trials():
    """Option B: Cross-reference FAERS PRR signal with ClinicalTrials.gov data."""
    drug = request.args.get('drug', 'Metformin')
    event = request.args.get('event', 'Nausea')
    CT_BASE = "https://clinicaltrials.gov/api/v2/studies"

    try:
        from routes.signals import compute_disproportionality_helper
        metrics = compute_disproportionality_helper(drug, event)
        if metrics:
            a = metrics["a"]
            b = metrics["b"]
            c = metrics["c"]
            d = metrics["d"]
            prr = metrics["prr"]
        else:
            a = b = c = d = 0
            prr = 0.0

        ct_params = {
            "query.intr": drug,
            "query.cond": event,
            "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING",
            "pageSize": 5,
            "fields": "NCTId,BriefTitle,OverallStatus,Phase,EnrollmentCount"
        }
        ct_res = http_requests.get(CT_BASE, params=ct_params, timeout=10).json()
        ct_studies = ct_res.get("studies", [])

        ct_drug_params = {
            "query.intr": drug,
            "filter.overallStatus": "RECRUITING,ACTIVE_NOT_RECRUITING",
            "pageSize": 5,
            "fields": "NCTId,BriefTitle,OverallStatus,Phase"
        }
        ct_drug_res = http_requests.get(CT_BASE, params=ct_drug_params, timeout=10).json()
        ct_drug_studies = ct_drug_res.get("studies", [])

        trial_matches = []
        for s in ct_studies:
            proto = s.get("protocolSection", {})
            id_mod = proto.get("identificationModule", {})
            status_mod = proto.get("statusModule", {})
            design_mod = proto.get("designModule", {})
            nct_id = id_mod.get("nctId", "")
            trial_matches.append({
                "nct_id": nct_id,
                "title": id_mod.get("briefTitle", ""),
                "status": status_mod.get("overallStatus", ""),
                "phase": ", ".join(design_mod.get("phases", ["N/A"])),
                "url": f"https://clinicaltrials.gov/study/{nct_id}" if nct_id else ""
            })

        has_signal = prr > 2.0 and a >= 3
        has_trial_match = len(trial_matches) > 0
        active_drug_monitoring = len(ct_drug_studies) > 0

        if has_signal and has_trial_match:
            corroboration = "STRONG"
            corroboration_msg = (
                f"FAERS PRR={prr:.2f} (signal confirmed) AND ClinicalTrials found {len(trial_matches)} "
                f"active study investigating '{drug}+{event}'. Two independent sources corroborate this signal."
            )
        elif has_signal and active_drug_monitoring:
            corroboration = "MODERATE"
            corroboration_msg = (
                f"FAERS PRR={prr:.2f} (signal confirmed). ClinicalTrials shows {len(ct_drug_studies)} "
                f"active study for '{drug}' (not specifically for '{event}'). Signal is partially corroborated."
            )
        elif has_signal:
            corroboration = "FAERS_ONLY"
            corroboration_msg = (
                f"FAERS PRR={prr:.2f} (signal confirmed) but NO active ClinicalTrials found for '{drug}+{event}'. "
                f"Signal is real per FAERS but not yet being studied in clinical trials."
            )
        else:
            corroboration = "NO_SIGNAL"
            corroboration_msg = f"PRR={prr:.2f} — below signal threshold. No pharmacovigilance concern detected."

        return jsonify({
            "drug": drug,
            "event": event,
            "prr": round(prr, 4),
            "a": a, "b": b, "c": c, "d": d,
            "is_signal": has_signal,
            "trial_matches": trial_matches,
            "active_drug_study_count": len(ct_drug_studies),
            "corroboration": corroboration,
            "corroboration_message": corroboration_msg
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


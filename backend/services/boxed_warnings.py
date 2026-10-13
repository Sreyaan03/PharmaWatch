# backend/services/boxed_warnings.py
# Boxed-warning domain logic. Phase 7 replaces the warning-year table below with a sourced file.

# ── True Warning Year Resolution ─────────────────────────────────────────────
from database import insert_violation, get_violations, get_all_violations
from clients.http import http_requests

HISTORICAL_WARNING_YEARS: dict[str, int] = {
    "metformin":           1995,
    "rosiglitazone":       2007,
    "pioglitazone":        2011,
    "canagliflozin":       2015,
    "empagliflozin":       2015,
    "dapagliflozin":       2015,
    "semaglutide":         2021,
    "liraglutide":         2010,
    "exenatide":           2009,
    "sitagliptin":         2015,
    "saxagliptin":         2016,
    "alogliptin":          2016,
    "tirzepatide":         2022,
    "warfarin":            1997,
    "amiodarone":          1985,
    "dronedarone":         2009,
    "digoxin":             2001,
    "flecainide":          1989,
    "propafenone":         1990,
    "sotalol":             1992,
    "dofetilide":          1999,
    "ibutilide":           1996,
    "atorvastatin":        2012,
    "simvastatin":         2010,
    "rosuvastatin":        2012,
    "pravastatin":         2012,
    "lovastatin":          2001,
    "cerivastatin":        1999,
    "lisinopril":          1992,
    "enalapril":           1992,
    "ramipril":            1995,
    "valsartan":           1997,
    "losartan":            1995,
    "spironolactone":      2008,
    "eplerenone":          2003,
    "amlodipine":          1992,
    "clonidine":           2000,
    "hydralazine":         1994,
    "minoxidil":           1979,
    "nitroglycerin":       2000,
    "clopidogrel":         1997,
    "prasugrel":           2009,
    "ticagrelor":          2011,
    "ticlopidine":         1993,
    "cilostazol":          1999,
    "bivalirudin":         2000,
    "fondaparinux":        2001,
    "heparin":             1982,
    "enoxaparin":          1993,
    "rivaroxaban":         2011,
    "apixaban":            2012,
    "dabigatran":          2010,
    "edoxaban":            2015,
    "amoxicillin":         2005,
    "ciprofloxacin":       2008,
    "levofloxacin":        2008,
    "moxifloxacin":        2008,
    "ofloxacin":           2008,
    "norfloxacin":         2008,
    "metronidazole":       1995,
    "clarithromycin":      2005,
    "azithromycin":        2013,
    "vancomycin":          1986,
    "linezolid":           2000,
    "rifampin":            1971,
    "isoniazid":           1971,
    "ethambutol":          1971,
    "pyrazinamide":        1971,
    "dapsone":             1998,
    "clindamycin":         2008,
    "nitrofurantoin":      1990,
    "trimethoprim":        1985,
    "sulfamethoxazole":    1985,
    "ibuprofen":           2005,
    "naproxen":            2005,
    "celecoxib":           2005,
    "diclofenac":          2005,
    "indomethacin":        2005,
    "ketorolac":           1991,
    "meloxicam":           2005,
    "piroxicam":           2005,
    "aspirin":             1999,
    "acetaminophen":       2011,
    "tramadol":            2016,
    "codeine":             2013,
    "hydrocodone":         2014,
    "oxycodone":           2001,
    "morphine":            2001,
    "fentanyl":            2005,
    "methadone":           2006,
    "buprenorphine":       2002,
    "naloxone":            2018,
    "naltrexone":          1984,
    "pregabalin":          2019,
    "gabapentin":          2019,
    "haloperidol":         2008,
    "olanzapine":          2003,
    "quetiapine":          2005,
    "risperidone":         2003,
    "clozapine":          1990,
    "aripiprazole":        2005,
    "ziprasidone":         2001,
    "asenapine":           2009,
    "lurasidone":          2010,
    "paliperidone":        2006,
    "lithium":             1970,
    "valproate":           1997,
    "lamotrigine":         1994,
    "carbamazepine":       1994,
    "phenytoin":           1994,
    "topiramate":          2011,
    "levetiracetam":       2008,
    "fluoxetine":          2004,
    "sertraline":          2004,
    "paroxetine":          2004,
    "citalopram":          2011,
    "escitalopram":        2011,
    "venlafaxine":         2004,
    "duloxetine":          2004,
    "amitriptyline":       2004,
    "clomipramine":        2004,
    "imipramine":          2004,
    "nortriptyline":       2004,
    "bupropion":           2009,
    "varenicline":         2009,
    "alprazolam":          2020,
    "diazepam":            2020,
    "lorazepam":           2020,
    "clonazepam":          2020,
    "zolpidem":            2013,
    "eszopiclone":         2013,
    "zaleplon":            2013,
    "methylphenidate":     2006,
    "amphetamine":         2006,
    "atomoxetine":         2004,
    "donepezil":           2009,
    "memantine":           2003,
    "methotrexate":        1988,
    "azathioprine":        1991,
    "cyclophosphamide":    1988,
    "mycophenolate":       2007,
    "tacrolimus":          2003,
    "cyclosporine":        1994,
    "leflunomide":         1998,
    "infliximab":          2004,
    "adalimumab":          2004,
    "etanercept":          2004,
    "certolizumab":        2009,
    "golimumab":           2009,
    "rituximab":           2004,
    "tofacitinib":         2021,
    "baricitinib":         2021,
    "upadacitinib":        2021,
    "abatacept":           2005,
    "belimumab":           2011,
    "natalizumab":         2006,
    "alemtuzumab":         2013,
    "fingolimod":          2010,
    "dimethyl fumarate":   2020,
    "hydroxyurea":         1995,
    "imatinib":            2001,
    "erlotinib":           2004,
    "gefitinib":           2003,
    "sunitinib":           2006,
    "sorafenib":           2005,
    "vemurafenib":         2011,
    "ipilimumab":          2011,
    "nivolumab":           2014,
    "pembrolizumab":       2014,
    "atezolizumab":        2016,
    "bortezomib":          2003,
    "lenalidomide":        2005,
    "thalidomide":         1998,
    "pomalidomide":        2013,
    "salmeterol":          2003,
    "formoterol":          2003,
    "fluticasone":         2015,
    "budesonide":          2015,
    "montelukast":         2020,
    "theophylline":        1995,
    "ipratropium":         2009,
    "tiotropium":          2009,
    "omeprazole":          2011,
    "esomeprazole":        2011,
    "pantoprazole":        2011,
    "lansoprazole":        2011,
    "rabeprazole":         2011,
    "cisapride":           1993,
    "metoclopramide":      2009,
    "domperidone":         2014,
    "ondansetron":         2011,
    "vedolizumab":         2014,
    "ustekinumab":         2016,
    "levothyroxine":       2010,
    "testosterone":        2015,
    "methyltestosterone":  2015,
    "anastrozole":         2000,
    "tamoxifen":           1994,
    "raloxifene":          2007,
    "estradiol":           2003,
    "conjugated estrogens":2003,
    "medroxyprogesterone": 2004,
    "dexamethasone":       2010,
    "prednisone":          2010,
    "hydrocortisone":      2010,
    "fludrocortisone":     2010,
    "furosemide":          1982,
    "torsemide":           1993,
    "hydrochlorothiazide": 1980,
    "sildenafil":          2007,
    "tadalafil":           2007,
    "vardenafil":          2007,
    "finasteride":         2011,
    "dutasteride":         2011,
    "tamsulosin":          1997,
    "isotretinoin":        1988,
    "doxycycline":         1975,
    "minocycline":         2003,
    "pimecrolimus":        2006,
}

def _get_true_warning_year(drug_name: str, api_year: int | None) -> int | None:
    """Return the historically-accurate year a drug's boxed warning was FIRST issued."""
    key = drug_name.strip().lower()
    if key in HISTORICAL_WARNING_YEARS:
        return HISTORICAL_WARNING_YEARS[key]
    first_word = key.split()[0].rstrip("-")
    if first_word in HISTORICAL_WARNING_YEARS:
        return HISTORICAL_WARNING_YEARS[first_word]
    return api_year




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
            return {
                "has_warning": False,
                "drug": drug_name,
                "warning_text": "",
                "brand_name": "",
                "generic_name": ""
            }, 200

        label = res["results"][0]
        warning_text = " ".join(label.get("boxed_warning", [""]))
        openfda = label.get("openfda", {})

        return {
            "has_warning": True,
            "drug": drug_name,
            "warning_text": warning_text,
            "brand_name": ", ".join(openfda.get("brand_name", [])),
            "generic_name": ", ".join(openfda.get("generic_name", [])),
            "pharm_class": ", ".join(openfda.get("pharm_class_epc", [])),
            "route": ", ".join(openfda.get("route", []))
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500

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

        return {
            "drug": drug_name,
            "total_reports": total_reports,
            "has_boxed_warning": bool(warning_text),
            "warned_events": warned_events,
            "other_events": non_warned_events,
            "boxed_warning_event_count": boxed_total,
            "boxed_warning_percentage": boxed_pct
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
            return {
                "drug": drug_name,
                "has_boxed_warning": False,
                "violations": [],
                "stored_violations": get_violations(drug_name),
                "message": "No boxed warning found for this drug."
            }, 200

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

        return {
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
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500

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
            return {
                "drug": drug_name,
                "error": "No time-series data available from openFDA for this drug."
            }, 200

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

        return {
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
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500

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
            return {
                "drug": drug_name, "warning_year": warning_year,
                "weber_peak_detected": False,
                "error": "No time-series FAERS data available."
            }, 200

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

        return {
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
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500

def get_prr_with_trials(drug, event):
    """Option B: Cross-reference FAERS PRR signal with ClinicalTrials.gov data."""
    CT_BASE = "https://clinicaltrials.gov/api/v2/studies"

    try:
        from services.signals import compute_disproportionality_helper
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

        return {
            "drug": drug,
            "event": event,
            "prr": round(prr, 4),
            "a": a, "b": b, "c": c, "d": d,
            "is_signal": has_signal,
            "trial_matches": trial_matches,
            "active_drug_study_count": len(ct_drug_studies),
            "corroboration": corroboration,
            "corroboration_message": corroboration_msg
        }, 200
    except Exception as e:
        return {"error": str(e)}, 500


def get_all_violations_route(limit_raw):
    """Return all violation records stored in the local SQLite database."""
    try:
        limit = int(limit_raw)
        rows = get_all_violations(limit=limit)
        return {"count": len(rows), "violations": rows}, 200
    except Exception as e:
        return {"error": str(e)}, 500

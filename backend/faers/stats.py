# backend/faers/stats.py
# Small statistical helpers shared by the FAERS analytics and the openFDA fallback.

import numpy as np


def zscore_spikes(counts, window=12, min_history=3, threshold=2.0):
    """Flag points more than `threshold` standard deviations above the trailing window.

    Returns (spikes, z_scores), one entry per count. The first `min_history`
    points are never flagged.
    """
    spikes = []
    z_scores = []
    counts_arr = np.array(counts, dtype=float)

    for i in range(len(counts)):
        if i < min_history:
            z_scores.append(0.0)
            spikes.append(False)
        else:
            w = counts_arr[max(0, i - window):i]
            mean = np.mean(w)
            std = np.std(w)
            if std == 0:
                std = 1.0
            z = (counts_arr[i] - mean) / std
            z_scores.append(round(float(z), 2))
            spikes.append(bool(z > threshold))
    return spikes, z_scores


def contingency(con, ingredient_rxcuis, pt, where=None):
    """The 2x2 table for one drug and one event, over de-duplicated cases.

    `ingredient_rxcuis`  one RxNorm ingredient, or several that must all be suspect in the same
                         case (a combination product).
    `pt`                 a MedDRA Preferred Term, matched case-insensitively.
    `where`              optional (sql, params) restricting the cohort, for example a sex or age
                         stratum: ("sex = ?", ["F"]). Written by the application, never by users.

    A drug counts as present when it is primary or secondary suspect. Returns
    {'a', 'b', 'c', 'd', 'n'} with a = drug and event, b = drug without event,
    c = event without drug, d = neither, n = cases in the cohort.
    """
    if isinstance(ingredient_rxcuis, str):
        ingredient_rxcuis = [ingredient_rxcuis]
    ingredient_rxcuis = list(dict.fromkeys(ingredient_rxcuis))
    if not ingredient_rxcuis:
        raise ValueError("at least one ingredient is required")

    cohort_where, cohort_params = (where if where else ("TRUE", []))
    marks = ", ".join("?" for _ in ingredient_rxcuis)
    row = con.execute(f"""
        WITH cohort AS (
            SELECT primaryid FROM faers_demo WHERE {cohort_where}
        ),
        exposed AS (
            SELECT cd.primaryid FROM case_drug cd
            JOIN cohort USING (primaryid)
            WHERE cd.suspect AND cd.ingredient_rxcui IN ({marks})
            GROUP BY cd.primaryid
            HAVING count(DISTINCT cd.ingredient_rxcui) = ?
        ),
        event AS (
            SELECT DISTINCT r.primaryid FROM faers_reac r
            JOIN cohort USING (primaryid)
            WHERE lower(r.pt) = lower(?)
        )
        SELECT (SELECT count(*) FROM cohort),
               (SELECT count(*) FROM exposed),
               (SELECT count(*) FROM event),
               (SELECT count(*) FROM exposed JOIN event USING (primaryid))
    """, list(cohort_params) + ingredient_rxcuis + [len(ingredient_rxcuis), pt]).fetchone()
    n, exposed, event, a = (int(x) for x in row)
    return {"a": a, "b": exposed - a, "c": event - a, "d": n - exposed - event + a, "n": n}


# ── cohorts shared by onset, strata and country counts ───────────────────────────
def _marks(items):
    return ", ".join("?" for _ in items)


def _exposed_with_event_sql(n_ingredients):
    """CTEs: `cohort` (all cases), `exposed` (suspect for every ingredient), `event` (has the term)."""
    return f"""
        cohort AS (SELECT primaryid FROM faers_demo),
        exposed AS (
            SELECT primaryid FROM case_drug
            WHERE suspect AND ingredient_rxcui IN ({_marks(range(n_ingredients))})
            GROUP BY primaryid HAVING count(DISTINCT ingredient_rxcui) = ?
        ),
        event AS (SELECT DISTINCT primaryid FROM faers_reac WHERE lower(pt) = lower(?)),
        hits AS (SELECT e.primaryid FROM exposed e JOIN event USING (primaryid))
    """


def _cohort_args(ingredient_rxcuis, pt):
    ingredient_rxcuis = list(dict.fromkeys(
        [ingredient_rxcuis] if isinstance(ingredient_rxcuis, str) else ingredient_rxcuis))
    if not ingredient_rxcuis:
        raise ValueError("at least one ingredient is required")
    return ingredient_rxcuis, ingredient_rxcuis + [len(ingredient_rxcuis), pt]


# ── time to onset ────────────────────────────────────────────────────────────────
ONSET_BUCKETS = [
    ("0-30 days", 0, 30), ("31-60 days", 31, 60), ("61-90 days", 61, 90),
    ("91-180 days", 91, 180), ("181-365 days", 181, 365), (">365 days", 366, None),
]
MIN_ONSET_CASES = 10


def time_to_onset(con, ingredient_rxcuis, pt, min_cases=MIN_ONSET_CASES):
    """Days from the start of the suspect drug to the event, from the reports' own dates.

    A case is usable only when both dates are complete (YYYYMMDD) and the event is not before
    the start of therapy. Nothing is estimated for the others; they are counted by reason so the
    result says how much of the evidence it rests on.
    """
    import numpy as np

    rxcuis, args = _cohort_args(ingredient_rxcuis, pt)
    rows = con.execute(f"""
        WITH {_exposed_with_event_sql(len(rxcuis))},
        drug_rows AS (
            SELECT d.primaryid, d.drug_seq
            FROM faers_drug d
            JOIN (SELECT DISTINCT raw_name, ingredient_rxcui FROM drug_identity) i
              ON i.raw_name = lower(trim(coalesce(nullif(trim(d.prod_ai), ''), d.drugname)))
            WHERE d.role_cod IN ('PS', 'SS') AND i.ingredient_rxcui IN ({_marks(rxcuis)})
        ),
        starts AS (
            SELECT t.primaryid, min(try_strptime(t.start_dt, '%Y%m%d')) AS start_date
            FROM faers_ther t
            JOIN drug_rows r ON r.primaryid = t.primaryid AND r.drug_seq = t.dsg_drug_seq
            WHERE length(t.start_dt) = 8
            GROUP BY t.primaryid
        )
        SELECT h.primaryid,
               CASE WHEN length(dm.event_dt) = 8 THEN try_strptime(dm.event_dt, '%Y%m%d') END AS event_date,
               s.start_date
        FROM hits h
        JOIN faers_demo dm USING (primaryid)
        LEFT JOIN starts s USING (primaryid)
    """, args + rxcuis).fetchall()

    total = len(rows)
    no_event_date = sum(1 for _, ev, st in rows if ev is None)
    no_start_date = sum(1 for _, ev, st in rows if st is None)
    days, negative = [], 0
    for _, ev, st in rows:
        if ev is None or st is None:
            continue
        delta = (ev - st).days
        if delta < 0:
            negative += 1
        else:
            days.append(delta)

    counts = []
    for _, low, high in ONSET_BUCKETS:
        counts.append(sum(1 for x in days if x >= low and (high is None or x <= high)))
    result = {
        "buckets": [b[0] for b in ONSET_BUCKETS],
        "counts": counts,
        "cases": total,
        "usable_cases": len(days),
        "excluded": {
            "no_complete_event_date": no_event_date,
            "no_complete_therapy_start": no_start_date,
            "event_before_therapy": negative,
        },
    }
    if len(days) >= min_cases:
        q1, med, q3 = (float(v) for v in np.percentile(days, [25, 50, 75]))
        result.update(median_days=round(med, 1), q1_days=round(q1, 1), q3_days=round(q3, 1),
                      peak_bucket=result["buckets"][int(np.argmax(counts))])
    else:
        result.update(median_days=None, q1_days=None, q3_days=None, peak_bucket=None)
    return result


# ── strata ──────────────────────────────────────────────────────────────────────
def stratified_metrics(con, ingredient_rxcuis, pt, strata):
    """PRR, ROR and class for each stratum, each from its own 2x2 table.

    `strata` maps a name to (sql, params) describing that stratum of the cohort, for example
    {"F": ("sex = ?", ["F"])}. Returns {name: metrics dict from compute_metrics, plus 'n'}.
    """
    from faers.disproportionality import compute_metrics

    out = {}
    for name, where in strata.items():
        t = contingency(con, ingredient_rxcuis, pt, where=where)
        m = compute_metrics(t["a"], t["b"], t["c"], t["d"])
        m["n"] = t["n"]
        out[name] = m
    return out


SEX_STRATA = {"F": ("sex = ?", ["F"]), "M": ("sex = ?", ["M"])}
AGE_STRATA = {
    "Pediatric (<18)": ("age_years IS NOT NULL AND age_years < 18", []),
    "Adult (18-64)": ("age_years >= 18 AND age_years < 65", []),
    "Geriatric (>=65)": ("age_years >= 65", []),
}


def unclassified_cases(con, ingredient_rxcuis, pt, where_sql):
    """Cases with the drug and the event that fall outside the strata (e.g. sex or age missing)."""
    rxcuis, args = _cohort_args(ingredient_rxcuis, pt)
    row = con.execute(f"""
        WITH {_exposed_with_event_sql(len(rxcuis))}
        SELECT count(*) FROM hits h JOIN faers_demo USING (primaryid) WHERE {where_sql}
    """, args).fetchone()
    return int(row[0])


def reports_by_country(con, ingredient_rxcuis, pt, limit=15):
    """Reports with the drug and the event, by country of occurrence, largest first."""
    rxcuis, args = _cohort_args(ingredient_rxcuis, pt)
    rows = con.execute(f"""
        WITH {_exposed_with_event_sql(len(rxcuis))}
        SELECT upper(trim(dm.occr_country)) AS country, count(*) AS n
        FROM hits h JOIN faers_demo dm USING (primaryid)
        WHERE length(trim(dm.occr_country)) >= 2
        GROUP BY 1 ORDER BY n DESC, country LIMIT ?
    """, args + [int(limit)]).fetchall()
    return [(r[0], int(r[1])) for r in rows]


# ── seasonality and report-month completeness ───────────────────────────────────
_DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
SEASONALITY_ALPHA = 0.01
MIN_SEASONALITY_CASES = 60        # keeps every expected monthly count at about 5 or more


def seasonality_test(counts):
    """Chi-square test of monthly counts (Jan..Dec) against a uniform spread over the calendar.

    Expected counts are proportional to the days in each month. Returns chi2, p_value and
    `applicable`; below MIN_SEASONALITY_CASES the test is not applicable and nothing is called
    seasonal.
    """
    from scipy.stats import chi2 as chi2_dist

    total = int(sum(counts))
    if len(counts) != 12 or total < MIN_SEASONALITY_CASES:
        return {"applicable": False, "chi2": None, "p_value": None, "df": 11, "total": total}
    days = sum(_DAYS_IN_MONTH)
    stat = sum((c - total * d / days) ** 2 / (total * d / days) for c, d in zip(counts, _DAYS_IN_MONTH))
    return {"applicable": True, "chi2": round(float(stat), 3),
            "p_value": float(chi2_dist.sf(stat, 11)), "df": 11, "total": total}


def drop_incomplete_tail(months, counts, min_remaining=6):
    """Remove the months of the most recent calendar quarter present in a monthly series.

    Reports for recent events keep arriving for months, so the newest quarter is under-counted
    and would otherwise look like a collapse (or distort a spike baseline). `months` are
    'YYYYMM' strings in ascending order. Nothing is dropped if fewer than `min_remaining`
    months would be left. Returns (months, counts, dropped_months).
    """
    if not months:
        return months, counts, []
    last = months[-1]
    year, month = int(last[:4]), int(last[4:6])
    quarter_start = f"{year:04d}{3 * ((month - 1) // 3) + 1:02d}"
    keep = [i for i, m in enumerate(months) if m < quarter_start]
    if len(keep) < min_remaining:
        return months, counts, []
    return ([months[i] for i in keep], [counts[i] for i in keep],
            [m for m in months if m >= quarter_start])


# ── reporting share over time ────────────────────────────────────────────────
def monthly_report_share(con, ingredient_rxcuis):
    """Reports with the drug as suspect, as a share of all reports, by month received.

    The month is that of the de-duplicated case's FDA receipt date (`fda_dt`). Using the share
    of all reports, not the raw count, removes the growth and seasonality of reporting volume
    that every drug shares. Returns {'months': ['YYYYMM', ...], 'drug': [...], 'total': [...],
    'share': [...]} with `share` in percent, over every month that has any report.
    """
    rxcuis = list(dict.fromkeys(
        [ingredient_rxcuis] if isinstance(ingredient_rxcuis, str) else ingredient_rxcuis))
    if not rxcuis:
        raise ValueError("at least one ingredient is required")
    rows = con.execute(f"""
        WITH exposed AS (
            SELECT primaryid FROM case_drug
            WHERE suspect AND ingredient_rxcui IN ({_marks(rxcuis)})
            GROUP BY primaryid HAVING count(DISTINCT ingredient_rxcui) = ?
        )
        SELECT substr(d.fda_dt, 1, 6) AS ym, count(*) AS total,
               count(e.primaryid) AS drug_reports
        FROM faers_demo d
        LEFT JOIN exposed e USING (primaryid)
        WHERE length(d.fda_dt) = 8
        GROUP BY ym ORDER BY ym
    """, rxcuis + [len(rxcuis)]).fetchall()
    months = [r[0] for r in rows]
    total = [int(r[1]) for r in rows]
    drug = [int(r[2]) for r in rows]
    return {"months": months, "drug": drug, "total": total,
            "share": [round(100.0 * d / t, 6) if t else 0.0 for d, t in zip(drug, total)]}

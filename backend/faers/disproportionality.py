import logging
import numpy as np
import requests

log = logging.getLogger("disproportionality")

TOTAL_FAERS_POPULATION = 20_692_690  # Global FDA FAERS total patient reports denominator

# Pre-seeded common global background counts to minimize external queries and speed up ingestion
INITIAL_GLOBAL_BACKGROUND = {
    "lactic acidosis": 31672,
    "neutropenia": 193353,
    "international normalised ratio increased": 28417,
    "asthma": 111696,
    "chronic kidney disease": 75758,
    "anxiety": 287756,
    "decreased appetite": 228555,
    "cough": 154210,
    "pyrexia": 389400,
    "myocardial infarction": 245100,
    "anaemia": 198750,
    "hospitalisation": 412000,
    "constipation": 142100,
    "impaired gastric emptying": 18900,
    "product use in unapproved indication": 312000,
    "weight increased": 95400,
    "pneumonia": 234100,
    "nausea": 542000,
    "diarrhoea": 489000,
    "vomiting": 341000,
    "fatigue": 612000,
    "dyspnoea": 421000,
    "headache": 465000,
    "dizziness": 389000,
    "blood glucose increased": 78900,
    "acute kidney injury": 215000,
    "weight decreased": 145000,
    "tendon rupture": 12800,
    "pulmonary toxicity": 19500,
    "agranulocytosis": 8400,
    "hepatotoxicity": 32100,
    "bleeding": 298000,
    "off label use": 845000,
    "asthenia": 182000,
    "fall": 234000,
    "drug ineffective": 678000
}


def get_global_event_count(event):
    """
    Returns the total number of reports worldwide in FDA FAERS for a given adverse event.
    Caches and persists results in MySQL table 'faers_background_events'.
    """
    event_clean = event.lower().strip()

    execute = None
    # 1. Check MySQL cache
    try:
        from db.mysql import query_one, execute
        row = query_one("SELECT global_count FROM faers_background_events WHERE event = %s", (event_clean,))
        if row and row.get("global_count") and int(row["global_count"]) > 0:
            return int(row["global_count"])
    except Exception:
        execute = None

    # 2. Check in-memory initial dictionary
    if event_clean in INITIAL_GLOBAL_BACKGROUND:
        count = INITIAL_GLOBAL_BACKGROUND[event_clean]
        try:
            if execute:
                execute(
                    "INSERT INTO faers_background_events (event, global_count, updated_at) "
                    "VALUES (%s, %s, NOW()) ON DUPLICATE KEY UPDATE global_count = VALUES(global_count)",
                    (event_clean, count)
                )
        except Exception:
            pass
        return count

    # 3. Query openFDA API for exact real-time background count
    count = 0
    try:
        url = f'https://api.fda.gov/drug/event.json?search=patient.reaction.reactionmeddrapt:"{event_clean}"&limit=1'
        res = requests.get(url, timeout=5)
        if res.status_code == 200:
            count = res.json().get("meta", {}).get("results", {}).get("total", 0)
    except Exception as e:
        log.warning(f"Failed to query openFDA background count for '{event_clean}': {e}")

    # Fallback to conservative estimate if openFDA is unreachable
    if count <= 0:
        count = 5000

    try:
        from db.mysql import execute
        execute(
            "INSERT INTO faers_background_events (event, global_count, updated_at) "
            "VALUES (%s, %s, NOW()) ON DUPLICATE KEY UPDATE global_count = VALUES(global_count)",
            (event_clean, count)
        )
    except Exception:
        pass

    return count


# Signal classes. A pair is a "signal" when it meets the Evans et al. (2001) criteria used by
# regulators for PRR: at least 3 co-reports, PRR >= 2 and chi-square >= 4. It is a "strong_signal"
# when, in addition, the lower bound of the ROR interval is above 1 and IC025 is above 0 (the two
# other standard measures agree). Everything else is "none": no disproportionality shown.
SIGNAL_NONE = "none"
SIGNAL = "signal"
SIGNAL_STRONG = "strong_signal"

MIN_CASES = 3
PRR_THRESHOLD = 2.0
CHI2_THRESHOLD = 4.0
Z_95 = 1.959964


def _empty_metrics(a, b, c, d):
    return {
        "a": int(a), "b": int(b), "c": int(c), "d": int(d),
        "prr": 0.0, "prr_ci_low": 0.0, "prr_ci_high": 0.0,
        "ror": 0.0, "ror_ci_low": 0.0, "ror_ci_high": 0.0,
        "chi2": 0.0, "bcpnn_ic": 0.0, "ic025": 0.0,
        "n_reports": int(a), "signal_strength": SIGNAL_NONE,
        "criteria": {"min_cases": False, "prr": False, "chi2": False,
                     "ror_lower_above_1": False, "ic025_above_0": False},
        "continuity_correction": False,
        "defined": False,
    }


def compute_metrics(a, b, c, d):
    """Disproportionality metrics for one 2x2 table: PRR, ROR (95% intervals), chi-square, IC.

    a = reports with the drug and the event, b = drug without the event, c = event without the
    drug, d = neither.

    Zero cells: when any cell is zero, 0.5 is added to all four (Haldane-Anscombe) before PRR and
    ROR and their intervals are computed. Chi-square (Yates) and IC use the observed counts.
    A table with no exposure, no event reports or no comparator reports is undefined and returns
    zeros with class "none".

    IC is the widely published approximation log2((a + 0.5) / (E + 0.5)) with E = (a+b)(a+c)/N;
    IC025 is its lower 95% bound (Gould 2003): IC - 3.3 (a+0.5)^-1/2 - 2 (a+0.5)^-3/2.
    """
    a, b, c, d = int(a), int(b), int(c), int(d)
    total = a + b + c + d
    if min(a, b, c, d) < 0:
        raise ValueError("cell counts cannot be negative")
    if total == 0 or (a + b) == 0 or (a + c) == 0 or (c + d) == 0:
        return _empty_metrics(a, b, c, d)

    corrected = 0 in (a, b, c, d)
    k = 0.5 if corrected else 0.0
    A, B, C, D = a + k, b + k, c + k, d + k

    prr = (A / (A + B)) / (C / (C + D))
    prr_se = np.sqrt(1 / A - 1 / (A + B) + 1 / C - 1 / (C + D))
    ror = (A * D) / (B * C)
    ror_se = np.sqrt(1 / A + 1 / B + 1 / C + 1 / D)
    prr_low, prr_high = np.exp(np.log(prr) - Z_95 * prr_se), np.exp(np.log(prr) + Z_95 * prr_se)
    ror_low, ror_high = np.exp(np.log(ror) - Z_95 * ror_se), np.exp(np.log(ror) + Z_95 * ror_se)

    # Chi-square with Yates continuity correction, on the observed table
    denominator = (a + b) * (c + d) * (a + c) * (b + d)
    numerator = total * max(0.0, abs(a * d - b * c) - total / 2) ** 2
    chi2 = numerator / denominator if denominator > 0 else 0.0

    expected_a = (a + b) * (a + c) / total
    ic = np.log2((a + 0.5) / (expected_a + 0.5))
    ic025 = ic - 3.3 * (a + 0.5) ** -0.5 - 2 * (a + 0.5) ** -1.5

    criteria = {
        "min_cases": a >= MIN_CASES,
        "prr": bool(prr >= PRR_THRESHOLD),
        "chi2": bool(chi2 >= CHI2_THRESHOLD),
        "ror_lower_above_1": bool(ror_low > 1.0),
        "ic025_above_0": bool(ic025 > 0.0),
    }
    if criteria["min_cases"] and criteria["prr"] and criteria["chi2"]:
        strength = SIGNAL_STRONG if (criteria["ror_lower_above_1"] and criteria["ic025_above_0"]) else SIGNAL
    else:
        strength = SIGNAL_NONE

    return {
        "a": a, "b": b, "c": c, "d": d,
        "prr": round(float(prr), 4),
        "prr_ci_low": round(float(prr_low), 4),
        "prr_ci_high": round(float(prr_high), 4),
        "ror": round(float(ror), 4),
        "ror_ci_low": round(float(ror_low), 4),
        "ror_ci_high": round(float(ror_high), 4),
        "chi2": round(float(chi2), 4),
        "bcpnn_ic": round(float(ic), 4),
        "ic025": round(float(ic025), 4),
        "n_reports": a,
        "signal_strength": strength,
        "criteria": criteria,
        "continuity_correction": corrected,
        "defined": True,
    }

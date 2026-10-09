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


def compute_metrics(a, b, c, d):
    """
    Computes disproportionality metrics: PRR, ROR, and BCPNN (IC).
    Includes Yates/Haldane continuity corrections with empirical Bayes stabilization.
    Returns a dictionary of the results.
    """
    total = a + b + c + d
    if (a + b) == 0 or (a + c) == 0 or total == 0:
        return {
            "a": a, "b": b, "c": c, "d": d,
            "prr": 0.0, "prr_ci_low": 0.0, "prr_ci_high": 0.0,
            "ror": 0.0, "ror_ci_low": 0.0, "ror_ci_high": 0.0,
            "chi2": 0.0, "bcpnn_ic": 0.0, "ic025": 0.0,
            "n_reports": a, "signal_strength": "moderate"
        }

    # Continuity correction: add 0.5 to cells if any is zero.
    # If c is zero (single-drug isolated cohort), apply empirical Bayes expected cell
    # to prevent artificial single-cohort explosions.
    if c == 0:
        expected_c = max(1.0, ((a + b) * (a + c)) / total)
        c_c = expected_c
    else:
        c_c = float(c)

    if a == 0:
        a_c = 0.5
    else:
        a_c = float(a)

    if b == 0:
        b_c = 0.5
    else:
        b_c = float(b)

    if d == 0:
        d_c = 0.5
    else:
        d_c = float(d)

    # PRR calculation
    prr = (a_c / (a_c + b_c)) / (c_c / (c_c + d_c))
    prr_se = np.sqrt(1/a_c - 1/(a_c + b_c) + 1/c_c - 1/(c_c + d_c))
    prr_ci_low = np.exp(np.log(max(prr, 1e-6)) - 1.96 * prr_se)
    prr_ci_high = np.exp(np.log(max(prr, 1e-6)) + 1.96 * prr_se)

    # ROR calculation
    ror = (a_c * d_c) / (b_c * c_c)
    ror_se = np.sqrt(1/a_c + 1/b_c + 1/c_c + 1/d_c)
    ror_ci_low = np.exp(np.log(max(ror, 1e-6)) - 1.96 * ror_se)
    ror_ci_high = np.exp(np.log(max(ror, 1e-6)) + 1.96 * ror_se)

    # Chi-square with Yates correction
    numerator = total * (max(0, abs(a * d - b * c) - total / 2)) ** 2
    denominator = (a + b) * (c + d) * (a + c) * (b + d)
    chi2 = numerator / denominator if denominator > 0 else 0

    # BCPNN calculation
    expected_a = (a + b) * (a + c) / total
    ic = np.log2((a + 0.5) / (expected_a + 0.5))
    ic025 = np.log2((a + 0.5) / (expected_a + 0.5)) - 3.3 * np.power(a + 0.5, -0.5) - 2 * np.power(a + 0.5, -1.5)

    # Determine signal strength
    is_signal = (a >= 3 and prr >= 2.0 and chi2 >= 4.0)

    if is_signal:
        if ror_ci_low > 1.0 and ic025 > 0.0 and prr > 4.0:
            strength = "critical"
        else:
            strength = "high"
    else:
        strength = "moderate"

    return {
        "a": int(a), "b": int(b), "c": int(c), "d": int(d),
        "prr": round(float(prr), 4),
        "prr_ci_low": round(float(prr_ci_low), 4),
        "prr_ci_high": round(float(prr_ci_high), 4),
        "ror": round(float(ror), 4),
        "ror_ci_low": round(float(ror_ci_low), 4),
        "ror_ci_high": round(float(ror_ci_high), 4),
        "chi2": round(float(chi2), 4),
        "bcpnn_ic": round(float(ic), 4),
        "ic025": round(float(ic025), 4),
        "n_reports": int(a),
        "signal_strength": strength
    }

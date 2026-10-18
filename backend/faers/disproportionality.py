import numpy as np

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

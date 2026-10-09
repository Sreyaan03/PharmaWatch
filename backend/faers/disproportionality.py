import numpy as np

def compute_metrics(a, b, c, d):
    """
    Computes disproportionality metrics: PRR, ROR, and BCPNN (IC).
    Includes Yates/Haldane continuity corrections where appropriate.
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

    # Continuity correction: add 0.5 to all cells if any is zero
    if a == 0 or b == 0 or c == 0 or d == 0:
        a_c, b_c, c_c, d_c = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    else:
        a_c, b_c, c_c, d_c = a, b, c, d

    # PRR calculation
    prr = (a_c / (a_c + b_c)) / (c_c / (c_c + d_c))
    prr_se = np.sqrt(1/a_c - 1/(a_c + b_c) + 1/c_c - 1/(c_c + d_c))
    prr_ci_low = np.exp(np.log(prr) - 1.96 * prr_se)
    prr_ci_high = np.exp(np.log(prr) + 1.96 * prr_se)

    # ROR calculation
    ror = (a_c * d_c) / (b_c * c_c)
    ror_se = np.sqrt(1/a_c + 1/b_c + 1/c_c + 1/d_c)
    ror_ci_low = np.exp(np.log(ror) - 1.96 * ror_se)
    ror_ci_high = np.exp(np.log(ror) + 1.96 * ror_se)

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

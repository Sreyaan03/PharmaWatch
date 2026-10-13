"""
compute_metrics against hand-worked 2x2 tables.

The expected values were computed independently of the project (plain arithmetic for PRR, ROR,
their log-normal 95% intervals and IC/IC025, and SciPy's Yates chi-square), and the zero-cell
example reproduces the worked numbers in formulation/DISPROPORTIONALITY_ZERO_CELL_HALDANE_CORRECTION.md.
"""

import os
import sys

import pytest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from faers.disproportionality import compute_metrics  # noqa: E402

# name: (table, expected)  — table is (a, b, c, d)
GOLDEN = {
    "clear signal": ((20, 980, 100, 98900), dict(
        prr=19.8000, prr_ci_low=12.3006, prr_ci_high=31.8716, ror=20.1837, ror_ci_low=12.4370,
        ror_ci_high=32.7555, chi2=282.2326, bcpnn_ic=3.5920, ic025=2.8416)),
    "zero cell c (formulation document example)": ((5, 45, 0, 32749), dict(
        prr=7063.7255, prr_ci_low=395.7283, prr_ci_high=126087.0754, ror=7917.4615, ror_ci_low=431.4718,
        ror_ci_high=145284.5588, chi2=2652.1734, bcpnn_ic=3.4376, ic025=1.8754)),
    "no co-reports (a = 0)": ((0, 100, 50, 9850), dict(
        prr=0.9706, prr_ci_low=0.0603, prr_ci_high=15.6229, ror=0.9704, ror_ci_low=0.0595,
        ror_ci_high=15.8381, chi2=0.0, bcpnn_ic=-1.0, ic025=-11.3238)),
    "no disproportionality": ((10, 990, 1000, 98000), dict(
        prr=0.9900, prr_ci_low=0.5327, prr_ci_high=1.8399, ror=0.9899, ror_ci_low=0.5293,
        ror_ci_high=1.8513, chi2=0.0, bcpnn_ic=-0.0137, ic025=-1.0909)),
    "signal but not strong (small a)": ((3, 7, 3, 87), dict(
        prr=9.0, prr_ci_low=2.0883, prr_ci_high=38.7874, ror=12.4286, ror_ci_low=2.1045,
        ror_ci_high=73.3999, chi2=7.1119, bcpnn_ic=1.6699, ic025=-0.3995)),
    "high PRR but only two cases": ((2, 8, 1, 989), dict(
        prr=198.0, prr_ci_low=19.4928, prr_ci_high=2011.2083, ror=247.25, ror_ci_low=20.3107,
        ror_ci_high=3009.8660, chi2=72.9765, bcpnn_ic=2.2379, ic025=-0.3552)),
    "zero cell b": ((12, 0, 40, 5000), dict(
        prr=119.6819, prr_ci_low=86.4357, prr_ci_high=165.7156, ror=3086.7284, ror_ci_low=179.7026,
        ror_ci_high=53020.3225, chi2=1061.2582, bcpnn_ic=4.3254, ic025=3.3467)),
    "zero cell d": ((30, 70, 20, 0), dict(
        prr=0.3093, prr_ci_low=0.2283, prr_ci_high=0.4192, ror=0.0106, ror_ci_low=0.0006,
        ror_ci_high=0.1801, chi2=30.7817, bcpnn_ic=-0.4673, ic025=-1.0767)),
}


@pytest.mark.parametrize("name", list(GOLDEN))
def test_metrics_match_hand_worked_values(name):
    table, expected = GOLDEN[name]
    got = compute_metrics(*table)
    for key, value in expected.items():
        assert got[key] == pytest.approx(value, rel=2e-4, abs=2e-4), f"{name}: {key}"
    assert (got["a"], got["b"], got["c"], got["d"]) == table


def test_zero_cell_correction_adds_half_to_all_four_cells_and_is_reported():
    assert compute_metrics(5, 45, 0, 32749)["continuity_correction"] is True
    assert compute_metrics(20, 980, 100, 98900)["continuity_correction"] is False


def test_no_zero_cell_means_the_plain_formulas():
    r = compute_metrics(20, 980, 100, 98900)
    assert r["prr"] == pytest.approx((20 / 1000) / (100 / 99000), rel=1e-4)
    assert r["ror"] == pytest.approx((20 * 98900) / (980 * 100), rel=1e-4)


# ── classes ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name,expected_class", [
    ("clear signal", "strong_signal"),
    ("zero cell c (formulation document example)", "strong_signal"),
    ("signal but not strong (small a)", "signal"),        # meets Evans; IC025 is below 0
    ("high PRR but only two cases", "none"),              # fewer than 3 cases
    ("no disproportionality", "none"),
    ("no co-reports (a = 0)", "none"),
])
def test_signal_classes(name, expected_class):
    assert compute_metrics(*GOLDEN[name][0])["signal_strength"] == expected_class


def test_a_pair_that_is_not_a_signal_is_never_called_moderate():
    for table, _ in GOLDEN.values():
        assert compute_metrics(*table)["signal_strength"] in ("none", "signal", "strong_signal")


def test_criteria_explain_the_class():
    r = compute_metrics(3, 7, 3, 87)
    assert r["criteria"] == {"min_cases": True, "prr": True, "chi2": True,
                             "ror_lower_above_1": True, "ic025_above_0": False}
    r = compute_metrics(2, 8, 1, 989)
    assert r["criteria"]["min_cases"] is False and r["criteria"]["prr"] is True


def test_threshold_edges():
    # exactly three cases is allowed; PRR is then checked on its own
    assert compute_metrics(3, 97, 30, 9870)["criteria"]["min_cases"] is True
    assert compute_metrics(2, 98, 30, 9870)["criteria"]["min_cases"] is False


# ── degenerate tables ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("table", [(0, 0, 0, 0), (0, 0, 5, 100), (0, 10, 0, 100), (5, 10, 0, 0)])
def test_undefined_tables_return_zeros_and_class_none(table):
    r = compute_metrics(*table)
    assert r["signal_strength"] == "none"
    assert r["prr"] == 0.0 and r["ror"] == 0.0 and r["chi2"] == 0.0


def test_negative_counts_are_rejected():
    with pytest.raises(ValueError):
        compute_metrics(-1, 5, 5, 100)


def test_every_value_is_finite():
    import math
    for table, _ in GOLDEN.values():
        for key in ("prr", "prr_ci_low", "prr_ci_high", "ror", "ror_ci_low", "ror_ci_high", "chi2",
                    "bcpnn_ic", "ic025"):
            assert math.isfinite(compute_metrics(*table)[key]), (table, key)


def test_numbers_are_plain_python_types_for_json():
    import json
    json.dumps(compute_metrics(5, 45, 0, 32749))

# backend/tests/test_ddi_calibration.py
# ─────────────────────────────────────────────────────────────────────────────
# Scientific verification tests for temperature scaling calibration:
# 1. Tests TemperatureScaler optimization reducing negative log likelihood.
# 2. Tests Expected Calibration Error (ECE) metric computation.
# 3. Tests calibrated interaction likelihood score numerical bounds [0.0, 1.0].
# ─────────────────────────────────────────────────────────────────────────────

import sys
import os
import numpy as np
import torch

# Ensure the backend packages are importable
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from validation.ddi_calibrate import TemperatureScaler, compute_ece


def test_temperature_scaling_optimization():
    """Verifies that TemperatureScaler fits T > 1 on overconfident logits and lowers NLL."""
    np.random.seed(42)
    torch.manual_seed(42)

    # Synthetic overconfident logits: large magnitude but noisy labels
    logits = np.random.normal(loc=0.0, scale=4.0, size=1000)
    probs = 1.0 / (1.0 + np.exp(-logits))
    # Labels with 30% noise
    labels = (np.random.rand(1000) < (0.7 * probs + 0.15)).astype(float)

    scaler = TemperatureScaler()
    opt_t, val_nll = scaler.fit(logits, labels, lr=0.05, max_iters=100)

    assert opt_t > 0.0, "Temperature must be strictly positive"
    # Overconfident models require T > 1.0 to soften logits
    assert opt_t > 1.0, f"Expected T > 1.0 for overconfident logits, got {opt_t}"

    # Verify scaled NLL is lower than unscaled NLL
    criterion = torch.nn.BCEWithLogitsLoss()
    unscaled_nll = criterion(torch.tensor(logits, dtype=torch.float), torch.tensor(labels, dtype=torch.float)).item()
    assert val_nll < unscaled_nll, f"Expected val_nll ({val_nll}) < unscaled_nll ({unscaled_nll})"


def test_ece_computation():
    """Verifies that ECE is ~0 for well-calibrated predictions and high for miscalibrated."""
    # Perfectly calibrated case: 100 samples with prob=0.8, exactly 80 positive
    probs_perfect = np.array([0.8] * 100 + [0.2] * 100)
    labels_perfect = np.array([1.0] * 80 + [0.0] * 20 + [1.0] * 20 + [0.0] * 80)
    ece_perf, stats_perf = compute_ece(probs_perfect, labels_perfect, n_bins=10)
    assert ece_perf < 0.02, f"Expected low ECE for calibrated probabilities, got {ece_perf}"

    # Highly miscalibrated case: all predictions 0.99, but all labels 0.0
    probs_bad = np.array([0.99] * 100)
    labels_bad = np.array([0.0] * 100)
    ece_bad, _ = compute_ece(probs_bad, labels_bad, n_bins=10)
    assert ece_bad > 0.9, f"Expected high ECE for miscalibrated probabilities, got {ece_bad}"


def test_calibrated_score_bounds():
    """Verifies that calibrated sigmoid outputs lie strictly in (0.0, 1.0)."""
    logits = np.array([-10.0, -2.5, 0.0, 2.5, 10.0])
    temperature = 1.35
    calibrated_probs = 1.0 / (1.0 + np.exp(-logits / temperature))

    assert np.all(calibrated_probs > 0.0)
    assert np.all(calibrated_probs < 1.0)
    # Check monotonicity
    assert np.all(np.diff(calibrated_probs) > 0)


# ── held-out calibration (Phase 8) ────────────────────────────────────────────
from validation import ddi_calibrate as C  # noqa: E402


def _overconfident(n=4000, seed=3):
    rng = np.random.default_rng(seed)
    true_p = rng.uniform(0.2, 0.8, n)
    labels = (rng.random(n) < true_p).astype(float)
    logits = np.log(true_p / (1 - true_p)) * 4.0              # same ranking, far too confident
    groups = rng.integers(0, 40, n)
    return 1 / (1 + np.exp(-logits)), labels, groups


def test_an_overconfident_model_gets_a_calibrator_that_lowers_held_out_ece():
    probs, labels, groups = _overconfident()
    chosen, cv = C.choose_calibrator(probs, labels, groups)
    assert chosen in ("temperature_scaling", "isotonic")
    assert cv[chosen] < cv["uncalibrated"] * 0.6


def test_cross_fitting_does_not_reward_memorising_the_calibration_data():
    """Labels independent of the score: isotonic fits its own data almost perfectly, but not the other fold."""
    rng = np.random.default_rng(0)
    probs = rng.random(3000)
    labels = (rng.random(3000) < 0.5).astype(float)
    groups = rng.integers(0, 30, 3000)
    fit, _ = C.fit_isotonic(probs, labels)
    in_sample = C.compute_ece(fit(probs), labels)[0]
    held_out = C.cross_fit_ece(C.fit_isotonic, probs, labels, groups)
    assert in_sample < 0.05 < held_out or held_out > in_sample * 2


def test_a_well_calibrated_model_is_not_given_a_calibrator_that_does_not_help():
    rng = np.random.default_rng(5)
    probs = rng.uniform(0.05, 0.95, 6000)
    labels = (rng.random(6000) < probs).astype(float)
    groups = rng.integers(0, 40, 6000)
    chosen, cv = C.choose_calibrator(probs, labels, groups)
    assert chosen is None or cv[chosen] < cv["uncalibrated"]


def test_calibrated_is_true_only_when_ece_improved():
    assert C.decide_calibrated("isotonic", 0.08, 0.03) is True
    assert C.decide_calibrated("isotonic", 0.03, 0.03) is False        # no improvement
    assert C.decide_calibrated("temperature_scaling", 0.0768, 0.078) is False   # the old model's case
    assert C.decide_calibrated(None, 0.08, 0.01) is False              # nothing was applied


def test_stored_calibrators_reproduce_the_fitted_one():
    probs, labels, _ = _overconfident(2000)
    for name in ("temperature_scaling", "isotonic"):
        fn, params = C.CALIBRATORS[name](probs, labels)
        assert np.allclose(C.apply_calibration(params, probs[:200]), fn(probs[:200]), atol=1e-4)
    assert np.allclose(C.apply_calibration({"method": None}, probs[:5]), probs[:5])


def test_precision_threshold_is_the_loosest_score_meeting_the_target():
    probs = np.array([0.95, 0.9, 0.85, 0.8, 0.7, 0.6, 0.5, 0.4])
    labels = np.array([1, 1, 1, 1, 0, 1, 0, 0], dtype=float)
    assert C.precision_threshold(probs, labels, 0.8) == 0.6          # top 6 are 5/6 = 0.83
    assert C.precision_threshold(probs, labels, 1.0) == 0.8
    assert C.precision_threshold(np.array([0.9, 0.8]), np.array([0.0, 0.0]), 0.8) is None

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
import pytest

# Ensure scripts folder is importable
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'scripts'))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from calibrate_model import TemperatureScaler, compute_ece


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

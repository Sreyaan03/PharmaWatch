"""
Metrics shared by the signal, DDI and NER benchmarks.

Pure numpy/scipy so they can be checked against scikit-learn on synthetic data
(tests/test_signals_benchmark.py). Labels are 0/1; scores are higher-is-more-positive.
"""

import numpy as np
from scipy.stats import rankdata


def auc(y_true, scores):
    """Area under the ROC curve (Mann-Whitney U, ties counted as half). None if one class only."""
    y = np.asarray(y_true, dtype=int)
    s = np.asarray(scores, dtype=float)
    pos, neg = int((y == 1).sum()), int((y == 0).sum())
    if pos == 0 or neg == 0:
        return None
    ranks = rankdata(s)                                   # average ranks for ties
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg))


def sensitivity_specificity(y_true, predicted):
    """(sensitivity, specificity); each None when its denominator is empty."""
    y = np.asarray(y_true, dtype=int)
    p = np.asarray(predicted, dtype=int)
    pos, neg = int((y == 1).sum()), int((y == 0).sum())
    sens = float(((p == 1) & (y == 1)).sum() / pos) if pos else None
    spec = float(((p == 0) & (y == 0)).sum() / neg) if neg else None
    return sens, spec


def precision_at_k(y_true, scores, k):
    """Share of the k highest-scored items that are positive (ties broken by input order)."""
    y = np.asarray(y_true, dtype=int)
    s = np.asarray(scores, dtype=float)
    k = int(min(k, len(y)))
    if k <= 0:
        return None
    order = np.argsort(-s, kind="stable")[:k]
    return float(y[order].mean())


def bootstrap_ci(metric, y_true, *arrays, n=1000, seed=0, level=0.95):
    """Percentile bootstrap interval of metric(y_true, *arrays) over resampled items.

    Resamples that make the metric undefined (None) are skipped; if fewer than half of the
    resamples are defined, the interval is None. Returns (low, high) or None.
    """
    y = np.asarray(y_true)
    arrs = [np.asarray(a) for a in arrays]
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(n):
        idx = rng.integers(0, len(y), len(y))
        v = metric(y[idx], *[a[idx] for a in arrs])
        if v is not None:
            values.append(v)
    if len(values) < n / 2:
        return None
    alpha = (1 - level) / 2
    return float(np.quantile(values, alpha)), float(np.quantile(values, 1 - alpha))

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

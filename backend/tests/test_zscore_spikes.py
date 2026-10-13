"""zscore_spikes: the single spike detector behind the velocity endpoints."""

import os
import sys

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from faers.stats import zscore_spikes  # noqa: E402


def test_flat_series_has_no_spikes():
    spikes, z = zscore_spikes([10] * 12)
    assert not any(spikes)
    assert z == [0.0] * 12


def test_first_three_points_are_never_flagged():
    spikes, z = zscore_spikes([1, 1000, 1, 1000, 1])
    assert spikes[:3] == [False, False, False]
    assert z[:3] == [0.0, 0.0, 0.0]


def test_jump_after_stable_history_is_flagged():
    counts = [10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 60]
    spikes, z = zscore_spikes(counts)
    assert spikes[-1] is True
    assert z[-1] > 2.0
    assert not any(spikes[:-1])


def test_zero_variance_window_uses_unit_std():
    spikes, z = zscore_spikes([5, 5, 5, 5, 8])
    assert z[-1] == 3.0          # (8 - 5) / 1.0
    assert spikes[-1] is True


def test_one_entry_per_count():
    spikes, z = zscore_spikes(list(range(30)))
    assert len(spikes) == len(z) == 30

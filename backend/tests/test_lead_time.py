"""
The lead-time study groups each label-change pair correctly: flagged before (with the right lead
time), flagged at or after, left-censored (signal already present in the earliest loaded quarter),
or never flagged. Left-censored pairs never count as flagged-before. Pure logic, no IO.
"""

import os
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from validation import lead_time  # noqa: E402

QUARTERS = ["2021q3", "2021q4", "2022q1", "2022q2", "2022q3"]


def test_classify_flagged_before_has_lead_time():
    # label change in 2022q3, first signal in 2022q1 -> 2 quarters earlier
    assert lead_time.classify("2022q3", "2022q1", QUARTERS) == ("flagged_before", 2)


def test_classify_flagged_at_or_after():
    assert lead_time.classify("2022q1", "2022q1", QUARTERS)[0] == "flagged_at_or_after"
    assert lead_time.classify("2022q1", "2022q3", QUARTERS)[0] == "flagged_at_or_after"


def test_classify_left_censored_never_counts_as_before():
    # first signal is the earliest loaded quarter: true first flag is unknown
    group, lead = lead_time.classify("2022q3", "2021q3", QUARTERS)
    assert group == "left_censored" and lead is None


def test_classify_never_flagged():
    assert lead_time.classify("2022q1", None, QUARTERS) == ("never_flagged", None)


def test_label_change_before_window_clamps_to_start():
    # a label change dated before our data starts: never "flagged before" (can't precede the start)
    group, _ = lead_time.classify("2020q1", "2021q4", QUARTERS)
    assert group == "flagged_at_or_after"


def test_compute_groups_and_share():
    pairs = {
        ("1", "rhabdomyolysis"): "2022q3",   # flagged before (first 2022q1)
        ("1", "nausea"): "2022q1",            # flagged at/after (first 2022q2)
        ("2", "pancreatitis"): "2022q3",      # left-censored (first 2021q3)
        ("3", "alopecia"): "2022q1",          # never flagged
    }
    first = {
        ("1", "rhabdomyolysis"): "2022q1",
        ("1", "nausea"): "2022q2",
        ("2", "pancreatitis"): "2021q3",
        ("3", "alopecia"): None,
    }
    s = lead_time.compute(pairs, first, QUARTERS)
    assert s["pairs"] == 4
    assert s["groups"] == {"flagged_before": 1, "flagged_at_or_after": 1,
                           "left_censored": 1, "never_flagged": 1}
    assert s["share_flagged_before"] == 0.25
    assert s["median_lead_quarters"] == 2           # single flagged-before pair, lead 2
    assert s["lead_time_quarters_distribution"] == {"2": 1}

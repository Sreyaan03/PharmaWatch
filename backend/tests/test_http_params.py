"""
bounded_int() turns an untrusted query value into a safe, clamped integer and never raises, so a
route can hand it straight to a service as a row count, page size or offset.
"""

import os
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from core.http_params import bounded_int  # noqa: E402


def test_valid_value_passes_through():
    assert bounded_int("50", 10, 1, 100) == 50


def test_missing_value_uses_default():
    assert bounded_int(None, 10, 1, 100) == 10


def test_garbage_falls_back_to_default():
    for bad in ("abc", "", "  ", "1e9", "3.5", "0x10", "1,000"):
        assert bounded_int(bad, 7, 1, 100) == 7


def test_value_above_maximum_is_clamped():
    assert bounded_int("999999", 10, 1, 500) == 500


def test_negative_value_is_clamped_to_minimum():
    assert bounded_int("-5", 10, 1, 100) == 1


def test_default_itself_is_clamped_into_range():
    assert bounded_int(None, 10000, 1, 500) == 500


def test_whitespace_is_tolerated():
    assert bounded_int("  42 ", 10, 1, 100) == 42

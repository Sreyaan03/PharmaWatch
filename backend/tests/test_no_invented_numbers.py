"""
No number without a source.

With the database unreachable, no FAERS data loaded and the network answering "{}", an analytic
endpoint has nothing to count. It must then return zeros, nulls or an explicit "no data" status;
any other number would have been made up. Every result must also say how it was obtained.
"""

import pytest

from core.response import EVIDENCE_KINDS, with_meta
from faers.stats import MIN_SEASONALITY_CASES, seasonality_test  # noqa: E402
from services.provenance import describe
from test_route_inventory import (PATH_VALUE, QUERY, _concrete, _rules,  # noqa: F401
                                  app, isolated)

# Endpoints whose numbers all come from data: every number must be zero without it.
MEASURED_GET = [
    "/api/prr", "/api/signals", "/api/demographics/sex", "/api/demographics/age",
    "/api/demographics/geo", "/api/demographics/overview", "/api/temporal/tto",
    "/api/temporal/velocity", "/api/temporal/seasonality", "/api/analytics/data-coverage",
    "/api/dashboard/signal-intensity", "/api/dashboard/prr-distribution",
    "/api/dashboard/drug-categories", "/api/dashboard/top-events", "/api/local-stats",
]

# Numbers that are labels or parameters of the request, not measurements.
ALLOWED = {"alpha", "df", "limit", "threshold", "min_cases", "min_reports"}

# A count of the items in the response's own list is not a measurement; it is checked against it.
LIST_LENGTH_KEYS = {"/api/signals": ("total", "signals")}


def _numbers(node, path=""):
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        yield path, node
    elif isinstance(node, dict):
        for k, v in node.items():
            if k == "meta":
                continue
            yield from _numbers(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _numbers(v, f"{path}[{i}]")


@pytest.mark.parametrize("path", MEASURED_GET)
def test_empty_data_yields_no_measurement(isolated, path):  # noqa: F811
    app.config["TESTING"] = True
    res = app.test_client().get(path, query_string=QUERY)
    body = res.get_json(silent=True)
    assert isinstance(body, dict), f"{path} returned {res.status_code} without a JSON object"

    if path in LIST_LENGTH_KEYS:
        count_key, list_key = LIST_LENGTH_KEYS[path]
        assert body[count_key] == len(body[list_key])
        body = {k: v for k, v in body.items() if k != count_key}

    invented = [(p, v) for p, v in _numbers(body)
                if v not in (0, 0.0) and p.rsplit(".", 1)[-1].split("[")[0] not in ALLOWED
                and not p.rsplit(".", 1)[-1].startswith("_")]
    assert not invented, f"{path} returned numbers with no data behind them: {invented[:6]}"


@pytest.mark.parametrize("path", MEASURED_GET)
def test_every_result_says_how_it_was_obtained(isolated, path):  # noqa: F811
    app.config["TESTING"] = True
    res = app.test_client().get(path, query_string=QUERY)
    body = res.get_json(silent=True)
    if res.status_code >= 400:
        pytest.skip("error responses carry no meta")
    meta = body["meta"]
    assert meta["evidence"] in EVIDENCE_KINDS
    assert "data_version" in meta and "generated_at" in meta


def test_every_analytic_route_is_classified():
    """A new /api/ route must be given an evidence kind, or it would ship without provenance."""
    unclassified = []
    for entry in _rules():
        path = _concrete(entry["rule"])
        if path.startswith("/api/") and describe(path) is None:
            unclassified.append(entry["rule"])
    assert not unclassified, f"add these to services/provenance.py: {unclassified}"


def test_with_meta_leaves_existing_keys_and_rejects_unknown_evidence():
    out = with_meta({"a": 1}, "measured", data_version="v", model_version=None)
    assert out["a"] == 1 and out["meta"]["evidence"] == "measured" and out["meta"]["data_version"] == "v"
    assert with_meta({"meta": {"x": 1}}, "measured") == {"meta": {"x": 1}}
    assert with_meta([1, 2], "measured") == [1, 2]
    with pytest.raises(ValueError):
        with_meta({}, "guessed")


# ── seasonality ───────────────────────────────────────────────────────────────
def test_a_calendar_proportional_spread_is_not_seasonal():
    days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    counts = [round(d * 4) for d in days]                    # 4 cases per day, all year
    t = seasonality_test(counts)
    assert t["applicable"] and t["p_value"] > 0.99


def test_a_winter_peak_is_seasonal():
    counts = [60, 55, 20, 15, 10, 8, 8, 9, 12, 15, 25, 50]
    t = seasonality_test(counts)
    assert t["applicable"] and t["p_value"] < 1e-6


def test_too_few_cases_gives_no_verdict():
    counts = [5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]            # looks extreme, but only 5 cases
    assert sum(counts) < MIN_SEASONALITY_CASES
    t = seasonality_test(counts)
    assert t["applicable"] is False and t["p_value"] is None

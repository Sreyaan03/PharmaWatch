"""
The forecast measures deviation in a drug's share of all reports, against a baseline.

  * a drug whose raw count rises exactly in line with total reporting is not flagged;
  * the response always carries the baseline's error and the shown model's error, and the LSTM is
    shown only when it beats the baseline on months it was not fitted on;
  * too short a series is refused, not padded.
"""

import json
import os
import sys

import duckdb
import numpy as np
import pytest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import app  # noqa: E402
from faers.stats import monthly_report_share  # noqa: E402
from ml import forecast  # noqa: E402
from services import forecasting  # noqa: E402


def _months(n, start=(2021, 7)):
    y, m = start
    out = []
    for _ in range(n):
        out.append(f"{y:04d}{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def _series(n=60, share_fn=lambda i: 2.0, total_fn=lambda i: 2000 + 40 * i):
    months = _months(n)
    totals = [total_fn(i) for i in range(n)]
    share = [share_fn(i) for i in range(n)]
    return months, share, totals


# ── normalisation ─────────────────────────────────────────────────────────────
def test_raw_count_rising_with_total_reporting_is_not_flagged():
    """Reporting volume triples; the drug is a constant 2% of it. The raw count triples too."""
    months, share, totals = _series(60, total_fn=lambda i: 1000 + 40 * i)
    counts = [round(s / 100 * t) for s, t in zip(share, totals)]
    assert counts[-1] > 2.5 * counts[0]                     # the raw count rose steeply
    r = forecast.evaluate(months, share, totals)
    assert r["above_interval"] is False and r["consecutive_months_above"] == 0
    assert r["baseline_error"] == pytest.approx(0.0, abs=1e-9)


def test_a_sustained_rise_in_share_is_flagged_after_three_months():
    months, share, totals = _series(60, share_fn=lambda i: 2.0 if i < 56 else 5.0)
    r = forecast.evaluate(months, share, totals)
    assert r["above_interval"] is True
    assert r["consecutive_months_above"] == 4 and r["run_reported"] is True
    assert "consecutive months" in forecast.reporting_scan(r)["message"]


def test_a_single_high_month_is_not_a_run():
    months, share, totals = _series(60, share_fn=lambda i: 5.0 if i == 59 else 2.0)
    r = forecast.evaluate(months, share, totals)
    assert r["above_interval"] is True and r["consecutive_months_above"] == 1
    assert r["run_reported"] is False


def test_a_repeating_seasonal_pattern_is_not_flagged():
    pattern = [1.0, 1.2, 1.5, 2.0, 2.6, 3.0, 2.8, 2.2, 1.6, 1.2, 1.0, 0.9]
    months, share, totals = _series(60, share_fn=lambda i: pattern[int(months_of(i)) - 1])
    r = forecast.evaluate(months, share, totals)
    assert r["above_interval"] is False and r["baseline_error"] == pytest.approx(0.0, abs=1e-9)


def months_of(i):
    return _months(60)[i][4:6]


def test_noise_within_sampling_variation_is_not_flagged():
    rng = np.random.default_rng(7)
    totals = [3000] * 60
    share = [100.0 * rng.binomial(t, 0.02) / t for t in totals]
    r = forecast.evaluate(_months(60), share, totals)
    assert r["consecutive_months_above"] < forecast.MIN_CONSECUTIVE


# ── reported error and model choice ───────────────────────────────────────────
def test_response_carries_baseline_and_model_error_and_a_labelled_unit():
    months, share, totals = _series(60, share_fn=lambda i: 2 + 0.01 * i)
    r = forecast.evaluate(months, share, totals)
    for key in ("baseline_error", "model_error", "lstm_error", "model_used", "interval", "unit",
                "holdout_months", "evaluated_months", "error_metric"):
        assert key in r
    assert r["model_used"] == "seasonal_naive" and r["lstm_error"] is None
    assert r["model_error"] == r["baseline_error"]
    assert len(r["labels"]) == len(r["actual"]) == len(r["baseline"]) == len(r["upper"]) == 60
    assert r["baseline"][:48] == [None] * 48 and None not in r["baseline"][48:]


def test_lstm_is_shown_only_when_it_beats_the_baseline_on_the_test_window():
    months, share, totals = _series(60, share_fn=lambda i: 2 + 0.01 * i)
    worse = forecast.evaluate(months, share, totals, lambda t: share[t] + 3.0)
    assert worse["model_used"] == "seasonal_naive" and worse["lstm_error"] > worse["baseline_error"]
    better = forecast.evaluate(months, share, totals, lambda t: share[t])
    assert better["model_used"] == "lstm" and better["model_error"] == better["lstm_error"] == 0.0


def test_a_short_series_is_refused_not_padded():
    months, share, totals = _series(24)
    with pytest.raises(ValueError):
        forecast.evaluate(months, share, totals)


# ── training uses only the training window; stale models are not used ─────────
@pytest.fixture
def model_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(forecast, "MODEL_DIR", str(tmp_path))
    return tmp_path


def test_training_fits_on_the_first_months_only_and_reports_held_out_error(model_dir):
    pytest.importorskip("torch")
    months, share, totals = _series(60, share_fn=lambda i: 2 + 0.5 * np.sin(i / 2))
    info, error = forecast.train_model("Druga", months, share, totals, epochs=15)
    assert error is None and info["lstm_error"] is not None and info["baseline_error"] is not None
    meta = json.loads(next(model_dir.glob("*_meta.json")).read_text())
    assert meta["train_end_month"] == months[60 - 2 * forecast.HOLDOUT - 1]
    assert meta["months"] == 60


def test_trained_model_is_used_for_the_same_data_and_marked_stale_otherwise(model_dir):
    pytest.importorskip("torch")
    months, share, totals = _series(60, share_fn=lambda i: 2 + 0.5 * np.sin(i / 2))
    forecast.train_model("Druga", months, share, totals, epochs=10)

    same = forecast.predict("Druga", months, share, totals)
    assert same["lstm_status"] == "trained" and same["lstm_error"] is not None

    more = forecast.predict("Druga", _months(61), share + [2.0], totals + [4000])
    assert more["lstm_status"] == "stale" and more["lstm_error"] is None
    assert more["model_used"] == "seasonal_naive"

    untrained = forecast.predict("Otherdrug", months, share, totals)
    assert untrained["lstm_status"] == "not_trained"


def test_too_few_months_to_train_is_an_error_not_a_model(model_dir):
    pytest.importorskip("torch")
    months, share, totals = _series(40)
    info, error = forecast.train_model("Druga", months, share, totals, epochs=2)
    assert info is None and "48" in error
    assert forecast.predict("Druga", months, share, totals)["lstm_status"] == "insufficient_data"


# ── monthly share from the database ──────────────────────────────────────────
def test_monthly_share_counts_suspect_reports_over_all_reports(tmp_path):
    db = str(tmp_path / "share.duckdb")
    c = duckdb.connect(db)
    c.execute("CREATE TABLE faers_demo (primaryid VARCHAR, fda_dt VARCHAR)")
    c.execute("CREATE TABLE case_drug (primaryid VARCHAR, ingredient_rxcui VARCHAR, suspect BOOLEAN)")
    rows = [("a1", "20240105"), ("a2", "20240120"), ("a3", "20240130"), ("a4", "20240131"),
            ("b1", "20240201"), ("b2", "20240215"), ("c1", "2024"), ("c2", "")]
    c.executemany("INSERT INTO faers_demo VALUES (?, ?)", rows)
    c.executemany("INSERT INTO case_drug VALUES (?, ?, ?)", [
        ("a1", "9001", True), ("a2", "9001", True), ("a3", "9001", False),   # a3: concomitant only
        ("b1", "9001", True), ("b2", "7777", True), ("c1", "9001", True)])
    s = monthly_report_share(c, ["9001"])
    c.close()
    assert s["months"] == ["202401", "202402"]
    assert s["drug"] == [2, 1] and s["total"] == [4, 2]
    assert s["share"] == [50.0, 50.0]


# ── service and route ────────────────────────────────────────────────────────
def test_the_service_reports_no_data_instead_of_padding(monkeypatch):
    from services import signals
    from faers import analytics
    monkeypatch.setattr(signals, "resolve_drug", lambda d: {"ready": True, "ok": True, "rxcuis": ["1"],
                                                            "identity": {}, "unresolved": [], "note": ""})
    monkeypatch.setattr(analytics, "get_monthly_share",
                        lambda r: {"months": _months(5), "drug": [1] * 5, "total": [10] * 5, "share": [10.0] * 5})
    body, status = forecasting.get_lstm_forecast("Druga")
    assert status == 200 and body["source"] == "no_data"
    assert body["months_available"] == 5 and body["actual"] == [] and body["baseline"] == []
    assert "at least 36" in body["message"]


def test_the_route_returns_the_forecast(monkeypatch):
    from services import signals
    from faers import analytics
    months, share, totals = _series(60, share_fn=lambda i: 2.0 if i < 56 else 5.0)
    drug = [round(s / 100 * t) for s, t in zip(share, totals)]
    monkeypatch.setattr(signals, "resolve_drug", lambda d: {"ready": True, "ok": True, "rxcuis": ["1"],
                                                            "identity": {"input": d}, "unresolved": [], "note": ""})
    monkeypatch.setattr(analytics, "get_monthly_share",
                        lambda r: {"months": months, "drug": drug, "total": totals, "share": share})
    app.config["TESTING"] = True
    body = app.test_client().get("/api/lstm?drug=Druga").get_json()
    assert body["source"] == "faers_local" and body["fallback"] is False
    assert body["run_reported"] is True and body["consecutive_months_above"] == 4
    assert body["baseline_error"] is not None and body["model_error"] is not None
    assert "rising_signal" not in body and "trend_slope" not in body
    assert body["meta"]["evidence"] == "modelled"


def test_the_gap_scan_makes_no_statement_about_needing_a_boxed_warning(monkeypatch):
    from services import signals
    from faers import analytics
    months, share, totals = _series(60, share_fn=lambda i: 2.0 if i < 56 else 5.0)
    monkeypatch.setattr(signals, "resolve_drug", lambda d: {"ready": True, "ok": True, "rxcuis": ["1"],
                                                            "identity": {}, "unresolved": [], "note": ""})
    monkeypatch.setattr(analytics, "get_monthly_share",
                        lambda r: {"months": months, "drug": [1] * 60, "total": totals, "share": share})
    monkeypatch.setattr(forecasting, "fetch_trial_monitoring",
                        lambda d: {"being_monitored": False, "active_studies": 0, "phase34_studies": 0})
    app.config["TESTING"] = True
    body = app.test_client().get("/api/lstm/gap-scan?drug=Druga").get_json()
    text = json.dumps(body).lower()
    for word in ("boxed warning", "risk_level", "needs a", "emerging risk", "rising_signal", "effective"):
        assert word not in text, word
    assert body["run_reported"] is True
    assert "does not show a change in risk" in body["message"]

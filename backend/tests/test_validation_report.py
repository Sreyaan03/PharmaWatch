"""The Methods & Validation view shows only what the recorded benchmark files contain."""

import json
import os
import sys

import pytest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from app import app  # noqa: E402
from services import validation as svc  # noqa: E402
from validation import report  # noqa: E402

DDI = {"benchmark": "ddi_deployed", "model": "M", "model_version": "v1", "calibrated": True, "run_at": "t",
       "dataset": "D", "regimes": {"S1 (One Unseen)": {"AUROC": 0.8, "AUPRC": 0.81, "P@100": 0.9, "P@1000": 0.9,
                                                       "pairs": 10, "ece_served": 0.03}}}


def test_the_summary_contains_only_values_from_the_component_files(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORTS_DIR", str(tmp_path))
    (tmp_path / "ddi.json").write_text(json.dumps(DDI), encoding="utf-8")
    s = report.build_summary()
    assert list(s["components"]) == ["ddi"] and set(s["not_run"]) == {"signals", "ner", "prescription", "scaling", "bigdata_timing", "label_check"}
    row = s["components"]["ddi"]["rows"][0]
    assert row["metrics"]["AUPRC"] == [0.81, None] and row["metrics"]["ECE (served score)"] == [0.03, None]
    assert (tmp_path / "summary.json").exists()
    assert s["known_limitations"], "limitations must always be shown"


def test_a_missing_component_is_listed_not_filled_in(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORTS_DIR", str(tmp_path))
    s = report.build_summary(write=False)
    assert s["components"] == {} and set(s["not_run"]) == {"signals", "ddi", "ner", "prescription", "scaling", "bigdata_timing", "label_check"}


def test_the_draft_outcome_map_is_called_out_on_the_signals_component(tmp_path, monkeypatch):
    monkeypatch.setattr(report, "REPORTS_DIR", str(tmp_path))
    view = {"covered_pairs_only": {}, "all_pairs_uncovered_as_no_signal": {}}
    stats = {"pairs": 4, "auc_prr": 0.7, "auc_prr_ci": [0.6, 0.8], "auc_ror_lower": 0.7, "auc_ror_lower_ci": [0.6, 0.8],
             "auc_ic025": 0.7, "auc_ic025_ci": [0.6, 0.8], "signal_sensitivity": 0.5, "signal_sensitivity_ci": [0.3, 0.7],
             "signal_specificity": 0.9, "signal_specificity_ci": [0.8, 1.0]}
    sig = {"data_version": "FAERS test", "run_at": "t", "outcome_definitions": {"status": ["draft"]},
           "results": {"omop": {"covered_pairs_only": stats, "all_pairs_uncovered_as_no_signal": stats,
                                "reference_pairs": 4, "coverage": 0.5}}}
    (tmp_path / "signals.json").write_text(json.dumps(sig), encoding="utf-8")
    comp = report.build_summary(write=False)["components"]["signals"]
    assert "draft" in comp["caveat"] and comp["data_version"] == "FAERS test"


def test_endpoint_says_so_when_nothing_has_been_run(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "SUMMARY_PATH", str(tmp_path / "none.json"))
    app.config["TESTING"] = True
    body = app.test_client().get("/api/validation/summary").get_json()
    assert body["components"] == {} and "run_all" in body["message"]


def test_endpoint_returns_the_recorded_file(tmp_path, monkeypatch):
    p = tmp_path / "summary.json"
    p.write_text(json.dumps({"components": {"ddi": {"title": "x"}}, "not_run": [], "known_limitations": []}),
                 encoding="utf-8")
    monkeypatch.setattr(svc, "SUMMARY_PATH", str(p))
    app.config["TESTING"] = True
    body = app.test_client().get("/api/validation/summary").get_json()
    assert body["components"]["ddi"]["title"] == "x" and body["meta"]["evidence"] == "measured"

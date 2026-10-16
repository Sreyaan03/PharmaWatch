"""
The validation regression gate: accuracy cannot fall below the recorded baseline unnoticed.

  * the gate logic itself (pure, always runs);
  * the deployed interaction model on the committed splits (runs wherever the model files are);
  * the signal benchmark's logic on a database with known answers (a logic gate: real accuracy
    needs real FAERS data and is gated locally);
  * the NER benchmark, only where the ADE corpus and the model are available.
"""

import copy
import os
import sys

import duckdb
import pytest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from validation import gate  # noqa: E402


# ── the gate logic ────────────────────────────────────────────────────────────
EXPECTED = {"A.AUPRC": {"baseline": 0.80, "tolerance": 0.02}, "B.AUC": {"baseline": 0.70, "tolerance": 0.0}}


def test_a_metric_within_tolerance_passes():
    assert gate.check({"A.AUPRC": 0.785, "B.AUC": 0.70}, EXPECTED) == []


def test_a_metric_below_baseline_minus_tolerance_fails():
    failures = gate.check({"A.AUPRC": 0.77, "B.AUC": 0.70}, EXPECTED)
    assert len(failures) == 1 and "A.AUPRC" in failures[0] and "below 0.7800" in failures[0]


def test_a_metric_that_disappears_fails():
    assert any("no value produced" in f for f in gate.check({"A.AUPRC": 0.9}, EXPECTED))
    assert any("no value produced" in f for f in gate.check({"A.AUPRC": 0.9, "B.AUC": None}, EXPECTED))


def test_improvement_never_fails():
    assert gate.check({"A.AUPRC": 0.99, "B.AUC": 0.95}, EXPECTED) == []


def test_degrading_a_recorded_baseline_makes_the_gate_fail():
    """The acceptance check: deliberately worsening a result is caught."""
    baselines = gate.load_baselines()
    for bench, expected in baselines.items():
        if bench.startswith("_"):
            continue
        perfect = {k: v["baseline"] for k, v in expected.items() if not k.startswith("_")}
        assert gate.check(perfect, expected) == [], bench
        worse = {k: v - expected[k]["tolerance"] - 0.05 for k, v in perfect.items()}
        assert gate.check(worse, expected), f"{bench}: a clear drop was not caught"


# ── deployed interaction model ───────────────────────────────────────────────
def _manifest_present():
    from core.config import MODEL_DIR
    return os.path.exists(os.path.join(MODEL_DIR, "ddi_manifest.json"))


@pytest.mark.skipif(not _manifest_present(), reason="no deployed interaction model")
def test_deployed_interaction_model_meets_its_baseline(tmp_path):
    from validation import ddi_deployed
    report, _ = ddi_deployed.run(smoke=True, out_path=str(tmp_path / "ddi_smoke.json"))
    failures = gate.check(gate.ddi_metrics(report), gate.load_baselines()["ddi_smoke"])
    assert not failures, failures


# ── signal benchmark logic ───────────────────────────────────────────────────
def test_signal_benchmark_reproduces_its_known_answers(tmp_path, monkeypatch):
    """Database built so the AUCs are known exactly; guards the code, not the data."""
    from validation import reference_sets as ref
    from validation import signals_benchmark as sb

    db = str(tmp_path / "gate.duckdb")
    c = duckdb.connect(db)
    c.execute("CREATE TABLE faers_demo (primaryid VARCHAR)")
    c.execute("CREATE TABLE faers_reac (primaryid VARCHAR, pt VARCHAR)")
    c.execute("CREATE TABLE case_drug (primaryid VARCHAR, ingredient_rxcui VARCHAR, suspect BOOLEAN)")
    n = 0

    def add(drug, with_event, count):
        nonlocal n
        for _ in range(count):
            n += 1
            c.execute("INSERT INTO faers_demo VALUES (?)", [f"c{n}"])
            if drug:
                c.execute("INSERT INTO case_drug VALUES (?, ?, TRUE)", [f"c{n}", drug])
            if with_event:
                c.execute("INSERT INTO faers_reac VALUES (?, 'Event one')", [f"c{n}"])
    add(None, True, 20)
    add(None, False, 380)
    for drug, k in (("1", 15), ("2", 1), ("3", 1), ("4", 4)):
        add(drug, True, k)
        add(drug, False, 30 - k)
    c.close()

    rows = [{"drug": f"d{i}", "outcome": "o", "label": lab} for i, lab in ((1, 1), (2, 1), (3, 0), (4, 0))]
    monkeypatch.setattr(ref, "SETS", {"toy": "toy.csv"})
    monkeypatch.setattr(ref, "load_set", lambda name: list(rows))
    monkeypatch.setattr(ref, "load_drug_map", lambda: {f"d{i}": {"rxcuis": [str(i)]} for i in range(1, 5)})
    monkeypatch.setattr(ref, "load_outcome_pts", lambda: ({"o": ["Event one"]}, {"draft"}))
    monkeypatch.setattr(ref, "verify_checksums", lambda: [])
    monkeypatch.setattr(ref, "_sha256", lambda p: "x")
    report, _ = sb.run(db, smoke=True, out_path=str(tmp_path / "s.json"))
    m = gate.signals_metrics(report)
    assert m["toy.covered.auc_prr"] == 1.0
    assert m["toy.all.auc_prr"] == pytest.approx(0.625)
    assert m["toy.coverage"] == 0.5


# ── text mining (only where its inputs exist) ────────────────────────────────
def _ner_inputs_present():
    from validation import ner_benchmark
    return os.path.exists(ner_benchmark.DEFAULT_DATA)


@pytest.mark.skipif(not _ner_inputs_present(), reason="ADE corpus not available (set DATA_DIR)")
def test_ner_meets_its_baseline(tmp_path):
    from validation import ner_benchmark
    report, _ = ner_benchmark.run(ner_benchmark.DEFAULT_DATA, smoke=True, out_path=str(tmp_path / "ner.json"))
    failures = gate.check(gate.ner_metrics(report), gate.load_baselines()["ner_smoke"])
    assert not failures, failures

"""
Signal-detection benchmark: metric correctness, frozen reference data, and the runner on data
built so the right answers are known.
"""

import csv
import os
import sys

import duckdb
import numpy as np
import pytest

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from validation import metrics as M  # noqa: E402
from validation import reference_sets as ref  # noqa: E402
from validation import signals_benchmark as sb  # noqa: E402


# ── metrics against scikit-learn ──────────────────────────────────────────────
def test_auc_matches_scikit_learn_including_ties():
    from sklearn.metrics import roc_auc_score
    rng = np.random.default_rng(3)
    for _ in range(20):
        y = rng.integers(0, 2, 60)
        s = rng.integers(0, 8, 60).astype(float)          # many ties
        if y.min() == y.max():
            continue
        assert M.auc(y, s) == pytest.approx(roc_auc_score(y, s), abs=1e-12)


def test_auc_is_undefined_with_one_class():
    assert M.auc([1, 1, 1], [0.1, 0.2, 0.3]) is None


def test_sensitivity_specificity_and_precision_at_k_by_hand():
    y = [1, 1, 1, 0, 0, 0, 0]
    p = [1, 1, 0, 1, 0, 0, 0]
    assert M.sensitivity_specificity(y, p) == (pytest.approx(2 / 3), pytest.approx(3 / 4))
    assert M.sensitivity_specificity([0, 0], [0, 1]) == (None, 0.5)
    s = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3]
    assert M.precision_at_k(y, s, 2) == 1.0
    assert M.precision_at_k(y, s, 4) == 0.75
    assert M.precision_at_k(y, s, 100) == pytest.approx(3 / 7)


def test_bootstrap_interval_brackets_the_point_estimate_and_is_reproducible():
    rng = np.random.default_rng(1)
    y = np.array([0] * 60 + [1] * 60)
    s = np.concatenate([rng.normal(0, 1, 60), rng.normal(1.0, 1, 60)])
    point = M.auc(y, s)
    lo, hi = M.bootstrap_ci(M.auc, y, s, n=300, seed=5)
    assert lo < point < hi and hi - lo < 0.3
    assert M.bootstrap_ci(M.auc, y, s, n=300, seed=5) == (lo, hi)


# ── frozen reference data ─────────────────────────────────────────────────────
def test_reference_sets_have_the_published_sizes_and_valid_labels():
    omop, euadr = ref.load_set("omop"), ref.load_set("euadr")
    assert (len(omop), sum(r["label"] for r in omop)) == (399, 165)          # Ryan 2013
    assert (len(euadr), sum(r["label"] for r in euadr)) == (93, 43)          # package copy; paper has 94/44
    assert {r["label"] for r in omop + euadr} == {0, 1}
    for rows in (omop, euadr):
        keys = [(r["drug"], r["outcome"]) for r in rows]
        assert len(keys) == len(set(keys)), "a drug-outcome pair appears twice"


def test_reference_files_match_the_freeze():
    assert ref.verify_checksums() == [], "reference data changed: update CHECKSUMS.sha256 deliberately"


def test_reference_every_outcome_has_preferred_terms_and_the_map_is_marked_draft():
    pts, statuses = ref.load_outcome_pts()
    outcomes = {r["outcome"] for s in ref.SETS for r in ref.load_set(s)}
    assert outcomes <= set(pts), outcomes - set(pts)
    assert all(pts[o] for o in outcomes)
    assert statuses == {"draft"}, "update README sign-off text when a reviewer changes the status"


def test_reference_drug_resolution_rate_is_reported_and_high():
    drug_map = ref.load_drug_map()
    names = {r["drug"] for s in ref.SETS for r in ref.load_set(s)}
    assert names <= set(drug_map), "drug_rxnorm_map.csv is missing drugs"
    rate = sum(drug_map[n]["resolved"] for n in names) / len(names)
    assert rate >= 0.95, f"only {rate:.0%} of reference drugs resolve"


def test_reference_no_resolution_is_to_a_similarly_named_other_drug():
    """Regression: the normalised RxNav search once mapped sulfamethoxazole to sulfamethazine."""
    m = ref.load_drug_map()
    for drug, wrong in (("sulfamethoxazole", "sulfamethazine"), ("scopolamine", "methscopolamine")):
        if drug in m and m[drug]["resolved"]:
            assert wrong not in [i.lower() for i in m[drug]["ingredients"]], drug


def test_reference_outcome_terms_exist_in_the_loaded_data():
    from faers.analytics import DB_PATH
    from faers.registry import list_quarters
    quarters = list_quarters(DB_PATH) if os.path.exists(DB_PATH) else []
    if not quarters or all(q["is_fixture"] for q in quarters):
        pytest.skip("needs real FAERS data loaded (set DATA_DIR)")
    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        present = {r[0].lower() for r in con.execute("SELECT DISTINCT pt FROM faers_reac").fetchall()}
    finally:
        con.close()
    pts, _ = ref.load_outcome_pts()
    missing = {o for o, terms in pts.items() if not any(t.lower() in present for t in terms)}
    assert not missing, f"no Preferred Term present in the data for: {missing}"


# ── the runner on a database with known answers ───────────────────────────────
@pytest.fixture
def known_db(tmp_path):
    db = str(tmp_path / "bench.duckdb")
    c = duckdb.connect(db)
    c.execute("CREATE TABLE faers_demo (primaryid VARCHAR)")
    c.execute("CREATE TABLE faers_reac (primaryid VARCHAR, pt VARCHAR)")
    c.execute("CREATE TABLE case_drug (primaryid VARCHAR, ingredient_rxcui VARCHAR, suspect BOOLEAN)")
    n = 0

    def add(drug, with_event, count):
        nonlocal n
        for _ in range(count):
            n += 1
            pid = f"c{n}"
            c.execute("INSERT INTO faers_demo VALUES (?)", [pid])
            if drug:
                c.execute("INSERT INTO case_drug VALUES (?, ?, TRUE)", [pid, drug])
            if with_event:
                c.execute("INSERT INTO faers_reac VALUES (?, 'Event one')", [pid])

    add(None, True, 20)           # background: 5% have the event
    add(None, False, 380)
    for drug, with_event in (("1", 15), ("2", 1), ("3", 1), ("4", 4)):
        add(drug, True, with_event)
        add(drug, False, 30 - with_event)
    c.close()
    return db


@pytest.fixture
def patched(monkeypatch):
    rows = [{"drug": "d1", "outcome": "o", "label": 1}, {"drug": "d2", "outcome": "o", "label": 1},
            {"drug": "d3", "outcome": "o", "label": 0}, {"drug": "d4", "outcome": "o", "label": 0},
            {"drug": "d5", "outcome": "o", "label": 0}]
    monkeypatch.setattr(ref, "SETS", {"toy": "toy.csv"})
    monkeypatch.setattr(ref, "load_set", lambda name: list(rows))
    monkeypatch.setattr(ref, "load_drug_map", lambda: {
        "d1": {"rxcuis": ["1"]}, "d2": {"rxcuis": ["2"]}, "d3": {"rxcuis": ["3"]},
        "d4": {"rxcuis": ["4"]}, "d5": {"rxcuis": []}})
    monkeypatch.setattr(ref, "load_outcome_pts", lambda: ({"o": ["Event one"]}, {"draft"}))
    monkeypatch.setattr(ref, "verify_checksums", lambda: [])
    monkeypatch.setattr(ref, "_sha256", lambda p: "x")


def test_runner_separates_covered_uncovered_and_unresolved_pairs(known_db, patched, tmp_path):
    report, path = sb.run(known_db, out_path=str(tmp_path / "out.json"))
    r = report["results"]["toy"]
    assert r["status_counts"] == {"covered": 2, "uncovered": 2, "drug_unresolved": 1}
    assert r["coverage"] == pytest.approx(0.4)
    status = {p["drug"]: p["status"] for p in r["pairs"]}
    assert status == {"d1": "covered", "d2": "uncovered", "d3": "uncovered", "d4": "covered", "d5": "drug_unresolved"}
    assert report["outcome_definitions"]["status"] == ["draft"] and os.path.exists(path)


def test_runner_scores_covered_only_and_all_pairs_as_defined(known_db, patched, tmp_path):
    r = sb.run(known_db, out_path=str(tmp_path / "out.json"))[0]["results"]["toy"]
    covered = r["covered_pairs_only"]
    assert covered["pairs"] == 2 and (covered["positives"], covered["negatives"]) == (1, 1)
    assert covered["auc_prr"] == 1.0                      # d1 (PRR ~10) above d4 (PRR ~2.6)
    allp = r["all_pairs_uncovered_as_no_signal"]
    assert allp["pairs"] == 4 and allp["auc_prr"] == pytest.approx(0.625)
    # app rule: d1 is a signal, d4 may be; uncovered d2 (a positive) counts as a miss
    assert allp["signal_sensitivity"] == 0.5
    assert covered["signal_sensitivity"] == 1.0


def test_runner_result_file_records_what_a_reader_needs(known_db, patched, tmp_path):
    report, _ = sb.run(known_db, out_path=str(tmp_path / "out.json"))
    for key in ("data_version", "run_at", "reference_checksums", "reference_files_match_freeze",
                "app_rule", "bootstrap", "outcome_definitions"):
        assert key in report
    assert report["bootstrap"]["resamples"] == 1000


def test_smoke_report_has_no_per_pair_rows(known_db, patched, tmp_path):
    report, _ = sb.run(known_db, smoke=True, out_path=str(tmp_path / "s.json"))
    assert "pairs" not in report["results"]["toy"]


# ── contingency over several terms ────────────────────────────────────────────
def test_several_terms_are_one_event(tmp_path):
    from faers.stats import contingency
    db = str(tmp_path / "multi.duckdb")
    c = duckdb.connect(db)
    c.execute("CREATE TABLE faers_demo (primaryid VARCHAR)")
    c.execute("CREATE TABLE faers_reac (primaryid VARCHAR, pt VARCHAR)")
    c.execute("CREATE TABLE case_drug (primaryid VARCHAR, ingredient_rxcui VARCHAR, suspect BOOLEAN)")
    for pid, drug, pts in (("a", "1", ["Term A"]), ("b", "1", ["Term B", "Term A"]), ("c", "1", []),
                           ("d", None, ["Term B"]), ("e", None, [])):
        c.execute("INSERT INTO faers_demo VALUES (?)", [pid])
        if drug:
            c.execute("INSERT INTO case_drug VALUES (?, ?, TRUE)", [pid, drug])
        for pt in pts:
            c.execute("INSERT INTO faers_reac VALUES (?, ?)", [pid, pt])
    one = contingency(c, ["1"], "term a")
    both = contingency(c, ["1"], ["Term A", "Term B"])
    c.close()
    assert (one["a"], one["c"]) == (2, 0)
    assert (both["a"], both["b"], both["c"], both["d"]) == (2, 1, 1, 1)         # case b counted once


def test_one_pass_tables_equal_the_per_pair_contingency(known_db):
    from faers.stats import contingency
    rows = [{"drug": d, "outcome": "o", "label": 1} for d in ("d1", "d2", "d3", "d4")]
    drug_map = {"d1": {"rxcuis": ["1"]}, "d2": {"rxcuis": ["2"]}, "d3": {"rxcuis": ["3"]},
                "d4": {"rxcuis": ["4"]}}
    con = duckdb.connect(known_db, read_only=True)
    tables = sb._tables(con, rows, drug_map, {"o": ["Event one"]})
    for d, x in (("d1", "1"), ("d2", "2"), ("d3", "3"), ("d4", "4")):
        t = contingency(con, [x], ["Event one"])
        a, exposed, event, n = tables[(d, "o")]
        assert (a, exposed - a, event - a, n - exposed - event + a) == (t["a"], t["b"], t["c"], t["d"])
    con.close()


def test_a_combination_needs_every_ingredient_suspect(tmp_path):
    db = str(tmp_path / "combo.duckdb")
    c = duckdb.connect(db)
    c.execute("CREATE TABLE faers_demo (primaryid VARCHAR)")
    c.execute("CREATE TABLE faers_reac (primaryid VARCHAR, pt VARCHAR)")
    c.execute("CREATE TABLE case_drug (primaryid VARCHAR, ingredient_rxcui VARCHAR, suspect BOOLEAN)")
    for pid, drugs in (("a", [("1", True), ("2", True)]), ("b", [("1", True)]), ("c", [("1", True), ("2", False)])):
        c.execute("INSERT INTO faers_demo VALUES (?)", [pid])
        c.execute("INSERT INTO faers_reac VALUES (?, 'E')", [pid])
        for rx, sus in drugs:
            c.execute("INSERT INTO case_drug VALUES (?, ?, ?)", [pid, rx, sus])
    c.close()
    con = duckdb.connect(db, read_only=True)
    t = sb._tables(con, [{"drug": "combo", "outcome": "o"}], {"combo": {"rxcuis": ["1", "2"]}}, {"o": ["E"]})
    con.close()
    assert t[("combo", "o")] == (1, 1, 3, 3)            # only case a has both suspect

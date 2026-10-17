"""
Real time-to-onset and stratified disproportionality.

  * onset is the difference between the report's own therapy start and event dates; reports with
    incomplete dates are counted by reason, never estimated;
  * each sex or age stratum has its own 2x2 table, and a group is called higher risk only when its
    PRR interval lies entirely above the others';
  * no number is built from a constant.
"""

import os
import sys

import duckdb
import pytest
import requests

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for p in (BASE_DIR, os.path.dirname(__file__)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app import app  # noqa: E402
from core.cache import PayloadCache  # noqa: E402
from faers import analytics  # noqa: E402
from faers.stats import (AGE_STRATA, SEX_STRATA, reports_by_country, stratified_metrics,  # noqa: E402
                         time_to_onset, unclassified_cases)
from services import demographics as demographics_service  # noqa: E402
from services import signals as signals_service  # noqa: E402
from services import temporal as temporal_service  # noqa: E402
from services.identity import IdentityService  # noqa: E402
from test_single_stats_path import LISINOPRIL, WARFARIN, _build  # noqa: E402


@pytest.fixture(scope="module")
def fixture_db(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("stats_real"))


@pytest.fixture
def con(fixture_db):
    c = duckdb.connect(fixture_db, read_only=True)
    yield c
    c.close()


# ── time to onset ─────────────────────────────────────────────────────────────
def test_onset_is_the_difference_between_the_reports_own_dates(con):
    r = time_to_onset(con, WARFARIN, "Haemorrhage", min_cases=1)
    # case 1006: therapy started 2023-12-01, event 2024-01-01 = 31 days
    assert r["cases"] == 2 and r["usable_cases"] == 1
    assert r["median_days"] == 31.0
    assert r["counts"] == [0, 1, 0, 0, 0, 0] and r["peak_bucket"] == "31-60 days"


def test_reports_without_a_therapy_start_are_counted_not_estimated(con):
    r = time_to_onset(con, WARFARIN, "Haemorrhage", min_cases=1)
    assert r["excluded"] == {"no_complete_event_date": 0, "no_complete_therapy_start": 1,
                             "event_before_therapy": 0}


def test_too_few_usable_cases_gives_no_summary(con):
    r = time_to_onset(con, WARFARIN, "Haemorrhage")            # default minimum is 10
    assert r["usable_cases"] == 1
    assert r["median_days"] is None and r["peak_bucket"] is None and r["q1_days"] is None


def _onset_db(tmp_path, cases):
    """cases: [(primaryid, event_dt, [start_dt, ...])] for one drug 'X' (rxcui 1) and event 'E'."""
    db = str(tmp_path / "onset.duckdb")
    c = duckdb.connect(db)
    c.execute("CREATE TABLE faers_demo (primaryid VARCHAR, event_dt VARCHAR)")
    c.execute("CREATE TABLE faers_reac (primaryid VARCHAR, pt VARCHAR)")
    c.execute("CREATE TABLE case_drug (primaryid VARCHAR, ingredient_rxcui VARCHAR, suspect BOOLEAN)")
    c.execute("CREATE TABLE faers_drug (primaryid VARCHAR, drug_seq VARCHAR, role_cod VARCHAR, drugname VARCHAR, prod_ai VARCHAR)")
    c.execute("CREATE TABLE faers_ther (primaryid VARCHAR, dsg_drug_seq VARCHAR, start_dt VARCHAR)")
    c.execute("CREATE TABLE drug_identity (raw_name VARCHAR, ingredient_rxcui VARCHAR)")
    c.execute("INSERT INTO drug_identity VALUES ('x', '1')")
    for pid, event_dt, starts in cases:
        c.execute("INSERT INTO faers_demo VALUES (?, ?)", [pid, event_dt])
        c.execute("INSERT INTO faers_reac VALUES (?, 'E')", [pid])
        c.execute("INSERT INTO case_drug VALUES (?, '1', TRUE)", [pid])
        c.execute("INSERT INTO faers_drug VALUES (?, '1', 'PS', 'X', 'X')", [pid])
        for s in starts:
            c.execute("INSERT INTO faers_ther VALUES (?, '1', ?)", [pid, s])
    c.close()
    return db


def test_onset_edge_cases_are_each_counted_in_the_right_place(tmp_path):
    db = _onset_db(tmp_path, [
        ("p1", "20240111", ["20240102"]),             # 9 days
        ("p2", "20240601", ["20231201"]),             # 183 days
        ("p3", "20240102", ["20240301"]),             # event before therapy
        ("p4", "20240102", ["202312"]),               # start only to the month: incomplete
        ("p5", "202401", ["20231201"]),               # event only to the month: incomplete
        ("p6", "20240402", ["20240102", "20240301"]), # two courses: the earliest start is used (91 days)
        ("p7", "20240131", ["20240131"]),             # same day: 0 days, allowed
    ])
    c = duckdb.connect(db, read_only=True)
    r = time_to_onset(c, "1", "E", min_cases=1)
    c.close()
    assert r["cases"] == 7 and r["usable_cases"] == 4
    assert r["excluded"] == {"no_complete_event_date": 1, "no_complete_therapy_start": 1,
                             "event_before_therapy": 1}
    assert r["counts"] == [2, 0, 0, 1, 1, 0]          # 0 and 9 | 91 | 183
    assert r["median_days"] == pytest.approx(50.0)    # median of 0, 9, 91, 183


def test_the_onset_buckets_have_no_overlap_or_gap():
    from faers.stats import ONSET_BUCKETS
    previous_high = -1
    for _, low, high in ONSET_BUCKETS:
        assert low == previous_high + 1
        previous_high = high if high is not None else previous_high
    assert ONSET_BUCKETS[-1][2] is None


# ── strata ────────────────────────────────────────────────────────────────────
def test_each_sex_has_its_own_table(con):
    s = stratified_metrics(con, WARFARIN, "Haemorrhage", SEX_STRATA)
    assert (s["F"]["a"], s["F"]["b"], s["F"]["c"], s["F"]["d"], s["F"]["n"]) == (1, 0, 0, 2, 3)
    assert (s["M"]["a"], s["M"]["b"], s["M"]["c"], s["M"]["d"], s["M"]["n"]) == (1, 0, 0, 2, 3)


def test_each_age_group_has_its_own_table_and_unknown_age_is_reported(con):
    s = stratified_metrics(con, WARFARIN, "Haemorrhage", AGE_STRATA)
    assert s["Adult (18-64)"]["a"] == 1 and s["Geriatric (>=65)"]["a"] == 1
    assert s["Pediatric (<18)"]["a"] == 0
    assert s["Pediatric (<18)"]["defined"] is False           # no event reports in that stratum
    # lisinopril + cough is case 1005, whose age (150 years) was out of range
    assert unclassified_cases(con, LISINOPRIL, "Cough", "age_years IS NULL") == 1
    assert unclassified_cases(con, WARFARIN, "Haemorrhage", "age_years IS NULL") == 0


def test_reports_by_country(con):
    assert reports_by_country(con, WARFARIN, "Haemorrhage") == [("US", 2)]
    assert reports_by_country(con, LISINOPRIL, "Cough") == [("DE", 1)]


# ── endpoints, on data built so the right answer is known ─────────────────────
def _stratified_db(tmp_path, cells, name="strata.duckdb"):
    """cells: {'F': (a, b, c, d), 'M': (a, b, c, d)} for drug 'drugx' (rxcui 9001) and event 'E'."""
    db = str(tmp_path / name)
    c = duckdb.connect(db)
    c.execute("CREATE TABLE faers_demo (primaryid VARCHAR, sex VARCHAR, age_years DOUBLE, occr_country VARCHAR, event_dt VARCHAR)")
    c.execute("CREATE TABLE faers_reac (primaryid VARCHAR, pt VARCHAR)")
    c.execute("CREATE TABLE case_drug (primaryid VARCHAR, ingredient_rxcui VARCHAR, suspect BOOLEAN)")
    c.execute("CREATE TABLE drug_identity (raw_name VARCHAR, part VARCHAR, rxcui VARCHAR, ingredient_rxcui VARCHAR, "
              "ingredient_name VARCHAR, match_type VARCHAR, n_reports BIGINT)")
    c.execute("CREATE TABLE pt_dictionary (pt VARCHAR, pt_lower VARCHAR, n_reports BIGINT)")
    c.execute("INSERT INTO drug_identity VALUES ('drugx', 'drugx', '9001', '9001', 'drugx', 'exact', 1)")
    c.execute("INSERT INTO pt_dictionary VALUES ('E', 'e', 1)")
    n = 0
    for sex, (a, b, cc, d) in cells.items():
        for exposed, event, count in ((1, 1, a), (1, 0, b), (0, 1, cc), (0, 0, d)):
            for _ in range(count):
                n += 1
                pid = f"c{n}"
                c.execute("INSERT INTO faers_demo VALUES (?, ?, 40.0, 'US', '')", [pid, sex])
                if event:
                    c.execute("INSERT INTO faers_reac VALUES (?, 'E')", [pid])
                if exposed:
                    c.execute("INSERT INTO case_drug VALUES (?, '9001', TRUE)", [pid])
    c.close()
    return db


@pytest.fixture
def api(monkeypatch):
    def use(db):
        monkeypatch.setattr(analytics, "DB_PATH", db)
        monkeypatch.setattr(signals_service, "_IDENTITY", IdentityService(db_file=db, synonyms={}))
        monkeypatch.setattr(demographics_service, "_version", lambda: "test")
        monkeypatch.setattr(temporal_service, "_version", lambda: "test")
        monkeypatch.setattr(demographics_service, "_cache", _NoCache())
        monkeypatch.setattr(temporal_service, "_cache", _NoCache())
        monkeypatch.setattr(requests.sessions.Session, "request",
                            lambda self, m, u, *a, **k: _empty_response())
        app.config["TESTING"] = True
        return app.test_client()
    return use


class _NoCache:
    def get(self, *a, **k):
        return None

    def set(self, *a, **k):
        return None


def _empty_response():
    r = requests.Response()
    r.status_code, r._content = 200, b"{}"
    r.headers["Content-Type"] = "application/json"
    return r


def test_sex_strata_use_each_sexs_own_prr_and_overlapping_intervals_show_no_difference(tmp_path, api):
    # F: PRR 9.0 [4.7-17.1]   M: PRR 3.0 [1.3-6.8]   -> intervals overlap
    client = api(_stratified_db(tmp_path, {"F": (30, 20, 10, 140), "M": (10, 40, 10, 140)}))
    body = client.get("/api/demographics/sex?drug=drugx&event=E").get_json()
    assert body["female_prr"] == pytest.approx(9.0, abs=0.01)
    assert body["male_prr"] == pytest.approx(3.0, abs=0.01)
    assert body["higher_risk_group"] == "No difference shown"
    assert body["female_count"] == 30 and body["male_count"] == 10 and body["unknown_count"] == 0
    assert body["strata"]["F"]["prr_ci_low"] < body["strata"]["M"]["prr_ci_high"]


def test_a_group_is_named_only_when_its_interval_is_entirely_above(tmp_path, api):
    # F: PRR 24 [13-45]   M: PRR 2 [1.1-3.5]  -> clearly higher in women
    client = api(_stratified_db(tmp_path, {"F": (60, 40, 10, 390), "M": (5, 95, 10, 390)}))
    body = client.get("/api/demographics/sex?drug=drugx&event=E").get_json()
    assert body["higher_risk_group"] == "Female"
    assert body["sex_risk_ratio"] == pytest.approx(body["female_prr"] / body["male_prr"], abs=0.01)


def test_no_number_is_a_constant_times_a_share(tmp_path, api):
    """The old code returned share x 3.2 as 'female PRR'. The same shares must not give those values."""
    client = api(_stratified_db(tmp_path, {"F": (30, 20, 10, 140), "M": (10, 40, 10, 140)}))
    body = client.get("/api/demographics/sex?drug=drugx&event=E").get_json()
    share_f = 30 / 40
    assert body["female_prr"] != pytest.approx(share_f * 3.2, abs=0.01)


def test_age_strata_and_unknown_age(tmp_path, api):
    client = api(_stratified_db(tmp_path, {"F": (30, 20, 10, 140), "M": (10, 40, 10, 140)}))
    body = client.get("/api/demographics/age?drug=drugx&event=E").get_json()
    assert body["groups"] == ["Pediatric (<18)", "Adult (18-64)", "Geriatric (>=65)"]
    assert body["counts"] == [0, 40, 0]                       # everyone in the fixture is 40 years old
    assert body["prr_values"][0] is None and body["prr_values"][2] is None
    assert body["highest_risk_group"] is None                 # one group: nothing to compare against
    assert body["unknown_age_count"] == 0


def test_country_share_is_labelled_as_reporting_volume(tmp_path, api):
    client = api(_stratified_db(tmp_path, {"F": (30, 20, 10, 140), "M": (10, 40, 10, 140)}))
    body = client.get("/api/demographics/geo?drug=drugx&event=E").get_json()
    assert body["countries"] == ["US"] and body["share_pct"] == [100.0]
    assert "not a difference in risk" in body["note"]


def test_too_few_reports_to_stratify_returns_no_data(tmp_path, api):
    client = api(_stratified_db(tmp_path, {"F": (3, 20, 10, 140), "M": (2, 40, 10, 140)}))
    body = client.get("/api/demographics/sex?drug=drugx&event=E").get_json()
    assert body["source"] == "no_data"


def test_unresolved_drug_is_not_stratified(tmp_path, api):
    client = api(_stratified_db(tmp_path, {"F": (30, 20, 10, 140), "M": (10, 40, 10, 140)}))
    body = client.get("/api/demographics/sex?drug=nosuchdrug&event=E").get_json()
    assert body["source"] == "no_data" and body["unresolved"] == ["drug"]


def test_onset_endpoint_reports_how_few_reports_it_rests_on(fixture_db, api):
    db = fixture_db
    client = api(db)
    body = client.get("/api/temporal/tto?drug=warfarin&event=Haemorrhage").get_json()
    assert body["source"] == "no_data"
    assert body["cases"] == 2 and body["usable_cases"] == 1
    assert "at least 10" in body["message"]
    assert body["median_days"] is None


# ── cache: nothing computed by other data or older code is served ─────────────
def test_cache_entries_are_only_served_for_the_same_version(monkeypatch):
    store = {}

    def fake_execute(sql, params):
        drug, event, kind, payload = params
        store[(drug, event, kind)] = payload

    def fake_query_one(sql, params):
        payload = store.get(tuple(params))
        if payload is None:
            return None
        from datetime import datetime
        return {"payload": payload, "cached_at": datetime.now()}

    monkeypatch.setattr("core.cache.execute", fake_execute)
    monkeypatch.setattr("core.cache.query_one", fake_query_one)
    cache = PayloadCache("t", "kind", "T")

    cache.set("D", "E", "sex", {"x": 1}, version="v1")
    assert cache.get("D", "E", "sex", version="v1") == {"x": 1}          # same version: hit, marker stripped
    assert cache.get("D", "E", "sex", version="v2") is None              # other data or algorithm: miss

    cache.set("D", "E", "tto", {"x": 2})                                 # an entry written without a version
    assert cache.get("D", "E", "tto", version="v1") is None              # is never served to a versioned reader
    assert cache.get("D", "E", "tto") == {"x": 2}


# ── timing is judged only inside the window the loaded data covers ────────────
def _timing_db(tmp_path, rows, name="timing.duckdb"):
    """rows: [(primaryid, fda_dt, event_dt)] all with drug 'drugx' (9001) and event 'E'."""
    db = str(tmp_path / name)
    c = duckdb.connect(db)
    c.execute("CREATE TABLE faers_demo (primaryid VARCHAR, sex VARCHAR, age_years DOUBLE, "
              "occr_country VARCHAR, event_dt VARCHAR, fda_dt VARCHAR)")
    c.execute("CREATE TABLE faers_reac (primaryid VARCHAR, pt VARCHAR)")
    c.execute("CREATE TABLE case_drug (primaryid VARCHAR, ingredient_rxcui VARCHAR, suspect BOOLEAN)")
    c.execute("CREATE TABLE drug_identity (raw_name VARCHAR, part VARCHAR, rxcui VARCHAR, ingredient_rxcui VARCHAR, "
              "ingredient_name VARCHAR, match_type VARCHAR, n_reports BIGINT)")
    c.execute("CREATE TABLE pt_dictionary (pt VARCHAR, pt_lower VARCHAR, n_reports BIGINT)")
    c.execute("INSERT INTO drug_identity VALUES ('drugx', 'drugx', '9001', '9001', 'drugx', 'exact', 1)")
    c.execute("INSERT INTO pt_dictionary VALUES ('E', 'e', 1)")
    for pid, fda, event in rows:
        c.execute("INSERT INTO faers_demo VALUES (?, 'F', 40.0, 'US', ?, ?)", [pid, event, fda])
        c.execute("INSERT INTO faers_reac VALUES (?, 'E')", [pid])
        c.execute("INSERT INTO case_drug VALUES (?, '9001', TRUE)", [pid])
    c.close()
    return db


def test_a_year_counts_only_when_the_loaded_window_covers_it_with_reporting_lag(tmp_path):
    from faers.stats import complete_event_years
    five_years = [("a", "20210701", "20210601"), ("b", "20260630", "20260601")]
    c = duckdb.connect(_timing_db(tmp_path, five_years), read_only=True)
    assert complete_event_years(c) == [2022, 2023, 2024, 2025]
    c.close()
    one_quarter = [("a", "20260401", "20260301"), ("b", "20260630", "20260601")]
    c = duckdb.connect(_timing_db(tmp_path, one_quarter, "q.duckdb"), read_only=True)
    assert complete_event_years(c) == []
    c.close()


def test_event_months_use_only_complete_years(tmp_path):
    from faers.stats import event_month_counts
    rows = [("a", "20210701", "20210801"),       # 2021: before the first full year, excluded
            ("b", "20220301", "20220215"),       # 2022 Feb
            ("c", "20230301", "20230215"),       # 2023 Feb
            ("d", "20240901", "20240701"),       # 2024 Jul
            ("e", "20260630", "20260601")]       # 2026: not complete, excluded
    c = duckdb.connect(_timing_db(tmp_path, rows), read_only=True)
    counts = event_month_counts(c, ["9001"], "E", [2022, 2023, 2024, 2025])
    c.close()
    assert counts == [0, 2, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0]


def test_receipt_velocity_fills_empty_months_with_zero(tmp_path):
    from faers.stats import receipt_velocity
    rows = [("a", "20240110", ""), ("b", "20240120", ""), ("c", "20240415", "")]
    c = duckdb.connect(_timing_db(tmp_path, rows), read_only=True)
    r = receipt_velocity(c, ["9001"], "E")
    c.close()
    assert r["months"] == ["202401", "202402", "202403", "202404"]
    assert r["counts"] == [2, 0, 0, 1] and r["span_months"] == 4


def test_one_quarter_of_data_makes_no_seasonality_or_spike_claim(tmp_path, api):
    rows = [(f"p{i}", f"2026{4 + i % 3:02d}15", f"2026{1 + i % 6:02d}10") for i in range(150)]
    client = api(_timing_db(tmp_path, rows))
    season = client.get("/api/temporal/seasonality?drug=drugx&event=E").get_json()
    assert season["source"] == "no_data" and season["is_seasonal"] is False
    assert season["years_used"] == [] and "complete calendar" in season["message"]
    velocity = client.get("/api/temporal/velocity?drug=drugx&event=E").get_json()
    assert velocity["source"] == "no_data" and velocity["has_spike"] is False
    assert "at least 12" in velocity["message"]


def test_spike_in_a_long_window_is_found_on_the_receipt_month(tmp_path, api):
    rows, n = [], 0
    for year in (2022, 2023, 2024):
        for month in range(1, 13):
            k = 30 if (year, month) == (2023, 6) else 3
            for _ in range(k):
                n += 1
                rows.append((f"p{n}", f"{year}{month:02d}15", f"{year}{month:02d}01"))
    client = api(_timing_db(tmp_path, rows))
    v = client.get("/api/temporal/velocity?drug=drugx&event=E").get_json()
    assert v["source"] == "faers_local" and v["span_months"] == 36
    assert v["has_spike"] is True
    assert v["months"][v["spikes"].index(True)] == "202306"


# ── dates recorded as 1 January because only the year was known ──────────────
def test_a_year_only_default_date_is_incomplete_for_onset_and_seasonality(tmp_path):
    """FAERS writes a year-only date as YYYY0101 (19% of event dates in the real data)."""
    from faers.stats import event_month_counts
    db = _onset_db(tmp_path, [
        ("a", "20240120", ["20240110"]),               # 10 days, genuine
        ("b", "20240101", ["20231201"]),               # event dated 1 Jan: year-only, excluded
        ("c", "20240220", ["20240101"]),               # therapy start dated 1 Jan: excluded
    ])
    c = duckdb.connect(db, read_only=True)
    r = time_to_onset(c, "1", "E", min_cases=1)
    assert r["cases"] == 3 and r["usable_cases"] == 1
    assert r["excluded"]["no_complete_event_date"] == 1 and r["excluded"]["no_complete_therapy_start"] == 1
    counts = event_month_counts(c, ["1"], "E", [2024])
    c.close()
    assert counts[0] == 1 and counts[1] == 1 and sum(counts) == 2    # the 1 Jan event is not in January

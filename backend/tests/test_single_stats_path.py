"""
One source of 2x2 counts: the same drug-event pair must give the same a, b, c, d from every
endpoint that reports them, drugs must match by ingredient (not by spelling), concomitant
drugs must not count as exposure, and a cached signal must not outlive the data it came from.
"""

import os
import sys

import duckdb
import pytest
import requests

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for p in (BASE_DIR, os.path.join(os.path.dirname(__file__), "fixtures", "faers")):
    if p not in sys.path:
        sys.path.insert(0, p)

import make_fixture  # noqa: E402
from app import app  # noqa: E402
from clients.rxnav import RxNavClient  # noqa: E402
from faers import analytics, identity_build, loader, parser, registry  # noqa: E402
from faers.stats import contingency  # noqa: E402
from services import signals as signals_service  # noqa: E402
from services.identity import IdentityService, load_synonyms  # noqa: E402

# fixture drug name -> (rxcui, ingredient name)
INGREDIENTS = {
    "warfarin": ("11289", "warfarin"),
    "aspirin": ("1191", "aspirin"),
    "amoxicillin": ("723", "amoxicillin"),
    "metformin hydrochloride": ("6809", "metformin"),
    "ibuprofen": ("5640", "ibuprofen"),
    "lisinopril": ("29046", "lisinopril"),
    "sertraline": ("36437", "sertraline"),
}
WARFARIN, METFORMIN, LISINOPRIL = "11289", "6809", "29046"


class _Resp:
    def __init__(self, payload, status=200):
        self.status_code, self._p = status, payload

    def json(self):
        return self._p

    def raise_for_status(self):
        pass


class FakeRxNav:
    def get(self, url, params=None, headers=None, timeout=None):
        path, params = url.split("/REST", 1)[1], params or {}
        if path == "/rxcui.json":
            hit = INGREDIENTS.get(params["name"].lower())
            return _Resp({"idGroup": {"rxnormId": [hit[0]]}} if hit else {"idGroup": {}})
        if path.endswith("/related.json"):
            rxcui = path.split("/")[2]
            ing = [{"rxcui": r, "name": n} for r, n in INGREDIENTS.values() if r == rxcui]
            return _Resp({"relatedGroup": {"conceptGroup": [{"tty": "IN", "conceptProperties": ing}]}})
        return _Resp({})


def _build(root, with_identity=True):
    z1, z2 = make_fixture.two_quarter_fixture(str(root / "raw"))
    out = str(root / "parquet")
    for z in (z1, z2):
        parser.parse_quarter(z, out, allow_fixture=True, log=lambda *_: None)
    db = str(root / ("identity.duckdb" if with_identity else "legacy.duckdb"))
    loader.load_database(out, db, log=lambda *_: None)
    if with_identity:
        con = duckdb.connect(db)
        identity_build.build_pt_dictionary(con)
        identity_build.build_drug_identity(
            con, RxNavClient(session=FakeRxNav(), min_interval=0, sleep=lambda s: None),
            min_reports=1, log=lambda *_: None)
        identity_build.build_case_drug(con)
        con.close()
    return db


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("stats"))


@pytest.fixture(scope="module")
def legacy_db(tmp_path_factory):
    return _build(tmp_path_factory.mktemp("legacy"), with_identity=False)


@pytest.fixture
def client(monkeypatch, db):
    """Flask client wired to the fixture database, with MySQL caching and the network stubbed."""
    saved = []
    monkeypatch.setattr(analytics, "DB_PATH", db)
    monkeypatch.setattr(signals_service, "_IDENTITY", IdentityService(db_file=db, synonyms=load_synonyms()))
    monkeypatch.setattr(signals_service, "WATCHLIST", [("Warfarin", "Haemorrhage")])
    monkeypatch.setattr(signals_service, "get_cached_signals", lambda n=100: [])
    monkeypatch.setattr(signals_service, "upsert_signal", lambda *a, **k: saved.append((a, k)))
    monkeypatch.setattr(signals_service, "_data_version", lambda: registry.data_version(db))
    monkeypatch.setattr(requests.sessions.Session, "request",
                        lambda self, method, url, *a, **k: _stub_response())
    app.config["TESTING"] = True
    c = app.test_client()
    c.saved = saved
    return c


def _stub_response():
    r = requests.Response()
    r.status_code, r._content = 200, b"{}"
    r.headers["Content-Type"] = "application/json"
    return r


def counts(body):
    return {k: body[k] for k in "abcd"}


# ── the contingency function itself ───────────────────────────────────────────
def test_contingency_counts_cases_not_reports(db):
    con = duckdb.connect(db, read_only=True)
    try:
        # six cases kept; warfarin is suspect in two (1001 v2 and 1006); both have Haemorrhage
        assert contingency(con, WARFARIN, "Haemorrhage") == {"a": 2, "b": 0, "c": 0, "d": 4, "n": 6}
        assert contingency(con, METFORMIN, "Lactic acidosis") == {"a": 1, "b": 0, "c": 0, "d": 5, "n": 6}
        # an event that never occurs with the drug
        assert contingency(con, WARFARIN, "Nausea") == {"a": 0, "b": 2, "c": 1, "d": 3, "n": 6}
    finally:
        con.close()


def test_event_match_ignores_case(db):
    con = duckdb.connect(db, read_only=True)
    try:
        assert contingency(con, WARFARIN, "HAEMORRHAGE")["a"] == contingency(con, WARFARIN, "haemorrhage")["a"] == 2
    finally:
        con.close()


def test_a_stratum_restricts_every_cell(db):
    con = duckdb.connect(db, read_only=True)
    try:
        male = contingency(con, WARFARIN, "Haemorrhage", where=("sex = ?", ["M"]))
        assert male == {"a": 1, "b": 0, "c": 0, "d": 2, "n": 3}      # only case 1006 is male
    finally:
        con.close()


def test_the_old_version_of_a_case_does_not_count(db):
    """Case 1001 v1 listed aspirin; v2 (the surviving version) does not."""
    con = duckdb.connect(db, read_only=True)
    try:
        assert contingency(con, "1191", "Nausea")["b"] == 0 and contingency(con, "1191", "Nausea")["a"] == 0
    finally:
        con.close()


def test_a_concomitant_drug_is_not_exposure(tmp_path):
    path = str(tmp_path / "t.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE TABLE faers_demo (primaryid VARCHAR, sex VARCHAR)")
    con.execute("CREATE TABLE faers_reac (primaryid VARCHAR, pt VARCHAR)")
    con.execute("CREATE TABLE case_drug (primaryid VARCHAR, ingredient_rxcui VARCHAR, suspect BOOLEAN)")
    con.executemany("INSERT INTO faers_demo VALUES (?, 'F')", [("1",), ("2",), ("3",)])
    con.executemany("INSERT INTO faers_reac VALUES (?, 'Rash')", [("1",), ("2",)])
    con.executemany("INSERT INTO case_drug VALUES (?, 'X', ?)", [("1", False), ("2", True)])   # 1 = concomitant
    assert contingency(con, "X", "Rash") == {"a": 1, "b": 0, "c": 1, "d": 1, "n": 3}
    con.close()


def test_a_combination_must_have_every_ingredient_suspect(tmp_path):
    con = duckdb.connect(str(tmp_path / "t.duckdb"))
    con.execute("CREATE TABLE faers_demo (primaryid VARCHAR)")
    con.execute("CREATE TABLE faers_reac (primaryid VARCHAR, pt VARCHAR)")
    con.execute("CREATE TABLE case_drug (primaryid VARCHAR, ingredient_rxcui VARCHAR, suspect BOOLEAN)")
    con.executemany("INSERT INTO faers_demo VALUES (?)", [("1",), ("2",), ("3",)])
    con.execute("INSERT INTO faers_reac VALUES ('1', 'Rash')")
    con.executemany("INSERT INTO case_drug VALUES (?, ?, TRUE)", [("1", "A"), ("1", "B"), ("2", "A"), ("3", "B")])
    assert contingency(con, ["A", "B"], "Rash") == {"a": 1, "b": 0, "c": 0, "d": 2, "n": 3}
    assert contingency(con, "A", "Rash")["b"] == 1          # A alone: cases 1 and 2
    con.close()


# ── every endpoint agrees ─────────────────────────────────────────────────────
EXPECTED = {"a": 2, "b": 0, "c": 0, "d": 4}


def test_all_endpoints_report_identical_counts(client):
    q = "drug=warfarin&event=Haemorrhage"
    prr = client.get(f"/api/prr?{q}").get_json()
    analytics_prr = client.get(f"/api/analytics/prr?{q}").get_json()
    trials = client.get(f"/api/prr-trials?{q}").get_json()
    sig = next(s for s in client.get("/api/signals").get_json()["signals"]
               if s["drug"].lower() == "warfarin" and s["event"].lower() == "haemorrhage")

    assert counts(prr) == counts(analytics_prr) == counts(trials) == counts(sig) == EXPECTED


def test_brand_salt_and_synonym_inputs_reach_the_same_counts(client, monkeypatch):
    # a brand name is not in the loaded data's identity table, so resolve it through RxNav
    monkeypatch.setattr(signals_service, "_IDENTITY",
                        IdentityService(db_file=analytics.DB_PATH, rxnav=RxNavClient(
                            session=FakeRxNav(), min_interval=0, sleep=lambda s: None),
                            synonyms=load_synonyms()))
    for drug, event in [("Warfarin", "haemorrhage"), ("WARFARIN", "Bleeding"), ("warfarin", "hemorrhage")]:
        body = client.get(f"/api/prr?drug={drug}&event={event}").get_json()
        assert counts(body) == EXPECTED, (drug, event)
    assert body["identity"]["event"]["match"] == "synonym"


def test_an_unresolved_drug_is_reported_not_matched_loosely(client):
    body = client.get("/api/prr?drug=Coumadin&event=Haemorrhage").get_json()      # not in loaded data, no RxNav
    assert counts(body) == {"a": 0, "b": 0, "c": 0, "d": 0}
    assert body["is_signal"] is False


def test_unresolved_event_is_reported_with_candidates(client):
    body = client.get("/api/analytics/prr?drug=warfarin&event=Haemorrhage%20severe").get_json()
    assert body["unresolved"] == ["event"]
    assert body["identity"]["event"]["resolved"] is False


def test_identity_provenance_is_returned(client):
    body = client.get("/api/analytics/prr?drug=warfarin&event=Haemorrhage").get_json()
    assert body["identity"]["drug"]["ingredients"] == [{"rxcui": WARFARIN, "name": "warfarin"}]
    assert body["identity"]["event"]["pt"] == "Haemorrhage"
    assert body["source"] == "faers_local"


# ── older databases and the fallback ──────────────────────────────────────────
def test_database_without_identity_tables_still_answers(monkeypatch, legacy_db):
    monkeypatch.setattr(analytics, "DB_PATH", legacy_db)
    assert analytics.identity_ready() is False
    res = signals_service.compute_disproportionality_helper("warfarin", "Haemorrhage")
    assert {k: res[k] for k in "abcd"} == EXPECTED         # name matching, brand map: COUMADIN + WARFARIN
    assert "identity" not in res


def test_openfda_fallback_is_labelled_and_restricted_to_suspect_drugs(monkeypatch):
    seen = []

    def fake_request(self, method, url, params=None, **kw):
        seen.append((params or {}).get("search", ""))
        r = requests.Response()
        r.status_code, r._content = 200, b'{"meta": {"results": {"total": 100}}}'
        return r

    monkeypatch.setattr(requests.sessions.Session, "request", fake_request)
    res = signals_service.compute_disproportionality_helper("Warfarin", "Haemorrhage", source="openfda")
    assert res["source"] == "openfda" and res["fallback"] is True and "approximate" in res["note"]
    drug_queries = [s for s in seen if "medicinalproduct" in s]
    assert drug_queries and all("drugcharacterization:1" in s for s in drug_queries)


def test_no_local_database_falls_back_to_openfda(monkeypatch, tmp_path):
    monkeypatch.setattr(analytics, "DB_PATH", str(tmp_path / "absent.duckdb"))
    monkeypatch.setattr(requests.sessions.Session, "request",
                        lambda self, method, url, *a, **k: _totals_response())
    res = signals_service.compute_disproportionality_helper("Warfarin", "Haemorrhage")
    assert res["source"] == "openfda" and res["fallback"] is True


def _totals_response():
    r = requests.Response()
    r.status_code, r._content = 200, b'{"meta": {"results": {"total": 50}}}'
    return r


# ── cache versioning ──────────────────────────────────────────────────────────
def test_a_cached_signal_is_reused_only_for_the_data_it_was_computed_from(monkeypatch, db):
    current = registry.data_version(db)
    cached = [
        {"drug": "warfarin", "event": "haemorrhage", "a": 99, "data_version": current, "source": "faers_local"},
        {"drug": "metformin", "event": "lactic acidosis", "a": 99, "data_version": "FAERS 2020Q1-2020Q4", "source": "faers_local"},
        {"drug": "ibuprofen", "event": "nausea", "a": 99, "data_version": None, "source": "faers_local"},
    ]
    written = []
    monkeypatch.setattr(analytics, "DB_PATH", db)
    monkeypatch.setattr(signals_service, "_IDENTITY", IdentityService(db_file=db, synonyms=load_synonyms()))
    monkeypatch.setattr(signals_service, "WATCHLIST",
                        [("Warfarin", "Haemorrhage"), ("Metformin", "Lactic acidosis"), ("Ibuprofen", "Nausea")])
    monkeypatch.setattr(signals_service, "get_cached_signals", lambda n=100: cached)
    monkeypatch.setattr(signals_service, "upsert_signal", lambda *a, **k: written.append((a[0], a[1], k)))
    monkeypatch.setattr(signals_service, "_data_version", lambda: current)

    body, _ = signals_service.get_signals(source=None, refresh=False)
    by_drug = {s["drug"].lower(): s for s in body["signals"] if s["drug"].lower() in ("warfarin", "metformin", "ibuprofen")}

    assert by_drug["warfarin"]["a"] == 99                    # current version: served from cache
    assert by_drug["metformin"]["a"] == 1                    # stale version: recomputed from the data
    assert by_drug["ibuprofen"]["a"] == 0                    # unversioned: recomputed, not trusted
    assert [w[0] for w in written] == ["Metformin", "Ibuprofen"]
    assert all(w[2]["data_version"] == current for w in written)


def test_a_resolved_drug_with_no_indexed_reports_is_not_reported_as_zero(client, monkeypatch):
    """Ibuprofen resolves (RxNav knows it) but its only case was deleted, so nothing is indexed."""
    monkeypatch.setattr(signals_service, "_IDENTITY",
                        IdentityService(db_file=analytics.DB_PATH, synonyms=load_synonyms(),
                                        rxnav=RxNavClient(session=FakeRxNav(), min_interval=0, sleep=lambda s: None)))
    body = client.get("/api/analytics/prr?drug=ibuprofen&event=Nausea").get_json()
    assert body["unresolved"] == ["drug_not_indexed"]
    assert body["identity"]["drug"]["resolved"] is True and body["identity"]["drug"]["indexed"] is False
    assert "not a count of zero" in body["note"]


def test_an_indexed_drug_with_genuinely_zero_event_reports_is_a_real_zero(client):
    body = client.get("/api/analytics/prr?drug=warfarin&event=Cough").get_json()
    assert "unresolved" not in body
    assert body["a"] == 0 and body["b"] == 2                 # warfarin is indexed: 2 exposed cases
    assert body["identity"]["drug"]["indexed"] is True

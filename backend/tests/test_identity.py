"""
Drug and event identity: RxNav client (cached, never guesses), identity build, the identity
service, and the checks that the watchlist and synonym table use real Preferred Terms.
"""

import json
import os
import sys

import duckdb
import pytest
import requests

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from clients.rxnav import RxNavClient, RxNavError  # noqa: E402
from core.config import WATCHLIST  # noqa: E402
from faers import identity_build  # noqa: E402
from faers.registry import database_path  # noqa: E402
from services.identity import IdentityService, load_synonyms  # noqa: E402

# name -> (rxcui, tty, ingredients)
CONCEPTS = {
    "warfarin": ("11289", "IN", [("11289", "warfarin")]),
    "warfarin sodium": ("114194", "PIN", [("11289", "warfarin")]),
    "coumadin": ("202421", "BN", [("11289", "warfarin")]),
    "metformin hydrochloride": ("861004", "SCD", [("6809", "metformin")]),
    "lamivudine": ("68735", "IN", [("68735", "lamivudine")]),
    "zidovudine": ("10109", "IN", [("10109", "zidovudine")]),
    "sulfamethoxazole": ("10180", "IN", [("10180", "sulfamethoxazole")]),
    "sulfamethazine": ("10178", "IN", [("10178", "sulfamethazine")]),
}
# RxNav's normalised search (search=1) also returns similarly named concepts, ahead of the right one.
NEIGHBOURS = {"sulfamethoxazole": ["10178"]}
PROPS = {c[0]: {"rxcui": c[0], "name": n, "tty": c[1]} for n, c in CONCEPTS.items()}


class FakeRxNav:
    """Stands in for requests: answers the four RxNav endpoints the client uses."""

    def __init__(self, fail_first=0, status=503):
        self.calls = []
        self.fail_first = fail_first
        self.status = status

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        if self.fail_first > 0:
            self.fail_first -= 1
            return _Resp(self.status, {})
        path = url.split("/REST", 1)[1]
        params = params or {}
        if path == "/rxcui.json":
            name = params["name"].lower()
            if name not in CONCEPTS:
                return _Resp(200, {"idGroup": {}})
            ids = [CONCEPTS[name][0]]
            if str(params.get("search")) == "1":
                ids = NEIGHBOURS.get(name, []) + ids
            return _Resp(200, {"idGroup": {"rxnormId": ids}})
        if path == "/approximateTerm.json":
            if params["term"].lower() == "metfromin":              # the real, dangerous case
                return _Resp(200, {"approximateGroup": {"candidate": [
                    {"rxcui": "6762", "name": "merbromin", "score": "9.2", "rank": "1"}]}})
            return _Resp(200, {"approximateGroup": {"candidate": []}})
        if path.endswith("/properties.json"):
            rxcui = path.split("/")[2]
            return _Resp(200, {"properties": PROPS.get(rxcui) or {"rxcui": rxcui, "name": "merbromin", "tty": "IN"}})
        if path.endswith("/related.json"):
            rxcui = path.split("/")[2]
            ing = next((c[2] for c in CONCEPTS.values() if c[0] == rxcui), [])
            groups = [{"tty": "IN", "conceptProperties": [{"rxcui": r, "name": n} for r, n in ing]}] if ing else [{"tty": "IN"}]
            return _Resp(200, {"relatedGroup": {"conceptGroup": groups}})
        return _Resp(404, {})


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


def client(tmp_path=None, **kw):
    return RxNavClient(cache_path=str(tmp_path / "cache.jsonl") if tmp_path else None,
                       session=FakeRxNav(**kw), min_interval=0, sleep=lambda s: None)


# ── RxNav client ──────────────────────────────────────────────────────────────
def test_generic_salt_and_brand_resolve_to_the_same_ingredient():
    c = client()
    ids = {name: [i["rxcui"] for i in c.resolve(name)["ingredients"]]
           for name in ("warfarin", "warfarin sodium", "Coumadin")}
    assert ids == {"warfarin": ["11289"], "warfarin sodium": ["11289"], "Coumadin": ["11289"]}
    assert c.resolve("coumadin")["match"] == "exact"


def test_a_fuzzy_match_is_only_a_suggestion_never_a_resolution():
    """'metfromin' scores 9.2 against merbromin (an antiseptic): it must not be accepted."""
    result = client().resolve("metfromin")
    assert result["ingredients"] == [] and result["match"] is None
    assert result["suggestion"]["name"] == "merbromin"


def test_a_similar_name_returned_first_by_the_normalised_search_is_not_taken():
    """RxNav's search=1 lists sulfamethazine ahead of sulfamethoxazole; the exact name must win."""
    r = client().resolve("sulfamethoxazole")
    assert [i["name"] for i in r["ingredients"]] == ["sulfamethoxazole"] and r["rxcui"] == "10180"


def test_exact_search_is_used_first_and_normalised_search_only_as_a_fallback():
    c = client()
    c.resolve("warfarin")
    searches = [p.get("search") for u, p in c.session.calls if u.endswith("/rxcui.json")]
    assert searches == [0]


def test_a_normalised_match_naming_several_concepts_is_not_resolved():
    class Ambiguous(FakeRxNav):
        def get(self, url, params=None, headers=None, timeout=None):
            if url.endswith("/rxcui.json"):
                self.calls.append((url, dict(params or {})))
                ids = [] if str(params.get("search")) == "0" else ["10178", "10180"]
                return _Resp(200, {"idGroup": {"rxnormId": ids} if ids else {}})
            return super().get(url, params, headers, timeout)

    c = RxNavClient(session=Ambiguous(), min_interval=0, sleep=lambda s: None)
    r = c.resolve("sulfa-thing")
    assert r["match"] is None and r["ingredients"] == []


def test_unknown_name_is_unresolved():
    result = client().resolve("xyzzy123")
    assert result["match"] is None and result["ingredients"] == [] and result["suggestion"] is None


def test_empty_input_makes_no_request():
    c = client()
    assert c.resolve("   ")["match"] is None
    assert c.session.calls == []


def test_responses_are_cached_in_memory_and_on_disk(tmp_path):
    c = client(tmp_path)
    c.resolve("warfarin")
    first = c.live_calls
    c.resolve("warfarin")
    assert c.live_calls == first                       # served from memory

    reopened = client(tmp_path)                        # new process, same cache file
    reopened.resolve("warfarin")
    assert reopened.live_calls == 0
    assert reopened.session.calls == []


def test_a_torn_cache_line_is_ignored(tmp_path):
    path = tmp_path / "cache.jsonl"
    path.write_text(json.dumps({"k": "/rxcui.json?name=x&search=1", "v": {}}) + "\n{\"k\": \"broken", encoding="utf-8")
    assert RxNavClient(cache_path=str(path), session=FakeRxNav(), sleep=lambda s: None)._cache


def test_transient_server_errors_are_retried():
    c = client(fail_first=2)
    assert c.resolve("warfarin")["ingredients"]


def test_persistent_failure_raises_instead_of_returning_a_guess():
    c = client(fail_first=99)
    with pytest.raises(RxNavError):
        c.resolve("warfarin")


# ── identity build ────────────────────────────────────────────────────────────
@pytest.fixture
def faers_db(tmp_path):
    path = str(tmp_path / "faers.duckdb")
    con = duckdb.connect(path)
    con.execute("CREATE TABLE faers_drug (primaryid VARCHAR, caseid VARCHAR, drug_seq VARCHAR, role_cod VARCHAR, "
                "drugname VARCHAR, prod_ai VARCHAR, quarter VARCHAR)")
    con.execute("CREATE TABLE faers_reac (primaryid VARCHAR, pt VARCHAR, quarter VARCHAR)")
    drugs = []
    for i in range(12):
        drugs.append((f"A{i}", "c", "1", "PS", "COUMADIN", "WARFARIN SODIUM", "q"))        # 12 reports
    for i in range(11):
        drugs.append((f"B{i}", "c", "1", "PS", "COMBIVIR", "LAMIVUDINE\\ZIDOVUDINE", "q"))  # combination
    for i in range(10):
        drugs.append((f"C{i}", "c", "1", "PS", "MYSTERY", "XYZZY123", "q"))               # unresolvable
    for i in range(3):
        drugs.append((f"D{i}", "c", "1", "PS", "RARE", "WARFARIN", "q"))                   # below threshold
    con.executemany("INSERT INTO faers_drug VALUES (?,?,?,?,?,?,?)", drugs)
    con.executemany("INSERT INTO faers_reac VALUES (?,?,?)",
                    [("A0", "Nausea", "q"), ("A1", "NAUSEA", "q"), ("B0", "Haemorrhage", "q")])
    con.close()
    return path


def test_identity_build_resolves_splits_combinations_and_reports_coverage(faers_db):
    con = duckdb.connect(faers_db)
    summary = identity_build.build_drug_identity(con, client(), min_reports=10, log=lambda *_: None)
    rows = con.execute("SELECT raw_name, part, ingredient_name FROM drug_identity ORDER BY 1, 2").fetchall()
    con.close()

    assert ("lamivudine\\zidovudine", "lamivudine", "lamivudine") in rows
    assert ("lamivudine\\zidovudine", "zidovudine", "zidovudine") in rows
    assert ("warfarin sodium", "warfarin sodium", "warfarin") in rows
    assert not any(r[0] == "warfarin" for r in rows), "names below the report threshold are not resolved"
    assert summary["names_considered"] == 3 and summary["names_resolved"] == 2
    assert summary["coverage_pct"] == pytest.approx(100 * 23 / 33, abs=0.01)
    assert summary["top_unresolved"] == ["xyzzy123 (10)"]


def test_pt_dictionary_merges_case_variants(faers_db):
    con = duckdb.connect(faers_db)
    identity_build.build_pt_dictionary(con)
    got = dict(con.execute("SELECT pt_lower, n_reports FROM pt_dictionary").fetchall())
    con.close()
    assert got == {"nausea": 2, "haemorrhage": 1}


# ── identity service ──────────────────────────────────────────────────────────
@pytest.fixture
def service(faers_db):
    con = duckdb.connect(faers_db)
    identity_build.build_pt_dictionary(con)
    identity_build.build_drug_identity(con, client(), min_reports=10, log=lambda *_: None)
    con.close()
    return IdentityService(db_file=faers_db, rxnav=client(), synonyms=load_synonyms())


def test_drug_found_in_loaded_data_needs_no_network(service):
    out = service.resolve_drug("Warfarin Sodium")
    assert out["resolved"] and out["source"] == "loaded_data"
    assert out["ingredients"] == [{"rxcui": "11289", "name": "warfarin"}]
    assert service.rxnav.session.calls == []


def test_drug_not_in_data_falls_back_to_rxnav(service):
    out = service.resolve_drug("Coumadin")
    assert out["resolved"] and out["source"] == "rxnav"
    assert out["ingredients"][0]["name"] == "warfarin"


def test_live_lookup_can_be_switched_off(service):
    out = service.resolve_drug("Coumadin", live=False)
    assert out["resolved"] is False and service.rxnav.session.calls == []


def test_unresolved_drug_carries_a_suggestion_not_a_match(service):
    out = service.resolve_drug("metfromin")
    assert out["resolved"] is False and out["ingredients"] == []
    assert out["suggestion"]["name"] == "merbromin"


def test_event_exact_match_is_case_insensitive(service):
    out = service.resolve_event("NAUSEA")
    assert out["resolved"] and out["pt"] in ("Nausea", "NAUSEA") and out["match"] == "exact" and out["n_reports"] == 2


def test_event_synonym_resolves_only_to_a_pt_present_in_the_data(service):
    out = service.resolve_event("Bleeding")
    assert out["resolved"] and out["pt"] == "Haemorrhage" and out["match"] == "synonym"
    assert "undercounts" in out["note"]
    # 'heart attack' maps to Myocardial infarction, which this database does not contain
    assert service.resolve_event("heart attack")["resolved"] is False


def test_unknown_event_lists_candidates_without_choosing(service):
    out = service.resolve_event("haemorrhage severe")
    assert out["resolved"] is False and out["pt"] is None
    out2 = service.resolve_event("nausea vomiting")
    assert out2["resolved"] is False


def test_event_without_loaded_data_is_unresolved(tmp_path):
    svc = IdentityService(db_file=str(tmp_path / "none.duckdb"), synonyms={})
    assert svc.resolve_event("Nausea")["resolved"] is False


# ── watchlist and synonym table against the real data (skipped when it is not loaded) ──
REAL_DB = database_path()
needs_real_data = pytest.mark.skipif(
    not (os.path.exists(REAL_DB) and os.environ.get("DATA_DIR")),
    reason="needs a loaded FAERS database (set DATA_DIR)")


def _real_dictionary():
    con = duckdb.connect(REAL_DB, read_only=True)
    try:
        return {r[0] for r in con.execute("SELECT lower(pt) FROM faers_reac GROUP BY 1").fetchall()}
    finally:
        con.close()


@needs_real_data
def test_every_watchlist_event_is_a_preferred_term_in_the_data():
    pts = _real_dictionary()
    missing = [(d, e) for d, e in WATCHLIST if e.lower() not in pts]
    assert missing == []


@needs_real_data
def test_every_synonym_target_is_a_preferred_term_in_the_data():
    pts = _real_dictionary()
    missing = [t for t, v in load_synonyms().items() if v["pt"].lower() not in pts]
    assert missing == []


def test_watchlist_has_no_known_non_pt_spellings():
    """Static guard that runs everywhere: the five spellings the audit found must not return."""
    events = {e.lower() for _, e in WATCHLIST}
    assert not events & {"bleeding", "qt prolongation", "peripheral edema", "nephrotoxicity", "peripheral neuropathy"}
    assert len(WATCHLIST) == 15


def test_synonym_table_is_well_formed():
    table = load_synonyms()
    assert len(table) >= 10
    for term, row in table.items():
        assert row["pt"] and row["source"], term
        assert term == term.lower()


def test_data_version_changes_when_the_identity_build_changes(tmp_path):
    import duckdb
    from faers.registry import data_version, identity_fingerprint
    db = str(tmp_path / "v.duckdb")
    c = duckdb.connect(db)
    c.execute("CREATE TABLE faers_quarters (quarter VARCHAR, zip_file VARCHAR, zip_sha256 VARCHAR, "
              "reports_in_file BIGINT, deleted_cases BIGINT, cases_kept BIGINT, drug_rows BIGINT, "
              "reac_rows BIGINT, is_fixture BOOLEAN, loaded_at TIMESTAMP)")
    c.execute("INSERT INTO faers_quarters VALUES ('2024q1', 'z', 'h', 10, 0, 10, 1, 1, FALSE, now())")
    c.close()
    assert identity_fingerprint(db) is None
    assert data_version(db) == "FAERS 2024Q1-2024Q1 (1 quarter, 10 cases)"
    c = duckdb.connect(db)
    c.execute("CREATE TABLE drug_identity (raw_name VARCHAR, part VARCHAR, rxcui VARCHAR, "
              "ingredient_rxcui VARCHAR, ingredient_name VARCHAR, match_type VARCHAR, n_reports BIGINT)")
    c.execute("INSERT INTO drug_identity VALUES ('sulfamethoxazole', 'x', '10178', '10178', 'sulfamethazine', 'exact', 5)")
    c.close()
    before = data_version(db)
    assert "identity " in before
    c = duckdb.connect(db)
    c.execute("UPDATE drug_identity SET ingredient_rxcui = '10180', ingredient_name = 'sulfamethoxazole'")
    c.close()
    assert data_version(db) != before

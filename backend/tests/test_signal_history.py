"""
Signal history: served from HBase when it holds the pair, otherwise from the Parquet results,
with the same numbers either way and a stated reason for the store used.
"""

import json
import os
import sys

import pytest

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from bigdata import hbase_history, timing  # noqa: E402
from core import storage  # noqa: E402
from services import signal_history as sh  # noqa: E402

RESULTS = "results/signals_by_quarter"


class FakeIdentity:
    def resolve_drug(self, text, live=True):
        known = {"statinol": [{"rxcui": "111", "name": "statinol"}],
                 "combo": [{"rxcui": "111", "name": "statinol"}, {"rxcui": "222", "name": "other"}]}
        ing = known.get(text.lower())
        return {"resolved": bool(ing), "ingredients": ing or []}

    def resolve_event(self, text):
        return {"resolved": text.lower() in ("rhabdomyolysis", "nausea", "headache"), "pt": text.capitalize()}


@pytest.fixture
def results(small_lake, monkeypatch):
    """Spark-shaped results for the small FAERS data, a run manifest, and a fake HBase."""
    con, _ = timing.run_duckdb(small_lake["lake"])
    out = os.path.join(small_lake["data_dir"], "lake", *RESULTS.split("/"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    con.execute(f"COPY results TO '{out}' (FORMAT PARQUET, PARTITION_BY (quarter))")
    rows = con.execute("SELECT * FROM results ORDER BY rxcui, pt_lower, quarter").fetchall()
    names = [d[0] for d in con.description]
    con.close()
    storage.put("lake", f"{RESULTS}/_manifest.json",
                json.dumps({"run_id": "run-1", "quarters": ["2024q1", "2024q2", "2024q3"],
                            "data_version": sh.data_version()}).encode())
    table = {}                                       # what bigdata.hbase_history would load
    for values in rows:
        r = dict(zip(names, values))
        table.setdefault(hbase_history.row_key(r["rxcui"], r["pt_lower"]), {})[
            f"q:{r['quarter']}".encode()] = hbase_history.cell(r)
    hb = {"table": table, "down": False}

    def read_row(name, key):
        assert name == "signal_history"
        if hb["down"]:
            raise ConnectionError("thrift: connection refused")
        return hb["table"].get(key, {})
    monkeypatch.setattr(sh.hbase, "read_row", read_row)
    monkeypatch.setattr(sh, "_identity", FakeIdentity())
    return hb


def _loaded(run_id="run-1"):
    storage.put("lake", f"{RESULTS}/_hbase_manifest.json", json.dumps({"spark_run_id": run_id}).encode())


def test_history_comes_from_hbase_when_it_holds_the_pair(results):
    _loaded()
    body, status = sh.history("Statinol", "Rhabdomyolysis")
    assert status == 200 and body["store"] == "hbase" and body["store_note"] is None
    assert [s["quarter"] for s in body["series"]] == ["2024q2", "2024q3"]
    assert body["first_signal_quarter"] == next(s["quarter"] for s in body["series"] if s["signal_strength"] != "none")


def test_hbase_and_parquet_give_the_same_numbers(results):
    _loaded()
    from_hbase, _ = sh.history("Statinol", "Nausea")
    results["down"] = True
    from_parquet, _ = sh.history("Statinol", "Nausea")
    assert from_hbase["store"] == "hbase" and from_parquet["store"] == "parquet"
    assert from_hbase["series"] == from_parquet["series"] and from_hbase["series"]


def test_hbase_down_falls_back_and_says_why(results):
    _loaded()
    results["down"] = True
    body, status = sh.history("Statinol", "Rhabdomyolysis")
    assert status == 200 and body["store"] == "parquet" and "HBase unavailable" in body["store_note"]


def test_hbase_loaded_from_an_older_run_is_not_trusted_for_absence(results):
    _loaded("run-0")
    results["table"].clear()
    body, _ = sh.history("Statinol", "Rhabdomyolysis")
    assert body["store"] == "parquet" and "not been loaded from the latest Spark run" in body["store_note"]


def test_errors_are_stated_not_guessed(results):
    assert sh.history("Unknownol", "Nausea")[1] == 404
    assert sh.history("Combo", "Nausea")[1] == 422
    assert sh.history("Statinol", "Sneezing")[1] == 404
    storage.delete("lake", f"{RESULTS}/_manifest.json")
    body, status = sh.history("Statinol", "Nausea")
    assert status == 503 and "bigdata.run_spark" in body["error"]


def test_a_pair_with_fewer_than_three_cases_has_no_history(results):
    _loaded()
    body, status = sh.history("Statinol", "Headache")          # drug 111 never appears with headache
    assert status == 200 and body["series"] == [] and body["first_signal_quarter"] is None
    assert "Fewer than 3 cases" in body["note"]


def test_the_route_validates_its_input(results):
    from app import app
    c = app.test_client()
    assert c.get("/api/signals/history?drug=Statinol").status_code == 400
    r = c.get("/api/signals/history?drug=Statinol&event=Rhabdomyolysis")
    assert r.status_code == 200 and r.get_json()["meta"]["evidence"] == "measured"

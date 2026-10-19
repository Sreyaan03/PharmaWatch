# backend/services/signal_history.py
# How a drug-event signal developed, quarter by quarter, from the Spark all-pairs results.
#
# Read from HBase (signal_history, one row per pair) when it is up and holds the pair; otherwise
# from the Parquet results in the lake, which hold every pair with 3+ cases. The response says
# which store answered and why. Values are cumulative: each quarter covers every loaded quarter
# up to and including it.

import json
import os

import duckdb

from clients import hbase
from core import storage
from faers.registry import data_version
from services.identity import IdentityService

RESULTS = "results/signals_by_quarter"
TABLE = "signal_history"
NOTE = ("Cumulative from the first loaded quarter. A case counts from the quarter of the report version "
        "kept after de-duplication, so a case updated later appears later. A signal is a reporting "
        "pattern, not proof that the drug causes the event.")

_identity = None


def _identity_service():
    global _identity
    if _identity is None:
        _identity = IdentityService()
    return _identity


def _manifest(name):
    raw = storage.get("lake", f"{RESULTS}/{name}")
    return json.loads(raw) if raw else None


def _from_hbase(rxcui, pt_lower):
    row = hbase.read_row(TABLE, f"{rxcui}|{pt_lower}".encode("utf-8"))
    if row is None:
        raise RuntimeError("happybase is not installed")
    series = []
    for col, value in sorted(row.items()):
        v = json.loads(value)
        series.append({"quarter": col.decode().split(":", 1)[1], "a": v["a"], "prr": v["prr"],
                       "ror_ci_low": v["rl"], "ic025": v["ic"], "signal_strength": v["s"]})
    return series


def _from_parquet(rxcui, pt_lower):
    if storage.backend().name != "local":
        raise RuntimeError("Parquet fallback reads the lake from local storage")
    glob = os.path.join(storage.backend().root("lake"), *RESULTS.split("/"), "*", "*.parquet").replace("\\", "/")
    con = duckdb.connect()
    try:
        rows = con.execute(
            f"SELECT quarter, a, prr, ror_ci_low, ic025, signal_strength "
            f"FROM read_parquet('{glob}', hive_partitioning = 1) WHERE rxcui = ? AND pt_lower = ? ORDER BY quarter",
            [rxcui, pt_lower]).fetchall()
    finally:
        con.close()
    return [{"quarter": q, "a": int(a), "prr": round(prr, 4), "ror_ci_low": round(rl, 4), "ic025": round(ic, 4),
             "signal_strength": s} for q, a, prr, rl, ic, s in rows]


def history(drug, event):
    spark = _manifest("_manifest.json")
    if spark is None:
        return {"error": "The quarterly batch results have not been computed (run python -m bigdata.run_spark).",
                "series": []}, 503
    ident = _identity_service()
    d = ident.resolve_drug(drug, live=False)
    if not d.get("resolved"):
        return {"error": f"'{drug}' is not a drug in the loaded data.", "series": []}, 404
    if len(d["ingredients"]) != 1:
        return {"error": "Quarterly history covers single-ingredient drugs; this name maps to "
                         f"{len(d['ingredients'])} ingredients.", "series": []}, 422
    e = ident.resolve_event(event)
    if not e.get("resolved"):
        return {"error": f"'{event}' is not a reaction term in the loaded data.", "series": []}, 404
    ingredient, pt = d["ingredients"][0], e["pt"]
    rxcui, pt_lower = str(ingredient["rxcui"]), pt.lower()

    loaded = _manifest("_hbase_manifest.json")
    store, why, series = None, None, None
    try:
        series = _from_hbase(rxcui, pt_lower)
        if series:
            store = "hbase"
        elif (loaded or {}).get("spark_run_id") != spark.get("run_id"):
            why = "HBase has not been loaded from the latest Spark run; read from the Parquet results"
        else:
            why = "HBase holds only pairs that were a signal in some quarter; read from the Parquet results"
    except Exception as exc:
        why = f"HBase unavailable ({type(exc).__name__}); read from the Parquet results"
    if store is None:
        series = _from_parquet(rxcui, pt_lower)
        store = "parquet"
    first = next((s["quarter"] for s in series if s["signal_strength"] != "none"), None)
    current = data_version()
    return {
        "drug": drug, "ingredient": ingredient, "pt": pt, "series": series, "first_signal_quarter": first,
        "quarters_loaded": spark.get("quarters"), "store": store, "store_note": why,
        "spark_run_id": spark.get("run_id"), "hbase_loaded_from_run": (loaded or {}).get("spark_run_id"),
        "results_data_version": spark.get("data_version"), "stale": spark.get("data_version") != current,
        "note": NOTE if series else "Fewer than 3 cases in every loaded quarter: no history to show.",
    }, 200

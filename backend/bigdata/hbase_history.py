# backend/bigdata/hbase_history.py
# Loads signal history from the Spark results into HBase, the serving store the app reads:
#
#   table  signal_history
#   row    <ingredient rxcui>|<preferred term, lower case>
#   column q:<quarter>   {"a": cases, "prr": .., "rl": ROR lower bound, "ic": IC025, "s": class}
#
# One row holds a pair's whole history, so the app draws "how this signal developed" with a
# single row read. Rows are loaded for pairs that are a signal in at least one quarter; every
# other pair is answered from the Parquet results (services.signal_history), which hold all pairs
# with 3+ cases.
#
#   python -m bigdata.hbase_history          # full reload after a Spark run

import argparse
import json
import sys
import time

import duckdb

from bigdata.run_spark import RESULTS, read_manifest
from core import storage

TABLE = "signal_history"
FAMILY = "q"


def results_glob():
    if storage.backend().name != "local":
        raise RuntimeError("the loader reads the Spark results from local storage")
    return f"{storage.backend().root('lake')}/{RESULTS}/*/*.parquet".replace("\\", "/")


def row_key(rxcui, pt_lower):
    return f"{rxcui}|{pt_lower}".encode("utf-8")


def cell(r):
    """Compact JSON of one pair-quarter (values rounded to 4 decimals, as the app reports them)."""
    return json.dumps({"a": int(r["a"]), "prr": round(r["prr"], 4), "rl": round(r["ror_ci_low"], 4),
                       "ic": round(r["ic025"], 4), "s": r["signal_strength"]}, separators=(",", ":")).encode()


def connect():
    import happybase
    from core import config
    conn = happybase.Connection(config.get("HBASE_HOST"), port=config.get("HBASE_PORT"), timeout=60000)
    conn.open()
    return conn


def load(batch_size=2000, log=print):
    started = time.time()
    glob = results_glob()
    conn = connect()
    try:
        if TABLE.encode() in conn.tables():
            conn.delete_table(TABLE, disable=True)
        conn.create_table(TABLE, {FAMILY: dict(max_versions=1)})
        table = conn.table(TABLE)
        con = duckdb.connect()
        cur = con.execute(f"""
            WITH r AS (SELECT * FROM read_parquet('{glob}', hive_partitioning = 1)),
            ever AS (SELECT DISTINCT rxcui, pt_lower FROM r WHERE signal_strength <> 'none')
            SELECT r.rxcui, r.pt_lower, r.quarter, r.a, r.prr, r.ror_ci_low, r.ic025, r.signal_strength
            FROM r JOIN ever USING (rxcui, pt_lower) ORDER BY r.rxcui, r.pt_lower, r.quarter""")
        names = [d[0] for d in cur.description]
        rows = cells = 0
        current, data = None, {}
        with table.batch(batch_size=batch_size) as b:
            while True:
                chunk = cur.fetchmany(50000)
                if not chunk:
                    break
                for values in chunk:
                    r = dict(zip(names, values))
                    key = row_key(r["rxcui"], r["pt_lower"])
                    if key != current:
                        if current is not None:
                            b.put(current, data)
                            rows += 1
                        current, data = key, {}
                    data[f"{FAMILY}:{r['quarter']}".encode()] = cell(r)
                    cells += 1
                if rows and rows % 100000 < len(chunk) // 10:
                    log(f"[*] {rows:,} rows loaded ({time.time() - started:.0f} s)")
            if current is not None:
                b.put(current, data)
                rows += 1
        con.close()
    finally:
        conn.close()
    spark = read_manifest() or {}
    manifest = {"table": TABLE, "rows": rows, "cells": cells, "spark_run_id": spark.get("run_id"),
                "data_version": spark.get("data_version"), "rule": "pairs that are a signal in at least one quarter",
                "seconds": round(time.time() - started, 1),
                "loaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    storage.put("lake", f"{RESULTS}/_hbase_manifest.json", json.dumps(manifest, indent=2).encode())
    log(f"[+] {TABLE}: {rows:,} rows, {cells:,} quarter cells in {manifest['seconds']} s")
    return manifest


def read_load_manifest():
    raw = storage.get("lake", f"{RESULTS}/_hbase_manifest.json")
    return json.loads(raw) if raw else None


def main(argv=None):
    ap = argparse.ArgumentParser(description="Load signal history from the Spark results into HBase.")
    ap.add_argument("--batch-size", type=int, default=2000)
    args = ap.parse_args(argv)
    from core.config import load_env
    load_env()
    try:
        load(args.batch_size)
    except Exception as exc:
        print(f"[-] {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

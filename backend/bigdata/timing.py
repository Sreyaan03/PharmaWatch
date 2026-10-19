# backend/bigdata/timing.py
# Spark and DuckDB on the same job: the all-pairs, all-quarters computation of
# bigdata/spark_signals.py, on the same lake and the same machine.
#
# DuckDB computes the 2x2 tables with the same definition (same pairs kept, same cumulative counts)
# and the result is compared with Spark's in full: every pair-quarter must be present in both with
# identical cells and class. Times are published whichever engine is faster; on one machine
# DuckDB is expected to win, and Spark's role is the job that can move to a cluster unchanged.
#
#   python -m bigdata.timing                 # runs Spark (local) and DuckDB, writes the report

import argparse
import json
import os
import platform
import sys
import time

import duckdb

from bigdata import run_spark
from core import config, storage
from core.config import REPORTS_DIR

OUT = os.path.join(REPORTS_DIR, "validation", "bigdata_timing.json")


def duckdb_tables_sql(lake):
    """The pair-quarter 2x2 tables, as spark_signals.build computes them."""
    def src(t):
        return f"read_parquet('{lake}/clean/{t}/*/*.parquet', hive_partitioning = 1)"
    return f"""
    WITH quarters AS (SELECT quarter, row_number() OVER (ORDER BY quarter) AS qi FROM (SELECT DISTINCT quarter FROM {src('case')})),
    n_total AS (SELECT quarter, sum(n) OVER (ORDER BY quarter) AS N FROM (SELECT quarter, count(*) AS n FROM {src('case')} GROUP BY 1)),
    drug_q AS (SELECT rxcui, quarter, count(*) AS n FROM {src('case_drug')} GROUP BY 1, 2),
    drug_tot AS (
        SELECT k.rxcui, q.quarter, sum(coalesce(d.n, 0)) OVER (PARTITION BY k.rxcui ORDER BY q.qi) AS exposed
        FROM (SELECT DISTINCT rxcui FROM drug_q) k CROSS JOIN quarters q
        LEFT JOIN drug_q d ON d.rxcui = k.rxcui AND d.quarter = q.quarter),
    event_q AS (SELECT pt_lower, quarter, count(*) AS n FROM {src('reaction')} GROUP BY 1, 2),
    event_tot AS (
        SELECT k.pt_lower, q.quarter, sum(coalesce(e.n, 0)) OVER (PARTITION BY k.pt_lower ORDER BY q.qi) AS events
        FROM (SELECT DISTINCT pt_lower FROM event_q) k CROSS JOIN quarters q
        LEFT JOIN event_q e ON e.pt_lower = k.pt_lower AND e.quarter = q.quarter),
    pair_q AS (
        SELECT d.rxcui, r.pt_lower, d.quarter, count(*) AS n
        FROM {src('case_drug')} d JOIN {src('reaction')} r USING (primaryid, quarter) GROUP BY 1, 2, 3),
    kept AS (SELECT rxcui, pt_lower FROM pair_q GROUP BY 1, 2 HAVING sum(n) >= 3),
    pair_cum AS (
        SELECT * FROM (
            SELECT k.rxcui, k.pt_lower, q.quarter,
                   sum(coalesce(p.n, 0)) OVER (PARTITION BY k.rxcui, k.pt_lower ORDER BY q.qi) AS a
            FROM kept k CROSS JOIN quarters q
            LEFT JOIN pair_q p ON p.rxcui = k.rxcui AND p.pt_lower = k.pt_lower AND p.quarter = q.quarter)
        WHERE a >= 3)
    SELECT p.rxcui, p.pt_lower, p.quarter, CAST(p.a AS DOUBLE) AS a, CAST(dt.exposed - p.a AS DOUBLE) AS b,
           CAST(et.events - p.a AS DOUBLE) AS c, CAST(nt.N - dt.exposed - et.events + p.a AS DOUBLE) AS d
    FROM pair_cum p JOIN drug_tot dt USING (rxcui, quarter) JOIN event_tot et USING (pt_lower, quarter)
    JOIN n_total nt USING (quarter)"""


def run_duckdb(lake, threads=None):
    """Compute the tables and the metrics (the same SQL as Spark's, in DuckDB). Returns (con, seconds)."""
    from bigdata.spark_signals import METRICS_SQL
    con = duckdb.connect()
    if threads:
        con.execute(f"SET threads = {int(threads)}")
    started = time.time()
    con.execute(f"CREATE TABLE tables AS {duckdb_tables_sql(lake)}")
    con.execute(f"CREATE TABLE results AS {METRICS_SQL}")
    return con, round(time.time() - started, 1)


def compare(con, lake):
    """Full comparison with the Spark output: (pair-quarters in Spark, only in one engine, differing)."""
    spark = f"read_parquet('{lake}/{run_spark.RESULTS}/*/*.parquet', hive_partitioning = 1)"
    row = con.execute(f"""
        SELECT count(*) FILTER (WHERE s.rxcui IS NOT NULL),
               count(*) FILTER (WHERE s.rxcui IS NULL OR r.rxcui IS NULL),
               count(*) FILTER (WHERE s.rxcui IS NOT NULL AND r.rxcui IS NOT NULL AND
                                (s.a <> r.a OR s.b <> r.b OR s.c <> r.c OR s.d <> r.d
                                 OR s.signal_strength <> r.signal_strength
                                 OR abs(s.ic025 - r.ic025) > 1e-9 * greatest(1, abs(r.ic025))))
        FROM {spark} s FULL OUTER JOIN results r USING (rxcui, pt_lower, quarter)""").fetchone()
    return {"spark_rows": row[0], "rows_in_only_one_engine": row[1], "rows_differing": row[2]}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Time Spark and DuckDB on the same all-pairs job.")
    ap.add_argument("--skip-spark", action="store_true", help="reuse the last Spark run's timing")
    args = ap.parse_args(argv)
    config.load_env()
    if storage.backend().name != "local":
        print("[-] timing reads the lake from local storage", file=sys.stderr)
        return 1
    lake = storage.backend().root("lake").replace("\\", "/")

    spark = run_spark.read_manifest() if args.skip_spark else run_spark.run(publish=False)
    con, duck_seconds = run_duckdb(lake)
    rows = con.execute("SELECT count(*) FROM results").fetchone()[0]
    agreement = compare(con, lake)
    con.close()
    report = {
        "job": "every ingredient x Preferred Term pair, every quarter, cumulative; 2x2 tables and metrics",
        "input": {"cases": 7222094, "lake": "lake/clean (Parquet, partitioned by quarter)"},
        "spark": {"version": spark.get("spark_version"), "master": spark.get("master"),
                  "parallelism": spark.get("default_parallelism"), "seconds": spark.get("seconds"),
                  "seconds_with_startup": spark.get("wall_seconds_including_startup"), "rows": spark.get("rows"),
                  "where": "official Spark image in Docker (driver memory 5 GB), local mode"},
        "duckdb": {"version": duckdb.__version__, "seconds": duck_seconds, "rows": rows,
                   "where": "in process on the host"},
        "agreement": agreement,
        "machine": {"os": platform.platform(), "cpus": os.cpu_count()},
        "note": ("One machine. DuckDB is the interactive engine and is expected to be faster here; Spark runs "
                 "the batch job because the same job runs unchanged on a cluster (SPARK_MASTER). "
                 "Spark ran inside Docker's VM and DuckDB on the host, so the comparison also includes that difference."),
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    print(f"[+] Spark {report['spark']['seconds']} s, DuckDB {duck_seconds} s; agreement {agreement}")
    return 0 if agreement["rows_in_only_one_engine"] == 0 and agreement["rows_differing"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

# backend/bigdata/verify.py
# Agreement check: the Spark all-pairs results must equal what the app computes itself.
#
# Samples pair-quarters from lake/results/signals_by_quarter, recomputes each with the app's own
# code (faers.stats.contingency over cases up to that quarter, then compute_metrics) and compares:
#   - the 2x2 cells and the signal class must be identical;
#   - every metric must equal the app's value, which is rounded to 4 decimals, within half a unit
#     of that rounding (plus a relative 1e-9 for large values such as chi-square).
# Any difference fails. Writes reports/validation/spark_agreement.json.
#
#   python -m bigdata.verify [--per-quarter 200]

import argparse
import json
import os
import sys
import time

import duckdb

from core import storage
from core.config import REPORTS_DIR
from faers.disproportionality import compute_metrics
from faers.registry import database_path
from faers.stats import contingency

METRICS = ("prr", "prr_ci_low", "prr_ci_high", "ror", "ror_ci_low", "ror_ci_high", "chi2", "bcpnn_ic", "ic025")
OUT = os.path.join(REPORTS_DIR, "validation", "spark_agreement.json")


def results_glob():
    root = storage.backend().root("lake") if storage.backend().name == "local" else None
    if root is None:
        raise RuntimeError("verification reads the lake from local storage")
    return os.path.join(root, "results", "signals_by_quarter", "*", "*.parquet").replace("\\", "/")


def sample(con, glob, quarter, n, seed):
    """Half from signal classes, half from 'none', plus any zero-cell tables (continuity correction)."""
    base = f"read_parquet('{glob}', hive_partitioning = 1)"
    cols = "rxcui, pt_lower, quarter, a, b, c, d, signal_strength, " + ", ".join(METRICS)
    rows = []
    for where, k in (("signal_strength <> 'none'", n // 2), ("signal_strength = 'none'", n - n // 2),
                     ("continuity_correction", 10)):
        rows += con.execute(f"SELECT {cols} FROM (SELECT * FROM {base} WHERE quarter = ? AND {where}) "
                            f"USING SAMPLE reservoir({int(k)} ROWS) REPEATABLE ({seed})", [quarter]).fetchall()
    names = cols.split(", ")
    return [dict(zip(names, r)) for r in rows]


def close(spark_value, app_value):
    if spark_value is None or app_value is None:
        return spark_value == app_value
    return abs(spark_value - app_value) <= 0.5e-4 + 1e-9 * abs(app_value) + 1e-12


def verify(per_quarter=200, seed=7, log=print):
    glob = results_glob()
    db = database_path()
    con = duckdb.connect(db, read_only=True)
    try:
        quarters = [r[0] for r in con.execute(
            f"SELECT DISTINCT quarter FROM read_parquet('{glob}', hive_partitioning = 1) ORDER BY 1").fetchall()]
        chosen = sorted({quarters[0], quarters[len(quarters) // 2], quarters[-1]})
        checked, mismatches, started = 0, [], time.time()
        for q in chosen:
            for row in sample(con, glob, q, per_quarter, seed):
                cells = contingency(con, row["rxcui"], row["pt_lower"], where=("quarter <= ?", [q]))
                app = compute_metrics(cells["a"], cells["b"], cells["c"], cells["d"])
                problems = [f"cell {k}: spark {row[k]} app {cells[k]}" for k in "abcd" if row[k] != cells[k]]
                if row["signal_strength"] != app["signal_strength"]:
                    problems.append(f"class: spark {row['signal_strength']} app {app['signal_strength']}")
                problems += [f"{m}: spark {row[m]!r} app {app[m]!r}" for m in METRICS if not close(row[m], app[m])]
                checked += 1
                if problems:
                    mismatches.append({"rxcui": row["rxcui"], "pt": row["pt_lower"], "quarter": q, "problems": problems})
            log(f"[*] {q}: {checked} checked so far, {len(mismatches)} mismatches")
    finally:
        con.close()
    raw = storage.get("lake", "results/signals_by_quarter/_manifest.json")
    manifest = json.loads(raw) if raw else {}
    report = {
        "checked": checked, "mismatches": len(mismatches), "examples": mismatches[:20], "quarters": chosen,
        "per_quarter": per_quarter, "seed": seed, "spark_run_id": manifest.get("run_id"),
        "data_version": manifest.get("data_version"),
        "rule": "cells and class identical; metrics within half a unit of the app's 4-decimal rounding",
        "seconds": round(time.time() - started, 1),
        "run_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    log(f"[{'+' if not mismatches else '!'}] {checked} pair-quarters checked against the app's own statistics: "
        f"{len(mismatches)} mismatches")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="Check Spark results against the app's own statistics.")
    ap.add_argument("--per-quarter", type=int, default=200)
    args = ap.parse_args(argv)
    from core.config import load_env
    load_env()
    report = verify(args.per_quarter)
    return 1 if report["mismatches"] else 0


if __name__ == "__main__":
    sys.exit(main())

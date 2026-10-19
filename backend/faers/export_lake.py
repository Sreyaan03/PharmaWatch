# backend/faers/export_lake.py
# Writes the cleaned, identity-resolved FAERS data to the lake (storage area "lake") as Parquet,
# partitioned by quarter, for batch engines (Spark) to read:
#
#   lake/clean/case/quarter=2024q1/*.parquet        primaryid             one row per de-duplicated case
#   lake/clean/case_drug/quarter=.../*.parquet      primaryid, rxcui      suspect ingredients, distinct
#   lake/clean/reaction/quarter=.../*.parquet       primaryid, pt_lower   reaction terms, distinct
#   lake/clean/<table>/_manifest.json               rows, quarters, data version, identity fingerprint
#
# These are exactly the rows faers.stats.contingency counts: a case belongs to the quarter of the
# report version kept after de-duplication, a drug counts when it is primary or secondary suspect,
# and terms are compared in lower case. The raw quarterly zips stay in DATA_DIR/faers_raw.
#
#   python -m faers.export_lake

import json
import os
import shutil
import sys
import tempfile
import time

import duckdb

from core import storage
from faers.registry import data_version, database_path, identity_fingerprint

TABLES = {
    "case": "SELECT primaryid, quarter FROM faers_demo",
    "case_drug": """SELECT DISTINCT cd.primaryid, cd.ingredient_rxcui AS rxcui, d.quarter
                    FROM case_drug cd JOIN faers_demo d USING (primaryid) WHERE cd.suspect""",
    "reaction": """SELECT DISTINCT r.primaryid, lower(r.pt) AS pt_lower, d.quarter
                   FROM faers_reac r JOIN faers_demo d USING (primaryid)""",
}


def export(db_file=None, log=print):
    """Export every table; returns {table: rows}. Replaces the previous export."""
    db_file = db_file or database_path()
    if not os.path.exists(db_file):
        raise FileNotFoundError(f"No FAERS database at {db_file}")
    version, fingerprint = data_version(db_file), identity_fingerprint(db_file)
    con = duckdb.connect(db_file, read_only=True)
    summary = {}
    work = tempfile.mkdtemp(prefix="lake-")
    try:
        for table, sql in TABLES.items():
            started = time.time()
            out_dir = os.path.join(work, table)
            con.execute(f"COPY ({sql}) TO '{out_dir}' (FORMAT PARQUET, PARTITION_BY (quarter), "
                        f"OVERWRITE_OR_IGNORE, COMPRESSION ZSTD)")
            rows = con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]
            quarters = sorted(d.split("=", 1)[1] for d in os.listdir(out_dir) if d.startswith("quarter="))
            storage.delete_prefix("lake", f"clean/{table}")
            files = 0
            for root, _, names in os.walk(out_dir):
                for name in names:
                    path = os.path.join(root, name)
                    key = "clean/" + table + "/" + os.path.relpath(path, out_dir).replace(os.sep, "/")
                    storage.put_file("lake", key, path)
                    files += 1
            manifest = {"table": table, "rows": rows, "quarters": quarters, "files": files,
                        "data_version": version, "identity_fingerprint": fingerprint,
                        "definition": " ".join(sql.split()),
                        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            storage.put("lake", f"clean/{table}/_manifest.json", json.dumps(manifest, indent=2).encode())
            summary[table] = rows
            log(f"[+] lake/clean/{table}: {rows:,} rows, {len(quarters)} quarters, {files} files "
                f"({time.time() - started:.0f} s)")
    finally:
        con.close()
        shutil.rmtree(work, ignore_errors=True)
    return summary


def read_manifest(table):
    raw = storage.get("lake", f"clean/{table}/_manifest.json")
    return json.loads(raw) if raw else None


def main(argv=None):
    from core.config import load_env
    load_env()
    try:
        export()
    except FileNotFoundError as exc:
        print(f"[-] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

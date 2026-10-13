# backend/faers/loader.py
# Loads parsed FAERS quarters into one DuckDB database, de-duplicated and registered.
#
#   python -m faers.loader                    # every parsed quarter that has a manifest
#   python -m faers.loader --quarters 2026q1 2026q2
#
# Reads <DATA_DIR>/faers_parquet and writes <DATA_DIR>/pharmawatch_faers.duckdb. The database
# is built beside the live one and swapped in at the end, so the running app never sees a
# half-loaded file.

import argparse
import glob
import json
import os
import sys

import duckdb

from core.config import data_dir
from faers.dedupe import build_clean_tables
from faers.registry import database_path, write_registry

# Brand -> generic names for the default watchlist drugs. Replaced by the RxNorm identity
# service in plan 06-02; kept until then because the analytics queries still read it.
DRUG_MAPPINGS = [
    ("coumadin", "warfarin"), ("jantoven", "warfarin"),
    ("cipro", "ciprofloxacin"),
    ("glucophage", "metformin"), ("glumetza", "metformin"), ("fortamet", "metformin"),
    ("cordarone", "amiodarone"), ("pacerone", "amiodarone"),
    ("clozaril", "clozapine"), ("fazaclo", "clozapine"), ("versacloz", "clozapine"),
    ("rheumatrex", "methotrexate"), ("trexall", "methotrexate"),
    ("zocor", "simvastatin"),
    ("accutane", "isotretinoin"), ("claravis", "isotretinoin"), ("amnesteem", "isotretinoin"),
    ("sotret", "isotretinoin"),
    ("lithobid", "lithium"), ("eskalith", "lithium"),
    ("depakote", "valproate"), ("depakene", "valproate"),
    ("haldol", "haloperidol"),
    ("oxycontin", "oxycodone"), ("roxicodone", "oxycodone"),
    ("taxol", "paclitaxel"), ("abraxane", "paclitaxel"),
    ("norvasc", "amlodipine"),
    ("lipitor", "atorvastatin"),
    ("bayer aspirin", "aspirin"), ("adprin", "aspirin"), ("ecotrin", "aspirin"),
]


def parquet_dir():
    return os.path.join(data_dir(), "faers_parquet")


def parsed_quarters(directory):
    """Manifests of every parsed quarter, oldest first."""
    manifests = []
    for path in sorted(glob.glob(os.path.join(directory, "manifest_*.json"))):
        with open(path, encoding="utf-8") as f:
            manifests.append(json.load(f))
    return sorted(manifests, key=lambda m: m["quarter"])


def load_database(parquet_directory=None, db_file=None, quarters=None, log=print):
    """Build the database from parsed quarters. Returns a summary dict."""
    directory = parquet_directory or parquet_dir()
    db_file = db_file or database_path()
    manifests = parsed_quarters(directory)
    if quarters:
        wanted = {q.lower() for q in quarters}
        manifests = [m for m in manifests if m["quarter"] in wanted]
        missing = wanted - {m["quarter"] for m in manifests}
        if missing:
            raise FileNotFoundError(f"Not parsed yet: {sorted(missing)}. Run faers.parser first.")
    if not manifests:
        raise FileNotFoundError(f"No parsed quarters in {directory}. Run faers.downloader and faers.parser first.")

    labels = [m["quarter"] for m in manifests]
    log(f"[*] Loading {len(labels)} quarter(s): {labels[0]} .. {labels[-1]}")
    log(f"[*] Database: {db_file}")

    building = db_file + ".building"
    for stale in (building, building + ".wal"):
        if os.path.exists(stale):
            os.remove(stale)
    os.makedirs(os.path.dirname(db_file), exist_ok=True)

    con = duckdb.connect(building)
    try:
        build_clean_tables(con, directory, labels, log=log)
        write_registry(con, manifests)

        con.execute("DROP TABLE IF EXISTS drug_mappings")
        con.execute("CREATE TABLE drug_mappings (brand_name VARCHAR PRIMARY KEY, generic_name VARCHAR)")
        con.executemany("INSERT INTO drug_mappings VALUES (?, ?)", DRUG_MAPPINGS)

        def count(table):
            return con.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

        summary = {
            "quarters": len(labels),
            "first": labels[0],
            "last": labels[-1],
            "reports_in_files": sum(m["tables"]["demo"] for m in manifests),
            "cases_kept": count("faers_demo"),
            "deleted_case_ids": count("deleted_cases"),
            "drug_rows": count("faers_drug"),
            "reac_rows": count("faers_reac"),
            "ther_rows": count("faers_ther"),
            "outc_rows": count("faers_outc"),
        }
        con.execute("CHECKPOINT")
    finally:
        con.close()

    if os.path.exists(db_file):
        os.remove(db_file)
    os.replace(building, db_file)
    summary["database"] = db_file
    summary["size_mb"] = round(os.path.getsize(db_file) / 1e6, 1)
    log(f"[+] {summary['cases_kept']:,} cases kept from {summary['reports_in_files']:,} reports "
        f"({summary['size_mb']} MB)")
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Load parsed FAERS quarters into DuckDB.")
    parser.add_argument("--quarters", nargs="+", metavar="YYYYqN", help="limit to these quarters")
    args = parser.parse_args(argv)
    try:
        from dotenv import load_dotenv
        from core.config import BACKEND_DIR
        load_dotenv(os.path.join(BACKEND_DIR, ".env"), override=False)
    except ImportError:
        pass
    try:
        load_database(quarters=args.quarters)
    except (FileNotFoundError, PermissionError) as exc:
        print(f"[-] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

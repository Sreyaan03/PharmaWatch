# backend/faers/sample_db.py
# A smaller FAERS database with the same schema, for test deployments (the scaled profile) and
# demonstrations on small machines: a fixed share of de-duplicated cases from every loaded
# quarter, with all their rows, and the identity tables unchanged.
#
#   python -m faers.sample_db --percent 5 --out C:/PharmaWatchData/faers_sample_5pct.duckdb
#
# Cases are chosen by a hash of primaryid, so the sample is the same every time. Counts in the
# sample are not FAERS counts: the database records the share it holds (identity_build_info
# 'sample_percent'), and its data version says "sample".

import argparse
import os
import sys

import duckdb

from faers.identity_build import build_pt_dictionary
from faers.registry import database_path

PER_CASE = ["faers_demo", "faers_drug", "faers_reac", "faers_outc", "faers_ther", "case_drug"]
WHOLE = ["deleted_cases", "drug_brand_map", "drug_identity", "drug_mappings", "identity_build_info"]


def make_sample(percent, out, source=None, log=print):
    source = source or database_path()
    if not 0 < percent <= 100:
        raise ValueError("percent must be in (0, 100]")
    if os.path.exists(out):
        os.remove(out)
    con = duckdb.connect(out)
    try:
        con.execute(f"ATTACH '{source}' AS src (READ_ONLY)")
        con.execute(f"CREATE TABLE keep AS SELECT primaryid FROM src.faers_demo "
                    f"WHERE hash(primaryid) % 10000 < {int(round(percent * 100))}")
        for t in PER_CASE:
            con.execute(f"CREATE TABLE {t} AS SELECT s.* FROM src.{t} s SEMI JOIN keep USING (primaryid)")
        for t in WHOLE:
            con.execute(f"CREATE TABLE {t} AS SELECT * FROM src.{t}")
        con.execute("""CREATE TABLE faers_quarters AS
            SELECT q.* REPLACE (coalesce(k.n, 0) AS cases_kept)
            FROM src.faers_quarters q LEFT JOIN (SELECT quarter, count(*) AS n FROM faers_demo GROUP BY 1) k USING (quarter)""")
        build_pt_dictionary(con)
        con.execute("DELETE FROM identity_build_info WHERE key = 'sample_percent'")
        con.execute("INSERT INTO identity_build_info VALUES ('sample_percent', ?)", [str(percent)])
        con.execute("DROP TABLE keep")
        con.execute("DETACH src")
        cases = con.execute("SELECT count(*) FROM faers_demo").fetchone()[0]
        con.execute("CHECKPOINT")
    finally:
        con.close()
    log(f"[+] {out}: {cases:,} cases ({percent}% sample), {os.path.getsize(out) / 1e6:.0f} MB")
    return cases


def main(argv=None):
    ap = argparse.ArgumentParser(description="Write a smaller FAERS database with the same schema.")
    ap.add_argument("--percent", type=float, default=5.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    from core.config import load_env
    load_env()
    make_sample(args.percent, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

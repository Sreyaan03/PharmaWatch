# backend/faers/identity_build.py
# Adds drug and event identity tables to a loaded FAERS database:
#
#   drug_identity(raw_name, part, rxcui, ingredient_rxcui, ingredient_name, match_type, n_reports)
#   case_drug(primaryid, ingredient_rxcui, suspect)      one row per case and ingredient
#   pt_dictionary(pt, pt_lower, n_reports)
#   identity_build_info(key, value)
#
#   python -m faers.identity_build [--min-reports 10] [--limit N] [--workers 8]
#
# Names are resolved through RxNav once and cached on disk, so a rerun (for example after
# loading another quarter) only looks up names it has not seen. Combination products
# ("lamivudine\zidovudine") are split on the backslash and each part resolved on its own.
# Only exact name matches resolve; unresolved names stay unresolved.
#
# The slow step (the RxNav lookups) runs with no database connection open, so the running app
# keeps reading the database while it works; the tables are written in one short step at the end.

import argparse
import os
import sys
import time

import duckdb

from clients.rxnav import RxNavClient
from core.config import data_dir
from faers.registry import database_path

NAME_EXPR = "lower(trim(coalesce(nullif(trim(prod_ai), ''), drugname)))"

NAME_SQL = f"""
    SELECT {NAME_EXPR} AS name, count(DISTINCT primaryid) AS n_reports
    FROM faers_drug
    WHERE trim(coalesce(nullif(trim(prod_ai), ''), drugname)) <> ''
    GROUP BY 1
    HAVING count(DISTINCT primaryid) >= ?
    ORDER BY n_reports DESC
"""


def cache_path():
    return os.path.join(data_dir(), "rxnav_cache.jsonl")


def split_parts(name):
    """'a\\b' -> ['a', 'b']; a plain name returns itself."""
    return [p.strip() for p in name.split("\\") if p.strip()]


def build_pt_dictionary(con):
    con.execute("""
        CREATE OR REPLACE TABLE pt_dictionary AS
        SELECT any_value(pt) AS pt, lower(pt) AS pt_lower, count(DISTINCT primaryid) AS n_reports
        FROM faers_reac WHERE trim(pt) <> '' GROUP BY lower(pt)
    """)
    return con.execute("SELECT count(*) FROM pt_dictionary").fetchone()[0]


def build_case_drug(con):
    """One row per (case, ingredient), flagged suspect when the drug is primary or secondary suspect.

    The per-case ingredient set is what every disproportionality count joins against.
    """
    name = "lower(trim(coalesce(nullif(trim(d.prod_ai), ''), d.drugname)))"
    con.execute(f"""
        CREATE OR REPLACE TABLE case_drug AS
        SELECT d.primaryid, i.ingredient_rxcui, bool_or(d.role_cod IN ('PS', 'SS')) AS suspect
        FROM faers_drug d
        JOIN (SELECT DISTINCT raw_name, ingredient_rxcui FROM drug_identity) i ON i.raw_name = {name}
        GROUP BY d.primaryid, i.ingredient_rxcui
        ORDER BY i.ingredient_rxcui, d.primaryid
    """)
    return con.execute("SELECT count(*) FROM case_drug").fetchone()[0]


# ── the three phases of the drug identity build ────────────────────────────────
def drug_names(con, min_reports=10, limit=None):
    """[(name, n_reports)] for the drug names that matter, most reported first."""
    names = con.execute(NAME_SQL, [min_reports]).fetchall()
    return names[:limit] if limit else names


def resolve_drug_names(client, names, workers=8, log=print):
    """Look every distinct name part up (no database connection needed). Returns {part: result|None}."""
    started = time.time()
    unique_parts = list(dict.fromkeys(p for name, _ in names for p in (split_parts(name) or [name])))
    log(f"[*] {len(unique_parts):,} distinct names to look up ({workers} concurrent requests)")
    resolved = {}
    for start in range(0, len(unique_parts), 500):
        resolved.update(client.resolve_many(unique_parts[start:start + 500], workers=workers))
        log(f"    {min(start + 500, len(unique_parts)):,}/{len(unique_parts):,} looked up, "
            f"{client.live_calls:,} live calls, {time.time() - started:.0f}s")
    return resolved


def store_drug_identity(con, names, resolved, min_reports=10, log=print):
    """Write drug_identity and identity_build_info. Returns the coverage summary."""
    total_reports = sum(n for _, n in names)
    rows = []
    resolved_reports = 0
    unresolved = []
    for name, n_reports in names:
        parts = split_parts(name) or [name]
        part_results = [(part, resolved.get(part)) for part in parts]
        failed = [p for p, r in part_results if r is None]
        if failed:
            log(f"[!] could not reach RxNav for {failed}; left unresolved")
        if all(r is not None and r["ingredients"] for _, r in part_results):
            resolved_reports += n_reports
            for part, r in part_results:
                for ing in r["ingredients"]:
                    rows.append((name, part, r["rxcui"], ing["rxcui"], ing["name"], r["match"], n_reports))
        else:
            unresolved.append((name, n_reports))

    con.execute("""CREATE OR REPLACE TABLE drug_identity (
        raw_name VARCHAR, part VARCHAR, rxcui VARCHAR, ingredient_rxcui VARCHAR,
        ingredient_name VARCHAR, match_type VARCHAR, n_reports BIGINT)""")
    if rows:
        con.executemany("INSERT INTO drug_identity VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    summary = {
        "names_considered": len(names),
        "names_resolved": len(names) - len(unresolved),
        "name_report_pairs": total_reports,
        "name_report_pairs_resolved": resolved_reports,
        "coverage_pct": round(100 * resolved_reports / total_reports, 2) if total_reports else 0.0,
        "min_reports": min_reports,
        "top_unresolved": [f"{n} ({c})" for n, c in unresolved[:10]],
    }
    con.execute("CREATE OR REPLACE TABLE identity_build_info (key VARCHAR, value VARCHAR)")
    con.executemany("INSERT INTO identity_build_info VALUES (?, ?)", [(k, str(v)) for k, v in summary.items()])
    log(f"[+] {summary['names_resolved']:,}/{summary['names_considered']:,} names resolved; "
        f"{summary['coverage_pct']}% of drug-report pairs covered")
    return summary


def build_drug_identity(con, client, min_reports=10, limit=None, log=print, workers=8):
    """All three phases on one connection (used by tests and small databases)."""
    names = drug_names(con, min_reports, limit)
    log(f"[*] Resolving {len(names):,} drug names (>= {min_reports} reports each, "
        f"{sum(n for _, n in names):,} name-report pairs)")
    resolved = resolve_drug_names(client, names, workers, log)
    return store_drug_identity(con, names, resolved, min_reports, log)


def _connect_for_write(db_file, attempts=30, wait=2.0):
    """Open the database for writing, waiting out the app's momentary read connections."""
    for attempt in range(attempts):
        try:
            return duckdb.connect(db_file)
        except duckdb.IOException:
            if attempt == attempts - 1:
                raise
            time.sleep(wait)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build drug and event identity tables in the FAERS database.")
    parser.add_argument("--min-reports", type=int, default=10, help="resolve names appearing in at least N reports")
    parser.add_argument("--limit", type=int, help="only the N most frequent names (for a quick trial)")
    parser.add_argument("--workers", type=int, default=8, help="concurrent RxNav requests")
    args = parser.parse_args(argv)
    from core.config import load_env
    load_env()

    db_file = database_path()
    if not os.path.exists(db_file):
        print(f"[-] No database at {db_file}. Run faers.loader first.", file=sys.stderr)
        return 1

    con = duckdb.connect(db_file, read_only=True)          # short: just read the names
    try:
        names = drug_names(con, args.min_reports, args.limit)
    finally:
        con.close()
    print(f"[*] Resolving {len(names):,} drug names (>= {args.min_reports} reports each, "
          f"{sum(n for _, n in names):,} name-report pairs)")

    client = RxNavClient(cache_path=cache_path())
    resolved = resolve_drug_names(client, names, args.workers)   # slow; no database connection held

    con = _connect_for_write(db_file)                      # short: write the tables
    try:
        print(f"[+] pt_dictionary: {build_pt_dictionary(con):,} Preferred Terms")
        store_drug_identity(con, names, resolved, args.min_reports)
        print(f"[+] case_drug: {build_case_drug(con):,} case-ingredient rows")
        from faers.brand_map import build_brand_map
        build_brand_map(con)
        con.execute("CHECKPOINT")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

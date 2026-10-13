# backend/faers/dedupe.py
# Builds the analysis tables from per-quarter Parquet files:
#   * one row per case, at its latest version (FDA rule: most recent FDA_DT, then largest PRIMARYID)
#   * cases the FDA later deleted are dropped, in every version
#   * age normalised to years from the reported unit
#   * child tables (drug, reac, ther, outc) keep only rows of the surviving version

import os

def age_years_expr(age="age", cod="age_cod"):
    """DuckDB expression: age and its unit code (YR, DEC, MON, WK, DY, HR) -> years.

    NULL when the unit is missing or unknown, or the result is outside 0-120 years.
    """
    num = f"TRY_CAST({age} AS DOUBLE)"
    converted = (
        f"CASE upper({cod}) "
        f"WHEN 'YR' THEN {num} "
        f"WHEN 'DEC' THEN {num} * 10 "
        f"WHEN 'MON' THEN {num} / 12 "
        f"WHEN 'WK' THEN {num} * 7 / 365.25 "
        f"WHEN 'DY' THEN {num} / 365.25 "
        f"WHEN 'HR' THEN {num} / (24 * 365.25) "
        f"ELSE NULL END"
    )
    return f"CASE WHEN ({converted}) BETWEEN 0 AND 120 THEN ({converted}) END"


def _files(parquet_dir, kind, labels):
    return [os.path.join(parquet_dir, f"{kind}_{label}.parquet").replace("\\", "/") for label in labels]


def build_clean_tables(con, parquet_dir, labels, log=print):
    """Create faers_demo, faers_drug, faers_reac, faers_ther, faers_outc in `con`."""
    labels = list(labels)
    if not labels:
        raise ValueError("No quarters to load.")

    con.execute("CREATE TABLE deleted_cases AS SELECT DISTINCT caseid FROM read_parquet(?)",
                [_files(parquet_dir, "dele", labels)])

    log(f"[*] Selecting the latest version of each case across {len(labels)} quarter(s)...")
    con.execute(f"""
        CREATE TABLE faers_demo AS
        WITH live AS (
            SELECT s.* FROM read_parquet(?) s
            WHERE NOT EXISTS (SELECT 1 FROM deleted_cases d WHERE d.caseid = s.caseid)
        ),
        ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY caseid
                ORDER BY fda_dt DESC, TRY_CAST(primaryid AS BIGINT) DESC
            ) AS rn
            FROM live
        )
        SELECT primaryid, caseid, TRY_CAST(caseversion AS INTEGER) AS caseversion, i_f_code,
               event_dt, fda_dt, age, age_cod, {age_years_expr()} AS age_years,
               sex, reporter_country, occr_country, quarter
        FROM ranked WHERE rn = 1
        ORDER BY primaryid
    """, [_files(parquet_dir, "demo", labels)])

    children = {
        "faers_drug": ("drug", "primaryid, caseid, drug_seq, role_cod, drugname, prod_ai, quarter"),
        "faers_reac": ("reac", "primaryid, pt, quarter"),
        "faers_ther": ("ther", "primaryid, dsg_drug_seq, start_dt, end_dt, quarter"),
        "faers_outc": ("outc", "primaryid, outc_cod, quarter"),
    }
    for table, (kind, columns) in children.items():
        log(f"[*] Building {table}...")
        con.execute(f"""
            CREATE TABLE {table} AS
            SELECT {columns} FROM read_parquet(?) c
            WHERE c.primaryid IN (SELECT primaryid FROM faers_demo)
            ORDER BY primaryid
        """, [_files(parquet_dir, kind, labels)])

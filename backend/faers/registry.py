# backend/faers/registry.py
# Which FAERS quarters are loaded, and the data version string stamped on every result.

import os

import duckdb

from core.config import data_dir

REGISTRY_DDL = """
CREATE TABLE faers_quarters (
    quarter            VARCHAR PRIMARY KEY,
    zip_file           VARCHAR,
    zip_sha256         VARCHAR,
    reports_in_file    BIGINT,
    deleted_cases      BIGINT,
    cases_kept         BIGINT,
    drug_rows          BIGINT,
    reac_rows          BIGINT,
    is_fixture         BOOLEAN,
    loaded_at          TIMESTAMP
)
"""


def database_path():
    return os.path.join(data_dir(), "pharmawatch_faers.duckdb")


def write_registry(con, manifests):
    """Record one row per loaded quarter. `manifests` is a list of parser manifest dicts."""
    con.execute(REGISTRY_DDL)
    kept = dict(con.execute("SELECT quarter, count(*) FROM faers_demo GROUP BY quarter").fetchall())
    drugs = dict(con.execute("SELECT quarter, count(*) FROM faers_drug GROUP BY quarter").fetchall())
    reacs = dict(con.execute("SELECT quarter, count(*) FROM faers_reac GROUP BY quarter").fetchall())
    for m in manifests:
        q = m["quarter"]
        con.execute(
            "INSERT INTO faers_quarters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, now())",
            [q, m["zip"], m.get("zip_sha256"), m["tables"]["demo"], m.get("deleted_cases", 0),
             kept.get(q, 0), drugs.get(q, 0), reacs.get(q, 0), bool(m.get("fixture"))],
        )


def list_quarters(db_file=None):
    """[{quarter, cases_kept, ...}] oldest first, or [] when nothing is loaded."""
    db_file = db_file or database_path()
    if not os.path.exists(db_file):
        return []
    con = duckdb.connect(db_file, read_only=True)
    try:
        rows = con.execute(
            "SELECT quarter, zip_file, zip_sha256, reports_in_file, deleted_cases, cases_kept, "
            "drug_rows, reac_rows, is_fixture, loaded_at FROM faers_quarters ORDER BY quarter"
        ).fetchall()
    except duckdb.Error:
        return []
    finally:
        con.close()
    keys = ["quarter", "zip_file", "zip_sha256", "reports_in_file", "deleted_cases", "cases_kept",
            "drug_rows", "reac_rows", "is_fixture", "loaded_at"]
    return [dict(zip(keys, r)) for r in rows]


def identity_fingerprint(db_file=None):
    """Short hash of the drug identity table, or None when it has not been built.

    The same quarters resolved with different identity logic give different counts, so this is
    part of the data version: cached results made from an earlier identity build are not reused.
    """
    import hashlib
    db_file = db_file or database_path()
    if not os.path.exists(db_file):
        return None
    con = duckdb.connect(db_file, read_only=True)
    try:
        row = con.execute(
            "SELECT count(*), coalesce(sum(n_reports), 0), count(DISTINCT ingredient_rxcui), "
            "string_agg(DISTINCT ingredient_rxcui, ',' ORDER BY ingredient_rxcui) FROM drug_identity").fetchone()
    except duckdb.Error:
        return None
    finally:
        con.close()
    return hashlib.sha256("|".join(str(x) for x in row).encode()).hexdigest()[:6]


def data_version(db_file=None):
    """e.g. 'FAERS 2021Q3-2026Q2 (20 quarters, 6,912,331 cases, identity 3fa91c)'."""
    quarters = list_quarters(db_file)
    if not quarters:
        return "FAERS data not loaded"
    first, last = quarters[0]["quarter"].upper(), quarters[-1]["quarter"].upper()
    cases = sum(q["cases_kept"] for q in quarters)
    ident = identity_fingerprint(db_file)
    detail = f"{cases:,} cases" + (f", identity {ident}" if ident else "")
    text = f"FAERS {first}-{last} ({len(quarters)} quarter{'s' if len(quarters) != 1 else ''}, {detail})"
    if any(q["is_fixture"] for q in quarters):
        text += " [test fixture]"
    return text

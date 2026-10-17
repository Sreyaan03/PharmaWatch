"""
Brand name -> ingredient map, derived from the FAERS data itself.

The identity table is keyed on the active-ingredient field, so brand names (Coumadin, Lipitor,
Augmentin) rarely appear in it. In the drug rows, though, `drugname` holds what the reporter wrote
(often a brand) next to `prod_ai`, the active ingredient. A brand is mapped to an ingredient when
it appears in at least MIN_ROWS rows with a single-ingredient `prod_ai` and at least MIN_SHARE of
those rows name the same ingredient. Brands that point to several ingredients are left out.

    python -m faers.brand_map          # (re)build the drug_brand_map table in the loaded database
"""

import os
import sys

import duckdb

MIN_ROWS = 20
MIN_SHARE = 0.90

BUILD_SQL = """
CREATE OR REPLACE TABLE drug_brand_map AS
WITH single AS (
    SELECT raw_name, any_value(ingredient_rxcui) AS ingredient_rxcui, any_value(ingredient_name) AS ingredient_name
    FROM drug_identity GROUP BY raw_name HAVING count(DISTINCT ingredient_rxcui) = 1
),
pairs AS (
    SELECT lower(trim(d.drugname)) AS brand, s.ingredient_rxcui, s.ingredient_name, count(*) AS n
    FROM faers_drug d JOIN single s ON s.raw_name = lower(trim(d.prod_ai))
    WHERE trim(d.drugname) <> '' AND d.prod_ai NOT LIKE '%\\%'
    GROUP BY 1, 2, 3
),
totals AS (SELECT brand, sum(n) AS total FROM pairs GROUP BY brand)
SELECT p.brand, p.ingredient_rxcui, p.ingredient_name, p.n AS n_rows, t.total, p.n * 1.0 / t.total AS share
FROM pairs p JOIN totals t USING (brand)
WHERE t.total >= ? AND p.n * 1.0 / t.total >= ? AND p.brand <> lower(p.ingredient_name)
"""


def build_brand_map(con, min_rows=MIN_ROWS, min_share=MIN_SHARE, log=print):
    con.execute(BUILD_SQL, [min_rows, min_share])
    n = con.execute("SELECT count(*) FROM drug_brand_map").fetchone()[0]
    log(f"[+] drug_brand_map: {n:,} brand names mapped to a single ingredient")
    return n


def main(argv=None):
    from faers.registry import database_path
    db = database_path()
    if not os.path.exists(db):
        print(f"[-] no database at {db}", file=sys.stderr)
        return 1
    con = duckdb.connect(db)
    try:
        build_brand_map(con)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

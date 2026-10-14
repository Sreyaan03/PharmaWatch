# backend/faers/analytics.py
import os
import duckdb
from core.config import data_dir
from .stats import (complete_event_years, event_month_counts, receipt_velocity,
                    AGE_STRATA, SEX_STRATA, contingency, reports_by_country,
                    monthly_report_share, stratified_metrics, time_to_onset, unclassified_cases)
import logging
log = logging.getLogger(__name__)

DB_PATH = os.path.join(data_dir(), "pharmawatch_faers.duckdb")

def get_connection():
    """Returns a read-only connection to the DuckDB database if it exists."""
    if not os.path.exists(DB_PATH):
        return None
    try:
        # Open in read-only mode to allow concurrent flask requests
        return duckdb.connect(DB_PATH, read_only=True)
    except Exception as e:
        log.error(f"[-] DuckDB connection failed: {e}")
        return None

def get_drug_terms(conn, drug_name):
    """
    Resolves drug name to a list of terms (generic name and all associated brand names).
    Implements the 'both' requirement: case-insensitive exact match + brand-to-generic lookup.
    """
    drug_lower = drug_name.lower().strip()
    
    # 1. Check if mapping table exists
    tables = [t[0] for t in conn.execute("SHOW TABLES").fetchall()]
    if "drug_mappings" not in tables:
        return [drug_lower]
        
    # 2. Check if input is a brand name and get generic
    res = conn.execute(
        "SELECT generic_name FROM drug_mappings WHERE LOWER(brand_name) = ?",
        [drug_lower]
    ).fetchone()
    
    generic = res[0] if res else drug_lower
    
    # 3. Get all brand names for this generic
    brands = conn.execute(
        "SELECT brand_name FROM drug_mappings WHERE LOWER(generic_name) = ?",
        [generic]
    ).fetchall()
    
    terms = {generic}
    for b in brands:
        terms.add(b[0].lower())
    terms.add(drug_lower) # always include search term
    return list(terms)

def build_drug_where_clause(terms):
    """Builds the SQL WHERE clause for matching a list of drug terms in faers_drug."""
    conditions = []
    params = []
    for term in terms:
        # Exact match
        conditions.append("LOWER(drugname) = ?")
        params.append(term)
        # Word boundary match (e.g. 'metformin hcl')
        conditions.append("regexp_matches(LOWER(drugname), ?)")
        params.append(f"(^|[^a-z]){term}([^a-z]|$)")
    
    drug_cond = " ( " + " OR ".join(conditions) + " ) "
    return f" ( {drug_cond} AND role_cod IN ('PS', 'SS') ) ", params

def compute_prr_duckdb(drug, event):
    """
    Computes disproportionality metrics (a, b, c, d, PRR, ROR, BCPNN) via DuckDB.
    Returns None if database is not available or query fails.
    """
    conn = get_connection()
    if not conn:
        return None
        
    try:
        drug_terms = get_drug_terms(conn, drug)
        event_lower = event.lower().strip()
        
        # Build SQL condition for drugs
        drug_cond, drug_params = build_drug_where_clause(drug_terms)
        
        # 1. Total unique reports (N)
        total = conn.execute("SELECT COUNT(DISTINCT primaryid) FROM faers_demo").fetchone()[0] or 1
        
        # 2. Reports with drug and event (a)
        # Join faers_drug and faers_reac on primaryid
        q_a = f"""
            SELECT COUNT(DISTINCT d.primaryid) 
            FROM faers_drug d
            JOIN faers_reac r ON d.primaryid = r.primaryid
            WHERE {drug_cond} AND LOWER(r.pt) = ?
        """
        a = conn.execute(q_a, drug_params + [event_lower]).fetchone()[0] or 0
        
        # 3. Reports with drug (ab)
        q_ab = f"""
            SELECT COUNT(DISTINCT primaryid)
            FROM faers_drug
            WHERE {drug_cond}
        """
        ab = conn.execute(q_ab, drug_params).fetchone()[0] or 0
        
        # 4. Reports with event (ac)
        q_ac = """
            SELECT COUNT(DISTINCT primaryid)
            FROM faers_reac
            WHERE LOWER(pt) = ?
        """
        ac = conn.execute(q_ac, [event_lower]).fetchone()[0] or 0
        
        conn.close()
        
        b = max(ab - a, 0)
        c = max(ac - a, 0)
        d = max(total - a - b - c, 0)
        
        from .disproportionality import compute_metrics
        res = compute_metrics(a, b, c, d)
        res["source"] = "faers_local"
        return res

        
    except Exception as e:
        log.error(f"[-] Error computing disproportionality in DuckDB: {e}")
        if conn:
            conn.close()
        return None

def get_temporal_trend(drug, granularity="month"):
    """
    Computes report trend count over time for a drug.
    Returns a list of dicts: [{'date': 'YYYY-MM', 'count': int}]
    """
    conn = get_connection()
    if not conn:
        return None
        
    try:
        drug_terms = get_drug_terms(conn, drug)
        drug_cond, drug_params = build_drug_where_clause(drug_terms)
        
        # Event date column parsing
        # FAERS format: event_dt is usually YYYYMMDD string.
        # We can extract YYYY-MM or YYYY-Q
        if granularity == "quarter":
            date_expr = "SUBSTR(d.event_dt, 1, 4) || '-Q' || ((CAST(SUBSTR(d.event_dt, 5, 2) AS INTEGER) - 1) // 3 + 1)"
        else: # month
            date_expr = "SUBSTR(d.event_dt, 1, 4) || '-' || SUBSTR(d.event_dt, 5, 2)"
            
        q = f"""
            SELECT {date_expr} as dt, COUNT(DISTINCT d.primaryid) as cnt
            FROM faers_demo d
            JOIN faers_drug dr ON d.primaryid = dr.primaryid
            WHERE {drug_cond} AND LENGTH(d.event_dt) >= 6
            GROUP BY dt
            ORDER BY dt
        """
        rows = conn.execute(q, drug_params).fetchall()
        conn.close()
        
        # Filter out empty dates or invalid formats
        result = []
        for r in rows:
            if r[0] and '-' in r[0] and not r[0].startswith('None') and not r[0].endswith('-None'):
                result.append({
                    "date": r[0],
                    "count": int(r[1])
                })
        return result
    except Exception as e:
        log.error(f"[-] Error fetching temporal trend from DuckDB: {e}")
        if conn:
            conn.close()
        return None

def get_top_reactions(drug, limit=20):
    """Returns top MedDRA terms for a drug ordered by report counts."""
    conn = get_connection()
    if not conn:
        return None
        
    try:
        drug_terms = get_drug_terms(conn, drug)
        drug_cond, drug_params = build_drug_where_clause(drug_terms)
        
        q = f"""
            SELECT LOWER(r.pt) as reaction, COUNT(DISTINCT r.primaryid) as cnt
            FROM faers_drug d
            JOIN faers_reac r ON d.primaryid = r.primaryid
            WHERE {drug_cond}
            GROUP BY reaction
            ORDER BY cnt DESC
            LIMIT ?
        """
        rows = conn.execute(q, drug_params + [limit]).fetchall()
        conn.close()
        
        return [{"reaction": r[0].capitalize(), "count": int(r[1])} for r in rows if r[0]]
    except Exception as e:
        log.error(f"[-] Error fetching top reactions from DuckDB: {e}")
        if conn:
            conn.close()
        return None

def get_data_coverage():
    """Returns metadata about the currently loaded FAERS dataset in DuckDB."""
    conn = get_connection()
    if not conn:
        return {
            "status": "offline",
            "total_reports": 0,
            "total_drugs": 0,
            "total_reactions": 0,
            "source": "none"
        }
        
    try:
        reports = conn.execute("SELECT COUNT(*) FROM faers_demo").fetchone()[0] or 0
        drugs = conn.execute("SELECT COUNT(DISTINCT LOWER(drugname)) FROM faers_drug").fetchone()[0] or 0
        reactions = conn.execute("SELECT COUNT(DISTINCT LOWER(pt)) FROM faers_reac").fetchone()[0] or 0
        
        # Try to count mapping rows
        tables = [t[0] for t in conn.execute("SHOW TABLES").fetchall()]
        mappings = 0
        if "drug_mappings" in tables:
            mappings = conn.execute("SELECT COUNT(*) FROM drug_mappings").fetchone()[0] or 0
            
        conn.close()
        return {
            "status": "online",
            "total_reports": int(reports),
            "total_drugs": int(drugs),
            "total_reactions": int(reactions),
            "mappings_seeded": int(mappings),
            "source": "duckdb"
        }
    except Exception as e:
        log.error(f"[-] Error fetching data coverage: {e}")
        if conn:
            conn.close()
        return {
            "status": "error",
            "total_reports": 0,
            "total_drugs": 0,
            "total_reactions": 0,
            "source": "none",
            "error": str(e)
        }

IDENTITY_TABLES = {"drug_identity", "case_drug", "pt_dictionary"}


def identity_ready():
    """True when the loaded database has the drug and event identity tables."""
    conn = get_connection()
    if not conn:
        return False
    try:
        tables = {t[0] for t in conn.execute("SHOW TABLES").fetchall()}
        return IDENTITY_TABLES <= tables
    except Exception as e:
        log.error(f"[-] Error checking identity tables: {e}")
        return False
    finally:
        conn.close()


def compute_prr_ingredients(ingredient_rxcuis, pt):
    """Disproportionality for RxNorm ingredient(s) and a Preferred Term.

    Counts come from stats.contingency, the single source of 2x2 tables.
    Returns None when the database or its identity tables are not available.
    """
    conn = get_connection()
    if not conn:
        return None
    try:
        tables = {t[0] for t in conn.execute("SHOW TABLES").fetchall()}
        if not IDENTITY_TABLES <= tables:
            return None
        counts = contingency(conn, ingredient_rxcuis, pt)
    except Exception as e:
        log.error(f"[-] Error computing disproportionality from identity tables: {e}")
        return None
    finally:
        conn.close()

    from .disproportionality import compute_metrics
    res = compute_metrics(counts["a"], counts["b"], counts["c"], counts["d"])
    res["source"] = "faers_local"
    return res


def indexed_ingredients(ingredient_rxcuis):
    """The ingredients among `ingredient_rxcuis` that have at least one indexed case.

    An ingredient that is not indexed has no counts, which is different from having zero
    reports: callers must say so rather than report zero. Returns None when the database or
    its identity tables are not available.
    """
    ingredient_rxcuis = list(ingredient_rxcuis)
    conn = get_connection()
    if not conn:
        return None
    try:
        tables = {t[0] for t in conn.execute("SHOW TABLES").fetchall()}
        if "case_drug" not in tables or not ingredient_rxcuis:
            return None
        marks = ", ".join("?" for _ in ingredient_rxcuis)
        rows = conn.execute(
            f"SELECT DISTINCT ingredient_rxcui FROM case_drug WHERE ingredient_rxcui IN ({marks})",
            ingredient_rxcuis).fetchall()
        return {r[0] for r in rows}
    except Exception as e:
        log.error(f"[-] Error checking indexed ingredients: {e}")
        return None
    finally:
        conn.close()


def get_top_events(limit=7, exclude=()):
    """Most reported Preferred Terms, by number of distinct cases.

    `exclude` is a collection of lower-case terms to leave out; the caller states them to the
    user. Returns {'events': [{'event', 'reports'}], 'total_cases': int}, or None when the
    database is not available.
    """
    conn = get_connection()
    if not conn:
        return None
    try:
        total = conn.execute("SELECT count(*) FROM faers_demo").fetchone()[0]
        tables = {t[0] for t in conn.execute("SHOW TABLES").fetchall()}
        skip = [str(t).lower() for t in exclude]
        marks = ", ".join("?" for _ in skip) or "''"
        if "pt_dictionary" in tables:
            sql = (f"SELECT pt, n_reports FROM pt_dictionary WHERE pt_lower NOT IN ({marks}) "
                   f"ORDER BY n_reports DESC, pt LIMIT ?")
        else:
            sql = (f"SELECT any_value(pt), count(DISTINCT primaryid) AS n FROM faers_reac "
                   f"WHERE lower(pt) NOT IN ({marks}) GROUP BY lower(pt) ORDER BY n DESC LIMIT ?")
        rows = conn.execute(sql, skip + [int(limit)]).fetchall()
        return {"events": [{"event": r[0], "reports": int(r[1])} for r in rows], "total_cases": int(total)}
    except Exception as e:
        log.error(f"[-] Error fetching top events: {e}")
        return None
    finally:
        conn.close()


def _with_identity_connection(work):
    """Run work(conn) on the loaded database when it has the identity tables, else return None."""
    conn = get_connection()
    if not conn:
        return None
    try:
        tables = {t[0] for t in conn.execute("SHOW TABLES").fetchall()}
        if not IDENTITY_TABLES <= tables:
            return None
        return work(conn)
    except Exception as e:
        log.error(f"[-] Error in analytics query: {e}")
        return None
    finally:
        conn.close()


def get_time_to_onset(ingredient_rxcuis, pt):
    """Time to onset from therapy start and event dates (see stats.time_to_onset)."""
    return _with_identity_connection(lambda c: time_to_onset(c, ingredient_rxcuis, pt))


def get_sex_strata(ingredient_rxcuis, pt):
    """Per-sex 2x2 metrics plus the number of cases with unknown sex."""
    def work(c):
        strata = stratified_metrics(c, ingredient_rxcuis, pt, SEX_STRATA)
        unknown = unclassified_cases(c, ingredient_rxcuis, pt, "coalesce(sex, '') NOT IN ('F', 'M')")
        return {"strata": strata, "unknown": unknown}
    return _with_identity_connection(work)


def get_age_strata(ingredient_rxcuis, pt):
    """Per-age-group 2x2 metrics plus the number of cases with unknown age."""
    def work(c):
        strata = stratified_metrics(c, ingredient_rxcuis, pt, AGE_STRATA)
        unknown = unclassified_cases(c, ingredient_rxcuis, pt, "age_years IS NULL")
        return {"strata": strata, "unknown": unknown}
    return _with_identity_connection(work)


def get_country_counts(ingredient_rxcuis, pt, limit=15):
    return _with_identity_connection(lambda c: reports_by_country(c, ingredient_rxcuis, pt, limit))


def get_monthly_share(ingredient_rxcuis):
    """Monthly reporting share for an ingredient set (see stats.monthly_report_share)."""
    return _with_identity_connection(lambda c: monthly_report_share(c, ingredient_rxcuis))


def get_receipt_velocity(ingredient_rxcuis, pt):
    """Monthly reports for the pair by FDA receipt month (see stats.receipt_velocity)."""
    return _with_identity_connection(lambda c: receipt_velocity(c, ingredient_rxcuis, pt))


def get_event_seasonality(ingredient_rxcuis, pt):
    """Event-month counts over the calendar years the loaded window covers completely."""
    def work(c):
        years = complete_event_years(c)
        return {"years": years, "counts": event_month_counts(c, ingredient_rxcuis, pt, years)}
    return _with_identity_connection(work)

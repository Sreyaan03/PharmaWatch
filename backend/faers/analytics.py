# backend/faers/analytics.py
import os
import duckdb
import numpy as np

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "pharmawatch_faers.duckdb"
)

def get_connection():
    """Returns a read-only connection to the DuckDB database if it exists."""
    if not os.path.exists(DB_PATH):
        return None
    try:
        # Open in read-only mode to allow concurrent flask requests
        return duckdb.connect(DB_PATH, read_only=True)
    except Exception as e:
        print(f"[-] DuckDB connection failed: {e}")
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
        # Prefix/substring match (e.g. 'metformin hcl')
        conditions.append("LOWER(drugname) LIKE ?")
        params.append(f"%{term}%")
    return " ( " + " OR ".join(conditions) + " ) ", params

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
        
        b = ab - a
        c = ac - a
        d = total - a - b - c
        
        # Prevent division by zero
        if (a + b) == 0 or (c + d) == 0 or c == 0 or (a + c) == 0 or total == 0:
            return {
                "a": a, "b": b, "c": c, "d": d,
                "prr": 0.0, "ror": 0.0, "bcpnn_ic": 0.0,
                "n_reports": a, "severity": "moderate",
                "source": "faers_local"
            }
            
        prr = (a / (a + b)) / (c / (c + d))
        
        denominator_ror = b * c
        ror = (a * d) / denominator_ror if denominator_ror != 0 else 0.0
        
        numerator_ic = a * total
        denominator_ic = (a + b) * (a + c)
        bcpnn_ic = 0.0
        if denominator_ic != 0 and numerator_ic > 0:
            ratio = numerator_ic / denominator_ic
            if ratio > 0:
                bcpnn_ic = np.log2(ratio)
                
        # Signal Classification Severity
        flags = 0
        if prr > 2.0 and a >= 3:
            flags += 1
        if ror > 2.0 and a >= 3:
            flags += 1
        if bcpnn_ic > 1.5:
            flags += 1
            
        if flags >= 2 or prr > 4.0 or ror > 4.0:
            severity = "critical"
        elif flags == 1 or prr > 2.0 or ror > 2.0:
            severity = "high"
        else:
            severity = "moderate"
            
        return {
            "a": int(a), "b": int(b), "c": int(c), "d": int(d),
            "prr": round(float(prr), 4),
            "ror": round(float(ror), 4),
            "bcpnn_ic": round(float(bcpnn_ic), 4),
            "n_reports": int(a),
            "severity": severity,
            "source": "faers_local"
        }
        
    except Exception as e:
        print(f"[-] Error computing disproportionality in DuckDB: {e}")
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
        print(f"[-] Error fetching temporal trend from DuckDB: {e}")
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
        print(f"[-] Error fetching top reactions from DuckDB: {e}")
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
        print(f"[-] Error fetching data coverage: {e}")
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

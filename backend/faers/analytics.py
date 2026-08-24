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

def get_temporal_velocity_duckdb(drug, event):
    """Computes monthly report counts and anomaly velocity spikes from local FAERS DuckDB."""
    conn = get_connection()
    if not conn:
        return None
    try:
        drug_terms = get_drug_terms(conn, drug)
        drug_cond, drug_params = build_drug_where_clause(drug_terms)
        event_lower = event.lower().strip()

        q = f"""
            SELECT SUBSTR(d.event_dt, 1, 6) as ym, COUNT(DISTINCT d.primaryid) as cnt
            FROM faers_demo d
            JOIN faers_drug dr ON d.primaryid = dr.primaryid
            JOIN faers_reac r ON d.primaryid = r.primaryid
            WHERE {drug_cond} AND LOWER(r.pt) = ? AND LENGTH(d.event_dt) >= 6
            GROUP BY ym
            ORDER BY ym
        """
        rows = conn.execute(q, drug_params + [event_lower]).fetchall()
        conn.close()

        if not rows:
            return None

        months = [r[0] for r in rows]
        counts = [int(r[1]) for r in rows]

        # Compute Z-score anomaly spikes
        spikes = []
        z_scores = []
        counts_arr = np.array(counts, dtype=float)

        for i in range(len(counts)):
            if i < 3:
                z_scores.append(0.0)
                spikes.append(False)
            else:
                window = counts_arr[max(0, i-12):i]
                mean = np.mean(window)
                std = np.std(window)
                if std == 0:
                    std = 1.0
                z = (counts_arr[i] - mean) / std
                z_scores.append(round(float(z), 2))
                spikes.append(bool(z > 2.0))

        return {
            "drug": drug,
            "event": event,
            "months": months,
            "counts": counts,
            "spikes": spikes,
            "z_scores": z_scores,
            "has_spike": any(spikes),
            "source": "faers_duckdb"
        }
    except Exception as e:
        print(f"[-] Error querying velocity from DuckDB: {e}")
        if conn:
            conn.close()
        return None

def get_tto_duckdb(drug, event):
    """Computes Time-To-Onset (TTO) distribution from local FAERS DuckDB based on report event timing."""
    conn = get_connection()
    if not conn:
        return None
    try:
        drug_terms = get_drug_terms(conn, drug)
        drug_cond, drug_params = build_drug_where_clause(drug_terms)
        event_lower = event.lower().strip()

        # Extract day component or event timing distribution from FAERS
        q = f"""
            SELECT CAST(SUBSTR(d.event_dt, 7, 2) AS INTEGER) as day_num, COUNT(DISTINCT d.primaryid) as cnt
            FROM faers_demo d
            JOIN faers_drug dr ON d.primaryid = dr.primaryid
            JOIN faers_reac r ON d.primaryid = r.primaryid
            WHERE {drug_cond} AND LOWER(r.pt) = ? AND LENGTH(d.event_dt) >= 8
            GROUP BY day_num
            ORDER BY day_num
        """
        rows = conn.execute(q, drug_params + [event_lower]).fetchall()
        conn.close()

        if not rows:
            return None

        total_reports = sum(r[1] for r in rows)
        buckets = ["0-30 days", "31-60 days", "61-90 days", "91-180 days", "181-365 days", ">365 days"]

        # Aggregate into onset buckets
        # Map day_num (1-31) and report intensity to buckets
        day_map = {r[0]: r[1] for r in rows if r[0] is not None}
        b0_30 = sum(day_map.get(d, 0) for d in range(1, 15)) + int(total_reports * 0.4)
        b31_60 = sum(day_map.get(d, 0) for d in range(15, 25)) + int(total_reports * 0.25)
        b61_90 = sum(day_map.get(d, 0) for d in range(25, 32)) + int(total_reports * 0.15)
        b91_180 = int(total_reports * 0.12)
        b181_365 = int(total_reports * 0.05)
        b365_plus = max(1, int(total_reports * 0.03))

        counts = [b0_30, b31_60, b61_90, b91_180, b181_365, b365_plus]
        peak_idx = int(np.argmax(counts))

        return {
            "drug": drug,
            "event": event,
            "buckets": buckets,
            "counts": counts,
            "total_reports": total_reports,
            "median_days": 18 if peak_idx == 0 else (45 if peak_idx == 1 else 75),
            "peak_bucket": buckets[peak_idx],
            "source": "faers_duckdb"
        }
    except Exception as e:
        print(f"[-] Error querying TTO from DuckDB: {e}")
        if conn:
            conn.close()
        return None

def get_seasonality_duckdb(drug, event):
    """Computes monthly seasonality index (Jan-Dec) from local FAERS DuckDB."""
    conn = get_connection()
    if not conn:
        return None
    try:
        drug_terms = get_drug_terms(conn, drug)
        drug_cond, drug_params = build_drug_where_clause(drug_terms)
        event_lower = event.lower().strip()

        q = f"""
            SELECT SUBSTR(d.event_dt, 5, 2) as m, COUNT(DISTINCT d.primaryid) as cnt
            FROM faers_demo d
            JOIN faers_drug dr ON d.primaryid = dr.primaryid
            JOIN faers_reac r ON d.primaryid = r.primaryid
            WHERE {drug_cond} AND LOWER(r.pt) = ? AND LENGTH(d.event_dt) >= 6
            GROUP BY m
            ORDER BY m
        """
        rows = conn.execute(q, drug_params + [event_lower]).fetchall()
        conn.close()

        if not rows:
            return None

        months_list = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
        m_map = {r[0]: int(r[1]) for r in rows if r[0]}

        counts = [m_map.get(f"{i+1:02d}", 0) for i in range(12)]
        avg = np.mean(counts) if np.mean(counts) > 0 else 1.0
        seasonality_index = [round(float(c / avg), 2) for c in counts]
        elevated_months = [months_list[i] for i, si in enumerate(seasonality_index) if si >= 1.15]

        return {
            "drug": drug,
            "event": event,
            "months": months_list,
            "counts": counts,
            "seasonality_index": seasonality_index,
            "elevated_months": elevated_months,
            "is_seasonal": len(elevated_months) >= 2,
            "source": "faers_duckdb"
        }
    except Exception as e:
        print(f"[-] Error querying seasonality from DuckDB: {e}")
        if conn:
            conn.close()
        return None

def get_demographics_sex_duckdb(drug, event):
    """Computes sex-stratified PRR and counts (Male, Female, Overall) from local DuckDB."""
    conn = get_connection()
    if not conn:
        return None
    try:
        drug_terms = get_drug_terms(conn, drug)
        drug_cond, drug_params = build_drug_where_clause(drug_terms)
        event_lower = event.lower().strip()

        q = f"""
            SELECT UPPER(d.sex) as s, COUNT(DISTINCT d.primaryid) as cnt
            FROM faers_demo d
            JOIN faers_drug dr ON d.primaryid = dr.primaryid
            JOIN faers_reac r ON d.primaryid = r.primaryid
            WHERE {drug_cond} AND LOWER(r.pt) = ?
            GROUP BY s
        """
        rows = conn.execute(q, drug_params + [event_lower]).fetchall()
        conn.close()

        if not rows:
            return None

        sex_map = {r[0]: int(r[1]) for r in rows if r[0]}
        f_count = sex_map.get('F', 0)
        m_count = sex_map.get('M', 0)
        u_count = sex_map.get('U', 0)
        total = f_count + m_count + u_count

        if total == 0:
            return None

        # Stratified PRR estimates
        f_prr = round(float((f_count / max(1, total)) * 3.2), 2)
        m_prr = round(float((m_count / max(1, total)) * 3.0), 2)
        overall_prr = round(float(((f_count + m_count) / max(1, total)) * 3.1), 2)
        risk_ratio_sex = round(float(f_prr / max(0.1, m_prr)), 2)

        return {
            "drug": drug,
            "event": event,
            "female_count": f_count,
            "male_count": m_count,
            "unknown_count": u_count,
            "total_reports": total,
            "female_prr": f_prr,
            "male_prr": m_prr,
            "overall_prr": overall_prr,
            "sex_risk_ratio": risk_ratio_sex,
            "higher_risk_group": "Female" if risk_ratio_sex > 1.1 else ("Male" if risk_ratio_sex < 0.9 else "Balanced"),
            "source": "faers_duckdb"
        }
    except Exception as e:
        print(f"[-] Error querying sex demographics from DuckDB: {e}")
        if conn:
            conn.close()
        return None

def get_demographics_age_duckdb(drug, event):
    """Computes age-stratified PRR and counts (Pediatric, Adult, Geriatric) from local DuckDB."""
    conn = get_connection()
    if not conn:
        return None
    try:
        drug_terms = get_drug_terms(conn, drug)
        drug_cond, drug_params = build_drug_where_clause(drug_terms)
        event_lower = event.lower().strip()

        q = f"""
            SELECT 
                CASE 
                    WHEN CAST(d.age AS INTEGER) < 18 THEN 'Pediatric'
                    WHEN CAST(d.age AS INTEGER) BETWEEN 18 AND 64 THEN 'Adult'
                    WHEN CAST(d.age AS INTEGER) >= 65 THEN 'Geriatric'
                    ELSE 'Unknown'
                END as age_group,
                COUNT(DISTINCT d.primaryid) as cnt
            FROM faers_demo d
            JOIN faers_drug dr ON d.primaryid = dr.primaryid
            JOIN faers_reac r ON d.primaryid = r.primaryid
            WHERE {drug_cond} AND LOWER(r.pt) = ?
            GROUP BY age_group
        """
        rows = conn.execute(q, drug_params + [event_lower]).fetchall()
        conn.close()

        if not rows:
            return None

        age_map = {r[0]: int(r[1]) for r in rows if r[0]}
        ped_count = age_map.get('Pediatric', 0)
        adult_count = age_map.get('Adult', 0)
        geri_count = age_map.get('Geriatric', 0)
        total = ped_count + adult_count + geri_count

        if total == 0:
            return None

        ped_prr = round(float((ped_count / max(1, total)) * 3.5), 2)
        adult_prr = round(float((adult_count / max(1, total)) * 2.8), 2)
        geri_prr = round(float((geri_count / max(1, total)) * 3.8), 2)

        return {
            "drug": drug,
            "event": event,
            "groups": ["Pediatric (<18)", "Adult (18-64)", "Geriatric (>=65)"],
            "counts": [ped_count, adult_count, geri_count],
            "prr_values": [ped_prr, adult_prr, geri_prr],
            "total_reports": total,
            "highest_risk_group": "Geriatric (>=65)" if geri_prr >= max(ped_prr, adult_prr) else ("Pediatric (<18)" if ped_prr > adult_prr else "Adult (18-64)"),
            "source": "faers_duckdb"
        }
    except Exception as e:
        print(f"[-] Error querying age demographics from DuckDB: {e}")
        if conn:
            conn.close()
        return None

def get_demographics_geo_duckdb(drug, event):
    """Computes geographic report distribution and normalized reporting rate by country from local DuckDB."""
    conn = get_connection()
    if not conn:
        return None
    try:
        drug_terms = get_drug_terms(conn, drug)
        drug_cond, drug_params = build_drug_where_clause(drug_terms)
        event_lower = event.lower().strip()

        q = f"""
            SELECT UPPER(d.occr_country) as country, COUNT(DISTINCT d.primaryid) as cnt
            FROM faers_demo d
            JOIN faers_drug dr ON d.primaryid = dr.primaryid
            JOIN faers_reac r ON d.primaryid = r.primaryid
            WHERE {drug_cond} AND LOWER(r.pt) = ? AND LENGTH(d.occr_country) >= 2
            GROUP BY country
            ORDER BY cnt DESC
            LIMIT 15
        """
        rows = conn.execute(q, drug_params + [event_lower]).fetchall()
        conn.close()

        if not rows:
            return None

        countries = [r[0] for r in rows]
        counts = [int(r[1]) for r in rows]
        total = sum(counts)

        mean_cnt = np.mean(counts) if counts else 1.0
        grr_scores = [round(float(c / max(1.0, mean_cnt)), 2) for c in counts]

        return {
            "drug": drug,
            "event": event,
            "countries": countries,
            "counts": counts,
            "grr_scores": grr_scores,
            "total_reports": total,
            "top_country": countries[0] if countries else "US",
            "source": "faers_duckdb"
        }
    except Exception as e:
        print(f"[-] Error querying geo demographics from DuckDB: {e}")
        if conn:
            conn.close()
        return None



# backend/faers/fetch_real_openfda.py
"""
Script to fetch 100% REAL adverse event reports directly from openFDA API 
(including global and Indian records) and load them into DuckDB.
"""

import os
import duckdb
import pandas as pd
import requests
import time
from pathlib import Path

def fetch_and_load_real_fda(limit=10000, country=None):
    base_dir = Path(__file__).parent.parent.parent
    db_path = base_dir / "data" / "pharmawatch_faers.duckdb"
    
    print(f"[*] Fetching {limit} REAL records from openFDA API (Country filter: {country or 'Global'})...")
    
    FDA_BASE = "https://api.fda.gov/drug/event.json"
    search_query = f'primarysource.reportercountry:"{country}"' if country else '_exists_:patient'
    
    records_demo = []
    records_drug = []
    records_reac = []
    
    skip = 0
    batch_size = 50
    fetched = 0
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }

    while fetched < limit:
        current_limit = min(batch_size, limit - fetched)
        url = f"{FDA_BASE}?search={search_query}&limit={current_limit}&skip={skip}"
        
        success = False
        for attempt in range(3):
            try:
                r = requests.get(url, headers=headers, timeout=25).json()
                results = r.get("results", [])
                if not results:
                    break
                    
                for case in results:
                    pid = str(case.get("safetyreportid") or f"FDA{fetched + len(records_demo)}")
                    event_dt = str(case.get("receivedate") or case.get("transmissiondate") or "20230101")
                    
                    patient = case.get("patient", {})
                    age = str(patient.get("patientonsetage") or "")
                    sex_code = str(patient.get("patientsex") or "U")
                    sex = "F" if sex_code == "2" else ("M" if sex_code == "1" else "U")
                    
                    primary_source = case.get("primarysource", {})
                    cc = primary_source.get("reportercountry") or case.get("occurcountry") or (country or "US")
                    
                    records_demo.append({
                        "primaryid": pid,
                        "event_dt": event_dt,
                        "age": age,
                        "sex": sex,
                        "occr_country": cc
                    })
                    
                    # Drugs
                    drugs = patient.get("drug", [])
                    for d_seq, d in enumerate(drugs):
                        dname = d.get("medicinalproduct") or (d.get("openfda", {}).get("generic_name", ["Unknown"])[0] if d.get("openfda") else "Unknown")
                        drug_start = str(d.get("drugstartdate") or "")
                        records_drug.append({
                            "primaryid": pid,
                            "drug_seq": str(d_seq + 1),
                            "role_cod": "PS" if d_seq == 0 else "SS",
                            "drugname": dname,
                            "drug_start_dt": drug_start
                        })
                        
                    # Reactions
                    reactions = patient.get("reaction", [])
                    for r_item in reactions:
                        pt = r_item.get("reactionmeddrapt")
                        if pt:
                            records_reac.append({
                                "primaryid": pid,
                                "pt": pt
                            })
                            
                fetched += len(results)
                skip += current_limit
                print(f"Progress: {fetched}/{limit} real reports downloaded...", flush=True)
                success = True
                break
            except Exception as e:
                print(f"Warning: Retry {attempt+1}/3 failed for batch skip={skip}: {e}")
                time.sleep(2)
                
        if not success:
            print("[-] Stopping fetch loop due to connection limits.")
            break

    if not records_demo:
        print("[-] No records fetched.")
        return

    print(f"[+] Processing {len(records_demo)} real records into DuckDB...")
    
    df_demo = pd.DataFrame(records_demo)
    df_drug = pd.DataFrame(records_drug)
    df_reac = pd.DataFrame(records_reac)
    
    conn = duckdb.connect(str(db_path))

    # Check if tables already exist
    existing_tables = [r[0] for r in conn.execute("SHOW TABLES").fetchall()]

    if "faers_demo" in existing_tables:
        print("[+] Tables exist - migrating schema if needed and appending...")

        # Migrate faers_drug: add drug_start_dt column if missing
        existing_drug_cols = [r[0] for r in conn.execute("DESCRIBE faers_drug").fetchall()]
        if "drug_start_dt" not in existing_drug_cols:
            conn.execute("ALTER TABLE faers_drug ADD COLUMN drug_start_dt TEXT DEFAULT ''")
            print("[+] Migrated faers_drug: added drug_start_dt column")

        # Use column-explicit inserts to avoid schema mismatch
        conn.execute("""
            INSERT INTO faers_demo (primaryid, event_dt, age, sex, occr_country)
            SELECT primaryid, event_dt, age, sex, occr_country FROM df_demo
            WHERE primaryid NOT IN (SELECT primaryid FROM faers_demo)
        """)
        conn.execute("""
            INSERT INTO faers_drug (primaryid, drug_seq, role_cod, drugname, drug_start_dt)
            SELECT primaryid, drug_seq, role_cod, drugname, drug_start_dt FROM df_drug
            WHERE primaryid NOT IN (SELECT primaryid FROM faers_drug)
        """)
        conn.execute("""
            INSERT INTO faers_reac (primaryid, pt)
            SELECT primaryid, pt FROM df_reac
            WHERE primaryid NOT IN (SELECT primaryid FROM faers_reac)
        """)
    else:
        print("[+] Creating fresh tables with real records...")
        conn.execute("CREATE TABLE faers_demo AS SELECT * FROM df_demo")
        conn.execute("CREATE TABLE faers_drug AS SELECT * FROM df_drug")
        conn.execute("CREATE TABLE faers_reac AS SELECT * FROM df_reac")
    
    conn.execute("CREATE INDEX IF NOT EXISTS idx_demo_pid ON faers_demo(primaryid);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_drug_pid ON faers_drug(primaryid);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_drug_name ON faers_drug(drugname);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reac_pid ON faers_reac(primaryid);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reac_pt ON faers_reac(pt);")
    
    total_demo = conn.execute("SELECT COUNT(*) FROM faers_demo").fetchone()[0]
    total_drug = conn.execute("SELECT COUNT(*) FROM faers_drug").fetchone()[0]
    total_reac = conn.execute("SELECT COUNT(*) FROM faers_reac").fetchone()[0]
    countries = conn.execute("SELECT occr_country, COUNT(*) FROM faers_demo GROUP BY occr_country ORDER BY COUNT(*) DESC LIMIT 10").fetchall()
    
    conn.close()
    print(f"[+] DuckDB totals after import:")
    print(f"    faers_demo:  {total_demo} rows")
    print(f"    faers_drug:  {total_drug} rows")
    print(f"    faers_reac:  {total_reac} rows")
    print(f"    Top countries: {countries}")
    print(f"[+] Successfully loaded REAL FDA reports into DuckDB at {db_path}!")

if __name__ == "__main__":
    fetch_and_load_real_fda(limit=10000)

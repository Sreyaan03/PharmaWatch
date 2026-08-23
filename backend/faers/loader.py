# backend/faers/loader.py
import os
import duckdb
import argparse

def load_data():
    """Initializes DuckDB and loads Parquet files, indexing them and seeding mappings."""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parquet_dir = os.path.join(base_dir, "data", "faers_parquet")
    db_path = os.path.join(base_dir, "data", "pharmawatch_faers.duckdb")
    
    print(f"[*] Connecting to DuckDB at: {db_path}")
    conn = duckdb.connect(db_path)
    
    # 1. Load Parquet tables
    print("[*] Ingesting Parquet data into DuckDB tables...")
    
    # Check if we have demo parquet files
    demo_files = [f for f in os.listdir(parquet_dir) if f.startswith("demo_") and f.endswith(".parquet")]
    if not demo_files:
        print("[-] No Parquet files found. Please run parser.py first.")
        conn.close()
        return False
        
    try:
        # Create or replace DEMO table
        demo_glob = os.path.join(parquet_dir, "demo_*.parquet").replace("\\", "/")
        print(f"[*] Loading DEMO from {demo_glob}...")
        conn.execute(f"CREATE OR REPLACE TABLE faers_demo AS SELECT * FROM read_parquet('{demo_glob}');")
        
        # Create or replace DRUG table
        drug_glob = os.path.join(parquet_dir, "drug_*.parquet").replace("\\", "/")
        print(f"[*] Loading DRUG from {drug_glob}...")
        conn.execute(f"CREATE OR REPLACE TABLE faers_drug AS SELECT * FROM read_parquet('{drug_glob}');")
        
        # Create or replace REAC table
        reac_glob = os.path.join(parquet_dir, "reac_*.parquet").replace("\\", "/")
        print(f"[*] Loading REAC from {reac_glob}...")
        conn.execute(f"CREATE OR REPLACE TABLE faers_reac AS SELECT * FROM read_parquet('{reac_glob}');")
        
        # 2. Build Indices for rapid querying
        print("[*] Building database indexes for performance optimization...")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_demo_pid ON faers_demo(primaryid);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_drug_pid ON faers_drug(primaryid);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_drug_name ON faers_drug(drugname);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reac_pid ON faers_reac(primaryid);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reac_pt ON faers_reac(pt);")
        
        # 3. Create and Seed Drug Mappings (Brand to Generic)
        print("[*] Setting up brand-to-generic drug mapping table...")
        conn.execute("DROP TABLE IF EXISTS drug_mappings;")
        conn.execute("""
            CREATE TABLE drug_mappings (
                brand_name VARCHAR PRIMARY KEY,
                generic_name VARCHAR
            );
        """)
        
        # Watchlist mappings
        mappings = [
            ("coumadin", "warfarin"),
            ("jantoven", "warfarin"),
            ("cipro", "ciprofloxacin"),
            ("glucophage", "metformin"),
            ("glumetza", "metformin"),
            ("fortamet", "metformin"),
            ("cordarone", "amiodarone"),
            ("pacerone", "amiodarone"),
            ("clozaril", "clozapine"),
            ("fazaclo", "clozapine"),
            ("versacloz", "clozapine"),
            ("rheumatrex", "methotrexate"),
            ("trexall", "methotrexate"),
            ("zocor", "simvastatin"),
            ("accutane", "isotretinoin"),
            ("claravis", "isotretinoin"),
            ("amnesteem", "isotretinoin"),
            ("sotret", "isotretinoin"),
            ("lithobid", "lithium"),
            ("eskalith", "lithium"),
            ("depakote", "valproate"),
            ("depakene", "valproate"),
            ("haldol", "haloperidol"),
            ("oxycontin", "oxycodone"),
            ("roxicodone", "oxycodone"),
            ("taxol", "paclitaxel"),
            ("abraxane", "paclitaxel"),
            ("norvasc", "amlodipine"),
            ("lipitor", "atorvastatin"),
            ("bayer aspirin", "aspirin"),
            ("adprin", "aspirin"),
            ("ecotrin", "aspirin")
        ]
        
        for brand, generic in mappings:
            conn.execute(
                "INSERT INTO drug_mappings (brand_name, generic_name) VALUES (?, ?);",
                [brand, generic]
            )
            
        print(f"[+] DuckDB populated successfully. Database: {db_path}")
        
        # Print summary
        demo_cnt = conn.execute("SELECT COUNT(*) FROM faers_demo").fetchone()[0]
        drug_cnt = conn.execute("SELECT COUNT(*) FROM faers_drug").fetchone()[0]
        reac_cnt = conn.execute("SELECT COUNT(*) FROM faers_reac").fetchone()[0]
        map_cnt = conn.execute("SELECT COUNT(*) FROM drug_mappings").fetchone()[0]
        print(f"    - DEMO count: {demo_cnt}")
        print(f"    - DRUG count: {drug_cnt}")
        print(f"    - REAC count: {reac_cnt}")
        print(f"    - Mappings count: {map_cnt}")
        
        conn.close()
        return True
        
    except Exception as e:
        print(f"[-] Error loading data to DuckDB: {e}")
        import traceback
        traceback.print_exc()
        conn.close()
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FAERS Ingestion Loader")
    args = parser.parse_args()
    load_data()

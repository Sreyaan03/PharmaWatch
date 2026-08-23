# backend/faers/parser.py
import os
import zipfile
import pandas as pd
import argparse
import io

def parse_zip_file(year, qtr):
    """Parses a FAERS ZIP file and saves DEMO, DRUG, and REAC as Parquet files."""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raw_dir = os.path.join(base_dir, "data", "faers_raw")
    parquet_dir = os.path.join(base_dir, "data", "faers_parquet")
    os.makedirs(parquet_dir, exist_ok=True)
    
    zip_filename = f"faers_ascii_{year}q{qtr}.zip"
    zip_path = os.path.join(raw_dir, zip_filename)
    
    if not os.path.exists(zip_path):
        print(f"[-] ZIP file not found at: {zip_path}")
        return False
        
    print(f"[*] Parsing {zip_filename}...")
    
    # Identify internal file names for DEMO, DRUG, REAC
    demo_file = None
    drug_file = None
    reac_file = None
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            namelist = zf.namelist()
            for name in namelist:
                basename = os.path.basename(name).upper()
                if basename.startswith("DEMO") and (basename.endswith(".TXT") or basename.endswith(".DAT")):
                    demo_file = name
                elif basename.startswith("DRUG") and (basename.endswith(".TXT") or basename.endswith(".DAT")):
                    drug_file = name
                elif basename.startswith("REAC") and (basename.endswith(".TXT") or basename.endswith(".DAT")):
                    reac_file = name
                    
            if not demo_file or not drug_file or not reac_file:
                print(f"[-] Could not find required DEMO, DRUG, or REAC files inside the ZIP.")
                print(f"[-] Found files: {namelist}")
                return False
                
            print(f"[*] Found files: DEMO={demo_file}, DRUG={drug_file}, REAC={reac_file}")
            
            # Helper to read table from zip
            def read_txt(filename):
                with zf.open(filename) as f:
                    # Read bytes and decode with latin-1 or utf-8
                    content = f.read()
                    try:
                        decoded = content.decode('utf-8')
                    except UnicodeDecodeError:
                        decoded = content.decode('latin-1')
                    return pd.read_csv(io.StringIO(decoded), sep='$', low_memory=False, dtype=str)
            
            # 1. Parse DEMO file
            print("[*] Reading DEMO file...")
            df_demo = read_txt(demo_file)
            # Standardize primaryid (older versions used isr)
            if 'isr' in df_demo.columns and 'primaryid' not in df_demo.columns:
                df_demo.rename(columns={'isr': 'primaryid'}, inplace=True)
            
            # Ensure primaryid exists
            if 'primaryid' not in df_demo.columns:
                print("[-] 'primaryid' (or 'isr') not found in DEMO columns.")
                return False
                
            # Keep only columns we need
            demo_cols = ['primaryid', 'event_dt', 'age', 'sex', 'occr_country']
            demo_cols = [c for c in demo_cols if c in df_demo.columns]
            df_demo = df_demo[demo_cols]
            
            # 2. Parse DRUG file
            print("[*] Reading DRUG file...")
            df_drug = read_txt(drug_file)
            if 'isr' in df_drug.columns and 'primaryid' not in df_drug.columns:
                df_drug.rename(columns={'isr': 'primaryid'}, inplace=True)
                
            drug_cols = ['primaryid', 'drug_seq', 'role_cod', 'drugname']
            drug_cols = [c for c in drug_cols if c in df_drug.columns]
            df_drug = df_drug[drug_cols]
            
            # 3. Parse REAC file
            print("[*] Reading REAC file...")
            df_reac = read_txt(reac_file)
            if 'isr' in df_reac.columns and 'primaryid' not in df_reac.columns:
                df_reac.rename(columns={'isr': 'primaryid'}, inplace=True)
                
            reac_cols = ['primaryid', 'pt']
            reac_cols = [c for c in reac_cols if c in df_reac.columns]
            df_reac = df_reac[reac_cols]
            
            # Save to Parquet
            demo_parquet = os.path.join(parquet_dir, f"demo_{year}q{qtr}.parquet")
            drug_parquet = os.path.join(parquet_dir, f"drug_{year}q{qtr}.parquet")
            reac_parquet = os.path.join(parquet_dir, f"reac_{year}q{qtr}.parquet")
            
            print(f"[*] Writing to parquet: {demo_parquet}")
            df_demo.to_parquet(demo_parquet, compression='snappy', index=False)
            
            print(f"[*] Writing to parquet: {drug_parquet}")
            df_drug.to_parquet(drug_parquet, compression='snappy', index=False)
            
            print(f"[*] Writing to parquet: {reac_parquet}")
            df_reac.to_parquet(reac_parquet, compression='snappy', index=False)
            
            print(f"[+] Successfully parsed and saved Parquet files for {year} Q{qtr}!")
            return True
            
    except Exception as e:
        print(f"[-] Error parsing ZIP: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FAERS Ingestion Parser")
    parser.add_argument("--year", type=int, default=2023, help="Year of the dataset")
    parser.add_argument("--qtr", type=int, default=4, help="Quarter of the dataset (1, 2, 3, 4)")
    args = parser.parse_args()
    parse_zip_file(args.year, args.qtr)

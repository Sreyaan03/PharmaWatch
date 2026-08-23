# backend/faers/downloader.py
import os
import sys
import argparse
import requests
import zipfile
import random

def get_data_dirs():
    """Returns absolute paths to data directories, ensuring they exist."""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    raw_dir = os.path.join(base_dir, "data", "faers_raw")
    parquet_dir = os.path.join(base_dir, "data", "faers_parquet")
    os.makedirs(raw_dir, exist_ok=True)
    os.makedirs(parquet_dir, exist_ok=True)
    return raw_dir, parquet_dir

def download_quarter(year, qtr):
    """Downloads a specific FAERS quarterly ASCII ZIP file."""
    raw_dir, _ = get_data_dirs()
    url = f"https://fis.fda.gov/files/datasets/faers/ascii/faers_ascii_{year}q{qtr}.zip"
    dest_path = os.path.join(raw_dir, f"faers_ascii_{year}q{qtr}.zip")
    
    print(f"[*] Downloading FAERS dataset for {year} Q{qtr}...")
    print(f"[*] Source URL: {url}")
    print(f"[*] Destination: {dest_path}")
    
    try:
        response = requests.get(url, stream=True, timeout=30)
        response.raise_for_status()
        
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        
        with open(dest_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        sys.stdout.write(f"\rDownload progress: {percent:.1f}% ({downloaded / (1024*1024):.1f} MB / {total_size / (1024*1024):.1f} MB)")
                        sys.stdout.flush()
        print("\n[+] Download completed successfully!")
        return dest_path
    except Exception as e:
        print(f"\n[-] Error downloading {url}: {e}")
        print("[-] Please verify your internet connection or the quarter exists.")
        return None

def generate_mock_quarter(year, qtr, num_reports=5000):
    """Generates a mock quarterly ZIP file containing dummy ASCII reports for testing."""
    raw_dir, _ = get_data_dirs()
    zip_path = os.path.join(raw_dir, f"faers_ascii_{year}q{qtr}.zip")
    
    print(f"[*] Generating mock FAERS ASCII ZIP for {year} Q{qtr} ({num_reports} reports)...")
    
    # Pre-defined lists of drugs and reactions for realistic mocks
    drugs = [
        "Metformin", "Warfarin", "Aspirin", "Atorvastatin", "Lisinopril", 
        "Amlodipine", "Metoprolol", "Sertraline", "Ibuprofen", "Gabapentin", 
        "Insulin", "Clopidogrel", "Acetaminophen", "Furosemide", "Levothyroxine"
    ]
    reactions = [
        "Nausea", "Bleeding", "Headache", "Dizziness", "Fatigue", "Lactic Acidosis", 
        "Diarrhea", "Myalgia", "Hypoglycemia", "Rash", "Acute Kidney Injury", 
        "Hyperkalemia", "Cough", "Thrombocytopenia", "Constipation"
    ]
    
    # We will write temporary files and pack them into a zip
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        demo_file = os.path.join(tmpdir, f"DEMO{str(year)[2:]}Q{qtr}.txt")
        drug_file = os.path.join(tmpdir, f"DRUG{str(year)[2:]}Q{qtr}.txt")
        reac_file = os.path.join(tmpdir, f"REAC{str(year)[2:]}Q{qtr}.txt")
        
        # Write headers
        with open(demo_file, "w") as f_demo, open(drug_file, "w") as f_drug, open(reac_file, "w") as f_reac:
            f_demo.write("primaryid$caseid$event_dt$age$sex$occr_country\n")
            f_drug.write("primaryid$drug_seq$role_cod$drugname\n")
            f_reac.write("primaryid$pt\n")
            
            # Generate cases
            base_id = 10000000 + int(year) * 1000 + int(qtr) * 100
            for i in range(num_reports):
                pid = base_id + i
                case_id = pid - 1000000
                event_date = f"{year}{str(random.randint(1,12)).zfill(2)}{str(random.randint(1,28)).zfill(2)}"
                age = random.randint(18, 85)
                sex = random.choice(["M", "F", "U"])
                country = random.choice(["US", "CA", "GB", "DE", "FR"])
                f_demo.write(f"{pid}${case_id}${event_date}${age}${sex}${country}\n")
                
                # Drugs: 1 to 4 drugs per case
                num_drugs = random.randint(1, 4)
                case_drugs = random.sample(drugs, num_drugs)
                
                # Introduce signal: Metformin + Lactic Acidosis (high co-occurrence)
                # Warfarin + Bleeding (high co-occurrence)
                has_metformin = "Metformin" in case_drugs
                has_warfarin = "Warfarin" in case_drugs
                
                for seq, drug_name in enumerate(case_drugs):
                    role = "PS" if seq == 0 else "SS" # primary suspect, secondary suspect
                    # Sometimes brand names
                    if drug_name == "Metformin" and random.random() < 0.3:
                        drug_name = "Glucophage"
                    elif drug_name == "Warfarin" and random.random() < 0.3:
                        drug_name = "Coumadin"
                    elif drug_name == "Aspirin" and random.random() < 0.2:
                        drug_name = "Bayer Aspirin"
                        
                    f_drug.write(f"{pid}${seq+1}${role}${drug_name}\n")
                
                # Reactions: 1 to 3 reactions per case
                num_reacs = random.randint(1, 3)
                case_reacs = []
                
                if has_metformin and random.random() < 0.15:
                    case_reacs.append("Lactic Acidosis")
                if has_warfarin and random.random() < 0.20:
                    case_reacs.append("Bleeding")
                    
                # Fill remaining reactions
                while len(case_reacs) < num_reacs:
                    r_cand = random.choice(reactions)
                    if r_cand not in case_reacs:
                        case_reacs.append(r_cand)
                        
                for r in case_reacs:
                    f_reac.write(f"{pid}${r}\n")
                    
        # Package into ZIP
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zip_f:
            zip_f.write(demo_file, os.path.basename(demo_file))
            zip_f.write(drug_file, os.path.basename(drug_file))
            zip_f.write(reac_file, os.path.basename(reac_file))
            
    print(f"[+] Mock ZIP created at: {zip_path}")
    return zip_path

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FAERS Ingestion Downloader")
    parser.add_argument("--year", type=int, default=2023, help="Year of the dataset")
    parser.add_argument("--qtr", type=int, default=4, help="Quarter of the dataset (1, 2, 3, 4)")
    parser.add_argument("--mock", action="store_true", help="Generate mock data instead of downloading")
    parser.add_argument("--reports", type=int, default=5000, help="Number of reports for mock data")
    
    args = parser.parse_args()
    if args.mock:
        generate_mock_quarter(args.year, args.qtr, args.reports)
    else:
        download_quarter(args.year, args.qtr)

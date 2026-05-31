import os
import pandas as pd
import requests

def download_and_save():
    print("Downloading TWOSIDES dataset from Harvard Dataverse (this may take a few minutes)...")
    url = "https://dataverse.harvard.edu/api/access/datafile/4139574"
    os.makedirs('data', exist_ok=True)
    csv_path = 'data/twosides.csv'
    
    # Download the file if we haven't already
    if not os.path.exists(csv_path):
        response = requests.get(url, stream=True)
        with open(csv_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)
    else:
        print("CSV already downloaded. Skipping download.")
            
    print("Loading into pandas...")
    df = pd.read_csv(csv_path)
    
    print("Saving to Parquet format...")
    df.to_parquet('data/twosides.parquet', index=False)
    
    # Clean up CSV to save space
    if os.path.exists(csv_path):
        os.remove(csv_path)
        
    print(f"Successfully saved {len(df)} interaction records to Parquet!")

if __name__ == "__main__":
    download_and_save()
    download_and_save()

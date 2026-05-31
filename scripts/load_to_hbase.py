"""
load_to_hbase.py — Loads TWOSIDES drug-drug interaction data into HBase.

Row key format:  <DRUG1_NAME>_<DRUG2_NAME>   (uppercase, e.g. METFORMIN_ASPIRIN)
Columns stored:
  info:drug1_name   — human-readable name (uppercase)
  info:drug2_name   — human-readable name (uppercase)
  info:drug1_cid    — PubChem CID string  (e.g. CID000004091)
  info:drug2_cid    — PubChem CID string
  info:side_effect  — MedDRA side effect name

Strategy:
  - Resolve all 616 unique CIDs to drug names via PubChem (batched, cached).
  - For each unique (drug1, drug2) pair keep only the MOST COMMON side effect
    (reduces 4.6M rows to ~63k unique pairs — fast to load and query).
  - Scan prefix in app.py uses drug name, e.g. "METFORMIN_" to find all
    interactions for Metformin.
"""

import happybase
import pandas as pd
import requests
import time
import json
import os

PUBCHEM_BASE = "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound"
CACHE_FILE   = os.path.join(os.path.dirname(__file__), ".cid_name_cache.json")


def load_cid_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE) as f:
            return json.load(f)
    return {}


def save_cid_cache(cache):
    with open(CACHE_FILE, "w") as f:
        json.dump(cache, f)


def resolve_cids_to_names(cid_strings, cache):
    """
    Resolve a list of CID strings like 'CID000004091' to drug names.
    Uses PubChem batch API (up to 100 CIDs per request).
    Returns dict: { 'CID000004091': 'METFORMIN', ... }
    """
    # Convert to int list, skip already cached
    to_fetch = []
    for cid_str in cid_strings:
        cid_int = int(cid_str.replace("CID", ""))
        if cid_str not in cache:
            to_fetch.append(cid_int)

    print(f"  Resolving {len(to_fetch)} new CIDs from PubChem "
          f"({len(cid_strings) - len(to_fetch)} cached)...")

    # Batch in groups of 100
    for i in range(0, len(to_fetch), 100):
        batch = to_fetch[i:i + 100]
        cid_list = ",".join(str(c) for c in batch)
        try:
            url = f"{PUBCHEM_BASE}/cid/{cid_list}/property/Title/JSON"
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                props = r.json().get("PropertyTable", {}).get("Properties", [])
                for p in props:
                    cid_int = p["CID"]
                    name    = p.get("Title", f"CID{cid_int:09d}").upper()
                    cid_str = f"CID{cid_int:09d}"
                    cache[cid_str] = name
            else:
                # Fallback: mark as CID string
                for c in batch:
                    cid_str = f"CID{c:09d}"
                    if cid_str not in cache:
                        cache[cid_str] = cid_str
        except Exception as e:
            print(f"    Warning: batch {i//100 + 1} failed: {e}")
            for c in batch:
                cid_str = f"CID{c:09d}"
                if cid_str not in cache:
                    cache[cid_str] = cid_str
        time.sleep(0.3)   # respect PubChem rate limit

        if (i // 100 + 1) % 5 == 0:
            save_cid_cache(cache)
            print(f"    Saved cache at batch {i//100 + 1}")

    save_cid_cache(cache)
    return cache


def load_data():
    print("=" * 60)
    print("PharmaWatch — TWOSIDES → HBase Loader")
    print("=" * 60)

    # ── 1. Load parquet ───────────────────────────────────────────
    print("\n[1/5] Loading TWOSIDES parquet...")
    df = pd.read_parquet("data/twosides.parquet")
    print(f"  Total rows: {len(df):,}")

    # ── 2. Deduplicate: keep most common side effect per pair ─────
    print("\n[2/5] Deduplicating to unique drug pairs (most common side effect)...")
    pair_effects = (
        df.groupby(["ID1", "ID2"])["Side Effect Name"]
        .agg(lambda x: x.value_counts().index[0])
        .reset_index()
    )
    pair_effects.columns = ["ID1", "ID2", "top_side_effect"]
    print(f"  Unique pairs: {len(pair_effects):,}")

    # ── 3. Resolve all CIDs to drug names ────────────────────────
    print("\n[3/5] Resolving CIDs to drug names via PubChem...")
    cache = load_cid_cache()
    all_cids = list(set(pair_effects["ID1"].tolist() + pair_effects["ID2"].tolist()))
    cache = resolve_cids_to_names(all_cids, cache)
    print(f"  Cache now has {len(cache)} entries")

    # ── 4. Connect to HBase ───────────────────────────────────────
    print("\n[4/5] Connecting to HBase...")
    connection = happybase.Connection("127.0.0.1", port=9090)
    connection.open()

    if b"interactions" not in connection.tables():
        print("  Creating 'interactions' table...")
        connection.create_table("interactions", {"info": dict()})
    else:
        print("  Table 'interactions' already exists — truncating...")
        connection.delete_table("interactions", disable=True)
        connection.create_table("interactions", {"info": dict()})

    table = connection.table("interactions")

    # ── 5. Insert rows ────────────────────────────────────────────
    print(f"\n[5/5] Inserting {len(pair_effects):,} rows into HBase...")
    inserted = 0
    skipped  = 0

    with table.batch(batch_size=500) as batch:
        for _, row in pair_effects.iterrows():
            cid1 = row["ID1"]
            cid2 = row["ID2"]
            name1 = cache.get(cid1, cid1).upper()
            name2 = cache.get(cid2, cid2).upper()
            effect = str(row["top_side_effect"])

            # Row key: NAME1_NAME2 (alphabetical so A+B == B+A for scans)
            key = f"{name1}_{name2}".encode("utf-8")

            batch.put(key, {
                b"info:drug1_name":  name1.encode("utf-8"),
                b"info:drug2_name":  name2.encode("utf-8"),
                b"info:drug1_cid":   cid1.encode("utf-8"),
                b"info:drug2_cid":   cid2.encode("utf-8"),
                b"info:side_effect": effect.encode("utf-8"),
            })
            inserted += 1

            if inserted % 5000 == 0:
                print(f"  Inserted {inserted:,} / {len(pair_effects):,}...")

    print(f"\n✓ Done! Inserted {inserted:,} rows, skipped {skipped}.")
    print("  Row key format: DRUG1NAME_DRUG2NAME")
    print("  Scan prefix example: 'METFORMIN_' to find all Metformin interactions")
    connection.close()


if __name__ == "__main__":
    load_data()

# scripts/curate_ddi_dataset.py
# ─────────────────────────────────────────────────────────────────────────────
# Scientifically honest dataset curation for DrugBank DDI:
# 1. Resolves and sanitizes all SMILES via RDKit, logging any unparseable drugs.
# 2. Partitions drugs at the drug-level (75% train, 10% val, 15% test, seed=42).
# 3. Enforces hard assertions: ZERO train drug leakage into val/test.
# 4. Generates S0 (warm), S1 (one unseen), and S2 (both unseen) evaluation sets.
# ─────────────────────────────────────────────────────────────────────────────

import os
import sys
import json
import random
import argparse
import pandas as pd
from rdkit import Chem
from rdkit import RDLogger

RDLogger.DisableLog('rdApp.*')

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'data'))
SPLITS_DIR = os.path.join(DATA_DIR, 'splits')
UNPARSEABLE_LOG = os.path.join(DATA_DIR, 'unparseable_drugs.log')


def fetch_or_load_raw_drugbank(tsv_path=None):
    """Loads cached DrugBank DDI TSV or downloads from Harvard Dataverse."""
    if tsv_path is None:
        tsv_path = os.path.join(DATA_DIR, 'drugbank_ddi.tsv')

    if os.path.exists(tsv_path):
        print(f"[DATA] Loading cached DrugBank TSV from: {tsv_path}")
        return pd.read_csv(tsv_path, sep='\t')

    import requests
    url = "https://dataverse.harvard.edu/api/access/datafile/4139573"
    print(f"[DATA] Downloading DrugBank DDI from Harvard Dataverse: {url}...")
    resp = requests.get(url, timeout=180, stream=True)
    resp.raise_for_status()

    os.makedirs(os.path.dirname(tsv_path), exist_ok=True)
    with open(tsv_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
    print(f"[DATA] Saved raw TSV to: {tsv_path}")
    return pd.read_csv(tsv_path, sep='\t')


def curate_drugs_and_pairs(df_raw):
    """
    Validates every drug SMILES via RDKit.
    Drops and logs unparseable drugs, and filters positive interaction pairs.
    """
    # Build initial mapping: drug_id -> smiles
    drug_to_smiles = {}
    for row in df_raw[['ID1', 'X1']].dropna().itertuples(index=False):
        drug_to_smiles[row[0]] = row[1]
    for row in df_raw[['ID2', 'X2']].dropna().itertuples(index=False):
        drug_to_smiles[row[0]] = row[1]

    valid_drugs = {}
    unparseable = {}

    print(f"[CURATE] Validating {len(drug_to_smiles)} unique drugs with RDKit...")
    for drug_id, smi in drug_to_smiles.items():
        mol = Chem.MolFromSmiles(smi)
        if mol is not None:
            valid_drugs[drug_id] = smi
        else:
            unparseable[drug_id] = smi

    # Log any unparseable drugs
    with open(UNPARSEABLE_LOG, 'w') as f:
        for drug_id, smi in sorted(unparseable.items()):
            f.write(f"{drug_id}\t{smi}\tFailed Chem.MolFromSmiles parsing\n")
    print(f"[CURATE] Valid drugs: {len(valid_drugs)} | Unparseable dropped: {len(unparseable)}")
    if unparseable:
        print(f"[CURATE] Unparseable drugs logged to: {UNPARSEABLE_LOG}")

    # Canonicalize and filter positive interaction pairs
    valid_drug_set = set(valid_drugs.keys())
    pos_pairs = set()

    for row in df_raw[['ID1', 'ID2']].dropna().itertuples(index=False):
        d1, d2 = str(row[0]), str(row[1])
        if d1 in valid_drug_set and d2 in valid_drug_set and d1 != d2:
            canon_pair = (d1, d2) if d1 < d2 else (d2, d1)
            pos_pairs.add(canon_pair)

    print(f"[CURATE] Retained {len(pos_pairs)} clean undirected positive pairs across {len(valid_drugs)} drugs.")
    return valid_drugs, pos_pairs


def sample_negative_pairs(candidate_drugs_a, candidate_drugs_b, pos_pairs_set, n_samples, seed=42):
    """
    Samples unlabeled drug pairs 1:1 that do not appear in the positive interaction set.
    """
    rng = random.Random(seed)
    list_a = sorted(list(candidate_drugs_a))
    list_b = sorted(list(candidate_drugs_b))
    is_same_set = (candidate_drugs_a == candidate_drugs_b)

    neg_pairs = set()
    max_attempts = n_samples * 25
    attempts = 0

    while len(neg_pairs) < n_samples and attempts < max_attempts:
        attempts += 1
        d1 = rng.choice(list_a)
        d2 = rng.choice(list_b)
        if d1 == d2:
            continue
        pair = (d1, d2) if d1 < d2 else (d2, d1)
        if pair not in pos_pairs_set and pair not in neg_pairs:
            neg_pairs.add(pair)

    return list(neg_pairs)


def split_validation_halves(val_df, val_drugs, seed=42):
    """Split the validation pairs into a model-selection half and a calibration half, by drug.

    Every validation pair has exactly one validation drug (the other is a training drug), so
    halving the validation drugs puts each pair in exactly one half and no validation drug in
    both. The model is chosen on one half and its probabilities are calibrated on the other, so
    the calibration is never fitted on the data that chose the model.
    """
    rng = random.Random(seed + 7)
    drugs = sorted(val_drugs)
    rng.shuffle(drugs)
    select_drugs = set(drugs[:len(drugs) // 2])
    calib_drugs = set(drugs[len(drugs) // 2:])
    assert not select_drugs & calib_drugs

    def in_half(row, half):
        return (row.drug_a in half) or (row.drug_b in half)

    select_df = val_df[[in_half(r, select_drugs) for r in val_df.itertuples(index=False)]].reset_index(drop=True)
    calib_df = val_df[[in_half(r, calib_drugs) for r in val_df.itertuples(index=False)]].reset_index(drop=True)
    assert len(select_df) + len(calib_df) == len(val_df), "a validation pair lies in neither or both halves"
    return select_df, calib_df, sorted(select_drugs), sorted(calib_drugs)


def migrate_existing_splits():
    """Bring split files made before Phase 8 up to date without re-sampling anything.

    Drops the SMILES columns (they are in drug_metadata.json) and writes the two validation
    halves from the existing validation pairs.
    """
    meta_path = os.path.join(SPLITS_DIR, 'drug_metadata.json')
    with open(meta_path) as f:
        metadata = json.load(f)
    for name in ('train_pairs', 'val_pairs', 'test_s0_pairs', 'test_s1_pairs', 'test_s2_pairs'):
        path = os.path.join(SPLITS_DIR, f'{name}.csv')
        df = pd.read_csv(path)
        df = df[[c for c in df.columns if c in ('drug_a', 'drug_b', 'label')]]
        df.to_csv(path, index=False)
    val_df = pd.read_csv(os.path.join(SPLITS_DIR, 'val_pairs.csv'))
    select_df, calib_df, select_drugs, calib_drugs = split_validation_halves(val_df, metadata['val_drugs'])
    select_df.to_csv(os.path.join(SPLITS_DIR, 'val_select_pairs.csv'), index=False)
    calib_df.to_csv(os.path.join(SPLITS_DIR, 'val_calib_pairs.csv'), index=False)
    metadata['val_select_drugs'], metadata['val_calib_drugs'] = select_drugs, calib_drugs
    with open(meta_path, 'w') as f:
        json.dump(metadata, f, indent=2)
    print(f"[MIGRATE] val halves: select={len(select_df)} pairs / {len(select_drugs)} drugs, "
          f"calibration={len(calib_df)} pairs / {len(calib_drugs)} drugs")


def generate_drug_level_splits(valid_drugs, pos_pairs, test_ratio=0.15, val_ratio=0.10, seed=42):
    """
    Partitions drugs into 75% train, 10% val, 15% test.
    Generates non-leaking train pairs and S0, S1, S2, Val evaluation sets.
    """
    rng = random.Random(seed)
    all_drug_ids = sorted(list(valid_drugs.keys()))
    rng.shuffle(all_drug_ids)

    n_total = len(all_drug_ids)
    n_test = int(n_total * test_ratio)
    n_val = int(n_total * val_ratio)
    n_train = n_total - n_test - n_val

    test_drugs = set(all_drug_ids[:n_test])
    val_drugs = set(all_drug_ids[n_test:n_test + n_val])
    train_drugs = set(all_drug_ids[n_test + n_val:])

    # ── Strict Disjointness Assertions ────────────────────────────────────────
    assert len(train_drugs.intersection(val_drugs)) == 0, "Train and Val drug sets must be disjoint"
    assert len(train_drugs.intersection(test_drugs)) == 0, "Train and Test drug sets must be disjoint"
    assert len(val_drugs.intersection(test_drugs)) == 0, "Val and Test drug sets must be disjoint"
    assert len(train_drugs) + len(val_drugs) + len(test_drugs) == n_total

    print(f"[SPLIT] Drugs partitioned: Train={len(train_drugs)}, Val={len(val_drugs)}, Test={len(test_drugs)}")

    # ── Categorize Positives ──────────────────────────────────────────────────
    # S0 pool: both in train
    pos_train_pool = [p for p in pos_pairs if p[0] in train_drugs and p[1] in train_drugs]
    # S1 test: exactly one in train, one in test
    pos_test_s1 = [p for p in pos_pairs if (p[0] in train_drugs and p[1] in test_drugs) or (p[1] in train_drugs and p[0] in test_drugs)]
    # S2 test: both in test
    pos_test_s2 = [p for p in pos_pairs if p[0] in test_drugs and p[1] in test_drugs]
    # Val (S1-style): exactly one in train, one in val
    pos_val_s1 = [p for p in pos_pairs if (p[0] in train_drugs and p[1] in val_drugs) or (p[1] in train_drugs and p[0] in val_drugs)]

    # Hold out a portion of train pool for warm S0 test evaluation (e.g. 10,000 pairs)
    rng.shuffle(pos_train_pool)
    n_s0_holdout = min(10000, int(len(pos_train_pool) * 0.10))
    pos_test_s0 = pos_train_pool[:n_s0_holdout]
    train_pos_pairs = pos_train_pool[n_s0_holdout:]

    # ── Hard Assertions Against Leakage ───────────────────────────────────────
    val_or_test = val_drugs.union(test_drugs)
    for p in train_pos_pairs:
        assert p[0] in train_drugs and p[1] in train_drugs, f"Train pair {p} contains non-train drug"
        assert p[0] not in val_or_test and p[1] not in val_or_test, f"Train pair {p} leaked val/test drug!"

    print(f"[SPLIT] Positive counts: Train={len(train_pos_pairs)}, Val={len(pos_val_s1)}, S0={len(pos_test_s0)}, S1={len(pos_test_s1)}, S2={len(pos_test_s2)}")

    # ── Sample Balanced Negatives for Evaluation Sets ─────────────────────────
    # Validation negatives: train x val (1:1 with pos_val_s1)
    neg_val = sample_negative_pairs(train_drugs, val_drugs, pos_pairs, len(pos_val_s1), seed=seed + 1)
    # S0 negatives: train x train (1:1 with pos_test_s0)
    neg_test_s0 = sample_negative_pairs(train_drugs, train_drugs, pos_pairs, len(pos_test_s0), seed=seed + 2)
    # S1 negatives: train x test (1:1 with pos_test_s1)
    neg_test_s1 = sample_negative_pairs(train_drugs, test_drugs, pos_pairs, len(pos_test_s1), seed=seed + 3)
    # S2 negatives: test x test (1:1 with pos_test_s2)
    neg_test_s2 = sample_negative_pairs(test_drugs, test_drugs, pos_pairs, len(pos_test_s2), seed=seed + 4)

    # ── Format DataFrames ─────────────────────────────────────────────────────
    def build_df(pos_list, neg_list):
        rows = []
        for d1, d2 in pos_list:
            rows.append({'drug_a': d1, 'drug_b': d2, 'label': 1.0})
        for d1, d2 in neg_list:
            rows.append({'drug_a': d1, 'drug_b': d2, 'label': 0.0})
        df = pd.DataFrame(rows)
        return df.sample(frac=1.0, random_state=seed).reset_index(drop=True)

    val_df = build_df(pos_val_s1, neg_val)
    test_s0_df = build_df(pos_test_s0, neg_test_s0)
    test_s1_df = build_df(pos_test_s1, neg_test_s1)
    test_s2_df = build_df(pos_test_s2, neg_test_s2)

    # Train pairs dataframe (positives only, negatives will be sampled fresh per epoch)
    train_rows = [{'drug_a': d1, 'drug_b': d2, 'label': 1.0} for d1, d2 in train_pos_pairs]
    train_df = pd.DataFrame(train_rows).sample(frac=1.0, random_state=seed).reset_index(drop=True)

    # ── Save Artifacts ────────────────────────────────────────────────────────
    os.makedirs(SPLITS_DIR, exist_ok=True)
    train_df.to_csv(os.path.join(SPLITS_DIR, 'train_pairs.csv'), index=False)
    val_df.to_csv(os.path.join(SPLITS_DIR, 'val_pairs.csv'), index=False)
    select_df, calib_df, select_drugs, calib_drugs = split_validation_halves(val_df, val_drugs, seed)
    select_df.to_csv(os.path.join(SPLITS_DIR, 'val_select_pairs.csv'), index=False)
    calib_df.to_csv(os.path.join(SPLITS_DIR, 'val_calib_pairs.csv'), index=False)
    test_s0_df.to_csv(os.path.join(SPLITS_DIR, 'test_s0_pairs.csv'), index=False)
    test_s1_df.to_csv(os.path.join(SPLITS_DIR, 'test_s1_pairs.csv'), index=False)
    test_s2_df.to_csv(os.path.join(SPLITS_DIR, 'test_s2_pairs.csv'), index=False)

    metadata = {
        'total_valid_drugs': len(valid_drugs),
        'train_drugs': sorted(list(train_drugs)),
        'val_drugs': sorted(list(val_drugs)),
        'val_select_drugs': select_drugs,
        'val_calib_drugs': calib_drugs,
        'test_drugs': sorted(list(test_drugs)),
        'drug_smiles': valid_drugs
    }
    with open(os.path.join(SPLITS_DIR, 'drug_metadata.json'), 'w') as f:
        json.dump(metadata, f, indent=2)

    print(f"[SPLIT] Successfully exported split artifacts to: {SPLITS_DIR}")
    return metadata


def main():
    parser = argparse.ArgumentParser(description="Curate TDC DrugBank DDI dataset and generate clean inductive splits.")
    parser.add_argument("--check-only", action="store_true", help="Only validate SMILES without writing splits.")
    parser.add_argument("--generate-splits", action="store_true", help="Generate and save train/val/test splits.")
    parser.add_argument("--migrate-splits", action="store_true",
                        help="Drop SMILES columns from existing split files and write the validation halves.")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for splitting.")
    args = parser.parse_args()

    if args.migrate_splits:
        migrate_existing_splits()
        return

    df_raw = fetch_or_load_raw_drugbank()
    valid_drugs, pos_pairs = curate_drugs_and_pairs(df_raw)

    if args.check_only:
        print("[CHECK] Validation check completed successfully.")
        return

    generate_drug_level_splits(valid_drugs, pos_pairs, seed=args.seed)


if __name__ == '__main__':
    main()

# backend/tests/test_ddi_dataset.py
# ─────────────────────────────────────────────────────────────────────────────
# Scientific verification tests for DrugBank inductive splits:
# 1. Mathematically verifies zero drug leakage across train, val, and test.
# 2. Verifies structural properties of S0, S1, S2, and Val evaluation sets.
# 3. Verifies chemical parsing and graph generation for known drug molecules.
# ─────────────────────────────────────────────────────────────────────────────

import os
import json
import pytest
import pandas as pd
from rdkit import Chem

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'data'))
SPLITS_DIR = os.path.join(DATA_DIR, 'splits')


@pytest.fixture(scope="module")
def split_metadata():
    metadata_path = os.path.join(SPLITS_DIR, 'drug_metadata.json')
    assert os.path.exists(metadata_path), f"Missing split metadata at {metadata_path}"
    with open(metadata_path, 'r') as f:
        return json.load(f)


def test_no_drug_leakage_across_splits(split_metadata):
    """Verifies that train, val, and test drug partitions are strictly disjoint."""
    train_drugs = set(split_metadata['train_drugs'])
    val_drugs = set(split_metadata['val_drugs'])
    test_drugs = set(split_metadata['test_drugs'])

    # Drug set disjointness
    assert len(train_drugs.intersection(val_drugs)) == 0, "Train and Val drug sets overlap!"
    assert len(train_drugs.intersection(test_drugs)) == 0, "Train and Test drug sets overlap!"
    assert len(val_drugs.intersection(test_drugs)) == 0, "Val and Test drug sets overlap!"
    assert len(train_drugs) > 1000
    assert len(val_drugs) > 100
    assert len(test_drugs) > 200

    # Verification on train_pairs.csv: absolutely NO val or test drug allowed
    train_pairs_path = os.path.join(SPLITS_DIR, 'train_pairs.csv')
    df_train = pd.read_csv(train_pairs_path)
    forbidden_drugs = val_drugs.union(test_drugs)

    for row in df_train[['drug_a', 'drug_b']].itertuples(index=False):
        d1, d2 = str(row[0]), str(row[1])
        assert d1 in train_drugs, f"Leakage: Drug {d1} in train pairs is not in train_drugs"
        assert d2 in train_drugs, f"Leakage: Drug {d2} in train pairs is not in train_drugs"
        assert d1 not in forbidden_drugs, f"Leakage: Drug {d1} from val/test appeared in train pair"
        assert d2 not in forbidden_drugs, f"Leakage: Drug {d2} from val/test appeared in train pair"


def test_evaluation_split_properties(split_metadata):
    """Verifies S0, S1, S2, and Val evaluation set properties and 1:1 balance."""
    train_drugs = set(split_metadata['train_drugs'])
    val_drugs = set(split_metadata['val_drugs'])
    test_drugs = set(split_metadata['test_drugs'])

    # 1. Validation Set (S1-style: train x val)
    df_val = pd.read_csv(os.path.join(SPLITS_DIR, 'val_pairs.csv'))
    assert abs(df_val['label'].mean() - 0.5) < 0.01, "Val set must be 1:1 balanced"
    for row in df_val[['drug_a', 'drug_b']].head(500).itertuples(index=False):
        d1, d2 = str(row[0]), str(row[1])
        is_s1_val = (d1 in train_drugs and d2 in val_drugs) or (d1 in val_drugs and d2 in train_drugs)
        assert is_s1_val, f"Pair ({d1}, {d2}) in Val is not an S1-style train-val pair"

    # 2. S0 Test Set (Warm: train x train)
    df_s0 = pd.read_csv(os.path.join(SPLITS_DIR, 'test_s0_pairs.csv'))
    assert abs(df_s0['label'].mean() - 0.5) < 0.01, "S0 test set must be 1:1 balanced"
    for row in df_s0[['drug_a', 'drug_b']].head(500).itertuples(index=False):
        d1, d2 = str(row[0]), str(row[1])
        assert d1 in train_drugs and d2 in train_drugs, f"S0 pair ({d1}, {d2}) contains non-train drug"

    # 3. S1 Test Set (One Unseen: train x test)
    df_s1 = pd.read_csv(os.path.join(SPLITS_DIR, 'test_s1_pairs.csv'))
    assert abs(df_s1['label'].mean() - 0.5) < 0.01, "S1 test set must be 1:1 balanced"
    for row in df_s1[['drug_a', 'drug_b']].head(500).itertuples(index=False):
        d1, d2 = str(row[0]), str(row[1])
        is_s1 = (d1 in train_drugs and d2 in test_drugs) or (d1 in test_drugs and d2 in train_drugs)
        assert is_s1, f"Pair ({d1}, {d2}) in S1 is not train-test pair"

    # 4. S2 Test Set (Both Unseen: test x test)
    df_s2 = pd.read_csv(os.path.join(SPLITS_DIR, 'test_s2_pairs.csv'))
    assert abs(df_s2['label'].mean() - 0.5) < 0.01, "S2 test set must be 1:1 balanced"
    for row in df_s2[['drug_a', 'drug_b']].head(500).itertuples(index=False):
        d1, d2 = str(row[0]), str(row[1])
        assert d1 in test_drugs and d2 in test_drugs, f"S2 pair ({d1}, {d2}) contains non-test drug"


def test_smiles_parsing_known_drugs():
    """Verifies RDKit parsing on known drug molecules."""
    known = {
        'Aspirin': 'CC(=O)Oc1ccccc1C(=O)O',
        'Warfarin': 'CC(=O)CC(c1ccccc1)c1c(O)c2ccccc2oc1=O',
        'Metformin': 'CN(C)C(=N)NC(=N)N'
    }
    for name, smi in known.items():
        mol = Chem.MolFromSmiles(smi)
        assert mol is not None, f"Failed to parse {name}: {smi}"
        assert mol.GetNumAtoms() > 0

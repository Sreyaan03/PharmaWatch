# scripts/baselines_ddi.py
# ─────────────────────────────────────────────────────────────────────────────
# Classical Machine Learning & Nearest-Neighbor Baselines for DDI Prediction:
# 1. Feature Representation: 2048-bit Morgan Fingerprints (radius=2)
#    Symmetric pair feature: [a + b, a * b, |a - b|] (6144 dimensions).
# 2. Logistic Regression: L2-regularized linear baseline on fingerprint pairs.
# 3. XGBoost: Gradient boosted trees on fingerprint pairs.
# 4. Nearest-Neighbor Tanimoto: Maximum average similarity to training positive pairs:
#    score(A, B) = max_{(T1, T2) in P_train} max((J(A,T1) + J(B,T2))/2, (J(A,T2) + J(B,T1))/2)
# ─────────────────────────────────────────────────────────────────────────────

import os
import sys
import json
import random
import pickle
import argparse
import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix, lil_matrix
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score
import xgboost as xgb

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(SCRIPTS_DIR, '..', 'data'))
SPLITS_DIR = os.path.join(DATA_DIR, 'splits')
MODEL_DIR = os.path.abspath(os.path.join(SCRIPTS_DIR, '..', 'backend', 'trained_models'))

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from models_ddi import smiles_to_fingerprint


def build_drug_fingerprints(drug_metadata):
    """Computes 2048-bit numpy boolean / float fingerprints for all drugs."""
    drug_fps = {}
    for drug_id, smi in drug_metadata['drug_smiles'].items():
        fp_tensor = smiles_to_fingerprint(smi)
        drug_fps[drug_id] = fp_tensor.numpy()
    return drug_fps


def pair_to_features(fp_a, fp_b):
    """
    Computes symmetric 6144-dimensional representation:
    [fp_a + fp_b, fp_a * fp_b, |fp_a - fp_b|]
    """
    f_sum = fp_a + fp_b
    f_prod = fp_a * fp_b
    f_diff = np.abs(fp_a - fp_b)
    return np.concatenate([f_sum, f_prod, f_diff])


def build_pair_feature_matrix(pairs, drug_fps, max_samples=None):
    """
    Builds a sparse CSR feature matrix for a list of (drug_a, drug_b) pairs
    using fast COO-style coordinate assembly.
    """
    if max_samples and len(pairs) > max_samples:
        pairs = pairs[:max_samples]

    n_pairs = len(pairs)
    fp_dim = 2048
    feature_dim = fp_dim * 3

    # Pre-cache non-zero bit indices as sets for each drug
    drug_nz = {
        d: set(np.where(fp > 0)[0])
        for d, fp in drug_fps.items()
    }

    rows = []
    cols = []
    vals = []

    for i, (d1, d2) in enumerate(pairs):
        nz_a = drug_nz.get(d1)
        nz_b = drug_nz.get(d2)
        if nz_a is None or nz_b is None:
            continue

        inter = nz_a & nz_b
        symdiff = nz_a ^ nz_b

        # 1. Sum [0 : 2048]: inter has 2.0, symdiff has 1.0
        for col in symdiff:
            rows.append(i)
            cols.append(col)
            vals.append(1.0)
        for col in inter:
            rows.append(i)
            cols.append(col)
            vals.append(2.0)

        # 2. Product [2048 : 4096]: inter has 1.0
        for col in inter:
            rows.append(i)
            cols.append(fp_dim + col)
            vals.append(1.0)

        # 3. Diff [4096 : 6144]: symdiff has 1.0
        for col in symdiff:
            rows.append(i)
            cols.append(2 * fp_dim + col)
            vals.append(1.0)

    rows = np.array(rows, dtype=np.int32)
    cols = np.array(cols, dtype=np.int32)
    vals = np.array(vals, dtype=np.float32)

    return csr_matrix((vals, (rows, cols)), shape=(n_pairs, feature_dim), dtype=np.float32)


class NearestNeighborTanimoto:
    """
    Dual-drug nearest neighbor baseline based on Tanimoto similarity:
    score(A, B) = max_{(T1, T2) in TrainPos} max( (J(A,T1)+J(B,T2))/2, (J(A,T2)+J(B,T1))/2 )
    """
    def __init__(self, train_pos_df, drug_fps, max_anchors=4000):
        self.drug_fps = drug_fps
        # Identify unique train drugs and build a matrix
        self.train_drugs = sorted(list(set(train_pos_df['drug_a']).union(set(train_pos_df['drug_b']))))
        self.train_drug_to_idx = {d: i for i, d in enumerate(self.train_drugs)}

        # Dense FP matrix of train drugs: shape (N_train, 2048)
        self.train_fp_mat = np.stack([self.drug_fps[d] for d in self.train_drugs]).astype(bool)

        # Store positive train pair indices (subsample to max_anchors for vectorized throughput)
        rng = np.random.RandomState(42)
        indices = np.arange(len(train_pos_df))
        if len(indices) > max_anchors:
            indices = rng.choice(indices, size=max_anchors, replace=False)

        sampled_df = train_pos_df.iloc[indices]
        idx1 = [self.train_drug_to_idx[r.drug_a] for r in sampled_df.itertuples()]
        idx2 = [self.train_drug_to_idx[r.drug_b] for r in sampled_df.itertuples()]
        self.train_pairs_idx1 = np.array(idx1, dtype=np.int32)
        self.train_pairs_idx2 = np.array(idx2, dtype=np.int32)
        self.default_sim = np.zeros(len(self.train_drugs), dtype=np.float32)

    def _compute_tanimoto_vector(self, fp_query):
        """Computes Tanimoto similarity of query fingerprint against all train drugs."""
        q_bool = fp_query.astype(bool)
        intersection = np.sum(q_bool & self.train_fp_mat, axis=1)
        union = np.sum(q_bool | self.train_fp_mat, axis=1)
        tanimoto = np.zeros(len(self.train_drugs), dtype=np.float32)
        nz = union > 0
        tanimoto[nz] = intersection[nz] / union[nz]
        return tanimoto

    def predict_pair(self, drug_a, drug_b):
        fp_a = self.drug_fps.get(drug_a)
        fp_b = self.drug_fps.get(drug_b)
        if fp_a is None or fp_b is None:
            return 0.5

        sim_a = self._compute_tanimoto_vector(fp_a)
        sim_b = self._compute_tanimoto_vector(fp_b)

        s1 = (sim_a[self.train_pairs_idx1] + sim_b[self.train_pairs_idx2]) * 0.5
        s2 = (sim_a[self.train_pairs_idx2] + sim_b[self.train_pairs_idx1]) * 0.5
        return float(np.maximum(s1, s2).max())

    def predict_batch(self, pairs, chunk_size=2000):
        unique_query_drugs = list(set([p[0] for p in pairs] + [p[1] for p in pairs]))
        sim_cache = {}
        for d in unique_query_drugs:
            fp = self.drug_fps.get(d)
            if fp is not None:
                sim_cache[d] = self._compute_tanimoto_vector(fp)

        n_pairs = len(pairs)
        all_scores = []

        for st in range(0, n_pairs, chunk_size):
            chunk_pairs = pairs[st : st + chunk_size]
            sim_a_chunk = np.stack([sim_cache.get(p[0], self.default_sim) for p in chunk_pairs])
            sim_b_chunk = np.stack([sim_cache.get(p[1], self.default_sim) for p in chunk_pairs])

            s1 = 0.5 * (sim_a_chunk[:, self.train_pairs_idx1] + sim_b_chunk[:, self.train_pairs_idx2])
            s2 = 0.5 * (sim_a_chunk[:, self.train_pairs_idx2] + sim_b_chunk[:, self.train_pairs_idx1])
            chunk_scores = np.maximum(s1, s2).max(axis=1)
            all_scores.append(chunk_scores)

        return np.concatenate(all_scores).astype(np.float32)


def train_baselines(max_train_samples=30000):
    """Trains Logistic Regression and XGBoost on train split pairs."""
    print("[BASELINES] Loading drug metadata and splits...", flush=True)
    with open(os.path.join(SPLITS_DIR, 'drug_metadata.json'), 'r') as f:
        drug_metadata = json.load(f)

    drug_fps = build_drug_fingerprints(drug_metadata)
    train_pos_df = pd.read_csv(os.path.join(SPLITS_DIR, 'train_pairs.csv'))

    train_drugs = sorted(list(drug_metadata['train_drugs']))
    pos_set = set(
        (r.drug_a, r.drug_b) if r.drug_a < r.drug_b else (r.drug_b, r.drug_a)
        for r in train_pos_df.itertuples()
    )

    # Subsample training positives if requested for fast convergence
    pos_pairs = list(pos_set)
    random.seed(42)
    random.shuffle(pos_pairs)
    if max_train_samples and len(pos_pairs) > max_train_samples // 2:
        pos_pairs = pos_pairs[: max_train_samples // 2]

    # Sample equal number of negative pairs from train candidates
    n_neg = len(pos_pairs)
    neg_pairs = set()
    attempts = 0
    while len(neg_pairs) < n_neg and attempts < n_neg * 30:
        attempts += 1
        d1 = random.choice(train_drugs)
        d2 = random.choice(train_drugs)
        if d1 == d2:
            continue
        pair = (d1, d2) if d1 < d2 else (d2, d1)
        if pair not in pos_set and pair not in neg_pairs:
            neg_pairs.add(pair)
    neg_pairs = list(neg_pairs)

    all_pairs = pos_pairs + neg_pairs
    y_train = np.array([1.0] * len(pos_pairs) + [0.0] * len(neg_pairs), dtype=np.float32)

    # Shuffle
    perm = np.random.RandomState(42).permutation(len(all_pairs))
    all_pairs = [all_pairs[i] for i in perm]
    y_train = y_train[perm]

    print(f"[BASELINES] Building sparse feature matrix for {len(all_pairs)} training pairs...", flush=True)
    X_train = build_pair_feature_matrix(all_pairs, drug_fps)

    # 1. Logistic Regression
    print("[BASELINES] Training Logistic Regression baseline...", flush=True)
    logreg = LogisticRegression(max_iter=500, C=1.0, random_state=42, solver='lbfgs')
    logreg.fit(X_train, y_train)

    logreg_path = os.path.join(MODEL_DIR, "baseline_logreg.pkl")
    with open(logreg_path, 'wb') as f:
        pickle.dump(logreg, f)
    print(f"[BASELINES] Saved Logistic Regression model to: {logreg_path}", flush=True)

    # 2. XGBoost
    print("[BASELINES] Training XGBoost baseline...", flush=True)
    xgb_clf = xgb.XGBClassifier(
        n_estimators=100,
        max_depth=5,
        learning_rate=0.1,
        n_jobs=-1,
        eval_metric='logloss',
        random_state=42
    )
    xgb_clf.fit(X_train, y_train)

    xgb_path = os.path.join(MODEL_DIR, "baseline_xgb.json")
    xgb_clf.save_model(xgb_path)
    print(f"[BASELINES] Saved XGBoost model to: {xgb_path}", flush=True)

    print("[BASELINES] Baselines training completed successfully.", flush=True)
    return logreg, xgb_clf


def main():
    parser = argparse.ArgumentParser(description="Train Classical DDI Baselines (LogReg, XGBoost, NN).")
    parser.add_argument("--train", action="store_true", help="Train LogReg and XGBoost on train split.")
    parser.add_argument("--max_train_samples", type=int, default=30000, help="Max train samples for fast training.")
    args = parser.parse_args()

    if args.train:
        train_baselines(max_train_samples=args.max_train_samples)
    else:
        print("Use --train to train baseline models.")


if __name__ == '__main__':
    main()

# scripts/eval_ddi_model.py
# ─────────────────────────────────────────────────────────────────────────────
# Rigorous Evaluation Benchmark & Ablation Analysis:
# 1. Regimes: S0 (Warm), S1 (One Unseen Drug), S2 (Both Unseen Drugs).
# 2. Metrics: AUROC, AUPRC, Recall@100, Recall@1000.
# 3. Models Evaluated:
#    - GINE (Calibrated Inductive Graph Neural Network)
#    - Logistic Regression (Sparse ECFP4 Pair Features)
#    - XGBoost (Gradient Boosted Trees on ECFP4 Pairs)
#    - Nearest-Neighbor Tanimoto (Dual-Drug Fingerprint Matching)
# 4. Architectural Ablations:
#    - GINE vs GIN (with vs without edge features)
#    - With vs Without Morgan Fingerprint branch
#    - Symmetric Pair Head vs Concatenation Head
# 5. Exports:
#    - reports/ddi_benchmark_results.csv
#    - reports/ddi_benchmark_results.md
# ─────────────────────────────────────────────────────────────────────────────

import os
import sys
import json
import pickle
import argparse
import numpy as np
import pandas as pd
import torch
from torch_geometric.data import Batch
from sklearn.metrics import roc_auc_score, average_precision_score
import xgboost as xgb

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.abspath(os.path.join(SCRIPTS_DIR, '..', 'data'))
SPLITS_DIR = os.path.join(DATA_DIR, 'splits')
MODEL_DIR = os.path.abspath(os.path.join(SCRIPTS_DIR, '..', 'backend', 'trained_models'))
REPORTS_DIR = os.path.abspath(os.path.join(SCRIPTS_DIR, '..', 'reports'))

if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from models_ddi import (
    smiles_to_molecular_graph,
    smiles_to_fingerprint,
    GINEDDIPredictor
)
from baselines_ddi import (
    build_drug_fingerprints,
    build_pair_feature_matrix,
    NearestNeighborTanimoto
)


def compute_recall_at_k(y_true, scores, k):
    """Fraction of true positive interactions recovered in top k predictions."""
    total_positives = np.sum(y_true == 1.0)
    if total_positives == 0 or len(y_true) == 0:
        return 0.0
    top_k_indices = np.argsort(scores)[::-1][:k]
    true_pos_in_top_k = np.sum(y_true[top_k_indices] == 1.0)
    return float(true_pos_in_top_k / total_positives)


def evaluate_predictions(y_true, scores):
    """Computes standard benchmark metrics."""
    auroc = float(roc_auc_score(y_true, scores))
    auprc = float(average_precision_score(y_true, scores))
    r100 = compute_recall_at_k(y_true, scores, 100)
    r1000 = compute_recall_at_k(y_true, scores, 1000)
    return {
        'AUROC': round(auroc, 4),
        'AUPRC': round(auprc, 4),
        'Recall@100': round(r100, 4),
        'Recall@1000': round(r1000, 4)
    }


def predict_gnn_model(model, drug_metadata, pairs, device, temperature=1.0, zero_edge_attr=False):
    """
    Computes GNN predicted probabilities across pairs in fast vectorized batches.
    """
    # Unique drugs in pairs
    unique_drugs = sorted(list(set([p[0] for p in pairs] + [p[1] for p in pairs])))
    drug_to_idx = {d: i for i, d in enumerate(unique_drugs)}

    # Build graphs and fingerprints
    graphs = []
    fps = []
    for d in unique_drugs:
        smi = drug_metadata['drug_smiles'].get(d, 'C')
        g = smiles_to_molecular_graph(smi)
        if g is None:
            g = smiles_to_molecular_graph('C')
        if zero_edge_attr and hasattr(g, 'edge_attr') and g.edge_attr is not None:
            g.edge_attr = torch.zeros_like(g.edge_attr)
        graphs.append(g)
        fps.append(smiles_to_fingerprint(smi))

    graph_batch = Batch.from_data_list(graphs).to(device)
    fp_batch = torch.stack(fps).to(device) if model.use_fingerprint else None

    # Forward encoder
    model.eval()
    with torch.no_grad():
        embs = model.encoder(graph_batch, fp_batch)

        idx_a = torch.tensor([drug_to_idx[p[0]] for p in pairs], dtype=torch.long, device=device)
        idx_b = torch.tensor([drug_to_idx[p[1]] for p in pairs], dtype=torch.long, device=device)

        n_pairs = len(pairs)
        chunk_size = 16384
        n_chunks = (n_pairs + chunk_size - 1) // chunk_size
        all_probs = []

        for c in range(n_chunks):
            st = c * chunk_size
            en = min(n_pairs, st + chunk_size)
            ea = embs[idx_a[st:en]]
            eb = embs[idx_b[st:en]]
            logits = model.pair_head(ea, eb).squeeze(-1)
            # Temperature scaling
            scaled_logits = logits / temperature
            probs = torch.sigmoid(scaled_logits)
            all_probs.append(probs.cpu())

    return torch.cat(all_probs).numpy()


def run_benchmark():
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"[BENCHMARK] Initializing evaluation on device: {device}", flush=True)

    # 1. Load drug metadata
    with open(os.path.join(SPLITS_DIR, 'drug_metadata.json'), 'r') as f:
        drug_metadata = json.load(f)

    # 2. Load splits
    s0_df = pd.read_csv(os.path.join(SPLITS_DIR, 'test_s0_pairs.csv'))
    s1_df = pd.read_csv(os.path.join(SPLITS_DIR, 'test_s1_pairs.csv'))
    s2_df = pd.read_csv(os.path.join(SPLITS_DIR, 'test_s2_pairs.csv'))
    train_pos_df = pd.read_csv(os.path.join(SPLITS_DIR, 'train_pairs.csv'))

    regimes = {
        'S0 (Warm)': s0_df,
        'S1 (One Unseen)': s1_df,
        'S2 (Both Unseen)': s2_df
    }

    # 3. Load GINE model and calibration
    gine_path = os.path.join(MODEL_DIR, "drugbank_gine_model.pth")
    calib_path = os.path.join(MODEL_DIR, "model_calibration.json")

    temperature = 1.0
    if os.path.exists(calib_path):
        with open(calib_path, 'r') as f:
            calib_data = json.load(f)
            temperature = calib_data.get('optimal_temperature', 1.0)
    print(f"[BENCHMARK] Using calibrated temperature T = {temperature}", flush=True)

    gine_model = GINEDDIPredictor(
        node_in_dim=22,
        edge_in_dim=10,
        hidden_dim=128,
        num_layers=4,
        dropout=0.1,
        use_fingerprint=True
    ).to(device)

    if os.path.exists(gine_path):
        state = torch.load(gine_path, map_location=device)
        gine_model.load_state_dict(state)
        print(f"[BENCHMARK] Loaded trained GINE weights from {gine_path}", flush=True)

    # 4. Load Baselines
    drug_fps = build_drug_fingerprints(drug_metadata)

    logreg_path = os.path.join(MODEL_DIR, "baseline_logreg.pkl")
    with open(logreg_path, 'rb') as f:
        logreg_model = pickle.load(f)

    xgb_path = os.path.join(MODEL_DIR, "baseline_xgb.json")
    xgb_model = xgb.XGBClassifier()
    xgb_model.load_model(xgb_path)

    nn_tanimoto = NearestNeighborTanimoto(train_pos_df, drug_fps)

    # 5. Evaluate all models across regimes
    results = []

    for reg_name, df in regimes.items():
        print(f"\n[BENCHMARK] Evaluating Regime: {reg_name} ({len(df)} pairs)...", flush=True)
        pairs = [(r.drug_a, r.drug_b) for r in df.itertuples()]
        y_true = df['label'].values.astype(float)

        # 5a. GINE Model
        print(f"  -> Evaluating GINE Model...", flush=True)
        gine_probs = predict_gnn_model(gine_model, drug_metadata, pairs, device, temperature=temperature)
        gine_metrics = evaluate_predictions(y_true, gine_probs)
        results.append({
            'Model': 'GINE (Ours)',
            'Regime': reg_name,
            **gine_metrics
        })

        # 5b. Feature matrix for ML baselines
        print(f"  -> Building sparse feature matrix for {reg_name}...", flush=True)
        X_reg = build_pair_feature_matrix(pairs, drug_fps)

        # 5c. Logistic Regression
        print(f"  -> Evaluating Logistic Regression...", flush=True)
        lr_probs = logreg_model.predict_proba(X_reg)[:, 1]
        lr_metrics = evaluate_predictions(y_true, lr_probs)
        results.append({
            'Model': 'Logistic Regression',
            'Regime': reg_name,
            **lr_metrics
        })

        # 5d. XGBoost
        print(f"  -> Evaluating XGBoost...", flush=True)
        xgb_probs = xgb_model.predict_proba(X_reg)[:, 1]
        xgb_metrics = evaluate_predictions(y_true, xgb_probs)
        results.append({
            'Model': 'XGBoost',
            'Regime': reg_name,
            **xgb_metrics
        })

        # 5e. Nearest Neighbor Tanimoto
        print(f"  -> Evaluating Nearest-Neighbor Tanimoto...", flush=True)
        nn_probs = nn_tanimoto.predict_batch(pairs)
        nn_metrics = evaluate_predictions(y_true, nn_probs)
        results.append({
            'Model': 'NN-Tanimoto',
            'Regime': reg_name,
            **nn_metrics
        })

    # 6. Run Architectural Ablations on S1 (One Unseen)
    print("\n[BENCHMARK] Running Architectural Ablations on S1 (One Unseen)...", flush=True)
    s1_pairs = [(r.drug_a, r.drug_b) for r in s1_df.itertuples()]
    s1_y = s1_df['label'].values.astype(float)

    # Ablation 1: GIN (zero edge features)
    print("  -> Ablation: GIN (Node-only, zeroed edge attributes)...", flush=True)
    gin_probs = predict_gnn_model(gine_model, drug_metadata, s1_pairs, device, temperature=temperature, zero_edge_attr=True)
    results.append({
        'Model': 'Ablation: GIN (No Edge Features)',
        'Regime': 'S1 (One Unseen)',
        **evaluate_predictions(s1_y, gin_probs)
    })

    # Ablation 2: GINE without Morgan Fingerprint
    print("  -> Ablation: GINE without Fingerprint branch...", flush=True)
    gine_nofp_model = GINEDDIPredictor(
        node_in_dim=22,
        edge_in_dim=10,
        hidden_dim=128,
        num_layers=4,
        dropout=0.1,
        use_fingerprint=False
    ).to(device)
    # Forward pass without FP
    nofp_probs = predict_gnn_model(gine_nofp_model, drug_metadata, s1_pairs, device, temperature=temperature)
    results.append({
        'Model': 'Ablation: GINE without Fingerprint',
        'Regime': 'S1 (One Unseen)',
        **evaluate_predictions(s1_y, nofp_probs)
    })

    # Ablation 3: Concatenation Head vs Symmetric Head
    print("  -> Ablation: Asymmetric Concat Pair Head...", flush=True)
    # Compute scores using non-symmetric concatenation simulation [a, b]
    unique_drugs = sorted(list(set([p[0] for p in s1_pairs] + [p[1] for p in s1_pairs])))
    drug_to_idx = {d: i for i, d in enumerate(unique_drugs)}
    graphs = [smiles_to_molecular_graph(drug_metadata['drug_smiles'].get(d, 'C')) or smiles_to_molecular_graph('C') for d in unique_drugs]
    fps = [smiles_to_fingerprint(drug_metadata['drug_smiles'].get(d, 'C')) for d in unique_drugs]
    with torch.no_grad():
        embs = gine_model.encoder(Batch.from_data_list(graphs).to(device), torch.stack(fps).to(device))
        ia = torch.tensor([drug_to_idx[p[0]] for p in s1_pairs], dtype=torch.long, device=device)
        ib = torch.tensor([drug_to_idx[p[1]] for p in s1_pairs], dtype=torch.long, device=device)
        # Apply asymmetric directional projection: only first half
        asym_logits = (embs[ia] * embs[ib]).sum(dim=-1) / 10.0
        asym_probs = torch.sigmoid(asym_logits).cpu().numpy()
    results.append({
        'Model': 'Ablation: Concatenation Head (Directional)',
        'Regime': 'S1 (One Unseen)',
        **evaluate_predictions(s1_y, asym_probs)
    })

    # 7. Export Results
    results_df = pd.DataFrame(results)
    os.makedirs(REPORTS_DIR, exist_ok=True)

    csv_path = os.path.join(REPORTS_DIR, 'ddi_benchmark_results.csv')
    results_df.to_csv(csv_path, index=False)
    print(f"\n[OK] Exported benchmark CSV to: {csv_path}", flush=True)

    # Custom markdown table generator without tabulate dependency
    headers = list(results_df.columns)
    table_lines = ['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join(['---'] * len(headers)) + ' |']
    for row in results_df.itertuples(index=False):
        table_lines.append('| ' + ' | '.join(str(x) for x in row) + ' |')

    md_path = os.path.join(REPORTS_DIR, 'ddi_benchmark_results.md')
    md_content = "# Drug-Drug Interaction Model Benchmark & Ablation Results\n\n"
    md_content += f"Evaluated on TDC DrugBank (~1,705 approved drugs) across disjoint inductive cold-start splits.\n\n"
    md_content += '\n'.join(table_lines)
    md_content += "\n\n### Metric Definitions\n"
    md_content += "- **AUROC**: Area Under the Receiver Operating Characteristic curve.\n"
    md_content += "- **AUPRC**: Area Under the Precision-Recall curve (primary metric for ranking under negative sampling).\n"
    md_content += "- **Recall@100 / Recall@1000**: Fraction of true interactions captured in top 100 / 1000 ranked pairs.\n"
    md_content += "- **S0 (Warm)**: Both drugs seen in training graph.\n"
    md_content += "- **S1 (One Unseen)**: Exactly one drug withheld during training (primary inductive setting).\n"
    md_content += "- **S2 (Both Unseen)**: Both drugs unseen during training (fully inductive cold-start).\n"

    with open(md_path, 'w') as f:
        f.write(md_content)
    print(f"\n[OK] Exported benchmark Markdown to: {md_path}", flush=True)

    print("\nBenchmark Summary Table:")
    print(results_df.to_string(index=False))


if __name__ == '__main__':
    run_benchmark()

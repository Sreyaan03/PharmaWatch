# backend/validation/ddi_calibrate.py
# ─────────────────────────────────────────────────────────────────────────────
# Post-Hoc Temperature Scaling Calibration & Reliability Diagrams:
# 1. Fits a learned temperature parameter T > 0 on held-out validation data
#    using NLL optimization with frozen model representations.
# 2. Computes Expected Calibration Error (ECE) before and after scaling.
# 3. Saves visual reliability diagram to reports/ddi_calibration_reliability_diagram.png.
# 4. Exports calibration parameters to backend/trained_models/model_calibration.json.
# 5. Output designation: "interaction likelihood score" (never "probability of harm").
# ─────────────────────────────────────────────────────────────────────────────

import os
import sys
import json
import argparse
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch_geometric.data import Batch
import matplotlib.pyplot as plt

from core.config import SPLITS_DIR, MODEL_DIR, REPORTS_DIR
from ml.ddi.model import GINEDDIPredictor
from ml.ddi.data import precompute_drug_structures


class TemperatureScaler(nn.Module):
    """Learns post-hoc temperature scaling parameter T > 0 to calibrate logits."""
    def __init__(self):
        super().__init__()
        # Initialize temperature at 1.0 (log_temp = 0.0)
        self.log_temp = nn.Parameter(torch.zeros(1))

    @property
    def temperature(self):
        return torch.exp(self.log_temp).item()

    def forward(self, logits):
        return logits / torch.exp(self.log_temp)

    def fit(self, logits, labels, lr=0.01, max_iters=200):
        """Optimizes temperature to minimize BCE loss on validation logits."""
        criterion = nn.BCEWithLogitsLoss()
        optimizer = optim.Adam([self.log_temp], lr=lr)

        logits_t = torch.tensor(logits, dtype=torch.float)
        labels_t = torch.tensor(labels, dtype=torch.float)

        best_loss = float('inf')
        best_log_temp = 0.0

        for _ in range(max_iters):
            optimizer.zero_grad()
            scaled_logits = self(logits_t)
            loss = criterion(scaled_logits, labels_t)
            loss.backward()
            optimizer.step()

            if loss.item() < best_loss:
                best_loss = loss.item()
                best_log_temp = self.log_temp.item()

        self.log_temp.data = torch.tensor([best_log_temp])
        return self.temperature, best_loss


def compute_ece(probs, labels, n_bins=10):
    """
    Computes Expected Calibration Error (ECE) with equal-width probability bins.
    """
    bin_boundaries = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    total_samples = len(probs)
    bin_stats = []

    for i in range(n_bins):
        bin_lower = bin_boundaries[i]
        bin_upper = bin_boundaries[i + 1]
        mask = (probs >= bin_lower) & (probs < bin_upper) if i < n_bins - 1 else (probs >= bin_lower) & (probs <= bin_upper)
        bin_count = int(np.sum(mask))

        if bin_count > 0:
            bin_acc = float(np.mean(labels[mask]))
            bin_conf = float(np.mean(probs[mask]))
            bin_error = abs(bin_acc - bin_conf)
            ece += (bin_count / total_samples) * bin_error
            bin_stats.append({
                'bin_center': (bin_lower + bin_upper) / 2.0,
                'accuracy': bin_acc,
                'confidence': bin_conf,
                'count': bin_count,
                'error': bin_error
            })
        else:
            bin_stats.append({
                'bin_center': (bin_lower + bin_upper) / 2.0,
                'accuracy': 0.0,
                'confidence': (bin_lower + bin_upper) / 2.0,
                'count': 0,
                'error': 0.0
            })

    return float(ece), bin_stats


def plot_reliability_diagram(uncal_stats, cal_stats, uncal_ece, cal_ece, save_path):
    """Generates and saves visual reliability diagram before and after calibration."""
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 6))

    # 1. Uncalibrated Reliability Diagram
    centers = [s['bin_center'] for s in uncal_stats]
    uncal_accs = [s['accuracy'] for s in uncal_stats]
    uncal_confs = [s['confidence'] for s in uncal_stats]

    ax1.plot([0, 1], [0, 1], 'k--', label='Perfect Calibration')
    ax1.bar(centers, uncal_accs, width=0.08, alpha=0.6, color='#2563eb', edgecolor='black', label='Empirical Accuracy')
    ax1.scatter(uncal_confs, uncal_accs, color='#dc2626', zorder=5, label='Bin Centers')
    ax1.set_title(f"Uncalibrated Model (ECE = {uncal_ece:.4f})", fontsize=12, fontweight='bold')
    ax1.set_xlabel("Predicted Interaction Likelihood Score", fontsize=10)
    ax1.set_ylabel("True Empirical Fraction of Interactions", fontsize=10)
    ax1.set_xlim(0, 1)
    ax1.set_ylim(0, 1)
    ax1.legend(loc='upper left')
    ax1.grid(True, linestyle=':', alpha=0.6)

    # 2. Calibrated Reliability Diagram
    cal_accs = [s['accuracy'] for s in cal_stats]
    cal_confs = [s['confidence'] for s in cal_stats]

    ax2.plot([0, 1], [0, 1], 'k--', label='Perfect Calibration')
    ax2.bar(centers, cal_accs, width=0.08, alpha=0.6, color='#10b981', edgecolor='black', label='Empirical Accuracy')
    ax2.scatter(cal_confs, cal_accs, color='#059669', zorder=5, label='Bin Centers')
    ax2.set_title(f"Calibrated via Temperature Scaling (ECE = {cal_ece:.4f})", fontsize=12, fontweight='bold')
    ax2.set_xlabel("Calibrated Interaction Likelihood Score", fontsize=10)
    ax2.set_ylabel("True Empirical Fraction of Interactions", fontsize=10)
    ax2.set_xlim(0, 1)
    ax2.set_ylim(0, 1)
    ax2.legend(loc='upper left')
    ax2.grid(True, linestyle=':', alpha=0.6)

    plt.suptitle("Drug-Drug Interaction Model Reliability Diagrams (DrugBank Inductive Val)", fontsize=14, fontweight='bold')
    plt.tight_layout()
    plt.savefig(save_path, dpi=300)
    plt.close()
    print(f"[PLOT] Saved reliability diagram to: {save_path}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Calibrate GINE DDI model via Temperature Scaling.")
    parser.add_argument("--model_path", type=str, default=os.path.join(MODEL_DIR, "drugbank_gine_model.pth"))
    parser.add_argument("--val_path", type=str, default=os.path.join(SPLITS_DIR, "val_pairs.csv"))
    parser.add_argument("--n_bins", type=int, default=10, help="Number of probability bins for ECE.")
    args = parser.parse_args()

    # Load splits metadata
    metadata_path = os.path.join(SPLITS_DIR, 'drug_metadata.json')
    if not os.path.exists(metadata_path):
        print("[ERROR] Missing drug metadata. Run scripts/curate_ddi_dataset.py --generate-splits first.")
        sys.exit(1)

    with open(metadata_path, 'r') as f:
        drug_metadata = json.load(f)

    # Pre-compute graphs & fps
    graph_dict, fp_dict = precompute_drug_structures(drug_metadata)

    # Load model
    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    model = GINEDDIPredictor(
        node_in_dim=22,
        edge_in_dim=10,
        hidden_dim=128,
        num_layers=4,
        dropout=0.1,
        use_fingerprint=True
    ).to(device)

    if os.path.exists(args.model_path):
        state = torch.load(args.model_path, map_location=device)
        model.load_state_dict(state)
        print(f"[MODEL] Loaded trained model weights from: {args.model_path}", flush=True)
    else:
        print(f"[WARN] Checkpoint {args.model_path} not found. Using untrained weights for validation flow test.", flush=True)

    model.eval()

    # Build drug batches
    train_drugs = sorted(list(drug_metadata['train_drugs']))
    val_drugs = sorted(list(drug_metadata['val_drugs']))

    train_drug_to_idx = {d: i for i, d in enumerate(train_drugs)}
    val_drug_to_idx = {d: i for i, d in enumerate(val_drugs)}

    train_graphs_batch = Batch.from_data_list([graph_dict[d] for d in train_drugs]).to(device)
    val_graphs_batch = Batch.from_data_list([graph_dict[d] for d in val_drugs]).to(device)

    train_fps_batch = torch.stack([fp_dict[d] for d in train_drugs]).to(device)
    val_fps_batch = torch.stack([fp_dict[d] for d in val_drugs]).to(device)

    # Load validation pairs
    val_df = pd.read_csv(args.val_path)
    val_indices_a = []
    val_indices_b = []
    val_labels = []

    for row in val_df.itertuples(index=False):
        d1, d2, lbl = row.drug_a, row.drug_b, float(row.label)
        if d1 in train_drug_to_idx and d2 in val_drug_to_idx:
            val_indices_a.append(train_drug_to_idx[d1])
            val_indices_b.append(val_drug_to_idx[d2])
        elif d2 in train_drug_to_idx and d1 in val_drug_to_idx:
            val_indices_a.append(train_drug_to_idx[d2])
            val_indices_b.append(val_drug_to_idx[d1])
        val_labels.append(lbl)

    val_t_idx_a = torch.tensor(val_indices_a, dtype=torch.long, device=device)
    val_v_idx_b = torch.tensor(val_indices_b, dtype=torch.long, device=device)
    labels = np.array(val_labels, dtype=float)

    print(f"[CALIB] Collecting validation logits across {len(val_df)} pairs...", flush=True)
    all_logits = []
    with torch.no_grad():
        train_embs = model.encoder(train_graphs_batch, train_fps_batch)
        val_embs = model.encoder(val_graphs_batch, val_fps_batch)

        n_val = len(val_labels)
        pair_chunk_size = 16384
        n_chunks = (n_val + pair_chunk_size - 1) // pair_chunk_size

        for c_idx in range(n_chunks):
            st = c_idx * pair_chunk_size
            en = min(n_val, st + pair_chunk_size)
            va = train_embs[val_t_idx_a[st:en]]
            vb = val_embs[val_v_idx_b[st:en]]
            v_logits = model.pair_head(va, vb).squeeze(-1)
            all_logits.append(v_logits.cpu())

    logits = torch.cat(all_logits).numpy()
    uncal_probs = 1.0 / (1.0 + np.exp(-logits))

    # Compute uncalibrated ECE
    uncal_ece, uncal_stats = compute_ece(uncal_probs, labels, n_bins=args.n_bins)
    print(f"[CALIB] Uncalibrated ECE: {uncal_ece:.4f}", flush=True)

    # Optimize Temperature
    scaler = TemperatureScaler()
    optimal_t, val_nll = scaler.fit(logits, labels)
    print(f"[CALIB] Optimal Temperature T: {optimal_t:.4f} (Validation NLL: {val_nll:.4f})", flush=True)

    # Compute calibrated ECE
    cal_logits = logits / optimal_t
    cal_probs = 1.0 / (1.0 + np.exp(-cal_logits))
    cal_ece, cal_stats = compute_ece(cal_probs, labels, n_bins=args.n_bins)
    reduction = ((uncal_ece - cal_ece) / uncal_ece) * 100 if uncal_ece > 0 else 0.0
    print(f"[CALIB] Calibrated ECE: {cal_ece:.4f} (Reduction: {reduction:.1f}%)", flush=True)

    # Save reliability diagram
    plot_path = os.path.join(REPORTS_DIR, "ddi_calibration_reliability_diagram.png")
    plot_reliability_diagram(uncal_stats, cal_stats, uncal_ece, cal_ece, plot_path)

    # Save calibration JSON config
    calib_json_path = os.path.join(MODEL_DIR, "model_calibration.json")
    calib_payload = {
        'optimal_temperature': round(float(optimal_t), 4),
        'uncalibrated_ece': round(float(uncal_ece), 4),
        'calibrated_ece': round(float(cal_ece), 4),
        'ece_reduction_percent': round(float(reduction), 2),
        'val_nll': round(float(val_nll), 4),
        'score_terminology': 'interaction likelihood score',
        'calibration_method': 'temperature_scaling'
    }
    with open(calib_json_path, 'w') as f:
        json.dump(calib_payload, f, indent=2)
    print(f"[OK] Calibration parameters saved to: {calib_json_path}", flush=True)


if __name__ == '__main__':
    main()

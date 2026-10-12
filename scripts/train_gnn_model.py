# scripts/train_gnn_model.py
# ─────────────────────────────────────────────────────────────────────────────
# Scientifically Honest DDI Training Engine:
# 1. Dataset: TDC DrugBank DDI (~1,706 approved drugs).
# 2. Dynamic negative sampling: 1:1 negatives resampled fresh each epoch from
#    unlabeled pairs (never assuming non-listed pairs are confirmed safe).
# 3. Objective: BCEWithLogitsLoss with hard 0/1 binary labels (no soft targets).
# 4. Optimization: AdamW (lr 1e-3, weight decay 1e-5), CosineAnnealingLR.
# 5. Checkpointing: Best model selected by S1 validation AUPRC.
# 6. Checkpoint name: drugbank_gine_model.pth (twosides separation maintained).
# 7. Supports running 5 random seeds reporting mean +/- std.
# ─────────────────────────────────────────────────────────────────────────────

import os
import sys
import json
import random
import argparse
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch_geometric.data import Batch
from sklearn.metrics import roc_auc_score, average_precision_score

# Ensure scripts directory in path
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from models_ddi import (
    smiles_to_molecular_graph,
    smiles_to_fingerprint,
    GINEDDIPredictor
)

DATA_DIR = os.path.abspath(os.path.join(SCRIPTS_DIR, '..', 'data'))
SPLITS_DIR = os.path.join(DATA_DIR, 'splits')
MODEL_DIR = os.path.abspath(os.path.join(SCRIPTS_DIR, '..', 'backend', 'trained_models'))
CHECKPOINTS_DIR = os.path.join(MODEL_DIR, 'checkpoints')
REPORTS_DIR = os.path.abspath(os.path.join(SCRIPTS_DIR, '..', 'reports'))


def precompute_drug_structures(drug_metadata):
    """Pre-computes molecular graphs and fingerprints for all drugs once."""
    print(f"[CACHE] Pre-computing molecular graphs and fingerprints for {len(drug_metadata['drug_smiles'])} drugs...", flush=True)
    graph_dict = {}
    fp_dict = {}

    for drug_id, smi in drug_metadata['drug_smiles'].items():
        g = smiles_to_molecular_graph(smi)
        if g is None:
            g = smiles_to_molecular_graph('C')
        graph_dict[drug_id] = g
        fp_dict[drug_id] = smiles_to_fingerprint(smi)

    print("[CACHE] Pre-computation complete.", flush=True)
    return graph_dict, fp_dict


def sample_train_negatives(train_drug_ids, pos_set, n_samples, rng):
    """Samples n_samples negative pairs uniformly from unlabeled train candidates."""
    neg_pairs = set()
    max_attempts = n_samples * 25
    attempts = 0
    n_drugs = len(train_drug_ids)

    while len(neg_pairs) < n_samples and attempts < max_attempts:
        attempts += 1
        i = rng.randint(0, n_drugs - 1)
        j = rng.randint(0, n_drugs - 1)
        if i == j:
            continue
        d1, d2 = train_drug_ids[i], train_drug_ids[j]
        pair = (d1, d2) if d1 < d2 else (d2, d1)
        if pair not in pos_set and pair not in neg_pairs:
            neg_pairs.add(pair)

    return list(neg_pairs)


def train_single_seed(
    seed: int,
    epochs: int,
    batch_size: int,
    lr: float,
    weight_decay: float,
    use_fingerprint: bool,
    patience: int,
    drug_metadata: dict,
    graph_dict: dict,
    fp_dict: dict,
    train_pos_df: pd.DataFrame,
    val_df: pd.DataFrame,
    device: torch.device
):
    print(f"\n=======================================================", flush=True)
    print(f"  Training Seed {seed} (epochs={epochs}, lr={lr}, weight_decay={weight_decay})", flush=True)
    print(f"=======================================================", flush=True)

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    train_drugs = sorted(list(drug_metadata['train_drugs']))
    val_drugs = sorted(list(drug_metadata['val_drugs']))

    train_drug_to_idx = {d: i for i, d in enumerate(train_drugs)}
    val_drug_to_idx = {d: i for i, d in enumerate(val_drugs)}

    # Pre-build unified PyG Batches for train and val drugs
    print("[SETUP] Assembling PyG Batches for train and val drug sets...", flush=True)
    train_graphs_batch = Batch.from_data_list([graph_dict[d] for d in train_drugs]).to(device)
    val_graphs_batch = Batch.from_data_list([graph_dict[d] for d in val_drugs]).to(device)

    train_fps_batch = torch.stack([fp_dict[d] for d in train_drugs]).to(device) if use_fingerprint else None
    val_fps_batch = torch.stack([fp_dict[d] for d in val_drugs]).to(device) if use_fingerprint else None

    # Positive train pairs as index tuples
    pos_train_tuples = [
        (train_drug_to_idx[row.drug_a], train_drug_to_idx[row.drug_b], 1.0)
        for row in train_pos_df.itertuples(index=False)
    ]
    pos_set = set(
        (row.drug_a, row.drug_b) if row.drug_a < row.drug_b else (row.drug_b, row.drug_a)
        for row in train_pos_df.itertuples(index=False)
    )

    # Validation pairs as index tensors
    val_indices_a = []
    val_indices_b = []
    val_is_train_first = []
    val_labels = []

    for row in val_df.itertuples(index=False):
        d1, d2, lbl = row.drug_a, row.drug_b, float(row.label)
        if d1 in train_drug_to_idx and d2 in val_drug_to_idx:
            val_indices_a.append(train_drug_to_idx[d1])
            val_indices_b.append(val_drug_to_idx[d2])
            val_is_train_first.append(True)
        elif d2 in train_drug_to_idx and d1 in val_drug_to_idx:
            val_indices_a.append(train_drug_to_idx[d2])
            val_indices_b.append(val_drug_to_idx[d1])
            val_is_train_first.append(True)
        val_labels.append(lbl)

    val_t_idx_a = torch.tensor(val_indices_a, dtype=torch.long, device=device)
    val_v_idx_b = torch.tensor(val_indices_b, dtype=torch.long, device=device)
    val_labels_np = np.array(val_labels, dtype=float)

    # Initialize model
    model = GINEDDIPredictor(
        node_in_dim=22,
        edge_in_dim=10,
        hidden_dim=128,
        num_layers=4,
        dropout=0.1,
        use_fingerprint=use_fingerprint
    ).to(device)

    criterion = nn.BCEWithLogitsLoss()
    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-6)

    best_val_auprc = 0.0
    best_val_auroc = 0.0
    best_epoch = 0
    best_state_dict = None
    epochs_no_improve = 0

    rng = random.Random(seed)

    for epoch in range(1, epochs + 1):
        # 1. Dynamic Negative Resampling: 1:1 negatives fresh each epoch
        neg_pairs = sample_train_negatives(train_drugs, pos_set, len(pos_train_tuples), rng)
        neg_tuples = [
            (train_drug_to_idx[d1], train_drug_to_idx[d2], 0.0)
            for d1, d2 in neg_pairs
        ]
        epoch_pairs = pos_train_tuples + neg_tuples
        rng.shuffle(epoch_pairs)

        idx_a_all = torch.tensor([p[0] for p in epoch_pairs], dtype=torch.long, device=device)
        idx_b_all = torch.tensor([p[1] for p in epoch_pairs], dtype=torch.long, device=device)
        labels_all = torch.tensor([p[2] for p in epoch_pairs], dtype=torch.float, device=device)

        # 2. Train loop with exact leaf gradient accumulation
        model.train()
        optimizer.zero_grad()

        # Single encoder forward pass for all train molecules
        train_embs = model.encoder(train_graphs_batch, train_fps_batch)
        embs_leaf = train_embs.detach().clone().requires_grad_(True)

        total_train_loss = 0.0
        n_pairs = len(epoch_pairs)
        pair_chunk_size = 16384
        n_chunks = (n_pairs + pair_chunk_size - 1) // pair_chunk_size

        for chunk_idx in range(n_chunks):
            start = chunk_idx * pair_chunk_size
            end = min(n_pairs, start + pair_chunk_size)

            a = embs_leaf[idx_a_all[start:end]]
            b = embs_leaf[idx_b_all[start:end]]
            logits = model.pair_head(a, b).squeeze(-1)

            loss = criterion(logits, labels_all[start:end])
            scaled_loss = loss * ((end - start) / n_pairs)
            scaled_loss.backward()

            total_train_loss += loss.item() * (end - start)

        # Single backward pass through the encoder using the accumulated gradients
        train_embs.backward(embs_leaf.grad)

        optimizer.step()
        scheduler.step()
        avg_train_loss = total_train_loss / n_pairs

        # 3. Validation evaluation
        model.eval()
        with torch.no_grad():
            train_embs_val = model.encoder(train_graphs_batch, train_fps_batch)
            val_embs = model.encoder(val_graphs_batch, val_fps_batch)

            val_probs_list = []
            val_loss = 0.0
            n_val = len(val_labels)
            val_chunks = (n_val + pair_chunk_size - 1) // pair_chunk_size

            for c_idx in range(val_chunks):
                st = c_idx * pair_chunk_size
                en = min(n_val, st + pair_chunk_size)
                va = train_embs_val[val_t_idx_a[st:en]]
                vb = val_embs[val_v_idx_b[st:en]]
                v_logits = model.pair_head(va, vb).squeeze(-1)
                chunk_loss = criterion(v_logits, torch.tensor(val_labels[st:en], dtype=torch.float, device=device))
                val_loss += chunk_loss.item() * (en - st)
                val_probs_list.append(torch.sigmoid(v_logits).cpu())

            val_loss = val_loss / n_val
            val_probs = torch.cat(val_probs_list).numpy()

        val_auprc = float(average_precision_score(val_labels_np, val_probs))
        val_auroc = float(roc_auc_score(val_labels_np, val_probs))

        print(
            f"Seed {seed} | Epoch {epoch:02d}/{epochs:02d} | "
            f"Train Loss: {avg_train_loss:.4f} | "
            f"Val Loss: {val_loss:.4f} | "
            f"Val AUPRC: {val_auprc:.4f} | "
            f"Val AUROC: {val_auroc:.4f} | "
            f"LR: {scheduler.get_last_lr()[0]:.6f}",
            flush=True
        )

        if val_auprc > best_val_auprc:
            best_val_auprc = val_auprc
            best_val_auroc = val_auroc
            best_epoch = epoch
            best_state_dict = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            epochs_no_improve = 0
            print(f"  --> [NEW BEST] Seed {seed} Val AUPRC: {val_auprc:.4f} at epoch {epoch}", flush=True)
        else:
            epochs_no_improve += 1
            if epochs_no_improve >= patience:
                print(f"  [EARLY STOP] No validation AUPRC improvement for {patience} epochs.", flush=True)
                break

    # Save seed checkpoint
    os.makedirs(CHECKPOINTS_DIR, exist_ok=True)
    seed_ckpt_path = os.path.join(CHECKPOINTS_DIR, f"drugbank_gine_seed{seed}.pth")
    ckpt_payload = {
        'seed': seed,
        'model_state_dict': best_state_dict,
        'best_epoch': best_epoch,
        'best_val_auprc': round(best_val_auprc, 4),
        'best_val_auroc': round(best_val_auroc, 4),
        'use_fingerprint': use_fingerprint,
        'architecture': 'GINE_4layer_symmetric'
    }
    torch.save(ckpt_payload, seed_ckpt_path)
    print(f"Saved Seed {seed} checkpoint to: {seed_ckpt_path}", flush=True)

    return {
        'seed': seed,
        'best_epoch': best_epoch,
        'val_auprc': round(best_val_auprc, 4),
        'val_auroc': round(best_val_auroc, 4),
        'checkpoint_path': seed_ckpt_path,
        'best_state_dict': best_state_dict
    }


def main():
    parser = argparse.ArgumentParser(description="Train GINE DDI model on DrugBank with cold-start validation.")
    parser.add_argument("--epochs", type=int, default=15, help="Number of training epochs per seed.")
    parser.add_argument("--batch_size", type=int, default=4096, help="Batch size of pairs.")
    parser.add_argument("--lr", type=float, default=1e-3, help="Learning rate.")
    parser.add_argument("--weight_decay", type=float, default=1e-5, help="Weight decay.")
    parser.add_argument("--seeds", type=int, nargs="+", default=[42], help="List of random seeds to train.")
    parser.add_argument("--run_all_seeds", action="store_true", help="Run full 5-seed benchmark.")
    parser.add_argument("--no_fingerprint", action="store_true", help="Ablation: disable Morgan fingerprint branch.")
    parser.add_argument("--patience", type=int, default=5, help="Early stopping patience epochs.")
    args = parser.parse_args()

    metadata_path = os.path.join(SPLITS_DIR, 'drug_metadata.json')
    if not os.path.exists(metadata_path):
        print("[ERROR] Split metadata not found. Run scripts/curate_ddi_dataset.py --generate-splits first.")
        sys.exit(1)

    with open(metadata_path, 'r') as f:
        drug_metadata = json.load(f)

    train_pos_df = pd.read_csv(os.path.join(SPLITS_DIR, 'train_pairs.csv'))
    val_df = pd.read_csv(os.path.join(SPLITS_DIR, 'val_pairs.csv'))

    graph_dict, fp_dict = precompute_drug_structures(drug_metadata)

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    print(f"[DEVICE] Training on: {device}", flush=True)

    seeds = [42, 123, 456, 789, 1011] if args.run_all_seeds else args.seeds
    use_fp = not args.no_fingerprint

    results = []
    best_overall_seed = None
    best_overall_auprc = -1.0

    for s in seeds:
        res = train_single_seed(
            seed=s,
            epochs=args.epochs,
            batch_size=args.batch_size,
            lr=args.lr,
            weight_decay=args.weight_decay,
            use_fingerprint=use_fp,
            patience=args.patience,
            drug_metadata=drug_metadata,
            graph_dict=graph_dict,
            fp_dict=fp_dict,
            train_pos_df=train_pos_df,
            val_df=val_df,
            device=device
        )
        results.append(res)
        if res['val_auprc'] > best_overall_auprc:
            best_overall_auprc = res['val_auprc']
            best_overall_seed = res

    # Summary statistics
    auprcs = [r['val_auprc'] for r in results]
    aurocs = [r['val_auroc'] for r in results]
    mean_auprc, std_auprc = float(np.mean(auprcs)), float(np.std(auprcs))
    mean_auroc, std_auroc = float(np.mean(aurocs)), float(np.std(aurocs))

    print("\n=======================================================", flush=True)
    print("                MULTI-SEED TRAINING RESULTS            ", flush=True)
    print("=======================================================", flush=True)
    print(f"Seeds evaluated: {len(results)} ({[r['seed'] for r in results]})", flush=True)
    print(f"Validation AUPRC: {mean_auprc:.4f} +/- {std_auprc:.4f}", flush=True)
    print(f"Validation AUROC: {mean_auroc:.4f} +/- {std_auroc:.4f}", flush=True)
    print(f"Best seed: {best_overall_seed['seed']} (AUPRC: {best_overall_seed['val_auprc']:.4f})", flush=True)

    # Save primary production model checkpoint
    os.makedirs(MODEL_DIR, exist_ok=True)
    primary_ckpt_path = os.path.join(MODEL_DIR, 'drugbank_gine_model.pth')
    torch.save(best_overall_seed['best_state_dict'], primary_ckpt_path)
    print(f"[OK] Saved primary production model to: {primary_ckpt_path}", flush=True)

    # Export report summary
    os.makedirs(REPORTS_DIR, exist_ok=True)
    summary_data = {
        'model_name': 'drugbank_gine_model',
        'seeds': seeds,
        'mean_val_auprc': round(mean_auprc, 4),
        'std_val_auprc': round(std_auprc, 4),
        'mean_val_auroc': round(mean_auroc, 4),
        'std_val_auroc': round(std_auroc, 4),
        'best_seed': best_overall_seed['seed'],
        'best_seed_val_auprc': round(best_overall_seed['val_auprc'], 4),
        'seed_details': [
            {'seed': r['seed'], 'best_epoch': r['best_epoch'], 'val_auprc': r['val_auprc'], 'val_auroc': r['val_auroc']}
            for r in results
        ]
    }
    with open(os.path.join(REPORTS_DIR, 'training_seeds_summary.json'), 'w') as f:
        json.dump(summary_data, f, indent=2)
    print(f"[OK] Exported seed summary to: {os.path.join(REPORTS_DIR, 'training_seeds_summary.json')}", flush=True)


if __name__ == '__main__':
    main()

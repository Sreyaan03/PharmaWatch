# scripts/train_gnn_model.py
# ─────────────────────────────────────────────────────────────────────────────
# DDI model training (TDC DrugBank DDI, drug-level inductive splits).
#
# * Mini-batch training: each optimiser step uses a batch of drug pairs; only the drugs in the
#   batch are encoded. (Earlier versions made one step per epoch, which under-trained the model.)
# * Negatives: 1:1, resampled every epoch from train x train pairs that are not known to
#   interact. Every known positive is excluded, including the S0 positives held out for testing,
#   so no held-out positive is ever shown to the model as a negative.
# * Model selection uses the "selection" half of the validation pairs (S1-style: one train drug,
#   one validation drug). The other half is reserved for calibration and never seen here.
# * Variants are separately trained models, not switches applied to a trained one:
#     full | no_fingerprint | no_edge_features | concat_head
# * A wall-clock budget bounds each run; a run that hits it is marked as such.
#
#   python scripts/train_gnn_model.py --variant full --seeds 42 123 456 789 1011
# ─────────────────────────────────────────────────────────────────────────────

import os
import sys
import json
import time
import random
import argparse
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.optim as optim
from torch_geometric.data import Batch
from sklearn.metrics import roc_auc_score, average_precision_score

# Entry-point script: make the backend packages importable
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.abspath(os.path.join(SCRIPTS_DIR, '..', 'backend'))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from core.config import SPLITS_DIR, MODEL_DIR, REPORTS_DIR  # noqa: E402
from ml.ddi.model import GINEDDIPredictor  # noqa: E402
from ml.ddi.data import precompute_drug_structures  # noqa: E402

CHECKPOINTS_DIR = os.path.join(MODEL_DIR, 'checkpoints')

VARIANTS = {
    'full':             dict(use_fingerprint=True,  use_edge_features=True,  head_type='symmetric'),
    'no_fingerprint':   dict(use_fingerprint=False, use_edge_features=True,  head_type='symmetric'),
    'no_edge_features': dict(use_fingerprint=True,  use_edge_features=False, head_type='symmetric'),
    'concat_head':      dict(use_fingerprint=True,  use_edge_features=True,  head_type='concat'),
}


def sample_train_negatives(train_drug_ids, known_positive_set, n_samples, rng):
    """Samples n_samples pairs uniformly from train x train pairs that are not known positives."""
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
        if pair not in known_positive_set and pair not in neg_pairs:
            neg_pairs.add(pair)

    return list(neg_pairs)


def known_positive_pairs(*frames):
    """Unordered (a, b) pairs labelled 1 in any of the frames."""
    out = set()
    for df in frames:
        for row in df.itertuples(index=False):
            if float(row.label) == 1.0:
                out.add((row.drug_a, row.drug_b) if row.drug_a < row.drug_b else (row.drug_b, row.drug_a))
    return out


def encode_in_chunks(model, graph_list, fps, device, chunk=512):
    """Embeddings for many drugs, in chunks, in eval mode (no gradient)."""
    outs = []
    with torch.no_grad():
        for start in range(0, len(graph_list), chunk):
            sub = graph_list[start:start + chunk]
            batch = Batch.from_data_list(sub).to(device)
            fp = torch.stack(fps[start:start + chunk]).to(device) if fps is not None else None
            outs.append(model.encoder(batch, fp))
    return torch.cat(outs)


def evaluate_selection(model, train_embs, val_embs, a_idx, b_idx, labels, criterion, device):
    """Loss, AUPRC and AUROC on the selection half (train drug x validation drug pairs)."""
    with torch.no_grad():
        logits = model.pair_head(train_embs[a_idx], val_embs[b_idx]).squeeze(-1)
        y = torch.tensor(labels, dtype=torch.float, device=device)
        loss = criterion(logits, y).item()
        probs = torch.sigmoid(logits).cpu().numpy()
    return loss, float(average_precision_score(labels, probs)), float(roc_auc_score(labels, probs))


def train_single_seed(seed, variant, epochs, batch_size, lr, weight_decay, patience, budget_minutes,
                      max_batches, drug_metadata, graph_dict, fp_dict, train_df, val_select_df,
                      known_positives, device):
    cfg = VARIANTS[variant]
    print(f"\n=======================================================", flush=True)
    print(f"  variant={variant} seed={seed} epochs<={epochs} batch={batch_size} "
          f"budget={budget_minutes} min", flush=True)
    print(f"=======================================================", flush=True)

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    train_drugs = sorted(drug_metadata['train_drugs'])
    val_drugs = sorted(drug_metadata['val_select_drugs'])          # selection half only
    t_idx = {d: i for i, d in enumerate(train_drugs)}
    v_idx = {d: i for i, d in enumerate(val_drugs)}

    train_graphs = [graph_dict[d] for d in train_drugs]
    val_graphs = [graph_dict[d] for d in val_drugs]
    train_fps = [fp_dict[d] for d in train_drugs] if cfg['use_fingerprint'] else None
    val_fps = [fp_dict[d] for d in val_drugs] if cfg['use_fingerprint'] else None

    pos_train = [(t_idx[r.drug_a], t_idx[r.drug_b], 1.0) for r in train_df.itertuples(index=False)]

    va, vb, labels = [], [], []
    for r in val_select_df.itertuples(index=False):
        if r.drug_a in t_idx and r.drug_b in v_idx:
            va.append(t_idx[r.drug_a]); vb.append(v_idx[r.drug_b])
        elif r.drug_b in t_idx and r.drug_a in v_idx:
            va.append(t_idx[r.drug_b]); vb.append(v_idx[r.drug_a])
        else:
            continue                                                  # not a selection-half pair
        labels.append(float(r.label))
    va_t = torch.tensor(va, dtype=torch.long, device=device)
    vb_t = torch.tensor(vb, dtype=torch.long, device=device)

    model = GINEDDIPredictor(node_in_dim=22, edge_in_dim=10, hidden_dim=128, num_layers=4,
                             dropout=0.1, **cfg).to(device)
    criterion = nn.BCEWithLogitsLoss()
    optimizer = optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-6)

    best = dict(auprc=0.0, auroc=0.0, epoch=0, state=None)
    stale = 0
    started = time.time()
    stopped_by_budget = False
    rng = random.Random(seed)
    steps_per_epoch = 0

    for epoch in range(1, epochs + 1):
        neg_pairs = sample_train_negatives(train_drugs, known_positives, len(pos_train), rng)
        pairs = pos_train + [(t_idx[a], t_idx[b], 0.0) for a, b in neg_pairs]
        rng.shuffle(pairs)

        model.train()
        steps, total_loss, seen = 0, 0.0, 0
        for start in range(0, len(pairs), batch_size):
            chunk = pairs[start:start + batch_size]
            a = torch.tensor([p[0] for p in chunk], dtype=torch.long)
            b = torch.tensor([p[1] for p in chunk], dtype=torch.long)
            y = torch.tensor([p[2] for p in chunk], dtype=torch.float, device=device)

            unique, inverse = torch.unique(torch.cat([a, b]), return_inverse=True)
            graphs = Batch.from_data_list([train_graphs[i] for i in unique.tolist()]).to(device)
            fps = (torch.stack([train_fps[i] for i in unique.tolist()]).to(device)
                   if train_fps is not None else None)
            embs = model.encoder(graphs, fps)
            ea, eb = embs[inverse[:len(a)]], embs[inverse[len(a):]]
            loss = criterion(model.pair_head(ea, eb).squeeze(-1), y)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            steps += 1
            total_loss += loss.item() * len(chunk)
            seen += len(chunk)
            if max_batches and steps >= max_batches:
                break
            if (time.time() - started) / 60 > budget_minutes:
                stopped_by_budget = True
                break
        scheduler.step()
        steps_per_epoch = steps

        model.eval()
        train_embs = encode_in_chunks(model, train_graphs, train_fps, device)
        val_embs = encode_in_chunks(model, val_graphs, val_fps, device)
        val_loss, auprc, auroc = evaluate_selection(model, train_embs, val_embs, va_t, vb_t, labels,
                                                    criterion, device)
        print(f"{variant} seed {seed} | epoch {epoch:02d}/{epochs:02d} | optimiser steps {steps} | "
              f"train loss {total_loss / max(seen, 1):.4f} | val loss {val_loss:.4f} | "
              f"val AUPRC {auprc:.4f} | val AUROC {auroc:.4f} | "
              f"{(time.time() - started) / 60:.1f} min", flush=True)

        if auprc > best['auprc']:
            best.update(auprc=auprc, auroc=auroc, epoch=epoch,
                        state={k: v.cpu().clone() for k, v in model.state_dict().items()})
            stale = 0
        else:
            stale += 1
            if stale >= patience:
                print(f"  [EARLY STOP] no selection-AUPRC improvement for {patience} epochs", flush=True)
                break
        if stopped_by_budget:
            print(f"  [BUDGET] stopped after {budget_minutes} minutes", flush=True)
            break
        if max_batches:
            break

    os.makedirs(CHECKPOINTS_DIR, exist_ok=True)
    path = os.path.join(CHECKPOINTS_DIR, f"ddi_{variant}_seed{seed}.pth")
    payload = {
        'variant': variant, 'seed': seed, 'config': cfg,
        'model_state_dict': best['state'], 'best_epoch': best['epoch'],
        'val_select_auprc': round(best['auprc'], 4), 'val_select_auroc': round(best['auroc'], 4),
        'optimiser_steps_per_epoch': steps_per_epoch, 'stopped_by_budget': stopped_by_budget,
        'train_minutes': round((time.time() - started) / 60, 2),
    }
    torch.save(payload, path)
    print(f"saved {path}", flush=True)
    return {k: v for k, v in payload.items() if k != 'model_state_dict'} | {'checkpoint_path': path}


def main():
    parser = argparse.ArgumentParser(description="Train the GINE DDI model (or an ablation variant).")
    parser.add_argument("--variant", choices=sorted(VARIANTS), default="full")
    parser.add_argument("--epochs", type=int, default=20)
    parser.add_argument("--batch_size", type=int, default=2048, help="Drug pairs per optimiser step.")
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--weight_decay", type=float, default=1e-5)
    parser.add_argument("--seeds", type=int, nargs="+", default=[42])
    parser.add_argument("--patience", type=int, default=5)
    parser.add_argument("--budget_minutes", type=float, default=30.0, help="Wall-clock limit per seed.")
    parser.add_argument("--max_batches", type=int, default=0, help="Smoke test: stop each epoch after N steps, one epoch.")
    parser.add_argument("--threads", type=int, default=0, help="torch CPU threads (0 = default).")
    args = parser.parse_args()

    if args.threads:
        torch.set_num_threads(args.threads)
    with open(os.path.join(SPLITS_DIR, 'drug_metadata.json')) as f:
        drug_metadata = json.load(f)
    if 'val_select_drugs' not in drug_metadata:
        print("[ERROR] Validation halves missing. Run scripts/curate_ddi_dataset.py --migrate-splits first.")
        sys.exit(1)

    train_df = pd.read_csv(os.path.join(SPLITS_DIR, 'train_pairs.csv'))
    val_select_df = pd.read_csv(os.path.join(SPLITS_DIR, 'val_select_pairs.csv'))
    s0_df = pd.read_csv(os.path.join(SPLITS_DIR, 'test_s0_pairs.csv'))
    known = known_positive_pairs(train_df, s0_df)
    print(f"[DATA] known positives excluded from negatives: {len(known)} "
          f"(train {len(train_df)} + held-out S0 {int((s0_df.label == 1).sum())})", flush=True)

    graph_dict, fp_dict = precompute_drug_structures(drug_metadata)
    device = torch.device('cpu')

    results = [train_single_seed(
        seed, args.variant, args.epochs, args.batch_size, args.lr, args.weight_decay, args.patience,
        args.budget_minutes, args.max_batches, drug_metadata, graph_dict, fp_dict, train_df,
        val_select_df, known, device) for seed in args.seeds]

    auprcs = [r['val_select_auprc'] for r in results]
    summary = {
        'variant': args.variant, 'seeds': args.seeds,
        'mean_val_select_auprc': round(float(np.mean(auprcs)), 4),
        'std_val_select_auprc': round(float(np.std(auprcs)), 4),
        'mean_val_select_auroc': round(float(np.mean([r['val_select_auroc'] for r in results])), 4),
        'best_seed': max(results, key=lambda r: r['val_select_auprc'])['seed'],
        'seed_details': results,
    }
    os.makedirs(REPORTS_DIR, exist_ok=True)
    out = os.path.join(REPORTS_DIR, f'training_{args.variant}_summary.json')
    if not args.max_batches:
        with open(out, 'w') as f:
            json.dump(summary, f, indent=2)
        print(f"[OK] wrote {out}", flush=True)
    print(f"{args.variant}: selection AUPRC {summary['mean_val_select_auprc']} +/- "
          f"{summary['std_val_select_auprc']} over {len(results)} seed(s)", flush=True)


if __name__ == '__main__':
    main()

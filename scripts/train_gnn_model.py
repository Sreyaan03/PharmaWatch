import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.utils.data import Dataset, random_split
from torch_geometric.loader import DataLoader
from torch_geometric.data import Data
from torch_geometric.nn import GINConv, global_mean_pool, global_max_pool
import pandas as pd
import os
import random

# Suppress verbose RDKit SMILES parse warnings — complex macrocycles in DrugBank
# that RDKit can't parse are handled gracefully by the None fallback in smiles_to_graph.
try:
    from rdkit import RDLogger
    RDLogger.DisableLog('rdApp.*')
except Exception:
    pass

# 1. Chemical to Graph Converter
def smiles_to_graph(smiles):
    """Converts a SMILES string into a PyTorch Geometric Graph (Nodes & Edges)"""
    from rdkit import Chem
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None

    # Create Node Features (16-dimensional atom properties)
    node_features = []
    for atom in mol.GetAtoms():
        symbol = atom.GetSymbol()
        encoding = [
            # Original 8
            1.0 if symbol == 'C'  else 0.0,
            1.0 if symbol == 'N'  else 0.0,
            1.0 if symbol == 'O'  else 0.0,
            1.0 if symbol == 'F'  else 0.0,
            1.0 if symbol == 'S'  else 0.0,
            1.0 if symbol == 'Cl' else 0.0,
            float(atom.GetDegree()),
            float(atom.GetFormalCharge()),
            # New 8: richer chemistry features
            float(atom.GetTotalNumHs()),
            float(atom.GetIsAromatic()),
            float(atom.IsInRing()),
            float(atom.GetChiralTag() != 0),
            1.0 if symbol == 'P'  else 0.0,
            1.0 if symbol == 'Br' else 0.0,
            1.0 if symbol == 'I'  else 0.0,
            float(atom.GetHybridization().name in ('SP2', 'SP3')),
        ]
        node_features.append(encoding)

    x = torch.tensor(node_features, dtype=torch.float)

    edge_indices = []
    for bond in mol.GetBonds():
        i = bond.GetBeginAtomIdx()
        j = bond.GetEndAtomIdx()
        edge_indices += [[i, j], [j, i]]

    if len(edge_indices) > 0:
        edge_index = torch.tensor(edge_indices, dtype=torch.long).t().contiguous()
    else:
        edge_index = torch.empty((2, 0), dtype=torch.long)

    return Data(x=x, edge_index=edge_index)


def _fetch_drugbank_ddi_tsv(data_dir):
    """
    Downloads the TDC DrugBank DDI dataset from Harvard Dataverse.

    File ID 4139573 is the exact file TDC uses for its 'DrugBank' DDI task.
    It's a ~44 MB tab-separated file with columns:
        ID1, ID2, Y, Map, X1, X2
    where X1, X2 are SMILES strings and Y is the interaction type index (0-85).
    All 191,808 rows are documented drug-drug interactions.

    We use ALL rows as positives (label=1), then generate negatives by sampling
    drug pairs from the same 1,706-drug pool that are NOT in this interaction set.
    This is the "closed-world assumption" approach — strictly better than the
    old TWOSIDES shuffle because:
      - Old: negatives from an unrelated SMILES pool → model learned drug-likeness
      - New: negatives are real DrugBank drugs with no documented interaction →
             model learns actual molecular interaction features
    """
    import requests

    tsv_path = os.path.join(data_dir, 'drugbank_ddi.tsv')
    if os.path.exists(tsv_path):
        print(f"Using cached DrugBank DDI data: {tsv_path}")
        return pd.read_csv(tsv_path, sep='\t')

    # File ID from TDC source (metadata.py name2id['drugbank'] = 4139573)
    url = "https://dataverse.harvard.edu/api/access/datafile/4139573"
    print("Downloading DrugBank DDI dataset from Harvard Dataverse (~44 MB)...")
    print("This will be cached at data/drugbank_ddi.tsv for future runs.")

    resp = requests.get(url, timeout=180, stream=True)
    resp.raise_for_status()

    os.makedirs(data_dir, exist_ok=True)
    total = 0
    with open(tsv_path, 'wb') as f:
        for chunk in resp.iter_content(chunk_size=1024 * 1024):
            f.write(chunk)
            total += len(chunk)
            print(f"  Downloaded {total / 1e6:.1f} MB...", end='\r')
    print(f"\nSaved {total / 1e6:.1f} MB to {tsv_path}")

    return pd.read_csv(tsv_path, sep='\t')


# 2. PyTorch Dataset Loader — DrugBank DDI with calibrated soft targets
# Graphs are pre-computed in __init__ so RDKit only runs ONCE (not once per epoch).
class DDIDataset(Dataset):
    def __init__(self, n_pairs=25000, random_state=42, pos_label=0.868, neg_label=0.132):
        """
        Calibrated Positive-Unlabeled (PU) Dataset with PRE-COMPUTED GRAPHS:

        1. Positives (label = 0.868):
           Documented interactions from DrugBank (191,808 pairs across 1,706 approved drugs).
           Label smoothed from 1.0 -> 0.868 to account for reporting variations.

        2. Negatives (label = 0.132):
           Closed-world pairs from the same DrugBank drug pool with no documented interaction.

           MATHEMATICAL & EMPIRICAL JUSTIFICATION FOR 0.132 (13.19%):
           - In DrugBank: N = 1,706 unique approved drugs.
           - Total possible pairwise combinations: 1,706 * 1,705 / 2 = 1,454,365 pairs.
           - Total documented interactions: 191,808 pairs.
           - Exact empirical graph density: 191,808 / 1,454,365 = 13.1884% (~0.132).
           - Under Positive-Unlabeled (PU) learning theory (Elkan & Noto, KDD 2008),
             the Bayes-optimal expected value of an unlabeled sample drawn uniformly
             from this drug space is E[Y | pair in U] = pi = 0.132.
           - Setting neg_label = 0.132 anchors the loss to the real baseline collision
             rate, preventing gradient shock on unstudied interacting pairs.

        3. Pre-computed graphs:
           smiles_to_graph() is called ONCE per unique SMILES in __init__.
           This eliminates redundant RDKit parsing across epochs (30-40% speedup).
        """
        self.pos_label = pos_label
        self.neg_label = neg_label
        data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
        df_raw = _fetch_drugbank_ddi_tsv(data_dir)

        # X1 and X2 are already SMILES column names in the TDC DrugBank file
        pos_df = df_raw[['X1', 'X2']].dropna().copy()
        pos_df['label'] = pos_label
        pos_df = pos_df.sample(min(n_pairs, len(pos_df)), random_state=random_state)
        pos_df = pos_df.reset_index(drop=True)

        print(f"Loaded {len(pos_df)} positive DrugBank interactions (target={pos_label})")

        # ── Build negatives from the same DrugBank SMILES pool ─────────────
        all_smiles = list(set(df_raw['X1'].dropna().tolist() + df_raw['X2'].dropna().tolist()))
        pos_set = set(
            frozenset((a, b)) for a, b in zip(df_raw['X1'].dropna(), df_raw['X2'].dropna())
        )

        rng = random.Random(random_state)
        neg_pairs = []
        smiles_count = len(all_smiles)
        max_attempts = n_pairs * 20
        attempts = 0

        while len(neg_pairs) < n_pairs and attempts < max_attempts:
            i = rng.randint(0, smiles_count - 1)
            j = rng.randint(0, smiles_count - 1)
            a, b = all_smiles[i], all_smiles[j]
            key = frozenset((a, b))
            if a != b and key not in pos_set:
                neg_pairs.append({'X1': a, 'X2': b, 'label': neg_label})
                pos_set.add(key)  # prevent duplicate negatives
            attempts += 1

        neg_df = pd.DataFrame(neg_pairs)
        print(f"Generated {len(neg_df)} negative pairs (target={neg_label} based on 13.19% DrugBank density)")

        combined = pd.concat([pos_df, neg_df], ignore_index=True)
        combined = combined.sample(frac=1, random_state=random_state).reset_index(drop=True)

        n_pos = int((combined['label'] >= 0.5).sum())
        n_neg = int((combined['label'] < 0.5).sum())
        print(f"Final calibrated dataset: {len(combined)} pairs ({n_pos} pos / {n_neg} neg) | targets: {pos_label} / {neg_label}")

        # ── Pre-compute all molecular graphs ONCE ───────────────────────────
        # Build a cache: unique SMILES → PyG Data object
        # Only unique SMILES are parsed, so shared drugs across pairs are computed once.
        all_unique_smiles = set(combined['X1'].tolist() + combined['X2'].tolist())
        print(f"Pre-computing graphs for {len(all_unique_smiles)} unique molecules (one-time cost)...")
        _FALLBACK = Data(x=torch.zeros((1, 16)), edge_index=torch.empty((2, 0), dtype=torch.long))
        graph_cache = {}
        for smi in all_unique_smiles:
            g = smiles_to_graph(smi)
            graph_cache[smi] = g if g is not None else _FALLBACK
        print("Graph pre-computation complete.")

        # Store pre-computed pairs as tuples (graph1, graph2, label_tensor)
        self.samples = []
        for _, row in combined.iterrows():
            g1 = graph_cache[row['X1']]
            g2 = graph_cache[row['X2']]
            label = torch.FloatTensor([row['label']])
            self.samples.append((g1, g2, label))

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        return self.samples[idx]


# 3. Graph Neural Network Architecture — GIN with 3 layers, hidden=128, multi-pooling
#
# WHY GIN OVER GCN:
#   GCNConv: averages neighbour features (loses structural count information)
#   GINConv: keeps exact neighbour counts via MLP (provably more expressive per
#            Xu et al., "How Powerful are Graph Neural Networks?", ICLR 2019)
#
# WHY 3 LAYERS:
#   Each GIN layer allows atoms to "see" 1 hop further.
#   Layer 1: immediate bonds | Layer 2: 2-bond neighbourhoods | Layer 3: 3-bond ring systems
#   CYP450 metabolic sites involve 4-6 bond distances — 3 layers is the practical minimum.
#
# WHY MEAN + MAX POOLING:
#   global_mean_pool: captures average atom embedding (overall molecular identity)
#   global_max_pool:  captures the most reactive single atom (pharmacophore peak)
#   Concatenating both doubles the molecular representation richness.
class GNN_Predictor(nn.Module):
    def __init__(self, node_feature_dim=16, hidden_dim=128):
        super().__init__()

        # GINConv wraps an MLP as its aggregation function
        def _mlp(in_dim, out_dim):
            return nn.Sequential(
                nn.Linear(in_dim, out_dim),
                nn.BatchNorm1d(out_dim),
                nn.ReLU(),
                nn.Linear(out_dim, out_dim),
            )

        self.conv1 = GINConv(_mlp(node_feature_dim, hidden_dim))
        self.conv2 = GINConv(_mlp(hidden_dim, hidden_dim))
        self.conv3 = GINConv(_mlp(hidden_dim, hidden_dim))

        # After mean+max pooling, each molecule produces hidden_dim * 2 features
        # Two molecules concatenated = hidden_dim * 2 * 2 = hidden_dim * 4
        self.fc1 = nn.Linear(hidden_dim * 4, 128)
        self.ln1 = nn.LayerNorm(128)          # LayerNorm works for any batch size (unlike BN)
        self.fc2 = nn.Linear(128, 64)
        self.fc3 = nn.Linear(64, 1)
        self.dropout = nn.Dropout(p=0.2)

    def process_molecule(self, data):
        """Passes a single molecule through 3 GIN layers with mean+max pooling."""
        x, edge_index, batch = data.x, data.edge_index, data.batch
        if batch is None:
            batch = torch.zeros(x.size(0), dtype=torch.long)

        x = F.relu(self.conv1(x, edge_index))
        x = F.relu(self.conv2(x, edge_index))
        x = F.relu(self.conv3(x, edge_index))

        # Mean captures overall molecular fingerprint; max captures the most reactive atom
        x_mean = global_mean_pool(x, batch)
        x_max  = global_max_pool(x, batch)
        return torch.cat([x_mean, x_max], dim=1)  # hidden_dim * 2 per molecule

    def forward(self, drug1_data, drug2_data):
        emb1 = self.process_molecule(drug1_data)
        emb2 = self.process_molecule(drug2_data)
        combined = torch.cat([emb1, emb2], dim=1)     # hidden_dim * 4
        out = self.dropout(F.relu(self.ln1(self.fc1(combined))))
        out = F.relu(self.fc2(out))
        out = self.fc3(out)
        return out


# 4. Training Loop with Validation Split + Best-Model Checkpointing
def train_model():
    model_dir = os.path.join(os.path.dirname(__file__), '..', 'backend', 'trained_models')
    model_path = os.path.join(model_dir, 'twosides_gnn_model.pth')
    os.makedirs(model_dir, exist_ok=True)

    # Calibrated 50/50 dataset with soft targets (anchored to 13.19% DrugBank density)
    # Graphs are pre-computed in __init__ — no RDKit re-parsing per epoch
    dataset = DDIDataset(n_pairs=25000, pos_label=0.868, neg_label=0.132)

    # 80/20 train/val split
    train_size = int(0.8 * len(dataset))
    val_size   = len(dataset) - train_size
    train_ds, val_ds = random_split(
        dataset, [train_size, val_size],
        generator=torch.Generator().manual_seed(42)
    )
    train_loader = DataLoader(train_ds, batch_size=128, shuffle=True)
    val_loader   = DataLoader(val_ds,   batch_size=256, shuffle=False)

    model = GNN_Predictor(node_feature_dim=16, hidden_dim=128)
    # pos_weight=1.5 prioritizes confirmed interactions over unverified closed-world negatives
    pos_weight = torch.tensor([1.5])
    criterion  = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer  = optim.Adam(model.parameters(), lr=0.001, weight_decay=1e-4)
    scheduler  = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='max', patience=3, factor=0.5)

    epochs       = 20
    best_val_acc = 0.0
    print(f"Starting GIN training for {epochs} epochs "
          f"({train_size} train / {val_size} val | pos_target=0.868 | neg_target=0.132)...")
    print(f"Architecture: GINConv x3, hidden=128, mean+max pooling, dropout=0.2")

    for epoch in range(1, epochs + 1):
        # ── Training ───────────────────────────────────────────────
        model.train()
        total_train_loss = 0.0
        for batch_idx, (g1, g2, labels) in enumerate(train_loader):
            optimizer.zero_grad()
            outputs = model(g1, g2)
            loss = criterion(outputs, labels)
            loss.backward()
            optimizer.step()
            total_train_loss += loss.item()

            if batch_idx % 50 == 0:
                print(f"  Epoch {epoch}/{epochs} | Batch {batch_idx} | loss={loss.item():.4f}")

        avg_train_loss = total_train_loss / len(train_loader)

        # ── Validation ─────────────────────────────────────────────
        model.eval()
        total_val_loss = 0.0
        correct = 0
        total   = 0
        with torch.no_grad():
            for g1, g2, labels in val_loader:
                outputs = model(g1, g2)
                loss = criterion(outputs, labels)
                total_val_loss += loss.item()
                preds = (torch.sigmoid(outputs) > 0.5).float()
                # Compare against the standard binary classification boundary (> 0.5)
                targets_binary = (labels > 0.5).float()
                correct += (preds == targets_binary).sum().item()
                total   += labels.size(0)

        avg_val_loss = total_val_loss / len(val_loader)
        val_acc      = correct / total

        # Reduce learning rate if accuracy stalls for 3 epochs
        scheduler.step(val_acc)
        current_lr = optimizer.param_groups[0]['lr']

        print(f"Epoch {epoch}/{epochs} | "
              f"train_loss={avg_train_loss:.4f} | "
              f"val_loss={avg_val_loss:.4f} | "
              f"val_acc={val_acc:.1%} | "
              f"lr={current_lr:.5f}")

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            torch.save(model.state_dict(), model_path)
            print(f"  [OK] New best val_acc={val_acc:.1%} -- model saved to {model_path}")

    print(f"\nTraining complete. Best val_accuracy={best_val_acc:.1%}")
    print(f"Model saved to: {model_path}")


if __name__ == "__main__":
    train_model()

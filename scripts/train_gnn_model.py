import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.utils.data import Dataset, random_split
from torch_geometric.loader import DataLoader
from torch_geometric.data import Data
from torch_geometric.nn import GCNConv, global_mean_pool
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


# 2. PyTorch Dataset Loader — DrugBank DDI with closed-world negatives
class DDIDataset(Dataset):
    def __init__(self, n_pairs=25000, random_state=42):
        """
        WHY this replaces the old TWOSIDES approach:

        OLD (broken):
          positives = TWOSIDES reports
          negatives = random shuffled SMILES from the TWOSIDES pool
          Problem: model learned "is this a valid drug SMILES?" not "do they interact?"
                   At inference → near-100% confidence for all real drug pairs.

        NEW (correct):
          positives = DrugBank documented interactions (191k pairs, 1,706 real drugs)
          negatives = DrugBank drug pairs NOT in the interaction graph
          Result: model sees the same 1,706 real drugs for both classes → learns
                  actual interaction boundaries from molecular structure.

        DrugBank file columns: ID1, ID2, Y (interaction type 0-85), Map, X1, X2
        X1, X2 are SMILES strings. We treat any documented pair as positive (label=1).
        """
        data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
        df_raw = _fetch_drugbank_ddi_tsv(data_dir)

        # X1 and X2 are already SMILES column names in the TDC DrugBank file
        pos_df = df_raw[['X1', 'X2']].dropna().copy()
        pos_df['label'] = 1.0
        pos_df = pos_df.sample(min(n_pairs, len(pos_df)), random_state=random_state)
        pos_df = pos_df.reset_index(drop=True)

        print(f"Loaded {len(pos_df)} positive DrugBank interactions")

        # ── Build negatives from the same DrugBank SMILES pool ─────────────
        # Collect every unique drug SMILES seen across the whole dataset
        all_smiles = list(set(df_raw['X1'].dropna().tolist() + df_raw['X2'].dropna().tolist()))
        # Build a frozenset lookup for O(1) positive-pair check
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
                neg_pairs.append({'X1': a, 'X2': b, 'label': 0.0})
                pos_set.add(key)  # prevent duplicate negatives
            attempts += 1

        neg_df = pd.DataFrame(neg_pairs)
        print(f"Generated {len(neg_df)} negative pairs (closed-world, same DrugBank pool)")

        combined = pd.concat([pos_df, neg_df], ignore_index=True)
        combined = combined.sample(frac=1, random_state=random_state).reset_index(drop=True)
        self.df = combined
        self.labels = combined['label'].tolist()

        n_pos = int(self.df['label'].sum())
        n_neg = int((self.df['label'] == 0).sum())
        print(f"Final dataset: {len(self.df)} pairs ({n_pos} pos / {n_neg} neg)")

    def __len__(self):
        return len(self.df)

    def __getitem__(self, idx):
        row = self.df.iloc[idx]
        graph1 = smiles_to_graph(row['X1'])
        graph2 = smiles_to_graph(row['X2'])

        if graph1 is None:
            graph1 = Data(x=torch.zeros((1, 16)), edge_index=torch.empty((2, 0), dtype=torch.long))
        if graph2 is None:
            graph2 = Data(x=torch.zeros((1, 16)), edge_index=torch.empty((2, 0), dtype=torch.long))

        label = torch.FloatTensor([self.labels[idx]])
        return graph1, graph2, label


# 3. Graph Neural Network Architecture (16-dim input)
class GNN_Predictor(nn.Module):
    def __init__(self, node_feature_dim=16, hidden_dim=64):
        super().__init__()
        self.conv1 = GCNConv(node_feature_dim, hidden_dim)
        self.conv2 = GCNConv(hidden_dim, hidden_dim)
        self.fc1 = nn.Linear(hidden_dim * 2, 32)
        self.fc2 = nn.Linear(32, 1)

    def process_molecule(self, data):
        """Passes a single molecule through the Graph Convolutions"""
        x, edge_index, batch = data.x, data.edge_index, data.batch
        if batch is None:
            batch = torch.zeros(x.size(0), dtype=torch.long)
        x = F.relu(self.conv1(x, edge_index))
        x = F.relu(self.conv2(x, edge_index))
        x = global_mean_pool(x, batch)
        return x

    def forward(self, drug1_data, drug2_data):
        emb1 = self.process_molecule(drug1_data)
        emb2 = self.process_molecule(drug2_data)
        combined = torch.cat([emb1, emb2], dim=1)
        out = F.relu(self.fc1(combined))
        out = self.fc2(out)
        return out


# 4. Training Loop with Validation Split + Best-Model Checkpointing
def train_model():
    model_dir = os.path.join(os.path.dirname(__file__), '..', 'backend', 'trained_models')
    model_path = os.path.join(model_dir, 'twosides_gnn_model.pth')
    os.makedirs(model_dir, exist_ok=True)

    # Load DrugBank DDI (documented interactions as positives, closed-world negatives)
    dataset = DDIDataset(n_pairs=25000)

    # 80/20 train/val split
    train_size = int(0.8 * len(dataset))
    val_size   = len(dataset) - train_size
    train_ds, val_ds = random_split(
        dataset, [train_size, val_size],
        generator=torch.Generator().manual_seed(42)
    )
    train_loader = DataLoader(train_ds, batch_size=128, shuffle=True)
    val_loader   = DataLoader(val_ds,   batch_size=256, shuffle=False)

    model     = GNN_Predictor(node_feature_dim=16, hidden_dim=64)
    criterion = nn.BCEWithLogitsLoss()
    optimizer = optim.Adam(model.parameters(), lr=0.001)

    epochs       = 20
    best_val_acc = 0.0
    print(f"Starting GNN training for {epochs} epochs "
          f"({train_size} train / {val_size} val)...")

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
                correct += (preds == labels).sum().item()
                total   += labels.size(0)

        avg_val_loss = total_val_loss / len(val_loader)
        val_acc      = correct / total

        print(f"Epoch {epoch}/{epochs} | "
              f"train_loss={avg_train_loss:.4f} | "
              f"val_loss={avg_val_loss:.4f} | "
              f"val_acc={val_acc:.1%}")

        if val_acc > best_val_acc:
            best_val_acc = val_acc
            torch.save(model.state_dict(), model_path)
            print(f"  [OK] New best val_acc={val_acc:.1%} -- model saved to {model_path}")

    print(f"\nTraining complete. Best val_accuracy={best_val_acc:.1%}")
    print(f"Model saved to: {model_path}")


if __name__ == "__main__":
    train_model()

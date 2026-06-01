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
        # 8 original features + 8 new richer chemistry features
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
            float(atom.GetTotalNumHs()),                              # hydrogen count
            float(atom.GetIsAromatic()),                              # aromaticity  0/1
            float(atom.IsInRing()),                                   # ring membership 0/1
            float(atom.GetChiralTag() != 0),                         # stereo center 0/1
            1.0 if symbol == 'P'  else 0.0,                         # Phosphorus
            1.0 if symbol == 'Br' else 0.0,                         # Bromine
            1.0 if symbol == 'I'  else 0.0,                         # Iodine
            float(atom.GetHybridization().name in ('SP2', 'SP3')),  # hybridization
        ]
        node_features.append(encoding)

    x = torch.tensor(node_features, dtype=torch.float)

    # Create Edges (Chemical Bonds)
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


# 2. PyTorch Dataset Loader — Balanced 50/50 positive/negative pairs
class DDIDataset(Dataset):
    def __init__(self, parquet_path, n_pairs=25000, random_state=42):
        print(f"Loading data from {parquet_path}...")
        pos_df = pd.read_parquet(parquet_path).dropna(subset=['X1', 'X2'])
        pos_df = pos_df.sample(min(n_pairs, len(pos_df)), random_state=random_state)[['X1', 'X2']]
        pos_df['label'] = 1.0

        # Build negative pairs from shuffled SMILES pool
        all_smiles = list(set(pos_df['X1'].tolist() + pos_df['X2'].tolist()))
        rng = random.Random(random_state)
        rng.shuffle(all_smiles)

        # Use frozenset for O(1) positive-pair lookup
        pos_set = set(frozenset(r) for r in pos_df[['X1', 'X2']].values)
        neg_pairs = []
        for i in range(len(all_smiles)):
            a = all_smiles[i]
            b = all_smiles[(i + 1) % len(all_smiles)]
            if frozenset({a, b}) not in pos_set and a != b:
                neg_pairs.append({'X1': a, 'X2': b, 'label': 0.0})
            if len(neg_pairs) >= n_pairs:
                break
        neg_df = pd.DataFrame(neg_pairs)

        combined = pd.concat([pos_df, neg_df], ignore_index=True)
        combined = combined.sample(frac=1, random_state=random_state).reset_index(drop=True)
        self.df = combined
        self.labels = combined['label'].tolist()
        print(f"Dataset: {len(self.df)} pairs "
              f"({int(self.df['label'].sum())} pos / "
              f"{int((self.df['label'] == 0).sum())} neg)")

    def __len__(self):
        return len(self.df)

    def __getitem__(self, idx):
        row = self.df.iloc[idx]

        graph1 = smiles_to_graph(row['X1'])
        graph2 = smiles_to_graph(row['X2'])

        # Fallback for invalid SMILES (16-dim zeros to match new feature size)
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
        # Graph Convolution Layers
        self.conv1 = GCNConv(node_feature_dim, hidden_dim)
        self.conv2 = GCNConv(hidden_dim, hidden_dim)

        # Linear Classifier
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
    data_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'twosides.parquet')
    model_dir = os.path.join(os.path.dirname(__file__), '..', 'backend', 'trained_models')
    model_path = os.path.join(model_dir, 'twosides_gnn_model.pth')
    os.makedirs(model_dir, exist_ok=True)

    dataset = DDIDataset(data_path)

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

    epochs      = 20
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

        # ── Save best model ────────────────────────────────────────
        if val_acc > best_val_acc:
            best_val_acc = val_acc
            torch.save(model.state_dict(), model_path)
            print(f"  ✓ New best val_acc={val_acc:.1%} — model saved to {model_path}")

    print(f"\nTraining complete. Best val_accuracy={best_val_acc:.1%}")
    print(f"Model saved to: {model_path}")


if __name__ == "__main__":
    train_model()

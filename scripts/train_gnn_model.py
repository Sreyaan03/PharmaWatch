import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torch.utils.data import Dataset
from torch_geometric.loader import DataLoader
from torch_geometric.data import Data
from torch_geometric.nn import GCNConv, global_mean_pool
import pandas as pd
from rdkit import Chem
import os

# 1. Chemical to Graph Converter
def smiles_to_graph(smiles):
    """Converts a SMILES string into a PyTorch Geometric Graph (Nodes & Edges)"""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None: return None
        
    # Create Node Features (Atom properties)
    node_features = []
    for atom in mol.GetAtoms():
        symbol = atom.GetSymbol()
        # Simplified one-hot encoding for atoms + physical traits
        encoding = [
            1.0 if symbol == 'C' else 0.0,
            1.0 if symbol == 'N' else 0.0,
            1.0 if symbol == 'O' else 0.0,
            1.0 if symbol == 'F' else 0.0,
            1.0 if symbol == 'S' else 0.0,
            1.0 if symbol == 'Cl' else 0.0,
            float(atom.GetDegree()), 
            float(atom.GetFormalCharge())
        ]
        node_features.append(encoding)
        
    x = torch.tensor(node_features, dtype=torch.float)
    
    # Create Edges (Chemical Bonds)
    edge_indices = []
    for bond in mol.GetBonds():
        i = bond.GetBeginAtomIdx()
        j = bond.GetEndAtomIdx()
        # Undirected graph means bonds go both ways
        edge_indices += [[i, j], [j, i]] 
        
    if len(edge_indices) > 0:
        edge_index = torch.tensor(edge_indices, dtype=torch.long).t().contiguous()
    else:
        edge_index = torch.empty((2, 0), dtype=torch.long)
        
    return Data(x=x, edge_index=edge_index)

# 2. PyTorch Dataset Loader
class DDIDataset(Dataset):
    def __init__(self, parquet_path):
        print(f"Loading data from {parquet_path}...")
        # Only take a subset (e.g., 5000 records) to train quickly for demonstration purposes
        self.df = pd.read_parquet(parquet_path).dropna(subset=['X1', 'X2']).head(5000)
        
    def __len__(self):
        return len(self.df)
        
    def __getitem__(self, idx):
        row = self.df.iloc[idx]
        
        # Convert both drugs to Graphs
        graph1 = smiles_to_graph(row['X1'])
        graph2 = smiles_to_graph(row['X2'])
        
        # Fallback for invalid SMILES strings
        if graph1 is None: graph1 = Data(x=torch.zeros((1,8)), edge_index=torch.empty((2,0), dtype=torch.long))
        if graph2 is None: graph2 = Data(x=torch.zeros((1,8)), edge_index=torch.empty((2,0), dtype=torch.long))
        
        label = torch.FloatTensor([1.0]) # TWOSIDES contains positive interactions
        return graph1, graph2, label

# 3. Graph Neural Network Architecture
class GNN_Predictor(nn.Module):
    def __init__(self, node_feature_dim=8, hidden_dim=64):
        super().__init__()
        # Graph Convolution Layers (Learns from the structure of the molecule)
        self.conv1 = GCNConv(node_feature_dim, hidden_dim)
        self.conv2 = GCNConv(hidden_dim, hidden_dim)
        
        # Linear Classifier (Combines the two molecules to predict interaction)
        self.fc1 = nn.Linear(hidden_dim * 2, 32)
        self.fc2 = nn.Linear(32, 1)
        
    def process_molecule(self, data):
        """Passes a single molecule through the Graph Convolutions"""
        x, edge_index, batch = data.x, data.edge_index, data.batch
        
        # If no batch vector exists (e.g. single inference), create one
        if batch is None: batch = torch.zeros(x.size(0), dtype=torch.long)
            
        x = F.relu(self.conv1(x, edge_index))
        x = F.relu(self.conv2(x, edge_index))
        
        # Pool all atoms into one single vector that represents the entire drug
        x = global_mean_pool(x, batch)
        return x

    def forward(self, drug1_data, drug2_data):
        # 1. Get structural embeddings for both drugs
        emb1 = self.process_molecule(drug1_data)
        emb2 = self.process_molecule(drug2_data)
        
        # 2. Combine them and predict
        combined = torch.cat([emb1, emb2], dim=1)
        out = F.relu(self.fc1(combined))
        out = self.fc2(out) # Outputs logits (not probabilities)
        return out

# 4. The Actual Training Loop
def train_model():
    dataset = DDIDataset("data/twosides.parquet")
    # PyTorch Geometric DataLoader handles batching the Graphs automatically
    dataloader = DataLoader(dataset, batch_size=128, shuffle=True)
    
    model = GNN_Predictor(node_feature_dim=8, hidden_dim=64)
    criterion = nn.BCEWithLogitsLoss()
    optimizer = optim.Adam(model.parameters(), lr=0.001)
    
    epochs = 5
    print(f"Starting GNN training for {epochs} epochs...")
    
    model.train()
    for epoch in range(epochs):
        total_loss = 0
        for batch_idx, (g1, g2, labels) in enumerate(dataloader):
            optimizer.zero_grad()
            
            outputs = model(g1, g2)
            loss = criterion(outputs, labels)
            
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
            
            if batch_idx % 100 == 0:
                print(f"Epoch {epoch+1}/{epochs} | Batch {batch_idx} | Loss: {loss.item():.4f}")
                
        print(f"--- Epoch {epoch+1} Complete | Avg Loss: {total_loss/len(dataloader):.4f} ---")
        
    os.makedirs("backend/trained_models", exist_ok=True)
    torch.save(model.state_dict(), "backend/trained_models/twosides_gnn_model.pth")
    print("GNN successfully trained and saved!")

if __name__ == "__main__":
    train_model()

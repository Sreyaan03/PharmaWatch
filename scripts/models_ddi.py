# scripts/models_ddi.py
# ─────────────────────────────────────────────────────────────────────────────
# GINEConv Drug-Drug Interaction Architecture:
# - Rich 22-d node features + 10-d edge features
# - Shared-weight Siamese GINEConv encoder with edge message passing
# - Mean + Max + Sum multi-pooling readout
# - Toggleable auxiliary Morgan fingerprint branch (radius 2, 2048-bit ECFP4)
# - Mathematically symmetric pair head: [a+b, a*b, |a-b|] ensuring f(A,B) == f(B,A)
# - Batch forward pass deduplication for high throughput
# ─────────────────────────────────────────────────────────────────────────────

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.data import Data, Batch
from torch_geometric.nn import GINEConv, global_mean_pool, global_max_pool, global_add_pool
from rdkit import Chem
from rdkit.Chem import AllChem
from rdkit import RDLogger

RDLogger.DisableLog('rdApp.*')

# ── 1. Molecular Graph Featurization ─────────────────────────────────────────

# Atom symbols to one-hot encode
ATOM_LIST = ['C', 'N', 'O', 'F', 'S', 'Cl', 'P', 'Br', 'I']
# Hybridization states to one-hot encode
HYBRIDIZATION_LIST = [
    Chem.rdchem.HybridizationType.SP,
    Chem.rdchem.HybridizationType.SP2,
    Chem.rdchem.HybridizationType.SP3,
    Chem.rdchem.HybridizationType.SP3D,
    Chem.rdchem.HybridizationType.SP3D2,
]
# Bond stereo states
STEREO_LIST = [
    Chem.rdchem.BondStereo.STEREOZ,
    Chem.rdchem.BondStereo.STEREOE,
    Chem.rdchem.BondStereo.STEREONONE,
]


def smiles_to_molecular_graph(smiles: str):
    """
    Converts a SMILES string into a PyTorch Geometric Data object with
    22-dimensional atom features and 10-dimensional bond features.
    """
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return None

    # ── Node Features (22-d) ─────────────────────────────────────────────────
    node_features = []
    for atom in mol.GetAtoms():
        sym = atom.GetSymbol()
        sym_one_hot = [1.0 if sym == s else 0.0 for s in ATOM_LIST]
        sym_other = 1.0 if sym not in ATOM_LIST else 0.0

        hyb = atom.GetHybridization()
        hyb_one_hot = [1.0 if hyb == h else 0.0 for h in HYBRIDIZATION_LIST]
        hyb_other = 1.0 if hyb not in HYBRIDIZATION_LIST else 0.0

        feat = (
            sym_one_hot + [sym_other] +  # 10
            [float(atom.GetDegree())] +  # 1
            [float(atom.GetFormalCharge())] +  # 1
            [float(atom.GetTotalNumHs())] +  # 1
            [1.0 if atom.GetIsAromatic() else 0.0] +  # 1
            [1.0 if atom.IsInRing() else 0.0] +  # 1
            [1.0 if atom.GetChiralTag() != Chem.rdchem.ChiralType.CHI_UNSPECIFIED else 0.0] +  # 1
            hyb_one_hot + [hyb_other]  # 6
        )
        node_features.append(feat)

    x = torch.tensor(node_features, dtype=torch.float)

    # ── Edge Features (10-d) ─────────────────────────────────────────────────
    edge_indices = []
    edge_features = []

    for bond in mol.GetBonds():
        i = bond.GetBeginAtomIdx()
        j = bond.GetEndAtomIdx()

        bt = bond.GetBondType()
        bt_one_hot = [
            1.0 if bt == Chem.rdchem.BondType.SINGLE else 0.0,
            1.0 if bt == Chem.rdchem.BondType.DOUBLE else 0.0,
            1.0 if bt == Chem.rdchem.BondType.TRIPLE else 0.0,
            1.0 if bt == Chem.rdchem.BondType.AROMATIC else 0.0,
        ]
        is_conj = 1.0 if bond.GetIsConjugated() else 0.0
        in_ring = 1.0 if bond.IsInRing() else 0.0

        st = bond.GetStereo()
        st_one_hot = [1.0 if st == s else 0.0 for s in STEREO_LIST]
        st_other = 1.0 if st not in STEREO_LIST else 0.0

        bfeat = bt_one_hot + [is_conj, in_ring] + st_one_hot + [st_other]  # 4 + 1 + 1 + 3 + 1 = 10

        # Undirected edge
        edge_indices.append([i, j])
        edge_features.append(bfeat)
        edge_indices.append([j, i])
        edge_features.append(bfeat)

    if len(edge_indices) > 0:
        edge_index = torch.tensor(edge_indices, dtype=torch.long).t().contiguous()
        edge_attr = torch.tensor(edge_features, dtype=torch.float)
    else:
        edge_index = torch.empty((2, 0), dtype=torch.long)
        edge_attr = torch.empty((0, 10), dtype=torch.float)

    return Data(x=x, edge_index=edge_index, edge_attr=edge_attr)


def smiles_to_fingerprint(smiles: str, radius: int = 2, n_bits: int = 2048):
    """Generates a 2048-bit radius-2 Morgan fingerprint (ECFP4) as FloatTensor."""
    mol = Chem.MolFromSmiles(smiles)
    if mol is None:
        return torch.zeros(n_bits, dtype=torch.float)
    bv = AllChem.GetMorganFingerprintAsBitVect(mol, radius, nBits=n_bits)
    arr = [float(b) for b in bv]
    return torch.tensor(arr, dtype=torch.float)


# ── 2. Siamese GINEConv Molecular Graph Encoder ──────────────────────────────

class GINE_DrugEncoder(nn.Module):
    """
    4-layer GINEConv encoder with edge-feature integration, residual connections,
    and mean+max+sum global pooling, coupled with an optional Morgan fingerprint branch.
    """
    def __init__(
        self,
        node_in_dim: int = 22,
        edge_in_dim: int = 10,
        hidden_dim: int = 128,
        num_layers: int = 4,
        dropout: float = 0.1,
        use_fingerprint: bool = True,
        fp_dim: int = 2048
    ):
        super().__init__()
        self.hidden_dim = hidden_dim
        self.num_layers = num_layers
        self.use_fingerprint = use_fingerprint

        # Initial node projection
        self.node_init = nn.Sequential(
            nn.Linear(node_in_dim, hidden_dim),
            nn.BatchNorm1d(hidden_dim),
            nn.ReLU()
        )

        # Edge feature projectors for each GINEConv layer
        self.edge_projs = nn.ModuleList([
            nn.Linear(edge_in_dim, hidden_dim) for _ in range(num_layers)
        ])

        # GINEConv layers with 2-layer MLPs
        self.convs = nn.ModuleList()
        self.bns = nn.ModuleList()
        for _ in range(num_layers):
            mlp = nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim),
                nn.BatchNorm1d(hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, hidden_dim)
            )
            self.convs.append(GINEConv(mlp, edge_dim=hidden_dim))
            self.bns.append(nn.BatchNorm1d(hidden_dim))

        self.dropout = nn.Dropout(dropout)

        # Multi-pooling projection: concat mean(128) + max(128) + sum(128) = 384 -> 256
        self.readout_proj = nn.Sequential(
            nn.Linear(hidden_dim * 3, 256),
            nn.BatchNorm1d(256),
            nn.ReLU(),
            nn.Dropout(dropout)
        )

        # Auxiliary Morgan fingerprint branch
        if use_fingerprint:
            self.fp_mlp = nn.Sequential(
                nn.Linear(fp_dim, 256),
                nn.BatchNorm1d(256),
                nn.ReLU(),
                nn.Dropout(dropout),
                nn.Linear(256, 128),
                nn.BatchNorm1d(128),
                nn.ReLU()
            )
            self.drug_dim = 256 + 128  # 384
        else:
            self.drug_dim = 256

    def forward(self, batch_graph, fp_batch=None):
        """Encodes a batched graph (and optional fingerprint tensor) into drug embeddings."""
        x, edge_index, edge_attr, batch = (
            batch_graph.x,
            batch_graph.edge_index,
            batch_graph.edge_attr,
            batch_graph.batch
        )
        if batch is None:
            batch = torch.zeros(x.size(0), dtype=torch.long, device=x.device)

        h = self.node_init(x)

        for i in range(self.num_layers):
            if edge_attr.size(0) > 0:
                edge_emb = self.edge_projs[i](edge_attr)
            else:
                edge_emb = torch.empty((0, self.hidden_dim), device=x.device)
            h_conv = self.convs[i](h, edge_index, edge_emb)
            h_conv = self.bns[i](h_conv)
            h_conv = F.relu(h_conv)
            h_conv = self.dropout(h_conv)
            h = h + h_conv  # residual connection

        # Readout: mean + max + sum pooling
        mean_p = global_mean_pool(h, batch)
        max_p = global_max_pool(h, batch)
        sum_p = global_add_pool(h, batch)
        multi_pooled = torch.cat([mean_p, max_p, sum_p], dim=-1)
        graph_emb = self.readout_proj(multi_pooled)

        if self.use_fingerprint:
            if fp_batch is None:
                raise ValueError("Fingerprint branch enabled but fp_batch not provided")
            fp_emb = self.fp_mlp(fp_batch)
            return torch.cat([graph_emb, fp_emb], dim=-1)
        return graph_emb


# ── 3. Mathematically Symmetric Pair Interaction Head ────────────────────────

class SymmetricPairHead(nn.Module):
    """
    Permutation-symmetric interaction head:
    Given drug representations a and b:
      s_sum  = a + b
      s_prod = a * b
      s_diff = |a - b|
    Concat [s_sum, s_prod, s_diff] -> Linear -> LayerNorm -> ReLU -> Dropout -> Linear -> logit
    Strictly guarantees f(A, B) == f(B, A).
    """
    def __init__(self, drug_dim: int = 384, hidden_dim: int = 256, dropout: float = 0.2):
        super().__init__()
        in_dim = drug_dim * 3  # [a+b, a*b, |a-b|]
        self.mlp = nn.Sequential(
            nn.Linear(in_dim, hidden_dim),
            nn.LayerNorm(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, 1)
        )

    def forward(self, a, b):
        s_sum = a + b
        s_prod = a * b
        s_diff = torch.abs(a - b)
        h = torch.cat([s_sum, s_prod, s_diff], dim=-1)
        return self.mlp(h)


# ── 4. Complete GINE DDI Predictor with Deduplication ────────────────────────

class GINEDDIPredictor(nn.Module):
    """
    End-to-end Siamese GINE DDI predictor with batch unique molecule deduplication.
    """
    def __init__(
        self,
        node_in_dim: int = 22,
        edge_in_dim: int = 10,
        hidden_dim: int = 128,
        num_layers: int = 4,
        dropout: float = 0.1,
        use_fingerprint: bool = True
    ):
        super().__init__()
        self.encoder = GINE_DrugEncoder(
            node_in_dim=node_in_dim,
            edge_in_dim=edge_in_dim,
            hidden_dim=hidden_dim,
            num_layers=num_layers,
            dropout=dropout,
            use_fingerprint=use_fingerprint
        )
        self.pair_head = SymmetricPairHead(
            drug_dim=self.encoder.drug_dim,
            hidden_dim=256,
            dropout=0.2
        )
        self.use_fingerprint = use_fingerprint

    def forward_pairs(self, unique_graphs, unique_fps, pair_indices):
        """
        Embeds each unique drug in the batch once, then indexes into pairs.
        Args:
          unique_graphs: PyG Batch of unique molecules in batch
          unique_fps: Tensor of unique fingerprints [U, 2048] (or None)
          pair_indices: LongTensor of shape [B, 2] indexing into unique molecules
        """
        unique_embs = self.encoder(unique_graphs, unique_fps)
        a = unique_embs[pair_indices[:, 0]]
        b = unique_embs[pair_indices[:, 1]]
        return self.pair_head(a, b)

    def forward(self, g1, g2, fp1=None, fp2=None):
        """Standard PyTorch module forward pass for a pair of drugs."""
        if fp1 is not None and fp1.dim() == 1:
            fp1 = fp1.unsqueeze(0)
        if fp2 is not None and fp2.dim() == 1:
            fp2 = fp2.unsqueeze(0)
        return self.forward_individual(g1, g2, fp1, fp2)

    def forward_individual(self, g1, g2, fp1=None, fp2=None):
        """Forward pass for two paired inputs."""
        if fp1 is not None and fp1.dim() == 1:
            fp1 = fp1.unsqueeze(0)
        if fp2 is not None and fp2.dim() == 1:
            fp2 = fp2.unsqueeze(0)
        emb1 = self.encoder(g1, fp1)
        emb2 = self.encoder(g2, fp2)
        return self.pair_head(emb1, emb2)

    def predict_pair(self, smiles_a: str, smiles_b: str, temperature: float = 1.0):
        """
        Inference helper for arbitrary SMILES strings.
        Returns dict with logit, calibrated probability, and interaction likelihood score.
        """
        self.eval()
        g1 = smiles_to_molecular_graph(smiles_a)
        g2 = smiles_to_molecular_graph(smiles_b)
        if g1 is None or g2 is None:
            raise ValueError("Could not parse molecular graphs for given SMILES")

        device = next(self.parameters()).device
        g1 = g1.to(device)
        g2 = g2.to(device)

        fp1 = smiles_to_fingerprint(smiles_a).unsqueeze(0).to(device) if self.use_fingerprint else None
        fp2 = smiles_to_fingerprint(smiles_b).unsqueeze(0).to(device) if self.use_fingerprint else None

        with torch.no_grad():
            emb1 = self.encoder(g1, fp1)
            emb2 = self.encoder(g2, fp2)
            logit = self.pair_head(emb1, emb2).item()
            calibrated_prob = torch.sigmoid(torch.tensor(logit / temperature)).item()

        return {
            'logit': logit,
            'interaction_likelihood_score': round(calibrated_prob, 4),
            'temperature_applied': temperature
        }

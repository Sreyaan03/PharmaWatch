# Summary 04-02: GINEConv Molecular Graph Encoder, Multi-Pooling, Morgan FP Branch, and Symmetric Pair Head

## What was built
1. **Rich Molecular Graph Featurizer (`scripts/models_ddi.py`)**:
   - `smiles_to_molecular_graph()`:
     - 22 Node Features: one-hot atomic symbol (C, N, O, F, S, Cl, P, Br, I, other), degree, formal charge, total hydrogens, aromaticity, ring membership, chirality tag, one-hot hybridization (SP, SP2, SP3, SP3D, SP3D2, other).
     - 10 Edge Features: one-hot bond type (single, double, triple, aromatic), conjugation, ring membership, one-hot stereo (Z, E, NONE, other).
     - Symmetric undirected edge representation ($[u, v]$ and $[v, u]$).
   - `smiles_to_fingerprint()`: 2048-bit radius-2 Morgan fingerprint (ECFP4).
2. **Siamese GINEConv Encoder (`GINE_DrugEncoder`)**:
   - 4 layers of `GINEConv` message passing with edge projections and residual connections ($h + \text{conv}(h, e)$).
   - Multi-pooling readout: concatenation of `global_mean_pool`, `global_max_pool`, and `global_add_pool` (sum) $\to 384$-d, projected via Linear to 256-d.
   - Auxiliary Morgan fingerprint branch ($2048 \to 256 \to 128$) fused with graph embedding ($384$-d total drug representation). Toggleable via `use_fingerprint=False` for ablation.
3. **Mathematically Symmetric Pair Head (`SymmetricPairHead`)**:
   - Input: $a, b \in \mathbb{R}^{B \times D}$.
   - Interaction representation: $h = [a+b, a \odot b, |a-b|] \in \mathbb{R}^{B \times 3D}$.
   - MLP with LayerNorm, ReLU, and Dropout outputting raw logit.
   - Mathematical symmetry guarantee: $f(A, B) \equiv f(B, A)$ within floating-point tolerance ($10^{-6}$).
4. **End-to-End Predictor with Batch Deduplication (`GINEDDIPredictor`)**:
   - `forward_pairs`: evaluates GNN encoder once per unique molecule in a batch and indexes into pair embeddings for fast batch execution.
   - `predict_pair`: single pair inference returning logit, calibrated score, and applied temperature.
5. **Unit Verification Suite (`backend/tests/test_ddi_model.py`)**:
   - 4 tests verifying node/edge feature dimensions, pair head permutation invariance ($|f(A, B) - f(B, A)| < 10^{-6}$), end-to-end model symmetry on Aspirin/Warfarin/Metformin, and ablation branch toggling.
   - 100% passing.

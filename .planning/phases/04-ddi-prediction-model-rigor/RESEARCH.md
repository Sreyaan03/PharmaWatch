# Phase 04 Research: DDI Prediction Model Scientific Rigor

## 1. Domain & Problem Formulation

### Scientific Honesty in DDI Modeling
Drug-Drug Interaction (DDI) prediction models in computational pharmacology often suffer from subtle forms of data leakage and overoptimistic evaluation:
1. **Pair-level random splitting vs. Drug-level splitting**:
   - Randomly splitting (drugA, drugB) pairs allows both drugA and drugB to appear in the training set (in other pairs). This evaluates transductive interpolation ($S_0$), where the model simply memorizes drug affinities.
   - Clinical utility demands inductive generalization to new candidates ($S_1$: one drug unseen, $S_2$: both drugs unseen, cold-start).
2. **Negative Sampling Reality**:
   - In TDC DrugBank (~1,706 approved drugs), ~191,808 interactions are documented.
   - Non-listed pairs are **unlabeled**, not confirmed safe.
   - Treating non-listed pairs as strictly "safe" or hard negative ground truth is scientifically flawed. However, under standard binary cross-entropy formulation, pairing unlisted combinations 1:1 and resampling fresh every epoch prevents overfitting to an arbitrary fixed negative sample and prevents label leakage.
3. **Loss Function**:
   - Using artificial soft targets like $0.868 / 0.132$ distorts standard calibrated logits.
   - Standard practice uses `BCEWithLogitsLoss` on hard $0/1$ labels with dynamic negative sampling, followed by post-hoc temperature scaling on held-out validation data.
4. **Permutation Invariance**:
   - A DDI between Drug A and Drug B is symmetric: $f(A, B) \equiv f(B, A)$.
   - Simple concatenation $[a, b]$ is order-dependent ($f(A, B) \neq f(B, A)$).
   - Symmetric aggregation $[a+b, a \odot b, |a-b|]$ guarantees $f(A, B) = f(B, A)$ within floating-point tolerance $10^{-6}$.

---

## 2. Molecular Graph Representation & Architecture

### Atom (Node) Features
Using RDKit, each atom is converted into a rich feature vector:
- Atomic Number: One-hot vector for `[C, N, O, F, S, Cl, P, Br, I, other]` (10 dimensions).
- Degree: float (`atom.GetDegree()`, 1 dim).
- Formal Charge: float (`atom.GetFormalCharge()`, 1 dim).
- Total Hydrogens: float (`atom.GetTotalNumHs()`, 1 dim).
- Aromaticity: binary (`atom.GetIsAromatic()`, 1 dim).
- In-Ring: binary (`atom.IsInRing()`, 1 dim).
- Chirality: binary (`atom.GetChiralTag() != 0`, 1 dim).
- Hybridization: One-hot vector for `[SP, SP2, SP3, SP3D, SP3D2, other]` (6 dimensions).
- **Total Node Feature Dimension: 22**.

### Bond (Edge) Features
- Bond Type: One-hot vector for `[SINGLE, DOUBLE, TRIPLE, AROMATIC]` (4 dimensions).
- Conjugated: binary (`bond.GetIsConjugated()`, 1 dim).
- In-Ring: binary (`bond.IsInRing()`, 1 dim).
- Stereo: One-hot vector for `[STEREOZ, STEREOE, STEREONONE, other]` (4 dimensions).
- **Total Edge Feature Dimension: 10**.

### GINEConv Layer Mechanics
PyTorch Geometric's `GINEConv(nn, edge_dim=edge_dim)` takes edge features into account:
$$x_i^{(k)} = h_\Theta \left( (1 + \epsilon^{(k)}) x_i^{(k-1)} + \sum_{j \in \mathcal{N}(i)} \text{ReLU}(x_j^{(k-1)} + e_{j,i}) \right)$$
where $e_{j,i}$ is mapped to the node feature dimension via an edge embedding linear layer.
- Encoder: 3 to 4 GINEConv layers with hidden dim = 128, BatchNorm1d, ReLU, residual connections, and dropout 0.1.
- Global Readout: Concatenation of `global_mean_pool`, `global_max_pool`, and `global_add_pool` (sum pooling).
  Produces $128 \times 3 = 384$ dimensions, projected via `Linear(384, 256)` to a 256-d graph embedding.

### Auxiliary Morgan Fingerprint Branch
- Radius 2, 2048-bit ECFP4 fingerprint generated via `AllChem.GetMorganFingerprintAsBitVect(mol, 2, nBits=2048)`.
- Projected through MLP: `Linear(2048, 256) -> BatchNorm1d -> ReLU -> Dropout(0.1) -> Linear(256, 128)`.
- Fused with graph embedding: `concat([graph_emb, fp_emb])` $\to 256 + 128 = 384$ dimensions.
- Toggleable flag `--use_fingerprint` allows straightforward ablation.

### Symmetric Pair Interaction Head
Given drug representations $a \in \mathbb{R}^d$ and $b \in \mathbb{R}^d$:
1. $s_{sum} = a + b$
2. $s_{prod} = a \odot b$
3. $s_{diff} = |a - b|$
4. $h = [s_{sum}, s_{prod}, s_{diff}] \in \mathbb{R}^{3d}$
5. $z = \text{Linear}(3d, 256) \to \text{LayerNorm}(256) \to \text{ReLU} \to \text{Dropout}(0.2) \to \text{Linear}(256, 1)$ (raw logit output).

Because addition, elementwise product, and absolute difference are commutative:
$[a+b, a \odot b, |a-b|] \equiv [b+a, b \odot a, |b-a|]$
The prediction is strictly invariant to drug input order: $f(A, B) \equiv f(B, A)$.

### Batch Forward Efficiency
In batch training, pairs often contain repeated drugs. Instead of passing duplicates through the GNN encoder:
1. Extract unique molecules across all pairs in the batch.
2. Collate unique graphs into a PyG `Batch` and run the encoder once.
3. Index back into the resulting embedding table using pair indices $(i, j)$ to form $a$ and $b$.
4. Pass $[a+b, a \odot b, |a-b|]$ through the pair MLP head.
This yields a $2-3\times$ speedup per epoch.

---

## 3. Split Strategy: Inductive Cold-Start Evaluation

Let $\mathcal{D}$ be the set of unique valid drugs in DrugBank (~1,706 drugs).
- Split drugs into disjoint subsets:
  - $\mathcal{D}_{test}$: 15% of drugs (e.g., ~256 drugs)
  - $\mathcal{D}_{val}$: 10% of drugs (e.g., ~170 drugs)
  - $\mathcal{D}_{train}$: 75% of drugs (e.g., ~1,280 drugs)
- **Zero-Leakage Assertion**:
  $$\mathcal{D}_{train} \cap \mathcal{D}_{val} = \emptyset, \quad \mathcal{D}_{train} \cap \mathcal{D}_{test} = \emptyset, \quad \mathcal{D}_{val} \cap \mathcal{D}_{test} = \emptyset$$
- Pair Assignment:
  - **Train Pairs**: Both drugs must belong to $\mathcal{D}_{train}$: $(u, v) \in \mathcal{D}_{train} \times \mathcal{D}_{train}$.
    *Hard assertion: No drug from $\mathcal{D}_{val} \cup \mathcal{D}_{test}$ is present in any train pair.*
  - **S0 Test Set (Warm)**: Both drugs belong to $\mathcal{D}_{train}$, but the *pair* was held out from training: $(u, v) \in \mathcal{D}_{train} \times \mathcal{D}_{train}, (u, v) \notin \mathcal{P}_{train}$.
  - **S1 Test Set (Half Cold)**: Exactly one drug in $\mathcal{D}_{train}$ and one in $\mathcal{D}_{test}$: $(u \in \mathcal{D}_{train}, v \in \mathcal{D}_{test})$ or $(u \in \mathcal{D}_{test}, v \in \mathcal{D}_{train})$.
  - **S2 Test Set (Fully Cold)**: Both drugs in $\mathcal{D}_{test}$: $(u, v) \in \mathcal{D}_{test} \times \mathcal{D}_{test}$.
  - **Validation Set**: S1-style pairs built with $(u \in \mathcal{D}_{train}, v \in \mathcal{D}_{val})$ to mimic realistic cold-start model selection.
- Early stopping and model selection are guided by **Validation AUPRC on the S1 val set**, preventing overfitting to transductive memorization.

---

## 4. Calibration & Temperature Scaling

### Mechanics
A binary classifier outputting logits $z$ has raw predicted probabilities $\hat{p} = \sigma(z)$.
Neural networks with batch normalization and deep architectures are frequently overconfident.
Post-hoc temperature scaling introduces a single learned scalar parameter $T > 0$:
$$\hat{p}_{cal} = \sigma\left(\frac{z}{T}\right)$$
$T$ is optimized on the held-out validation set to minimize negative log likelihood (BCE loss) with model weights frozen.
- Expected Calibration Error (ECE) is computed with $M=10$ or $15$ bins:
  $$\text{ECE} = \sum_{m=1}^M \frac{|B_m|}{N} |\text{acc}(B_m) - \text{conf}(B_m)|$$
- A reliability diagram is saved plotting confidence vs. empirical accuracy against the perfect calibration diagonal.

### Terminology & Product Integration
- Never label outputs "probability of harm" or "safe".
- Label outputs: **"interaction likelihood score"** ($0.0 - 1.0$).
- 3 Evidence Tiers in API:
  1. `documented`: Confirmed interaction in DrugBank/TWOSIDES with explicit phenotype.
  2. `model_predicted`: GINE score + calibrated flag + explicit disclaimer: "Hypothesis-generating prediction".
  3. `no_evidence_found`: No documented or predicted interaction detected. Never return "safe".
- API metadata disclaimer: `"Research use only, not clinical advice"`.

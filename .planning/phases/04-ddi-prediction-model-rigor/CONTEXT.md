# Phase 04 Context: DDI Prediction Model Rigor

## 1. Locked Decisions & Core Objectives

### Data & Checkpoint Naming
- Source: TDC DrugBank DDI (~1,706 approved drugs).
- Drop and log any drugs that fail RDKit parsing (e.g. organometallics or invalid valences) to `data/unparseable_drugs.log`.
- Positives = DrugBank-listed pairs. Non-listed pairs are UNLABELED, not "safe".
- Sample negatives from unlabeled pairs, 1:1 ratio, resampled fresh each epoch.
- Rename checkpoint from `twosides_gnn_model.pth` to `drugbank_gine_model.pth`.
- Keep DrugBank, TWOSIDES, and FAERS data domains strictly separated in code and documentation.

### Drug-Level Splits (No Leakage)
- Split drugs: 15% test, 10% validation, 75% training using a fixed reproducible random seed (seed=42).
- Generate 3 distinct test evaluation sets:
  - **S0 (Warm)**: Both drugs seen in training, pair unseen.
  - **S1 (One Unseen)**: Exactly one drug unseen in training.
  - **S2 (Cold-Start)**: Both drugs unseen in training.
- Hard assertion: Verify no drug in validation or test appears in any training pair.
- Checkpoint selection guided by validation AUPRC on an S1-style validation set.

### Molecular Graph Architecture
- Node features (22-d): atomic number one-hot (C, N, O, F, S, Cl, P, Br, I, other), degree, formal charge, num Hs, aromatic, in-ring, chirality tag, hybridization one-hot.
- Edge features (10-d): bond type one-hot (single, double, triple, aromatic), conjugated, in-ring, stereo.
- Model: Siamese GINEConv encoder (3-4 layers, hidden 128, BatchNorm1d + ReLU + residual + dropout 0.1).
- Multi-pooling readout: Concatenation of mean, max, and sum global pooling -> Linear(384, 256) -> 256-d drug embedding.
- Morgan fingerprint auxiliary branch: 2048-bit radius 2 -> MLP -> 128-d, concatenated with graph embedding (toggleable via `--use_fingerprint`).
- Symmetric pair interaction head: Given representations $a, b$, form $[a+b, a \odot b, |a-b|] \to \text{Linear} \to \text{LayerNorm} \to \text{ReLU} \to \text{Dropout}(0.2) \to \text{Linear} \to \text{logit}$.
- Efficiency: In batch training/inference, embed each unique drug once and index into pair rows.

### Training & Calibration Engine
- Loss: `BCEWithLogitsLoss` using hard 0/1 binary labels (no soft targets).
- Optimizer: AdamW (lr 1e-3, weight decay 1e-5), Cosine Annealing learning rate schedule, batch size 512 pairs.
- Run across 5 random seeds, reporting metric mean ± std.
- Post-hoc temperature scaling fitted via NLL optimization on validation set.
- Compute ECE and save reliability diagram to `reports/ddi_calibration_reliability_diagram.png`.
- Terminology: Designate predictions as "interaction likelihood score" (never "probability of harm" or "safe").

### Baselines & Evaluation
- Baselines on identical splits:
  - Logistic Regression on $[a+b, a \odot b, |a-b|]$ Morgan fingerprints.
  - XGBoost on $[a+b, a \odot b, |a-b|]$ Morgan fingerprints.
  - Nearest-Neighbor Tanimoto baseline: score = max Tanimoto similarity to known training interacting pairs.
- Metrics reported on S0, S1, S2: AUROC, AUPRC, Recall@100, Recall@1000.
- Export results table as CSV (`reports/ddi_benchmark_results.csv`) and Markdown (`reports/ddi_benchmark_results.md`).
- Ablation experiments: GIN vs GINE, with vs without fingerprint branch, symmetric vs concat head.

### Tests
- Permutation invariance unit test: $|f(A, B) - f(B, A)| < 10^{-6}$.
- Split leakage unit test: $\text{drugs}(\text{train}) \cap (\text{drugs}(\text{val}) \cup \text{drugs}(\text{test})) = \emptyset$.
- SMILES parsing unit test on aspirin, warfarin, metformin.

### Product & API Integration
- Backend `/api/graph/predict` returns 3 tiers:
  1. `documented`
  2. `model_predicted`
  3. `no_evidence_found`
- Caching in MySQL table `interaction_predictions` keyed on canonically sorted pair `(drugA, drugB)`.
- Metadata: Mandatory `"Research use only, not clinical advice"`.

# Summary 04-01: Data Curation, Clean SMILES Parsing, Drug-Level S0/S1/S2 Splits, and Zero-Leakage Tests

## What was built
1. **Dataset Curation & Sanitization (`scripts/curate_ddi_dataset.py`)**:
   - Ingested TDC DrugBank DDI dataset (~1,706 approved drugs).
   - Validated all 1,706 drug SMILES with RDKit.
   - Identified 1 unparseable molecule (`DB11630` / Temoporfin with invalid valence ring syntax), dropped it and logged to `data/unparseable_drugs.log`.
   - Retained 1,705 cleanly parseable drugs and 191,392 undirected positive interaction pairs.
2. **Drug-Level Disjoint Splitting**:
   - Partitioned drugs randomly with fixed seed (42):
     - Train: 1,280 drugs (75%)
     - Val: 170 drugs (10%)
     - Test: 255 drugs (15%)
   - Disjointness mathematically verified: $\mathcal{D}_{train} \cap (\mathcal{D}_{val} \cup \mathcal{D}_{test}) = \emptyset$.
3. **Inductive Evaluation Sets**:
   - `train_pairs.csv`: 95,196 positive pairs formed strictly between train drugs (with dynamic negative resampling during training).
   - `test_s0_pairs.csv`: 10,000 positive + 10,000 sampled negative pairs (warm start).
   - `test_s1_pairs.csv`: 47,400 positive + 47,400 sampled negative pairs (exactly one drug unseen).
   - `test_s2_pairs.csv`: 5,262 positive + 5,262 sampled negative pairs (both drugs unseen, complete cold-start).
   - `val_pairs.csv`: 26,025 positive + 26,025 sampled negative pairs (S1-style train $\times$ val pairs for model selection).
4. **Unit Verification Suite (`backend/tests/test_ddi_dataset.py`)**:
   - 3 comprehensive tests verifying zero leakage across splits, S0/S1/S2 properties, and SMILES parsing on aspirin, warfarin, metformin.
   - 100% passing.

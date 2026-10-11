# Requirements — PharmaWatch

## Hardening & Real Data (Phase 2)

### HARD-01: Remove hardcoded 127.0.0.1 from _interactions_new.js
Replace all 7 absolute `http://127.0.0.1:5000/...` fetch URLs in `src/_interactions_new.js` with relative paths (`/api/...`). The frontend and backend are always served from the same origin in development.

### HARD-02: Remove hardcoded 127.0.0.1 from app.js
Replace all absolute `http://127.0.0.1:5000/...` fetch URLs in `src/app.js` with relative paths. Count verified: 9+ occurrences at lines 523, 525, 814, 892, 1423, 1424, 1425, 1580, 1581, 1582, 2004.

### HARD-03: Remove hardcoded 127.0.0.1 from ml_models.js
Replace all 3 absolute `http://127.0.0.1:5000/...` fetch URLs in `src/ml_models.js` with relative paths. Lines 65, 170, 304.

### HARD-04: Replace fake Drug Trend Chart with LSTM data
`src/app.js:586` builds `fakeTrend` using `Math.random()`. Replace with real data from `/api/lstm?drug={drug}` — use the `actual` array as the trend line. Fall back to a flat line at `totalReportsLocal/12` if LSTM call fails.

### HARD-05: Replace fake Signal Intensity Chart with real backend data
`src/app.js:657-695` uses `randomArray()` to generate 3 series of 24 fake datapoints. Add backend endpoint `/api/dashboard/signal-intensity` that queries the SQLite `signals` cache table, groups by severity (critical/high/moderate) and buckets into 24 hourly time slots. Frontend polls this endpoint and falls back to zeroed arrays if unavailable.

### HARD-06: Replace fake Drug Category Doughnut with openFDA data
`src/app.js:698-719` uses hardcoded `[31, 18, 22, 14, 9, 6]`. Add backend endpoint `/api/dashboard/drug-categories` that calls openFDA `drug/event.json?count=patient.drug.medicinalproduct.exact` for known category seed drugs and returns real proportional counts. Frontend calls this on dashboard init.

### HARD-07: Add /api/local-stats backend endpoint
The FEATURE_AUDIT recommends pulling animated counters from a real `/api/local-stats` endpoint. Create endpoint in `backend/app.py` that returns: `{ drugs_monitored, signals_detected, reports_analyzed, drug_event_pairs }` from SQLite. Use SQLite `SELECT COUNT(DISTINCT drug)`, `COUNT(*)` from `signals` and `drug_events` tables.

### HARD-08: Fix CORS scope + pipeline disclaimer badge
1. CORS: Change `CORS(app)` from wildcard to `CORS(app, origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:5000,http://127.0.0.1:5000").split(","))`
2. Pipeline section: Add a visible "Simulated — Architecture Demo Only" badge/banner above the Kafka/Spark/HDFS metrics so users understand they are illustrative.

## Backend Architecture (Phase 3)

### ARCH-01: Split app.py into Flask Blueprints
Modularize the 2928-line `backend/app.py` into blueprint modules:
- `backend/routes/drugs.py` — drug label, events, autocomplete, trials
- `backend/routes/signals.py` — PRR, signals, signal network, NER
- `backend/routes/interactions.py` — graph, predict, polypharmacy, uncharted, recent
- `backend/routes/boxed_warnings.py` — boxed warning, events, timeline, bias analysis, violations
- `backend/routes/ml.py` — LSTM train/predict, BioBERT, gap-scan
- `backend/routes/system.py` — HBase status, system start, local-stats, reddit
- `backend/app.py` becomes a thin entry point (< 200 lines): registers blueprints, initializes extensions

### ARCH-02: Add python-dotenv .env loading
Add `from dotenv import load_dotenv; load_dotenv()` at top of app.py. Read `FLASK_SECRET_KEY`, `FDA_API_KEY`, `ALLOWED_ORIGINS`, `HBASE_HOST`, `HBASE_PORT` from `.env`. Update `backend/.env.example` with documented keys.

## DDI Prediction Model Rigor (Phase 4)

### DDI-DATA: TDC DrugBank Curation & Negative Resampling
- Download/cache TDC DrugBank DDI dataset (~1,706 unique drugs, ~191,808 interactions).
- Parse SMILES via RDKit, filter out unparseable molecules and log dropped drugs.
- Treat non-listed pairs as unlabeled candidates. Resample unlabeled negative pairs 1:1 fresh every epoch.
- Separate DrugBank, TWOSIDES, and FAERS data domains in code and comments.
- Rename model checkpoint to `drugbank_gine_model.pth` (eliminating obsolete "twosides_" naming).

### DDI-SPLIT: Drug-Level Splitting & Cold-Start Test Sets (S0, S1, S2)
- Split drugs at the molecule level (10% validation, 15% test, 75% train) using fixed random seeds.
- Construct 3 distinct evaluation regimes:
  - **S0 (Warm)**: Both drugs observed during training.
  - **S1 (Half-Cold)**: Exactly one drug unseen during training.
  - **S2 (Cold-Start)**: Both drugs completely unseen during training.
- Hard assertion in code verifying 0 training pairs contain any validation or test drug.
- Checkpoint selection keyed on S1-style validation AUPRC.

### DDI-GRAPH: Rich Molecular Graph Representation with Edge Features
- Atom Node Features: Atomic number one-hot (C, N, O, F, S, Cl, P, Br, I, +other), degree, formal charge, total num Hs, aromaticity, ring membership, chirality tag, hybridization state one-hot.
- Bond Edge Features: Bond type one-hot (SINGLE, DOUBLE, TRIPLE, AROMATIC), conjugation boolean, ring membership, stereochemistry tag.

### DDI-MODEL: Shared-Weight Siamese GINEConv Encoder & Symmetric Head
- Encoder: 3-4 GINEConv layers leveraging edge feature embeddings, hidden dimension 128, BatchNorm + ReLU + residual skip connections + dropout 0.1.
- Readout: Multi-pooling combining `global_mean_pool`, `global_max_pool`, and `global_add_pool` -> Linear projection to 256-d drug embedding.
- Morgan Fingerprint Branch: Auxiliary 2048-bit radius-2 Morgan fingerprint -> MLP -> 128-d, concatenated with graph embedding (toggleable for ablation).
- Symmetric Pair Head: Given drug representations $a, b$, form symmetric interaction representation $[a+b, a \odot b, |a-b|]$ -> Linear -> LayerNorm -> ReLU -> Dropout(0.2) -> Linear -> raw logit.
- Efficiency: In batch forward passes, embed each unique drug in the batch once and index into representations.

### DDI-TRAIN: Robust Training Engine with 5 Seeds
- Loss: `BCEWithLogitsLoss` using hard 0/1 labels (no ungrounded soft targets).
- Optimizer: AdamW (lr 1e-3, weight decay 1e-5), Cosine Annealing learning rate schedule, batch size 512 pairs.
- Early stopping on validation AUPRC. Run across 5 random seeds reporting mean ± std.

### DDI-CALIB: Temperature Scaling Calibration & Reliability Diagrams
- Post-hoc temperature scaling fitted via NLL optimization on the validation set.
- Compute Expected Calibration Error (ECE) and save a visual reliability diagram.
- Terminology rule: Designate outputs as "interaction likelihood score" (never "probability of harm").

### DDI-BASE: Classical ML & Similarity Baselines
- Feature representation: Morgan fingerprint pairs combined via $[a+b, a \odot b, |a-b|]$.
- Train and evaluate XGBoost and Logistic Regression classifiers on the exact same splits.
- Nearest-neighbor similarity baseline: score = max Tanimoto similarity to known training interacting pairs.

### DDI-EVAL: Benchmarks & Ablation Matrix
- For S0, S1, and S2, calculate and report: AUROC, AUPRC, Recall@100, and Recall@1000.
- Export results comparison table (GINE vs Baselines across S0/S1/S2) as CSV and Markdown.
- Run ablation experiments: GIN vs GINE, with vs without Morgan fingerprint branch, symmetric vs concatenation head.

### DDI-TEST: Unit Tests & Numerical Verification
- Invariance unit test: Verify $f(A, B) == f(B, A)$ within numerical tolerance $10^{-6}$.
- Data leakage unit test: Verify disjoint drug sets across train, val, and test splits.
- Molecule parsing unit test: Verify valid graph generation for aspirin, warfarin, and metformin.

### DDI-PROD: 3-Tier Backend API Integration
- Update `/api/graph/predict` and polypharmacy endpoints to return 3 distinct evidence tiers:
  1. `documented`: Confirmed interaction from curated database with phenotypes.
  2. `model_predicted`: GINE score + calibrated flag + explicit note that it is hypothesis-generating.
  3. `no_evidence_found`: No documented or high-likelihood interaction (never label "safe").
- Cache predictions in MySQL table `interaction_predictions` keyed on canonically sorted drug pair tuples `(drugA, drugB)`.
- Include mandatory "Research use only, not clinical advice" disclaimer in API metadata.

## GNN Training (Phase 1 — COMPLETE)

### GNN-01: Balanced dataset — DONE (commit 25d9a7d)
### GNN-02: 16-feature atom encoding — DONE (commit 25d9a7d)
### GNN-03: 20 epochs with val split — DONE (commit 25d9a7d)
### GNN-04: Side-effects on graph edges — DONE (commit a68b2f3)
### GNN-05: Safe-graph rendering fix — DONE (commit a68b2f3)
### GNN-06: D3 zoom/pan + reset button — DONE (commit a68b2f3)
### GNN-07: Parallel PubChem resolution — DONE (commit a68b2f3)


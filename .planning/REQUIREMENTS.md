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

## Milestone 2 — University Pilot Release (Phases 5–11)

Context, evidence and architecture: `.planning/MILESTONE-2-CONTEXT.md`.

### Clean Architecture (Phase 5)

- **CLEAN-01: Layered structure.** Code is organised as `core/`, `routes/`, `services/`, `faers/`, `clients/`, `ml/`, `ingest/`, `validation/`, `db/`. Routes hold HTTP handling only.
- **CLEAN-02: No behaviour change.** Every endpoint returns the same status and top-level keys as before the move, proven by a route-inventory test.
- **CLEAN-03: One copy of each helper.** Admin-key check, cache helper, z-score spike detection, HBase pair lookup and the openFDA HTTP wrapper each exist once.
- **CLEAN-04: Dead code removed.** Everything in the Phase 5 removal table of the context document is deleted.
- **CLEAN-05: Clean imports.** No module modifies `sys.path`; DDI model definitions live in `backend/ml/`.

### Data Foundation (Phase 6)

- **DATA-01: Real FAERS.** The 20 most recently released quarterly ASCII files are loaded. Mock generation exists only in test fixtures and the loader rejects mock files.
- **DATA-02: Case de-duplication.** `caseid` and `caseversion` are kept and only the latest version of each case is counted.
- **DATA-03: Complete fields.** `age_cod` normalises age to years; THER (therapy dates) and OUTC (outcomes) are loaded.
- **DATA-04: Quarter registry.** A table records each loaded quarter, its row counts, checksum and load time, and supplies `data_version`.
- **DATA-05: Identity.** Drugs resolve to an RxNorm ingredient through RxNav with a local cache. Events are validated against the Preferred Terms present in the loaded data. The watchlist uses valid terms.
- **DATA-06: Single statistics path.** All 2×2 counts come from `faers/`. openFDA is a labelled fallback restricted to suspect drugs.

### Statistical Correctness (Phase 7)

- **STAT-01: Tested metrics.** `compute_metrics` is covered by golden tests against hand-worked tables, including zero cells.
- **STAT-02: Real time-to-onset.** Computed from therapy start and event dates; reports the count of usable cases.
- **STAT-03: Real stratified PRR.** Sex and age strata each use their own 2×2 table.
- **STAT-04: Honest classes.** A `none` class exists; only pairs meeting the criteria count as signals.
- **STAT-05: No invented numbers.** All fallbacks that synthesise values are removed; empty data returns `no_data`. Every response carries `evidence`, `data_version` and, where relevant, `model_version`.
- **STAT-06: Descriptive wording.** Boxed-warning and forecast outputs describe reporting patterns only. The forecast uses share of total reports and an out-of-sample baseline. The unverified warning-year table is removed.

### Validation (Phase 8)

- **VAL-01: Signal benchmark.** AUC, sensitivity and specificity on the OMOP and EU-ADR reference sets, with committed mappings.
- **VAL-02: NER benchmark.** Precision, recall and F1 per entity type on the ADE corpus; model named correctly.
- **VAL-03: DDI model.** Mini-batch training, separately trained ablations, calibration fitted and scored on separate folds, Precision@k, best S1 model deployed, `is_harmful` renamed, `calibrated` reported truthfully.
- **VAL-04: Reproducible report.** One command reruns all benchmarks and regenerates the Methods & Validation page.
- **VAL-05: Regression gate.** CI runs a reduced benchmark and fails on a drop below the recorded baseline.

### Prescription Review (Phase 9)

- **RX-01: Extraction.** A swappable extractor returns drug, strength, dose, route, frequency and duration with confidences. Default is the cloud vision model; local OCR is the fallback.
- **RX-02: Confirmation.** The user confirms or corrects the list before analysis.
- **RX-03: Report.** Interactions in three tiers with evidence, boxed warnings, duplicate therapy, top labelled reactions, active signals, versions, research-use statement.
- **RX-04: Export.** The report is available as PDF.
- **RX-05: Privacy.** Images are not stored by default, identifiers are kept out of logs and analysis calls, cloud use is disclosed.
- **RX-06: Measured accuracy.** Field-level accuracy on a synthetic prescription set appears on the validation page.

### Surveillance pipeline and coherence (Phase 10)

- **COH-01: Navigation.** Seven areas; every existing feature reachable.
- **COH-02: Drug profile.** One page per drug combining all evidence.
- **COH-03: Evidence badges.** Every result shows measured, modelled or simulated, and the data version.
- **KAF-01: Ingest only.** The consumer computes no statistics; the hard-coded background table and its helpers are removed.
- **KAF-02: Release trigger.** A `faers-release` event starts the batch recompute.
- **KAF-03: Linked evidence.** Alerts, papers and trials attach to drug identities; consumers are idempotent.
- **BD-01: Spark batch.** Spark computes all drug and side-effect pairs for every quarter, and the values match the app's statistics code.
- **BD-02: HBase serving.** HBase serves per-pair signal history, with a Parquet fallback.
- **BD-03: Measured engines.** Spark and DuckDB timings for the same job are published.
- **SURV-01: Label watcher.** Changes to watched drugs' FDA labels become `label-change` events.
- **SURV-02: Label-text check.** Each signal reports whether its effect is in the label text; the check's accuracy is measured.
- **SURV-03: Alerts.** New signals, not-in-label signals and label changes become reviewable alerts worded as possible signals.
- **REPLAY-01: Replay.** History replays through Kafka without touching live data.
- **REPLAY-02: Lead time.** Whether signals preceded label changes is measured, with censoring and caveats.

### Platform (Phase 11)

- **PLAT-01: Accounts and roles.** Viewer, analyst, admin, behind a replaceable auth interface.
- **PLAT-02: Audit log.** Append-only record of user, action, inputs and versions.
- **PLAT-03: Feedback.** Stored, exportable feedback on every result.
- **PLAT-04: Security.** Header-only admin key, upload limits, safe model loading, no raw exception text, persistent job state.
- **PLAT-05: Portable deployment.** Environment-driven configuration, Compose profiles, data and models as volumes, `DATA_DIR` outside synced folders.
- **PLAT-06: CI/CD.** Lint, unit, statistics and benchmark jobs; image built without data files; validation report published; deploy job unchanged.
- **PLAT-07: Documentation.** Accurate README, LICENSE, methods document, pilot guide.

## GNN Training (Phase 1 — COMPLETE)

### GNN-01: Balanced dataset — DONE (commit 25d9a7d)
### GNN-02: 16-feature atom encoding — DONE (commit 25d9a7d)
### GNN-03: 20 epochs with val split — DONE (commit 25d9a7d)
### GNN-04: Side-effects on graph edges — DONE (commit a68b2f3)
### GNN-05: Safe-graph rendering fix — DONE (commit a68b2f3)
### GNN-06: D3 zoom/pan + reset button — DONE (commit a68b2f3)
### GNN-07: Parallel PubChem resolution — DONE (commit a68b2f3)


# Roadmap: PharmaWatch — Production Excellence

## Overview

PharmaWatch is a real-time pharmacovigilance dashboard. Phase 1 fixed the GNN training pipeline and polypharmacy UX. Phase 2 eliminated hardcoded URLs and added real data sources. Phase 3 completed the backend modularization, MySQL migration, connection pooling, and CI/CD pipelines. Phase 4 overhauls the DDI prediction model in `scripts/train_gnn_model.py` to be scientifically rigorous, properly evaluated under cold-start conditions, calibrated, benchmarked against classical baselines, and integrated cleanly with a 3-tier backend API.

Milestone 2 (Phases 5–11, one phase per day) turns the project into a coherent research and education tool for pharmacy and medical colleges, with real FAERS data, tested statistics, published benchmarks and a Prescription Review workflow. Decisions, audit evidence and the target architecture are in `.planning/MILESTONE-2-CONTEXT.md`.

## Phases

### Milestone 1 — complete

- [x] **Phase 1: Interactions GNN Fix** — Balanced dataset, 16-feat atoms, 20 epochs, side-effects on graph, zoom/pan
- [x] **Phase 2: Hardening & Real Data** — Eliminate all hardcoded 127.0.0.1 URLs, replace fake charts with live data, add backend stats endpoint, fix CORS scope
- [x] **Phase 3: Backend Modularization & MySQL Production** — Split app.py into blueprints, MySQL 8.0 pooling, Caddy reverse-proxy edge, CI/CD, and security fixes
- [x] **Phase 4: DDI Prediction Model Rigor** — Scientifically honest GINE model, drug-level S0/S1/S2 splits, temperature calibration, baselines, and 3-tier API

### Milestone 2 — University Pilot Release (planned)

- [x] **Phase 5 (Day 1): Baseline & Clean Architecture** — Layered structure, duplicate consolidation, dead-code removal, no behaviour change
- [ ] **Phase 6 (Day 2): Real FAERS Data Foundation** — 20 real quarters, case de-duplication, RxNorm and Preferred Term identity, single statistics path
- [ ] **Phase 7 (Day 3): Statistical Correctness** — Real time-to-onset and stratified PRR, no-signal class, no invented numbers, descriptive wording
- [ ] **Phase 8 (Day 4): Validation Harness** — Reference-set benchmarks for signals, NER and DDI; Methods & Validation page; CI regression gate
- [ ] **Phase 9 (Day 5): Prescription Review** — Upload, cloud vision extraction, confirm step, interaction and safety report, PDF export
- [ ] **Phase 10 (Day 6): Coherence & Kafka Integration** — Regrouped navigation, drug profile page, evidence badges, Kafka as evidence ingest
- [ ] **Phase 11 (Day 7): Platform & Pilot Release** — Accounts and roles, audit log, feedback, security fixes, portable deployment, CI/CD, docs

## Phase Details

### Phase 1: Interactions GNN Fix
**Goal**: Fix GNN training pipeline and polypharmacy graph UX
**Depends on**: Nothing (first phase)
**Requirements**: [GNN-01, GNN-02, GNN-03, GNN-04, GNN-05, GNN-06, GNN-07]
**Status**: Complete (2026-06-05)

### Phase 2: Hardening & Real Data
**Goal**: Remove all hardcoded 127.0.0.1 URLs, replace fake charts with live data, add /api/local-stats backend endpoint for real counters, narrow CORS
**Depends on**: Phase 1
**Requirements**: [HARD-01, HARD-02, HARD-03, HARD-04, HARD-05, HARD-06, HARD-07, HARD-08]
**Status**: Complete (2026-06-05)

### Phase 3: Backend Modularization & MySQL Production
**Goal**: Split app.py (2928 lines) into Flask Blueprint modules, migrate to MySQL 8.0, add Docker + Caddy production stack, connection pooling, and CI/CD
**Depends on**: Phase 2
**Requirements**: [ARCH-01, ARCH-02]
**Status**: Complete (2026-10-11)

### Phase 4: DDI Prediction Model Rigor
**Goal**: Rework the DDI prediction model in PharmaWatch (scripts/train_gnn_model.py) to be scientifically honest, evaluated properly on drug-level S0/S1/S2 splits, calibrated via temperature scaling, compared to ML/similarity baselines, and integrated cleanly with a 3-tier backend API.
**Depends on**: Phase 3
**Requirements**: [DDI-DATA, DDI-SPLIT, DDI-GRAPH, DDI-MODEL, DDI-TRAIN, DDI-CALIB, DDI-BASE, DDI-EVAL, DDI-TEST, DDI-PROD]
**Status**: Complete (2026-10-12). Follow-up defects found in the 2026-10-12 audit are handled in Phase 8 (VAL-DDI).
**Success Criteria** (what must be TRUE):
  1. Drug-level splits hold out 15% test and 10% validation drugs with zero train drug leakage verified by assertion.
  2. S0 (warm), S1 (one drug unseen), and S2 (both drugs unseen) test sets evaluated with AUROC, AUPRC, and Recall@k (k=100, 1000).
  3. GINEConv encoder with rich atom (16-d+) and bond features, multi-pooling (mean+max+sum), optional Morgan fingerprint branch, and symmetric pair head where f(A,B) == f(B,A).
  4. Temperature scaling calibration produces low ECE and generates a reliability diagram; outputs designated as "interaction likelihood score" (never "probability of harm").
  5. XGBoost, Logistic Regression, and Nearest-Neighbor Tanimoto baselines benchmarked against GINE across S0/S1/S2.
  6. Backend /api/graph/predict returns 3 explicit tiers: "documented", "model_predicted", "no_evidence_found", with research-use-only metadata.

Plans:
- [x] 04-01: Dataset curation, clean SMILES parsing, drug-level S0/S1/S2 split generator, and zero-leakage test
- [x] 04-02: GINEConv molecular graph encoder, edge features, multi-pooling, Morgan FP branch, and symmetric pair head
- [x] 04-03: Training engine with dynamic negative resampling, hard BCE loss, cosine annealing, 5-seed runner, and temperature scaling calibration (ECE + reliability diagram)
- [x] 04-04: Baseline models (XGBoost, LogReg, Nearest-Neighbor), S0/S1/S2 evaluation benchmark + ablations table, and 3-tier backend API integration

### Phase 5 (Day 1): Baseline & Clean Architecture
**Goal**: Reach the layered structure in MILESTONE-2-CONTEXT section 5 by moving, consolidating and deleting code, with no change in any endpoint's output.
**Depends on**: Phase 4 (commit the untracked Phase 4 work first, then branch)
**Requirements**: [CLEAN-01, CLEAN-02, CLEAN-03, CLEAN-04, CLEAN-05]
**Success Criteria** (what must be TRUE):
  1. The 26 existing tests pass unchanged.
  2. A route-inventory test lists every endpoint and confirms each still responds with the same status and top-level keys as before the move.
  3. Route files contain no SQL, no outbound HTTP calls and no model code.
  4. Everything in the "Confirmed removals (Phase 5)" table is gone, and each consolidated helper exists in exactly one place.
  5. No module adds to `sys.path` at import time.

Plans:
- [x] 05-01: Commit Phase 4, capture the route inventory snapshot, create `core/`, `services/`, `clients/`, `ml/`, `ingest/`, `validation/` and move code
- [x] 05-02: Consolidate duplicates (admin-key decorator, cache helper, z-score, HBase lookup, HTTP wrapper) and merge duplicate routes behind aliases
- [x] 05-03: Delete dead code and artefacts, update Docker and CI paths, add the route-inventory test to CI

### Phase 6 (Day 2): Real FAERS Data Foundation
**Goal**: Replace the 3,800-report sample with 20 real, de-duplicated FAERS quarters and give every drug and event one identity across the app.
**Depends on**: Phase 5
**Requirements**: [DATA-01, DATA-02, DATA-03, DATA-04, DATA-05, DATA-06]
**Success Criteria** (what must be TRUE):
  1. The quarter registry lists 20 loaded quarters with row counts and file checksums, and the loader refuses mock files.
  2. Each case appears once, at its latest version; a fixture test proves it.
  3. Ages are in years regardless of the reported unit; a fixture test proves it.
  4. Therapy start dates and outcomes are loaded.
  5. A brand name, a generic name and a salt form of one drug resolve to the same RxNorm ingredient.
  6. The same drug–event pair returns the same 2×2 counts from every endpoint that reports them.
  7. Every watchlist event is a Preferred Term present in the loaded data.

Plans:
- [ ] 06-01: Parser and loader: keep `caseid`, `caseversion`, `age_cod`, read THER and OUTC, de-duplicate, quarter registry, sequential low-memory load; move the mock generator to test fixtures
- [ ] 06-02: Identity service: RxNav client with a local cache table, Preferred Term dictionary built from loaded data, watchlist correction
- [ ] 06-03: Single statistics path: all counts through `faers/`, openFDA as a labelled fallback restricted to suspect drugs; run the 20-quarter load

### Phase 7 (Day 3): Statistical Correctness
**Goal**: Every displayed number is computed from data or absent. No invented values, no mislabelled classes, no causal claims from report counts.
**Depends on**: Phase 6
**Requirements**: [STAT-01, STAT-02, STAT-03, STAT-04, STAT-05, STAT-06]
**Success Criteria** (what must be TRUE):
  1. `compute_metrics` matches hand-worked 2×2 tables for PRR, ROR, χ², IC and their intervals, including zero-cell cases.
  2. Time-to-onset is the difference between therapy start and event date, with the number of usable cases reported.
  3. Sex and age PRRs come from per-stratum 2×2 tables.
  4. Pairs that fail the signal criteria are classed `none`, and the dashboard counts only signals.
  5. A test fails if any endpoint returns a number when its source data is empty.
  6. No response text states or implies that reports are prescriptions or that a count trend is a risk trend.

Plans:
- [ ] 07-01: Golden tests for `compute_metrics`; add the `none` class; real time-to-onset and stratified PRR
- [ ] 07-02: Remove dashboard fallbacks; add the response envelope (`evidence`, `data_version`, `model_version`); empty-data test
- [ ] 07-03: Reword boxed-warning and forecast outputs, normalise the forecast by share of total reports with an out-of-sample baseline, remove the unverified warning-year table

### Phase 8 (Day 4): Validation Harness
**Goal**: Measure the tool against published reference standards and publish the results inside the app, reproducible with one command.
**Depends on**: Phase 7
**Requirements**: [VAL-01, VAL-02, VAL-03, VAL-04, VAL-05]
**Success Criteria** (what must be TRUE):
  1. One command runs every benchmark and regenerates the Methods & Validation page.
  2. Signal detection reports AUC, sensitivity and specificity on the OMOP and EU-ADR reference sets, with the drug and outcome mapping committed.
  3. NER reports precision, recall and F1 per entity type on the ADE corpus, and the model is named correctly in the UI.
  4. The DDI model is trained with mini-batches, ablations are separately trained models, calibration is fitted and scored on separate folds, and the best model on S1 AUPRC is the one deployed.
  5. Precision@k replaces Recall@k, and `calibrated` is true only when ECE improved.
  6. CI runs a reduced benchmark and fails when a metric drops below its recorded baseline.

Plans:
- [ ] 08-01: Reference-set loaders and mappings; signal benchmark runner
- [ ] 08-02: DDI retrain, real ablations, held-out calibration, model selection, API field cleanup (`is_harmful` renamed)
- [ ] 08-03: NER benchmark, report generator, Methods & Validation page, CI regression gate

### Phase 9 (Day 5): Prescription Review
**Goal**: A user uploads a prescription, confirms the extracted medication list, and receives a structured safety report.
**Depends on**: Phase 6 (identity), Phase 7 (statistics), Phase 8 (deployed interaction model)
**Requirements**: [RX-01, RX-02, RX-03, RX-04, RX-05, RX-06]
**Success Criteria** (what must be TRUE):
  1. A PDF or image upload returns structured lines: drug, strength, dose, route, frequency, duration, each with a confidence.
  2. No analysis runs until the user confirms or corrects the list.
  3. The report contains the confirmed list, pairwise interactions in the three existing tiers with label evidence, boxed warnings, duplicate therapy, top labelled reactions and active signals, with data and model versions.
  4. The report exports as PDF and carries the research-use statement.
  5. The uploaded image is not stored by default, patient identifiers are not sent to analysis services or logs, and the user is told when extraction uses the cloud.
  6. Without an API key the tab falls back to local OCR and says so.
  7. Field-level accuracy on the synthetic prescription set is measured and shown on the validation page.

Plans:
- [ ] 09-01: Extractor interface, cloud vision client with a fixed output schema, local OCR fallback, job table replacing the in-memory task dict
- [ ] 09-02: Prescription service composing the identity, interaction, boxed-warning and signal services; duplicate-therapy check; report and PDF
- [ ] 09-03: Upload, confirm and report views; synthetic prescription test set and accuracy benchmark

### Phase 10 (Day 6): Coherence & Kafka Integration
**Goal**: One navigable product where each feature has a place, each number states how it was obtained, and Kafka delivers evidence to the drug it concerns.
**Depends on**: Phases 7 and 9
**Requirements**: [COH-01, COH-02, COH-03, KAF-01, KAF-02, KAF-03]
**Success Criteria** (what must be TRUE):
  1. Navigation has seven areas: Monitor, Investigate, Interactions, Prescription Review, Text Mining, Pipeline, Methods & Validation, and every existing feature is reachable.
  2. A drug profile page shows signals, demographics, temporal analysis, boxed warning, interactions, trials, alerts and papers for one drug.
  3. Every result shows an evidence badge and the data version.
  4. The Kafka consumer computes no statistics; a `faers-release` event triggers the batch recompute.
  5. Alerts, papers and trials from Kafka appear on the matching drug profile.
  6. Delivering the same Kafka message twice leaves the database unchanged.
  7. Social listening is labelled exploratory and never feeds FAERS statistics.

Plans:
- [ ] 10-01: Navigation regroup, drug profile page from existing endpoints, evidence badge component
- [ ] 10-02: Kafka: remove PRR from the consumer, add `faers-release`, link alerts, papers and trials to drug identities, idempotency test; remove the hard-coded background table
- [ ] 10-03: Text Mining area (correct model name, negation handling, exploratory label), Pipeline area with live and simulated parts marked, browser smoke test

### Phase 11 (Day 7): Platform & Pilot Release
**Goal**: A secured, auditable build that runs locally with one command and can move to a server by changing configuration only.
**Depends on**: Phase 10
**Requirements**: [PLAT-01, PLAT-02, PLAT-03, PLAT-04, PLAT-05, PLAT-06, PLAT-07]
**Success Criteria** (what must be TRUE):
  1. Users sign in; viewer, analyst and admin roles are enforced and tested.
  2. An append-only audit log records user, action, inputs, data version and model version.
  3. Every result has a feedback control whose submissions are stored and exportable.
  4. The admin key is accepted in the header only, uploads are size- and rate-limited, models load safely, and responses contain no raw exception text.
  5. A fresh clone starts with one Compose command; data and models are volumes; moving hosts requires only environment changes.
  6. CI runs lint, unit, statistics and benchmark jobs, builds the image without data files, and publishes the validation report as an artefact; the deploy job remains gated on its secret.
  7. The README matches the code, a LICENSE file exists, and a pilot guide for colleges is written.

Plans:
- [ ] 11-01: Accounts, roles, audit log, feedback capture
- [ ] 11-02: Security fixes; configuration and Compose profiles; Dockerfile without data copies; CI/CD jobs
- [ ] 11-03: README, LICENSE, methods document, pilot guide; end-to-end run from a fresh clone

## Progress

| Phase | Plans Complete | Status | Completed |
|---|---|---|---|
| 1. Interactions GNN Fix | 3/3 | Complete | 2026-06-05 |
| 2. Hardening & Real Data | 3/3 | Complete | 2026-06-05 |
| 3. Backend Modularization & MySQL | 2/2 | Complete | 2026-10-11 |
| 4. DDI Prediction Model Rigor | 4/4 | Complete | 2026-10-12 |
| 5. Baseline & Clean Architecture | 3/3 | Complete | 2026-10-13 |
| 6. Real FAERS Data Foundation | 0/3 | Planned | — |
| 7. Statistical Correctness | 0/3 | Planned | — |
| 8. Validation Harness | 0/3 | Planned | — |
| 9. Prescription Review | 0/3 | Planned | — |
| 10. Coherence & Kafka Integration | 0/3 | Planned | — |
| 11. Platform & Pilot Release | 0/3 | Planned | — |

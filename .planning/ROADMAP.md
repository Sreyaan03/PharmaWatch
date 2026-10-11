# Roadmap: PharmaWatch — Production Excellence

## Overview

PharmaWatch is a real-time pharmacovigilance dashboard. Phase 1 fixed the GNN training pipeline and polypharmacy UX. Phase 2 eliminated hardcoded URLs and added real data sources. Phase 3 completed the backend modularization, MySQL migration, connection pooling, and CI/CD pipelines. Phase 4 overhauls the DDI prediction model in `scripts/train_gnn_model.py` to be scientifically rigorous, properly evaluated under cold-start conditions, calibrated, benchmarked against classical baselines, and integrated cleanly with a 3-tier backend API.

## Phases

- [x] **Phase 1: Interactions GNN Fix** — Balanced dataset, 16-feat atoms, 20 epochs, side-effects on graph, zoom/pan
- [x] **Phase 2: Hardening & Real Data** — Eliminate all hardcoded 127.0.0.1 URLs, replace fake charts with live data, add backend stats endpoint, fix CORS scope
- [x] **Phase 3: Backend Modularization & MySQL Production** — Split app.py into blueprints, MySQL 8.0 pooling, Caddy reverse-proxy edge, CI/CD, and security fixes
- [ ] **Phase 4: DDI Prediction Model Rigor** — Scientifically honest GINE model, drug-level S0/S1/S2 splits, temperature calibration, baselines, and 3-tier API

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
**Success Criteria** (what must be TRUE):
  1. Drug-level splits hold out 15% test and 10% validation drugs with zero train drug leakage verified by assertion.
  2. S0 (warm), S1 (one drug unseen), and S2 (both drugs unseen) test sets evaluated with AUROC, AUPRC, and Recall@k (k=100, 1000).
  3. GINEConv encoder with rich atom (16-d+) and bond features, multi-pooling (mean+max+sum), optional Morgan fingerprint branch, and symmetric pair head where f(A,B) == f(B,A).
  4. Temperature scaling calibration produces low ECE and generates a reliability diagram; outputs designated as "interaction likelihood score" (never "probability of harm").
  5. XGBoost, Logistic Regression, and Nearest-Neighbor Tanimoto baselines benchmarked against GINE across S0/S1/S2.
  6. Backend /api/graph/predict returns 3 explicit tiers: "documented", "model_predicted", "no_evidence_found", with research-use-only metadata.

Plans:
- [ ] 04-01: Dataset curation, clean SMILES parsing, drug-level S0/S1/S2 split generator, and zero-leakage test
- [ ] 04-02: GINEConv molecular graph encoder, edge features, multi-pooling, Morgan FP branch, and symmetric pair head
- [ ] 04-03: Training engine with dynamic negative resampling, hard BCE loss, cosine annealing, 5-seed runner, and temperature scaling calibration (ECE + reliability diagram)
- [ ] 04-04: Baseline models (XGBoost, LogReg, Nearest-Neighbor), S0/S1/S2 evaluation benchmark + ablations table, and 3-tier backend API integration

## Progress

| Phase | Plans Complete | Status | Completed |
|---|---|---|---|
| 1. Interactions GNN Fix | 3/3 | Complete | 2026-06-05 |
| 2. Hardening & Real Data | 3/3 | Complete | 2026-06-05 |
| 3. Backend Modularization & MySQL | 2/2 | Complete | 2026-10-11 |
| 4. DDI Prediction Model Rigor | 0/4 | In Planning | - |

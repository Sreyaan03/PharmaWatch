# Project State — PharmaWatch

## Project Reference

See: PROJECT_DEFINITION.md and FEATURE_AUDIT.md (updated 2026-10-11)

**Core value:** Real-time pharmacovigilance dashboard using live FDA data, NLP, and graph-theoretic analysis — no fake data
**Current focus:** Phase 4 — DDI Prediction Model Rigor

## Current Position

Phase: 4 of 4 (DDI Prediction Model Rigor)
Plan: 0 of 4 in current phase
Status: Ready to execute Phase 4
Last activity: 2026-10-11 — Phase 3 complete; Phase 4 planned with 4 executable plans (`04-01` to `04-04`).

Progress: [████████░░] 75% (Phases 1, 2, and 3 complete)

## Accumulated Context

### Decisions

- [Phase 1]: GNN uses node_feature_dim=16 — consistent across train script and backend loader
- [Phase 1]: Negative sampling via SMILES pool shuffle (not random pairs) to avoid data leakage
- [Phase 1]: Best-model checkpoint on val_accuracy (not just final epoch)
- [Phase 1]: D3 zoom uses scaleExtent([0.4, 3]) with a reset button
- [Phase 2]: CORS scoped to localhost/127.0.0.1 origins via ALLOWED_ORIGINS env var
- [Phase 2]: Table names in backend queries corrected from 'signals' to 'signals_cache' and 'predictions' to 'interaction_predictions'
- [Phase 2]: Column name in backend signal intensity query corrected from 'sev' to 'severity'
- [Phase 2]: Pipeline Health disclaimer badge added to HTML front-page
- [Phase 3]: Modularized `backend/app.py` into 7 Flask blueprints (`auth`, `analytics`, `interactions`, `monitoring`, `reports`, `health`, `frontend`)
- [Phase 3]: Migrated database access to MySQL 8.0 with DBUtils connection pooling and SQLite fallbacks
- [Phase 3]: Production Docker stack with Caddy reverse proxy, rate-limiting, and CI/CD workflow
- [Phase 4 Plan]: TDC DrugBank DDI dataset (~1,706 approved drugs) with RDKit sanitization and unparseable molecule logging
- [Phase 4 Plan]: Drug-level splits (75% train, 10% val, 15% test) with hard zero-leakage assertions and S0, S1, S2 evaluation sets
- [Phase 4 Plan]: Siamese GINEConv encoder with 22-d node features, 10-d edge features, multi-pooling (mean+max+sum), toggleable Morgan FP branch, and symmetric pair head $[a+b, a \odot b, |a-b|]$
- [Phase 4 Plan]: BCEWithLogitsLoss with hard 0/1 binary labels and dynamic 1:1 negative resampling per epoch
- [Phase 4 Plan]: Multi-seed training (5 seeds) with early stopping on S1 validation AUPRC
- [Phase 4 Plan]: Temperature scaling calibration with ECE and reliability diagrams; outputs labeled "interaction likelihood score"
- [Phase 4 Plan]: XGBoost, Logistic Regression, and Nearest-Neighbor Tanimoto baselines benchmarked across S0, S1, S2
- [Phase 4 Plan]: 3-tier API response (`documented`, `model_predicted`, `no_evidence_found`), canonically sorted MySQL pair caching, and research disclaimers

### Key Technical State

- Backend modularized into clean blueprints
- PyTorch 2.2.0+cpu and PyG 2.7.0 installed in venv
- All 13 existing backend unit tests pass cleanly
- CI/CD passes on GitHub Actions
- Plan artifacts generated in `.planning/phases/04-ddi-prediction-model-rigor/`

## Session Continuity

Last session: 2026-10-11
Stopped at: Phase 4 planned. Ready to execute `/gsd-execute-phase 04`.
Resume file: None

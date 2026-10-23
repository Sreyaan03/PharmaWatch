# Project State — PharmaWatch

## Project Reference

See: PROJECT_DEFINITION.md and FEATURE_AUDIT.md (updated 2026-10-11)
See: `.planning/MILESTONE-2-CONTEXT.md` for Milestone 2 decisions, audit evidence and target architecture

**Core value:** Real-time pharmacovigilance dashboard using live FDA data, NLP, and graph-theoretic analysis — no fake data
**Current focus:** Milestone 2 — University Pilot Release (Phases 5–11)

## Current Position

Phase: 11 of 11 (Platform & Pilot Release) — 11-01 and 11-02 done; 11-03 docs done, release verification pending owner
Plan: 11-01 (accounts/audit/feedback) done; 11-02 (security, portable deployment, CI jobs) done; 11-03 docs written (README, LICENSE, DEPLOYMENT, PILOT_GUIDE, METHODS). Remaining: the human-verify fresh-clone install + UAT + validation reproduce, then tag the release.
Status: All 20 FAERS quarters (2021Q3-2026Q2, ~7.2M cases) loaded with drug identity. Interaction model deployed by manifest. One docker/compose.yml with core/kafka/demo profiles; image bakes in no data and runs non-root; startup config validation reports all missing values at once; /api/health reports FAERS release, DDI model and job workers. Full test suite green (557 tests). Licence: PolyForm Noncommercial 1.0.0.
Open checkpoints (owner): rotate the committed FDA/NCBI keys + purge from git history before any public hosting; confirm the fresh-clone verification and tag the release.
Last activity: 2026-10-23 — 11-02 (deployment + CI) and 11-03 (documentation) built, committed and tested.

Progress: [██████████] 11 of 11 phases built; pilot-release verification pending owner sign-off

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
- [Phase 3]: Modularized `backend/app.py` into 10 Flask blueprints (`general`, `signals`, `interactions`, `boxed_warnings`, `ml`, `dashboard`, `analytics`, `temporal`, `demographics`, `pipeline`)
- [Phase 3]: Migrated database access to MySQL 8.0 with DBUtils connection pooling
- [Phase 3]: Production Docker stack with Caddy reverse proxy, rate-limiting, and CI/CD workflow
- [Phase 4]: TDC DrugBank DDI dataset (~1,706 approved drugs) with RDKit sanitization and unparseable molecule logging
- [Phase 4]: Drug-level splits (75% train, 10% val, 15% test) with hard zero-leakage assertions and S0, S1, S2 evaluation sets
- [Phase 4]: Siamese GINEConv encoder with 22-d node features, 10-d edge features, multi-pooling (mean+max+sum), toggleable Morgan FP branch, and symmetric pair head $[a+b, a \odot b, |a-b|]$
- [Phase 4]: BCEWithLogitsLoss with hard 0/1 binary labels and dynamic 1:1 negative resampling per epoch
- [Phase 4]: Multi-seed training (5 seeds) with early stopping on S1 validation AUPRC
- [Phase 4]: Temperature scaling calibration with ECE and reliability diagrams; outputs labeled "interaction likelihood score"
- [Phase 4]: XGBoost, Logistic Regression, and Nearest-Neighbor Tanimoto baselines benchmarked across S0, S1, S2
- [Phase 4]: 3-tier API response (`documented`, `model_predicted`, `no_evidence_found`), canonically sorted MySQL pair caching, and research disclaimers
- [Milestone 2]: Load the 20 most recent real FAERS quarters (expected 2021Q3–2026Q2)
- [Milestone 2]: Prescription extraction uses a cloud vision model (Claude API) behind a swappable interface; EasyOCR is the offline fallback
- [Milestone 2]: Host locally with Docker Compose now; portable to a server by configuration only
- [Milestone 2]: Keep every feature; fix or relabel unsound ones; do not alter working features
- [Milestone 2]: Kafka ingests evidence and triggers recomputation; it never computes statistics
- [Milestone 2]: Synthetic prescriptions only until ethics approval
- [Milestone 2]: Claim "benchmarked against published reference standards", never "medically validated"

### Key Technical State

- Backend is layered: `routes` (HTTP only) -> `services` -> `clients`/`ml`/`faers`/`db`, with `core` and `ingest`; `test_architecture.py` enforces it
- 42 tests pass; `scripts/refactor_check/` compares every endpoint's body and SQL before and after a refactor
- PyTorch 2.2.0+cpu and PyG 2.7.0 installed in venv
- Local FAERS database holds a 3,800-report openFDA sample; `data/faers_raw/` holds mock zips
- Phase 4 is committed; per-seed checkpoints in `backend/trained_models/checkpoints/` are intentionally untracked
- `DATA_DIR` for Milestone 2 is `C:\PharmaWatchData`
- Deployed DDI model (GINE) is outperformed by the XGBoost baseline in S0, S1 and S2

### Known defects carried into Milestone 2

Listed with file references in `.planning/MILESTONE-2-CONTEXT.md` section 4.

## Session Continuity

Last session: 2026-10-13
Stopped at: Phase 5 complete. Next step is to execute plan 06-01 (needs the 20-quarter FAERS download started by hand).
Resume file: `.planning/MILESTONE-2-CONTEXT.md`

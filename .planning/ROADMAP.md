# Roadmap: PharmaWatch — Production Excellence

## Overview

PharmaWatch is a real-time pharmacovigilance dashboard. Phase 1 fixed the GNN training pipeline and polypharmacy UX. Phase 2 targets all remaining hardcoded/fake data issues and security fixes identified in the FEATURE_AUDIT.md — bringing the platform from ~74% real to >92% real. Phase 3 covers backend architecture (splitting app.py monolith into blueprints). Phase 4 covers DDI severity enrichment via DDInter.

## Phases

- [x] **Phase 1: Interactions GNN Fix** — Balanced dataset, 16-feat atoms, 20 epochs, side-effects on graph, zoom/pan
- [x] **Phase 2: Hardening & Real Data** — Eliminate all hardcoded 127.0.0.1 URLs, replace fake charts with live data, add backend stats endpoint, fix CORS scope
- [ ] **Phase 3: Backend Modularization** — Split 2928-line app.py monolith into Flask blueprints; add proper .env loading
- [ ] **Phase 4: DDInter Drug Severity** — Integrate DDInter DDI database for real clinical severity in /api/graph; load CSV into SQLite

## Phase Details

### Phase 1: Interactions GNN Fix
**Goal**: Fix GNN training pipeline and polypharmacy graph UX
**Depends on**: Nothing (first phase)
**Requirements**: [GNN-01, GNN-02, GNN-03, GNN-04, GNN-05, GNN-06, GNN-07]
**Status**: Complete (2026-06-05)
**Plans**: 3 plans complete

Plans:
- [x] 01-01: GNN balanced dataset + 16-feat atoms + 20 epochs training
- [x] 01-02: Side-effects on graph edges + safe-graph fix
- [x] 01-03: Zoom/pan + parallel PubChem + CSS polish

### Phase 2: Hardening & Real Data
**Goal**: Remove all hardcoded 127.0.0.1 URLs (25 occurrences across 3 files), replace 3 fake charts with live data, add /api/local-stats backend endpoint for real counters, narrow CORS to localhost:5000
**Depends on**: Phase 1
**Requirements**: [HARD-01, HARD-02, HARD-03, HARD-04, HARD-05, HARD-06, HARD-07, HARD-08]
**Success Criteria** (what must be TRUE):
  1. grep for `127.0.0.1` in src/ returns 0 results — all URLs are relative paths
  2. Drug Trend chart calls real `/api/lstm` data, not Math.random()
  3. Signal Intensity chart fetches from `/api/dashboard/signal-intensity` backend endpoint (or falls back gracefully)
  4. Drug Category doughnut fetches from openFDA count API via `/api/dashboard/drug-categories`
  5. `/api/local-stats` returns real SQLite-sourced counts (drugs monitored, signals detected, reports analyzed)
  6. CORS is scoped to `ALLOWED_ORIGINS` env var instead of wildcard `*`
  7. Pipeline section has visible "Simulated — Architecture Demo" disclaimer badge
  8. All 127.0.0.1 occurrences removed from `_interactions_new.js`, `app.js`, and `ml_models.js`
**Plans**: 3 plans

Plans:
- [x] 02-01: Replace all 127.0.0.1 hardcoded URLs with relative paths (all 3 JS files)
- [x] 02-02: Real data for Drug Trend chart + Signal Intensity chart + Drug Category doughnut
- [x] 02-03: /api/local-stats endpoint + CORS scope fix + pipeline disclaimer badge

### Phase 3: Backend Modularization
**Goal**: Split app.py (2928 lines) into Flask Blueprint modules; add python-dotenv .env loading at startup
**Depends on**: Phase 2
**Requirements**: [ARCH-01, ARCH-02]
**Success Criteria** (what must be TRUE):
  1. backend/app.py is under 200 lines (entry point only)
  2. Routes split into blueprint files: routes/drugs.py, routes/signals.py, routes/interactions.py, routes/boxed_warnings.py, routes/ml.py
  3. backend/.env loaded via python-dotenv on startup
  4. All tests pass / server starts cleanly
**Plans**: 2 plans

Plans:
- [ ] 03-01: Create blueprint structure and split all routes into modules
- [ ] 03-02: Add python-dotenv .env config loading and update secret key management

### Phase 4: DDInter Drug Severity
**Goal**: Integrate DDInter DDI database (302k curated drug-drug interactions) for real clinical severity in /api/graph endpoint — replacing count-based risk thresholds
**Depends on**: Phase 3
**Requirements**: [DDI-01, DDI-02, DDI-03]
**Success Criteria** (what must be TRUE):
  1. backend/ddi_lookup.py exists with init_ddi_table(), lookup_interaction(), get_all_interactions_for_drug() functions
  2. /api/graph enriches each co-drug with DDInter severity (major/moderate/minor/unknown) from SQLite
  3. D3 graph nodes colored by real DDInter severity, not frequency thresholds
**Plans**: 1 plan

Plans:
- [ ] 04-01: Create ddi_lookup.py + update /api/graph to use real DDI severity

## Progress

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Interactions GNN Fix | 3/3 | Complete | 2026-06-05 |
| 2. Hardening & Real Data | 3/3 | Complete | 2026-06-05 |
| 3. Backend Modularization | 0/2 | Not started | - |
| 4. DDInter Drug Severity | 0/1 | Not started | - |

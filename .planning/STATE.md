# Project State — PharmaWatch

## Project Reference

See: PROJECT_DEFINITION.md and FEATURE_AUDIT.md (updated 2026-06-05)

**Core value:** Real-time pharmacovigilance dashboard using live FDA data, NLP, and graph-theoretic analysis — no fake data
**Current focus:** Phase 2 — Hardening & Real Data

## Current Position

Phase: 3 of 4 (Backend Modularization)
Plan: 0 of 2 in current phase
Status: Ready to plan
Last activity: 2026-06-05 — Phase 2 complete (Hardening, URL cleanup, and real dashboard API wiring)

Progress: [████░░░░░░] 44% (Phases 1 and 2 complete — 6 plans done)

## Performance Metrics

**Velocity:**
- Total plans completed: 6
- Average duration: ~45 min/plan
- Total execution time: ~4.5 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. GNN Fix | 3/3 | ~2.5h | ~50 min |
| 2. Hardening | 3/3 | ~2.0h | ~40 min |

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

### Key Technical State

- backend/app.py is 3088 lines — monolith (Phase 3 will split)
- CORS is scoped to ALLOWED_ORIGINS (scoped allowlist)
- All frontend JS fetch calls use relative paths (0 occurrences of 127.0.0.1 in src/)
- Drug Trend chart updates dynamically from real LSTM data
- Signal Intensity chart updates dynamically from real SQLite signals_cache counts
- Drug Category doughnut updates dynamically from real SQLite signals_cache distribution

### Blockers/Concerns

- BioBERT model is NOT fine-tuned (uses generic d4data/biomedical-ner-all) — low priority, noted
- HBase is available via Docker but optional — all features have SQLite fallback

## Session Continuity

Last session: 2026-06-05
Stopped at: Phase 2 complete. Phase 3 planning is next.
Resume file: None

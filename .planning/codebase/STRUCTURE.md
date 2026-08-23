# Structure

**Analysis Date:** 2026-05-31

## Top-Level Layout

- `backend/` — Flask backend, SQLite helpers, models, runtime artifacts.
- `src/` — browser app, styles, and client-side integration code.
- `scripts/` — offline data loading, dataset download, and training utilities.
- `data/` — committed datasets such as `twosides.csv`.
- `.agent/` — GSD workflow definitions and helper scripts.
- `venv/` — local Python virtual environment.

## Backend Files

- `backend/app.py` — main Flask server and API hub.
- `backend/database.py` — SQLite schema creation and CRUD helpers.
- `backend/lstm_model.py` — PyTorch LSTM training and inference.
- `backend/reddit_scraper.py` — Reddit ingestion support.
- `backend/check_db.py` — local database inspection utility.
- `backend/pharmawatch.db` — committed SQLite database artifact.
- `backend/trained_models/` — saved model weights and metadata.

## Frontend Files

- `src/index.html` — single-page dashboard shell and script loading order.
- `src/app.js` — main UI orchestration and rendering logic.
- `src/api_layer.js` — external API wrapper and caching layer.
- `src/biobert.js` — wrapper around the Flask NER API.
- `src/distributed_storage.js` — simulated pipeline metrics and charts.
- `src/ml_models.js` — model-related UI behavior.
- `src/_interactions_new.js` — interaction-graph support code.
- `src/style.css` — the full visual system.

## Scripts

- `scripts/download_twosides.py` — fetches the TWOSIDES dataset.
- `scripts/load_to_hbase.py` — loads TWOSIDES into HBase.
- `scripts/train_gnn_model.py` — trains the graph interaction model.

## Documentation

- `README.md` — high-level project overview and run instructions.
- `README_SIMPLE.md` — plain-language explanation.
- `PROJECT_DEFINITION.md` — project definition and scope notes.
- `FEATURE_AUDIT.md` — feature inventory and audit notes.
- `UPGRADE_GUIDE.md` — upgrade-oriented guidance.
- Domain-specific guide files such as `biobert_ner_guide.md` and `twosides_ml_guide.md` support deeper model work.

## File Ownership Rules

- Put backend persistence changes in `backend/database.py` unless the schema needs a separate module.
- Put Flask route changes in `backend/app.py` only when the route is simple and self-contained.
- Put browser UI changes in `src/app.js` and style changes in `src/style.css`.
- Put API fetch logic in `src/api_layer.js` instead of sprinkling `fetch()` calls across the app.
- Put reusable model training logic in `backend/lstm_model.py` or a new module under `backend/`.

## Naming / Placement Conventions

- Keep runtime artifacts under `backend/trained_models/` rather than the repo root.
- Keep sample or source data under `data/` or `backend/` depending on ownership.
- Preserve the current static frontend layout unless the project is intentionally being restructured.

## What Is Not Present

- No `package.json`.
- No `src/` framework component tree.
- No test directory.
- No `pyproject.toml`.
- No CI configuration directory detected.

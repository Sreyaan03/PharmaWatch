# Architecture

**Analysis Date:** 2026-05-31

## System Shape

PharmaWatch is a two-tier application:
- A Flask backend in `backend/app.py` handles API requests, model loading, local persistence, and static file serving.
- A single-page frontend in `src/index.html` loads plain JavaScript modules and renders the dashboard directly in the browser.

There is no build step, no framework router, and no separate frontend app server.

## Main Runtime Flow

1. `backend/app.py` starts the Flask server.
2. The server loads environment variables, initializes SQLite, and loads the biomedical NER model.
3. The root route serves `src/index.html` from the `src/` directory.
4. `src/index.html` loads the browser scripts in a fixed order.
5. `src/app.js` wires the UI after `DOMContentLoaded` and orchestrates charts, panels, and navigation.
6. `src/api_layer.js` and `src/biobert.js` call the backend and external services.
7. `backend/database.py` persists drug-event pairs, Reddit posts, boxed-warning violations, and interaction predictions.

## Backend Responsibilities

### API hub
- `backend/app.py` is the central control plane.
- It exposes health, NER, PRR, LSTM, graph, and other domain endpoints.
- It also stores results produced by those endpoints.

### Persistence
- `backend/database.py` creates the SQLite schema and provides helper functions.
- The database is file-backed in `backend/pharmawatch.db`.

### Model execution
- `backend/app.py` eagerly loads the biomedical token-classification model.
- `backend/lstm_model.py` owns training and inference for the forecasting model.
- Model files are stored in `backend/trained_models/`.

## Frontend Responsibilities

### UI composition
- `src/index.html` contains the entire dashboard shell and all major sections.
- `src/style.css` provides the CDC-inspired design system and responsive layout.

### Browser data layer
- `src/api_layer.js` centralizes live API fetches and caching.
- `src/biobert.js` wraps the Flask NER service.
- `src/distributed_storage.js` simulates pipeline metrics for Kafka, Spark, HDFS, and HBase views.
- `src/ml_models.js` and `_interactions_new.js` support the analytic views.

### Application orchestration
- `src/app.js` is the main UI coordinator.
- It renders tables, charts, alerts, filters, and detail panels.
- It relies on global module-style objects rather than imports or a bundler.

## Layer Boundaries

### Good separation already present
- Database logic is isolated in `backend/database.py`.
- Model logic is isolated in `backend/lstm_model.py`.
- Browser fetch logic is centralized in `src/api_layer.js`.

### Boundary weaknesses to watch
- `backend/app.py` still owns too many concerns in one file.
- Frontend globals are tightly coupled through load order.
- Some configuration values are embedded directly in browser code.

## Architectural Pattern

This repo uses a pragmatic monolith pattern:
- One backend process.
- One static frontend.
- Local persistence for runtime artifacts.
- External APIs for live data.

That pattern is fine for a mini-project, but it means feature growth should prefer small modules over another framework layer.

## Where to Add New Code

- Add new API routes in `backend/app.py` only if the feature is small.
- Split backend logic into a new module if the route needs reusable helpers or long business logic.
- Add frontend feature code to the existing browser modules in `src/`.
- Add new data-processing scripts under `scripts/`.

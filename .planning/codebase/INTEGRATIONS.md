# Integrations

**Analysis Date:** 2026-05-31

## External APIs

### openFDA
- Used for FAERS adverse-event counts, drug labels, and real-time PRR calculations.
- Primary usage sites: `backend/app.py`, `backend/lstm_model.py`, and `src/api_layer.js`.
- The backend also patches `requests.get` so `FDA_API_KEY` is appended automatically for API calls to `api.fda.gov`.

### Wikipedia REST API
- Used for term definitions and hover tooltips in the frontend.
- Integration lives in `src/api_layer.js`.

### ClinicalTrials.gov
- Referenced by the backend for trial-related views and dashboard panels.
- The repo treats it as an external data source rather than a first-class service wrapper.

### Reddit / PRAW
- Used for scraping and processing posts into the local drug-event store.
- Main support files: `backend/reddit_scraper.py` and `backend/app.py`.

### PubChem / biomedical lookup data
- The frontend comments and API layer indicate chemical/drug enrichment from public sources.
- No dedicated service module was isolated for this in the current structure.

## Internal Integrations

### Frontend to backend
- `src/biobert.js` calls `http://localhost:5000/api/health` and `/api/ner`.
- `src/app.js` and `src/api_layer.js` drive most UI fetches from browser code.
- `backend/app.py` serves `src/index.html` and the browser assets.

### Backend to storage
- `backend/database.py` owns SQLite initialization and persistence helpers.
- `backend/app.py` writes NER outputs, violations, and predictions into the local database.

### Backend to model artifacts
- `backend/lstm_model.py` reads and writes files in `backend/trained_models/`.
- `backend/app.py` conditionally imports the LSTM module and exposes training/prediction endpoints.

### Scripts to data infrastructure
- `scripts/download_twosides.py` fetches the TWOSIDES dataset.
- `scripts/train_gnn_model.py` trains the GNN model artifact.
- `scripts/load_to_hbase.py` loads interaction data into HBase.

## CDN / Browser Libraries

- Chart.js is loaded from a CDN for charts.
- D3.js is loaded from a CDN for graph visualizations.
- Popper and Tippy are loaded from CDNs for tooltips and popovers.
- Google Fonts are used for typography.

## Local Service Expectations

- The backend expects a live Python process on port 5000.
- The frontend assumes browser access to live APIs and does not include an offline fallback layer.
- `backend/lstm_model.py` expects local filesystem access for model persistence.

## Integration Risks

- Live API usage is rate-limit sensitive, especially openFDA.
- Several features fail softly rather than through a shared error boundary, so a single upstream outage can degrade multiple views.
- The frontend currently embeds an API key in `src/api_layer.js`, which is a security and maintenance risk.

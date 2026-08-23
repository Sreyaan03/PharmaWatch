# Technology Stack

**Analysis Date:** 2026-05-31

## Languages

**Primary:**
- Python — backend API, database helpers, model training/inference, data ingestion scripts in `backend/` and `scripts/`.
- JavaScript — browser application logic in `src/`.
- HTML5 — single-page dashboard shell in `src/index.html`.
- CSS3 — custom design system in `src/style.css`.

**Secondary:**
- SQL — SQLite schema and queries in `backend/database.py`.
- Shell / CLI — repo setup and operational commands.

## Runtime

**Environment:**
- Python 3.x environment managed locally in `venv/`.
- Browser runtime for the frontend, with CDN-loaded libraries.

**Package Manager:**
- No `package.json` or Node package manager detected.
- Python dependencies are installed from `backend/requirements.txt`.
- Lockfile: missing.

## Frameworks

**Core:**
- Flask — backend web server and API surface in `backend/app.py`.
- Flask-CORS — enables browser access from the frontend.
- SQLite3 — embedded persistence in `backend/database.py`.

**ML / NLP:**
- PyTorch — LSTM model training and inference in `backend/lstm_model.py`.
- Hugging Face Transformers — BioBERT-style biomedical NER pipeline in `backend/app.py`.
- NumPy — feature prep, calculations, and fallbacks.

**Testing / Utility:**
- No formal test framework detected.
- `requests`, `python-dotenv`, and `praw` support external data and environment loading.

**Build / Dev:**
- No frontend bundler detected.
- The frontend is a static script stack loaded directly from `src/index.html`.

## Key Dependencies

**Critical:**
- `flask` — API server.
- `transformers` — biomedical NER model loading.
- `torch` — LSTM forecasting model.
- `numpy` — math and data shaping.
- `flask-cors` — browser access during local development.
- `python-dotenv` — environment variable loading.

**Infrastructure:**
- `praw` — Reddit ingestion support.
- `requests` — direct HTTP calls to openFDA, ClinicalTrials, Wikipedia, and other APIs.

## Configuration

**Environment:**
- `backend/.env` is expected to contain `FDA_API_KEY`.
- `backend/app.py` loads environment variables at import time.
- Frontend integration is hard-wired to `http://localhost:5000` in `src/biobert.js`.

**Artifacts:**
- SQLite database: `backend/pharmawatch.db`.
- Model artifacts: `backend/trained_models/`.

## External Services

- openFDA FAERS and label APIs.
- ClinicalTrials.gov.
- Wikipedia REST API.
- Reddit via PRAW.
- HBase / TWOSIDES support through scripts in `scripts/`.

## Notes

- The stack is hybrid: a Flask-backed local API serves a static HTML/JS dashboard.
- Runtime behavior depends heavily on live external services and locally cached model/data artifacts.
- The repo is not packaged as a distributable Python project or Node app.

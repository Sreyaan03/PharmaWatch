# Concerns

**Analysis Date:** 2026-05-31

## High-Priority Risks

### Hardcoded API key in frontend
- `src/api_layer.js` contains a hardcoded openFDA API key.
- That makes rotation, revocation, and secret management harder than it should be.
- The backend already reads `FDA_API_KEY` from the environment, so the frontend should not also own a secret.

### Open CORS policy
- `backend/app.py` enables CORS broadly for browser access.
- That is convenient for local development, but it is too permissive for a deployed service.

### Heavy import-time work
- `backend/app.py` loads the biomedical model during startup.
- `backend/database.py` initializes the database at import time.
- These side effects make startup slow and can make failures harder to isolate.

## Reliability Risks

### Dependence on live external services
- openFDA, Wikipedia, ClinicalTrials.gov, Reddit, and other external services can fail or rate-limit requests.
- Several UI paths depend on those services directly, so one upstream outage can degrade multiple views.

### Optional ML path can disappear
- The LSTM path is optional if `backend/lstm_model.py` fails to import.
- That is reasonable for resilience, but it means some functionality can silently vanish unless the UI surfaces the limitation clearly.

### Committed runtime artifacts
- `backend/pharmawatch.db` and files in `backend/trained_models/` are committed artifacts.
- That is useful for demo stability, but it can also hide drift between data, code, and persisted state.

## Maintainability Risks

### Monolithic backend entry point
- `backend/app.py` owns too many concerns: routing, model loading, data access, and external fetch logic.
- The file is already doing enough work that future feature additions should be split into modules.

### Dependency mismatch risk
- `backend/requirements.txt` does not obviously cover every package used by the supporting scripts.
- The `scripts/` folder appears to rely on extra ML and data-processing packages that are not listed there.

### No automated test net
- The repo currently has no obvious automated test suite.
- That increases the chance of regressions in API behavior, startup behavior, and fallback handling.

## Documentation Drift Risks

- `README.md` presents the project as a simple static app, but the current codebase is a Flask-backed system with live integrations and local persistence.
- The docs should be kept aligned with the actual runtime path so new work starts from the right mental model.

## Practical Priority Order

1. Remove secret handling from browser code.
2. Narrow CORS before deployment.
3. Add smoke tests for the Flask routes and storage helpers.
4. Split `backend/app.py` if the backend grows further.
5. Align `backend/requirements.txt` with the scripts if those scripts are part of the intended workflow.

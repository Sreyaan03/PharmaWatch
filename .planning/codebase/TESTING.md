# Testing

**Analysis Date:** 2026-05-31

## Current State

- No automated test suite was detected.
- No `tests/` directory was found.
- No `pytest`, `unittest`, Jest, or Vitest configuration was found.
- No CI workflow directory was detected.

## Existing Verification Paths

### Backend startup
- Run `python backend/app.py` and confirm the Flask server starts.
- Expected startup behavior includes schema initialization, model loading, and route registration.

### Health check
- Call `GET /api/health`.
- Expected response: JSON with `status: "ok"` and the loaded model name.

### NER endpoint
- Call `POST /api/ner` with a JSON body containing `text`.
- Expected response: `entities` array or a JSON `error` field for invalid input.

### PRR endpoint
- Call `GET /api/prr?drug=Metformin&event=Nausea`.
- Expected response: computed PRR fields plus signal classification.

### LSTM endpoint
- Call `GET /api/lstm?drug=Metformin`.
- Expected response: either trained-model predictions or the rolling-average fallback.

### Frontend smoke test
- Open the dashboard through the Flask server and verify the main sections render.
- Confirm charts, filters, tooltips, and sidebar/navigation interactions initialize without console errors.

## Manual Test Coverage That Matters Most

- Database writes after NER runs.
- Fallback behavior when external APIs rate-limit or fail.
- Chart rendering after data fetches.
- Model load failure handling when `lstm_model.py` or artifacts are missing.
- Tooltip and hover initialization after dynamic DOM updates.

## Gaps

- There are no automated regressions protecting route behavior.
- There are no unit tests for `backend/database.py` helpers.
- There are no frontend component or integration tests.
- There is no automated check that `backend/requirements.txt` matches the scripts.

## Recommended Test Direction

If test coverage is added, start with:
- Flask route tests for `backend/app.py`.
- SQLite helper tests for `backend/database.py`.
- Smoke tests for `backend/lstm_model.py` input/output behavior.
- Browser-level smoke checks for the critical dashboard flows.

# Summary 05-01: Route Snapshot, Layered Packages, Thin Routes

## What was built
1. **Safety net (before any code moved)**
   - `backend/tests/test_route_inventory.py` records all 51 registered rules (50 distinct method/path pairs) and the status and top-level keys of a call to each, with sockets refused, `.env` ignored, DuckDB empty and the HBase breaker reset. Identical in a hostile environment and in any test order. Regenerate with `pytest --update-inventory`.
   - `scripts/refactor_check/` differential harness: every endpoint against a fake network and fake MySQL with realistic canned data; compares full bodies and the SQL statements issued. 76 requests, deterministic over 3 runs.
2. **Layered packages** (`backend/`): `core/` (paths, watchlist), `clients/` (http, pubchem, openfda, hbase, clinicaltrials), `ml/` (ner, ocr, forecast, `ddi/{model,baselines,data,inference}`), `ingest/` (producer, consumer), `validation/` (ddi_eval, ddi_calibrate), `services/` (one module per route file, plus `health`).
3. **Thin routes**: route files fell from about 2,500 lines to 500. Each handler parses the request, calls one service function and returns `jsonify(payload), status`. Services import no Flask.
4. **Removed**: `backend/helpers/` package, the DDI model's `sys.path` edit, the `routes/` -> `routes/` import in the boxed-warning logic.

## Verification
- 28 tests pass (26 existing + 2 new).
- Route inventory (recorded before the move) passes unchanged.
- Differential harness: 76/76 requests identical in status, body and SQL log between the commit before Task 3 and the result.
- 100/125 top-level definitions are syntactically identical to the originals; the 25 that differ are import lines and path constants, each reviewed.
- `grep` finds no `query(`, `execute(`, `http_requests`, `torch`, `duckdb` or `ping(` under `backend/routes/`, and no `flask` under `backend/services/`.

## Deviations from the plan
- **No compatibility shims.** Every importer was updated in the same commit (three tests, `routes/ml.py`, two compose files), so there is nothing for 05-03 to delete. Kafka compose commands now use `python -m ingest.producer` / `ingest.consumer`.
- **`core/config.py` holds paths and the watchlist only.** Environment-variable reads stay where they are; consolidating them is done in 05-02 (admin key) and Phase 11 (startup validation). `DATA_DIR` still resolves to `<repo>/data`; the `DATA_DIR` environment override arrives with the Phase 6 data work.
- **Services return `(payload, status)`** instead of raising typed errors. It is a mechanical transformation of `return jsonify(x), n`, which is what made the byte-level comparison possible.
- `baselines_ddi.py` moved into `ml/ddi/` (not `scripts/`) because `validation/` imports it and backend packages must not depend on `scripts/`. `precompute_drug_structures` moved to `ml/ddi/data.py` for the same reason.
- Admin-key checks were deliberately left in the routes; 05-02 replaces the four copies with one decorator.

## Findings for later plans
- `/` is registered twice (`app.py` and `general.py`): 05-02.
- `/api/analytics/top-reactions` returns tied counts in arbitrary order (DuckDB `ORDER BY cnt DESC` without a tiebreaker): Phase 7.
- `backend/tests/test_interactions_api.py` calls live PubChem and openFDA; a cold run takes minutes and results depend on the network: add mocks in a later plan.
- `scripts/test_ner.py` imports a name `app` no longer exports: removed in 05-03.
- `app.py` and nothing else still edits `sys.path`: removed in 05-03.

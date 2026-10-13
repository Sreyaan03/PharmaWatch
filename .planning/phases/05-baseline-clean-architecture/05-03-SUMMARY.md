# Summary 05-03: Dead Code Removed, Layering Enforced

## What changed
- **Deleted** (each re-checked as unreferenced first): `backend/check_db.py`, `scripts/fix_prr_stale.py`, `scripts/download_twosides.py`, `scripts/test_ner.py` (imported a name `app` no longer exported), `backend/trained_models/twosides_gnn_model.pth`, root `requirements.txt` (one pin, unused), the empty `scratch/` folder.
- **Removed from `database.py`**: `get_prr_data`, `get_drug_event_counts`, `get_signal`, `init_interaction_predictions_table`. `get_prediction` stays (a test uses it).
- **Retired `POST /api/system/start-bigdata`**, which ran `docker start` from a web request. The Interactions tab now only reports HBase status, with a "Recheck" button and the command to start the container.
- **`app.py` no longer edits `sys.path`**; nothing outside the tests does.
- **Docker**: the web and Kafka images no longer copy `scripts/` (the app does not import from it any more).
- **`backend/tests/test_architecture.py`** (5 tests) enforces: no `sys.path` edits outside tests; routes import no DB, HTTP, ML, FAERS or numeric libraries; services import no Flask; lower layers do not import upward; only `app.py` imports routes. Shown to fail when violations are planted.
- **Test hygiene**: `test_seed_clean_legacy_data_handles_missing_file` created an empty `non_existent_file.db` in the working directory on every run; it now uses a temporary path.

## Verification
- 42 tests pass (37 + 5 architecture).
- Differential harness vs the pre-Phase-5 baseline: 73/76 identical; the 3 differences are the two requests to the removed endpoint (now 404) and `GET /` (2 characters longer: "Retry" -> "Recheck").
- Snapshot differs only by the removed endpoint.

## Deviations
- The plan's "delete the re-export shims" step was a no-op: 05-01 updated every importer directly and left no shims.
- CI workflow is unchanged. Its lint and `pytest backend/tests/` steps already cover the new layout; new jobs belong to Phase 11.
- Docker still copies three gitignored data files, which breaks the CI image build. That is fixed in plan 11-02 with the rest of the deployment work.

## Phase 5 outcome
Backend is organised as `core / clients / ml / faers / services / routes / ingest / validation`, routes are 500 lines (from about 2,500), each shared helper has one home, and the layering is enforced by tests. Behaviour is unchanged except for the one endpoint removed on purpose.

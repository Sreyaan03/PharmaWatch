# Milestone 2 Context: University Pilot Release

Planned 2026-10-12. Covers Phases 5–11 (one phase per day). Read this before planning or executing any of those phases.

## 1. Goal

Turn PharmaWatch into a coherent, all-in-one pharmacovigilance tool for pharmacy and medical colleges, where every number shown is either computed from real data or labelled as modelled or simulated, and where published benchmarks back the claims.

## 2. Locked decisions

| Decision | Choice |
|---|---|
| FAERS volume | The 20 most recently released quarters (expected 2021Q3–2026Q2), real quarterly ASCII files from fis.fda.gov |
| Prescription extraction | Cloud vision model (Claude API, default `claude-opus-5-5`, set by `RX_VISION_MODEL`), behind a swappable extractor interface; existing EasyOCR kept as the offline fallback for printed text |
| Hosting | Local for now, Docker Compose. Must move to a server later with configuration changes only |
| Features | Keep every existing feature. Fix or relabel the unsound ones; do not delete user-facing features |
| Working features | Do not change behaviour of features that already compute real results |
| Kafka | Keep. It ingests evidence and triggers recomputation; it never computes statistics |
| Pilot data | Synthetic prescriptions only until ethics approval |
| Positioning | Research and education tool. Never "medically validated"; say "benchmarked against published reference standards" |
| CI/CD | Keep the existing GitHub Actions pipeline and extend it |

## 3. Rules for every phase

1. Phase 5 is behaviour-preserving: move and delete only. The existing 26 tests are the safety net.
2. Each phase ends with a test that proves its goal, and that test joins CI.
3. Reuse existing code first. New modules are allowed only where listed in section 6.
4. Every API result carries `evidence` (`measured`, `modelled` or `simulated`), `data_version` (FAERS quarters loaded) and, where a model is involved, `model_version`.
5. An endpoint with no source data returns `no_data`. It never returns a derived or default number.
6. No causal language from report counts (reports are not prescriptions, and a rising count is not a rising risk).

## 4. Audit findings this milestone fixes

Evidence gathered on 2026-10-12.

### Data
- `data/faers_raw/*.zip` are mock files (about 617 KB each) from `generate_mock_quarter` in `backend/faers/downloader.py`.
- `data/pharmawatch_faers.duckdb` holds 3,800 reports sampled through the openFDA API by `backend/faers/fetch_real_openfda.py`. All local statistics today rest on this sample.
- `backend/faers/parser.py` drops `caseid`, `caseversion` and `age_cod`, and never reads the THER or OUTC files. No case de-duplication; ages in months or decades are read as years.

### Invented or invalid numbers
- `get_tto_duckdb` (`backend/faers/analytics.py`): time-to-onset built from day-of-month plus fixed percentage shares.
- `get_demographics_sex_duckdb`, `get_demographics_age_duckdb`: "PRR" is share of reports times a constant.
- `scripts/kafka_consumer.py` `handle_faers`: drug total is the sum of top-10 event counts; background comes from `INITIAL_GLOBAL_BACKGROUND` or a 5,000 default.
- `backend/routes/dashboard.py`: `reports_analyzed` fallback `signals * 1200`; hourly chart sine-curve fallback; every cached pair counted as a signal.
- `compute_metrics`: non-signals are labelled `moderate`.
- `HISTORICAL_WARNING_YEARS` (`backend/helpers/external_apis.py`): unverified, includes drugs with no boxed warning; the timeline endpoint uses it even when no boxed warning exists.
- `/api/boxed-warning/violations/*`: post-warning reports described as "continued prescriptions despite the Black Box Warning"; warning date is the latest label revision; events matched by any 4-letter substring.
- `backend/lstm_model.py`: rising raw report count flagged as emerging risk; plotted baseline is in-sample; comments say daily, data is monthly.

### Signal method
- `WATCHLIST` (`backend/helpers/models.py`) contains terms that are not MedDRA Preferred Terms (Bleeding, QT Prolongation, Peripheral Edema, Nephrotoxicity, Peripheral Neuropathy).
- The openFDA path matches free-text product names and includes concomitant drugs, so it disagrees with the DuckDB path.
- No test covers `compute_metrics`.

### DDI model (Phase 4 follow-up)
- `scripts/train_gnn_model.py` takes one optimiser step per epoch (15 updates in total); `--batch_size` is unused.
- XGBoost beats the deployed GINE in S0, S1 and S2.
- The three ablations in `scripts/eval_ddi_model.py` are not trained ablations (random-init network, dot product, zeroed edge features at inference).
- Calibration raised ECE (0.0768 to 0.0780) and was fitted and scored on the early-stopping set; the API hard-codes `calibrated: True`.
- Recall@100 is saturated (S1 has about 47k positives). Held-out S0 positives can be sampled as training negatives.

### NLP
- `d4data/biomedical-ner-all` is DistilBERT-based; the UI calls it BioBERT.
- Drug–event pairs are the cross product of all drugs and events in a text, with no negation handling.
- Reddit credentials are placeholders.

### Platform
- No user accounts, roles or audit trail. Admin key accepted in the query string.
- `/api/signals/ner-mine` is unauthenticated and stores uploads in an unbounded in-memory dict.
- `torch.load` without `weights_only` for the GNN; raw exception text returned to clients.
- `docker/Dockerfile` copies three gitignored data files, so the CI image build fails.
- README contradicts the code; no LICENSE file.

## 5. Target architecture

```
backend/
  app.py            entry point: config, blueprint registration
  core/             config, auth, audit, errors, response envelope
  routes/           HTTP only: parse request, call a service, return
  services/         signals, interactions, drug_profile, prescription, text_mining
  faers/            ingest (download, parse, dedupe, load) and all statistics
  clients/          openfda, pubchem, rxnav, clinicaltrials, vision
  ml/               ddi (model definitions + inference), ner, forecast
  ingest/           kafka producer and consumer
  validation/       benchmark runners and report generator
  db/               mysql pool, schema, migrations
  tests/
scripts/            training and one-off data preparation only
src/                frontend (regrouped, not rewritten)
docker/             one compose file with profiles: core, kafka, demo
```

Sources for the move:

| Target | Built from |
|---|---|
| `services/` | Logic now inside `routes/*.py` |
| `clients/` | Split of `helpers/external_apis.py`, plus `helpers/http_client.py` |
| `ml/ddi` | `scripts/models_ddi.py` and `_run_gnn_pair`; removes the `sys.path` hack in `helpers/models.py` |
| `ml/ner` | NER loader in `helpers/models.py` |
| `ml/forecast` | `lstm_model.py` |
| `ingest/` | `scripts/kafka_producer.py`, `scripts/kafka_consumer.py` |
| `validation/` | `scripts/eval_ddi_model.py`, `scripts/calibrate_model.py`, plus new runners |

### Portability (local now, server later)
- All paths, hosts, keys and feature switches come from environment variables. `DATA_DIR` is `C:\PharmaWatchData` on the development machine, outside the OneDrive folder.
- Data and models are mounted volumes, never copied into the image.
- Compose profiles: `core` (web, MySQL, Caddy), `kafka` (ZooKeeper, Kafka, producer, consumer), `demo` (HBase).
- The existing deploy job stays, gated on the `PROD_SERVER_IP` secret as it is today.
- Auth sits behind one interface so college single sign-on can replace local accounts later.
- Background jobs sit behind one interface, backed by a MySQL table now.

## 6. New modules allowed

`core/auth`, `core/audit`, `clients/rxnav`, `clients/vision`, `services/prescription`, `services/drug_profile`, `faers/dedupe`, `faers/registry`, `validation/*`, and the Prescription Review, Drug Profile and Methods & Validation views in `src/`.

## 7. Confirmed removals (Phase 5)

| Remove | Reason |
|---|---|
| `backend/check_db.py`, `scripts/fix_prr_stale.py`, `scripts/download_twosides.py`, `scripts/test_ner.py` | Referenced nowhere |
| `backend/trained_models/twosides_gnn_model.pth`, `non_existent_file.db`, `scratch/`, root `requirements.txt` | Stale artefacts |
| `get_prr_data`, `get_drug_event_counts`, `get_signal`, `init_interaction_predictions_table` in `backend/database.py` | Never called (`get_prediction` stays; a test uses it) |
| `PatchedRequests` in `backend/lstm_model.py` | Duplicate of `helpers/http_client.py` |
| `/api/system/start-bigdata` | Runs `docker start` from a web request |
| Duplicate routes: `/api/prr` vs `/api/analytics/prr`; `/api/graph` vs `/api/graph/detailed` | Merge to one each, keep the old path as an alias until the frontend is switched |

Removed later, when their replacement lands:

| Remove | Phase |
|---|---|
| Five overlapping ML guides in `docs/` (replaced by `docs/METHODS.md`) | 8 |
| `get_cached_fda_response`, `cache_fda_response` once the forecaster uses the shared HTTP client; the `fda_cache` table | 5, table in 11 |
| `generate_mock_quarter` from the live downloader (moves to test fixtures) | 6 |
| `INITIAL_GLOBAL_BACKGROUND`, `TOTAL_FAERS_POPULATION`, `get_global_event_count`, `faers_background_events` table, `scripts/recalculate_signals.py` | 10 |
| `HISTORICAL_WARNING_YEARS` | 7 |

Consolidate to one copy each in Phase 5: the admin-key check (four copies), the cache helpers in `routes/temporal.py` and `routes/demographics.py`, the z-score spike code (two copies), the HBase pair lookup (three copies).

## 8. Feature map (nothing is dropped)

| Area | Features |
|---|---|
| Monitor | Dashboard, signal table, safety alerts feed |
| Investigate | Drug profile: demographics, time-to-onset, seasonality, boxed warning, Weber analysis, forecast, trials |
| Interactions | Pair check, polypharmacy graph, uncharted pairs |
| Prescription Review | Upload, confirm, report |
| Text Mining | NER on documents, social listening (exploratory) |
| Pipeline | Kafka ingest (live), Spark/HDFS/HBase (simulated demo) |
| Methods & Validation | Formulas, limitations, benchmark results |

## 9. Deferred until after the pilot

Signal review workflow (open, assess, close), handwriting-specific accuracy targets, dose-range checks, single sign-on, real prescriptions, MedDRA-licensed hierarchy (SMQ grouping).

## 10. Open risks

- FAERS download and load of 20 quarters is about 1.5 GB down and 3–5 GB kept; start it early on Day 2 and let it run unattended.
- Reference sets (OMOP, EU-ADR) need a drug and outcome mapping to RxNorm ingredients and Preferred Term groups; budget half of Day 4 for it.
- DDI retraining time on CPU is unknown. Fallback: deploy XGBoost as the modelled tier and keep GINE as a labelled experimental score.
- The cloud vision model needs `ANTHROPIC_API_KEY`; without it the tab falls back to local OCR and says so.

## 11. Plan files

Task-level plans are in `.planning/phases/05-*` to `11-*`, three per phase (21 in total). Plans marked `autonomous: false` contain a step that needs a person: the 20-quarter load (06-03), obtaining and reviewing reference sets (08-01), the paid cloud extraction benchmark (09-03) and the fresh-clone release check (11-03).

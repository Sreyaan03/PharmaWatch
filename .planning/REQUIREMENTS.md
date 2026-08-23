# Requirements — PharmaWatch

## Hardening & Real Data (Phase 2)

### HARD-01: Remove hardcoded 127.0.0.1 from _interactions_new.js
Replace all 7 absolute `http://127.0.0.1:5000/...` fetch URLs in `src/_interactions_new.js` with relative paths (`/api/...`). The frontend and backend are always served from the same origin in development.

### HARD-02: Remove hardcoded 127.0.0.1 from app.js
Replace all absolute `http://127.0.0.1:5000/...` fetch URLs in `src/app.js` with relative paths. Count verified: 9+ occurrences at lines 523, 525, 814, 892, 1423, 1424, 1425, 1580, 1581, 1582, 2004.

### HARD-03: Remove hardcoded 127.0.0.1 from ml_models.js
Replace all 3 absolute `http://127.0.0.1:5000/...` fetch URLs in `src/ml_models.js` with relative paths. Lines 65, 170, 304.

### HARD-04: Replace fake Drug Trend Chart with LSTM data
`src/app.js:586` builds `fakeTrend` using `Math.random()`. Replace with real data from `/api/lstm?drug={drug}` — use the `actual` array as the trend line. Fall back to a flat line at `totalReportsLocal/12` if LSTM call fails.

### HARD-05: Replace fake Signal Intensity Chart with real backend data
`src/app.js:657-695` uses `randomArray()` to generate 3 series of 24 fake datapoints. Add backend endpoint `/api/dashboard/signal-intensity` that queries the SQLite `signals` cache table, groups by severity (critical/high/moderate) and buckets into 24 hourly time slots. Frontend polls this endpoint and falls back to zeroed arrays if unavailable.

### HARD-06: Replace fake Drug Category Doughnut with openFDA data
`src/app.js:698-719` uses hardcoded `[31, 18, 22, 14, 9, 6]`. Add backend endpoint `/api/dashboard/drug-categories` that calls openFDA `drug/event.json?count=patient.drug.medicinalproduct.exact` for known category seed drugs and returns real proportional counts. Frontend calls this on dashboard init.

### HARD-07: Add /api/local-stats backend endpoint
The FEATURE_AUDIT recommends pulling animated counters from a real `/api/local-stats` endpoint. Create endpoint in `backend/app.py` that returns: `{ drugs_monitored, signals_detected, reports_analyzed, drug_event_pairs }` from SQLite. Use SQLite `SELECT COUNT(DISTINCT drug)`, `COUNT(*)` from `signals` and `drug_events` tables.

### HARD-08: Fix CORS scope + pipeline disclaimer badge
1. CORS: Change `CORS(app)` from wildcard to `CORS(app, origins=os.getenv("ALLOWED_ORIGINS", "http://localhost:5000,http://127.0.0.1:5000").split(","))`
2. Pipeline section: Add a visible "Simulated — Architecture Demo Only" badge/banner above the Kafka/Spark/HDFS metrics so users understand they are illustrative.

## Backend Architecture (Phase 3)

### ARCH-01: Split app.py into Flask Blueprints
Modularize the 2928-line `backend/app.py` into blueprint modules:
- `backend/routes/drugs.py` — drug label, events, autocomplete, trials
- `backend/routes/signals.py` — PRR, signals, signal network, NER
- `backend/routes/interactions.py` — graph, predict, polypharmacy, uncharted, recent
- `backend/routes/boxed_warnings.py` — boxed warning, events, timeline, bias analysis, violations
- `backend/routes/ml.py` — LSTM train/predict, BioBERT, gap-scan
- `backend/routes/system.py` — HBase status, system start, local-stats, reddit
- `backend/app.py` becomes a thin entry point (< 200 lines): registers blueprints, initializes extensions

### ARCH-02: Add python-dotenv .env loading
Add `from dotenv import load_dotenv; load_dotenv()` at top of app.py. Read `FLASK_SECRET_KEY`, `FDA_API_KEY`, `ALLOWED_ORIGINS`, `HBASE_HOST`, `HBASE_PORT` from `.env`. Update `backend/.env.example` with documented keys.

## DDI Severity (Phase 4)

### DDI-01: Create ddi_lookup.py module
New file `backend/ddi_lookup.py` with `init_ddi_table()`, `load_ddinter_csv()`, `lookup_interaction(drug_a, drug_b)`, `get_all_interactions_for_drug(drug)`. Auto-initializes SQLite `ddi_interactions` table on import.

### DDI-02: Update /api/graph to use real DDI severity
Replace count-based risk thresholds in `/api/graph` with DDInter lookups. Each co-drug node gets severity from `lookup_interaction()`. Unknown severity (no DDInter record) is shown as "unknown" risk, not fabricated.

### DDI-03: Update D3 graph coloring
D3 graph in `src/app.js` or `src/_interactions_new.js` colors nodes by the severity field returned from `/api/graph`: major=red, moderate=amber, minor=green, unknown=grey.

## GNN Training (Phase 1 — COMPLETE)

### GNN-01: Balanced dataset — DONE (commit 25d9a7d)
### GNN-02: 16-feature atom encoding — DONE (commit 25d9a7d)
### GNN-03: 20 epochs with val split — DONE (commit 25d9a7d)
### GNN-04: Side-effects on graph edges — DONE (commit a68b2f3)
### GNN-05: Safe-graph rendering fix — DONE (commit a68b2f3)
### GNN-06: D3 zoom/pan + reset button — DONE (commit a68b2f3)
### GNN-07: Parallel PubChem resolution — DONE (commit a68b2f3)

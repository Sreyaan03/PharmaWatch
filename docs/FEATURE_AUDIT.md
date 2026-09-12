# PharmaWatch — Feature Audit: Real vs Hardcoded
**Last updated:** 2026-06-05  
**Reflects commits:** `5ac78a3` → `f175af7` → `25d9a7d` → `a68b2f3`

---

## Summary Table

| Feature | Status | Data Source |
|---------|--------|-------------|
| Drug Search autocomplete | ✅ **REAL** | openFDA `label.json` API |
| Drug Profile (class, indication, dosage) | ✅ **REAL** | openFDA `label.json` API |
| ADE Bar Chart (top adverse events) | ✅ **REAL** | openFDA `event.json` API |
| Drug Trend Line (monthly reports) | ❌ **FAKE** | `Math.random()` × total reports (app.js:586) |
| \"Active Signals\" badge | ❌ **REMOVED** | Removed from header — not rendered anymore |
| \"Highest PRR\" badge | ✅ **REAL** | `/api/prr-trials` endpoint (slow async load) |
| PRR Calculator | ✅ **REAL** | Backend `/api/prr` → openFDA (4 API calls) |
| LSTM Time-Series Chart (ML Models tab) | ✅ **REAL** | Backend `/api/lstm` → openFDA `receivedate` counts |
| LSTM Gap-Scan (Warning Gap bar) | ✅ **REAL** | Backend `/api/lstm/gap-scan` → FAERS comparison |
| D3 Interaction Graph (Drug Search tab) | ✅ **REAL** | Backend `/api/graph` → openFDA co-prescription data |
| Signal Intensity Chart (24h) | ❌ **FAKE** | `randomArray(24, 5, 20)` (app.js:668) |
| Drug Category Doughnut | ❌ **FAKE** | Hardcoded `[31, 18, 22, 14, 9, 6]` (app.js:707) |
| PRR Distribution Histogram | ✅ **REAL** (derived) | Built from cached live signals in SQLite |
| Signals Table | ✅ **REAL** | `/api/signals` → live PRR/ROR/BCPNN from FAERS + SQLite cache |
| Signal Network Graph (on row click) | ✅ **REAL** | `/api/signals/network` → openFDA co-report data |
| Recent Alerts List | ✅ **REAL** | Pulled from `/api/signals` cached results on load |
| Cluster Grid (dashboard) | ❌ **NOT PRESENT** | Removed in f175af7 refactor |
| Animated counters (dashboard) | ❌ **FAKE** | Hardcoded `data-target` values in HTML |
| BioBERT NER (Signals tab) | ✅ **REAL** | `d4data/biomedical-ner-all` model — correct model ✅ |
| NER Mine (PDF/Text upload) | ✅ **REAL** | SSE stream: OCR → NER → FAERS scoring |
| Wikipedia Tooltips | ✅ **REAL** | Wikipedia REST API |
| Live Alert Banner | ❌ **FAKE** | Hardcoded rotating strings (app.js:795) |
| Pipeline Metrics (Kafka msg/s, Spark, HBase) | ❌ **FAKE** | `distributed_storage.js` — fully simulated |
| Source Meters (FDA, EHR, Social) | ❌ **FAKE** | Hardcoded percentages cycling (app.js:776) |
| **[NEW] DDI Interaction Graph (Interactions tab)** | ✅ **REAL** | `/api/graph/twosides` → TWOSIDES HBase + openFDA |
| **[NEW] GNN DDI Prediction** | ✅ **REAL** | `/api/graph/predict` → PyTorch GNN (retrained, 16-feat, balanced) |
| **[NEW] Polypharmacy Analysis** | ✅ **REAL** | `/api/interactions/polypharmacy` → GNN per-pair + TWOSIDES side-effects |
| **[NEW] Side-Effects on Graph Edges** | ✅ **REAL** | HBase `interactions` table lookup → tooltip + pair list |
| **[NEW] Safe-pair Graph (all-safe fix)** | ✅ **FIXED** | Graph no longer disappears when no harmful pairs |
| **[NEW] Zoom/Pan on Polypharmacy Graph** | ✅ **REAL** | D3 zoom behaviour added |
| **[NEW] Uncharted Interactions** | ✅ **REAL** | `/api/interactions/uncharted` → FAERS signal mining (no label) |
| **[NEW] Recent Interactions History** | ✅ **REAL** | `/api/interactions/recent` → SQLite `predictions` table |
| **[NEW] Boxed Warnings tab** | ✅ **REAL** | `/api/boxed-warning/<drug>` → openFDA label scrape |
| **[NEW] Boxed Warning Events chart** | ✅ **REAL** | `/api/boxed-warning-events/<drug>` → FAERS event spikes |
| **[NEW] Boxed Warning Timeline** | ✅ **REAL** | `/api/boxed-warning/timeline/<drug>` → LSTM pre/post analysis |
| **[NEW] Weber Effect / Bias Analysis** | ✅ **REAL** | `/api/boxed-warning/bias-analysis/<drug>` → notoriety peak detection |
| **[NEW] FDA Violations Table** | ✅ **REAL** | `/api/boxed-warning/violations/all` → SQLite `violations` table |
| **[NEW] Clinical Trials Badge** | ✅ **REAL** | `/api/trials/<drug>` → ClinicalTrials.gov API |
| **[NEW] PRR + Trials combined badge** | ✅ **REAL** | `/api/prr-trials` → openFDA + trials combined |
| **[NEW] Drug name resolver (PubChem)** | ✅ **REAL** | `/api/interactions/resolve` → PubChem parallel lookup |
| **[NEW] Big Data system start/status** | ⚠️ **PARTIAL** | `/api/system/start-bigdata` attempts real HBase; falls back gracefully |
| INTERACTION_DATA dead code | ✅ **CLEANED** | Removed in f175af7 |
| RECENT_ALERTS_DATA hardcoded array | ✅ **CLEANED** | Removed — alerts now from live signals |

---

## Section-by-Section Breakdown

### 1. Drug Search Section
| Element | Real/Fake | Fix Difficulty |
|---------|-----------|----------------|
| Autocomplete suggestions | ✅ Real | — |
| Drug class & indication | ✅ Real | — |
| Dosage info | ✅ Real | — |
| ADE bar chart | ✅ Real | — |
| Monthly trend chart | ❌ Fake | **Easy** — use `/api/lstm` data (Fix 2A in guide) |
| \"Highest PRR\" badge | ✅ Real (async) | — |
| Clinical Trials badge | ✅ Real (async) | — |
| Signal alerts under chart | ✅ Real | ADE events from openFDA |

### 2. Signals Detection Table
| Element | Real/Fake | Fix Difficulty |
|---------|-----------|----------------|
| Signals table (watchlist) | ✅ Real | — |
| PRR / ROR / BCPNN values | ✅ Real | — |
| Report counts | ✅ Real | — |
| Signal network graph (on click) | ✅ Real | — |
| NER mine (text/PDF upload) | ✅ Real | — |

### 3. Dashboard Page
| Element | Real/Fake | Fix Difficulty |
|---------|-----------|----------------|
| Signal Intensity (24h line chart) | ❌ Fake | **Medium** — add `/api/dashboard/signal-intensity` (guide Phase 2C) |
| Drug Category doughnut | ❌ Fake | **Easy** — openFDA `count` endpoint (guide Phase 2E) |
| PRR Distribution histogram | ✅ Real | Derived from live cached signals |
| Recent Alerts list | ✅ Real | From `/api/signals` on boot |
| Animated counters | ❌ Fake | **Easy** — pull from `/api/local-stats` |
| Live alert banner | ❌ Fake | **Low priority** — cosmetic |

### 4. ML Models Section
| Element | Real/Fake | Fix Difficulty |
|---------|-----------|----------------|
| PRR Calculator | ✅ Real | — |
| LSTM Chart | ✅ Real | — |
| LSTM Warning Gap bar | ✅ Real | — |
| BioBERT NER demo | ✅ Real | `d4data/biomedical-ner-all` model |

### 5. Interactions Tab — **Largely New & Real**
| Element | Real/Fake | Fix Difficulty |
|---------|-----------|----------------|
| TWOSIDES DDI Graph | ✅ Real | — |
| GNN DDI Prediction (single pair) | ✅ Real | — |
| Polypharmacy multi-drug analysis | ✅ Real | — |
| Side-effects on graph edges & tooltips | ✅ Real | — |
| Safe-pair graph rendering | ✅ Fixed | — |
| Zoom/pan graph | ✅ Real | — |
| Uncharted Interactions | ✅ Real | — |
| Recent interaction history | ✅ Real | — |
| Hardcoded absolute URLs (`127.0.0.1`) | ❌ Still present | **Easy** — 7 occurrences in `_interactions_new.js` |

### 6. Boxed Warnings Tab — **Fully New & Real**
| Element | Real/Fake | Fix Difficulty |
|---------|-----------|----------------|
| Boxed warning text | ✅ Real | — |
| Post-warning FAERS event spike chart | ✅ Real | — |
| Warning timeline (pre/post LSTM) | ✅ Real | — |
| Weber Effect / Bias Analysis | ✅ Real | — |
| FDA Violations table | ✅ Real | — |
| Hardcoded absolute URLs (`127.0.0.1`) | ❌ Still present | **Easy** — 5 occurrences in `app.js` |

### 7. Pipeline Section
| Element | Real/Fake | Fix Difficulty |
|---------|-----------|----------------|
| Kafka msg/s | ❌ Simulated | N/A — architecture demo |
| Spark events/s | ❌ Simulated | N/A — architecture demo |
| HDFS storage | ❌ Simulated | N/A — architecture demo |
| HBase ops | ❌ Simulated | N/A — architecture demo |
| Source meters | ❌ Hardcoded | N/A — architecture demo |

> Recommend adding a visible disclaimer badge per guide Phase 5D.

---

## Remaining Issues (Quick Fix List)

### 🔴 Security / Reliability
1. **`127.0.0.1` hardcoded** in `_interactions_new.js` (7 occurrences) and `app.js` (9 occurrences) — replace with relative paths

### 🟠 Fake Data Still Present
2. **Drug Trend Chart** — `app.js:586` still uses `Math.random()` — easy fix with `/api/lstm`
3. **Signal Intensity Chart** — `app.js:668` uses `randomArray()` — needs new backend endpoint
4. **Drug Category Doughnut** — `app.js:707` hardcoded — easy fix with openFDA count API
5. **Animated Counters** — `data-target` values in HTML are hardcoded — pull from `/api/local-stats`
6. **Live Alert Banner** — `app.js:795-804` cycles hardcoded strings

### 🟡 Architecture
7. **`backend/app.py` is 2,928 lines** — needs splitting into route blueprints (Phase 4 of guide)
8. **CORS is wide-open** — `CORS(app)` allows `*` — should be scoped to `ALLOWED_ORIGIN`

---

## Real vs Fake Score

| Version | Real Features | Fake/Hardcoded | Score |
|---------|--------------|----------------|-------|
| **Original audit** | 7 / 20 | 13 / 20 | 35% real |
| **Current (after f175af7 + a68b2f3)** | **~28 / 38** | **~10 / 38** | **~74% real** |

**Net new real features added:** Polypharmacy GNN, TWOSIDES DDI graph, Uncharted Interactions, Boxed Warnings, Weber Effect/Bias Analysis, Violations table, Clinical Trials badge, PRR+Trials badge, LSTM gap-scan, NER mine pipeline, side-effects on edges, drug name resolver.

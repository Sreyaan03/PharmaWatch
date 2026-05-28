# PharmaWatch — Feature Audit: Real vs Hardcoded

## Overview

| Feature | Status | Data Source |
|---------|--------|-------------|
| Drug Search autocomplete | ✅ **REAL** | openFDA `label.json` API |
| Drug Profile (class, indication, dosage) | ✅ **REAL** | openFDA `label.json` API |
| ADE Bar Chart (top adverse events) | ✅ **REAL** | openFDA `event.json` API |
| Drug Trend Line (monthly reports) | ❌ **FAKE** | `Math.random()` × total reports (line 395) |
| "Active Signals" badge on drug profile | ❌ **FAKE** | `Math.random() * 4` (line 343) |
| "Highest PRR" badge on drug profile | ❌ **FAKE** | `1.5 + Math.random() * 2` (line 344) |
| PRR Calculator | ✅ **REAL** | Backend `/api/prr` → openFDA (4 API calls) |
| LSTM Time-Series Chart | ✅ **REAL** | Backend `/api/lstm` → openFDA `receivedate` counts |
| D3 Interaction Graph | ✅ **REAL** | Backend `/api/graph` → openFDA co-prescription data |
| Signal Intensity Chart (24h) | ❌ **FAKE** | `randomArray(24, 5, 20)` (lines 577–591) |
| Drug Category Doughnut | ❌ **FAKE** | Hardcoded: `[31, 18, 22, 14, 9, 6]` (line 616) |
| PRR Distribution Histogram | ❌ **FAKE** | Hardcoded: `[1840, 920, 480, ...]` (line 641) |
| Signals Table (40 signals) | ❌ **FAKE** | All random: `Math.random() * 7 + 1` for PRR (line 136) |
| Recent Alerts List | ❌ **FAKE** | Hardcoded array `RECENT_ALERTS_DATA` (lines 23–26) |
| Cluster Grid | ❌ **FAKE** | Hardcoded array `CLUSTERS` (lines 124–131) |
| INTERACTION_DATA object | ❌ **DEAD CODE** | Old hardcoded graph data (lines 30–122) — no longer used |
| Pipeline Metrics (Kafka msg/s, etc.) | ❌ **FAKE** | Hardcoded strings cycling (lines 700–716) |
| Source Meters (FDA, EHR, Social) | ❌ **FAKE** | Hardcoded percentages (lines 685–697) |
| BioBERT NER | ⚠️ **RUNS** but broken | Base model, random labels — not fine-tuned |
| Wikipedia Tooltips | ✅ **REAL** | Wikipedia REST API |
| Live Alert Banner | ❌ **FAKE** | Hardcoded rotating strings (lines 990–1001) |

---

## Section-by-Section Breakdown

### 1. Drug Search Section
| Element | Real/Fake | Fix Difficulty |
|---------|-----------|----------------|
| Autocomplete suggestions | ✅ Real | — |
| Drug class & indication | ✅ Real | — |
| Dosage info | ✅ Real | — |
| ADE bar chart | ✅ Real | — |
| Monthly trend chart | ❌ Fake | **Easy** — use `/api/lstm` data |
| "Active Signals" count | ❌ Fake | **Medium** — needs real PRR check |
| "Highest PRR" value | ❌ Fake | **Medium** — calculate from top ADE |
| Signal alerts under chart | ⚠️ Semi-real | Events are real, status labels are fake |

### 2. Signals Detection Table
| Element | Real/Fake | Fix Difficulty |
|---------|-----------|----------------|
| All 40 signals | ❌ Fully random | **Hard** — need batch PRR for multiple drugs |
| PRR values | ❌ `Math.random()` | Same as above |
| Report counts | ❌ `Math.random()` | Same as above |
| Source attribution | ❌ Random | Same as above |

### 3. Dashboard Page
| Element | Real/Fake | Fix Difficulty |
|---------|-----------|----------------|
| Signal Intensity (24h line chart) | ❌ Random | **Medium** — aggregate from DB |
| Drug Category doughnut | ❌ Hardcoded | **Easy** — count from openFDA |
| PRR Distribution histogram | ❌ Hardcoded | **Hard** — needs many PRR calculations |
| Recent Alerts list | ❌ Hardcoded | **Easy** — pull from DB |
| Cluster Grid | ❌ Hardcoded | **Hard** — needs graph analysis |
| Animated counters | ❌ Hardcoded | **Easy** — pull from DB stats |

### 4. ML Models Section
| Element | Real/Fake | Fix Difficulty |
|---------|-----------|----------------|
| PRR Calculator | ✅ Real | — |
| LSTM Chart | ✅ Real | — |
| BioBERT demo | ⚠️ Runs, bad output | **Easy** — swap model (Phase 1 upgrade) |

### 5. Interaction Graph
| Element | Real/Fake | Fix Difficulty |
|---------|-----------|----------------|
| D3 force graph | ✅ Real | — |
| Co-prescription data | ✅ Real | — |
| Risk coloring | ✅ Real (relative) | — |
| Autocomplete search | ✅ Real | — |

### 6. Pipeline Section
| Element | Real/Fake | Fix Difficulty |
|---------|-----------|----------------|
| Kafka msg/s | ❌ Hardcoded strings | N/A (visual demo) |
| Spark events/s | ❌ Hardcoded strings | N/A (visual demo) |
| HDFS storage | ❌ Hardcoded strings | N/A (visual demo) |
| Source meters | ❌ Hardcoded | N/A (visual demo) |

---

## Dead Code to Clean Up

The `INTERACTION_DATA` object (lines 30–122) is **no longer used**. The D3 graph now fetches from `/api/graph`. This entire block can be deleted to save ~90 lines.

---

## Suggested Improvements (by priority)

### Quick Wins (< 1 hour each)

1. **Make the Drug Trend chart real** — Replace `Math.random()` on line 395 with a `fetch('/api/lstm?drug=...')` call. You already have the endpoint.

2. **Make "Highest PRR" badge real** — After loading ADE data, call `/api/prr?drug=X&event=TOP_ADE` for the #1 adverse event and display the real value.

3. **Delete dead INTERACTION_DATA** — Lines 30–122. Not used anymore.

4. **Fix BioBERT** — Swap model to `d4data/biomedical-ner-all` (Phase 1 from upgrade guide).

### Medium Effort (2–4 hours)

5. **Make Recent Alerts real** — After Phase 2 (database), pull latest drug-event pairs from SQLite.

6. **Add event dropdown to PRR Calculator** — Currently hardcoded to "Nausea". Add a second autocomplete input so users can pick any event.

7. **Make "Active Signals" count real** — Check if PRR > 2 for the top 5 ADEs of the searched drug.

8. **Drug Category doughnut from real data** — Use openFDA `count` endpoint: `api.fda.gov/drug/event.json?count=patient.drug.openfda.pharm_class_epc.exact`

### Bigger Features (half day+)

9. **Real Signals Table** — Precompute PRR for top drug-event pairs and store in database. Display real ranked signals.

10. **Search history / recent drugs** — Store recent searches in localStorage, show as chips.

11. **Comparison mode** — Search two drugs side-by-side, compare ADE profiles.

12. **Export data** — Add "Download CSV" buttons for charts and signal tables.

13. **Drug recall alerts** — Fetch from `api.fda.gov/drug/enforcement.json` and show real FDA recall notices for the searched drug.

---

## Summary

**Real features**: 7 out of 20 (Drug search, profile, ADE chart, PRR calc, LSTM, D3 graph, Wikipedia tooltips)

**Fake/hardcoded**: 13 out of 20

**Biggest impact fixes**: Trend chart (#1), BioBERT (#4), Signals table (#9)

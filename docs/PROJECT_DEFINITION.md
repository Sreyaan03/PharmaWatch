# PharmaWatch — Project Definition Document

## One-Liner
A real-time pharmacovigilance dashboard that detects adverse drug reactions and drug-drug interactions using live FDA data, NLP, and graph-theoretic analysis.

---

## What It Does

PharmaWatch monitors drug safety signals by:

1. **Signal Detection** — Computes Proportional Reporting Ratios (PRR) from live FDA FAERS data to identify statistically unusual drug-event combinations
2. **Time-Series Forecasting** — Fetches real adverse event counts from openFDA and generates LSTM-style baseline predictions to spot accelerating safety signals
3. **Drug-Drug Interaction Mapping** — Builds D3.js force-directed graphs showing co-prescription networks from real FAERS reports, color-coded by relative frequency
4. **Named Entity Recognition** — Runs BioBERT-based NER on free-text clinical reports to extract drug names and adverse events
5. **Drug Profiling** — Fetches real-time drug labels, indications, warnings, and adverse event distributions from the openFDA API
6. **Contextual Definitions** — Hover tooltips pull disease/condition definitions from Wikipedia's API in real-time

### Data Flow (Current)
```
openFDA FAERS API (25M+ adverse event reports)
        ↓
Flask Backend (Python)
   ├── /api/prr    → PRR calculation from live data
   ├── /api/lstm   → Time-series from real event counts
   ├── /api/graph  → Co-prescription networks
   └── /api/ner    → BioBERT entity extraction
        ↓
Frontend Dashboard (HTML/CSS/JS)
   ├── Chart.js    → Bar/line/doughnut charts
   ├── D3.js v7    → Force-directed interaction graphs
   └── Live autocomplete from openFDA drug names
```

### Data Flow (After Upgrade — in progress)
```
Reddit (PRAW)  +  openFDA FAERS  +  FDA Labels
       ↓                ↓                ↓
  BioBERT NER     Direct queries    Warnings/interactions
       ↓                ↓                ↓
       └────── SQLite Database ──────────┘
                     ↓
              Flask Backend
                     ↓
           Frontend Dashboard
```

---

## Who It's For

| Audience | Use Case |
|----------|----------|
| **Pharmacovigilance officers** (pharma companies) | Monitor their drugs for emerging safety signals |
| **Drug safety regulators** (FDA, EMA, CDSCO) | Review adverse event trends across the market |
| **Clinical pharmacologists** | Research drug-drug interactions and ADR patterns |
| **Hospital pharmacists** | Check interaction risks before prescribing polypharmacy |
| **Pharmacoepidemiology researchers** | Run PRR/disproportionality analyses |
| **Medical students** | Learn about ADRs and drug interactions with real data |

### Market Context
- Enterprise equivalents: Oracle Argus ($50k+/yr), ArisGlobal, FDA's FAERS Dashboard
- PharmaWatch is an open-source alternative targeting researchers and smaller orgs who can't afford enterprise tools

---

## Tech Stack

### Backend
| Technology | Purpose | Details |
|-----------|---------|---------|
| **Python 3.x** | Core backend language | |
| **Flask** | REST API server | Serves 5 endpoints |
| **Flask-CORS** | Cross-origin requests | Enables frontend↔backend communication |
| **BioBERT / d4data NER** | Named Entity Recognition | HuggingFace Transformers model for biomedical text |
| **PyTorch** | ML inference engine | Runs NER model inference |
| **NumPy** | Numerical computation | LSTM baseline smoothing, PRR math |
| **Requests** | HTTP client | Fetches from openFDA API |
| **SQLite** | Local database | Stores drug-event pairs (in-progress upgrade) |
| **PRAW** | Reddit API wrapper | Social media pharmacovigilance (in-progress upgrade) |

### Frontend
| Technology | Purpose |
|-----------|---------|
| **Vanilla HTML/CSS/JS** | Core UI — no framework dependency |
| **Chart.js 4.4** | Bar, line, doughnut data visualizations |
| **D3.js v7** | Force-directed drug interaction graphs |
| **Tippy.js + Popper.js** | Wikipedia-powered hover tooltips |
| **Google Fonts (Inter)** | Typography |

### External APIs (all free, no cost)
| API | Data | Volume |
|-----|------|--------|
| **openFDA FAERS** | 25M+ adverse event reports | Real-time queries |
| **openFDA Labels** | FDA-approved drug labels | Drug info, warnings, interactions |
| **Wikipedia REST** | Medical condition definitions | On-hover tooltips |
| **Reddit (PRAW)** | Patient drug discussions | Social media NLP pipeline (planned) |

### Architecture Concepts Demonstrated
- Real-time data streaming architecture (designed for Kafka/Spark)
- NLP/NER pipeline for unstructured text processing
- Disproportionality analysis (PRR — standard pharmacovigilance method)
- Graph-theoretic network analysis (co-prescription detection)
- Time-series anomaly detection (LSTM-style baseline comparison)
- API aggregation and caching layer

---

## Features Breakdown

### Implemented & Working
- [x] Live drug search with openFDA autocomplete
- [x] Real-time drug profiles (indications, class, dosage, warnings)
- [x] FAERS adverse event distribution charts (real data)
- [x] PRR calculator pulling live 2x2 contingency data from openFDA
- [x] LSTM time-series visualization with real openFDA event counts
- [x] D3.js force-directed drug interaction graph (real co-prescription data)
- [x] Free-text drug name search for interaction graph
- [x] Signal detection table with severity filtering
- [x] BioBERT NER endpoint (model loads and runs inference)
- [x] Wikipedia tooltip definitions for medical terms
- [x] Responsive dashboard with 7 sections
- [x] Big data pipeline visualization (Kafka/Spark/HDFS architecture)
- [x] Live alert banner with rotating safety signals

### In Progress (Upgrade Guide written)
- [ ] Swap to fine-tuned biomedical NER model (d4data/biomedical-ner-all)
- [ ] SQLite database for persistent drug-event storage
- [ ] Reddit ingestion pipeline (scrape → NER → store)
- [ ] FDA label-based interaction severity (real clinical warnings)

### Simulated / Demo-Only
- Pipeline throughput metrics (Kafka msg/s, Spark events/s) — visual demo
- HDFS storage counters — visual demo
- Some signal table data uses randomized PRR values

---

## Project Metrics

| Metric | Value |
|--------|-------|
| **Frontend** | ~960 lines JS (app.js) + ~130 lines (ml_models.js) + ~290 lines (api_layer.js) + ~916 lines HTML |
| **Backend** | ~268 lines Python (app.py) |
| **API integrations** | 3 external APIs (openFDA, Wikipedia, Reddit planned) |
| **ML models** | 1 (BioBERT/biomedical NER) |
| **Data source** | 25M+ real adverse event reports via FDA |
| **Visualizations** | 6+ interactive charts + 1 force-directed graph |
| **Endpoints** | 5 REST API routes |

---

## What Makes It Non-Trivial

1. **Real data, not mock** — PRR, LSTM, and Graph features query live openFDA data (25M+ records)
2. **Production NLP** — Actually loads and runs a transformer model (BioBERT) for entity extraction
3. **Statistical pharmacovigilance** — Implements the real PRR formula used by FDA/WHO
4. **Graph theory** — D3.js physics simulation for drug interaction networks
5. **Multi-API orchestration** — openFDA + Wikipedia + Reddit coordinated in one app
6. **Full-stack** — Python backend + vanilla frontend + database + ML model
7. **Domain expertise** — Requires understanding pharmacovigilance, FAERS, MedDRA, disproportionality analysis

---

## Team

| Name | Roll No | Institution |
|------|---------|-------------|
| Sreyaan Roy | 230968184 | Manipal Academy of Higher Education |
| Sohan Arvind Sanil | 2309681 | Manipal Academy of Higher Education |

**Context**: Big Data Mini-Project

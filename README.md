# PharmaWatch — Real-Time Adverse Drug Event Detection Platform

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![HTML5](https://img.shields.io/badge/HTML5-E34F26?logo=html5&logoColor=white)
![CSS3](https://img.shields.io/badge/CSS3-1572B6?logo=css3&logoColor=white)
![JavaScript](https://img.shields.io/badge/JavaScript-F7DF1E?logo=javascript&logoColor=black)

> A CDC-styled pharmacovigilance web dashboard — Big Data Mini-Project by Sreyaan Roy & Sohan Arvind Sanil, Manipal Academy of Higher Education.

---

## Overview

**PharmaWatch** is a simulated front-end demonstration of a real-time, multi-source big data pipeline for Adverse Drug Event (ADE) detection. The interface is built in the style of the CDC (Centers for Disease Control and Prevention) website, presenting:

- Live signal detection tables with PRR scoring
- Drug safety search with profile views
- Drug–Drug interaction network graphs
- Data pipeline architecture visualizer
- Analytical methodology explorer (BioBERT, PRR, LSTM, Graph algorithms)

> ⚠ **Disclaimer:** All data shown is simulated for educational purposes. This is NOT a clinical tool and should not be used for medical decisions.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Structure | HTML5 (Semantic) |
| Styling | Vanilla CSS (CSS Custom Properties, Grid, Flexbox) |
| Logic | Vanilla JavaScript (ES2020) |
| Charts | [Chart.js v4.4.0](https://www.chartjs.org/) (CDN) |
| Fonts | Google Fonts — Open Sans, Source Sans 3 |

No build tools, no frameworks, no dependencies to install — just open and run.

---

## Project Structure

```
PharmaWatch/
├── src/
│   ├── index.html      # Main application (all 7 sections)
│   ├── style.css       # CDC-inspired design system
│   └── app.js          # Data engine, charts, navigation
├── README.md           # Developer README (this file)
├── README_SIMPLE.md    # Plain-English guide for non-technical users
└── PharmaWatch_Report_BigData_MiniProject.docx
```

---

## Running the App

PharmaWatch now uses a real Python/Flask backend to power its live openFDA data fetching, PRR calculations, and LSTM machine learning model.

### 1. Prerequisites
- [Python 3.10+](https://www.python.org/downloads/)

### 2. Setup the Environment
Open a terminal in the project folder and install the required Python packages:
```bash
pip install -r backend/requirements.txt
```

### 3. Add Your API Key
To fetch live data from openFDA without getting rate-limited, you need an API key.
1. Create a file named `.env` inside the `backend/` folder.
2. Add your key like this:
```ini
FDA_API_KEY=your_api_key_here
```
*(If you don't have one, get a free key at [openFDA](https://open.fda.gov/apis/authentication/))*

### 4. Run the Server
Start the Flask backend server:
```bash
python backend/app.py
```
*(On Mac/Linux, you might need to use `python3 backend/app.py`)*

### 5. Open the Dashboard
Once the server is running, open your web browser and go to:
**http://127.0.0.1:5000**

---

## Features

### 1. Dashboard
- Animated stat counters (reports processed, active signals, latency)
- 3-range signal intensity chart (24H / 7D / 30D)
- Real-time alert feed with severity colour-coding
- Drug category ADE distribution (donut chart)
- Data source volume meters (live-simulated)
- Top 7 reported adverse events
- Pipeline health panel (Kafka → Spark → BioBERT → LSTM → HDFS)

### 2. Signal Detection
- Sortable/filterable table of 40 drug-event PRR signals
- Severity filter (Critical PRR >5, High PRR 3–5, Moderate PRR 2–3)
- Source filter (openFDA, EHR, Social Media, ClinicalTrials)
- Click any row → expandable detail panel with PRR trend chart
- PRR distribution histogram

### 3. Drug Search
- Autocomplete search across 30 drugs
- Drug profile: drug class, indication, total reports, active signals
- ADE frequency bar chart (MedDRA-coded)
- 12-month reporting trend line chart
- Active signals list per drug
- Popular drug chips for quick access

### 4. Drug–Drug Interaction Graph
- Canvas-based animated interaction network graph
- Select drug → view interaction nodes with risk colour coding
- Polypharmacy cluster listing with risk descriptions

### 5. Data Sources & Pipeline
- Visual architecture diagram: Ingestion → Processing → Storage layers
- Live-simulated message rates per Kafka topic
- Throughput and consumer lag charts

### 6. Methodology
- BioBERT NLP section with live throughput counter
- Interactive PRR calculator (enter a/b/c/d values, compute PRR live)
- Graph-theoretic analysis explanation with tech tags
- LSTM baseline vs. real-time prediction demo chart

---

## Inductive Drug-Drug Interaction (DDI) Benchmark

PharmaWatch incorporates an inductive Graph Isomorphism Network with Edge Features (**GINE**) paired with a 2048-bit Morgan Fingerprint branch to predict novel, uncatalogued drug-drug interaction likelihoods directly from 2D molecular structures.

### Rigorous Evaluation Regimes (TDC DrugBank ~1,705 Drugs)
The model was evaluated against classical machine learning baselines across disjoint drug-level splits with zero molecular leakage:
- **S0 (Warm)**: Both drugs seen in the training graph.
- **S1 (One Unseen)**: Exactly one drug withheld during training (primary inductive cold-start).
- **S2 (Both Unseen)**: Both drugs unseen during training (fully inductive cold-start).

| Model | Regime | AUROC | AUPRC | Recall@100 | Recall@1000 |
| --- | --- | --- | --- | --- | --- |
| **GINE (Calibrated)** | S0 (Warm) | 0.6887 | 0.6731 | 0.0085 | 0.0803 |
| Logistic Regression | S0 (Warm) | 0.8683 | 0.8455 | 0.0098 | 0.0932 |
| XGBoost | S0 (Warm) | 0.8249 | 0.8064 | 0.0096 | 0.0923 |
| NN-Tanimoto | S0 (Warm) | 0.7928 | 0.7867 | 0.0097 | 0.0940 |
| **GINE (Calibrated)** | S1 (One Unseen) | 0.6617 | 0.6410 | 0.0018 | 0.0161 |
| Logistic Regression | S1 (One Unseen) | 0.6939 | 0.6582 | 0.0018 | 0.0164 |
| XGBoost | S1 (One Unseen) | 0.7569 | 0.7379 | 0.0020 | 0.0190 |
| NN-Tanimoto | S1 (One Unseen) | 0.7511 | 0.7311 | 0.0021 | 0.0196 |
| **GINE (Calibrated)** | S2 (Both Unseen) | 0.6374 | 0.6133 | 0.0154 | 0.1252 |
| Logistic Regression | S2 (Both Unseen) | 0.5853 | 0.5653 | 0.0110 | 0.1144 |
| XGBoost | S2 (Both Unseen) | 0.6713 | 0.6571 | 0.0169 | 0.1420 |
| NN-Tanimoto | S2 (Both Unseen) | 0.5765 | 0.5835 | 0.0163 | 0.1250 |

### Architectural Ablations (S1 Setting)
- **GIN (No Edge Features)**: AUROC 0.6722 | AUPRC 0.6536
- **GINE without Morgan Fingerprint**: AUROC 0.5260 | AUPRC 0.4984 (*Drops by 13.5% AUROC; structural fingerprints are indispensable for generalization*)
- **Concatenation Head (Directional)**: AUROC 0.5151 | AUPRC 0.4907 (*Drops to random chance; permutation symmetry $[a+b, a \odot b, \|a-b\|]$ is essential*)

### Calibration & Scientific Limitations
1. **Temperature Scaling**: Post-hoc calibrated with $T = 0.8231$ minimizing negative log likelihood on held-out validation pairs.
2. **Scientific Honesty & Terminology**:
   - Model predictions are strictly labeled **"interaction likelihood score"** (never "probability of harm" or "safety score").
   - Absence of an interaction prediction means **"no evidence found"** rather than "safe to combine". Non-listed pairs in biomedical databases are unlabeled candidates, not confirmed non-interacting pairs.
   - **Research use only**: Predictions are hypothesis-generating and do not constitute clinical guidance.

---
- Project abstract, tech stack grid
- Team member cards (Sreyaan Roy, Sohan Arvind Sanil)
- Key references with live external links

---

## Design System

The UI follows CDC.gov design principles:
- **Primary colour:** `#003d7c` (CDC Blue)
- **Accent:** `#f0c040` (Gold)
- **Alert colours:** Red (critical), Amber (high), Green (moderate)
- **Typography:** Open Sans (body), tabular numerics for counters
- **Animations:** Entry fade-ins, animated counters, pulse badges, graph animations
- **Responsive:** Adapts to mobile, tablet, and desktop

---

## Browser Support

| Browser | Status |
|---------|--------|
| Chrome 90+ | ✅ Full support |
| Firefox 88+ | ✅ Full support |
| Edge 90+ | ✅ Full support |
| Safari 14+ | ✅ Full support |

---

## Academic Context

**Title:** PharmaWatch: A Real-Time, Multi-Source Big Data Pipeline for Adverse Drug Event Detection  
**Course:** Big Data — Mini Project  
**Institution:** Manipal Academy of Higher Education  
**Authors:** Sreyaan Roy (230968184), Sohan Arvind Sanil (2309681)

---

## License

MIT License — free to use, modify, and distribute with attribution.

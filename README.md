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

PharmaWatch scores drug pairs with a graph neural network (**GINE**: a graph network over each molecule's atoms and bonds) plus a 2048-bit Morgan fingerprint branch, trained on TDC DrugBank DDI. The score is a hypothesis from molecular structure, not clinical evidence.

### Evaluation regimes (TDC DrugBank, ~1,705 drugs)
Drugs, not pairs, are split into train, validation and test, so a test drug is never seen in training:
- **S0 (Warm)**: both drugs seen in training.
- **S1 (One Unseen)**: exactly one drug withheld (the primary cold-start setting).
- **S2 (Both Unseen)**: both drugs withheld.

Test sets have 1:1 sampled negatives (unlabelled pairs, not confirmed non-interactions), so the base rate is 0.5. GINE rows are mean ± standard deviation over 5 trained seeds. Every row is a trained model.

| Model | Regime | Seeds | AUROC | AUPRC | Precision@100 |
| --- | --- | --- | --- | --- | --- |
| GINE (full) | S0 (Warm) | 5 | 0.979 ± 0.003 | 0.979 ± 0.003 | 1.000 |
| Logistic regression | S0 (Warm) | 1 | 0.890 | 0.873 | 0.980 |
| XGBoost | S0 (Warm) | 1 | 0.844 | 0.831 | 0.980 |
| NN-Tanimoto | S0 (Warm) | 1 | 0.793 | 0.787 | 0.970 |
| GINE (full) | S1 (One Unseen) | 5 | 0.849 ± 0.006 | 0.851 ± 0.005 | 0.974 ± 0.026 |
| Logistic regression | S1 (One Unseen) | 1 | 0.679 | 0.641 | 0.850 |
| XGBoost | S1 (One Unseen) | 1 | 0.770 | 0.753 | 0.970 |
| NN-Tanimoto | S1 (One Unseen) | 1 | 0.751 | 0.731 | 0.980 |
| GINE (full) | S2 (Both Unseen) | 5 | 0.712 ± 0.010 | 0.725 ± 0.009 | 0.918 ± 0.023 |
| Logistic regression | S2 (Both Unseen) | 1 | 0.574 | 0.554 | 0.620 |
| XGBoost | S2 (Both Unseen) | 1 | 0.672 | 0.660 | 0.900 |
| NN-Tanimoto | S2 (Both Unseen) | 1 | 0.577 | 0.584 | 0.860 |

The full table, with the ablations, is in `reports/ddi_benchmark_results.md`, and is regenerated by `python -m validation.ddi_eval`.

### Ablations (separately trained models, S1)
- **No fingerprint branch**: AUPRC 0.770 ± 0.013 against 0.851 for the full model. The fingerprint branch matters for unseen drugs.
- **No edge features (plain GIN)** and **concatenation head instead of the symmetric head**: both are within the seed-to-seed spread of the full model, so this benchmark does not show that either component helps.

Each model was trained for at most 15 minutes on CPU (about 2-3 epochs); longer training was not explored, so these are results for that budget.

An earlier version of this table reported much larger ablation effects. Those rows came from untrained or modified stand-ins and have been removed.

### Calibration and limitations
1. The model deployed is the one with the best AUPRC on a validation *selection* half; the calibrator is fitted on a different *calibration* half (split by drug). Isotonic regression beat temperature scaling in cross-fitting.
2. On the test sets, ECE fell from 0.153 to 0.021 for S1 and from 0.265 to 0.079 for S2, but **rose from 0.007 to 0.149 for S0**: the calibrated score is trustworthy only for pairs that include a drug the model was not trained on. The served `calibrated` flag and this scope come from `backend/trained_models/ddi_manifest.json`.
3. Terminology: the output is an **"interaction likelihood score"**, never a "probability of harm" or a "safety score". The absence of a prediction means **"no evidence found"**, not "safe to combine".
4. **Research use only.** Predictions are hypothesis-generating and are not clinical guidance.

All validation results, with reference sets and data versions, are in the app under **Methods & Validation**; formulas and thresholds are in `docs/METHODS.md`.

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

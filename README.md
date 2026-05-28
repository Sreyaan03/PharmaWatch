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

### Option 1 — Open Directly (Simplest)
```
Double-click src/index.html
```
Opens in your default browser. All features work offline — no server needed.

### Option 2 — Live-Reload Dev Server (Recommended for Development)
Requires [Node.js](https://nodejs.org) (v16+):
```bash
npx -y live-server src/
```

### Option 3 — Python HTTP Server
```bash
# Python 3
cd src
python -m http.server 8080
# Then open: http://localhost:8080
```

### Option 4 — VS Code
Install the **Live Server** extension → right-click `index.html` → "Open with Live Server"

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

### 7. About
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

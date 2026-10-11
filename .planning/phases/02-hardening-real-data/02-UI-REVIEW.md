# Phase 02 / 03 — UI Visual & Interaction Review

**Audited:** 2026-10-11  
**Baseline:** Abstract 6-Pillar UX Standards (CDC-Inspired Design System)  
**Dev Server:** Active (HTTP 200 on `http://127.0.0.1:5000`)  
**Frontend Stack:** Vanilla HTML5, CSS3 Custom Properties, Vanilla JavaScript (ES6+), D3.js v7, Chart.js v4, Tippy.js / Popper.js

---

## 6-Pillar Scorecard

| Pillar | Score | Rating | Primary Observation |
|---|:---:|:---:|---|
| **1. Copywriting** | 3 / 4 | Good | Contextual, clinically-accurate microcopy ("Analyze Patterns", "Detect Signals", "Weber Effect"). Minor hardcoded error URL in demographics.js. |
| **2. Visuals** | 4 / 4 | Excellent | Strong visual hierarchy, authoritative government/CDC aesthetics, D3 force graph with custom particle/link animations, live radar pulses. |
| **3. Color** | 4 / 4 | Excellent | Strict 60/30/10 palette: 60% clean slate neutrals, 30% deep CDC blues (`#003d7c`), 10% clinical severity accents (Red `#c0392b`, Amber `#f0a500`, Green `#2e7d32`, Gold `#f0c040`). |
| **4. Typography** | 3 / 4 | Good | Standardized Open Sans and Source Sans 3 loaded via Google Fonts; cohesive font scale from `xs` (0.75rem) to `4xl` (2.5rem). Few ad-hoc inline font-sizes. |
| **5. Spacing** | 3 / 4 | Good | Cohesive 4px-based spacing scale (`--space-1` to `--space-16`); minor inline style padding overrides on several form buttons. |
| **6. Experience Design** | 4 / 4 | Excellent | Real-time SSE progress streaming with page-by-page OCR transcription, D3 zoom/pan/drag, animated metric counters, interactive drug resolution badges. |

### **Overall Score: 21 / 24** (Grade: A / Excellent)

---

## Pillar Findings & Deep Dive

### 1. Copywriting (Score: 3/4)
- **Strengths**:
  - High-precision clinical nomenclature used throughout the platform: *Proportional Reporting Ratio (PRR)*, *Reporting Odds Ratio (ROR)*, *BCPNN Information Component (IC025)*, *MedDRA Preferred Term*, *Weber Effect Spike Detection*.
  - Every button has an explicit, domain-specific verb: `Analyze Patterns`, `Analyze Stratification`, `Detect Signals`, `Predict Interaction`, `Train Model`, `Mine Literature`. No generic "Submit" or "Click Here".
  - Empty states provide actionable guidance (e.g. suggesting *Clozapine* or *Metformin* for initial analysis).
- **Findings / Opportunities**:
  - `src/index.html:33`: Top utility bar contains a duplicate divider tag (`<span class="utility-bar__divider">|</span>` rendered twice consecutively).
  - `src/demographics.js:160`: Error message text mentions `Ensure the Flask backend is running at http://127.0.0.1:5000` which is confusing when deployed in cloud environments behind reverse proxies.

### 2. Visuals (Score: 4/4)
- **Strengths**:
  - Clear focal point on dashboard hero with live statistic badges and pulsating online indicators (`.dot-pulse`).
  - Interactive D3 Force Network Graph provides rich visual feedback: nodes size according to report count, edge stroke widths scale with interaction severity, and drag simulation maintains bounding box constraints.
  - Streaming BioBERT NER interface visually displays entity tags with color-coded classification chips (`Medication` in blue, `Sign/Symptom` in orange).

### 3. Color (Score: 4/4)
- **Strengths**:
  - WCAG AA compliant color contrast across all UI surfaces.
  - Comprehensive token system defined in `:root`:
    ```css
    --cdc-blue:       #003d7c;
    --cdc-blue-light: #0070c0;
    --cdc-teal:       #00695c;
    --cdc-red:        #c0392b;
    --cdc-amber:      #f0a500;
    --cdc-green:      #2e7d32;
    --cdc-gold:       #f0c040;
    ```
  - Accent colors are strictly reserved for clinical severity signals (Critical = Red, High = Amber, Moderate = Teal/Blue, Benign/OK = Green).

### 4. Typography (Score: 3/4)
- **Strengths**:
  - Google Fonts preconnect with fallback to system fonts: `'Open Sans', 'Source Sans 3', sans-serif`.
  - Tabular numbers and monospaced font treatment for scientific formulas and disproportionality values.
- **Findings / Opportunities**:
  - Occasional inline styles with explicit font sizes (e.g. `font-size: 0.82rem`) rather than using CSS utility classes.

### 5. Spacing (Score: 3/4)
- **Strengths**:
  - Grid systems (`.dashboard-grid`, `.hero__stats`, `.guide-stepper`) use semantic token gap variables (`var(--space-4)`, `var(--space-6)`).
  - Consistent padding in cards and modal overlays.
- **Findings / Opportunities**:
  - A few buttons in `src/index.html` (lines 1493, 1596) declare inline padding (`padding:var(--space-3) var(--space-6)`) instead of using `.btn--primary` default styling.

### 6. Experience Design (Score: 4/4)
- **Strengths**:
  - SSE real-time streaming pipeline gives progressive feedback as clinical trial documents and PDFs are processed page-by-page.
  - Interactive D3 zoom, pan, and dragging controls allow exploring dense polypharmacy clusters without clutter.
  - Full mobile responsiveness: nav ribbon collapses into an accessible hamburger menu with slide-out drawer on `<640px` viewports, and grids fold to 1 column.
  - Hover tooltips powered by Tippy.js provide Wikipedia definitions for obscure medical and pharmacological terms.
- **Findings / Opportunities**:
  - Add explicit `aria-label` attributes to a few icon-only utility elements to maximize screen reader accessibility.

---

## Top 3 Recommended Polish Items
1. **Remove Duplicate Utility Bar Divider**: Fix consecutive `| |` in `src/index.html:33`.
2. **Generalize Demographics Backend Error String**: Change error message in `src/demographics.js:160` from `http://127.0.0.1:5000` to a generic network failure notice.
3. **Button Style Consolidation**: Remove inline padding overrides on lines 1493 & 1596 in `src/index.html` to standardize on `.btn--primary`.

---

## Files Audited
- `src/index.html` (Structure & accessibility)
- `src/style.css` (Design system, tokens, media queries)
- `src/app.js` (Interactivity & chart initialization)
- `src/_interactions_new.js` (D3 force simulation & polypharmacy)
- `src/biobert.js` (NER streaming client)
- `src/demographics.js` (Patient stratification charts)
- `src/temporal.js` (Weber effect & temporal analysis)
- `src/distributed_storage.js` (Kafka pipeline dashboard)

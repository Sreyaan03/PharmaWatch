/**
 * Research Guide — Full Manual
 * Comprehensive reference covering every feature, metric, formula, and workflow
 * in PharmaWatch. Designed to work without external dependencies.
 */

// ==========================================
// 1. DATA DEFINITIONS
// ==========================================

const GLOSSARY_TERMS = [
  {
    id: "prr",
    term: "Proportional Reporting Ratio (PRR)",
    plain: "A number that tells you how much more often a drug is linked to a side effect compared to all other drugs in the database.",
    technical: "Ratio of the proportion of reports for drug X mentioning event Y versus the proportion of all other drug reports mentioning event Y. Uses a 2×2 contingency table from FAERS counts.",
    formula: "PRR = [a / (a+b)] ÷ [c / (c+d)]  |  where: a = reports of drug X with event Y, b = reports of drug X without event Y, c = reports of all other drugs with event Y, d = reports of all other drugs without event Y",
    example: "Aspirin shows PRR = 3.4 for GI Bleeding. That means aspirin users are 3.4× more likely to appear in GI bleeding reports than users of any other drug. FDA flags a signal when PRR > 2.0 AND report count > 3.",
    links_to: "signals"
  },
  {
    id: "ror",
    term: "Reporting Odds Ratio (ROR)",
    plain: "Similar to PRR but uses odds instead of proportions — makes it easier to build confidence intervals and compare across studies.",
    technical: "The odds of a specific adverse event occurring WITH a specific drug, divided by the odds of the same event occurring WITH all other drugs. Less sensitive to large databases than PRR.",
    formula: "ROR = (a × d) / (b × c)  |  Same 2×2 table as PRR. A 95% CI lower bound > 1.0 is the standard signal threshold.",
    example: "ROR of 2.5 for Warfarin + Bleeding: the odds of a bleeding report for Warfarin are 2.5× higher than for any other drug. Used by Lareb (Netherlands pharmacovigilance centre).",
    links_to: "signals"
  },
  {
    id: "bcpnn",
    term: "Bayesian Confidence Propagation Neural Network (BCPNN IC)",
    plain: "A 'smart' version of PRR that prevents false alarms when you only have a few reports. It says 'I'm not sure yet' until enough data exists.",
    technical: "Bayesian shrinkage estimator. Calculates an Information Component (IC) = log2(Observed / Expected). The IC025 (lower 2.5th percentile of the posterior) must be > 0 for a confirmed signal. Used by WHO Uppsala Monitoring Centre.",
    formula: "IC = log2 (P(drug ∩ event) / (P(drug) × P(event)))  |  IC025 > 0 → confirmed signal",
    example: "A rare drug with only 2 reports of a rare event might have PRR = 50 (alarming). BCPNN IC025 will be negative (−0.3), correctly telling you: 'not enough data to be certain'. BCPNN prevents you from acting on noise.",
    links_to: "signals"
  },
  {
    id: "tto",
    term: "Time-To-Onset (TTO)",
    plain: "How many days after starting a drug before a patient first experiences the side effect. Tells you if a reaction is immediate or builds up over time.",
    technical: "Computed from FAERS fields: patient.patientonsetdate − patient.drug.drugstartdate. Distributions are plotted as histograms. Short TTO = acute reaction; Long TTO = cumulative/metabolic.",
    formula: "TTO (days) = Onset Date − Drug Start Date",
    example: "Anaphylaxis: TTO 0–1 day (immune reaction is immediate). Methotrexate liver damage: TTO 30–180 days (cumulative toxicity). Statin myopathy: TTO 30–120 days. TTO informs which monitoring protocol to use.",
    links_to: "dashboard"
  },
  {
    id: "velocity",
    term: "Velocity Spike",
    plain: "A sudden, statistically significant surge in adverse event reports for a specific drug — like an early alarm that something may be going wrong.",
    technical: "A month where the FAERS report count exceeds the 12-month rolling mean by ≥ 2 standard deviations (Z-score > 2.0). Detected using the temporal analysis pipeline.",
    formula: "Z-score = (Count(M) − Mean(M-12..M-1)) / StDev(M-12..M-1)  |  Spike if Z > 2.0",
    example: "Metformin normally receives ~2,000 reports/month. If October shows 4,800 reports, Z-score ≈ 3.5 → velocity spike. This could precede an FDA safety communication. PharmaWatch shows this in the Temporal tab.",
    links_to: "dashboard"
  },
  {
    id: "polypharmacy",
    term: "Polypharmacy",
    plain: "When a patient takes multiple medications at the same time. More drugs = more chances for dangerous interactions between them.",
    technical: "Concurrent use of ≥2 active pharmaceutical ingredients. PharmaWatch's Polypharmacy Analyzer accepts 2–5 drugs, runs GNN prediction on every possible pair, and computes a cumulative risk score.",
    formula: "Risk Pairs = C(n,2) = n! / (2!(n-2)!)  |  e.g. 4 drugs → 6 pairs to evaluate",
    example: "Patient on Warfarin, Aspirin, Omeprazole, Metformin: 6 pairs. The GNN predicts Warfarin+Aspirin = HIGH risk (bleeding), Warfarin+Omeprazole = MODERATE (enzyme inhibition), Metformin+Aspirin = LOW risk.",
    links_to: "interactions"
  },
  {
    id: "bc",
    term: "Betweenness Centrality (BC)",
    plain: "A measure of how 'connected' a drug is in the interaction network. High BC = that drug appears in many other drugs' interaction paths, making it the riskiest hub.",
    technical: "The fraction of all shortest paths in the drug co-prescription network that pass through a given drug node. High BC drugs are critical structural hubs — removing them disrupts the interaction graph.",
    formula: "BC(v) = Σ [σ(s,t|v) / σ(s,t)]  for all pairs (s,t) ≠ v",
    example: "Warfarin has extremely high BC because it interacts with NSAIDs, antibiotics, antifungals, antiepileptics, and more. Any patient on Warfarin automatically enters a high-risk polypharmacy profile when a second drug is added.",
    links_to: "interactions"
  },
  {
    id: "weber",
    term: "Weber Effect",
    plain: "Adverse event reports for a new drug always spike in the first 1–2 years after launch, then decline — even if the actual side effect rate stays constant. This is a known reporting bias.",
    technical: "First described by Weber (1984). New drug + enthusiastic reporting by early adopters + media attention = artificial peak in FAERS counts. Inflates PRR for newly approved drugs. PharmaWatch detects this pattern in Boxed Warnings timeline analysis.",
    formula: "Weber Curve: Reports peak at Year 1–2 post-launch, then decay exponentially regardless of actual incidence rate.",
    example: "A new anticoagulant launches in 2020. FAERS bleeding reports peak in 2021–2022 (Weber spike). PRR looks like 8.0 (alarming). By 2025 PRR settles to 2.1 (still elevated but not panicking). Without Weber correction, 2021 data is misleading.",
    links_to: "boxed-warnings"
  },
  {
    id: "faers",
    term: "FAERS / openFDA",
    plain: "The FDA's massive public database where doctors, pharmacists, patients, and manufacturers voluntarily report side effects. Over 25 million reports since 1968.",
    technical: "FDA Adverse Event Reporting System. openFDA provides a REST JSON API at api.fda.gov/drug/event.json. Key fields: medicinalproduct (drug name), reactionmeddrapt (reaction term in MedDRA vocabulary), receivedate (report date). PharmaWatch queries this API live.",
    formula: "Not Applicable — this is a data source, not a metric",
    example: "When you search 'Metformin' in PharmaWatch Drug Search, it calls: api.fda.gov/drug/event.json?search=patient.drug.medicinalproduct:\"metformin\"&count=patient.reaction.reactionmeddrapt.exact&limit=8 — and renders the top 8 adverse events as a bar chart.",
    links_to: "drug-search"
  },
  {
    id: "meddra",
    term: "MedDRA (Medical Dictionary for Regulatory Activities)",
    plain: "The standardized vocabulary used by regulators worldwide to classify medical conditions and adverse events. Every reaction in FAERS uses MedDRA terms.",
    technical: "International medical terminology developed for regulatory submissions. Organized in a 5-level hierarchy: System Organ Class (SOC) → High Level Group Term → High Level Term → Preferred Term (PT) → Lowest Level Term (LLT). FAERS stores reactions as Preferred Terms.",
    formula: "Not Applicable",
    example: "'Nausea' is a MedDRA Preferred Term under SOC 'Gastrointestinal disorders'. This standardization means you can compare nausea reports across different drugs even if they were submitted in different countries using different words.",
    links_to: "signals"
  },
  {
    id: "twosides",
    term: "TWOSIDES Dataset",
    plain: "A massive scientific database of over 60,000 drug-drug interaction side effects, built by mining medical literature and clinical trial data.",
    technical: "TWOSIDES (Tatonetti et al., 2012) contains 3.4 million statistically significant drug-drug-outcome associations extracted from FAERS using a propensity-score-matched analysis. PharmaWatch loads the pre-processed parquet version for GNN training and interaction lookups.",
    formula: "Coverage: ~60,000 drug pairs × 1,000+ side-effect terms = 3.4M associations",
    example: "TWOSIDES says Warfarin + Aspirin → bleeding risk (with statistical confidence). PharmaWatch's GNN is trained on TWOSIDES embeddings, so when you enter those two drugs, the model returns the interaction probability sourced from this dataset.",
    links_to: "interactions"
  },
  {
    id: "gnn",
    term: "Graph Neural Network (GNN)",
    plain: "A type of AI that learns patterns from networks (graphs) rather than tables. PharmaWatch's GNN learns drug interaction patterns from how drugs connect to each other in the TWOSIDES dataset.",
    technical: "PyTorch-based GNN trained on TWOSIDES drug-pair embeddings with 16 molecular features per drug. Architecture: 2-layer graph convolution + sigmoid output. Trained with balanced sampling (harmful/safe pairs). Output: interaction probability (0–1) and binary classification (harmful/safe).",
    formula: "h_v^(k) = σ(W^k · AGGREGATE({h_u^(k-1) : u ∈ N(v)}))",
    example: "Metformin + Warfarin → GNN outputs: probability = 0.59, label = 'No Significant Interaction'. Metformin + Alcohol → GNN outputs: probability = 0.87, label = 'Harmful Interaction (Lactic Acidosis risk)'.",
    links_to: "interactions"
  },
  {
    id: "lstm",
    term: "LSTM (Long Short-Term Memory)",
    plain: "A type of AI that's good at learning patterns in time-ordered data. PharmaWatch uses it to predict what the 'expected' number of adverse event reports should be each month, so it can detect when the real number is alarmingly higher.",
    technical: "PyTorch LSTM trained on monthly FAERS report counts per drug (pulled live from openFDA). Input: 12-month sliding window. Output: next month prediction. Trained models saved for Metformin, Warfarin, Pantoprazole, Montelukast. Gap Scan compares LSTM baseline to actual counts.",
    formula: "f_t = σ(W_f · [h_{t-1}, x_t] + b_f) ... standard LSTM cell equations",
    example: "For Metformin: LSTM baseline for 2024-06 predicts 1,950 reports. Actual FAERS shows 2,800 reports. Gap = +850 (43% above baseline). Warning Gap Predictor flags this as 'Rising Signal — check if boxed warning is warranted'.",
    links_to: "drug-search"
  },
  {
    id: "biobert",
    term: "BioBERT / NER",
    plain: "An AI trained on millions of medical papers that can read clinical text and automatically identify drug names, diseases, and side effects — without you having to highlight them manually.",
    technical: "PharmaWatch uses d4data/biomedical-ner-all (HuggingFace), a BERT model fine-tuned on biomedical NER datasets. Extracts entities tagged as Drug (DRUG), Disease (DISE), and Chemical (CHEM). Supports both raw text and PDF upload (via EasyOCR).",
    formula: "NER = classify each token t_i into {B-DRUG, I-DRUG, B-DISE, I-DISE, O, ...} using softmax over BERT hidden states",
    example: "Input: 'Patient was prescribed Metformin 500mg for type 2 diabetes, developed nausea'. Output: DRUG: [Metformin], DISE: [type 2 diabetes, nausea]. These are then scored against FAERS for pharmacovigilance signals.",
    links_to: "signals"
  }
];

const WORKFLOW_PERSONAS = [
  {
    id: "researcher",
    name: "Drug Safety Researcher",
    steps: [
      { title: "Find Signals", desc: "Open Signal Detection → set PRR threshold to 2.0 and report count > 3. Sort by ROR descending to find strongest signals.", icon: "📊", link: "signals" },
      { title: "Verify with BCPNN", desc: "For each flagged signal, check the IC025 column. Only signals where IC025 > 0 are statistically confirmed — discard the rest to avoid false positives.", icon: "🔬", link: "signals" },
      { title: "Temporal Check", desc: "Click a signal → jump to Temporal Analysis. Look for velocity spikes and check if the Weber Effect is driving the numbers. If Weber peak is present, apply a 40–60% discount to the reported PRR.", icon: "📈", link: "dashboard" },
      { title: "Cross-check NER", desc: "Upload a clinical report PDF in the Signals tab. BioBERT will extract drug and disease names. Match extracted entities against current signal list to find unlabeled events.", icon: "📄", link: "signals" },
      { title: "Report Gap", desc: "Run the Warning Gap Predictor in ML Models for any drug with a rising LSTM signal. If the drug has no boxed warning and no ClinicalTrials monitoring, flag for regulatory escalation.", icon: "⚠️", link: "drug-search" }
    ]
  },
  {
    id: "pharmacist",
    name: "Clinical Pharmacist",
    steps: [
      { title: "Enter Patient Drugs", desc: "Go to Interactions → Polypharmacy Analyzer. Type each drug name and press Enter (up to 5 drugs). The resolver auto-corrects brand names to generic via PubChem.", icon: "💊", link: "interactions" },
      { title: "Read GNN Results", desc: "The GNN evaluates all drug pairs. Red nodes = harmful interactions. Hover over edges to see the specific side effects from the TWOSIDES dataset.", icon: "🕸️", link: "interactions" },
      { title: "Check Boxed Warnings", desc: "For any high-risk drug in the interaction result, click its name to jump to Boxed Warnings. Review the warning text, post-warning event spike chart, and violations table.", icon: "⚠️", link: "boxed-warnings" },
      { title: "Verify Alternatives", desc: "In Drug Search, look up an alternative drug from the same class. Compare PRR and adverse event profiles between the original and alternative to find the safer option.", icon: "🔄", link: "drug-search" }
    ]
  },
  {
    id: "student",
    name: "Policy Analyst / Student",
    steps: [
      { title: "Start Here", desc: "Read this Research Guide top-to-bottom. Focus on PRR, BCPNN, and Weber Effect — these three concepts explain 90% of pharmacovigilance decisions.", icon: "📖", link: "research-guide" },
      { title: "Explore Real Data", desc: "Search any drug in Drug Search. The adverse event bar chart and trend line are live FDA data. Try comparing Metformin (safe, old drug) vs a newer drug to see the Weber Effect in action.", icon: "🔍", link: "drug-search" },
      { title: "Compare Signals", desc: "Open Signal Detection → filter by severity = 'Critical'. These are real signals computed from FAERS data using the PRR/ROR/BCPNN pipeline, not mock data.", icon: "🚨", link: "signals" },
      { title: "Understand Architecture", desc: "Visit the Dashboard Pipeline section to see how the Kafka→Spark→DuckDB big data architecture is designed. This is the production-scale version of what runs locally.", icon: "⚙️", link: "dashboard" },
      { title: "Run LSTM Demo", desc: "In ML Models, search 'Warfarin' and view the LSTM chart. The blue line is the AI-predicted baseline vs the red actual FAERS counts going back to 2006.", icon: "🤖", link: "drug-search" }
    ]
  }
];

const METRIC_CARDS = [
  {
    acronym: "PRR",
    name: "Proportional Reporting Ratio",
    purpose: "Primary disproportionality metric — how much more often is this drug linked to this side effect compared to all other drugs?",
    formula: "PRR = [a / (a+b)] ÷ [c / (c+d)]",
    scale: "< 2.0 = no signal (green), 2.0–4.0 = weak signal (amber), > 4.0 = strong signal (red)",
    context: "FDA uses PRR > 2.0 AND count > 3 as its minimum signal threshold. Chi-squared test also applied.",
    color: "color-red"
  },
  {
    acronym: "ROR",
    name: "Reporting Odds Ratio",
    purpose: "Odds-based disproportionality — allows construction of confidence intervals. Less inflated than PRR in large databases.",
    formula: "ROR = (a × d) / (b × c)",
    scale: "ROR 95% CI lower bound > 1.0 = signal; Dutch Lareb uses ROR > 2.0",
    context: "Used by European pharmacovigilance agencies. More statistically principled than PRR.",
    color: "color-amber"
  },
  {
    acronym: "IC",
    name: "Information Component (BCPNN)",
    purpose: "Bayesian signal — prevents false positives for rare drug-event combinations by shrinking small-sample estimates toward zero.",
    formula: "IC = log2(Observed / Expected)   IC025 (lower 2.5%ile) > 0 = signal",
    scale: "IC025 < 0 = no signal, IC025 0–1 = weak, IC025 > 1 = confirmed",
    context: "WHO Uppsala Monitoring Centre standard. Gold standard for global pharmacovigilance.",
    color: "color-blue"
  },
  {
    acronym: "TTO",
    name: "Time To Onset",
    purpose: "Days from drug start to adverse event onset. Establishes causality and guides monitoring schedules.",
    formula: "TTO = Onset Date − Drug Start Date (from FAERS patient records)",
    scale: "0–1 day = acute/immune, 2–7 days = subacute, > 30 days = chronic/cumulative",
    context: "Short TTO (anaphylaxis, acute allergy) = immediate monitoring needed. Long TTO (hepatotoxicity, cardiomyopathy) = periodic lab testing.",
    color: "color-green"
  }
];

const FEATURE_MANUAL = [
  {
    id: "drug-search",
    title: "Drug Search",
    icon: "🔍",
    description: "Live drug profiling using real-time openFDA API data. Search any drug to see its complete safety profile.",
    howToUse: [
      "Type any generic or brand drug name in the search bar — autocomplete pulls live names from FDA label database",
      "Press Enter or click a suggestion to load the drug profile",
      "The ADE bar chart shows the top 8 most-reported adverse events from FAERS (live data)",
      "The monthly trend line shows historical report volumes — look for acceleration",
      "Highest PRR badge (loads after 3–5 seconds) shows the single most disproportionate signal for this drug",
      "Clinical Trials badge shows how many active trials are monitoring this drug right now"
    ],
    tips: "Try searching 'Warfarin' — it's the most data-rich drug in FAERS with 20+ years of reports. Compare with 'Ozempic' (recent, fewer reports, Weber peak visible)."
  },
  {
    id: "signals",
    title: "Signal Detection",
    icon: "📊",
    description: "PRR / ROR / BCPNN disproportionality table computed live from FAERS. The same methodology used by the FDA's own Empirical Bayes Geometric Mean (EBGM) system.",
    howToUse: [
      "Signals load automatically on page open — allow 5–15 seconds for live FAERS queries",
      "Filter by severity (Critical / High / Moderate) using the dropdown",
      "Sort by PRR, ROR, or IC column headers",
      "Click any row to open the signal network graph for that drug-event pair",
      "Use the BioBERT NER text box to paste clinical note text — extracted entities are matched against the signal table",
      "PDF upload runs OCR → NER → FAERS matching in a streaming pipeline"
    ],
    tips: "A 'Critical' signal (PRR > 4, IC025 > 1) that has no boxed warning = potential research gap. The Warning Gap Predictor in ML Models can quantify this."
  },
  {
    id: "interactions",
    title: "Drug Interactions",
    icon: "🕸️",
    description: "GNN-powered drug-drug interaction prediction backed by the TWOSIDES dataset (3.4M statistical associations).",
    howToUse: [
      "Single pair check: enter Drug A and Drug B, press Predict. GNN returns probability (0–1) and label",
      "Polypharmacy Analyzer: add up to 5 drugs. System evaluates all pairs simultaneously",
      "The D3 force graph shows nodes (drugs) and edges (interactions) — red edges = harmful",
      "Hover over any edge to see the specific side effects from TWOSIDES",
      "Click 'Uncharted Interactions' to find pairs with FAERS signals but no label warning",
      "Recent History table shows your last 10 predictions with timestamps"
    ],
    tips: "Drug name resolver uses PubChem — so you can type 'Tylenol' and it resolves to 'acetaminophen' automatically."
  },
  {
    id: "boxed-warnings",
    title: "Boxed Warnings",
    icon: "⬛",
    description: "FDA's most serious safety warnings (Black Box). PharmaWatch shows the warning text, post-warning FAERS event spikes, Weber Effect analysis, and prescriber violations.",
    howToUse: [
      "Search any drug — the warning text is pulled live from the FDA label API",
      "The event spike chart shows FAERS report counts before and after the warning was issued",
      "The LSTM timeline overlays an 'expected' baseline — if actual > baseline, the warning may not be working",
      "Weber Effect / Bias Analysis section decomposes how much of the post-warning spike is real vs reporting bias",
      "FDA Violations table shows drug-warning pairs where prescriptions were issued contrary to boxed warnings"
    ],
    tips: "Try 'Clozapine' — it has 4 separate boxed warnings and very clear pre/post FAERS signature. Compare with 'Metformin' (no boxed warning) to understand what absence of signal looks like."
  },
  {
    id: "temporal",
    title: "Temporal Analysis",
    icon: "📅",
    description: "Time-based pharmacovigilance: seasonality, velocity spikes, and time-to-onset distributions from FAERS data.",
    howToUse: [
      "Select drug and adverse event using the dropdowns",
      "Seasonality chart shows if reports cluster in specific months (e.g. flu drugs spike in winter)",
      "Velocity chart shows month-by-month Z-scores — spikes above the red line are statistically significant",
      "TTO histogram shows how many days before the adverse event appeared after starting the drug",
      "Use velocity data to find drugs where reports are accelerating but no FDA action has been taken yet"
    ],
    tips: "Respiratory drugs often show strong November–January seasonality. Allergy drugs peak in March–May. This seasonal signal must be separated from a real safety signal."
  },
  {
    id: "demographics",
    title: "Demographics",
    icon: "👥",
    description: "Breaks down FAERS adverse event reports by sex, age group, and geography — critical for identifying vulnerable populations.",
    howToUse: [
      "Select drug and adverse event — charts update with FAERS demographic breakdowns",
      "Sex distribution pie chart: look for strong male/female skew (e.g. certain hormone drugs)",
      "Age group bar chart: identifies pediatric or elderly risk (e.g. NSAIDs → GI bleed in elderly)",
      "Geographic heatmap: country-level reporting density (note: US reports dominate FAERS due to mandatory reporting laws)"
    ],
    tips: "Always check demographics when PRR is high — a signal that appears only in elderly females may indicate a drug interaction with hormone replacement therapy, not a direct drug effect."
  },
  {
    id: "ml-models",
    title: "ML Models (LSTM + PRR Calculator)",
    icon: "🤖",
    description: "Interactive machine learning tools: LSTM time-series forecasting and manual PRR calculator.",
    howToUse: [
      "PRR Calculator: enter drug name and adverse event → system fetches live FAERS 2×2 table and computes PRR, ROR, and IC automatically",
      "LSTM Chart: search any drug → chart shows actual FAERS monthly counts (red) vs LSTM-predicted baseline (blue dashed) from 2006 to present",
      "Warning Gap Scan: click 'Scan Warning Gap' for a drug → LSTM detects rising slope + cross-references ClinicalTrials.gov for monitoring status",
      "Pre-trained models exist for: Metformin, Warfarin, Pantoprazole, Montelukast",
      "For any other drug, the system falls back to rolling average baseline (shown as 'fallback: true' in API response)"
    ],
    tips: "The Warning Gap result shows 'CRITICAL GAP' when: (1) LSTM detects a rising slope AND (2) no ClinicalTrials monitoring AND (3) no existing boxed warning. This is the highest-priority research finding PharmaWatch can produce."
  }
];

const INTERPRETATION_GUIDE = [
  {
    scenario: "PRR > 4.0 but IC025 < 0",
    meaning: "Probable false positive — likely caused by a very small number of reports (< 5). The drug-event combination is rare and data is insufficient.",
    action: "Do not escalate. Flag for monitoring once more reports accumulate (typically 6–12 months)."
  },
  {
    scenario: "PRR 2.0–4.0, IC025 > 0, rising velocity",
    meaning: "Emerging signal. Statistically confirmed, not yet at critical level, but report rate is accelerating month-over-month.",
    action: "Escalate to formal case-by-case review. Cross-check with TTO data to establish causality. Check ClinicalTrials for monitoring."
  },
  {
    scenario: "PRR > 4.0, IC025 > 1.0, no boxed warning",
    meaning: "Critical signal gap. Strong disproportionality with high statistical confidence, but the FDA has not yet issued a warning.",
    action: "Priority research finding. Run Warning Gap Predictor. Submit for regulatory review. Check if drug is recently approved (Weber effect may explain part of the signal)."
  },
  {
    scenario: "LSTM slope is negative (trend_slope < 0)",
    meaning: "FAERS reports for this drug are declining over time. Could mean the drug is being used less, or that the Weber Effect is subsiding post-launch.",
    action: "Low immediate concern. Continue routine monitoring. Compare with market share data if available."
  },
  {
    scenario: "GNN confidence 0.45–0.55",
    meaning: "Borderline prediction — the model is uncertain about this drug pair. The interaction may be real but context-dependent (dose, patient genetics).",
    action: "Do not rely solely on GNN. Cross-reference TWOSIDES dataset for any known associations. Check boxed warnings for each drug individually."
  }
];

// ==========================================
// 2. RENDER FUNCTIONS
// ==========================================

function renderGlossary() {
  const container = document.getElementById("guide-glossary-panel");
  if (!container) return;

  let html = `
    <h2 class="guide-section-title">Interactive Glossary</h2>
    <p style="color:var(--gray-600); margin-bottom:1.5rem; font-size:0.9rem;">Every metric, acronym, and formula used in PharmaWatch — with plain-English explanations, technical definitions, and real examples.</p>
    <div class="guide-glossary-controls">
      <input type="text" id="guide-search" class="guide-glossary-search" placeholder="Search terms, acronyms, formulas (e.g. PRR, LSTM, Weber)..." />
    </div>
    <div class="guide-glossary-grid" id="guide-glossary-grid">
  `;

  html += GLOSSARY_TERMS.map((term, index) => {
    const expandedClass = index < 3 ? "expanded" : "";
    return `
      <div class="guide-card ${expandedClass}" data-term="${term.term.toLowerCase()} ${term.id.toLowerCase()} ${term.formula.toLowerCase()}">
        <div class="guide-card-header">
          <h3 class="guide-card-title">${term.term}</h3>
          <button class="guide-card-badge" onclick="(function(){var btn=document.querySelector('[data-section=\\'${term.links_to}\\']'); if(btn) btn.click();})()">Go to ${term.links_to.toUpperCase()} ↗</button>
        </div>
        <p class="guide-card-plain">${term.plain}</p>
        <button class="guide-card-toggle">View Technical Details ▾</button>
        <div class="guide-card-details">
          <p><strong>Technical:</strong> ${term.technical}</p>
          <div class="guide-formula-box">${term.formula}</div>
          <p><strong>Real Example:</strong> ${term.example}</p>
        </div>
      </div>
    `;
  }).join('');

  html += `</div>`;
  container.innerHTML = html;

  container.querySelectorAll(".guide-card-toggle").forEach(toggle => {
    toggle.addEventListener("click", (e) => {
      const card = e.target.closest(".guide-card");
      const expanded = card.classList.toggle("expanded");
      e.target.textContent = expanded ? "Hide Technical Details ▴" : "View Technical Details ▾";
    });
  });

  const searchInput = document.getElementById("guide-search");
  if (searchInput) {
    searchInput.addEventListener("input", (e) => {
      const query = e.target.value.toLowerCase();
      container.querySelectorAll(".guide-card").forEach(card => {
        const text = card.getAttribute("data-term") || "";
        const allText = card.textContent.toLowerCase();
        card.style.display = (allText.includes(query) || text.includes(query)) ? "flex" : "none";
      });
    });
  }
}

function renderWorkflows() {
  const container = document.getElementById("guide-workflow-panel");
  if (!container) return;

  let html = `
    <h2 class="guide-section-title">How to Use PharmaWatch</h2>
    <p style="color:var(--gray-600); margin-bottom:1.5rem; font-size:0.9rem;">Step-by-step workflows tailored to your role. Each step links directly to the relevant section.</p>
    <div class="guide-workflow-tabs">
      ${WORKFLOW_PERSONAS.map((p, i) => `<button class="guide-persona-tab ${i === 0 ? 'active' : ''}" data-persona-id="${p.id}">${p.name}</button>`).join('')}
    </div>
    <div id="guide-workflow-content"></div>
  `;

  container.innerHTML = html;

  const contentDiv = document.getElementById("guide-workflow-content");

  const updateWorkflow = (personaId) => {
    const persona = WORKFLOW_PERSONAS.find(p => p.id === personaId);
    if (!persona) return;
    contentDiv.innerHTML = `
      <div class="guide-stepper">
        ${persona.steps.map((s, i) => `
          <div class="guide-step">
            <div class="guide-step-icon">${s.icon}</div>
            <div class="guide-step-title">Step ${i + 1}: ${s.title}</div>
            <div class="guide-step-desc">${s.desc}</div>
            <button class="btn btn--sm btn--outline" style="color:var(--cdc-blue); border-color:var(--cdc-blue); margin-top:0.5rem;" onclick="(function(){var btn=document.querySelector('[data-section=\\'${s.link}\\']'); if(btn) btn.click();})()">Open ${s.link.replace(/-/g, ' ')} →</button>
          </div>
        `).join('')}
      </div>
    `;
  };

  updateWorkflow(WORKFLOW_PERSONAS[0].id);

  container.querySelectorAll(".guide-persona-tab").forEach(tab => {
    tab.addEventListener("click", (e) => {
      container.querySelectorAll(".guide-persona-tab").forEach(t => t.classList.remove("active"));
      e.target.classList.add("active");
      // FIX: use data-persona-id (string id) not numeric index
      updateWorkflow(e.target.getAttribute("data-persona-id"));
    });
  });
}

function renderMetricCards() {
  const container = document.getElementById("guide-metrics-panel");
  if (!container) return;

  let html = `
    <h2 class="guide-section-title">Metric Cheat Sheet</h2>
    <p style="color:var(--gray-600); margin-bottom:1.5rem; font-size:0.9rem;">Quick reference for all pharmacovigilance statistics used in PharmaWatch — formula, scale, and real-world context.</p>
    <div class="guide-metrics-grid">
  `;

  html += METRIC_CARDS.map(m => `
    <div class="guide-metric-card">
      <div class="guide-metric-card-top ${m.color}"></div>
      <div class="guide-metric-card-body">
        <h3 class="guide-metric-acronym">${m.acronym}</h3>
        <div class="guide-metric-name">${m.name}</div>
        <p style="font-size:0.85rem; margin-bottom:1rem; color:var(--gray-700);">${m.purpose}</p>
        <div class="guide-formula-box" style="font-size:0.8rem;">${m.formula}</div>
        <div style="font-size:0.8rem; margin-top:1rem;"><strong>Scale:</strong> ${m.scale}</div>
        <div style="font-size:0.8rem; margin-top:0.5rem; color:var(--gray-600);"><em>${m.context}</em></div>
      </div>
    </div>
  `).join('');

  html += `</div>`;
  container.innerHTML = html;
}

function renderFeatureManual() {
  const container = document.getElementById("guide-flowchart-panel");
  if (!container) return;

  let html = `
    <h2 class="guide-section-title">Feature Manual</h2>
    <p style="color:var(--gray-600); margin-bottom:1.5rem; font-size:0.9rem;">Detailed instructions for every tab in PharmaWatch — what each feature does, how to use it, and expert tips.</p>
    <div class="guide-glossary-grid">
  `;

  html += FEATURE_MANUAL.map((f, index) => `
    <div class="guide-card ${index < 2 ? 'expanded' : ''}" data-term="${f.title.toLowerCase()} ${f.id.toLowerCase()}">
      <div class="guide-card-header">
        <h3 class="guide-card-title">${f.icon} ${f.title}</h3>
      </div>
      <p class="guide-card-plain">${f.description}</p>
      <button class="guide-card-toggle">How to Use ▾</button>
      <div class="guide-card-details">
        <ol style="padding-left:1.2rem; margin:0.75rem 0; font-size:0.85rem; line-height:1.8;">
          ${f.howToUse.map(step => `<li>${step}</li>`).join('')}
        </ol>
        <div class="guide-formula-box" style="font-size:0.82rem;">
          💡 <strong>Pro tip:</strong> ${f.tips}
        </div>
      </div>
    </div>
  `).join('');

  html += `</div>

    <h2 class="guide-section-title" style="margin-top:2.5rem;">Interpreting Results — What Should I Do?</h2>
    <p style="color:var(--gray-600); margin-bottom:1.5rem; font-size:0.9rem;">Common scenarios you'll encounter and how to act on them.</p>
    <div style="display:flex; flex-direction:column; gap:1rem;">
      ${INTERPRETATION_GUIDE.map(item => `
        <div style="background:var(--surface-2,#f8f9fa); border-left:4px solid var(--cdc-blue,#003d7c); border-radius:0 8px 8px 0; padding:1rem 1.25rem;">
          <div style="font-weight:700; color:var(--cdc-blue,#003d7c); font-size:0.9rem; margin-bottom:0.35rem;">Scenario: ${item.scenario}</div>
          <div style="font-size:0.85rem; color:var(--gray-700,#555); margin-bottom:0.4rem;"><strong>What it means:</strong> ${item.meaning}</div>
          <div style="font-size:0.85rem; color:var(--gray-600,#666);"><strong>What to do:</strong> ${item.action}</div>
        </div>
      `).join('')}
    </div>
  `;

  container.innerHTML = html;

  // attach toggle listeners for the new feature manual cards
  container.querySelectorAll(".guide-card-toggle").forEach(toggle => {
    toggle.addEventListener("click", (e) => {
      const card = e.target.closest(".guide-card");
      const expanded = card.classList.toggle("expanded");
      e.target.textContent = expanded ? "How to Use ▴" : "How to Use ▾";
    });
  });
}

// ==========================================
// 3. INITIALIZATION
// ==========================================

function initGuide() {
  console.log("[Guide] Initializing Research Guide...");
  renderWorkflows();
  renderMetricCards();
  renderGlossary();
  renderFeatureManual();   // replaces empty renderFlowchart
}

document.addEventListener("DOMContentLoaded", () => {
  initGuide();
});
